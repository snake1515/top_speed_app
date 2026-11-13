"""Moto Speed — punto de venta, caja, inventario con costo promedio, cartera, nómina,
reportes y tienda online para un almacén de repuestos de moto."""
import json
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
from urllib.parse import quote

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

import importador
import nomina as nom
import reportes as rpt
from models import (normalizar, DIAS, RESULTADOS_VISITA, Ruta, Visita, CargaMasiva, migrar, CATEGORIAS_GASTO, LISTAS_PRECIO, MOTIVOS_AJUSTE, ROLES, TIPOS_MEDIO, TIPOS_MOV,
                    TIPOS_PAGO_EMP, UNIDADES, Ajuste, Categoria, Cliente, Compra, Cotizacion,
                    Empleado, Gasto, MedioPago, MovCaja, Movimiento, PagoNomina, Pedido, Producto,
                    Proveedor, Turno, Usuario, Venta, ahora, config, db, semilla)
from services import (ErrorNegocio, abrir_turno, anular_ajuste, anular_compra, anular_mov_caja,
                      anular_venta, cant, cerrar_turno, crear_pedido, devolver, guardar_cotizacion,
                      mov_caja, pagar_compra, pedido_vencido, pesos, registrar_abono, registrar_ajuste,
                      registrar_compra, registrar_venta, registrar_visita, crear_pedido_preventa, reservado_por_producto, resumen_turno,
                      saldo_cliente, turno_abierto)

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_hex(32)
db_url = (os.getenv("DATABASE_URL") or "sqlite:///topspeed.db").strip().strip('"').strip("'")
if "[YOUR-PASSWORD]" in db_url:
    raise SystemExit("ERROR DATABASE_URL: aún contiene [YOUR-PASSWORD]. Pon la contraseña real de Supabase.")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=14)
if os.getenv("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = True
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30
STATIC_V = (os.getenv("RENDER_GIT_COMMIT") or secrets.token_hex(4))[:8]
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "info"


@login_manager.user_loader
def cargar_usuario(uid):
    return db.session.get(Usuario, int(uid))


# ================================================================ utilidades
def n(valor, defecto=0.0):
    """Texto a número aceptando 1.500,5 / 1500.5 / $ 1.500."""
    if valor is None:
        return defecto
    if isinstance(valor, (int, float)):
        return float(valor)
    v = str(valor).strip().replace(" ", "").replace("$", "")
    if not v:
        return defecto
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".")
    elif "," in v:
        v = v.replace(",", ".")
    elif v.count(".") > 1 or (v.count(".") == 1 and len(v.split(".")[1]) == 3):
        v = v.replace(".", "")
    try:
        return float(v)
    except ValueError:
        return defecto


def pct(valor, defecto=0.0):
    """Porcentajes y decimales: 0,522 / 0.522 / 19 (sin la regla de miles)."""
    try:
        return float(str(valor).strip().replace("%", "").replace(",", ".")) if str(valor or "").strip() else defecto
    except ValueError:
        return defecto


def _fecha(txt, defecto=None):
    try:
        return datetime.strptime(txt or "", "%Y-%m-%d").date()
    except ValueError:
        return defecto


def fecha_form(texto):
    d = _fecha(texto)
    hoy = ahora()
    if d is None or d == hoy.date():
        return hoy
    return datetime.combine(d, datetime.min.time()).replace(hour=12)


def periodo():
    hoy = ahora().date()
    desde = _fecha(request.args.get("desde"), hoy.replace(day=1))
    hasta = _fecha(request.args.get("hasta"), hoy)
    if hasta < desde:
        desde, hasta = hasta, desde
    return desde, hasta


def rango_dt(desde, hasta):
    return datetime.combine(desde, datetime.min.time()), datetime.combine(hasta + timedelta(days=1), datetime.min.time())


def requiere(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def envuelta(*a, **kw):
            if not current_user.puede(*roles):
                flash("Tu usuario no tiene permiso para esa sección.", "err")
                return redirect(url_for("inicio_usuario"))
            return f(*a, **kw)
        return envuelta
    return deco


def errores_flash(e):
    for m in e.errores:
        flash(m, "err")


def get_or_404(modelo, oid):
    obj = db.session.get(modelo, oid)
    if obj is None:
        abort(404)
    return obj


def wa_link(numero, texto):
    num = "".join(ch for ch in (numero or "") if ch.isdigit())
    if len(num) == 10:
        num = "57" + num
    return f"https://wa.me/{num}?text={quote(texto)}" if num else f"https://wa.me/?text={quote(texto)}"


def procesar_imagen(archivo, lado=1000):
    from PIL import Image, ImageOps
    try:
        img = Image.open(archivo)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            fondo = Image.new("RGB", img.size, (255, 255, 255))
            fondo.paste(img, mask=img.split()[-1])
            img = fondo
        else:
            img = img.convert("RGB")
        img.thumbnail((lado, lado))
        buf = BytesIO()
        img.save(buf, "JPEG", quality=82, optimize=True)
        return buf.getvalue()
    except Exception:
        raise ErrorNegocio("No pude leer la imagen. Usa una foto JPG o PNG.")


# límites sencillos contra abuso (login y pedidos de la tienda)
_intentos = defaultdict(list)


def limitar(clave, maximo, segundos):
    t = time.time()
    lst = [x for x in _intentos[clave] if t - x < segundos]
    _intentos[clave] = lst
    if len(lst) >= maximo:
        return False
    lst.append(t)
    return True


def ip():
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()


# ======================================================== CSRF y contexto
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]


@app.before_request
def proteger_post():
    if request.method == "POST":
        enviado = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not enviado or not secrets.compare_digest(enviado, session.get("_csrf", "")):
            if request.is_json or request.headers.get("X-CSRF-Token") is not None:
                return jsonify(ok=False, errores=["La sesión expiró. Recarga la página."]), 400
            flash("La página estaba vieja. Intenta de nuevo.", "err")
            return redirect(request.referrer or url_for("login"))


@app.context_processor
def contexto():
    cfg = config()
    extra = {"cfg": cfg, "csrf_token": csrf_token, "STATIC_V": STATIC_V, "pesos": pesos, "cant": cant,
             "ROLES": ROLES, "LISTAS_PRECIO": LISTAS_PRECIO, "TIPOS_MEDIO": TIPOS_MEDIO}
    if current_user.is_authenticated:
        extra["TURNO"] = turno_abierto()
        extra["PEDIDOS_PEND"] = Pedido.query.filter_by(estado="pendiente").count()
    return extra


@app.template_filter("pesos")
def f_pesos(v):
    return pesos(v)


@app.template_filter("cant")
def f_cant(v):
    return cant(v)


@app.template_filter("fh")
def f_fh(d):
    return d.strftime("%d/%m/%Y %I:%M %p").replace("AM", "a. m.").replace("PM", "p. m.") if d else ""


@app.template_filter("f")
def f_f(d):
    return d.strftime("%d/%m/%Y") if d else ""


@app.errorhandler(404)
def no_encontrado(e):
    return render_template("error.html", titulo="No encontrado", msg="Esa página no existe."), 404


@app.errorhandler(413)
def muy_grande(e):
    flash("El archivo es muy pesado (máximo 8 MB).", "err")
    return redirect(request.referrer or url_for("login"))


@app.errorhandler(500)
def error_500(e):
    db.session.rollback()
    return render_template("error.html", titulo="Algo falló", msg="Ocurrió un error inesperado. Intenta de nuevo."), 500


# ================================================================ arranque
with app.app_context():
    # Render arranca varios procesos a la vez: un candado evita crear dos veces los datos iniciales
    es_pg = db.engine.dialect.name == "postgresql"
    if es_pg:
        db.session.execute(db.text("SELECT pg_advisory_lock(7310515)"))
    db.create_all()
    migrar()
    semilla()
    if not Usuario.query.first():
        clave = os.getenv("ADMIN_PASSWORD")
        if clave:
            u = Usuario(usuario=(os.getenv("ADMIN_USER") or "admin").strip().lower(), nombre="Administrador", rol="admin")
            u.set_password(clave)
            db.session.add(u)
            db.session.commit()
            print("Usuario administrador creado.", flush=True)
        else:
            print("AVISO: no hay usuarios. Define ADMIN_PASSWORD para crear el administrador.", flush=True)
    if es_pg:
        db.session.execute(db.text("SELECT pg_advisory_unlock(7310515)"))
        db.session.commit()


@app.route("/salud")
def salud():
    """Respuesta liviana para despertar la app (la usa la página de entrada de softgestapps.com)."""
    r = jsonify(ok=True)
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Cache-Control"] = "no-store"
    return r


# ================================================================ archivos
@app.route("/logo")
def logo():
    cfg = config()
    if cfg.logo:
        r = send_file(BytesIO(cfg.logo), mimetype=cfg.logo_mime or "image/png")
        r.headers["Cache-Control"] = "public, max-age=2592000"
        return r
    # Sin logo propio en Configuración: logo oficial de Moto Speed.
    # ?f=c -> versión cuadrada (para los espacios redondos: menú, login, tienda).
    archivo = "logo-cuadrado.webp" if request.args.get("f") == "c" else "logo.webp"
    return redirect(url_for("static", filename=archivo, v=STATIC_V))


@app.route("/img/p/<int:pid>")
def img_producto(pid):
    p = db.session.get(Producto, pid)
    if p is None or not p.imagen:
        return redirect(url_for("static", filename="sin-foto.svg"))
    r = send_file(BytesIO(p.imagen), mimetype=p.imagen_mime or "image/jpeg")
    r.headers["Cache-Control"] = "public, max-age=2592000"
    return r


# ================================================================== login
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inicio_usuario"))
    sin_usuarios = Usuario.query.first() is None
    if request.method == "POST":
        if not limitar("login:" + ip(), 10, 600):
            flash("Demasiados intentos. Espera unos minutos.", "err")
            return redirect(url_for("login"))
        u = Usuario.query.filter_by(usuario=(request.form.get("usuario") or "").strip().lower()).first()
        if u and u.activo and u.check_password(request.form.get("clave") or ""):
            login_user(u, remember=bool(request.form.get("recordar")))
            session.permanent = True
            u.ultimo_ingreso = ahora()
            db.session.commit()
            return redirect(request.args.get("next") if (request.args.get("next") or "").startswith("/") else url_for("inicio_usuario"))
        flash("Usuario o clave incorrectos.", "err")
    return render_template("login.html", sin_usuarios=sin_usuarios)


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/inicio")
@login_required
def inicio_usuario():
    if current_user.rol == "admin":
        return redirect(url_for("panel"))
    if current_user.rol == "bodega":
        return redirect(url_for("productos"))
    if current_user.rol == "preventista":
        return redirect(url_for("mi_ruta"))
    return redirect(url_for("pos"))


@app.route("/cuenta", methods=["GET", "POST"])
@login_required
def cuenta():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("actual") or ""):
            flash("La clave actual no es correcta.", "err")
        elif len(request.form.get("nueva") or "") < 6:
            flash("La nueva clave debe tener al menos 6 caracteres.", "err")
        elif request.form.get("nueva") != request.form.get("repite"):
            flash("Las claves nuevas no coinciden.", "err")
        else:
            current_user.set_password(request.form["nueva"])
            db.session.commit()
            flash("Clave actualizada.", "ok")
            return redirect(url_for("inicio_usuario"))
    return render_template("cuenta.html")


# ================================================================== panel
@app.route("/panel")
@requiere("admin")
def panel():
    hoy = ahora().date()
    r_hoy = rpt.resumen(hoy, hoy)
    r_mes = rpt.resumen(hoy.replace(day=1), hoy)
    r_30 = rpt.resumen(hoy - timedelta(days=29), hoy)
    prods = Producto.query.filter_by(activo=True).all()
    bajos = sorted([p for p in prods if p.bajo_minimo], key=lambda p: p.stock - p.stock_minimo)[:12]
    valor_inv = sum(p.valor_inventario for p in prods)
    cartera = sum(v.saldo for v in Venta.query.filter_by(estado="credito").all())
    por_pagar = sum(c.saldo for c in Compra.query.filter(Compra.anulada.is_(False), Compra.pagado < Compra.total).all())
    turno = turno_abierto()
    caja = resumen_turno(turno) if turno else None
    pedidos = Pedido.query.filter_by(estado="pendiente").order_by(Pedido.fecha.desc()).limit(6).all()
    top = rpt.top_productos(hoy - timedelta(days=29), hoy, 8)
    serie30 = [(d.strftime("%d/%m"), round(v), round(u)) for d, v, u in r_30["serie"]]
    return render_template("panel.html", serie30=serie30, hoy=hoy, r_hoy=r_hoy, r_mes=r_mes, r_30=r_30, bajos=bajos, valor_inv=valor_inv,
                           cartera=cartera, por_pagar=por_pagar, turno=turno, caja=caja, pedidos=pedidos, top=top,
                           num_prods=len(prods), num_bajos=sum(1 for p in prods if p.bajo_minimo))


# ===================================================================== POS
def producto_json(p, reservado=None, ver_costo=False):
    d = {"id": p.id, "codigo": p.codigo, "barras": p.codigo_barras or "", "nombre": p.nombre,
         "marca": p.marca or "", "compat": p.compatibilidad or "", "ubicacion": p.ubicacion or "",
         "unidad": p.unidad, "stock": p.stock, "reservado": (reservado or {}).get(p.id, 0),
         "detal": p.precio_detal, "mayor": p.precio_mayor or p.precio_detal, "taller": p.precio_taller or p.precio_detal,
         "iva": p.iva_pct, "img": url_for("img_producto", pid=p.id, v=p.imagen_v) if p.imagen_v else ""}
    if ver_costo:
        d["costo"] = round(p.costo_promedio or 0)
        d["ultimo"] = round(p.ultimo_costo or p.costo_promedio or 0)
    return d


def buscar_productos(q, limite=30, solo_activos=True):
    query = Producto.query
    if solo_activos:
        query = query.filter(Producto.activo.is_(True))
    q = (q or "").strip()
    if q:
        exacto = Producto.query.filter(or_(Producto.codigo.ilike(q), Producto.codigo_barras == q),
                                       Producto.activo.is_(True)).first()
        for palabra in normalizar(q).split()[:6]:
            query = query.filter(Producto.busqueda.like(f"%{palabra}%"))
        res = query.order_by(Producto.nombre).limit(limite).all()
        if exacto and exacto not in res:
            res.insert(0, exacto)
        elif exacto:
            res.remove(exacto)
            res.insert(0, exacto)
        return res
    return query.order_by(Producto.nombre).limit(limite).all()


@app.route("/pos")
@requiere("cajero")
def pos():
    medios = MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden, MedioPago.id).all()
    precarga = None
    if request.args.get("cotizacion"):
        c = db.session.get(Cotizacion, int(request.args["cotizacion"]))
        if c and c.estado == "abierta":
            precarga = {"tipo": "cotizacion", "id": c.id, "folio": c.folio, "lista": c.lista,
                        "descuento": c.descuento, "nota": c.nota or "",
                        "cliente": cliente_json(c.cliente) if c.cliente else None,
                        "cliente_nombre": c.cliente_nombre or "", "cliente_tel": c.cliente_tel or "",
                        "items": [{"producto": producto_json(it.producto, ver_costo=current_user.ve_costos),
                                   "cantidad": it.cantidad, "precio": it.precio} for it in c.items if it.producto.activo]}
    elif request.args.get("pedido"):
        ped = db.session.get(Pedido, int(request.args["pedido"]))
        if ped and ped.estado == "pendiente":
            cli = ped.cliente or (Cliente.query.filter(Cliente.telefono.ilike(f"%{ped.telefono[-10:]}%")).first()
                                  if ped.telefono else None)
            etiqueta = (f"Preventa {ped.folio} · {ped.vendedor.nombre}" if ped.es_preventa and ped.vendedor
                        else f"Pedido web {ped.folio}")
            precarga = {"tipo": "pedido", "id": ped.id, "folio": ped.folio, "lista": ped.lista or "detal", "descuento": 0,
                        "nota": etiqueta + (f" · {ped.nota}" if ped.nota else ""),
                        "cliente": cliente_json(cli) if cli else None, "cliente_nombre": ped.nombre,
                        "cliente_tel": ped.telefono, "cliente_doc": ped.documento or "",
                        "items": [{"producto": producto_json(it.producto, ver_costo=current_user.ve_costos),
                                   "cantidad": it.cantidad, "precio": it.precio} for it in ped.items]}
    return render_template("pos.html", medios=medios, turno=turno_abierto(), precarga=precarga,
                           destacados=[producto_json(p, ver_costo=current_user.ve_costos) for p in
                                       Producto.query.filter_by(activo=True, destacado=True).limit(12).all()])


@app.route("/api/productos")
@login_required
def api_productos():
    res = buscar_productos(request.args.get("q"), 30)
    reservado = reservado_por_producto()
    return jsonify([producto_json(p, reservado, current_user.ve_costos) for p in res])


def cliente_json(c):
    return {"id": c.id, "nombre": c.nombre, "documento": c.documento or "", "telefono": c.telefono or "",
            "lista": c.lista, "cupo": c.cupo, "saldo": saldo_cliente(c)}


@app.route("/api/clientes")
@login_required
def api_clientes():
    q = (request.args.get("q") or "").strip()
    query = Cliente.query.filter_by(activo=True)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Cliente.nombre.ilike(like), Cliente.documento.ilike(like), Cliente.telefono.ilike(like)))
    return jsonify([cliente_json(c) for c in query.order_by(Cliente.nombre).limit(15).all()])


@app.route("/api/clientes", methods=["POST"])
@requiere("cajero")
def api_cliente_nuevo():
    d = request.get_json(silent=True) or {}
    nombre = (d.get("nombre") or "").strip()
    if len(nombre) < 2:
        return jsonify(ok=False, errores=["Escribe el nombre del cliente."]), 400
    doc = (d.get("documento") or "").strip()[:30] or None
    if doc and Cliente.query.filter_by(documento=doc).first():
        return jsonify(ok=False, errores=["Ya existe un cliente con ese documento."]), 400
    c = Cliente(nombre=nombre[:140], documento=doc, telefono=(d.get("telefono") or "").strip()[:40] or None,
                lista=d.get("lista") if d.get("lista") in LISTAS_PRECIO else "detal")
    db.session.add(c)
    db.session.commit()
    return jsonify(ok=True, cliente=cliente_json(c))


@app.route("/pos/venta", methods=["POST"])
@requiere("cajero")
def pos_venta():
    d = request.get_json(silent=True) or {}
    cliente = db.session.get(Cliente, int(d["cliente_id"])) if d.get("cliente_id") else None
    pedido = db.session.get(Pedido, int(d["pedido_id"])) if d.get("pedido_id") else None
    if pedido and pedido.estado != "pendiente":
        return jsonify(ok=False, errores=["Ese pedido web ya fue confirmado o cancelado."]), 400
    cot = db.session.get(Cotizacion, int(d["cotizacion_id"])) if d.get("cotizacion_id") else None
    try:
        v = registrar_venta(current_user, d.get("items"), d.get("pagos"), cliente=cliente,
                            cliente_nombre=d.get("cliente_nombre", ""), cliente_doc=d.get("cliente_doc", ""),
                            cliente_tel=d.get("cliente_tel", ""), lista=d.get("lista", "detal"),
                            descuento=n(d.get("descuento")), credito=bool(d.get("credito")), nota=d.get("nota", ""),
                            pedido=pedido, cotizacion=cot if cot and cot.estado == "abierta" else None)
        db.session.commit()
    except ErrorNegocio as e:
        db.session.rollback()
        return jsonify(ok=False, errores=e.errores), 400
    return jsonify(ok=True, id=v.id, folio=v.folio, total=v.total, cambio=v.cambio, saldo=v.saldo,
                   ticket=url_for("venta_ticket", vid=v.id), detalle=url_for("venta_detalle", vid=v.id),
                   whatsapp=wa_link(v.cliente_tel, texto_recibo(v)) if v.cliente_tel else "")


@app.route("/pos/cotizacion", methods=["POST"])
@requiere("cajero")
def pos_cotizacion():
    d = request.get_json(silent=True) or {}
    cliente = db.session.get(Cliente, int(d["cliente_id"])) if d.get("cliente_id") else None
    try:
        c = guardar_cotizacion(current_user, d.get("items"), cliente, d.get("cliente_nombre", ""),
                               d.get("cliente_tel", ""), d.get("lista", "detal"), n(d.get("descuento")),
                               d.get("nota", ""))
        db.session.commit()
    except ErrorNegocio as e:
        db.session.rollback()
        return jsonify(ok=False, errores=e.errores), 400
    return jsonify(ok=True, url=url_for("cotizacion_detalle", cid=c.id))


def texto_recibo(v):
    cfg = config()
    lineas = [f"*{cfg.nombre}* · Recibo {v.folio}", f"{v.fecha:%d/%m/%Y %I:%M %p}", ""]
    for it in v.items:
        lineas.append(f"{cant(it.cantidad)} × {it.nombre} — {pesos(it.total)}")
    if v.descuento:
        lineas.append(f"Descuento: -{pesos(v.descuento)}")
    lineas += ["", f"*Total: {pesos(v.total)}*"]
    if v.saldo:
        lineas.append(f"Saldo pendiente: {pesos(v.saldo)}")
    lineas += ["", "Ver recibo: " + url_for("recibo_publico", token=v.token, _external=True)]
    return "\n".join(lineas)


# ================================================================= ventas
@app.route("/ventas")
@requiere("cajero")
def ventas():
    desde, hasta = periodo()
    ini, fin = rango_dt(desde, hasta)
    q = Venta.query.filter(Venta.fecha >= ini, Venta.fecha < fin)
    estado = request.args.get("estado")
    if estado in ("pagada", "credito", "anulada"):
        q = q.filter(Venta.estado == estado)
    if request.args.get("origen") in ("pos", "tienda"):
        q = q.filter(Venta.origen == request.args["origen"])
    texto = (request.args.get("q") or "").strip()
    if texto:
        num = "".join(ch for ch in texto if ch.isdigit())
        conds = [Venta.cliente_nombre.ilike(f"%{texto}%"), Venta.cliente_doc.ilike(f"%{texto}%")]
        if num:
            conds.append(Venta.numero == int(num))
        q = q.filter(or_(*conds))
    lista = q.order_by(Venta.fecha.desc()).limit(500).all()
    validas = [v for v in lista if not v.anulada]
    return render_template("ventas.html", ventas=lista, desde=desde, hasta=hasta,
                           total=sum(v.neto for v in validas), ganancia=sum(v.ganancia for v in validas))


@app.route("/ventas/<int:vid>")
@requiere("cajero")
def venta_detalle(vid):
    v = get_or_404(Venta, vid)
    medios = MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all()
    return render_template("venta_detalle.html", v=v, medios=medios, turno=turno_abierto(),
                           wa=wa_link(v.cliente_tel, texto_recibo(v)),
                           devs=[(d, json.loads(d.detalle or "[]")) for d in v.devoluciones])


@app.route("/ventas/<int:vid>/ticket")
@requiere("cajero")
def venta_ticket(vid):
    v = get_or_404(Venta, vid)
    return render_template("ticket.html", v=v, publico=False, auto=request.args.get("imprimir") == "1")


@app.route("/r/<token>")
def recibo_publico(token):
    v = Venta.query.filter_by(token=token).first_or_404()
    return render_template("ticket.html", v=v, publico=True, auto=False)


@app.route("/ventas/<int:vid>/abono", methods=["POST"])
@requiere("cajero")
def venta_abono(vid):
    v = get_or_404(Venta, vid)
    medio = db.session.get(MedioPago, int(request.form.get("medio_id") or 0))
    try:
        if medio is None:
            raise ErrorNegocio("Elige el medio de pago.")
        registrar_abono(v, current_user, n(request.form.get("valor")), medio, request.form.get("referencia"))
        db.session.commit()
        flash("Abono registrado.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(request.form.get("volver") or url_for("venta_detalle", vid=vid))


@app.route("/ventas/<int:vid>/anular", methods=["POST"])
@requiere("admin")
def venta_anular(vid):
    v = get_or_404(Venta, vid)
    try:
        anular_venta(v, current_user, request.form.get("motivo"))
        db.session.commit()
        flash(f"Venta {v.folio} anulada: el inventario y la caja quedaron corregidos.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("venta_detalle", vid=vid))


@app.route("/ventas/<int:vid>/devolucion", methods=["POST"])
@requiere("cajero")
def venta_devolucion(vid):
    v = get_or_404(Venta, vid)
    cantidades = {}
    for it in v.items:
        q = n(request.form.get(f"dev_{it.id}"))
        if q > 0:
            cantidades[it.id] = q
    medio = db.session.get(MedioPago, int(request.form.get("medio_id") or 0))
    try:
        if medio is None:
            raise ErrorNegocio("Elige cómo se devuelve el dinero.")
        d = devolver(v, current_user, cantidades, medio, request.form.get("motivo"))
        db.session.commit()
        msg = f"Devolución registrada por {pesos(d.valor)}."
        if d.reembolso:
            msg += f" Se le devuelven {pesos(d.reembolso)} en {medio.nombre}."
        flash(msg, "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("venta_detalle", vid=vid))


@app.route("/cartera")
@requiere("cajero")
def cartera():
    vs = Venta.query.filter_by(estado="credito").order_by(Venta.fecha).all()
    hoy = ahora().date()
    por_cliente = defaultdict(lambda: {"saldo": 0, "ventas": [], "vencido": 0})
    for v in vs:
        k = v.cliente_id or 0
        d = por_cliente[k]
        d["cliente"] = v.cliente
        d["nombre"] = v.cliente_nombre
        d["saldo"] += v.saldo
        d["ventas"].append(v)
        if v.vence and v.vence < hoy:
            d["vencido"] += v.saldo
    filas = sorted(por_cliente.values(), key=lambda d: -d["saldo"])
    for d in filas:
        c = d.get("cliente")
        d["wa"] = wa_link(c.telefono if c else "", f"Hola {d['nombre']}, te saluda {config().nombre}. "
                          f"Te recordamos que tienes un saldo pendiente de {pesos(d['saldo'])}. ¡Gracias!") if c and c.telefono else ""
    return render_template("cartera.html", filas=filas, total=sum(d["saldo"] for d in filas),
                           vencido=sum(d["vencido"] for d in filas), hoy=hoy)


# =================================================================== caja
DENOMINACIONES = [100000, 50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50]


@app.route("/caja")
@requiere("cajero")
def caja():
    t = turno_abierto()
    r = resumen_turno(t) if t else None
    ultimo = Turno.query.filter_by(estado="cerrado").order_by(Turno.id.desc()).first()
    medios = MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all()
    return render_template("caja.html", t=t, r=r, ultimo=ultimo, medios=medios, denominaciones=DENOMINACIONES)


@app.route("/caja/abrir", methods=["POST"])
@requiere("cajero")
def caja_abrir():
    try:
        abrir_turno(current_user, n(request.form.get("base")), request.form.get("nota"))
        db.session.commit()
        flash("Caja abierta. ¡Buenas ventas!", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(request.form.get("volver") or url_for("caja"))


@app.route("/caja/movimiento", methods=["POST"])
@requiere("cajero")
def caja_movimiento():
    tipo = request.form.get("tipo")
    medio = db.session.get(MedioPago, int(request.form.get("medio_id") or 0))
    concepto = (request.form.get("concepto") or "").strip()
    try:
        if tipo not in ("ingreso", "egreso"):
            raise ErrorNegocio("Elige si es entrada o salida de dinero.")
        if medio is None:
            raise ErrorNegocio("Elige el medio.")
        if len(concepto) < 3:
            raise ErrorNegocio("Escribe el concepto (ej. pago domicilio, almuerzo, base extra).")
        mov_caja(tipo, n(request.form.get("valor")), medio, concepto, "manual", None, current_user.id)
        db.session.commit()
        flash("Movimiento registrado.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("caja"))


@app.route("/caja/movimiento/<int:mid>/anular", methods=["POST"])
@requiere("admin")
def caja_mov_anular(mid):
    m = get_or_404(MovCaja, mid)
    if m.origen != "manual" or not m.turno or not m.turno.abierto:
        flash("Solo se anulan movimientos manuales de la caja abierta.", "err")
    else:
        m.anulado = True
        db.session.commit()
        flash("Movimiento anulado.", "ok")
    return redirect(url_for("caja"))


@app.route("/caja/cerrar", methods=["POST"])
@requiere("cajero")
def caja_cerrar():
    t = turno_abierto()
    if t is None:
        flash("No hay caja abierta.", "err")
        return redirect(url_for("caja"))
    denoms = {}
    total = 0
    for d in DENOMINACIONES:
        q = int(n(request.form.get(f"d_{d}")))
        if q > 0:
            denoms[str(d)] = q
            total += q * d
    contado = total if denoms else n(request.form.get("contado"))
    try:
        cerrar_turno(t, current_user, contado, denoms, request.form.get("nota"))
        db.session.commit()
        flash(f"Caja #{t.numero} cerrada.", "ok")
        return redirect(url_for("turno_detalle", tid=t.id))
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
        return redirect(url_for("caja"))


@app.route("/caja/turnos")
@requiere("cajero")
def turnos():
    q = Turno.query.order_by(Turno.id.desc())
    if not current_user.puede("admin"):
        q = q.filter(Turno.usuario_id == current_user.id)
    return render_template("turnos.html", turnos=q.limit(120).all())


@app.route("/caja/turno/<int:tid>")
@requiere("cajero")
def turno_detalle(tid):
    t = get_or_404(Turno, tid)
    if not current_user.puede("admin") and t.usuario_id != current_user.id:
        abort(404)
    r = resumen_turno(t)
    return render_template("turno_detalle.html", t=t, r=r, denoms=json.loads(t.denominaciones or "{}"),
                           denominaciones=DENOMINACIONES)


# =============================================================== clientes
@app.route("/clientes")
@requiere("cajero")
def clientes():
    q = (request.args.get("q") or "").strip()
    query = Cliente.query.filter_by(activo=True)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Cliente.nombre.ilike(like), Cliente.documento.ilike(like), Cliente.telefono.ilike(like)))
    lista = query.order_by(Cliente.nombre).limit(300).all()
    saldos = defaultdict(float)
    for v in Venta.query.filter_by(estado="credito").all():
        saldos[v.cliente_id] += v.saldo
    return render_template("clientes.html", clientes=lista, saldos=saldos, q=q)


@app.route("/clientes/nuevo", methods=["GET", "POST"])
@app.route("/clientes/<int:cid>/editar", methods=["GET", "POST"])
@requiere("cajero")
def cliente_form(cid=None):
    c = get_or_404(Cliente, cid) if cid else Cliente(lista="detal", plazo_dias=30, cupo=0)
    if request.method == "POST":
        f = request.form
        c.nombre = (f.get("nombre") or "").strip()[:140]
        c.documento = (f.get("documento") or "").strip()[:30] or None
        c.telefono = (f.get("telefono") or "").strip()[:40] or None
        c.email = (f.get("email") or "").strip()[:120] or None
        c.direccion = (f.get("direccion") or "").strip()[:200] or None
        c.lista = f.get("lista") if f.get("lista") in LISTAS_PRECIO else "detal"
        c.barrio = (f.get("barrio") or "").strip()[:80] or None
        coords = leer_coordenadas(f.get("ubicacion"))
        if coords:
            c.lat, c.lng = coords
        elif (f.get("ubicacion") or "").strip() == "":
            c.lat = c.lng = None
        if current_user.puede("admin"):
            nueva_ruta = int(f.get("ruta_id")) if (f.get("ruta_id") or "").isdigit() else None
            if nueva_ruta != c.ruta_id:
                c.ruta_id = nueva_ruta
                c.orden_ruta = (db.session.query(db.func.max(Cliente.orden_ruta)).filter(Cliente.ruta_id == nueva_ruta)
                                .scalar() or 0) + 1 if nueva_ruta else 0
            c.cupo = max(n(f.get("cupo")), 0)
            c.plazo_dias = max(int(n(f.get("plazo_dias"), 30)), 0)
        c.notas = (f.get("notas") or "").strip()[:300] or None
        if len(c.nombre) < 2:
            flash("Escribe el nombre.", "err")
        elif c.documento and Cliente.query.filter(Cliente.documento == c.documento, Cliente.id != (c.id or 0)).autoflush(False).first():
            flash("Ya hay otro cliente con ese documento.", "err")
        else:
            db.session.add(c)
            db.session.commit()
            flash("Cliente guardado.", "ok")
            return redirect(url_for("cliente_detalle", cid=c.id))
    return render_template("cliente_form.html", c=c, rutas=Ruta.query.filter_by(activa=True).order_by(Ruta.nombre).all())


@app.route("/clientes/<int:cid>")
@requiere("cajero")
def cliente_detalle(cid):
    c = get_or_404(Cliente, cid)
    vs = c.ventas.order_by(Venta.fecha.desc()).limit(100).all()
    medios = MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all()
    total_comprado = sum(v.neto for v in c.ventas.filter(Venta.estado != "anulada").all())
    return render_template("cliente_detalle.html", c=c, ventas=vs, saldo=saldo_cliente(c), medios=medios,
                           total_comprado=total_comprado, turno=turno_abierto())


@app.route("/clientes/<int:cid>/archivar", methods=["POST"])
@requiere("admin")
def cliente_archivar(cid):
    c = get_or_404(Cliente, cid)
    if saldo_cliente(c) > 0:
        flash("No se puede archivar: tiene saldo pendiente.", "err")
        return redirect(url_for("cliente_detalle", cid=cid))
    c.activo = False
    db.session.commit()
    flash("Cliente archivado.", "ok")
    return redirect(url_for("clientes"))


# =========================================================== cotizaciones
@app.route("/cotizaciones")
@requiere("cajero")
def cotizaciones():
    lista = Cotizacion.query.order_by(Cotizacion.fecha.desc()).limit(200).all()
    return render_template("cotizaciones.html", cotizaciones=lista, hoy=ahora())


@app.route("/cotizaciones/<int:cid>")
@requiere("cajero")
def cotizacion_detalle(cid):
    c = get_or_404(Cotizacion, cid)
    cfg = config()
    texto = "\n".join([f"*{cfg.nombre}* · Cotización {c.folio}", ""] +
                      [f"{cant(i.cantidad)} × {i.nombre} — {pesos(i.cantidad * i.precio)}" for i in c.items] +
                      ([f"Descuento: -{pesos(c.descuento)}"] if c.descuento else []) +
                      ["", f"*Total: {pesos(c.total)}*", f"Válida por {c.vigencia_dias} días."])
    return render_template("cotizacion_detalle.html", c=c, wa=wa_link(c.cliente_tel, texto),
                           vence=c.fecha + timedelta(days=c.vigencia_dias))


@app.route("/cotizaciones/<int:cid>/descartar", methods=["POST"])
@requiere("cajero")
def cotizacion_descartar(cid):
    c = get_or_404(Cotizacion, cid)
    if c.estado == "abierta":
        c.estado = "descartada"
        db.session.commit()
    return redirect(url_for("cotizaciones"))


# ======================================================== pedidos web (admin)
@app.route("/pedidos")
@requiere("cajero")
def pedidos():
    estado = request.args.get("estado", "pendiente")
    origen = request.args.get("origen", "")
    q = Pedido.query
    if estado in ("pendiente", "confirmado", "cancelado"):
        q = q.filter_by(estado=estado)
    if origen == "preventa":
        q = q.filter(Pedido.origen == "preventa")
    elif origen == "web":
        q = q.filter(or_(Pedido.origen == "web", Pedido.origen.is_(None)))
    return render_template("pedidos.html", pedidos=q.order_by(Pedido.fecha.desc()).limit(200).all(), estado=estado,
                           origen=origen, vencido=pedido_vencido)


@app.route("/pedidos/<int:pid>")
@requiere("cajero")
def pedido_detalle(pid):
    p = get_or_404(Pedido, pid)
    cfg = config()
    texto = (f"Hola {p.nombre.split()[0]}, te saluda {cfg.nombre}. Recibimos tu pedido {p.folio} por {pesos(p.total)}. ")
    return render_template("pedido_detalle.html", p=p, vencido=pedido_vencido(p), wa=wa_link(p.telefono, texto),
                           venta=db.session.get(Venta, p.venta_id) if p.venta_id else None)


@app.route("/pedidos/<int:pid>/cancelar", methods=["POST"])
@requiere("cajero")
def pedido_cancelar(pid):
    p = get_or_404(Pedido, pid)
    if p.estado == "pendiente":
        p.estado = "cancelado"
        p.gestion_nota = (request.form.get("motivo") or "").strip()[:300] or "Cancelado"
        db.session.commit()
        flash(f"Pedido {p.folio} cancelado; las unidades apartadas vuelven a estar disponibles.", "ok")
    return redirect(url_for("pedido_detalle", pid=pid))


# ============================================================== productos
@app.route("/productos")
@requiere("cajero", "bodega")
def productos():
    q = request.args.get("q", "")
    cat = request.args.get("cat", type=int)
    filtro = request.args.get("f", "")
    query = Producto.query.filter(Producto.activo.is_(filtro != "inactivos"))
    if cat:
        query = query.filter(Producto.categoria_id == cat)
    if q.strip():
        for palabra in normalizar(q).split()[:6]:
            query = query.filter(Producto.busqueda.like(f"%{palabra}%"))
    lista = query.order_by(Producto.nombre).all()
    if filtro == "bajos":
        lista = [p for p in lista if p.bajo_minimo]
    elif filtro == "agotados":
        lista = [p for p in lista if (p.stock or 0) <= 0]
    elif filtro == "sinfoto":
        lista = [p for p in lista if not p.imagen_v]
    total_valor = sum(p.valor_inventario for p in lista)
    return render_template("productos.html", productos=lista[:600], total=len(lista), q=q, cat=cat, filtro=filtro,
                           categorias=Categoria.query.order_by(Categoria.nombre).all(), total_valor=total_valor,
                           reservado=reservado_por_producto())


@app.route("/productos/nuevo", methods=["GET", "POST"])
@app.route("/productos/<int:pid>/editar", methods=["GET", "POST"])
@requiere("bodega")
def producto_form(pid=None):
    p = get_or_404(Producto, pid) if pid else Producto(unidad="unidad", en_tienda=True, activo=True, stock=0,
                                                       costo_promedio=0, iva_pct=0)
    cfg = config()
    if request.method == "POST":
        f = request.form
        nuevo = p.id is None
        p.codigo = (f.get("codigo") or "").strip()[:60]
        p.nombre = (f.get("nombre") or "").strip()[:200]
        p.codigo_barras = (f.get("codigo_barras") or "").strip()[:60] or None
        p.categoria_id = int(f.get("categoria_id")) if (f.get("categoria_id") or "").isdigit() else None
        for campo, largo in (("marca", 80), ("referencia", 80), ("compatibilidad", 400), ("ubicacion", 60)):
            setattr(p, campo, (f.get(campo) or "").strip()[:largo] or None)
        p.unidad = f.get("unidad") if f.get("unidad") in UNIDADES else "unidad"
        p.descripcion = (f.get("descripcion") or "").strip() or None
        p.stock_minimo = max(n(f.get("stock_minimo")), 0)
        p.precio_detal = max(round(n(f.get("precio_detal"))), 0)
        p.precio_mayor = max(round(n(f.get("precio_mayor"))), 0)
        p.precio_taller = max(round(n(f.get("precio_taller"))), 0)
        p.iva_pct = pct(f.get("iva_pct")) if cfg.cobra_iva else (p.iva_pct or 0)
        p.en_tienda = bool(f.get("en_tienda"))
        p.destacado = bool(f.get("destacado"))
        p.activo = bool(f.get("activo")) if not nuevo else True
        errores = []
        with db.session.no_autoflush:
            dup_codigo = p.codigo and Producto.query.filter(Producto.codigo.ilike(p.codigo), Producto.id != (p.id or 0)).first()
            dup_barras = p.codigo_barras and Producto.query.filter(Producto.codigo_barras == p.codigo_barras,
                                                                   Producto.id != (p.id or 0)).first()
        if not p.codigo:
            errores.append("Escribe el código del repuesto.")
        if len(p.nombre) < 2:
            errores.append("Escribe el nombre del repuesto.")
        if p.precio_detal <= 0:
            errores.append("Escribe el precio detal.")
        if dup_codigo:
            errores.append("Ese código ya lo tiene otro repuesto.")
        if dup_barras:
            errores.append("Ese código de barras ya lo tiene otro repuesto.")
        stock_ini, costo_ini = n(f.get("stock_inicial")), n(f.get("costo_inicial"))
        if nuevo and stock_ini > 0 and costo_ini <= 0:
            errores.append("Si cargas stock inicial escribe también el costo unitario.")
        foto = request.files.get("foto")
        try:
            if foto and foto.filename:
                p.imagen = procesar_imagen(foto)
                p.imagen_mime = "image/jpeg"
                p.imagen_v = (p.imagen_v or 0) + 1
        except ErrorNegocio as e:
            errores += e.errores
        if errores:
            if not nuevo:
                db.session.rollback()
                p = db.session.get(Producto, pid)
            for m in errores:
                flash(m, "err")
            return render_template("producto_form.html", p=p, categorias=Categoria.query.order_by(Categoria.nombre).all(),
                                   unidades=UNIDADES, f=f)
        db.session.add(p)
        db.session.flush()
        if nuevo and stock_ini > 0:
            try:
                registrar_ajuste(current_user, "inicial",
                                 [{"producto_id": p.id, "cantidad": stock_ini, "costo": costo_ini}], "Al crear el repuesto")
                p.ultimo_costo = costo_ini
            except ErrorNegocio as e:
                db.session.rollback()
                errores_flash(e)
                return redirect(url_for("producto_form"))
        db.session.commit()
        flash("Repuesto guardado.", "ok")
        if f.get("otro"):
            return redirect(url_for("producto_form"))
        return redirect(url_for("producto_detalle", pid=p.id))
    return render_template("producto_form.html", p=p, categorias=Categoria.query.order_by(Categoria.nombre).all(),
                           unidades=UNIDADES, f={})


@app.route("/productos/<int:pid>")
@requiere("cajero", "bodega")
def producto_detalle(pid):
    p = get_or_404(Producto, pid)
    movs = Movimiento.query.filter_by(producto_id=p.id).order_by(Movimiento.fecha.desc(), Movimiento.id.desc()).limit(300).all()
    hoy = ahora().date()
    ini, fin = rango_dt(hoy - timedelta(days=89), hoy)
    vendidos_90 = -sum(m.cantidad for m in movs if m.tipo == "venta" and ini <= m.fecha < fin)
    return render_template("producto_detalle.html", p=p, movs=movs, vendidos_90=vendidos_90,
                           reservado=reservado_por_producto().get(p.id, 0))


@app.route("/productos/<int:pid>/foto/borrar", methods=["POST"])
@requiere("bodega")
def producto_foto_borrar(pid):
    p = get_or_404(Producto, pid)
    p.imagen = None
    p.imagen_v = 0
    db.session.commit()
    flash("Foto eliminada.", "ok")
    return redirect(url_for("producto_form", pid=pid))


@app.route("/productos/plantilla")
@requiere("bodega")
def productos_plantilla():
    return send_file(importador.plantilla(), as_attachment=True, download_name="plantilla_repuestos.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/productos/plantilla.csv")
@requiere("bodega")
def productos_plantilla_csv():
    return Response(importador.plantilla_csv(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=plantilla_repuestos.csv"})


@app.route("/productos/importar")
@requiere("bodega")
def productos_importar():
    cargas = CargaMasiva.query.order_by(CargaMasiva.id.desc()).limit(15).all()
    return render_template("importar.html", cargas=cargas, tam_lote=importador.TAM_LOTE)


@app.route("/productos/carga/leer-excel", methods=["POST"])
@requiere("bodega")
def carga_leer_excel():
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename.lower().endswith(".xlsx"):
        return jsonify(ok=False, error="Sube un archivo .xlsx o .csv."), 400
    try:
        encabezados, filas = importador.leer_excel(archivo)
    except ErrorNegocio as e:
        return jsonify(ok=False, error=" ".join(e.errores)), 400
    return jsonify(ok=True, encabezados=encabezados, filas=filas)


def _json_carga():
    datos = request.get_json(silent=True) or {}
    carga = db.session.get(CargaMasiva, int(datos.get("carga_id") or 0))
    if not carga:
        abort(404)
    return datos, carga


@app.route("/productos/carga/iniciar", methods=["POST"])
@requiere("bodega")
def carga_iniciar():
    datos = request.get_json(silent=True) or {}
    carga = importador.iniciar_carga(str(datos.get("archivo") or ""), current_user.id)
    db.session.commit()
    return jsonify(ok=True, carga_id=carga.id, consecutivo=carga.consecutivo)


@app.route("/productos/carga/lote", methods=["POST"])
@requiere("bodega")
def carga_lote():
    datos = request.get_json(silent=True) or {}
    carga = db.session.query(CargaMasiva).filter_by(id=int(datos.get("carga_id") or 0)).with_for_update().first()
    if not carga:
        return jsonify(ok=False, error="La carga no existe; vuelve a empezar."), 404
    try:
        r = importador.procesar_lote(carga, datos.get("filas") or [], int(datos.get("indice") or 0),
                                     current_user.id)
        db.session.commit()
    except ErrorNegocio as e:
        db.session.rollback()
        return jsonify(ok=False, error=" ".join(e.errores)), 400
    return jsonify(ok=True, **r)


@app.route("/productos/carga/finalizar", methods=["POST"])
@requiere("bodega")
def carga_finalizar():
    datos, carga = _json_carga()
    r = importador.finalizar_carga(carga)
    db.session.commit()
    return jsonify(ok=True, url=url_for("carga_detalle", cid=carga.id), **r)


@app.route("/productos/cargas/<int:cid>")
@requiere("bodega")
def carga_detalle(cid):
    return render_template("carga_detalle.html", c=get_or_404(CargaMasiva, cid))


@app.route("/productos/cargas/<int:cid>/rechazados.csv")
@requiere("bodega")
def carga_rechazados(cid):
    c = get_or_404(CargaMasiva, cid)
    return Response(importador.rechazados_csv(c), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={c.consecutivo}-rechazados.csv"})


@app.route("/categorias", methods=["POST"])
@requiere("bodega")
def categorias():
    accion = request.form.get("accion")
    if accion == "crear":
        nombre = (request.form.get("nombre") or "").strip()[:80]
        if nombre and not Categoria.query.filter(Categoria.nombre.ilike(nombre)).first():
            db.session.add(Categoria(nombre=nombre))
            db.session.commit()
            flash("Categoría creada.", "ok")
    elif accion == "renombrar":
        c = db.session.get(Categoria, int(request.form.get("id") or 0))
        nombre = (request.form.get("nombre") or "").strip()[:80]
        if c and nombre:
            c.nombre = nombre
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("Ya existe una categoría con ese nombre.", "err")
    elif accion == "borrar":
        c = db.session.get(Categoria, int(request.form.get("id") or 0))
        if c:
            if Producto.query.filter_by(categoria_id=c.id).first():
                flash("No se puede borrar: hay repuestos en esa categoría.", "err")
            else:
                db.session.delete(c)
                db.session.commit()
    return redirect(request.form.get("volver") or url_for("configuracion") + "#categorias")


@app.route("/kardex")
@requiere("bodega")
def kardex():
    desde, hasta = periodo()
    ini, fin = rango_dt(desde, hasta)
    q = Movimiento.query.filter(Movimiento.fecha >= ini, Movimiento.fecha < fin)
    if request.args.get("tipo") in TIPOS_MOV:
        q = q.filter(Movimiento.tipo == request.args["tipo"])
    movs = q.order_by(Movimiento.fecha.desc(), Movimiento.id.desc()).limit(800).all()
    return render_template("kardex.html", movs=movs, desde=desde, hasta=hasta, tipos=TIPOS_MOV)


# =============================================================== compras
def items_desde_form(prefijo_costo="costo"):
    ids = request.form.getlist("producto_id")
    cants = request.form.getlist("cantidad")
    costos = request.form.getlist(prefijo_costo)
    out = []
    for i, pid in enumerate(ids):
        if not pid:
            continue
        out.append({"producto_id": pid, "cantidad": n(cants[i] if i < len(cants) else 0),
                    prefijo_costo: n(costos[i] if i < len(costos) else 0)})
    return out


def precarga_producto():
    p = db.session.get(Producto, request.args.get("p", type=int) or 0)
    return producto_json(p, ver_costo=True) if p else None


@app.route("/compras")
@requiere("bodega")
def compras():
    desde, hasta = periodo()
    ini, fin = rango_dt(desde, hasta)
    q = Compra.query
    if request.args.get("f") == "porpagar":
        q = q.filter(Compra.anulada.is_(False), Compra.pagado < Compra.total)
    else:
        q = q.filter(Compra.fecha >= ini, Compra.fecha < fin)
    lista = q.order_by(Compra.fecha.desc()).limit(400).all()
    por_pagar = sum(c.saldo for c in Compra.query.filter(Compra.anulada.is_(False), Compra.pagado < Compra.total).all())
    return render_template("compras.html", compras=lista, desde=desde, hasta=hasta, por_pagar=por_pagar,
                           total=sum(c.total for c in lista if not c.anulada))


@app.route("/compras/nueva", methods=["GET", "POST"])
@requiere("bodega")
def compra_nueva():
    medios = MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all()
    proveedores = Proveedor.query.filter_by(activo=True).order_by(Proveedor.nombre).all()
    if request.method == "POST":
        f = request.form
        prov = db.session.get(Proveedor, int(f.get("proveedor_id") or 0)) if (f.get("proveedor_id") or "").isdigit() else None
        medio = db.session.get(MedioPago, int(f.get("medio_id") or 0)) if (f.get("medio_id") or "").isdigit() else None
        try:
            c = registrar_compra(current_user, prov, items_desde_form(), f.get("factura"), f.get("nota"),
                                 fecha_form(f.get("fecha")),
                                 {"modo": f.get("modo"), "medio": medio, "desde_caja": bool(f.get("desde_caja"))},
                                 int(n(f.get("plazo"), 30)))
            db.session.commit()
            flash(f"Compra C-{c.numero:05d} registrada: el stock y el costo promedio quedaron actualizados.", "ok")
            return redirect(url_for("compra_detalle", cid=c.id))
        except ErrorNegocio as e:
            db.session.rollback()
            errores_flash(e)
    return render_template("compra_form.html", medios=medios, proveedores=proveedores, turno=turno_abierto(),
                           hoy=ahora().date(), pre=precarga_producto())


@app.route("/compras/<int:cid>")
@requiere("bodega")
def compra_detalle(cid):
    c = get_or_404(Compra, cid)
    return render_template("compra_detalle.html", c=c, medios=MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all(),
                           turno=turno_abierto())


@app.route("/compras/<int:cid>/pago", methods=["POST"])
@requiere("bodega")
def compra_pago(cid):
    c = get_or_404(Compra, cid)
    medio = db.session.get(MedioPago, int(request.form.get("medio_id") or 0))
    try:
        if medio is None:
            raise ErrorNegocio("Elige el medio de pago.")
        pagar_compra(c, current_user, n(request.form.get("valor")), medio, bool(request.form.get("desde_caja")))
        db.session.commit()
        flash("Pago al proveedor registrado.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("compra_detalle", cid=cid))


@app.route("/compras/<int:cid>/anular", methods=["POST"])
@requiere("admin")
def compra_anular(cid):
    c = get_or_404(Compra, cid)
    try:
        anular_compra(c, current_user)
        db.session.commit()
        flash("Compra anulada; el stock y el costo promedio se recalcularon.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("compra_detalle", cid=cid))


@app.route("/proveedores")
@requiere("bodega")
def proveedores():
    lista = Proveedor.query.filter_by(activo=True).order_by(Proveedor.nombre).all()
    saldos = defaultdict(float)
    for c in Compra.query.filter(Compra.anulada.is_(False), Compra.pagado < Compra.total).all():
        saldos[c.proveedor_id] += c.saldo
    return render_template("proveedores.html", proveedores=lista, saldos=saldos)


@app.route("/proveedores/nuevo", methods=["GET", "POST"])
@app.route("/proveedores/<int:pid>/editar", methods=["GET", "POST"])
@requiere("bodega")
def proveedor_form(pid=None):
    p = get_or_404(Proveedor, pid) if pid else Proveedor()
    if request.method == "POST":
        f = request.form
        p.nombre = (f.get("nombre") or "").strip()[:140]
        for campo, largo in (("nit", 30), ("telefono", 40), ("contacto", 120), ("ciudad", 80), ("notas", 300)):
            setattr(p, campo, (f.get(campo) or "").strip()[:largo] or None)
        if f.get("archivar"):
            p.activo = False
        if len(p.nombre) < 2:
            flash("Escribe el nombre del proveedor.", "err")
        else:
            db.session.add(p)
            db.session.commit()
            flash("Proveedor guardado.", "ok")
            return redirect(request.args.get("volver") or url_for("proveedores"))
    return render_template("proveedor_form.html", p=p)


# ================================================================ ajustes
@app.route("/ajustes")
@requiere("bodega")
def ajustes():
    lista = Ajuste.query.order_by(Ajuste.fecha.desc()).limit(300).all()
    return render_template("ajustes.html", ajustes=lista)


@app.route("/ajustes/nuevo", methods=["GET", "POST"])
@requiere("bodega")
def ajuste_nuevo():
    if request.method == "POST":
        try:
            a = registrar_ajuste(current_user, request.form.get("motivo"), items_desde_form(), request.form.get("nota"))
            db.session.commit()
            flash(f"Ajuste A-{a.numero:05d} registrado.", "ok")
            return redirect(url_for("ajuste_detalle", aid=a.id))
        except ErrorNegocio as e:
            db.session.rollback()
            errores_flash(e)
    return render_template("ajuste_form.html", motivos=MOTIVOS_AJUSTE, pre=precarga_producto())


@app.route("/ajustes/<int:aid>")
@requiere("bodega")
def ajuste_detalle(aid):
    return render_template("ajuste_detalle.html", a=get_or_404(Ajuste, aid))


@app.route("/ajustes/<int:aid>/anular", methods=["POST"])
@requiere("admin")
def ajuste_anular(aid):
    a = get_or_404(Ajuste, aid)
    try:
        anular_ajuste(a, current_user)
        db.session.commit()
        flash("Ajuste anulado.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("ajuste_detalle", aid=aid))


# ============================================================ empleados
@app.route("/empleados")
@requiere("admin")
def empleados():
    return render_template("empleados.html", empleados=Empleado.query.order_by(Empleado.activo.desc(), Empleado.nombre).all())


@app.route("/empleados/nuevo", methods=["GET", "POST"])
@app.route("/empleados/<int:eid>/editar", methods=["GET", "POST"])
@requiere("admin")
def empleado_form(eid=None):
    e = get_or_404(Empleado, eid) if eid else Empleado(tipo_pago="fijo", aux_transporte=True, activo=True,
                                                       salario=config().smmlv)
    if request.method == "POST":
        f = request.form
        e.nombre = (f.get("nombre") or "").strip()[:140]
        e.documento = (f.get("documento") or "").strip()[:30] or None
        e.cargo = (f.get("cargo") or "").strip()[:80] or None
        e.telefono = (f.get("telefono") or "").strip()[:40] or None
        e.tipo_pago = f.get("tipo_pago") if f.get("tipo_pago") in TIPOS_PAGO_EMP else "fijo"
        e.salario = max(n(f.get("salario")), 0)
        e.comision_pct = min(max(pct(f.get("comision_pct")), 0), 100)
        e.aux_transporte = bool(f.get("aux_transporte"))
        e.usuario_id = int(f.get("usuario_id")) if (f.get("usuario_id") or "").isdigit() else None
        e.fecha_ingreso = _fecha(f.get("fecha_ingreso"))
        e.activo = bool(f.get("activo")) if eid else True
        if len(e.nombre) < 2:
            flash("Escribe el nombre.", "err")
        elif e.tipo_pago in ("comision", "mixto") and not e.usuario_id:
            flash("Para pagar comisión enlaza el usuario con el que vende en el POS.", "err")
        else:
            db.session.add(e)
            db.session.commit()
            flash("Empleado guardado.", "ok")
            return redirect(url_for("empleados"))
    return render_template("empleado_form.html", e=e, usuarios=Usuario.query.filter_by(activo=True).all(),
                           tipos=TIPOS_PAGO_EMP)


@app.route("/nomina")
@requiere("admin")
def nominas():
    lista = PagoNomina.query.order_by(PagoNomina.fecha_pago.desc(), PagoNomina.id.desc()).limit(300).all()
    return render_template("nominas.html", nominas=lista)


@app.route("/nomina/nueva", methods=["GET", "POST"])
@requiere("admin")
def nomina_nueva():
    cfg = config()
    hoy = ahora().date()
    f = request.form if request.method == "POST" else request.args
    emp = db.session.get(Empleado, int(f.get("empleado_id"))) if (f.get("empleado_id") or "").isdigit() else None
    desde = _fecha(f.get("desde"), hoy.replace(day=1) if hoy.day <= 15 else hoy.replace(day=16))
    hasta = _fecha(f.get("hasta"), hoy)
    dias = int(n(f.get("dias"), min((hasta - desde).days + 1, 30)))
    valores = {k: n(f.get(k)) for k in ("horas_extra", "bonificaciones", "otras_deducciones")}
    marcado = request.method == "POST" or "_c" in f
    ss = bool(f.get("seguridad_social")) if marcado else True
    pr = bool(f.get("prestaciones")) if marcado else True
    medios = MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all()
    if request.method == "POST":
        medio = db.session.get(MedioPago, int(f.get("medio_id") or 0))
        try:
            pago = nom.registrar(emp, cfg, desde, hasta, _fecha(f.get("fecha_pago"), hoy), dias,
                                 deducciones_detalle=f.get("deducciones_detalle"), seguridad_social=ss,
                                 prestaciones=pr, medio=medio, desde_caja=bool(f.get("desde_caja")),
                                 nota=f.get("nota"), usuario_id=current_user.id, **valores)
            db.session.commit()
            flash("Nómina registrada.", "ok")
            return redirect(url_for("nomina_detalle", nid=pago.id))
        except ErrorNegocio as e:
            db.session.rollback()
            errores_flash(e)
    calc = None
    if emp:
        calc = nom.calcular(emp, cfg, dias, nom.ventas_del_periodo(emp, desde, hasta), seguridad_social=ss,
                            prestaciones=pr, **valores)
    return render_template("nomina_form.html", empleados=Empleado.query.filter_by(activo=True).order_by(Empleado.nombre).all(),
                           emp=emp, desde=desde, hasta=hasta, dias=dias, valores=valores, calc=calc, ss=ss, pr=pr,
                           medios=medios, turno=turno_abierto(), hoy=hoy, f=f)


@app.route("/nomina/<int:nid>")
@requiere("admin")
def nomina_detalle(nid):
    return render_template("nomina_detalle.html", p=get_or_404(PagoNomina, nid))


@app.route("/nomina/<int:nid>/anular", methods=["POST"])
@requiere("admin")
def nomina_anular(nid):
    p = get_or_404(PagoNomina, nid)
    try:
        nom.anular(p, current_user.id)
        db.session.commit()
        flash("Nómina anulada.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("nomina_detalle", nid=nid))


# ================================================================ gastos
@app.route("/gastos", methods=["GET", "POST"])
@requiere("admin")
def gastos():
    medios = MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all()
    if request.method == "POST":
        f = request.form
        medio = db.session.get(MedioPago, int(f.get("medio_id") or 0))
        try:
            valor = round(n(f.get("valor")))
            desc = (f.get("descripcion") or "").strip()
            if valor <= 0:
                raise ErrorNegocio("Escribe el valor del gasto.")
            if len(desc) < 3:
                raise ErrorNegocio("Escribe la descripción.")
            if f.get("categoria") not in CATEGORIAS_GASTO:
                raise ErrorNegocio("Elige la categoría.")
            g = Gasto(fecha=fecha_form(f.get("fecha")), categoria=f["categoria"], descripcion=desc[:200], valor=valor,
                      medio=medio, usuario_id=current_user.id)
            db.session.add(g)
            db.session.flush()
            if f.get("desde_caja") and medio:
                g.mov_caja_id = mov_caja("egreso", valor, medio, f"Gasto: {desc[:150]}", "gasto", g.id, current_user.id).id
            db.session.commit()
            flash("Gasto registrado.", "ok")
            return redirect(url_for("gastos"))
        except ErrorNegocio as e:
            db.session.rollback()
            errores_flash(e)
    desde, hasta = periodo()
    ini, fin = rango_dt(desde, hasta)
    lista = Gasto.query.filter(Gasto.fecha >= ini, Gasto.fecha < fin).order_by(Gasto.fecha.desc()).all()
    return render_template("gastos.html", gastos=lista, categorias=CATEGORIAS_GASTO, medios=medios, desde=desde,
                           hasta=hasta, total=sum(g.valor for g in lista if not g.anulado), turno=turno_abierto(),
                           hoy=ahora().date())


@app.route("/gastos/<int:gid>/anular", methods=["POST"])
@requiere("admin")
def gasto_anular(gid):
    g = get_or_404(Gasto, gid)
    if not g.anulado:
        try:
            anular_mov_caja(g.mov_caja_id, current_user.id, f"Reverso gasto: {g.descripcion[:150]}")
            g.anulado = True
            db.session.commit()
            flash("Gasto anulado.", "ok")
        except ErrorNegocio as e:
            db.session.rollback()
            errores_flash(e)
    return redirect(url_for("gastos"))


# ============================================================== reportes
@app.route("/reportes")
@requiere("admin")
def reportes():
    desde, hasta = periodo()
    r = rpt.resumen(desde, hasta)
    prods = Producto.query.filter_by(activo=True).all()
    por_cat = defaultdict(float)
    for p in prods:
        por_cat[p.categoria.nombre if p.categoria else "Sin categoría"] += p.valor_inventario
    ini, fin = rango_dt(desde, hasta)
    por_vendedor = defaultdict(lambda: [0, 0.0, 0.0])
    for v in r["ventas"]:
        vend = v.vendedor or v.usuario
        k = vend.nombre if vend else "—"
        por_vendedor[k][0] += 1
        por_vendedor[k][1] += v.neto
        por_vendedor[k][2] += v.ganancia
    serie = [(d.strftime("%d/%m"), round(v), round(u)) for d, v, u in r["serie"]]
    return render_template("reportes.html", serie=serie, r=r, desde=desde, hasta=hasta, top=rpt.top_productos(desde, hasta, 20),
                           quietos=rpt.sin_movimiento(), valor_inv=sum(p.valor_inventario for p in prods),
                           por_cat=sorted(por_cat.items(), key=lambda x: -x[1]),
                           por_vendedor=sorted(por_vendedor.items(), key=lambda x: -x[1][1]),
                           tienda=sum(v.neto for v in r["ventas"] if v.origen == "tienda"),
                           preventa=sum(v.neto for v in r["ventas"] if v.origen == "preventa"))


@app.route("/reportes/excel")
@requiere("admin")
def reportes_excel():
    desde, hasta = periodo()
    return send_file(rpt.excel(desde, hasta, config()), as_attachment=True,
                     download_name=f"reporte_{desde:%Y%m%d}_{hasta:%Y%m%d}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ============================================================== usuarios
@app.route("/usuarios")
@requiere("admin")
def usuarios():
    return render_template("usuarios.html", usuarios=Usuario.query.order_by(Usuario.activo.desc(), Usuario.nombre).all())


@app.route("/usuarios/nuevo", methods=["GET", "POST"])
@app.route("/usuarios/<int:uid>/editar", methods=["GET", "POST"])
@requiere("admin")
def usuario_form(uid=None):
    u = get_or_404(Usuario, uid) if uid else Usuario(rol="cajero", activo=True)
    if request.method == "POST":
        f = request.form
        u.nombre = (f.get("nombre") or "").strip()[:120]
        u.usuario = (f.get("usuario") or "").strip().lower()[:60]
        rol = f.get("rol") if f.get("rol") in ROLES else "cajero"
        activo = bool(f.get("activo")) if uid else True
        clave = f.get("clave") or ""
        errores = []
        if len(u.nombre) < 2 or len(u.usuario) < 3:
            errores.append("Escribe el nombre y un usuario de al menos 3 letras.")
        with db.session.no_autoflush:
            duplicado = Usuario.query.filter(Usuario.usuario == u.usuario, Usuario.id != (u.id or 0)).first()
        if duplicado:
            errores.append("Ese usuario ya existe.")
        if not uid and len(clave) < 6:
            errores.append("La clave debe tener al menos 6 caracteres.")
        if uid and clave and len(clave) < 6:
            errores.append("La clave debe tener al menos 6 caracteres.")
        if u.id == current_user.id and (rol != "admin" or not activo):
            errores.append("No puedes quitarte a ti mismo el rol de administrador ni desactivarte.")
        if errores:
            for m in errores:
                flash(m, "err")
            db.session.rollback()
        else:
            u.rol, u.activo = rol, activo
            if clave:
                u.set_password(clave)
            db.session.add(u)
            db.session.commit()
            flash("Usuario guardado.", "ok")
            return redirect(url_for("usuarios"))
    return render_template("usuario_form.html", u=u)


# ========================================================= configuración
@app.route("/configuracion", methods=["GET", "POST"])
@requiere("admin")
def configuracion():
    cfg = config()
    if request.method == "POST":
        f = request.form
        sec = f.get("seccion")
        if sec == "negocio":
            cfg.nombre = (f.get("nombre") or "").strip()[:120] or "Moto Speed"
            for campo, largo in (("eslogan", 160), ("nit", 30), ("direccion", 200), ("ciudad", 80), ("telefono", 40),
                                 ("email", 120), ("mensaje_recibo", 300)):
                setattr(cfg, campo, (f.get(campo) or "").strip()[:largo] or None)
            cfg.whatsapp = "".join(ch for ch in (f.get("whatsapp") or "") if ch.isdigit())[:15] or None
            cfg.ancho_ticket = 58 if f.get("ancho_ticket") == "58" else 80
            coords = leer_coordenadas(f.get("ubicacion"))
            if coords:
                cfg.lat, cfg.lng = coords
            elif not (f.get("ubicacion") or "").strip():
                cfg.lat = cfg.lng = None
            logo = request.files.get("logo")
            if logo and logo.filename:
                try:
                    cfg.logo = procesar_imagen(logo, 500)
                    cfg.logo_mime = "image/jpeg"
                    cfg.logo_v = (cfg.logo_v or 0) + 1
                except ErrorNegocio as e:
                    errores_flash(e)
            if f.get("quitar_logo"):
                cfg.logo = None
                cfg.logo_v = (cfg.logo_v or 0) + 1
        elif sec == "ventas":
            cfg.cobra_iva = bool(f.get("cobra_iva"))
            cfg.iva_defecto = pct(f.get("iva_defecto"), 19)
            cfg.permitir_sin_stock = bool(f.get("permitir_sin_stock"))
            cfg.margen_detal = pct(f.get("margen_detal"), 35)
            cfg.margen_mayor = pct(f.get("margen_mayor"), 20)
            cfg.margen_taller = pct(f.get("margen_taller"), 25)
        elif sec == "tienda":
            cfg.tienda_activa = bool(f.get("tienda_activa"))
            cfg.tienda_muestra_agotados = bool(f.get("tienda_muestra_agotados"))
            cfg.tienda_mensaje = (f.get("tienda_mensaje") or "").strip()[:300] or None
            cfg.tienda_envio = (f.get("tienda_envio") or "").strip()[:300] or None
            cfg.reserva_horas = max(int(n(f.get("reserva_horas"), 48)), 1)
        elif sec == "nomina":
            cfg.smmlv = n(f.get("smmlv"), cfg.smmlv)
            cfg.aux_transporte = n(f.get("aux_transporte"), cfg.aux_transporte)
            cfg.nom_arl_pct = pct(f.get("nom_arl_pct"), cfg.nom_arl_pct)
            cfg.nom_salud_empleador = bool(f.get("nom_salud_empleador"))
        elif sec == "medio":
            mid = f.get("id")
            m = db.session.get(MedioPago, int(mid)) if (mid or "").isdigit() else MedioPago()
            nombre = (f.get("nombre") or "").strip()[:60]
            if not nombre:
                flash("Escribe el nombre del medio de pago.", "err")
                return redirect(url_for("configuracion") + "#medios")
            m.nombre = nombre
            if not m.es_efectivo or m.id is None:
                m.tipo = f.get("tipo") if f.get("tipo") in TIPOS_MEDIO and f.get("tipo") != "efectivo" else "transferencia"
            m.detalle = (f.get("detalle") or "").strip()[:160] or None
            m.orden = int(n(f.get("orden"), 10))
            m.activo = True if m.es_efectivo else bool(f.get("activo"))
            db.session.add(m)
            db.session.commit()
            flash("Medio de pago guardado.", "ok")
            return redirect(url_for("configuracion") + "#medios")
        db.session.commit()
        flash("Configuración guardada.", "ok")
        return redirect(url_for("configuracion") + "#" + (sec or ""))
    return render_template("configuracion.html", medios=MedioPago.query.order_by(MedioPago.orden, MedioPago.id).all(),
                           categorias=Categoria.query.order_by(Categoria.nombre).all(),
                           conteo_cat={c: n_ for c, n_ in db.session.query(Producto.categoria_id, db.func.count(Producto.id))
                                       .group_by(Producto.categoria_id).all()})


# ============================================================== preventa
def leer_coordenadas(texto):
    """Saca lat,lng de «7.89, -72.50», de un enlace largo de Google Maps (@lat,lng o q=lat,lng)."""
    import re
    t = (texto or "").strip()
    if not t:
        return None
    for patron in (r"@(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)", r"[?&](?:q|query|ll|destination)=(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)",
                   r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)", r"^(-?\d{1,2}\.\d+)\s*[,;]\s*(-?\d{1,3}\.\d+)$"):
        m = re.search(patron, t)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return lat, lng
    return None


def maps_lugar(c):
    return "https://www.google.com/maps/search/?api=1&query=" + quote(c.destino_maps) if c.destino_maps else ""


def maps_llegar(c):
    return ("https://www.google.com/maps/dir/?api=1&travelmode=driving&destination=" + quote(c.destino_maps)
            if c.destino_maps else "")


def origen_local():
    cfg = config()
    if cfg.lat is not None and cfg.lng is not None:
        return f"{cfg.lat:.6f},{cfg.lng:.6f}"
    return ", ".join(x for x in (cfg.direccion, cfg.ciudad) if x)


def maps_tramos(clientes, desde_local=True):
    """Google Maps admite hasta 9 paradas intermedias por enlace: se parte la ruta en tramos."""
    paradas = [c.destino_maps for c in clientes if c.destino_maps]
    if not paradas:
        return []
    tramos, origen = [], (origen_local() if desde_local else "")
    for i in range(0, len(paradas), 10):
        grupo = paradas[i:i + 10]
        url = "https://www.google.com/maps/dir/?api=1&travelmode=driving"
        if origen:
            url += "&origin=" + quote(origen)
        url += "&destination=" + quote(grupo[-1])
        if len(grupo) > 1:
            url += "&waypoints=" + quote("|".join(grupo[:-1]))
        tramos.append({"url": url, "desde": i + 1, "hasta": i + len(grupo)})
        origen = grupo[-1]
    return tramos


def distancia(a, b):
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 12742 * asin(sqrt(h))


def ordenar_por_cercania(clientes):
    """Vecino más cercano desde el local: sencillo y suficiente para una ruta de barrio."""
    cfg = config()
    con = [c for c in clientes if c.tiene_ubicacion]
    sin = [c for c in clientes if not c.tiene_ubicacion]
    actual = (cfg.lat, cfg.lng) if cfg.lat is not None else ((con[0].lat, con[0].lng) if con else None)
    orden = []
    while con:
        sig = min(con, key=lambda c: distancia(actual, (c.lat, c.lng)))
        orden.append(sig)
        con.remove(sig)
        actual = (sig.lat, sig.lng)
    return orden + sin


def ruta_json(clientes):
    return [{"id": c.id, "n": i + 1, "nombre": c.nombre, "lat": c.lat, "lng": c.lng} for i, c in enumerate(clientes)
            if c.tiene_ubicacion]


def rutas_de(usuario):
    q = Ruta.query.filter_by(activa=True)
    if not usuario.puede("admin"):
        q = q.filter(Ruta.usuario_id == usuario.id)
    return q.order_by(Ruta.dia.is_(None), Ruta.dia, Ruta.nombre).all()


@app.route("/preventa")
@requiere("preventista")
def mi_ruta():
    rutas = rutas_de(current_user)
    hoy = ahora()
    ruta = None
    if request.args.get("ruta", type=int):
        ruta = next((r for r in rutas if r.id == request.args.get("ruta", type=int)), None)
    if ruta is None:
        ruta = next((r for r in rutas if r.dia == hoy.weekday()), None) or (rutas[0] if rutas else None)
    clientes = ruta.clientes_orden() if ruta else []
    ini = datetime.combine(hoy.date(), datetime.min.time())
    visitas_hoy = {}
    for v in Visita.query.filter(Visita.fecha >= ini, Visita.usuario_id == current_user.id).order_by(Visita.fecha).all():
        visitas_hoy[v.cliente_id] = v
    saldos = defaultdict(float)
    for v in Venta.query.filter(Venta.estado == "credito", Venta.cliente_id.in_([c.id for c in clientes] or [0])).all():
        saldos[v.cliente_id] += v.saldo
    ultima = {}
    for cid, f in (db.session.query(Venta.cliente_id, db.func.max(Venta.fecha))
                   .filter(Venta.estado != "anulada", Venta.cliente_id.in_([c.id for c in clientes] or [0]))
                   .group_by(Venta.cliente_id).all()):
        ultima[cid] = (hoy - f).days
    pendientes = [c for c in clientes if c.id not in visitas_hoy]
    return render_template("preventa_ruta.html", rutas=rutas, ruta=ruta, clientes=clientes, visitas=visitas_hoy,
                           saldos=saldos, ultima=ultima, tramos=maps_tramos(pendientes), tramos_todo=maps_tramos(clientes),
                           lugar=maps_lugar, llegar=maps_llegar, wa=wa_link, resultados=RESULTADOS_VISITA,
                           puntos=ruta_json(clientes), hoy=hoy, DIAS=DIAS,
                           pedidos_hoy=Pedido.query.filter(Pedido.vendedor_id == current_user.id, Pedido.fecha >= ini)
                           .filter(Pedido.estado != "cancelado").all())


@app.route("/preventa/visita", methods=["POST"])
@requiere("preventista")
def preventa_visita():
    c = get_or_404(Cliente, int(request.form.get("cliente_id") or 0))
    try:
        registrar_visita(current_user, c, request.form.get("resultado"), request.form.get("nota"),
                         pct(request.form.get("lat"), None), pct(request.form.get("lng"), None))
        db.session.commit()
        flash(f"Visita a {c.nombre} registrada.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        errores_flash(e)
    return redirect(url_for("mi_ruta", ruta=request.form.get("ruta_id")) + f"#c{c.id}")


@app.route("/preventa/cliente/<int:cid>/ubicacion", methods=["POST"])
@requiere("preventista")
def preventa_ubicacion(cid):
    c = get_or_404(Cliente, cid)
    d = request.get_json(silent=True) or {}
    try:
        lat, lng = float(d.get("lat")), float(d.get("lng"))
    except (TypeError, ValueError):
        return jsonify(ok=False, errores=["No llegó la ubicación."]), 400
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify(ok=False, errores=["Ubicación inválida."]), 400
    c.lat, c.lng = lat, lng
    db.session.commit()
    return jsonify(ok=True)


@app.route("/preventa/cliente/<int:cid>/pedido")
@requiere("preventista")
def preventa_pedido_form(cid):
    c = get_or_404(Cliente, cid)
    ruta_id = request.args.get("ruta", type=int)
    return render_template("preventa_pedido.html", c=c, ruta_id=ruta_id, saldo=saldo_cliente(c))


@app.route("/preventa/productos")
@requiere("preventista")
def preventa_productos():
    lista = request.args.get("lista") if request.args.get("lista") in LISTAS_PRECIO else "detal"
    reservado = reservado_por_producto()
    out = []
    for p in buscar_productos(request.args.get("q"), 30):
        d = producto_json(p, reservado)
        d["precio"] = p.precio(lista)
        d["disp"] = max((p.stock or 0) - reservado.get(p.id, 0), 0)
        out.append(d)
    return jsonify(out)


@app.route("/preventa/pedido", methods=["POST"])
@requiere("preventista")
def preventa_pedido():
    d = request.get_json(silent=True) or {}
    c = db.session.get(Cliente, int(d.get("cliente_id") or 0))
    try:
        ped = crear_pedido_preventa(current_user, c, d.get("items"), d.get("nota"), d.get("lat"), d.get("lng"))
        db.session.commit()
    except ErrorNegocio as e:
        db.session.rollback()
        return jsonify(ok=False, errores=e.errores), 400
    return jsonify(ok=True, folio=ped.folio, total=ped.total,
                   url=url_for("mi_ruta", ruta=d.get("ruta_id")) + f"#c{c.id}",
                   wa=wa_link(c.telefono, f"Hola {c.nombre}, te saluda {current_user.nombre} de {config().nombre}. "
                                          f"Tu pedido {ped.folio} por {pesos(ped.total)} quedó registrado. ¡Gracias!")
                   if c.telefono else "")


@app.route("/preventa/mis-pedidos")
@requiere("preventista")
def preventa_mis_pedidos():
    desde, hasta = periodo()
    ini, fin = rango_dt(desde, hasta)
    q = Pedido.query.filter(Pedido.origen == "preventa", Pedido.fecha >= ini, Pedido.fecha < fin)
    if not current_user.puede("admin"):
        q = q.filter(Pedido.vendedor_id == current_user.id)
    lista = q.order_by(Pedido.fecha.desc()).all()
    return render_template("preventa_mis_pedidos.html", pedidos=lista, desde=desde, hasta=hasta,
                           total=sum(p.total for p in lista if p.estado != "cancelado"),
                           confirmado=sum(p.total for p in lista if p.estado == "confirmado"))


@app.route("/preventa/cliente-nuevo", methods=["GET", "POST"])
@requiere("preventista")
def preventa_cliente_nuevo():
    rutas = rutas_de(current_user)
    if request.method == "POST":
        f = request.form
        nombre = (f.get("nombre") or "").strip()
        doc = (f.get("documento") or "").strip()[:30] or None
        if len(nombre) < 2:
            flash("Escribe el nombre del local o del cliente.", "err")
        elif doc and Cliente.query.filter_by(documento=doc).first():
            flash("Ya existe un cliente con ese documento.", "err")
        else:
            ruta_id = int(f.get("ruta_id")) if (f.get("ruta_id") or "").isdigit() else None
            if ruta_id and ruta_id not in [r.id for r in rutas]:
                ruta_id = None
            c = Cliente(nombre=nombre[:140], documento=doc, telefono=(f.get("telefono") or "").strip()[:40] or None,
                        direccion=(f.get("direccion") or "").strip()[:200] or None,
                        barrio=(f.get("barrio") or "").strip()[:80] or None,
                        lista=f.get("lista") if f.get("lista") in LISTAS_PRECIO else "taller",
                        notas=(f.get("notas") or "").strip()[:300] or None, ruta_id=ruta_id,
                        orden_ruta=(db.session.query(db.func.max(Cliente.orden_ruta)).filter(Cliente.ruta_id == ruta_id)
                                    .scalar() or 0) + 1 if ruta_id else 0)
            coords = leer_coordenadas(f.get("ubicacion"))
            if coords:
                c.lat, c.lng = coords
            db.session.add(c)
            db.session.commit()
            flash(f"{c.nombre} quedó registrado{' en la ruta' if ruta_id else ''}.", "ok")
            return redirect(url_for("mi_ruta", ruta=ruta_id) if ruta_id else url_for("mi_ruta"))
    return render_template("preventa_cliente.html", rutas=rutas)


# ------------------------------------------------------------ rutas (admin)
@app.route("/rutas")
@requiere("admin")
def rutas():
    lista = Ruta.query.order_by(Ruta.activa.desc(), Ruta.dia.is_(None), Ruta.dia, Ruta.nombre).all()
    return render_template("rutas.html", rutas=lista, sin_ruta=Cliente.query.filter_by(activo=True, ruta_id=None).count())


@app.route("/rutas/nueva", methods=["GET", "POST"])
@app.route("/rutas/<int:rid>/editar", methods=["GET", "POST"])
@requiere("admin")
def ruta_form(rid=None):
    r = get_or_404(Ruta, rid) if rid else Ruta(activa=True)
    if request.method == "POST":
        f = request.form
        r.nombre = (f.get("nombre") or "").strip()[:80]
        r.dia = int(f.get("dia")) if (f.get("dia") or "").isdigit() and int(f.get("dia")) < 7 else None
        r.usuario_id = int(f.get("usuario_id")) if (f.get("usuario_id") or "").isdigit() else None
        r.notas = (f.get("notas") or "").strip()[:300] or None
        r.activa = bool(f.get("activa")) if rid else True
        if len(r.nombre) < 2:
            flash("Escribe el nombre de la ruta (ej. Ruta Norte).", "err")
        else:
            db.session.add(r)
            db.session.commit()
            flash("Ruta guardada.", "ok")
            return redirect(url_for("ruta_detalle", rid=r.id))
    return render_template("ruta_form.html", r=r, DIAS=DIAS,
                           preventistas=Usuario.query.filter(Usuario.activo.is_(True),
                                                             Usuario.rol.in_(["preventista", "admin"])).all())


@app.route("/rutas/<int:rid>")
@requiere("admin")
def ruta_detalle(rid):
    r = get_or_404(Ruta, rid)
    clientes = r.clientes_orden()
    hoy = ahora()
    hace30 = hoy - timedelta(days=30)
    ventas30 = defaultdict(float)
    for v in Venta.query.filter(Venta.fecha >= hace30, Venta.estado != "anulada",
                                Venta.cliente_id.in_([c.id for c in clientes] or [0])).all():
        ventas30[v.cliente_id] += v.neto
    disponibles = Cliente.query.filter(Cliente.activo.is_(True), or_(Cliente.ruta_id.is_(None), Cliente.ruta_id != r.id)) \
        .order_by(Cliente.nombre).all()
    return render_template("ruta_detalle.html", r=r, clientes=clientes, ventas30=ventas30, disponibles=disponibles,
                           tramos=maps_tramos(clientes), lugar=maps_lugar, puntos=ruta_json(clientes),
                           sin_ubicacion=sum(1 for c in clientes if not c.destino_maps))


@app.route("/rutas/<int:rid>/clientes", methods=["POST"])
@requiere("admin")
def ruta_clientes(rid):
    r = get_or_404(Ruta, rid)
    accion = request.form.get("accion")
    clientes = r.clientes_orden()
    if accion == "agregar":
        ids = [int(x) for x in request.form.getlist("cliente_id") if x.isdigit()]
        n_ = len(clientes)
        for c in Cliente.query.filter(Cliente.id.in_(ids or [0])).all():
            n_ += 1
            c.ruta_id, c.orden_ruta = r.id, n_
        flash(f"{len(ids)} cliente(s) agregados a la ruta.", "ok")
    elif accion in ("subir", "bajar", "quitar"):
        cid = int(request.form.get("cid") or 0)
        idx = next((i for i, c in enumerate(clientes) if c.id == cid), None)
        if idx is not None:
            if accion == "quitar":
                clientes[idx].ruta_id = None
                clientes.pop(idx)
            else:
                j = idx - 1 if accion == "subir" else idx + 1
                if 0 <= j < len(clientes):
                    clientes[idx], clientes[j] = clientes[j], clientes[idx]
        for i, c in enumerate(clientes, 1):
            c.orden_ruta = i
    elif accion == "cercania":
        for i, c in enumerate(ordenar_por_cercania(clientes), 1):
            c.orden_ruta = i
        flash("Ruta ordenada por cercanía desde el local. Los clientes sin ubicación quedaron al final.", "ok")
    db.session.commit()
    return redirect(url_for("ruta_detalle", rid=rid) + "#paradas")


@app.route("/preventa/reporte")
@requiere("admin")
def preventa_reporte():
    desde, hasta = periodo()
    ini, fin = rango_dt(desde, hasta)
    visitas = Visita.query.filter(Visita.fecha >= ini, Visita.fecha < fin).all()
    pedidos = Pedido.query.filter(Pedido.origen == "preventa", Pedido.fecha >= ini, Pedido.fecha < fin).all()
    ventas = Venta.query.filter(Venta.origen == "preventa", Venta.estado != "anulada", Venta.fecha >= ini,
                                Venta.fecha < fin).all()
    por = defaultdict(lambda: {"visitas": 0, "con_pedido": 0, "pedidos": 0, "valor_pedidos": 0.0, "ventas": 0.0,
                               "utilidad": 0.0, "motivos": defaultdict(int)})
    for v in visitas:
        d = por[v.usuario.nombre if v.usuario else "—"]
        d["visitas"] += 1
        d["con_pedido"] += v.resultado == "pedido"
        d["motivos"][v.resultado] += 1
    for p in pedidos:
        if p.estado != "cancelado":
            d = por[p.vendedor.nombre if p.vendedor else "—"]
            d["pedidos"] += 1
            d["valor_pedidos"] += p.total
    for v in ventas:
        d = por[v.vendedor.nombre if v.vendedor else "—"]
        d["ventas"] += v.neto
        d["utilidad"] += v.ganancia
    por_ruta = defaultdict(lambda: {"visitas": 0, "pedidos": 0, "valor": 0.0})
    for v in visitas:
        k = v.ruta.nombre if v.ruta else "Sin ruta"
        por_ruta[k]["visitas"] += 1
        por_ruta[k]["pedidos"] += v.resultado == "pedido"
    for p in pedidos:
        if p.estado != "cancelado":
            k = p.cliente.ruta.nombre if p.cliente and p.cliente.ruta else "Sin ruta"
            por_ruta[k]["valor"] += p.total
    # clientes de ruta que no compran hace más de 30 días
    hoy = ahora()
    en_ruta = Cliente.query.filter(Cliente.activo.is_(True), Cliente.ruta_id.isnot(None)).all()
    ultima = dict(db.session.query(Venta.cliente_id, db.func.max(Venta.fecha)).filter(Venta.estado != "anulada")
                  .group_by(Venta.cliente_id).all())
    dormidos = sorted([(c, (hoy - ultima[c.id]).days if c.id in ultima else None) for c in en_ruta
                       if c.id not in ultima or (hoy - ultima[c.id]).days > 30],
                      key=lambda x: -(x[1] if x[1] is not None else 9999))[:40]
    return render_template("preventa_reporte.html", desde=desde, hasta=hasta, por=sorted(por.items()),
                           por_ruta=sorted(por_ruta.items()), dormidos=dormidos, resultados=RESULTADOS_VISITA,
                           total_visitas=len(visitas), total_pedidos=sum(1 for p in pedidos if p.estado != "cancelado"),
                           total_ventas=sum(v.neto for v in ventas))


# ========================================================= tienda pública
def precio_tienda(p):
    return p.precio_detal


@app.route("/")
def raiz():
    cfg = config()
    if cfg.tienda_activa:
        return tienda()
    return redirect(url_for("login"))


@app.route("/tienda")
def tienda():
    cfg = config()
    if not cfg.tienda_activa:
        return render_template("tienda_cerrada.html"), 200
    q = (request.args.get("q") or "").strip()
    cat = request.args.get("cat", type=int)
    pagina = max(request.args.get("p", 1, type=int), 1)
    query = Producto.query.filter(Producto.activo.is_(True), Producto.en_tienda.is_(True), Producto.precio_detal > 0)
    if not cfg.tienda_muestra_agotados:
        query = query.filter(Producto.stock > 0)
    if cat:
        query = query.filter(Producto.categoria_id == cat)
    if q:
        for palabra in normalizar(q).split()[:6]:
            query = query.filter(Producto.busqueda.like(f"%{palabra}%"))
    orden = request.args.get("orden", "")
    if orden == "menor":
        query = query.order_by(Producto.precio_detal)
    elif orden == "mayor":
        query = query.order_by(Producto.precio_detal.desc())
    else:
        query = query.order_by(Producto.destacado.desc(), (Producto.stock > 0).desc(), Producto.imagen_v.desc(), Producto.nombre)
    total = query.count()
    por_pagina = 24
    prods = query.offset((pagina - 1) * por_pagina).limit(por_pagina).all()
    reservado = reservado_por_producto()
    cats = (db.session.query(Categoria, db.func.count(Producto.id)).join(Producto, Producto.categoria_id == Categoria.id)
            .filter(Producto.activo.is_(True), Producto.en_tienda.is_(True)).group_by(Categoria.id)
            .order_by(Categoria.nombre).all())
    return render_template("tienda.html", prods=prods, reservado=reservado, q=q, cat=cat, cats=cats, total=total,
                           pagina=pagina, paginas=max((total + por_pagina - 1) // por_pagina, 1), orden=orden,
                           medios=MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all())


@app.route("/tienda/p/<int:pid>")
def tienda_producto(pid):
    cfg = config()
    p = db.session.get(Producto, pid)
    if not cfg.tienda_activa or p is None or not p.activo or not p.en_tienda:
        abort(404)
    reservado = reservado_por_producto()
    disp = max((p.stock or 0) - reservado.get(p.id, 0), 0)
    relacionados = (Producto.query.filter(Producto.activo.is_(True), Producto.en_tienda.is_(True), Producto.id != p.id,
                                          Producto.categoria_id == p.categoria_id, Producto.stock > 0)
                    .order_by(Producto.imagen_v.desc()).limit(8).all()) if p.categoria_id else []
    return render_template("tienda_producto.html", p=p, disp=disp, relacionados=relacionados, reservado=reservado,
                           medios=MedioPago.query.filter_by(activo=True).order_by(MedioPago.orden).all())


@app.route("/tienda/disponible", methods=["POST"])
def tienda_disponible():
    """El carrito consulta precio y disponibilidad actuales antes de pedir."""
    d = request.get_json(silent=True) or {}
    ids = [int(x) for x in (d.get("ids") or [])[:60] if str(x).isdigit()]
    reservado = reservado_por_producto()
    out = {}
    for p in Producto.query.filter(Producto.id.in_(ids)).all() if ids else []:
        ok = p.activo and p.en_tienda and p.precio_detal > 0
        out[p.id] = {"nombre": p.nombre, "codigo": p.codigo, "precio": p.precio_detal if ok else 0,
                     "disp": max((p.stock or 0) - reservado.get(p.id, 0), 0) if ok else 0,
                     "img": url_for("img_producto", pid=p.id, v=p.imagen_v) if p.imagen_v else ""}
    return jsonify(out)


@app.route("/tienda/pedido", methods=["POST"])
def tienda_pedido():
    cfg = config()
    if not cfg.tienda_activa:
        return jsonify(ok=False, errores=["La tienda está cerrada en este momento."]), 400
    if not limitar("pedido:" + ip(), 6, 3600):
        return jsonify(ok=False, errores=["Hiciste muchos pedidos seguidos. Escríbenos por WhatsApp."]), 429
    d = request.get_json(silent=True) or {}
    if d.get("web"):  # campo trampa para bots
        return jsonify(ok=True, url=url_for("tienda"))
    try:
        ped = crear_pedido(d, d.get("items"))
        db.session.commit()
    except ErrorNegocio as e:
        db.session.rollback()
        return jsonify(ok=False, errores=e.errores), 400
    return jsonify(ok=True, url=url_for("tienda_estado_pedido", token=ped.token))


@app.route("/tienda/pedido/<token>")
def tienda_estado_pedido(token):
    cfg = config()
    p = Pedido.query.filter_by(token=token).first_or_404()
    lineas = [f"Hola {cfg.nombre}, acabo de hacer el pedido *{p.folio}* en la tienda online:", ""]
    lineas += [f"{cant(i.cantidad)} × {i.nombre} ({i.codigo})" for i in p.items]
    lineas += ["", f"*Total: {pesos(p.total)}*", f"Entrega: {dict(recoger='Recojo en el local', domicilio='Domicilio', envio='Envío nacional')[p.entrega]}"]
    if p.direccion:
        lineas.append(f"Dirección: {p.direccion}{', ' + p.ciudad if p.ciudad else ''}")
    lineas.append(f"Nombre: {p.nombre}")
    return render_template("tienda_pedido.html", p=p, vencido=pedido_vencido(p),
                           wa=wa_link(cfg.whatsapp, "\n".join(lineas)) if cfg.whatsapp else "",
                           medios=MedioPago.query.filter(MedioPago.activo.is_(True), MedioPago.tipo != "efectivo").order_by(MedioPago.orden).all())


@app.route("/sw.js")
def service_worker():
    r = send_file(os.path.join(app.static_folder, "sw.js"), mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Service-Worker-Allowed"] = "/"
    return r


@app.route("/manifest.webmanifest")
def manifest():
    r = send_file(os.path.join(app.static_folder, "manifest.webmanifest"), mimetype="application/manifest+json")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /panel\nDisallow: /pos\nDisallow: /r/\nAllow: /\n", mimetype="text/plain")


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
