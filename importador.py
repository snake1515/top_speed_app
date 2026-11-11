"""Importar repuestos desde Excel: plantilla, validación fila por fila e importación todo-o-nada."""
from io import BytesIO

from models import Categoria, Producto, db
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
               "KA-428", "Boxer 100, CT 100", "E2-C3", "", "kit", 3, 85000, 129000, 115000, 120000, 2, 0, "SI"])
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
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"«{v}» no es un número")


def importar(archivo, usuario_id):
    """Devuelve (creados, actualizados). Si hay cualquier error no se guarda nada."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(archivo, data_only=True, read_only=True)
    except Exception:
        raise ErrorNegocio("No pude leer el archivo. Sube un Excel (.xlsx) basado en la plantilla.")
    ws = wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    if not filas:
        raise ErrorNegocio("El archivo está vacío.")
    enc = [str(c or "").strip().lower() for c in filas[0]]
    idx = {}
    for clave, titulo, _ in COLUMNAS:
        t = titulo.lower()
        if t in enc:
            idx[clave] = enc.index(t)
    if "codigo" not in idx or "nombre" not in idx:
        raise ErrorNegocio("No encuentro las columnas «Código *» y «Nombre *». Descarga la plantilla y úsala.")
    errores, datos, vistos = [], [], set()
    for n, fila in enumerate(filas[1:], start=2):
        g = lambda k: (fila[idx[k]] if k in idx and idx[k] < len(fila) else None)
        codigo = str(g("codigo") or "").strip()
        nombre = str(g("nombre") or "").strip()
        if not codigo and not nombre:
            continue
        if codigo.upper().startswith("EJEMPLO"):
            continue
        if not codigo or not nombre:
            errores.append(f"Fila {n}: falta el código o el nombre.")
            continue
        if codigo.lower() in vistos:
            errores.append(f"Fila {n}: el código {codigo} está repetido en el archivo.")
            continue
        vistos.add(codigo.lower())
        try:
            d = {"codigo": codigo[:60], "nombre": nombre[:200],
                 "categoria": str(g("categoria") or "").strip()[:80],
                 "marca": str(g("marca") or "").strip()[:80] or None,
                 "referencia": str(g("referencia") or "").strip()[:80] or None,
                 "compatibilidad": str(g("compatibilidad") or "").strip()[:400] or None,
                 "ubicacion": str(g("ubicacion") or "").strip()[:60] or None,
                 "codigo_barras": str(g("codigo_barras") or "").strip().removesuffix(".0")[:60] or None,
                 "unidad": str(g("unidad") or "unidad").strip().lower()[:20] or "unidad",
                 "stock": _num(g("stock")), "costo": _num(g("costo")), "precio_detal": _num(g("precio_detal")),
                 "precio_mayor": _num(g("precio_mayor")), "precio_taller": _num(g("precio_taller")),
                 "stock_minimo": _num(g("stock_minimo")), "iva": _num(g("iva")),
                 "en_tienda": str(g("en_tienda") or "SI").strip().upper() not in ("NO", "N", "0", "FALSE")}
        except ValueError as e:
            errores.append(f"Fila {n}: {e}.")
            continue
        if min(d["stock"], d["costo"], d["precio_detal"], d["precio_mayor"], d["precio_taller"]) < 0:
            errores.append(f"Fila {n}: hay valores negativos.")
            continue
        if d["precio_detal"] <= 0:
            errores.append(f"Fila {n}: falta el precio detal.")
            continue
        if d["stock"] > 0 and d["costo"] <= 0:
            errores.append(f"Fila {n}: si trae stock inicial también necesita el costo unitario.")
            continue
        datos.append(d)
    if errores:
        raise ErrorNegocio(errores[:40] + ([f"… y {len(errores) - 40} errores más."] if len(errores) > 40 else []))
    if not datos:
        raise ErrorNegocio("No encontré repuestos para importar.")
    cats = {c.nombre.lower(): c for c in Categoria.query.all()}
    existentes = {p.codigo.lower(): p for p in Producto.query.all()}
    creados = actualizados = 0
    for d in datos:
        cat = None
        if d["categoria"]:
            cat = cats.get(d["categoria"].lower())
            if cat is None:
                cat = Categoria(nombre=d["categoria"])
                db.session.add(cat)
                cats[d["categoria"].lower()] = cat
        p = existentes.get(d["codigo"].lower())
        nuevo = p is None
        if nuevo:
            p = Producto(codigo=d["codigo"], stock=0, costo_promedio=0)
            db.session.add(p)
        p.nombre, p.categoria = d["nombre"], cat or p.categoria
        for k in ("marca", "referencia", "compatibilidad", "ubicacion", "codigo_barras", "unidad",
                  "precio_detal", "precio_mayor", "precio_taller", "stock_minimo", "en_tienda"):
            setattr(p, k, d[k])
        p.iva_pct = d["iva"]
        db.session.flush()
        if nuevo:
            creados += 1
            if d["stock"] > 0:
                entrada(p, d["stock"], d["costo"], "inicial", "Importación Excel", "importacion", None, usuario_id,
                        actualizar_ultimo=True)
            elif d["costo"]:
                p.ultimo_costo = d["costo"]
        else:
            actualizados += 1
    return creados, actualizados
