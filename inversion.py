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
