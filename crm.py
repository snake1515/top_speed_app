"""Clientes y promociones: recordatorios de recompra (aceite…), prospectos de redes y listas para promos por WhatsApp."""
import json
from datetime import timedelta

from sqlalchemy import or_

from models import (Cliente, Producto, Promo, PromoEnvio, Prospecto, Recordatorio, Venta, VentaItem, ahora, config, db,
                    normalizar, tel_wa)


# ------------------------------------------------------------------ recordatorios
def crear_recordatorios(venta, usuario=None):
    """Al vender: por cada repuesto con «recordar en X días» crea el aviso para escribirle al cliente."""
    tel = venta.cliente_tel or (venta.cliente.telefono if venta.cliente else "")
    norm = tel_wa(tel)
    if not norm:
        return []
    hoy = (venta.fecha or ahora()).date()
    hechos = []
    vistos = set()
    for it in venta.items:
        p = it.producto
        if not p or not (p.recordar_dias or 0) > 0 or p.id in vistos:
            continue
        vistos.add(p.id)
        # el cliente volvió a comprar lo mismo: el aviso anterior ya no hace falta
        for viejo in Recordatorio.query.filter(Recordatorio.tel_norm == norm, Recordatorio.producto_id == p.id,
                                               Recordatorio.estado.in_(["pendiente", "avisado"])).all():
            viejo.estado = "recompro"
        r = Recordatorio(fecha=hoy + timedelta(days=int(p.recordar_dias)), cliente_id=venta.cliente_id, venta_id=venta.id,
                         producto_id=p.id, producto_nombre=p.nombre[:200], nombre=(venta.cliente_nombre or "")[:140] or None,
                         telefono=tel[:40], tel_norm=norm, moto=venta.moto, dias=int(p.recordar_dias), compra_fecha=hoy,
                         usuario_id=usuario.id if usuario else None)
        db.session.add(r)
        hechos.append(r)
    return hechos


def anular_recordatorios(venta):
    for r in Recordatorio.query.filter(Recordatorio.venta_id == venta.id,
                                       Recordatorio.estado.in_(["pendiente", "avisado"])).all():
        r.estado = "descartado"


def recordatorios_vencidos():
    return Recordatorio.query.filter(Recordatorio.estado == "pendiente", Recordatorio.fecha <= ahora().date())


def primer_nombre(nombre):
    partes = (nombre or "").strip().split()
    return partes[0].capitalize() if partes else ""


def texto_recordatorio(r, cfg=None):
    cfg = cfg or config()
    base = cfg.msg_recordatorio or ("Hola {nombre} 👋 Te saluda {negocio}. Ya van {dias} días desde que compraste "
                                    "{producto}{moto_txt}. Es buen momento para el cambio.")
    dias = (ahora().date() - r.compra_fecha).days if r.compra_fecha else (r.dias or 0)
    return (base.replace("{nombre}", primer_nombre(r.nombre) or "")
            .replace("{negocio}", cfg.nombre or "")
            .replace("{dias}", str(dias))
            .replace("{producto}", r.producto_nombre or "tu repuesto")
            .replace("{moto_txt}", f" para tu {r.moto}" if r.moto else "")
            .replace("{moto}", r.moto or "moto")
            .replace("Hola  ", "Hola "))


# ------------------------------------------------------------------ motos
def motos_conocidas():
    from models import MOTOS_COMUNES
    usadas = set()
    for (m,) in db.session.query(Cliente.moto).filter(Cliente.moto.isnot(None)).distinct().limit(300):
        usadas.add(m.strip())
    for (m,) in db.session.query(Venta.moto).filter(Venta.moto.isnot(None)).distinct().limit(300):
        usadas.add(m.strip())
    for (m,) in db.session.query(Prospecto.moto).filter(Prospecto.moto.isnot(None)).distinct().limit(300):
        usadas.add(m.strip())
    vistos, out = set(), []
    for m in sorted(usadas | set(MOTOS_COMUNES), key=lambda x: normalizar(x)):
        k = normalizar(m).replace(" ", "")
        if m and k not in vistos:
            vistos.add(k)
            out.append(m)
    return out


def moto_coincide(moto, filtros_moto):
    if not filtros_moto:
        return True
    m = normalizar(moto or "").replace(" ", "")
    return bool(m) and any(normalizar(f).replace(" ", "") in m for f in filtros_moto if f.strip())


def tras_venta(venta, moto, usuario=None):
    """Guarda la moto en la venta y el cliente, marca al prospecto como cliente y crea los recordatorios."""
    moto = (moto or "").strip()[:80]
    if not moto and venta.cliente and venta.cliente.moto:
        moto = venta.cliente.moto
    venta.moto = moto or None
    if venta.cliente and moto and (venta.cliente.moto or "") != moto:
        venta.cliente.moto = moto
    norm = tel_wa(venta.cliente_tel)
    if norm:
        for pr in Prospecto.query.filter_by(tel_norm=norm).all():
            if pr.estado != "cliente":
                pr.estado = "cliente"
            if venta.cliente_id and not pr.cliente_id:
                pr.cliente_id = venta.cliente_id
            if moto and not pr.moto:
                pr.moto = moto
    db.session.flush()
    return crear_recordatorios(venta, usuario)


# ------------------------------------------------------------------ prospectos
COLUMNAS = {
    "nombre": ["nombre", "name", "cliente", "contacto", "nombre completo", "full name", "full_name"],
    "telefono": ["telefono", "teléfono", "celular", "whatsapp", "numero", "número", "phone", "phone number",
                 "phone_number", "movil", "móvil", "tel"],
    "moto": ["moto", "modelo", "modelo de moto", "referencia moto", "vehiculo", "vehículo"],
    "interes": ["interes", "interés", "producto", "le interesa", "busca", "necesita"],
    "campana": ["campana", "campaña", "campaign", "campaign_name", "origen", "anuncio", "ad_name"],
    "notas": ["notas", "nota", "observaciones", "comentario", "comentarios"],
}


def _campo(cabecera):
    c = normalizar(str(cabecera or "")).strip().replace("_", " ")
    for k, opciones in COLUMNAS.items():
        if c in [normalizar(o).replace("_", " ") for o in opciones]:
            return k
    for k, opciones in COLUMNAS.items():   # coincidencia parcial (ej. «Número de WhatsApp»)
        if any(normalizar(o) in c for o in opciones if len(o) > 3):
            return k
    return None


def filas_de_excel(archivo):
    """Lee un .xlsx/.csv y devuelve dicts con los campos reconocidos por el nombre de la columna."""
    nombre = (getattr(archivo, "filename", "") or "").lower()
    filas = []
    if nombre.endswith(".csv"):
        import csv
        import io
        txt = archivo.read().decode("utf-8-sig", errors="replace")
        dialecto = csv.Sniffer().sniff(txt[:2000], delimiters=",;\t") if txt.strip() else csv.excel
        filas = list(csv.reader(io.StringIO(txt), dialecto))
    else:
        from openpyxl import load_workbook
        wb = load_workbook(archivo, read_only=True, data_only=True)
        ws = wb.active
        filas = [list(r) for r in ws.iter_rows(values_only=True)]
    filas = [r for r in filas if any(x not in (None, "") for x in r)]
    if not filas:
        return []
    mapa = {i: _campo(h) for i, h in enumerate(filas[0])}
    if "telefono" not in mapa.values():
        # sin encabezados: se toma la columna que parezca teléfono y la de texto como nombre
        mapa = {}
        for i, x in enumerate(filas[0]):
            if tel_wa(str(x or "")):
                mapa[i] = "telefono"
            elif x and "nombre" not in mapa.values():
                mapa[i] = "nombre"
        cuerpo = filas
    else:
        cuerpo = filas[1:]
    out = []
    for r in cuerpo:
        d = {}
        for i, x in enumerate(r):
            k = mapa.get(i)
            if k and x not in (None, ""):
                if isinstance(x, float) and x.is_integer():
                    x = int(x)
                d[k] = str(x).strip()
        out.append(d)
    return out


def filas_de_texto(texto):
    """Una persona por línea: «Nombre, celular, moto, interés» (también con ; o tabulador)."""
    out = []
    for linea in (texto or "").splitlines():
        if not linea.strip():
            continue
        sep = "\t" if "\t" in linea else (";" if ";" in linea else ",")
        partes = [x.strip() for x in linea.split(sep)]
        d = {}
        resto = []
        for x in partes:
            if not d.get("telefono") and tel_wa(x):
                d["telefono"] = x
            else:
                resto.append(x)
        for k, x in zip(["nombre", "moto", "interes", "campana"], resto):
            if x:
                d[k] = x
        out.append(d)
    return out


def importar_prospectos(filas, campana="", interes="", moto=""):
    nuevos = actualizados = sin_tel = 0
    existentes = {p.tel_norm: p for p in Prospecto.query.all() if p.tel_norm}
    compradores = {tel_wa(t) for (t,) in db.session.query(Cliente.telefono).filter(Cliente.telefono.isnot(None))}
    compradores |= {tel_wa(t) for (t,) in db.session.query(Venta.cliente_tel)
                    .filter(Venta.cliente_tel.isnot(None), Venta.estado != "anulada").distinct()}
    for d in filas:
        norm = tel_wa(d.get("telefono"))
        if not norm:
            sin_tel += 1
            continue
        p = existentes.get(norm)
        if p is None:
            p = Prospecto(telefono=d["telefono"][:40], tel_norm=norm, estado="nuevo")
            db.session.add(p)
            existentes[norm] = p
            nuevos += 1
            if norm in compradores:
                p.estado = "cliente"
        else:
            actualizados += 1
        p.nombre = (d.get("nombre") or p.nombre or "")[:140] or None
        p.moto = (d.get("moto") or p.moto or moto or "")[:80] or None
        p.interes = (d.get("interes") or p.interes or interes or "")[:120] or None
        p.campana = (d.get("campana") or p.campana or campana or "")[:120] or None
        if d.get("notas"):
            p.notas = d["notas"][:300]
    return nuevos, actualizados, sin_tel


# ------------------------------------------------------------------ promociones
FILTROS_DEFECTO = {"clientes": True, "prospectos": True, "motos": [], "categoria_id": None, "producto": "",
                   "interes": "", "estados": ["nuevo", "contactado", "interesado"], "excluir_dias": 7,
                   "compro_dias": 0}


def leer_filtros(promo):
    f = dict(FILTROS_DEFECTO)
    try:
        f.update(json.loads(promo.filtros or "{}"))
    except ValueError:
        pass
    return f


def audiencia(f):
    """Lista de destinatarios (sin repetir teléfono) según los filtros de la promo."""
    motos = [m.strip() for m in (f.get("motos") or []) if m and m.strip()]
    gente = {}

    if f.get("clientes"):
        # compradores: clientes registrados y ventas con celular (aunque no se haya creado el cliente)
        q = (db.session.query(Venta).filter(Venta.estado != "anulada")
             .filter(or_(Venta.cliente_tel.isnot(None), Venta.cliente_id.isnot(None))))
        dias = int(f.get("compro_dias") or 0)
        if dias > 0:
            q = q.filter(Venta.fecha >= ahora() - timedelta(days=dias))
        filtrar_prod = f.get("categoria_id") or (f.get("producto") or "").strip()
        if filtrar_prod:
            q = q.join(VentaItem, VentaItem.venta_id == Venta.id).join(Producto, Producto.id == VentaItem.producto_id)
            if f.get("categoria_id"):
                q = q.filter(Producto.categoria_id == int(f["categoria_id"]))
            if (f.get("producto") or "").strip():
                q = q.filter(Producto.busqueda.like(f"%{normalizar(f['producto'])}%"))
        for v in q.order_by(Venta.fecha.desc()).limit(5000).all():
            tel = v.cliente_tel or (v.cliente.telefono if v.cliente else "")
            norm = tel_wa(tel)
            if not norm or norm in gente:
                continue
            moto = (v.cliente.moto if v.cliente and v.cliente.moto else None) or v.moto
            if not moto:   # la moto de otra venta del mismo celular
                moto = next((x.moto for x in Venta.query.filter(Venta.cliente_tel == v.cliente_tel, Venta.moto.isnot(None))
                             .order_by(Venta.fecha.desc()).limit(1)), None) if v.cliente_tel else None
            gente[norm] = {"tipo": "cliente", "ref_id": v.cliente_id, "nombre": (v.cliente.nombre if v.cliente else v.cliente_nombre) or "",
                           "telefono": tel, "tel_norm": norm, "moto": moto or "",
                           "detalle": f"Última compra {v.fecha.strftime('%d/%m/%Y')}"}
        if not filtrar_prod and not dias:
            # clientes registrados con celular que aún no han comprado
            for c in Cliente.query.filter(Cliente.activo.is_(True), Cliente.telefono.isnot(None)).all():
                norm = tel_wa(c.telefono)
                if norm and norm not in gente:
                    gente[norm] = {"tipo": "cliente", "ref_id": c.id, "nombre": c.nombre, "telefono": c.telefono,
                                   "tel_norm": norm, "moto": c.moto or "", "detalle": "Cliente registrado"}

    if f.get("prospectos"):
        q = Prospecto.query.filter(Prospecto.no_contactar.is_(False))
        estados = f.get("estados") or []
        if estados:
            q = q.filter(Prospecto.estado.in_(estados))
        for p in q.order_by(Prospecto.creado.desc()).all():
            if (f.get("interes") or "").strip() and normalizar(f["interes"]) not in normalizar(p.interes or ""):
                continue
            norm = p.tel_norm or tel_wa(p.telefono)
            if not norm:
                continue
            if norm in gente:
                if not gente[norm]["moto"] and p.moto:
                    gente[norm]["moto"] = p.moto
                continue
            gente[norm] = {"tipo": "prospecto", "ref_id": p.id, "nombre": p.nombre or "", "telefono": p.telefono,
                           "tel_norm": norm, "moto": p.moto or "",
                           "detalle": " · ".join(x for x in (p.interes, p.campana, p.estado_nombre) if x)}

    lista = [d for d in gente.values() if moto_coincide(d["moto"], motos)]
    # no repetirle a quien ya recibió una promo hace poco (ni esta misma)
    dias_ex = int(f.get("excluir_dias") or 0)
    if dias_ex > 0 and lista:
        recientes = {t for (t,) in db.session.query(PromoEnvio.tel_norm)
                     .filter(PromoEnvio.fecha >= ahora() - timedelta(days=dias_ex)).distinct()}
        for d in lista:
            d["reciente"] = d["tel_norm"] in recientes
    return sorted(lista, key=lambda d: (d["tipo"] != "prospecto", normalizar(d["nombre"] or "zzz")))


def personalizar(mensaje, d):
    nombre = primer_nombre(d.get("nombre"))
    txt = (mensaje or "").replace("{nombre}", nombre).replace("{moto}", d.get("moto") or "tu moto")
    return txt.replace("Hola ,", "Hola,").replace("Hola  ", "Hola ").replace("Hola !", "¡Hola!")


def registrar_envio(promo, d, usuario):
    db.session.add(PromoEnvio(promo_id=promo.id, tel_norm=d["tel_norm"], nombre=(d.get("nombre") or "")[:140],
                              tipo=d.get("tipo"), ref_id=d.get("ref_id"), usuario_id=usuario.id if usuario else None))
    if d.get("tipo") == "prospecto" and d.get("ref_id"):
        p = db.session.get(Prospecto, d["ref_id"])
        if p:
            p.envios = (p.envios or 0) + 1
            p.ultimo_envio = ahora()
            if p.estado == "nuevo":
                p.estado = "contactado"


def excel(titulo, columnas, filas):
    """Libro de Excel sencillo: encabezado en negrita, filtros y anchos ajustados."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = titulo[:31]
    ws.append(columnas)
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="C8102E")
    for f in filas:
        ws.append(list(f))
    for fila in ws.iter_rows(min_row=2):
        for c in fila:
            c.font = Font(name="Arial")
    for i, col in enumerate(columnas, 1):
        largo = max([len(str(col))] + [len(str(f[i - 1] or "")) for f in filas[:500]])
        ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(largo + 2, 10), 60)
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
