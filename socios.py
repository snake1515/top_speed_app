"""Reporte para socios: página de solo consulta con enlace secreto (sin usuario ni clave).
Junta ventas, gastos, compras, cuentas por pagar, cartera, caja y los aportes de cada socio."""
from collections import defaultdict
from datetime import date, datetime, timedelta

import reportes as rpt
from inversion import fijos_del_mes, reporte_aportes
from models import Compra, Gasto, Producto, Venta, ahora
from services import resumen_turno, saldos_caja_mayor, turno_abierto

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
PERIODOS = [("hoy", "Hoy"), ("ayer", "Ayer"), ("semana", "Esta semana"), ("mes", "Este mes"), ("mes_pasado", "Mes pasado")]


def _fecha(txt):
    try:
        return datetime.strptime(txt or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def rango(per, desde_txt=None, hasta_txt=None):
    """(clave, desde, hasta, nombre, desde_ant, hasta_ant) · el periodo anterior tiene la misma duración."""
    hoy = ahora().date()
    d, h = _fecha(desde_txt), _fecha(hasta_txt)
    if d and h:
        if d > h:
            d, h = h, d
        dias = (h - d).days + 1
        nombre = f"{d.strftime('%d/%m/%Y')} al {h.strftime('%d/%m/%Y')}"
        return "rango", d, h, nombre, d - timedelta(days=dias), d - timedelta(days=1)
    if per == "ayer":
        a = hoy - timedelta(days=1)
        return "ayer", a, a, f"Ayer {a.strftime('%d/%m/%Y')}", a - timedelta(days=1), a - timedelta(days=1)
    if per == "semana":
        ini = hoy - timedelta(days=hoy.weekday())
        n = (hoy - ini).days
        return "semana", ini, hoy, f"Semana del {ini.strftime('%d/%m')} al {hoy.strftime('%d/%m/%Y')}", ini - timedelta(days=7), ini - timedelta(days=7) + timedelta(days=n)
    if per == "mes":
        ini = hoy.replace(day=1)
        prev_fin = ini - timedelta(days=1)
        prev_ini = prev_fin.replace(day=1)
        # mismo número de días del mes anterior, para comparar parejo
        prev_h = min(prev_ini + timedelta(days=(hoy - ini).days), prev_fin)
        return "mes", ini, hoy, f"{MESES[hoy.month - 1].capitalize()} {hoy.year} (al {hoy.strftime('%d/%m')})", prev_ini, prev_h
    if per == "mes_pasado":
        fin = hoy.replace(day=1) - timedelta(days=1)
        ini = fin.replace(day=1)
        p_fin = ini - timedelta(days=1)
        return "mes_pasado", ini, fin, f"{MESES[ini.month - 1].capitalize()} {ini.year}", p_fin.replace(day=1), p_fin
    return "hoy", hoy, hoy, f"Hoy {hoy.strftime('%d/%m/%Y')}", hoy - timedelta(days=1), hoy - timedelta(days=1)


def _variacion(actual, anterior):
    if not anterior:
        return None
    return (actual - anterior) / abs(anterior) * 100


def datos(cfg, per="hoy", desde_txt=None, hasta_txt=None):
    clave, desde, hasta, nombre, d_ant, h_ant = rango(per, desde_txt, hasta_txt)
    ini, fin = rpt._rango(desde, hasta)
    hoy = ahora().date()
    r = rpt.resumen(desde, hasta)
    ant = rpt.resumen(d_ant, h_ant)
    comp = {k: _variacion(r[k], ant[k]) for k in ("ventas_netas", "utilidad_bruta", "gastos", "neta")}

    # ganancia por cobrar: la parte de la ganancia de las ventas a crédito del periodo que todavía no se ha pagado
    # (proporcional a lo que falta de cada venta)
    por_cobrar, ventas_pend = 0.0, 0
    for v in r["ventas"]:
        saldo = v.saldo or 0
        if saldo > 0.5 and (v.total or 0) > 0:
            util = (v.total or 0) - (v.iva or 0) - (v.costo or 0)
            por_cobrar += util * min(saldo / v.total, 1)
            ventas_pend += 1
    ganancia = {"total": r["neta"], "por_cobrar": round(por_cobrar), "cobrada": round(r["neta"] - por_cobrar),
                "ventas_pend": ventas_pend}

    # gastos uno por uno
    gastos = (Gasto.query.filter(Gasto.fecha >= ini, Gasto.fecha < fin, Gasto.anulado.is_(False))
              .order_by(Gasto.fecha.desc(), Gasto.id.desc()).all())
    fijos = fijos_del_mes(hoy)

    # compras del periodo
    compras = (Compra.query.filter(Compra.fecha >= ini, Compra.fecha < fin, Compra.anulada.is_(False))
               .order_by(Compra.fecha.desc(), Compra.id.desc()).all())

    # cuentas por pagar (hoy, sin importar el periodo)
    por_prov = defaultdict(lambda: {"saldo": 0.0, "vencido": 0.0, "compras": 0, "proximo": None})
    for c in Compra.query.filter(Compra.anulada.is_(False), Compra.pagado < Compra.total).all():
        if (c.saldo or 0) < 0.5:
            continue
        d = por_prov[c.proveedor.nombre if c.proveedor else "Sin proveedor"]
        d["saldo"] += c.saldo
        d["compras"] += 1
        if c.vence and c.vence < hoy:
            d["vencido"] += c.saldo
        if c.vence and (d["proximo"] is None or c.vence < d["proximo"]):
            d["proximo"] = c.vence
    por_pagar = sorted(({"nombre": k, **v} for k, v in por_prov.items()), key=lambda x: -x["saldo"])

    # cartera (hoy)
    por_cli = defaultdict(lambda: {"saldo": 0.0, "vencido": 0.0, "ventas": 0})
    for v in Venta.query.filter_by(estado="credito").all():
        if (v.saldo or 0) < 0.5:
            continue
        d = por_cli[v.cliente_nombre or "Cliente"]
        d["saldo"] += v.saldo
        d["ventas"] += 1
        if v.vence and v.vence < hoy:
            d["vencido"] += v.saldo
    cartera = sorted(({"nombre": k, **v} for k, v in por_cli.items()), key=lambda x: -x["saldo"])

    # caja abierta
    t = turno_abierto()
    caja = None
    if t:
        rt = resumen_turno(t)
        caja = {"turno": t, "esperado": rt["efectivo_esperado"], "ventas": rt["ventas_total"], "num": rt["num_ventas"],
                "medios": [(d["medio"].nombre, d["neto"]) for d in rt["medios"]]}

    # inventario hoy (mismo cálculo de la pantalla Repuestos: stock real × costo promedio, repuestos activos)
    prods = Producto.query.filter(Producto.activo.is_(True)).all()
    con_stock = [p for p in prods if (p.stock or 0) > 0 and not p.virtual]
    por_cat = defaultdict(lambda: {"costo": 0.0, "venta": 0.0, "unidades": 0.0, "refs": 0})
    for p in con_stock:
        c = por_cat[p.categoria.nombre if p.categoria else "Sin categoría"]
        c["costo"] += p.valor_inventario
        c["venta"] += (p.stock or 0) * (p.precio_detal or 0)
        c["unidades"] += p.stock or 0
        c["refs"] += 1
    inv_costo = sum(p.valor_inventario for p in con_stock)
    inv_venta = sum((p.stock or 0) * (p.precio_detal or 0) for p in con_stock)
    inventario = {
        "costo": inv_costo, "venta": inv_venta, "ganancia": inv_venta - inv_costo,
        "unidades": sum(p.stock or 0 for p in con_stock), "refs": len(con_stock),
        "agotados": sum(1 for p in prods if not p.virtual and (p.stock or 0) <= 0),
        "bajos": sum(1 for p in con_stock if (p.stock_minimo or 0) > 0 and (p.stock or 0) <= p.stock_minimo),
        "virtuales": sum(1 for p in prods if p.virtual),
        "por_cat": sorted(({"nombre": k, **v} for k, v in por_cat.items()), key=lambda x: -x["costo"]),
    }

    return {
        "inventario": inventario,
        "ganancia": ganancia,
        "caja_mayor": sorted((d for d in saldos_caja_mayor().values() if abs(d["saldo"]) > 0.5), key=lambda d: d["medio"].orden),
        "clave": clave, "desde": desde, "hasta": hasta, "nombre": nombre, "r": r, "ant": ant, "comp": comp,
        "d_ant": d_ant, "h_ant": h_ant,
        "gastos": gastos, "fijos": fijos, "compras": compras,
        "total_compras": sum(c.total for c in compras),
        "compras_credito": sum(c.saldo for c in compras),
        "por_pagar": por_pagar, "total_por_pagar": sum(x["saldo"] for x in por_pagar),
        "vencido_por_pagar": sum(x["vencido"] for x in por_pagar),
        "cartera": cartera, "total_cartera": sum(x["saldo"] for x in cartera),
        "vencido_cartera": sum(x["vencido"] for x in cartera),
        "top": rpt.top_productos(desde, hasta, 10),
        "caja": caja,
        "aportes": reporte_aportes(cfg),   # de toda la vida del negocio, no depende del periodo
        "generado": ahora(),
    }
