"""Carga masiva de repuestos desde CSV o Excel, por lotes, como en Stocka.

Flujo (lo maneja el navegador):
  1. iniciar  → crea la carga padre CSV-0001.
  2. lote     → procesa un bloque de filas (CSV-0001-A, -B…): cada fila se acepta o se rechaza
                con su motivo; las buenas se guardan aunque otras fallen.
  3. finalizar → cierra la carga con los totales y queda el comprobante con los rechazados.
"""
from io import BytesIO

import json
import unicodedata

from sqlalchemy.exc import IntegrityError

from models import CargaLote, CargaMasiva, Categoria, Producto, config, db, letra_lote, siguiente
from services import ErrorNegocio, entrada

COLUMNAS = [
    ("codigo", "Código *", "Código interno único (ej. PAST-PUL180)"),
    ("nombre", "Nombre *", "Ej. Pastillas de freno delanteras"),
    ("categoria", "Categoría", "Se crea si no existe"),
    ("marca", "Marca", "Marca del repuesto"),
    ("referencia", "Referencia fabricante", ""),
    ("compatibilidad", "Compatible con", "Motos donde sirve, separadas por coma"),
    ("ubicacion", "Ubicación", "Estante o cajón"),
    ("codigo_barras", "Código de barras", ""),
    ("unidad", "Unidad", "unidad, par, juego, kit, litro…"),
    ("stock", "Stock inicial", "Solo para repuestos nuevos"),
    ("costo", "Costo unitario", "Costo de compra de cada unidad"),
    ("precio_detal", "Precio detal *", "IVA incluido si cobras IVA"),
    ("precio_mayor", "Precio por mayor", ""),
    ("precio_taller", "Precio taller", ""),
    ("stock_minimo", "Stock mínimo", "Para la alerta de reposición"),
    ("iva", "IVA %", "0 o 19"),
    ("en_tienda", "En tienda (SI/NO)", "Si se publica en la tienda online"),
    ("virtual", "Bodega virtual (SI/NO)", "SI = se vende sin tenerlo; el costo es el costo virtual y el stock va vacío"),
]


def plantilla():
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Repuestos"
    ws.append([c[1] for c in COLUMNAS])
    for i, c in enumerate(ws[1]):
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="E8243C" if "*" in c.value else "0B0A07")
        if COLUMNAS[i][2]:
            c.comment = Comment(COLUMNAS[i][2], "Moto Speed")
        ws.column_dimensions[c.column_letter].width = 20
    ws.append(["EJEMPLO-001", "Kit de arrastre 428 (borrar esta fila)", "Transmisión (kit de arrastre)", "Riffel",
               "KA-428", "Boxer 100, CT 100", "E2-C3", "", "kit", 3, 85000, 129000, 115000, 120000, 2, 0, "SI", "NO"])
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _num(v, defecto=0.0):
    if v is None or v == "":
        return defecto
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # 1.250,50
        else:
            s = s.replace(",", "")                      # 1,250.50
    elif s.count(".") > 1 or s.count(",") > 1:
        s = s.replace(".", "").replace(",", "")
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        ent, dec = s.split(sep)
        s = ent + dec if len(dec) == 3 and ent not in ("", "0", "-0") else ent + "." + dec  # 129.000 = 129000
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"«{v}» no es un número")


TAM_LOTE = 200

# Nombres de columna aceptados (se comparan sin tildes, sin mayúsculas, sin «*»).
ALIAS = {
    "codigo": ["codigo", "cod", "codigo_interno", "sku", "procodigo"],
    "nombre": ["nombre", "descripcion", "producto", "nombre_del_producto"],
    "categoria": ["categoria", "grupo", "linea"],
    "marca": ["marca"],
    "referencia": ["referencia", "referencia_fabricante", "ref", "prorefe"],
    "compatibilidad": ["compatibilidad", "compatible_con", "aplica_para", "motos"],
    "ubicacion": ["ubicacion", "proubica", "estante"],
    "codigo_barras": ["codigo_barras", "codigo_de_barras", "barras", "ean", "procodbar"],
    "unidad": ["unidad", "medida"],
    "stock": ["stock", "stock_inicial", "existencias", "cantidad", "inventario"],
    "costo": ["costo", "costo_unitario", "procosto", "costo_promedio"],
    "precio_detal": ["precio_detal", "precio", "precio_venta", "proprecio", "pvp"],
    "precio_mayor": ["precio_mayor", "precio_por_mayor", "proprecio2"],
    "precio_taller": ["precio_taller", "proprecio3"],
    "stock_minimo": ["stock_minimo", "minimo", "prominstk"],
    "iva": ["iva", "iva_pct", "proivapor"],
    "en_tienda": ["en_tienda", "en_tienda_si_no", "tienda", "publicar"],
    "virtual": ["virtual", "bodega_virtual", "bodega_virtual_si_no", "virtual_si_no", "es_virtual"],
}
_MAPA = {a: k for k, lista in ALIAS.items() for a in lista}
_SUMA_STOCK = ("existencias_bodega", "existencias_almacen")  # formato Stocka


def clave_columna(titulo):
    t = unicodedata.normalize("NFKD", str(titulo or "")).encode("ascii", "ignore").decode().lower()
    t = "".join(ch if ch.isalnum() else "_" for ch in t.replace("*", "").replace("%", ""))
    while "__" in t:
        t = t.replace("__", "_")
    return t.strip("_")


def normalizar_fila(fila):
    """Convierte una fila {titulo_original: valor} a las claves internas."""
    d, extra = {}, 0.0
    for titulo, valor in fila.items():
        if titulo == "_fila" or titulo == "_dup":
            d[titulo] = valor
            continue
        k = clave_columna(titulo)
        v = "" if valor is None else str(valor).strip()
        if v.lower() in ("nan", "none", "null"):
            v = ""
        if k in _SUMA_STOCK:
            try:
                extra += _num(v)
            except ValueError:
                pass
            d.setdefault("_suma", True)
            continue
        campo = _MAPA.get(k)
        if campo and (campo not in d or d[campo] == "" or
                      (campo == "categoria" and d[campo].isdigit() and v and not v.isdigit())):
            d[campo] = v
    if d.pop("_suma", False) and not d.get("stock"):
        d["stock"] = str(extra)
    return d


def plantilla_csv():
    enc = [c[0] for c in COLUMNAS]
    ej = ["EJEMPLO-001", "Kit de arrastre 428 (borrar esta fila)", "Transmisión (kit de arrastre)", "Riffel",
          "KA-428", "Boxer 100, CT 100", "E2-C3", "", "kit", "3", "85000", "129000", "115000", "120000", "2", "0",
          "SI", "NO"]
    texto = ";".join(enc) + "\r\n" + ";".join(f'"{x}"' if "," in x or ";" in x else x for x in ej) + "\r\n"
    return ("\ufeff" + texto).encode("utf-8")


def leer_excel(archivo):
    """Lee la primera hoja de un .xlsx y devuelve una lista de dicts {título: valor} (para mandar por lotes)."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(archivo, data_only=True, read_only=True)
    except Exception:
        raise ErrorNegocio("No pude leer el Excel. Revisa que sea .xlsx (o guárdalo como CSV).")
    filas = wb.worksheets[0].iter_rows(values_only=True)
    try:
        enc = [str(c or "").strip() for c in next(filas)]
    except StopIteration:
        raise ErrorNegocio("El archivo está vacío.")
    salida = []
    for n, fila in enumerate(filas, start=2):
        if not any(c not in (None, "") for c in fila):
            continue
        d = {"_fila": n}
        for i, t in enumerate(enc):
            if t and i < len(fila):
                v = fila[i]
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                d[t] = "" if v is None else str(v)
        salida.append(d)
    return enc, salida


def iniciar_carga(nombre_archivo, usuario_id):
    c = CargaMasiva(numero=siguiente("sig_carga"), archivo=(nombre_archivo or "archivo.csv")[:200],
                    usuario_id=usuario_id)
    db.session.add(c)
    db.session.flush()
    return c


def _validar(d, existe):
    """Devuelve (datos, motivo_rechazo)."""
    codigo = d.get("codigo", "").strip().removesuffix(".0")
    if not codigo:
        return None, "Falta el código"
    if d.get("_dup"):
        return None, f"El código {codigo} está repetido en el archivo (se usó la fila {d['_dup']})"
    try:
        num = {k: (_num(d.get(k)) if d.get(k, "") != "" else None)
               for k in ("stock", "costo", "precio_detal", "precio_mayor", "precio_taller", "stock_minimo", "iva")}
    except ValueError as e:
        return None, str(e)
    if any(v is not None and v < 0 for v in num.values()):
        return None, "Hay valores negativos"
    if num["iva"] is not None and num["iva"] not in (0, 5, 19):
        return None, "El IVA debe ser 0, 5 o 19"
    nombre = d.get("nombre", "").strip()
    v = d.get("virtual", "").strip().upper()
    virtual = None if not v else v in ("SI", "SÍ", "S", "1", "X", "TRUE", "VERDADERO")
    if virtual and not existe and not num["costo"]:
        return None, "Repuesto virtual sin costo (escribe el costo virtual en la columna costo)"
    if virtual and (num["stock"] or 0) > 0:
        return None, "Un repuesto virtual no lleva stock (déjalo vacío o en 0)"
    if not existe:
        faltan = []
        if not nombre:
            faltan.append("nombre")
        if not num["precio_detal"]:
            faltan.append("precio detal")
        if faltan:
            return None, "Falta(n): " + ", ".join(faltan)
        if not virtual and (num["stock"] or 0) > 0 and not num["costo"]:
            return None, "Trae stock inicial pero no el costo unitario"
    datos = {"codigo": codigo[:60], "nombre": nombre[:200], "num": num,
             "categoria": d.get("categoria", "").strip()[:80],
             "texto": {k: d.get(k, "").strip()[:n] for k, n in
                       (("marca", 80), ("referencia", 80), ("compatibilidad", 400), ("ubicacion", 60),
                        ("unidad", 20)) if d.get(k, "").strip()},
             "codigo_barras": d.get("codigo_barras", "").strip().removesuffix(".0")[:60],
             "en_tienda": d.get("en_tienda", "").strip().upper(), "virtual": virtual}
    if datos["texto"].get("unidad"):
        datos["texto"]["unidad"] = datos["texto"]["unidad"].lower()
    return datos, None


def _aplicar(datos, p, cats, carga, doc_lote, usuario_id):
    """Crea o actualiza el repuesto. Devuelve True si es nuevo."""
    nuevo = p is None
    if nuevo:
        p = Producto(codigo=datos["codigo"], stock=0, costo_promedio=0, unidad="unidad",
                     precio_mayor=0, precio_taller=0, stock_minimo=0, iva_pct=0, en_tienda=True)
        db.session.add(p)
    if datos["nombre"]:
        p.nombre = datos["nombre"]
    if datos["categoria"]:
        cat = cats.get(datos["categoria"].lower())
        if cat is None:
            cat = Categoria(nombre=datos["categoria"])
            db.session.add(cat)
            cats[datos["categoria"].lower()] = cat
        p.categoria = cat
    for k, v in datos["texto"].items():
        setattr(p, k, v)
    if datos["codigo_barras"]:
        p.codigo_barras = datos["codigo_barras"]
    n = datos["num"]
    for k in ("precio_detal", "precio_mayor", "precio_taller", "stock_minimo"):
        if n[k] is not None:
            setattr(p, k, n[k])
    if n["iva"] is not None:
        p.iva_pct = n["iva"]
    if datos["en_tienda"]:
        p.en_tienda = datos["en_tienda"] not in ("NO", "N", "0", "FALSE", "FALSO")
    if datos["virtual"] is not None:
        if datos["virtual"] and (p.stock or 0) > 0:
            raise ErrorNegocio(f"{p.codigo} tiene unidades reales; no puede pasar a la bodega virtual")
        p.virtual = datos["virtual"]
    if p.virtual:
        if n["costo"]:
            p.costo_virtual = n["costo"]
    elif nuevo:
        if (n["stock"] or 0) > 0:
            entrada(p, n["stock"], n["costo"], "inicial", doc_lote, "carga", carga.id, usuario_id,
                    nota=f"Carga masiva {carga.archivo or ''}", actualizar_ultimo=True)
        elif n["costo"]:
            p.ultimo_costo = n["costo"]
    return nuevo


def procesar_lote(carga, filas, indice, usuario_id):
    """Procesa un bloque de filas. Cada fila queda aceptada o rechazada con su motivo.
    No hace commit (lo hace la ruta)."""
    if carga.estado != "en_progreso":
        raise ErrorNegocio("Esta carga ya se cerró.")
    if len(filas) > TAM_LOTE * 3:
        raise ErrorNegocio("El lote es demasiado grande.")
    doc_lote = f"{carga.consecutivo}-{letra_lote(indice)}"
    filas = [normalizar_fila(f) for f in filas if isinstance(f, dict)]
    rechazados, validas = [], []
    for f in filas:
        if str(f.get("codigo", "")).upper().startswith("EJEMPLO"):
            continue
        validas.append(f)
    codigos = {str(f.get("codigo", "")).strip().removesuffix(".0").lower() for f in validas} - {""}
    existentes = {}
    lista = list(codigos)
    for i in range(0, len(lista), 500):
        for p in Producto.query.filter(db.func.lower(Producto.codigo).in_(lista[i:i + 500])):
            existentes[p.codigo.lower()] = p
    cats = {c.nombre.lower(): c for c in Categoria.query.all()}

    def rechazar(f, motivo):
        rechazados.append({"fila": f.get("_fila"), "codigo": f.get("codigo", ""),
                           "nombre": f.get("nombre", ""), "motivo": motivo, "lote": doc_lote})

    preparados, vistos = [], {}
    for f in validas:
        cod = str(f.get("codigo", "")).strip().removesuffix(".0").lower()
        if cod and cod in vistos and not f.get("_dup"):
            f["_dup"] = vistos[cod]
        vistos.setdefault(cod, f.get("_fila"))
        datos, motivo = _validar(f, cod in existentes)
        if motivo:
            rechazar(f, motivo)
        else:
            preparados.append((f, datos))

    creados = actualizados = 0
    try:
        with db.session.begin_nested():
            for f, datos in preparados:
                if _aplicar(datos, existentes.get(datos["codigo"].lower()), cats, carga, doc_lote, usuario_id):
                    creados += 1
                else:
                    actualizados += 1
            db.session.flush()
    except (IntegrityError, ErrorNegocio):
        # Algo chocó (otra carga al mismo tiempo, etc.): se reintenta fila por fila.
        creados = actualizados = 0
        cats = {c.nombre.lower(): c for c in Categoria.query.all()}
        for f, datos in preparados:
            p = Producto.query.filter(db.func.lower(Producto.codigo) == datos["codigo"].lower()).first()
            try:
                with db.session.begin_nested():
                    if _aplicar(datos, p, cats, carga, doc_lote, usuario_id):
                        creados += 1
                    else:
                        actualizados += 1
                    db.session.flush()
            except IntegrityError:
                cats = {c.nombre.lower(): c for c in Categoria.query.all()}
                rechazar(f, "No se pudo guardar (código o categoría en conflicto)")
            except ErrorNegocio as e:
                rechazar(f, " ".join(e.errores))

    db.session.add(CargaLote(carga=carga, indice=indice, filas=len(filas), creados=creados,
                             actualizados=actualizados, rechazados=len(rechazados)))
    carga.creados = (carga.creados or 0) + creados
    carga.actualizados = (carga.actualizados or 0) + actualizados
    if rechazados:
        carga.rechazados_json = json.dumps(carga.rechazados + rechazados, ensure_ascii=False)
    return {"consecutivo_lote": doc_lote, "creados": creados, "actualizados": actualizados,
            "rechazados": len(rechazados)}


def finalizar_carga(carga):
    carga.estado = "completo"
    return {"consecutivo": carga.consecutivo, "creados": carga.creados, "actualizados": carga.actualizados,
            "rechazados": carga.total_rechazados}


def rechazados_csv(carga):
    lineas = ["fila;codigo;nombre;lote;motivo"]
    for r in carga.rechazados:
        lineas.append(";".join(str(r.get(k) or "").replace(";", ",").replace("\n", " ")
                               for k in ("fila", "codigo", "nombre", "lote", "motivo")))
    return ("\ufeff" + "\r\n".join(lineas) + "\r\n").encode("utf-8")
