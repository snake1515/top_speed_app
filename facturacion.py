"""Facturación electrónica con el facturador gratuito de la DIAN.

La DIAN no tiene una forma de que otro programa le envíe facturas a su facturador gratuito: allá se digitan.
Aquí la app lleva el control: qué ventas faltan por facturar, los datos listos para copiar, el número y el
CUFE de cada factura, y cuánto se ha vendido en el año frente al tope de 3.500 UVT (para saber cuándo se
vuelve obligatorio facturar).
"""
from datetime import date, datetime

from models import Venta, db

URL_DIAN = "https://catalogo-vpfe.dian.gov.co/User/Login"
CONSUMIDOR_FINAL = "222222222222"


def tope_anual(cfg, anio=None):
    """Ingresos brutos del año (ventas no anuladas menos devoluciones) frente al tope en UVT."""
    anio = anio or date.today().year
    ini, fin = datetime(anio, 1, 1), datetime(anio + 1, 1, 1)
    vendido, devuelto = db.session.query(db.func.coalesce(db.func.sum(Venta.total), 0),
                                         db.func.coalesce(db.func.sum(Venta.devuelto), 0)) \
        .filter(Venta.fecha >= ini, Venta.fecha < fin, Venta.estado != "anulada").one()
    ingresos = float(vendido or 0) - float(devuelto or 0)
    tope = (cfg.fe_tope_uvt or 3500) * (cfg.fe_uvt or 0)
    pct = ingresos / tope * 100 if tope else 0
    # proyección al cierre del año con el ritmo desde la primera venta del año (la app pudo empezar a mitad de año)
    hoy = date.today()
    primera = db.session.query(db.func.min(Venta.fecha)).filter(Venta.fecha >= ini, Venta.fecha < fin).scalar()
    fin_anio = date(anio, 12, 31)
    desde = primera.date() if primera else hoy
    corte = min(hoy, fin_anio)
    dias = (corte - desde).days + 1
    restan = max((fin_anio - corte).days, 0)
    proyeccion = ingresos + (ingresos / dias * restan if dias >= 7 else 0)
    nivel = "pasado" if pct >= 100 else ("aviso" if pct >= (cfg.fe_aviso_pct or 80) else "ok")
    return {"anio": anio, "ingresos": ingresos, "tope": tope, "pct": pct, "nivel": nivel, "falta": max(tope - ingresos, 0),
            "proyeccion": proyeccion, "uvt": cfg.fe_uvt, "tope_uvt": cfg.fe_tope_uvt}


def pendientes(cfg):
    """Ventas que faltan por facturar en la DIAN (desde la fecha configurada)."""
    q = Venta.query.filter(Venta.estado != "anulada", db.or_(Venta.fe_numero.is_(None), Venta.fe_numero == ""))
    if cfg.fe_desde:
        q = q.filter(Venta.fecha >= datetime.combine(cfg.fe_desde, datetime.min.time()))
    return q


def lineas_factura(v):
    """Renglones como se digitan en el facturador: valor unitario antes de IVA, ya con el descuento repartido."""
    out = []
    for it in v.items:
        q = (it.cantidad or 0) - (it.devuelto or 0)
        if q <= 0:
            continue
        total = (it.neto if it.neto is not None else it.total) * (q / it.cantidad if it.cantidad else 1)
        iva = it.iva_pct or 0
        base = total / (1 + iva / 100) if iva else total
        out.append({"codigo": (it.producto.codigo_interno if it.producto and it.producto.codigo_interno else it.codigo) or "",
                    "nombre": it.nombre, "cantidad": q, "unitario": round(base / q, 2) if q else 0,
                    "iva_pct": iva, "iva": round(total - base, 2), "total": round(total, 2)})
    return out
