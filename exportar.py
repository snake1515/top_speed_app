"""Exportación del inventario a Excel: todo, bodega virtual, con inventario, bajo mínimo/agotados y movimientos."""
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import TIPOS_MOV, ahora

NEGRO, AMARILLO, ROJO = "0B0A07", "FFC400", "E8243C"
F = "Arial"
PESOS = '"$"#,##0;-"$"#,##0;"-"'
CANT = '#,##0.##;-#,##0.##;"0"'
PCT = '0.0"%";-0.0"%";"-"'


# ---------- fórmulas con su resultado guardado
# openpyxl guarda las fórmulas sin resultado: Excel en "Vista protegida" (archivos descargados) no calcula y las
# muestra vacías. Por eso cada fórmula se registra con el valor que ya calculamos y al guardar se escribe ese
# resultado dentro del archivo: se ve de una vez y la fórmula sigue funcionando al editar.
def _fx(ws, fila, col, formula, valor):
    c = ws.cell(row=fila, column=col, value=formula)
    if not hasattr(ws.parent, "_resultados"):
        ws.parent._resultados = {}
    ws.parent._resultados[(ws.title, c.coordinate)] = valor
    return c


def _guardar(wb):
    import re
    import zipfile
    from xml.sax.saxutils import escape
    crudo = BytesIO()
    wb.save(crudo)
    res = getattr(wb, "_resultados", {})
    if not res:
        crudo.seek(0)
        return crudo
    por_hoja = {}
    for (titulo, celda), v in res.items():
        por_hoja.setdefault(titulo, {})[celda] = v
    rutas = {f"xl/worksheets/sheet{i}.xml": ws.title for i, ws in enumerate(wb.worksheets, 1)}
    zin = zipfile.ZipFile(BytesIO(crudo.getvalue()))
    salida = BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            datos = zin.read(item.filename)
            vals = por_hoja.get(rutas.get(item.filename))
            if vals:
                xml = datos.decode("utf-8")

                def poner(m):
                    attrs, formula = m.group(1), m.group(2)
                    ref = re.search(r'\br="([A-Z]+\d+)"', attrs)
                    if not ref or ref.group(1) not in vals:
                        return m.group(0)
                    v = vals[ref.group(1)]
                    attrs = re.sub(r'\st="[^"]*"', "", attrs)
                    if isinstance(v, str):
                        return f'<c{attrs} t="str">{formula}<v>{escape(v)}</v></c>'
                    return f"<c{attrs}>{formula}<v>{float(v or 0)!r}</v></c>"
                xml = re.sub(r"<c(\s[^>]*)>(<f>.*?</f>)(?:<v>.*?</v>|<v\s*/>)?</c>", poner, xml)
                datos = xml.encode("utf-8")
            zout.writestr(item, datos)
    salida.seek(0)
    return salida


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
    sumas = {"Stock": 0.0, "Vendido por surtir": 0.0, "Valor inventario": 0.0}
    for p in productos:
        for i, (t, _, fmt, fn) in enumerate(cols, 1):
            if fn is None:   # Valor inventario
                st, cp = get_column_letter(idx["Stock"]), get_column_letter(idx["Costo promedio"])
                v = f"=MAX({st}{fila},0)*{cp}{fila}"
                c = _fx(ws, fila, i, v, max(p.stock or 0, 0) * (p.costo_promedio or 0))
            else:
                v = fn(p, extra)
                c = ws.cell(row=fila, column=i, value=v)
            c.font = Font(name=F, color=ROJO if (t == "Estado" and v in ("Agotado", "Bajo mínimo")) else NEGRO,
                          bold=(t == "Código interno"))
            if fmt:
                c.number_format = fmt
        for t in sumas:
            if t in idx:
                x = ws.cell(row=fila, column=idx[t]).value
                sumas[t] += (max(p.stock or 0, 0) * (p.costo_promedio or 0)) if t == "Valor inventario" else (x if isinstance(x, (int, float)) else 0)
        fila += 1
    ultima = fila - 1
    if ultima >= 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(len(cols))}{ultima}"
        # totales que siguen los filtros (SUBTOTAL ignora filas ocultas por el filtro)
        ws.cell(row=fila + 1, column=1, value="Totales (filas visibles)").font = Font(name=F, bold=True)
        for t in ("Stock", "Vendido por surtir", "Valor inventario"):
            if t in idx:
                L = get_column_letter(idx[t])
                c = _fx(ws, fila + 1, idx[t], f"=SUBTOTAL(109,{L}4:{L}{ultima})", sumas[t])
                c.font = Font(name=F, bold=True)
                c.number_format = PESOS if t == "Valor inventario" else CANT
        _fx(ws, fila + 1, idx["Nombre"], f"=SUBTOTAL(103,D4:D{ultima})&\" repuesto(s)\"", f"{ultima - 3} repuesto(s)").font = Font(name=F, bold=True)
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
    return _guardar(wb)


def inventario_excel(productos, reservado, por_surtir, movimientos, ve_costos, cfg, desde, hasta):
    wb = Workbook()
    wb.remove(wb.active)
    cols = _columnas(ve_costos)
    extra = {"reservado": reservado, "por_surtir": por_surtir}
    hoy = ahora().strftime("%d/%m/%Y %I:%M %p")
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
        if isinstance(v, str) and v.startswith("="):
            c = _fx(ws, i, 2, v, sum(max(p.stock or 0, 0) * (p.costo_promedio or 0) for p in reales))
        else:
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

    return _guardar(wb)


def inversion_excel(r, cfg, url_soporte):
    """Inversión inicial: resumen, detalle con soportes, aportes y participación de socios."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    neg = (cfg.nombre or "Moto Speed").upper()
    _titulo(ws, f"{neg} · Inversión inicial", f"Generado el {ahora():%d/%m/%Y %I:%M %p}", 2)
    fila = 4

    def par(t, v, fmt=PESOS, negrita=False):
        nonlocal fila
        ws.cell(row=fila, column=1, value=t).font = Font(name=F, bold=negrita)
        c = _fx(ws, fila, 2, v, sum(i.valor for i in r["invs"])) if isinstance(v, str) and v.startswith("=") else ws.cell(row=fila, column=2, value=v)
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
                i.pago_txt, i.proveedor or "", i.medio or "", i.nota or ""]
        for k, v in enumerate(vals, 1):
            c = d.cell(row=n_, column=k, value=v)
            c.font = Font(name=F)
        d.cell(row=n_, column=1).number_format = "dd/mm/yyyy"
        d.cell(row=n_, column=5).number_format = PESOS
        if i.soportes or i.soporte_v:
            n_s = len(i.soportes) or 1
            c = d.cell(row=n_, column=10, value="Ver soporte" if n_s == 1 else f"Ver soportes ({n_s})")
            c.hyperlink = url_soporte.replace("/0/", f"/{i.id}/")
            c.font = Font(name=F, color="0563C1", underline="single")
    if ndet:
        d.auto_filter.ref = f"A3:J{ndet + 3}"
        c = d.cell(row=ndet + 5, column=4, value="Total")
        c.font = Font(name=F, bold=True)
        c = _fx(d, ndet + 5, 5, f"=SUBTOTAL(109,E4:E{ndet + 3})", sum(i.valor for i in r["invs"]))
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
        calc = {5: x["aportes"] + x["pagos"], 6: x["pct"] / 100}
        for k, v in enumerate(vals, 1):
            c = _fx(s, n_, k, v, calc[k]) if k in calc else s.cell(row=n_, column=k, value=v)
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

    return _guardar(wb)


def _periodo_txt(r):
    if r["desde"] and r["hasta"]:
        return f"Del {r['desde']:%d/%m/%Y} al {r['hasta']:%d/%m/%Y}"
    if r["desde"]:
        return f"Desde el {r['desde']:%d/%m/%Y}"
    if r["hasta"]:
        return f"Hasta el {r['hasta']:%d/%m/%Y}"
    return "Todos los aportes"


def aportes_excel(r, cfg):
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen por socio"
    neg = (cfg.nombre or "Moto Speed").upper()
    sub = _periodo_txt(r) + (f" · {r['socio'].nombre}" if r["socio"] else "") + f" · generado {ahora():%d/%m/%Y %I:%M %p}"
    _titulo(ws, f"{neg} · Reporte de aportes de socios", sub, 0)
    _encabezado(ws, ["Socio", "Cédula / NIT", "Puesto en el periodo", "Aportes en dinero (total)", "Compras que pagó (total)",
                     "Total puesto", "Participación"], [28, 16, 18, 20, 20, 16, 14])
    n = len(r["socios"])
    for i, x in enumerate(r["socios"], 4):
        vals = [x["socio"].nombre, x["socio"].documento or "", x["periodo"], x["aportes"], x["pagos"], f"=D{i}+E{i}", x["pct"] / 100]
        for k, v in enumerate(vals, 1):
            c = _fx(ws, i, k, v, x["aportes"] + x["pagos"]) if k == 6 else ws.cell(row=i, column=k, value=v)
            c.font = Font(name=F, bold=(k == 1))
            c.number_format = PESOS if 3 <= k <= 6 else ("0.0%" if k == 7 else "General")
    if n:
        fila = n + 4
        ws.cell(row=fila, column=1, value="TOTAL").font = Font(name=F, bold=True)
        tot = {3: sum(x["periodo"] for x in r["socios"]), 4: sum(x["aportes"] for x in r["socios"]),
               5: sum(x["pagos"] for x in r["socios"]), 6: sum(x["aportes"] + x["pagos"] for x in r["socios"])}
        for k, L in ((3, "C"), (4, "D"), (5, "E"), (6, "F")):
            c = _fx(ws, fila, k, f"=SUM({L}4:{L}{fila - 1})", tot[k])
            c.font = Font(name=F, bold=True)
            c.number_format = PESOS
        ws.cell(row=fila + 2, column=1, value="Fondo común: aportado − gastado").font = Font(name=F)
        c = ws.cell(row=fila + 2, column=6, value=r["fondo"])
        c.font = Font(name=F, bold=True)
        c.number_format = PESOS
        ws.cell(row=fila + 3, column=1, value="Total invertido en el negocio").font = Font(name=F)
        c = ws.cell(row=fila + 3, column=6, value=r["total_invertido"])
        c.font = Font(name=F, bold=True)
        c.number_format = PESOS
        ws.cell(row=fila + 5, column=1, value="La participación sale de lo que cada socio puso (aportes + compras que pagó). "
                                              "Es una referencia: vale lo que los socios acordaron por escrito.").font = Font(name=F, italic=True, color="666666")

    pc = wb.create_sheet("Por concepto")
    _titulo(pc, "Relación por concepto: en qué se fue lo que puso cada socio", sub, 0)
    cols = r["cols"]
    _encabezado(pc, ["Concepto"] + [so.nombre for so in cols] + ["Total"], [52] + [18] * len(cols) + [18])
    fila, filas_cat = 4, []
    ultima = get_column_letter(len(cols) + 2)
    for c in r["por_concepto"]:
        filas_cat.append(fila)
        pc.cell(row=fila, column=1, value=c["concepto"]).font = Font(name=F, bold=True)
        for k, so in enumerate(cols, 2):
            v = c["por_socio"].get(so.id, 0)
            x = pc.cell(row=fila, column=k, value=v or None)
            x.font = Font(name=F, bold=True)
            x.number_format = PESOS
        x = _fx(pc, fila, len(cols) + 2, f"=SUM(B{fila}:{get_column_letter(len(cols) + 1)}{fila})", c["total"])
        x.font = Font(name=F, bold=True)
        x.number_format = PESOS
        for k in range(1, len(cols) + 3):
            pc.cell(row=fila, column=k).fill = PatternFill("solid", fgColor="F6F4EE")
        fila += 1
        for it in c["lineas"]:
            pc.cell(row=fila, column=1, value=f"    {it['fecha']:%d/%m/%Y} · {it['concepto']}").font = Font(name=F, color="555555")
            for k, so in enumerate(cols, 2):
                v = it["por_socio"].get(so.id, 0)
                x = pc.cell(row=fila, column=k, value=v or None)
                x.font = Font(name=F, color="555555")
                x.number_format = PESOS
            x = pc.cell(row=fila, column=len(cols) + 2, value=it["total"])
            x.font = Font(name=F, color="555555")
            x.number_format = PESOS
            fila += 1
    if filas_cat:
        pc.cell(row=fila + 1, column=1, value="TOTAL").font = Font(name=F, bold=True)
        for k in range(2, len(cols) + 3):
            L = get_column_letter(k)
            x = _fx(pc, fila + 1, k, "=" + "+".join(f"{L}{f}" for f in filas_cat),
                    r["total_periodo"] if k == len(cols) + 2 else r["en_periodo"].get(cols[k - 2].id, 0))
            x.font = Font(name=F, bold=True)
            x.number_format = PESOS
            x.fill = PatternFill("solid", fgColor="FFF3C4")
        pc.cell(row=fila + 1, column=1).fill = PatternFill("solid", fgColor="FFF3C4")
    pc.freeze_panes = "B4"

    d = wb.create_sheet("Detalle de aportes")
    _titulo(d, "Detalle: cada aporte de cada socio", sub, 0)
    _encabezado(d, ["Socio", "Fecha", "Tipo", "Recibo", "Concepto", "Medio", "Valor"], [28, 12, 20, 11, 44, 16, 16])
    fila, subtot = 4, []
    for g in r["grupos"]:
        ini = fila
        for m in g["movs"]:
            vals = [g["socio"].nombre, m["fecha"], m["tipo"], m["folio"], m["concepto"], m["medio"], m["valor"]]
            for k, v in enumerate(vals, 1):
                d.cell(row=fila, column=k, value=v).font = Font(name=F)
            d.cell(row=fila, column=2).number_format = "dd/mm/yyyy"
            d.cell(row=fila, column=7).number_format = PESOS
            fila += 1
        c = d.cell(row=fila, column=5, value=f"Total {g['socio'].nombre}")
        c.font = Font(name=F, bold=True)
        c = _fx(d, fila, 7, f"=SUM(G{ini}:G{fila - 1})", g["total"])
        c.font = Font(name=F, bold=True)
        c.number_format = PESOS
        for k in range(1, 8):
            d.cell(row=fila, column=k).fill = PatternFill("solid", fgColor="FFF3C4")
        subtot.append(f"G{fila}")
        fila += 2
    if subtot:
        c = d.cell(row=fila, column=5, value="TOTAL DE TODOS LOS SOCIOS")
        c.font = Font(name=F, bold=True)
        c = _fx(d, fila, 7, "=" + "+".join(subtot), r["total_periodo"])
        c.font = Font(name=F, bold=True)
        c.number_format = PESOS
    else:
        d.cell(row=4, column=1, value="Sin aportes en el periodo").font = Font(name=F, italic=True)
    d.freeze_panes = "A4"
    return _guardar(wb)


def aportes_pdf(r, cfg, logo_path):
    """PDF tamaño carta del reporte de aportes, con firmas de los socios."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from PIL import Image as PILImage

    def p(v):
        return "$ " + f"{round(v or 0):,.0f}".replace(",", ".")
    NEGRO, AMA, ROJO, GRIS = colors.HexColor("#0B0A07"), colors.HexColor("#FFC400"), colors.HexColor("#E8243C"), colors.HexColor("#6E6A60")
    st = {
        "t": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=16, leading=19, textColor=NEGRO),
        "s": ParagraphStyle("s", fontName="Helvetica", fontSize=9, leading=12, textColor=GRIS),
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=NEGRO, spaceBefore=10, spaceAfter=5),
        "c": ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=10.5),
        "n": ParagraphStyle("n", fontName="Helvetica-Oblique", fontSize=8, leading=10, textColor=GRIS),
    }
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm, bottomMargin=1.5 * cm,
                            title="Reporte de aportes de socios", author=cfg.nombre or "Moto Speed")
    story = []

    # encabezado: logo + datos del negocio
    try:
        im = PILImage.open(logo_path).convert("RGBA")
        fondo = PILImage.new("RGBA", im.size, (255, 255, 255, 255))
        fondo.alpha_composite(im)
        lb = BytesIO()
        fondo.convert("RGB").save(lb, "PNG")
        lb.seek(0)
        ancho = 5.2 * cm
        logo = RLImage(lb, width=ancho, height=ancho * im.height / im.width)
    except Exception:
        logo = Paragraph(cfg.nombre or "Moto Speed", st["t"])
    datos = "<br/>".join(x for x in [f"<b>{(cfg.nombre or 'Moto Speed').upper()}</b>", f"NIT {cfg.nit}" if cfg.nit else "",
                                     cfg.direccion or "", " · ".join(z for z in [cfg.ciudad or "", f"Tel. {cfg.telefono}" if cfg.telefono else ""] if z)] if x)
    enc = Table([[logo, Paragraph(datos, ParagraphStyle("d", fontName="Helvetica", fontSize=9, leading=12, alignment=2))]],
                colWidths=[7 * cm, None])
    enc.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [enc, Spacer(1, 6)]
    franja = Table([[""]], colWidths=[doc.width], rowHeights=[4])
    franja.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AMA), ("LINEBELOW", (0, 0), (-1, -1), 2, ROJO)]))
    story += [franja, Spacer(1, 10)]
    story.append(Paragraph("REPORTE DE APORTES DE SOCIOS", st["t"]))
    story.append(Paragraph(_periodo_txt(r) + (f" · {r['socio'].nombre}" if r["socio"] else "")
                           + f" · generado el {ahora():%d/%m/%Y %I:%M %p}", st["s"]))

    def tabla(filas, anchos, num_desde, total=False):
        t = Table(filas, colWidths=anchos, repeatRows=1)
        estilo = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
                  ("BACKGROUND", (0, 0), (-1, 0), NEGRO), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("ALIGN", (num_desde, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("ROWBACKGROUNDS", (0, 1), (-1, -2 if total else -1), [colors.white, colors.HexColor("#F6F4EE")]),
                  ("LINEBELOW", (0, 0), (-1, -1), .25, colors.HexColor("#DDD8CC")),
                  ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        if total:
            estilo += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FFF3C4")),
                       ("LINEABOVE", (0, -1), (-1, -1), 1.2, NEGRO)]
        t.setStyle(TableStyle(estilo))
        return t

    # resumen por socio
    story.append(Paragraph("Resumen por socio", st["h"]))
    filas = [["Socio", "Cédula", "Puesto en\nel periodo", "Aportes en\ndinero (total)", "Compras que\npagó (total)", "Total puesto", "%"]]
    for x in r["socios"]:
        filas.append([Paragraph(f"<b>{x['socio'].nombre}</b>", st["c"]), x["socio"].documento or "", p(x["periodo"]), p(x["aportes"]),
                      p(x["pagos"]), p(x["total"]), f"{x['pct']:.1f} %".replace(".", ",")])
    filas.append(["TOTAL", "", p(sum(x["periodo"] for x in r["socios"])), p(sum(x["aportes"] for x in r["socios"])),
                  p(sum(x["pagos"] for x in r["socios"])), p(sum(x["total"] for x in r["socios"])),
                  "100 %" if not r["socio"] else ""])
    story.append(tabla(filas, [4.6 * cm, 2.3 * cm, 2.4 * cm, 2.5 * cm, 2.5 * cm, 2.4 * cm, 1.5 * cm], 2, total=True))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Total invertido en el negocio: <b>{p(r['total_invertido'])}</b> · Fondo común: aportado "
                           f"{p(r['total_aportes'])}, gastado {p(r['del_fondo'])}, "
                           f"{'sin gastar' if r['fondo'] >= 0 else 'faltante'} <b>{p(abs(r['fondo']))}</b>.", st["s"]))

    # relación por concepto
    cols = r["cols"]
    if r["por_concepto"]:
        story.append(Paragraph("Relación por concepto", st["h"]))
        ancho_c = max(1.9 * cm, min(2.6 * cm, (doc.width - 6.2 * cm - 2.3 * cm) / max(len(cols), 1)))
        filas = [["Concepto"] + [Paragraph(f"<b>{so.nombre}</b>", ParagraphStyle("hc", fontName="Helvetica-Bold", fontSize=7.5, leading=9,
                                                                                  textColor=colors.white, alignment=2)) for so in cols] + ["Total"]]
        negritas = []
        for c in r["por_concepto"]:
            negritas.append(len(filas))
            filas.append([Paragraph(f"<b>{c['concepto']}</b>", st["c"])] + [p(c["por_socio"].get(so.id, 0)) if c["por_socio"].get(so.id) else "—" for so in cols] + [p(c["total"])])
            for it in c["lineas"]:
                filas.append([Paragraph(f"&nbsp;&nbsp;&nbsp;{it['fecha']:%d/%m/%Y} · {it['concepto']}", ParagraphStyle("ci", parent=st["c"], textColor=GRIS))]
                             + [p(it["por_socio"].get(so.id, 0)) if it["por_socio"].get(so.id) else "" for so in cols] + [p(it["total"])])
        filas.append(["TOTAL"] + [p(r["en_periodo"].get(so.id, 0)) for so in cols] + [p(r["total_periodo"])])
        t = tabla(filas, [doc.width - ancho_c * len(cols) - 2.4 * cm] + [ancho_c] * len(cols) + [2.4 * cm], 1, total=True)
        t.setStyle(TableStyle([("FONT", (1, k), (-1, k), "Helvetica-Bold", 8.5) for k in negritas]
                              + [("BACKGROUND", (0, k), (-1, k), colors.HexColor("#EFEBE0")) for k in negritas]))
        story.append(t)

    # detalle: cada aporte de cada socio
    story.append(Paragraph(f"Detalle: cada aporte de cada socio ({len(r['movs'])} registros)", st["h"]))
    if not r["grupos"]:
        story.append(Paragraph("Sin aportes en el periodo.", st["s"]))
    for g in r["grupos"]:
        story.append(Paragraph(f"<b>{g['socio'].nombre}</b>" + (f"  ·  C.C. {g['socio'].documento}" if g["socio"].documento else ""),
                               ParagraphStyle("gs", fontName="Helvetica", fontSize=10, leading=13, spaceBefore=8, spaceAfter=3)))
        filas = [["Fecha", "Tipo", "Recibo", "Concepto", "Medio", "Valor"]]
        for m in g["movs"]:
            filas.append([m["fecha"].strftime("%d/%m/%Y"), Paragraph(m["tipo"], st["c"]), m["folio"] or "—", Paragraph(m["concepto"], st["c"]),
                          Paragraph(m["medio"], st["c"]), p(m["valor"])])
        if g["dinero"] and g["pagos"]:
            filas.append(["", "", "", Paragraph(f"Dinero {p(g['dinero'])} · bolsillo {p(g['pagos'])}", st["c"]), "", ""])
        filas.append(["", "", "", f"TOTAL {g['socio'].nombre.upper()}"[:46], "", p(g["total"])])
        story.append(tabla(filas, [2 * cm, 2.8 * cm, 1.8 * cm, 6.4 * cm, 2.6 * cm, 2.6 * cm], 5, total=True))
    if len(r["grupos"]) > 1:
        tt = Table([["TOTAL DE TODOS LOS SOCIOS", p(r["total_periodo"])]], colWidths=[doc.width - 3.2 * cm, 3.2 * cm])
        tt.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica-Bold", 10), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                                ("BACKGROUND", (0, 0), (-1, -1), AMA), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story += [Spacer(1, 8), tt]
    story.append(Spacer(1, 8))
    story.append(Paragraph("La participación sale de lo que cada socio puso (aportes en dinero + compras que pagó de su bolsillo). "
                           "Es una referencia: lo que acordaron los socios por escrito es lo que vale.", st["n"]))

    # firmas de los socios
    story.append(Spacer(1, 34))
    firmas = [[Paragraph(f"<b>{x['socio'].nombre}</b><br/>{('C.C. ' + x['socio'].documento) if x['socio'].documento else ''}",
                         ParagraphStyle("f", fontName="Helvetica", fontSize=8.5, leading=11, alignment=1)) for x in r["socios"][i:i + 3]]
              for i in range(0, len(r["socios"]), 3)]
    if firmas:
        for fila in firmas:
            while len(fila) < 3:
                fila.append("")
        t = Table(firmas, colWidths=[doc.width / 3] * 3, rowHeights=[46] * len(firmas))
        estilo = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14)]
        for ri, fila in enumerate(firmas):
            for ci, celda in enumerate(fila):
                if celda:
                    estilo.append(("LINEABOVE", (ci, ri), (ci, ri), .8, NEGRO))
        t.setStyle(TableStyle(estilo))
        story.append(t)

    def pie(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRIS)
        canvas.drawString(doc_.leftMargin, 0.9 * cm, f"{cfg.nombre or 'Moto Speed'} · Reporte de aportes de socios")
        canvas.drawRightString(letter[0] - doc_.rightMargin, 0.9 * cm, f"Página {doc_.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=pie, onLaterPages=pie)
    buf.seek(0)
    return buf
