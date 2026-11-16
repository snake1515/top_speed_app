"""Exportación del inventario a Excel: todo, bodega virtual, con inventario, bajo mínimo/agotados y movimientos."""
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import TIPOS_MOV

NEGRO, AMARILLO, ROJO = "0B0A07", "FFC400", "E8243C"
F = "Arial"
PESOS = '"$"#,##0;-"$"#,##0;"-"'
CANT = '#,##0.##;-#,##0.##;"0"'
PCT = '0.0"%";-0.0"%";"-"'


def estado(p):
    if p.virtual:
        return "Virtual"
    if (p.stock or 0) <= 0:
        return "Agotado"
    if p.bajo_minimo:
        return "Bajo mínimo"
    return "Con stock"


def _columnas(ve_costos):
    """(título, ancho, formato, función que saca el valor). Los costos solo salen si el usuario puede verlos."""
    cols = [
        ("Código interno", 13, "@", lambda p, x: p.codigo_interno or ""),
        ("Código proveedor", 22, "@", lambda p, x: p.codigo or ""),
        ("Código de barras", 16, "@", lambda p, x: p.codigo_barras or ""),
        ("Nombre", 44, None, lambda p, x: p.nombre),
        ("Descripción", 40, None, lambda p, x: p.descripcion or ""),
        ("Categoría", 18, None, lambda p, x: p.categoria.nombre if p.categoria else ""),
        ("Marca", 14, None, lambda p, x: p.marca or ""),
        ("Referencia", 14, None, lambda p, x: p.referencia or ""),
        ("Compatibilidad / medida", 36, None, lambda p, x: p.compatibilidad or ""),
        ("Ubicación", 11, None, lambda p, x: p.ubicacion or ""),
        ("Unidad", 9, None, lambda p, x: p.unidad or ""),
        ("Bodega", 10, None, lambda p, x: "Virtual" if p.virtual else "Real"),
        ("Estado", 13, None, lambda p, x: estado(p)),
        ("Stock", 9, CANT, lambda p, x: 0 if p.virtual else (p.stock or 0)),
        ("Apartado web", 10, CANT, lambda p, x: x["reservado"].get(p.id, 0)),
        ("Vendido por surtir", 11, CANT, lambda p, x: x["por_surtir"].get(p.id, 0)),
        ("Stock mínimo", 10, CANT, lambda p, x: p.stock_minimo or 0),
    ]
    if ve_costos:
        cols += [
            ("Costo promedio", 13, PESOS, lambda p, x: p.costo_promedio or 0),
            ("Costo virtual", 13, PESOS, lambda p, x: p.costo_virtual or 0),
            ("Último costo", 13, PESOS, lambda p, x: p.ultimo_costo or 0),
            ("Valor inventario", 15, PESOS, None),  # fórmula: Stock × Costo promedio
        ]
    cols += [
        ("Precio detal", 13, PESOS, lambda p, x: p.precio_detal or 0),
        ("Precio mayor", 13, PESOS, lambda p, x: p.precio_mayor or 0),
        ("Precio taller", 13, PESOS, lambda p, x: p.precio_taller or 0),
    ]
    if ve_costos:
        cols.append(("Margen detal %", 11, PCT, lambda p, x: round(p.margen_detal, 1) if p.margen_detal is not None else None))
    cols += [
        ("IVA %", 7, '0"%"', lambda p, x: p.iva_pct or 0),
        ("En tienda web", 9, None, lambda p, x: "SI" if p.en_tienda else "NO"),
        ("Activo", 8, None, lambda p, x: "SI" if p.activo else "NO"),
    ]
    return cols


# Al volver a subir el Excel estas columnas no cambian nada (son informativas). Encabezado gris.
SOLO_LECTURA = {
    "Código interno": "Lo pone la app y no se cambia. Sirve para reconocer el repuesto al subir el archivo. "
                      "Déjalo vacío en las filas nuevas.",
    "Estado": "Informativo.", "Apartado web": "Informativo.", "Vendido por surtir": "Informativo.",
    "Último costo": "Informativo.", "Valor inventario": "Informativo (Stock × Costo promedio).",
    "Margen detal %": "Informativo.",
}
NOTAS = {
    "Stock": "Solo cuenta en repuestos NUEVOS (inventario inicial). Para corregir existencias usa Ajustes o Compras.",
    "Costo promedio": "Solo cuenta en repuestos NUEVOS con stock (costo de cada unidad).",
    "Costo virtual": "Para la bodega virtual: lo que te costaría comprarlo.",
    "Bodega": "Real o Virtual.",
    "Código proveedor": "Código de fábrica o del proveedor. Se puede cambiar.",
    "Activo": "NO = se archiva (deja de salir en la caja y la tienda).",
}


def _encabezado(ws, titulos, anchos, fila=3):
    from openpyxl.comments import Comment
    borde = Border(bottom=Side(style="thin", color=NEGRO))
    for i, (t, w) in enumerate(zip(titulos, anchos), 1):
        c = ws.cell(row=fila, column=i, value=t)
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="7A7A72" if t in SOLO_LECTURA else NEGRO)
        nota = SOLO_LECTURA.get(t) or NOTAS.get(t)
        if nota and ws.title != "Movimientos":
            c.comment = Comment(nota, "Moto Speed")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        c.border = borde
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[fila].height = 30


def _titulo(ws, texto, sub, ncols):
    ws["A1"] = texto
    ws["A1"].font = Font(name=F, bold=True, size=14)
    ws["A2"] = sub
    ws["A2"].font = Font(name=F, italic=True, color="666666")
    ws.sheet_view.showGridLines = False


def _hoja_productos(wb, nombre, titulo, sub, productos, cols, extra):
    ws = wb.create_sheet(nombre)
    _titulo(ws, titulo, sub, len(cols))
    _encabezado(ws, [c[0] for c in cols], [c[1] for c in cols])
    idx = {c[0]: i for i, c in enumerate(cols, 1)}
    fila = 4
    for p in productos:
        for i, (t, _, fmt, fn) in enumerate(cols, 1):
            if fn is None:   # Valor inventario
                st, cp = get_column_letter(idx["Stock"]), get_column_letter(idx["Costo promedio"])
                v = f"=MAX({st}{fila},0)*{cp}{fila}"
            else:
                v = fn(p, extra)
            c = ws.cell(row=fila, column=i, value=v)
            c.font = Font(name=F, color=ROJO if (t == "Estado" and v in ("Agotado", "Bajo mínimo")) else NEGRO,
                          bold=(t == "Código interno"))
            if fmt:
                c.number_format = fmt
        fila += 1
    ultima = fila - 1
    if ultima >= 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(cols))}{ultima}"
        # totales que siguen los filtros (SUBTOTAL ignora filas ocultas por el filtro)
        ws.cell(row=fila + 1, column=1, value="Totales (filas visibles)").font = Font(name=F, bold=True)
        for t in ("Stock", "Vendido por surtir", "Valor inventario"):
            if t in idx:
                L = get_column_letter(idx[t])
                c = ws.cell(row=fila + 1, column=idx[t], value=f"=SUBTOTAL(109,{L}4:{L}{ultima})")
                c.font = Font(name=F, bold=True)
                c.number_format = PESOS if t == "Valor inventario" else CANT
        ws.cell(row=fila + 1, column=idx["Nombre"], value=f"=SUBTOTAL(103,D4:D{ultima})&\" repuesto(s)\"").font = Font(name=F, bold=True)
    else:
        ws.cell(row=4, column=1, value="(sin repuestos en esta lista)").font = Font(name=F, italic=True, color="666666")
    ws.freeze_panes = "E4"
    return ws


def _hoja_movimientos(wb, movimientos, ve_costos, desde, hasta):
    ws = wb.create_sheet("Movimientos")
    _titulo(ws, "Entradas y salidas de inventario (kardex)",
            f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y} · cantidad positiva = entra, negativa = sale", 0)
    cols = [("Fecha", 17), ("Código interno", 13), ("Código proveedor", 20), ("Repuesto", 40), ("Movimiento", 22),
            ("Entra", 9), ("Sale", 9), ("Saldo", 9), ("Documento", 16), ("Usuario", 16), ("Nota", 36)]
    if ve_costos:
        cols[8:8] = [("Costo unit.", 12), ("Costo prom. después", 14)]
    _encabezado(ws, [c[0] for c in cols], [c[1] for c in cols])
    fila = 4
    for m in movimientos:
        p = m.producto
        vals = [m.fecha, p.codigo_interno or "", p.codigo or "", p.nombre, TIPOS_MOV.get(m.tipo, m.tipo),
                m.cantidad if m.cantidad > 0 else None, -m.cantidad if m.cantidad < 0 else None, m.saldo]
        fmts = ["dd/mm/yyyy hh:mm", "@", "@", None, None, CANT, CANT, CANT]
        if ve_costos:
            vals += [m.costo_unitario, m.costo_promedio]
            fmts += [PESOS, PESOS]
        vals += [m.documento or "", m.usuario.nombre if m.usuario else "", m.nota or ""]
        fmts += [None, None, None]
        for i, (v, fmt) in enumerate(zip(vals, fmts), 1):
            c = ws.cell(row=fila, column=i, value=v)
            c.font = Font(name=F)
            if fmt:
                c.number_format = fmt
        fila += 1
    if fila > 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(cols))}{fila - 1}"
    else:
        ws.cell(row=4, column=1, value="(sin movimientos en el periodo)").font = Font(name=F, italic=True, color="666666")
    ws.freeze_panes = "E4"


def plantilla(ve_costos=True):
    """Excel vacío con el mismo formato del que descarga la app (hoja «Inventario»), para subir repuestos."""
    wb = Workbook()
    wb.remove(wb.active)
    cols = _columnas(ve_costos)
    ws = _hoja_productos(wb, "Inventario", "Moto Speed · Plantilla de repuestos",
                         "Una fila por repuesto. Obligatorio en nuevos: Nombre y Precio detal. Deja vacío el Código "
                         "interno: la app lo asigna. Columnas grises = informativas. Borra la fila de ejemplo.",
                         [], cols, {"reservado": {}, "por_surtir": {}})
    ej = {"Código proveedor": "EJEMPLO-001", "Nombre": "Kit de arrastre 428 (borrar esta fila)",
          "Descripción": "Kit completo: piñón, corona y cadena", "Categoría": "Transmisión", "Marca": "Riffel",
          "Referencia": "KA-428", "Compatibilidad / medida": "Boxer 100, CT 100", "Ubicación": "E2-C3",
          "Unidad": "kit", "Bodega": "Real", "Stock": 3, "Stock mínimo": 2, "Costo promedio": 85000,
          "Precio detal": 129000, "Precio mayor": 115000, "Precio taller": 120000, "IVA %": 0,
          "En tienda web": "SI", "Activo": "SI"}
    for i, (t, _, fmt, _fn) in enumerate(cols, 1):
        c = ws.cell(row=4, column=i, value=ej.get(t))
        c.font = Font(name=F, italic=True, color="666666")
        if fmt:
            c.number_format = fmt
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def inventario_excel(productos, reservado, por_surtir, movimientos, ve_costos, cfg, desde, hasta):
    wb = Workbook()
    wb.remove(wb.active)
    cols = _columnas(ve_costos)
    extra = {"reservado": reservado, "por_surtir": por_surtir}
    hoy = datetime.now().strftime("%d/%m/%Y %I:%M %p")
    neg = (cfg.nombre or "Moto Speed").upper()

    virtuales = [p for p in productos if p.virtual]
    reales = [p for p in productos if not p.virtual and (p.stock or 0) > 0]
    alertas = [p for p in productos if not p.virtual and ((p.stock or 0) <= 0 or p.bajo_minimo)]

    # Resumen
    ws = wb.create_sheet("Resumen")
    _titulo(ws, f"{neg} · Inventario", f"Generado el {hoy}", 2)
    filas = [("Repuestos en total", len(productos), CANT),
             ("Con inventario (bodega real)", len(reales), CANT),
             ("Bodega virtual", len(virtuales), CANT),
             ("Bajo mínimo o agotados", len(alertas), CANT),
             ("Unidades en bodega real", sum(max(p.stock or 0, 0) for p in productos if not p.virtual), CANT),
             ("Unidades virtuales vendidas por surtir", sum(por_surtir.values()), CANT)]
    if ve_costos:
        filas.append(("Valor del inventario al costo promedio",
                      ("=SUM('Con inventario'!{L}4:{L}{n})".format(L=get_column_letter([c[0] for c in cols].index("Valor inventario") + 1),
                                                                     n=len(reales) + 3) if reales else 0), PESOS))
    for i, (t, v, fmt) in enumerate(filas, 4):
        ws.cell(row=i, column=1, value=t).font = Font(name=F)
        c = ws.cell(row=i, column=2, value=v)
        c.font = Font(name=F, bold=True)
        c.number_format = fmt
    ws.cell(row=i + 2, column=1, value="Hojas: Inventario (todo) · Bodega virtual · Con inventario · "
                                        "Bajo mínimo y agotados · Movimientos").font = Font(name=F, italic=True, color="666666")
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 18

    _hoja_productos(wb, "Inventario", f"{neg} · Inventario completo", f"Todos los repuestos activos · {hoy}",
                    productos, cols, extra)
    _hoja_productos(wb, "Bodega virtual", "Bodega virtual",
                    "Se venden sin tener unidades; pasan a la bodega real cuando se compran", virtuales, cols, extra)
    _hoja_productos(wb, "Con inventario", "Con inventario (bodega real)", "Repuestos con unidades físicas", reales, cols, extra)
    _hoja_productos(wb, "Bajo mínimo y agotados", "Bajo mínimo y agotados", "Para pedir al proveedor", alertas, cols, extra)
    _hoja_movimientos(wb, movimientos, ve_costos, desde, hasta)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def inversion_excel(r, cfg, url_soporte):
    """Inversión inicial: resumen, detalle con soportes, aportes y participación de socios."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    neg = (cfg.nombre or "Moto Speed").upper()
    _titulo(ws, f"{neg} · Inversión inicial", f"Generado el {datetime.now():%d/%m/%Y %I:%M %p}", 2)
    fila = 4

    def par(t, v, fmt=PESOS, negrita=False):
        nonlocal fila
        ws.cell(row=fila, column=1, value=t).font = Font(name=F, bold=negrita)
        c = ws.cell(row=fila, column=2, value=v)
        c.font = Font(name=F, bold=True)
        if fmt:
            c.number_format = fmt
        fila += 1

    ndet = len(r["invs"])
    par("Total invertido", f"=SUM(Detalle!E4:E{ndet + 3})" if ndet else 0, negrita=True)
    for t, k in (("Activos (duran años)", "activo"), ("Gastos preoperativos", "gasto"), ("Inventario", "inventario")):
        par("   " + t, r["por_tipo"].get(k, 0))
    if cfg.fecha_apertura:
        par("Fecha de apertura", cfg.fecha_apertura, "dd/mm/yyyy")
    if r["rec"]:
        par("Ganancia neta desde la apertura", r["rec"]["neta"])
        par("Recuperado de la inversión", r["rec"]["pct"] / 100, "0.0%")
    fila += 1
    ws.cell(row=fila, column=1, value="Por categoría").font = Font(name=F, bold=True)
    fila += 1
    for k, v in r["por_cat"]:
        par(k, v)
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 18

    d = wb.create_sheet("Detalle")
    _titulo(d, "Detalle de la inversión inicial", "Cada cosa que se pagó para montar el negocio", 0)
    cols = [("Fecha", 12), ("Concepto", 40), ("Categoría", 26), ("Tipo", 18), ("Valor", 14), ("Pagó", 20),
            ("Proveedor", 22), ("Medio", 14), ("Nota", 30), ("Soporte", 14)]
    _encabezado(d, [c[0] for c in cols], [c[1] for c in cols])
    from models import TIPOS_INVERSION
    for n_, i in enumerate(r["invs"], 4):
        vals = [i.fecha, i.concepto, i.categoria, TIPOS_INVERSION.get(i.tipo, i.tipo), i.valor,
                i.socio.nombre if i.socio else "Fondo común", i.proveedor or "", i.medio or "", i.nota or ""]
        for k, v in enumerate(vals, 1):
            c = d.cell(row=n_, column=k, value=v)
            c.font = Font(name=F)
        d.cell(row=n_, column=1).number_format = "dd/mm/yyyy"
        d.cell(row=n_, column=5).number_format = PESOS
        if i.soporte_v:
            c = d.cell(row=n_, column=10, value="Ver soporte")
            c.hyperlink = url_soporte.replace("/0/", f"/{i.id}/")
            c.font = Font(name=F, color="0563C1", underline="single")
    if ndet:
        d.auto_filter.ref = f"A3:J{ndet + 3}"
        c = d.cell(row=ndet + 5, column=4, value="Total")
        c.font = Font(name=F, bold=True)
        c = d.cell(row=ndet + 5, column=5, value=f"=SUBTOTAL(109,E4:E{ndet + 3})")
        c.font = Font(name=F, bold=True)
        c.number_format = PESOS
    d.freeze_panes = "C4"

    s = wb.create_sheet("Socios")
    _titulo(s, "Lo que puso cada socio", "Aportes al fondo común + compras que pagó directamente", 0)
    _encabezado(s, ["Socio", "Documento", "Aportes en dinero", "Compras que pagó", "Total", "Participación"],
                [26, 16, 18, 18, 16, 14])
    nsoc = len(r["por_socio"])
    for n_, x in enumerate(r["por_socio"], 4):
        vals = [x["socio"].nombre, x["socio"].documento or "", x["aportes"], x["pagos"], f"=C{n_}+D{n_}",
                f"=IF(SUM($E$4:$E${nsoc + 3})=0,0,E{n_}/SUM($E$4:$E${nsoc + 3}))"]
        for k, v in enumerate(vals, 1):
            c = s.cell(row=n_, column=k, value=v)
            c.font = Font(name=F)
            c.number_format = PESOS if k in (3, 4, 5) else ("0.0%" if k == 6 else "General")
    s.cell(row=nsoc + 5, column=1, value="Fondo común sin gastar").font = Font(name=F, bold=True)
    c = s.cell(row=nsoc + 5, column=5, value=r["fondo"])
    c.font = Font(name=F, bold=True)
    c.number_format = PESOS

    a = wb.create_sheet("Aportes")
    _titulo(a, "Aportes en dinero al fondo común", "", 0)
    _encabezado(a, ["Fecha", "Socio", "Valor", "Medio", "Nota"], [12, 26, 16, 16, 36])
    for n_, x in enumerate(r["aportes"], 4):
        for k, v in enumerate([x.fecha, x.socio.nombre, x.valor, x.medio or "", x.nota or ""], 1):
            c = a.cell(row=n_, column=k, value=v)
            c.font = Font(name=F)
        a.cell(row=n_, column=1).number_format = "dd/mm/yyyy"
        a.cell(row=n_, column=3).number_format = PESOS

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
