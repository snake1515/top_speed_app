"""Inversión inicial (cómo se fundó el negocio) y gastos fijos que se repiten cada mes."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta

from models import AporteSocio, Gasto, GastoFijo, Inversion, Movimiento, Socio, db


# ------------------------------------------------------------------ inversión inicial
def inventario_inicial_app(apertura=None):
    """Valor al costo de la mercancía que entró como inventario inicial / carga masiva (antes de abrir)."""
    q = db.session.query(db.func.coalesce(db.func.sum(Movimiento.cantidad * Movimiento.costo_unitario), 0)) \
        .filter(Movimiento.tipo.in_(("inicial", "carga")), Movimiento.cantidad > 0)
    if apertura:
        q = q.filter(Movimiento.fecha < datetime.combine(apertura + timedelta(days=1), datetime.min.time()))
    return float(q.scalar() or 0)


def resumen_fundacion(cfg, neta_desde_apertura=None):
    invs = Inversion.query.filter_by(anulado=False).order_by(Inversion.fecha, Inversion.id).all()
    aportes = AporteSocio.query.filter_by(anulado=False).order_by(AporteSocio.fecha, AporteSocio.id).all()
    socios = Socio.query.order_by(Socio.nombre).all()

    total = sum(i.valor for i in invs)
    por_cat = defaultdict(float)
    por_tipo = defaultdict(float)
    for i in invs:
        por_cat[i.categoria] += i.valor
        por_tipo[i.tipo] += i.valor

    # lo que puso cada socio = aportes al fondo común + compras que pagó de su bolsillo
    por_socio = {s.id: {"socio": s, "aportes": 0.0, "pagos": 0.0} for s in socios}
    for a in aportes:
        por_socio.setdefault(a.socio_id, {"socio": a.socio, "aportes": 0.0, "pagos": 0.0})["aportes"] += a.valor
    for i in invs:
        if i.socio_id:
            por_socio.setdefault(i.socio_id, {"socio": i.socio, "aportes": 0.0, "pagos": 0.0})["pagos"] += i.valor
    total_socios = sum(d["aportes"] + d["pagos"] for d in por_socio.values())
    filas_socio = []
    for d in por_socio.values():
        puso = d["aportes"] + d["pagos"]
        if puso or d["socio"].activo:
            filas_socio.append({**d, "total": puso, "pct": puso / total_socios * 100 if total_socios else 0})
    filas_socio.sort(key=lambda x: -x["total"])

    del_fondo = sum(i.valor for i in invs if not i.socio_id)
    fondo = sum(a.valor for a in aportes) - del_fondo   # dinero aportado que no se ha gastado (o faltante si < 0)

    inv_app = inventario_inicial_app(cfg.fecha_apertura)
    mercancia_registrada = por_tipo.get("inventario", 0)

    rec = None
    if neta_desde_apertura is not None and cfg.fecha_apertura and total:
        meses = max((date.today() - cfg.fecha_apertura).days / 30.44, 0)
        recuperado = max(neta_desde_apertura, 0)
        pct = min(recuperado / total * 100, 100)
        falta = max(total - neta_desde_apertura, 0)
        ritmo = neta_desde_apertura / meses if meses >= 1 else None
        rec = {"neta": neta_desde_apertura, "pct": pct, "falta": falta, "meses_abierto": meses,
               "ritmo": ritmo, "meses_faltan": (falta / ritmo) if ritmo and ritmo > 0 else None}

    linea = [(i.fecha, i.concepto, i.valor, i.categoria, i.socio.nombre if i.socio else "Fondo común") for i in invs]
    linea += [(a.fecha, f"Aporte de {a.socio.nombre}", a.valor, "Aporte de socio", a.socio.nombre) for a in aportes]
    linea.sort(key=lambda x: x[0])

    return {"invs": invs, "aportes": aportes, "socios": socios, "total": total,
            "por_cat": sorted(por_cat.items(), key=lambda x: -x[1]), "por_tipo": dict(por_tipo),
            "por_socio": filas_socio, "total_socios": total_socios, "fondo": fondo, "del_fondo": del_fondo,
            "inv_app": inv_app, "mercancia_registrada": mercancia_registrada, "rec": rec,
            "primera": invs[0].fecha if invs else None, "linea": linea}


# ------------------------------------------------------------------ gastos fijos
def periodo_de(d):
    return f"{d.year:04d}-{d.month:02d}"


def fijos_del_mes(hoy=None):
    """Gastos fijos activos con su estado este mes: pagado (con el gasto) o pendiente (con días de atraso)."""
    hoy = hoy or date.today()
    per = periodo_de(hoy)
    pagos = {g.gasto_fijo_id: g for g in Gasto.query.filter(Gasto.periodo == per, Gasto.anulado.is_(False),
                                                             Gasto.gasto_fijo_id.isnot(None))}
    out = []
    for f in GastoFijo.query.filter_by(activo=True).order_by(GastoFijo.dia, GastoFijo.nombre):
        if f.desde and periodo_de(f.desde) > per:
            continue
        vence = date(hoy.year, hoy.month, min(max(f.dia or 1, 1), monthrange(hoy.year, hoy.month)[1]))
        out.append({"fijo": f, "pago": pagos.get(f.id), "vence": vence, "atraso": (hoy - vence).days})
    return out


# ------------------------------------------------------------------ recibo de aporte (PNG para WhatsApp)
_UNI = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece",
        "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós",
        "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
_DEC = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
_CEN = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos",
        "ochocientos", "novecientos"]


def _menor_mil(n):
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    partes = [_CEN[c]] if c else []
    if r < 30:
        partes.append(_UNI[r])
    else:
        d, u = divmod(r, 10)
        partes.append(_DEC[d] + (f" y {_UNI[u]}" if u else ""))
    return " ".join(p for p in partes if p)


def en_letras(valor):
    """1500000 -> 'un millón quinientos mil pesos M/CTE'."""
    n = int(round(valor or 0))
    if n == 0:
        return "cero pesos M/CTE"
    if n == 1:
        return "un peso M/CTE"
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones:
        partes.append("un millón" if millones == 1 else f"{_apocope(_letras_grupo(millones))} millones")
    if miles:
        partes.append("mil" if miles == 1 else f"{_apocope(_menor_mil(miles))} mil")
    if unidades:
        partes.append(_menor_mil(unidades))
    texto = " ".join(partes)
    de = " de" if millones and not miles and not unidades else ""
    return f"{_apocope(texto)}{de} pesos M/CTE"


def _letras_grupo(n):
    if n < 1000:
        return _menor_mil(n)
    m, r = divmod(n, 1000)
    return ("mil" if m == 1 else f"{_apocope(_menor_mil(m))} mil") + (f" {_menor_mil(r)}" if r else "")


def _apocope(s):
    """'uno' -> 'un' al final ('veintiuno mil' -> 'veintiún mil')."""
    if s.endswith("veintiuno"):
        return s[:-9] + "veintiún"
    if s.endswith("uno"):
        return s[:-3] + "un"
    return s


def _pesos(v):
    return "$ " + f"{round(v or 0):,.0f}".replace(",", ".")


def recibo_aporte_png(a, cfg, total_socio, pct_socio, logo_path, fuentes_dir):
    """Imagen PNG del recibo de aporte (1080 px de ancho) para compartir por WhatsApp."""
    import os
    from PIL import Image, ImageDraw, ImageFont

    def F(nombre, tam):
        return ImageFont.truetype(os.path.join(fuentes_dir, nombre), tam)
    neg, bol, reg = "InterDisplay-Black.otf", "Inter-Bold.otf", "Inter-Regular.otf"
    NEGRO, AMA, ROJO, GRIS, BLANCO = (11, 10, 7), (255, 196, 0), (232, 36, 60), (110, 106, 96), (255, 255, 255)
    Wd, M = 1080, 70
    img = Image.new("RGB", (Wd, 2200), BLANCO)
    d = ImageDraw.Draw(img)

    # encabezado negro con el logo y los datos del negocio (alto según lo que haya)
    datos = [x for x in [f"NIT {cfg.nit}" if cfg.nit else "", cfg.direccion or "",
                         " · ".join(z for z in [cfg.ciudad or "", f"Tel. {cfg.telefono}" if cfg.telefono else ""] if z)] if x]
    try:
        lg = Image.open(logo_path).convert("RGBA")
        lw = 420
        lg = lg.resize((lw, round(lw * lg.height / lg.width)), Image.LANCZOS)
    except Exception:
        lg = None
    alto_enc = 34 + (lg.height + 18 if lg else 30) + 40 * len(datos) + 26
    d.rectangle([0, 0, Wd, alto_enc], fill=NEGRO)
    y = 34
    if lg:
        img.paste(lg, ((Wd - lg.width) // 2, y), lg)
        y += lg.height + 18
    for linea in datos:
        f = F(reg, 30)
        w = d.textlength(linea, font=f)
        d.text(((Wd - w) / 2, y), linea, font=f, fill=(236, 230, 210))
        y += 40
    d.rectangle([0, alto_enc, Wd, alto_enc + 12], fill=AMA)
    d.rectangle([0, alto_enc + 12, Wd, alto_enc + 20], fill=ROJO)
    y = alto_enc + 60

    def centro(txt, f, color, yy):
        w = d.textlength(txt, font=f)
        d.text(((Wd - w) / 2, yy), txt, font=f, fill=color)

    centro("RECIBO DE APORTE DE SOCIO", F(neg, 50), NEGRO, y)
    y += 74
    centro(a.folio, F(neg, 64), ROJO, y)
    y += 100

    def fila(lbl, val, fuerte=False):
        nonlocal y
        d.text((M, y), lbl, font=F(reg, 34), fill=GRIS)
        f = F(bol if fuerte else reg, 36)
        # el valor puede ser largo: se parte en líneas a la derecha
        palabras, lineas, actual = str(val).split(), [], ""
        for p in palabras:
            prueba = (actual + " " + p).strip()
            if d.textlength(prueba, font=f) > Wd - 2 * M - 330 and actual:
                lineas.append(actual)
                actual = p
            else:
                actual = prueba
        lineas.append(actual)
        for i, ln in enumerate(lineas):
            w = d.textlength(ln, font=f)
            d.text((Wd - M - w, y + i * 46), ln, font=f, fill=NEGRO)
        y += 46 * len(lineas) + 18
        d.line([M, y - 9, Wd - M, y - 9], fill=(232, 228, 216), width=2)

    fila("Fecha del aporte", a.fecha.strftime("%d/%m/%Y"))
    fila("Socio", a.socio.nombre, True)
    if a.socio.documento:
        fila("Cédula / NIT", a.socio.documento)
    if a.medio:
        fila("Medio", a.medio)
    if a.nota:
        fila("Concepto", a.nota)
    y += 20

    # valor destacado
    d.rounded_rectangle([M, y, Wd - M, y + 190], radius=28, fill=NEGRO)
    centro("VALOR APORTADO", F(bol, 32), AMA, y + 26)
    centro(_pesos(a.valor), F(neg, 84), BLANCO, y + 72)
    y += 220
    # en letras (varias líneas si hace falta)
    f = F(reg, 32)
    txt = "Son: " + en_letras(a.valor)
    palabras, actual = txt.split(), ""
    for p in palabras:
        prueba = (actual + " " + p).strip()
        if d.textlength(prueba, font=f) > Wd - 2 * M and actual:
            centro(actual, f, NEGRO, y); y += 42; actual = p
        else:
            actual = prueba
    centro(actual, f, NEGRO, y)
    y += 70

    fila("Total aportado por el socio", _pesos(total_socio), True)
    fila("Participación a la fecha", f"{pct_socio:.1f} %".replace(".", ","), True)
    y += 90

    # firmas
    mitad = Wd // 2
    for x0, x1, t1, t2 in [(M, mitad - 30, "Recibí", cfg.nombre or "Moto Speed"), (mitad + 30, Wd - M, "Entregó", a.socio.nombre)]:
        d.line([x0, y, x1, y], fill=NEGRO, width=3)
        w = d.textlength(t1, font=F(bol, 30)); d.text(((x0 + x1 - w) / 2, y + 12), t1, font=F(bol, 30), fill=NEGRO)
        f2 = F(reg, 26)
        t2 = t2 if d.textlength(t2, font=f2) < x1 - x0 else t2[:28] + "…"
        w = d.textlength(t2, font=f2); d.text(((x0 + x1 - w) / 2, y + 52), t2, font=f2, fill=GRIS)
    y += 130

    pie = f"Registró {a.usuario.nombre if a.usuario else ''} · {a.creado.strftime('%d/%m/%Y %I:%M %p')}"
    centro(pie, F(reg, 24), GRIS, y)
    y += 60
    d.rectangle([0, y, Wd, y + 14], fill=AMA)
    d.rectangle([0, y + 14, Wd, y + 22], fill=ROJO)
    y += 22

    img = img.crop((0, 0, Wd, y))
    if a.anulado:   # marca de agua de anulado
        capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dc = ImageDraw.Draw(capa)
        f = F(neg, 200)
        w = dc.textlength("ANULADO", font=f)
        dc.text(((Wd - w) / 2, img.height / 2 - 120), "ANULADO", font=f, fill=(232, 36, 60, 90))
        capa = capa.rotate(25, center=(Wd / 2, img.height / 2))
        img = Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------------ reporte de aportes
def reporte_aportes(cfg, desde=None, hasta=None, socio_id=None):
    """Aportes en el periodo (y de un socio si se elige) + resumen por socio con participación total."""
    q = AporteSocio.query.filter_by(anulado=False)
    if desde:
        q = q.filter(AporteSocio.fecha >= desde)
    if hasta:
        q = q.filter(AporteSocio.fecha <= hasta)
    if socio_id:
        q = q.filter(AporteSocio.socio_id == socio_id)
    aportes = q.order_by(AporteSocio.fecha, AporteSocio.id).all()
    general = resumen_fundacion(cfg)
    en_periodo = defaultdict(float)
    for a in aportes:
        en_periodo[a.socio_id] += a.valor
    filas = []
    for x in general["por_socio"]:
        if socio_id and x["socio"].id != socio_id:
            continue
        filas.append({**x, "periodo": en_periodo.get(x["socio"].id, 0.0)})
    return {"aportes": aportes, "socios": filas, "total_periodo": sum(a.valor for a in aportes),
            "total_aportes": sum(x["aportes"] for x in general["por_socio"]),
            "total_pagos": sum(x["pagos"] for x in general["por_socio"]), "total_socios": general["total_socios"],
            "fondo": general["fondo"], "del_fondo": general["del_fondo"], "total_invertido": general["total"],
            "desde": desde, "hasta": hasta, "socio": filas[0]["socio"] if socio_id and filas else None}
