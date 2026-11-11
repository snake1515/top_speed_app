"""Cálculos de reportes: ventas, costo, utilidad, gastos, nómina y ganancia neta."""
from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO

from models import (MOTIVOS_AJUSTE, Ajuste, Devolucion, Gasto, MovCaja, PagoNomina, Producto, Venta,
                    VentaItem, db)


def _rango(desde, hasta):
    return datetime.combine(desde, datetime.min.time()), datetime.combine(hasta + timedelta(days=1), datetime.min.time())


def resumen(desde, hasta):
    ini, fin = _rango(desde, hasta)
    ventas = Venta.query.filter(Venta.fecha >= ini, Venta.fecha < fin, Venta.estado != "anulada").all()
    devs = (Devolucion.query.join(Venta).filter(Devolucion.fecha >= ini, Devolucion.fecha < fin,
                                                Venta.estado != "anulada").all())
    bruto = sum(v.total for v in ventas)
    iva = sum(v.iva for v in ventas)
    costo = sum(v.costo for v in ventas)
    dev_valor = sum(d.valor for d in devs)
    dev_iva = sum(d.iva for d in devs)
    dev_costo = sum(d.costo for d in devs)
    ventas_netas = bruto - dev_valor
    sin_iva = ventas_netas - (iva - dev_iva)
    costo_neto = costo - dev_costo
    utilidad_bruta = sin_iva - costo_neto
    gastos = Gasto.query.filter(Gasto.fecha >= ini, Gasto.fecha < fin, Gasto.anulado.is_(False)).all()
    total_gastos = sum(g.valor for g in gastos)
    gastos_cat = defaultdict(float)
    for g in gastos:
        gastos_cat[g.categoria] += g.valor
    nominas = PagoNomina.query.filter(PagoNomina.fecha_pago >= desde, PagoNomina.fecha_pago <= hasta,
                                      PagoNomina.anulada.is_(False)).all()
    total_nomina = sum(p.costo_empresa for p in nominas)
    ajustes = Ajuste.query.filter(Ajuste.fecha >= ini, Ajuste.fecha < fin, Ajuste.anulado.is_(False),
                                  Ajuste.motivo != "inicial").all()
    perdidas = sum(a.valor if a.sentido == "salida" else -a.valor for a in ajustes)
    neta = utilidad_bruta - total_gastos - total_nomina - perdidas
    # por día
    dias = defaultdict(lambda: {"ventas": 0.0, "utilidad": 0.0})
    for v in ventas:
        k = v.fecha.date()
        dias[k]["ventas"] += v.total
        dias[k]["utilidad"] += v.total - v.iva - v.costo
    for d in devs:
        k = d.fecha.date()
        dias[k]["ventas"] -= d.valor
        dias[k]["utilidad"] -= d.valor - d.iva - d.costo
    serie = []
    d = desde
    while d <= hasta:
        serie.append((d, dias[d]["ventas"], dias[d]["utilidad"]))
        d += timedelta(days=1)
    # ingresos por medio (dinero real que entró)
    movs = MovCaja.query.filter(MovCaja.fecha >= ini, MovCaja.fecha < fin, MovCaja.anulado.is_(False),
                                MovCaja.origen.in_(["venta", "abono"])).all()
    por_medio = defaultdict(float)
    for m in movs:
        por_medio[m.medio.nombre] += m.valor
    return {
        "ventas": ventas, "num_ventas": len(ventas), "bruto": bruto, "devoluciones": dev_valor,
        "ventas_netas": ventas_netas, "iva": iva - dev_iva, "sin_iva": sin_iva, "costo": costo_neto,
        "utilidad_bruta": utilidad_bruta, "margen": (utilidad_bruta / sin_iva * 100) if sin_iva else 0,
        "gastos": total_gastos, "gastos_cat": sorted(gastos_cat.items(), key=lambda x: -x[1]),
        "nomina": total_nomina, "perdidas": perdidas, "neta": neta, "serie": serie,
        "por_medio": sorted(por_medio.items(), key=lambda x: -x[1]),
        "ticket_promedio": (bruto / len(ventas)) if ventas else 0,
        "credito": sum(v.saldo for v in ventas),
    }


def top_productos(desde, hasta, limite=15):
    ini, fin = _rango(desde, hasta)
    filas = (db.session.query(VentaItem.producto_id, VentaItem.codigo, VentaItem.nombre,
                              db.func.sum(VentaItem.cantidad - VentaItem.devuelto),
                              db.func.sum(VentaItem.neto * (VentaItem.cantidad - VentaItem.devuelto) / VentaItem.cantidad),
                              db.func.sum(VentaItem.costo_unitario * (VentaItem.cantidad - VentaItem.devuelto)))
             .join(Venta).filter(Venta.fecha >= ini, Venta.fecha < fin, Venta.estado != "anulada")
             .group_by(VentaItem.producto_id, VentaItem.codigo, VentaItem.nombre).all())
    out = [{"id": f[0], "codigo": f[1], "nombre": f[2], "cantidad": float(f[3] or 0), "ventas": float(f[4] or 0),
            "costo": float(f[5] or 0)} for f in filas]
    for o in out:
        o["utilidad"] = o["ventas"] - o["costo"]
    return sorted(out, key=lambda o: -o["ventas"])[:limite]


def sin_movimiento(dias=90, limite=30):
    """Repuestos con stock que no se venden hace tiempo (plata quieta)."""
    corte = datetime.now() - timedelta(days=dias)
    vendidos = {pid for (pid,) in db.session.query(VentaItem.producto_id).join(Venta)
                .filter(Venta.fecha >= corte, Venta.estado != "anulada").distinct()}
    prods = Producto.query.filter(Producto.activo.is_(True), Producto.stock > 0).all()
    quietos = [p for p in prods if p.id not in vendidos and p.creado and p.creado < corte]
    return sorted(quietos, key=lambda p: -p.valor_inventario)[:limite]


def excel(desde, hasta, cfg):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    r = resumen(desde, hasta)
    wb = Workbook()
    titulo = Font(bold=True, size=14)
    neg = Font(bold=True, color="FFFFFF")
    relleno = PatternFill("solid", fgColor="0B0A07")
    moneda = '"$" #,##0'

    def hoja(ws, encabezados, filas, formatos=None):
        ws.append(encabezados)
        for c in ws[ws.max_row]:
            c.font = neg
            c.fill = relleno
            c.alignment = Alignment(vertical="center")
        for f in filas:
            ws.append(list(f))
        for i, _ in enumerate(encabezados, 1):
            ws.column_dimensions[get_column_letter(i)].width = 18
        for col, fmt in (formatos or {}).items():
            for celda in ws[col][1:]:
                celda.number_format = fmt
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Resumen"
    ws.append([f"{cfg.nombre} · Resultados del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"])
    ws["A1"].font = titulo
    ws.append([])
    for etq, val in [("Ventas brutas", r["bruto"]), ("Devoluciones", -r["devoluciones"]),
                     ("Ventas netas (con IVA)", r["ventas_netas"]), ("IVA", -r["iva"]),
                     ("Ventas sin IVA", r["sin_iva"]), ("Costo de lo vendido (promedio)", -r["costo"]),
                     ("Utilidad bruta", r["utilidad_bruta"]), ("Gastos", -r["gastos"]),
                     ("Nómina (costo empresa)", -r["nomina"]), ("Pérdidas por ajustes", -r["perdidas"]),
                     ("GANANCIA NETA", r["neta"])]:
        ws.append([etq, round(val)])
        ws.cell(ws.max_row, 2).number_format = moneda
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.cell(ws.max_row, 2).font = Font(bold=True)
    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 18

    ws = wb.create_sheet("Ventas")
    hoja(ws, ["Folio", "Fecha", "Cliente", "Vendedor", "Origen", "Total", "Devuelto", "IVA", "Costo", "Utilidad",
              "Pagado", "Saldo", "Estado"],
         [(v.folio, v.fecha.strftime("%Y-%m-%d %H:%M"), v.cliente_nombre or "", v.usuario.nombre if v.usuario else "",
           v.origen, v.total, v.devuelto, v.iva, round(v.costo), round(v.ganancia), v.pagado, v.saldo, v.estado)
          for v in r["ventas"]], {c: moneda for c in "FGHIJKL"})

    ws = wb.create_sheet("Más vendidos")
    hoja(ws, ["Código", "Repuesto", "Unidades", "Ventas", "Costo", "Utilidad"],
         [(t["codigo"], t["nombre"], t["cantidad"], round(t["ventas"]), round(t["costo"]), round(t["utilidad"]))
          for t in top_productos(desde, hasta, 500)], {c: moneda for c in "DEF"})

    ws = wb.create_sheet("Inventario")
    prods = Producto.query.filter(Producto.activo.is_(True)).order_by(Producto.nombre).all()
    hoja(ws, ["Código", "Repuesto", "Categoría", "Marca", "Ubicación", "Stock", "Mínimo", "Costo promedio",
              "Valor inventario", "Precio detal", "Precio mayor", "Precio taller"],
         [(p.codigo, p.nombre, p.categoria.nombre if p.categoria else "", p.marca or "", p.ubicacion or "",
           p.stock, p.stock_minimo, round(p.costo_promedio), round(p.valor_inventario), p.precio_detal,
           p.precio_mayor, p.precio_taller) for p in prods], {c: moneda for c in "HIJKL"})

    ws = wb.create_sheet("Gastos")
    ini, fin = _rango(desde, hasta)
    gastos = Gasto.query.filter(Gasto.fecha >= ini, Gasto.fecha < fin, Gasto.anulado.is_(False)).order_by(Gasto.fecha).all()
    hoja(ws, ["Fecha", "Categoría", "Descripción", "Valor", "Medio"],
         [(g.fecha.strftime("%Y-%m-%d"), g.categoria, g.descripcion, g.valor, g.medio.nombre if g.medio else "")
          for g in gastos], {"D": moneda})

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


__all__ = ["resumen", "top_productos", "sin_movimiento", "excel", "MOTIVOS_AJUSTE"]
