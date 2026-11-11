"""Modelos de Moto Speed: repuestos de moto, POS, caja, inventario con costo promedio,
cartera, tienda online, nómina, gastos.

Todas las tablas usan el prefijo ts_ para poder compartir una base de Supabase con otras apps.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.orm import deferred
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()
TZ = ZoneInfo("America/Bogota")


def normalizar(texto):
    """minúsculas y sin tildes: «Bujía» → «bujia»."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def ahora():
    """Hora de Colombia sin zona (así se guarda en la base)."""
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


ROLES = {
    "admin": "Administrador",
    "cajero": "Cajero / vendedor",
    "bodega": "Bodega",
    "preventista": "Preventista (ventas en ruta)",
}
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
RESULTADOS_VISITA = {
    "pedido": "Hizo pedido",
    "no_compro": "No compró",
    "tiene_stock": "Tiene surtido",
    "sin_dinero": "Sin dinero / después",
    "cerrado": "Local cerrado",
    "volver": "Volver otro día",
}
TIPOS_MEDIO = {"efectivo": "Efectivo", "transferencia": "Transferencia", "tarjeta": "Tarjeta / datáfono", "otro": "Otro"}
LISTAS_PRECIO = {"detal": "Detal", "mayor": "Por mayor", "taller": "Mecánico / taller"}
MOTIVOS_AJUSTE = {
    "inicial": ("Inventario inicial", "entrada"),
    "conteo_sobra": ("Conteo físico: sobrante", "entrada"),
    "conteo_falta": ("Conteo físico: faltante", "salida"),
    "danio": ("Repuesto dañado", "salida"),
    "garantia": ("Enviado a garantía al proveedor", "salida"),
    "garantia_vuelve": ("Regresa de garantía", "entrada"),
    "uso_interno": ("Uso interno / taller propio", "salida"),
    "perdida": ("Pérdida o robo", "salida"),
    "otro_entrada": ("Otra entrada", "entrada"),
    "otro_salida": ("Otra salida", "salida"),
}
TIPOS_MOV = {
    "inicial": "Inventario inicial",
    "compra": "Compra",
    "anula_compra": "Anulación de compra",
    "venta": "Venta",
    "anula_venta": "Anulación de venta",
    "devolucion": "Devolución de cliente",
    "ajuste_entrada": "Ajuste (entrada)",
    "ajuste_salida": "Ajuste (salida)",
    "anula_ajuste": "Anulación de ajuste",
}
CATEGORIAS_GASTO = ["Arriendo", "Servicios públicos", "Internet y teléfono", "Transporte y domicilios",
                    "Publicidad", "Mantenimiento del local", "Papelería y empaques", "Impuestos",
                    "Contador", "Comisiones bancarias", "Otros"]
TIPOS_PAGO_EMP = {"fijo": "Sueldo fijo", "comision": "Solo comisión", "mixto": "Sueldo + comisión"}
UNIDADES = ["unidad", "par", "juego", "kit", "litro", "galón", "metro", "caja"]


class Usuario(UserMixin, db.Model):
    __tablename__ = "ts_usuarios"
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(60), unique=True, nullable=False)
    nombre = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="cajero")
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado = db.Column(db.DateTime, default=ahora)
    ultimo_ingreso = db.Column(db.DateTime)

    def set_password(self, clave):
        self.password_hash = generate_password_hash(clave)

    def check_password(self, clave):
        return check_password_hash(self.password_hash, clave)

    @property
    def is_active(self):
        return self.activo

    @property
    def rol_nombre(self):
        return ROLES.get(self.rol, self.rol)

    def puede(self, *roles):
        return self.rol == "admin" or self.rol in roles

    @property
    def es_preventista(self):
        return self.rol == "preventista"

    @property
    def ve_costos(self):
        return self.rol in ("admin", "bodega")


class Config(db.Model):
    """Una sola fila con los datos del negocio y los parámetros."""
    __tablename__ = "ts_config"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False, default="Moto Speed")
    eslogan = db.Column(db.String(160), default="Repuestos para tu moto")
    nit = db.Column(db.String(30))
    direccion = db.Column(db.String(200))
    ciudad = db.Column(db.String(80))
    telefono = db.Column(db.String(40))
    whatsapp = db.Column(db.String(20))
    email = db.Column(db.String(120))
    mensaje_recibo = db.Column(db.String(300), default="¡Gracias por tu compra! Garantía según política del fabricante. Conserva este recibo.")
    logo = deferred(db.Column(db.LargeBinary))
    logo_mime = db.Column(db.String(40))
    logo_v = db.Column(db.Integer, default=0)
    ancho_ticket = db.Column(db.Integer, default=80)  # 58 u 80 mm
    lat = db.Column(db.Float)   # ubicación del local: punto de partida de las rutas
    lng = db.Column(db.Float)
    # impuestos y precios
    cobra_iva = db.Column(db.Boolean, nullable=False, default=False)
    iva_defecto = db.Column(db.Float, nullable=False, default=19.0)
    permitir_sin_stock = db.Column(db.Boolean, nullable=False, default=False)
    margen_detal = db.Column(db.Float, default=35.0)  # sugerencia de precio en la ficha
    margen_mayor = db.Column(db.Float, default=20.0)
    margen_taller = db.Column(db.Float, default=25.0)
    # tienda online
    tienda_activa = db.Column(db.Boolean, nullable=False, default=True)
    tienda_mensaje = db.Column(db.String(300), default="Pide en línea y recoge en el local o te lo enviamos.")
    tienda_envio = db.Column(db.String(300), default="Domicilios en la ciudad con costo según la zona. Envíos nacionales por transportadora (pago contra entrega o anticipado).")
    tienda_muestra_agotados = db.Column(db.Boolean, nullable=False, default=True)
    reserva_horas = db.Column(db.Integer, nullable=False, default=48)
    # nómina (Colombia 2026, editable)
    smmlv = db.Column(db.Float, default=1750905)
    aux_transporte = db.Column(db.Float, default=249095)
    nom_arl_pct = db.Column(db.Float, default=0.522)
    nom_salud_empleador = db.Column(db.Boolean, default=False)
    # consecutivos
    sig_venta = db.Column(db.Integer, nullable=False, default=1)
    sig_compra = db.Column(db.Integer, nullable=False, default=1)
    sig_turno = db.Column(db.Integer, nullable=False, default=1)
    sig_cotizacion = db.Column(db.Integer, nullable=False, default=1)
    sig_pedido = db.Column(db.Integer, nullable=False, default=1)
    sig_ajuste = db.Column(db.Integer, nullable=False, default=1)


class MedioPago(db.Model):
    __tablename__ = "ts_medios_pago"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(60), nullable=False)
    tipo = db.Column(db.String(20), nullable=False, default="transferencia")
    detalle = db.Column(db.String(160))  # ej. número de cuenta o celular, se muestra en la tienda
    activo = db.Column(db.Boolean, nullable=False, default=True)
    orden = db.Column(db.Integer, nullable=False, default=10)

    @property
    def es_efectivo(self):
        return self.tipo == "efectivo"


class Categoria(db.Model):
    __tablename__ = "ts_categorias"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), unique=True, nullable=False)


class Proveedor(db.Model):
    __tablename__ = "ts_proveedores"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(140), nullable=False)
    nit = db.Column(db.String(30))
    telefono = db.Column(db.String(40))
    contacto = db.Column(db.String(120))
    ciudad = db.Column(db.String(80))
    notas = db.Column(db.String(300))
    activo = db.Column(db.Boolean, nullable=False, default=True)


class Producto(db.Model):
    __tablename__ = "ts_productos"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(60), unique=True, nullable=False)
    codigo_barras = db.Column(db.String(60), index=True)
    nombre = db.Column(db.String(200), nullable=False)
    categoria_id = db.Column(db.Integer, db.ForeignKey("ts_categorias.id"))
    categoria = db.relationship("Categoria")
    marca = db.Column(db.String(80))           # marca del repuesto (ej. NGK, Yuasa, original Bajaj)
    referencia = db.Column(db.String(80))      # referencia del fabricante
    compatibilidad = db.Column(db.String(400))  # motos donde sirve
    ubicacion = db.Column(db.String(60))       # estante / cajón
    unidad = db.Column(db.String(20), nullable=False, default="unidad")
    stock = db.Column(db.Float, nullable=False, default=0)
    stock_minimo = db.Column(db.Float, nullable=False, default=0)
    costo_promedio = db.Column(db.Float, nullable=False, default=0)
    ultimo_costo = db.Column(db.Float, nullable=False, default=0)
    precio_detal = db.Column(db.Float, nullable=False, default=0)
    precio_mayor = db.Column(db.Float, nullable=False, default=0)
    precio_taller = db.Column(db.Float, nullable=False, default=0)
    iva_pct = db.Column(db.Float, nullable=False, default=0)
    descripcion = db.Column(db.Text)
    en_tienda = db.Column(db.Boolean, nullable=False, default=True)
    destacado = db.Column(db.Boolean, nullable=False, default=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    imagen = deferred(db.Column(db.LargeBinary))
    imagen_mime = db.Column(db.String(40))
    imagen_v = db.Column(db.Integer, nullable=False, default=0)
    creado = db.Column(db.DateTime, default=ahora)
    busqueda = db.Column(db.Text)  # texto sin tildes para buscar (se llena solo)

    def actualizar_busqueda(self):
        self.busqueda = normalizar(" ".join(str(x) for x in (self.codigo, self.codigo_barras, self.nombre, self.marca,
                                                             self.referencia, self.compatibilidad, self.ubicacion) if x))

    def precio(self, lista="detal"):
        p = {"detal": self.precio_detal, "mayor": self.precio_mayor, "taller": self.precio_taller}.get(lista)
        return p or self.precio_detal or 0

    @property
    def valor_inventario(self):
        return max(self.stock or 0, 0) * (self.costo_promedio or 0)

    @property
    def bajo_minimo(self):
        return (self.stock_minimo or 0) > 0 and (self.stock or 0) <= self.stock_minimo

    @property
    def margen_detal(self):
        if not self.precio_detal:
            return None
        base = self.precio_detal / (1 + (self.iva_pct or 0) / 100)
        return (base - (self.costo_promedio or 0)) / base * 100 if base else None


class Movimiento(db.Model):
    """Kardex: cada entrada o salida de un repuesto con su costo y saldo."""
    __tablename__ = "ts_movimientos"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False, index=True)
    producto = db.relationship("Producto")
    tipo = db.Column(db.String(20), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)  # + entra, - sale
    costo_unitario = db.Column(db.Float, nullable=False, default=0)
    saldo = db.Column(db.Float, nullable=False, default=0)
    costo_promedio = db.Column(db.Float, nullable=False, default=0)  # después del movimiento
    documento = db.Column(db.String(60))
    doc_tipo = db.Column(db.String(20))
    doc_id = db.Column(db.Integer)
    nota = db.Column(db.String(255))
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")

    @property
    def tipo_nombre(self):
        return TIPOS_MOV.get(self.tipo, self.tipo)


class Cliente(db.Model):
    __tablename__ = "ts_clientes"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(140), nullable=False)
    documento = db.Column(db.String(30), index=True)
    telefono = db.Column(db.String(40))
    email = db.Column(db.String(120))
    direccion = db.Column(db.String(200))
    lista = db.Column(db.String(10), nullable=False, default="detal")
    barrio = db.Column(db.String(80))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    ruta_id = db.Column(db.Integer, db.ForeignKey("ts_rutas.id"), index=True)
    ruta = db.relationship("Ruta", backref=db.backref("clientes", lazy="dynamic"))
    orden_ruta = db.Column(db.Integer, default=0)
    cupo = db.Column(db.Float, nullable=False, default=0)
    plazo_dias = db.Column(db.Integer, nullable=False, default=30)
    notas = db.Column(db.String(300))
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado = db.Column(db.DateTime, default=ahora)

    @property
    def lista_nombre(self):
        return LISTAS_PRECIO.get(self.lista, self.lista)

    @property
    def tiene_ubicacion(self):
        return self.lat is not None and self.lng is not None

    @property
    def destino_maps(self):
        """Texto que Google Maps entiende: coordenadas si las hay, si no la dirección."""
        if self.tiene_ubicacion:
            return f"{self.lat:.6f},{self.lng:.6f}"
        partes = [self.direccion, self.barrio, config().ciudad]
        return ", ".join(x for x in partes if x)


class Ruta(db.Model):
    """Recorrido de un preventista: locales en orden de visita."""
    __tablename__ = "ts_rutas"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False)
    dia = db.Column(db.Integer)  # 0 = lunes … 6 = domingo; vacío = cualquier día
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")
    notas = db.Column(db.String(300))
    activa = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def dia_nombre(self):
        return DIAS[self.dia] if self.dia is not None and 0 <= self.dia < 7 else "Cualquier día"

    def clientes_orden(self):
        return self.clientes.filter_by(activo=True).order_by(Cliente.orden_ruta, Cliente.nombre).all()


class Visita(db.Model):
    """Registro de cada parada del preventista: si hubo pedido o por qué no."""
    __tablename__ = "ts_visitas"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"), nullable=False, index=True)
    usuario = db.relationship("Usuario")
    cliente_id = db.Column(db.Integer, db.ForeignKey("ts_clientes.id"), nullable=False, index=True)
    cliente = db.relationship("Cliente")
    ruta_id = db.Column(db.Integer, db.ForeignKey("ts_rutas.id"))
    ruta = db.relationship("Ruta")
    resultado = db.Column(db.String(15), nullable=False)
    nota = db.Column(db.String(300))
    pedido_id = db.Column(db.Integer)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)

    @property
    def resultado_nombre(self):
        return RESULTADOS_VISITA.get(self.resultado, self.resultado)


class Turno(db.Model):
    """Un turno de caja: apertura con base, movimientos y cierre con arqueo."""
    __tablename__ = "ts_turnos"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"), nullable=False)
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])
    apertura = db.Column(db.DateTime, nullable=False, default=ahora)
    base = db.Column(db.Float, nullable=False, default=0)
    nota_apertura = db.Column(db.String(255))
    estado = db.Column(db.String(10), nullable=False, default="abierto", index=True)
    cierre = db.Column(db.DateTime)
    cerrado_por_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    cerrado_por = db.relationship("Usuario", foreign_keys=[cerrado_por_id])
    efectivo_esperado = db.Column(db.Float)
    efectivo_contado = db.Column(db.Float)
    diferencia = db.Column(db.Float)
    denominaciones = db.Column(db.Text)  # JSON {"100000": 3, ...}
    resumen = db.Column(db.Text)         # JSON con totales por medio al cerrar
    nota_cierre = db.Column(db.String(300))

    @property
    def abierto(self):
        return self.estado == "abierto"


class MovCaja(db.Model):
    """Libro de caja: todo el dinero que entra o sale, por medio de pago."""
    __tablename__ = "ts_mov_caja"
    id = db.Column(db.Integer, primary_key=True)
    turno_id = db.Column(db.Integer, db.ForeignKey("ts_turnos.id"), index=True)
    turno = db.relationship("Turno", backref=db.backref("movimientos", lazy="dynamic"))
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    tipo = db.Column(db.String(10), nullable=False)  # ingreso / egreso
    valor = db.Column(db.Float, nullable=False)
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"), nullable=False)
    medio = db.relationship("MedioPago")
    concepto = db.Column(db.String(200), nullable=False)
    origen = db.Column(db.String(20), nullable=False, default="manual")
    ref_id = db.Column(db.Integer)
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")


class Compra(db.Model):
    __tablename__ = "ts_compras"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    proveedor_id = db.Column(db.Integer, db.ForeignKey("ts_proveedores.id"))
    proveedor = db.relationship("Proveedor")
    factura = db.Column(db.String(60))
    total = db.Column(db.Float, nullable=False, default=0)
    pagado = db.Column(db.Float, nullable=False, default=0)
    vence = db.Column(db.Date)
    nota = db.Column(db.String(300))
    anulada = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")
    items = db.relationship("CompraItem", backref="compra", cascade="all, delete-orphan")
    pagos = db.relationship("PagoCompra", backref="compra", cascade="all, delete-orphan",
                            order_by="PagoCompra.fecha")

    @property
    def saldo(self):
        return 0 if self.anulada else max((self.total or 0) - (self.pagado or 0), 0)


class CompraItem(db.Model):
    __tablename__ = "ts_compra_items"
    id = db.Column(db.Integer, primary_key=True)
    compra_id = db.Column(db.Integer, db.ForeignKey("ts_compras.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False)
    producto = db.relationship("Producto")
    cantidad = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)

    @property
    def subtotal(self):
        return self.cantidad * self.costo_unitario


class PagoCompra(db.Model):
    __tablename__ = "ts_pagos_compra"
    id = db.Column(db.Integer, primary_key=True)
    compra_id = db.Column(db.Integer, db.ForeignKey("ts_compras.id"), nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora)
    valor = db.Column(db.Float, nullable=False)
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"))
    medio = db.relationship("MedioPago")
    desde_caja = db.Column(db.Boolean, nullable=False, default=False)
    mov_caja_id = db.Column(db.Integer)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))


class Ajuste(db.Model):
    __tablename__ = "ts_ajustes"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    motivo = db.Column(db.String(20), nullable=False)
    nota = db.Column(db.String(300))
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")
    items = db.relationship("AjusteItem", backref="ajuste", cascade="all, delete-orphan")

    @property
    def motivo_nombre(self):
        return MOTIVOS_AJUSTE.get(self.motivo, (self.motivo, ""))[0]

    @property
    def sentido(self):
        return MOTIVOS_AJUSTE.get(self.motivo, ("", "entrada"))[1]

    @property
    def valor(self):
        return sum(i.cantidad * i.costo_unitario for i in self.items)


class AjusteItem(db.Model):
    __tablename__ = "ts_ajuste_items"
    id = db.Column(db.Integer, primary_key=True)
    ajuste_id = db.Column(db.Integer, db.ForeignKey("ts_ajustes.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False)
    producto = db.relationship("Producto")
    cantidad = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)


class Venta(db.Model):
    __tablename__ = "ts_ventas"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False, index=True)
    token = db.Column(db.String(40), unique=True)  # para el recibo público por WhatsApp
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("ts_clientes.id"))
    cliente = db.relationship("Cliente", backref=db.backref("ventas", lazy="dynamic"))
    cliente_nombre = db.Column(db.String(140))
    cliente_doc = db.Column(db.String(30))
    cliente_tel = db.Column(db.String(40))
    lista = db.Column(db.String(10), nullable=False, default="detal")
    bruto = db.Column(db.Float, nullable=False, default=0)       # suma de líneas
    descuento = db.Column(db.Float, nullable=False, default=0)
    total = db.Column(db.Float, nullable=False, default=0)       # a pagar (IVA incluido)
    iva = db.Column(db.Float, nullable=False, default=0)
    costo = db.Column(db.Float, nullable=False, default=0)
    pagado = db.Column(db.Float, nullable=False, default=0)
    cambio = db.Column(db.Float, nullable=False, default=0)
    devuelto = db.Column(db.Float, nullable=False, default=0)        # valor devuelto por el cliente
    costo_devuelto = db.Column(db.Float, nullable=False, default=0)
    iva_devuelto = db.Column(db.Float, nullable=False, default=0)
    credito = db.Column(db.Boolean, nullable=False, default=False)
    vence = db.Column(db.Date)
    origen = db.Column(db.String(12), nullable=False, default="pos")  # pos / tienda
    pedido_id = db.Column(db.Integer)
    estado = db.Column(db.String(10), nullable=False, default="pagada")  # pagada / credito / anulada
    nota = db.Column(db.String(300))
    turno_id = db.Column(db.Integer, db.ForeignKey("ts_turnos.id"), index=True)
    turno = db.relationship("Turno")
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"), index=True)
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])
    vendedor_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"), index=True)  # quien hizo la venta (preventista o cajero)
    vendedor = db.relationship("Usuario", foreign_keys=[vendedor_id])
    anulada_fecha = db.Column(db.DateTime)
    anulada_motivo = db.Column(db.String(255))
    items = db.relationship("VentaItem", backref="venta", cascade="all, delete-orphan", order_by="VentaItem.id")
    pagos = db.relationship("PagoVenta", backref="venta", cascade="all, delete-orphan", order_by="PagoVenta.fecha")

    @property
    def anulada(self):
        return self.estado == "anulada"

    @property
    def neto(self):
        """Total después de devoluciones."""
        return 0 if self.anulada else (self.total or 0) - (self.devuelto or 0)

    @property
    def saldo(self):
        if self.anulada:
            return 0
        return max(round(self.neto - (self.pagado or 0)), 0)

    @property
    def ganancia(self):
        if self.anulada:
            return 0
        base = self.neto - ((self.iva or 0) - (self.iva_devuelto or 0))
        return base - ((self.costo or 0) - (self.costo_devuelto or 0))

    @property
    def folio(self):
        return f"V-{self.numero:05d}"


class VentaItem(db.Model):
    __tablename__ = "ts_venta_items"
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey("ts_ventas.id"), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False, index=True)
    producto = db.relationship("Producto")
    codigo = db.Column(db.String(60))
    nombre = db.Column(db.String(200), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)
    precio = db.Column(db.Float, nullable=False)        # precio unitario de lista cobrado (IVA incluido)
    total = db.Column(db.Float, nullable=False)         # cantidad × precio
    neto = db.Column(db.Float, nullable=False)          # total menos su parte del descuento general
    iva_pct = db.Column(db.Float, nullable=False, default=0)
    costo_unitario = db.Column(db.Float, nullable=False, default=0)
    devuelto = db.Column(db.Float, nullable=False, default=0)

    @property
    def neto_unitario(self):
        return self.neto / self.cantidad if self.cantidad else 0


class PagoVenta(db.Model):
    """Pago de contado o abono posterior a una venta."""
    __tablename__ = "ts_pagos_venta"
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey("ts_ventas.id"), nullable=False, index=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora)
    tipo = db.Column(db.String(10), nullable=False, default="contado")  # contado / abono
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"), nullable=False)
    medio = db.relationship("MedioPago")
    valor = db.Column(db.Float, nullable=False)
    recibido = db.Column(db.Float)  # efectivo recibido (para calcular cambio)
    referencia = db.Column(db.String(80))  # número de la transferencia
    turno_id = db.Column(db.Integer, db.ForeignKey("ts_turnos.id"))
    mov_caja_id = db.Column(db.Integer)
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))


class Devolucion(db.Model):
    __tablename__ = "ts_devoluciones"
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey("ts_ventas.id"), nullable=False, index=True)
    venta = db.relationship("Venta", backref="devoluciones")
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    valor = db.Column(db.Float, nullable=False)
    iva = db.Column(db.Float, nullable=False, default=0)
    costo = db.Column(db.Float, nullable=False, default=0)
    reembolso = db.Column(db.Float, nullable=False, default=0)
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"))
    medio = db.relationship("MedioPago")
    motivo = db.Column(db.String(255))
    detalle = db.Column(db.Text)  # JSON de líneas devueltas
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))


class Cotizacion(db.Model):
    __tablename__ = "ts_cotizaciones"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora)
    cliente_id = db.Column(db.Integer, db.ForeignKey("ts_clientes.id"))
    cliente = db.relationship("Cliente")
    cliente_nombre = db.Column(db.String(140))
    cliente_tel = db.Column(db.String(40))
    lista = db.Column(db.String(10), nullable=False, default="detal")
    descuento = db.Column(db.Float, nullable=False, default=0)
    total = db.Column(db.Float, nullable=False, default=0)
    vigencia_dias = db.Column(db.Integer, nullable=False, default=8)
    nota = db.Column(db.String(300))
    estado = db.Column(db.String(12), nullable=False, default="abierta")  # abierta / vendida / descartada
    venta_id = db.Column(db.Integer)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")
    items = db.relationship("CotizacionItem", backref="cotizacion", cascade="all, delete-orphan")

    @property
    def folio(self):
        return f"C-{self.numero:05d}"


class CotizacionItem(db.Model):
    __tablename__ = "ts_cotizacion_items"
    id = db.Column(db.Integer, primary_key=True)
    cotizacion_id = db.Column(db.Integer, db.ForeignKey("ts_cotizaciones.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False)
    producto = db.relationship("Producto")
    nombre = db.Column(db.String(200), nullable=False)
    codigo = db.Column(db.String(60))
    cantidad = db.Column(db.Float, nullable=False)
    precio = db.Column(db.Float, nullable=False)


class Pedido(db.Model):
    """Pedido de la tienda online o de un preventista. Reserva el stock hasta que se confirma o vence."""
    __tablename__ = "ts_pedidos"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    origen = db.Column(db.String(10), default="web")  # web / preventa
    cliente_id = db.Column(db.Integer, db.ForeignKey("ts_clientes.id"))
    cliente = db.relationship("Cliente")
    vendedor_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    vendedor = db.relationship("Usuario")
    lista = db.Column(db.String(10), default="detal")
    token = db.Column(db.String(40), unique=True, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    nombre = db.Column(db.String(140), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    documento = db.Column(db.String(30))
    email = db.Column(db.String(120))
    entrega = db.Column(db.String(12), nullable=False, default="recoger")  # recoger / domicilio / envio
    direccion = db.Column(db.String(200))
    ciudad = db.Column(db.String(80))
    medio_preferido = db.Column(db.String(60))
    nota = db.Column(db.String(400))
    total = db.Column(db.Float, nullable=False, default=0)
    estado = db.Column(db.String(12), nullable=False, default="pendiente", index=True)  # pendiente / confirmado / cancelado
    venta_id = db.Column(db.Integer)
    gestion_nota = db.Column(db.String(300))
    items = db.relationship("PedidoItem", backref="pedido", cascade="all, delete-orphan")

    @property
    def folio(self):
        return f"{'P' if self.origen == 'preventa' else 'W'}-{self.numero:05d}"

    @property
    def es_preventa(self):
        return self.origen == "preventa"


class PedidoItem(db.Model):
    __tablename__ = "ts_pedido_items"
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("ts_pedidos.id"), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False, index=True)
    producto = db.relationship("Producto")
    codigo = db.Column(db.String(60))
    nombre = db.Column(db.String(200), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)
    precio = db.Column(db.Float, nullable=False)


class Empleado(db.Model):
    __tablename__ = "ts_empleados"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(140), nullable=False)
    documento = db.Column(db.String(30))
    cargo = db.Column(db.String(80))
    telefono = db.Column(db.String(40))
    tipo_pago = db.Column(db.String(10), nullable=False, default="fijo")
    salario = db.Column(db.Float, nullable=False, default=0)
    comision_pct = db.Column(db.Float, nullable=False, default=0)
    aux_transporte = db.Column(db.Boolean, nullable=False, default=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))  # ventas de este usuario = comisión
    usuario = db.relationship("Usuario")
    fecha_ingreso = db.Column(db.Date)
    activo = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def tipo_nombre(self):
        return TIPOS_PAGO_EMP.get(self.tipo_pago, self.tipo_pago)


class PagoNomina(db.Model):
    __tablename__ = "ts_nominas"
    id = db.Column(db.Integer, primary_key=True)
    empleado_id = db.Column(db.Integer, db.ForeignKey("ts_empleados.id"), nullable=False)
    empleado = db.relationship("Empleado", backref=db.backref("pagos", lazy="dynamic"))
    desde = db.Column(db.Date, nullable=False)
    hasta = db.Column(db.Date, nullable=False)
    fecha_pago = db.Column(db.Date, nullable=False)
    dias = db.Column(db.Integer, nullable=False, default=30)
    basico = db.Column(db.Float, default=0)
    aux_transporte = db.Column(db.Float, default=0)
    ventas_base = db.Column(db.Float, default=0)
    comisiones = db.Column(db.Float, default=0)
    horas_extra = db.Column(db.Float, default=0)
    bonificaciones = db.Column(db.Float, default=0)
    deduccion_salud = db.Column(db.Float, default=0)
    deduccion_pension = db.Column(db.Float, default=0)
    otras_deducciones = db.Column(db.Float, default=0)
    deducciones_detalle = db.Column(db.String(255))
    neto = db.Column(db.Float, default=0)
    aportes = db.Column(db.Float, default=0)
    provisiones = db.Column(db.Float, default=0)
    costo_empresa = db.Column(db.Float, default=0)
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"))
    medio = db.relationship("MedioPago")
    mov_caja_id = db.Column(db.Integer)
    nota = db.Column(db.String(255))
    anulada = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))


class Gasto(db.Model):
    __tablename__ = "ts_gastos"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    categoria = db.Column(db.String(60), nullable=False)
    descripcion = db.Column(db.String(200), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"))
    medio = db.relationship("MedioPago")
    mov_caja_id = db.Column(db.Integer)
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")


@event.listens_for(Producto, "before_insert")
@event.listens_for(Producto, "before_update")
def _busqueda_producto(mapper, conn, p):
    p.actualizar_busqueda()


def config():
    """Fila única de configuración (la crea si no existe)."""
    c = db.session.get(Config, 1)
    if c is None:
        c = Config(id=1)
        db.session.add(c)
        db.session.flush()
    return c


def siguiente(campo):
    """Siguiente consecutivo bloqueando la fila de configuración (sin números repetidos)."""
    c = db.session.query(Config).filter_by(id=1).with_for_update().one()
    n = getattr(c, campo) or 1
    setattr(c, campo, n + 1)
    return n


def semilla():
    """Datos mínimos al instalar: configuración, efectivo y transferencias comunes, categorías."""
    cfg = config()
    if cfg.nombre and "top speed" in cfg.nombre.lower():  # cambio de marca
        cfg.nombre = "Moto Speed"
    if not MedioPago.query.first():
        for i, (nom, tipo) in enumerate([("Efectivo", "efectivo"), ("Nequi", "transferencia"),
                                         ("Daviplata", "transferencia"), ("Bancolombia", "transferencia"),
                                         ("Datáfono", "tarjeta")]):
            db.session.add(MedioPago(nombre=nom, tipo=tipo, orden=i, activo=nom != "Datáfono"))
    if not Categoria.query.first():
        for nom in ["Frenos", "Transmisión (kit de arrastre)", "Motor", "Eléctrico", "Iluminación",
                    "Suspensión", "Llantas y neumáticos", "Lubricantes", "Filtros", "Carburación e inyección",
                    "Embrague", "Carrocería y plásticos", "Accesorios", "Tornillería", "Herramientas"]:
            db.session.add(Categoria(nombre=nom))
    for p in Producto.query.filter(Producto.busqueda.is_(None)).all():
        p.actualizar_busqueda()
    db.session.commit()


def migrar():
    """Agrega columnas nuevas a tablas que ya existían (create_all no lo hace)."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    tablas = set(insp.get_table_names())
    for tabla in db.metadata.sorted_tables:
        if tabla.name not in tablas:
            continue
        existentes = {c["name"] for c in insp.get_columns(tabla.name)}
        for col in tabla.columns:
            if col.name in existentes:
                continue
            tipo = col.type.compile(dialect=db.engine.dialect)
            db.session.execute(text(f'ALTER TABLE {tabla.name} ADD COLUMN {col.name} {tipo}'))
            defecto = col.default.arg if col.default is not None and not callable(col.default.arg) else None
            if defecto is not None:
                db.session.execute(text(f"UPDATE {tabla.name} SET {col.name} = :v"), {"v": defecto})
    db.session.commit()
