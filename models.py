"""Modelos de Moto Speed: repuestos de moto, POS, caja, inventario con costo promedio,
cartera, tienda online, nómina, gastos.

Todas las tablas usan el prefijo ts_ para poder compartir una base de Supabase con otras apps.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, or_
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
    "edita_compra": "Edición de compra",
    "devolucion_compra": "Devolución a proveedor",
    "venta": "Venta",
    "anula_venta": "Anulación de venta",
    "devolucion": "Devolución de cliente",
    "ajuste_entrada": "Ajuste (entrada)",
    "ajuste_salida": "Ajuste (salida)",
    "anula_ajuste": "Anulación de ajuste",
    "carga": "Carga masiva",
}
CATEGORIAS_CAJA_MENOR = ["Transporte y domicilios", "Mensajería", "Aseo y cafetería", "Papelería", "Alimentación",
                         "Mantenimiento menor", "Herramienta menor", "Otros"]
# cómo se llama cada salida de caja según su origen (para discriminar los egresos)
CLASE_EGRESO = {"compra": "Pago a proveedor", "proveedor": "Pago a proveedor", "caja_menor": "Caja menor",
                "gasto": "Gasto del negocio", "nomina": "Nómina", "devolucion": "Devolución a cliente",
                "reverso": "Reversos", "manual": "Otras salidas", "ajuste": "Ajuste de caja",
                "caja_mayor": "Traslado a caja mayor"}
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
    # reporte para socios: enlace secreto de solo consulta, sin usuario ni clave
    socios_token = db.Column(db.String(40))
    caja_base_meta = db.Column(db.Float, nullable=False, default=400000)   # efectivo que se deja en la caja diaria
    socios_activo = db.Column(db.Boolean, nullable=False, default=False)
    socios_visitas = db.Column(db.Integer, nullable=False, default=0)
    socios_ultima = db.Column(db.DateTime)
    tienda_mensaje = db.Column(db.String(300), default="Pide en línea y recoge en el local o te lo enviamos.")
    tienda_envio = db.Column(db.String(300), default="Domicilios en la ciudad con costo según la zona. Envíos nacionales por transportadora (pago contra entrega o anticipado).")
    tienda_muestra_agotados = db.Column(db.Boolean, nullable=False, default=True)
    reserva_horas = db.Column(db.Integer, nullable=False, default=48)
    # horario del local («08:00-18:00»; vacío = cerrado) y redes, para la portada de la tienda
    horario_lv = db.Column(db.String(20), default="08:00-18:00")
    horario_sab = db.Column(db.String(20), default="08:00-17:00")
    horario_dom = db.Column(db.String(20), default="08:00-12:00")
    facebook_url = db.Column(db.String(200))
    instagram_url = db.Column(db.String(200))
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
    sig_carga = db.Column(db.Integer, nullable=False, default=1)
    sig_vale = db.Column(db.Integer, nullable=False, default=1)
    sig_ajuste_caja = db.Column(db.Integer, nullable=False, default=1)
    sig_egreso_mayor = db.Column(db.Integer, nullable=False, default=1)
    sig_cod_interno = db.Column(db.Integer, nullable=False, default=0)  # el primero es 0000
    sig_anulacion = db.Column(db.Integer, nullable=False, default=1)
    sig_dev_compra = db.Column(db.Integer, nullable=False, default=1)
    sig_aporte = db.Column(db.Integer, nullable=False, default=1)
    # Facturación electrónica con el facturador gratuito de la DIAN (se digita allá; aquí se lleva el control)
    fe_activa = db.Column(db.Boolean, nullable=False, default=False)
    fe_desde = db.Column(db.Date)                       # desde qué fecha las ventas se deben facturar
    fe_url = db.Column(db.String(200))                  # dirección del facturador
    fe_uvt = db.Column(db.Float, nullable=False, default=52374)      # valor UVT del año
    fe_tope_uvt = db.Column(db.Float, nullable=False, default=3500)  # tope de ingresos para no estar obligado
    fe_aviso_pct = db.Column(db.Float, nullable=False, default=80)   # avisar desde este % del tope
    fecha_apertura = db.Column(db.Date)                 # día en que el negocio abrió al público


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


DISP_VIRTUAL = 99  # unidades que se muestran disponibles para un producto de la bodega virtual


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
    # modelos de moto equivalentes, uno por línea: en la tienda cada uno sale como variante 0001-1, 0001-2…
    equivalencias = db.Column(db.Text)
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
    # regla de ganancia del precio detal: vacío = % general de Configuración · "pct" = % propio · "valor" = pesos fijos
    ganancia_tipo = db.Column(db.String(5))
    ganancia_valor = db.Column(db.Float)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    imagen = deferred(db.Column(db.LargeBinary))
    imagen_mini = deferred(db.Column(db.LargeBinary))   # miniatura de 400 px para listados (pesa ~15 KB)
    imagen_mime = db.Column(db.String(40))
    imagen_v = db.Column(db.Integer, nullable=False, default=0)
    creado = db.Column(db.DateTime, default=ahora)
    busqueda = db.Column(db.Text)  # texto sin tildes para buscar (se llena solo)
    # Código interno de inventario de Moto Speed (0001, 0002…). Se asigna solo al crear el repuesto.
    codigo_interno = db.Column(db.String(20), index=True)
    # Bodega virtual: se vende como si hubiera, pero no hay unidades físicas. Usa un costo virtual
    # hasta que se compre de verdad; ahí pasa al inventario real con costo promedio.
    virtual = db.Column(db.Boolean, nullable=False, default=False)
    costo_virtual = db.Column(db.Float, nullable=False, default=0)
    # al vender la última unidad real vuelve solo a la bodega virtual (con el último costo de compra)
    volver_virtual = db.Column(db.Boolean, nullable=False, default=False)
    # Comprado en promoción: el cajero/vendedor ve un aviso para buscar venderlo
    promo_compra = db.Column(db.Boolean, nullable=False, default=False)
    promo_nota = db.Column(db.String(160))
    # "arrastre": lo creó el cajero en una venta de arrastre (repuesto que no se tenía y se compra afuera)
    origen = db.Column(db.String(12))
    imagen_lote = db.Column(db.String(80))   # foto de catálogo puesta automáticamente (fotos_catalogo.py)
    donde_comprar = db.Column(db.String(120))  # proveedor / almacén donde se consigue

    def texto_base(self):
        """Texto de búsqueda sin las equivalencias (para filtrar las variantes de la tienda)."""
        return normalizar(" ".join(str(x) for x in (self.codigo_interno, self.codigo, self.codigo_barras, self.nombre, self.marca,
                                                    self.referencia, self.compatibilidad, self.ubicacion) if x))

    def actualizar_busqueda(self):
        self.busqueda = normalizar(self.texto_base() + " " + " ".join(self.lista_equivalencias))

    @property
    def lista_equivalencias(self):
        return separar_equivalencias(self.equivalencias)

    def variantes(self):
        """[(n, modelo, código)] · código = código interno + -n (0001-1, 0001-2…)."""
        base = self.codigo_interno or self.codigo
        return [(i, mod, f"{base}-{i}") for i, mod in enumerate(self.lista_equivalencias, 1)]

    def modelo_variante(self, n):
        eq = self.lista_equivalencias
        return eq[n - 1] if n and 0 < n <= len(eq) else None

    def precio(self, lista="detal"):
        p = {"detal": self.precio_detal, "mayor": self.precio_mayor, "taller": self.precio_taller}.get(lista)
        return p or self.precio_detal or 0

    @property
    def valor_inventario(self):
        return max(self.stock or 0, 0) * (self.costo_promedio or 0)

    @property
    def costo_ref(self):
        """Costo con el que se calcula la ganancia: el virtual si no hay unidades reales."""
        return (self.costo_virtual or 0) if self.virtual else (self.costo_promedio or 0)

    def disponible(self, reservado=None):
        if self.virtual:
            return DISP_VIRTUAL
        return max((self.stock or 0) - (reservado or {}).get(self.id, 0), 0)

    @property
    def bajo_minimo(self):
        return not self.virtual and (self.stock_minimo or 0) > 0 and (self.stock or 0) <= self.stock_minimo

    def precio_por_regla(self, cfg, costo=None):
        """Precio detal que sale de la regla de ganancia de este repuesto (con IVA si aplica), o None sin costo.
        % → costo × (1 + %) redondeado hacia arriba a 100 · valor fijo → costo + pesos, redondeado a 100."""
        c = self.costo_ref if costo is None else costo
        if not c or c <= 0:
            return None
        if self.ganancia_tipo == "valor" and (self.ganancia_valor or 0) > 0:
            base = c + self.ganancia_valor
        else:
            pct_ = self.ganancia_valor if self.ganancia_tipo == "pct" and self.ganancia_valor is not None else (cfg.margen_detal or 0)
            base = c * (1 + pct_ / 100)
        base *= 1 + (self.iva_pct or 0) / 100
        paso = 100 if base < 20000 else 500
        return int(-(-round(base) // paso) * paso)

    @property
    def regla_txt(self):
        if self.ganancia_tipo == "valor" and self.ganancia_valor:
            return f"+ ${self.ganancia_valor:,.0f}".replace(",", ".")
        if self.ganancia_tipo == "pct" and self.ganancia_valor is not None:
            return f"{self.ganancia_valor:g} %"
        return None

    @property
    def margen_detal(self):
        if not self.precio_detal:
            return None
        base = self.precio_detal / (1 + (self.iva_pct or 0) / 100)
        return (base - self.costo_ref) / base * 100 if base else None


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
    # reabierta por el administrador solo para ajustes (estado "ajuste"): el efectivo queda bloqueado
    reabierto_por_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    reabierto_por = db.relationship("Usuario", foreign_keys=[reabierto_por_id])
    reabierto_en = db.Column(db.DateTime)
    motivo_reapertura = db.Column(db.String(300))
    ajustes_log = db.Column(db.Text)  # bitácora de reaperturas y ajustes

    @property
    def abierto(self):
        return self.estado == "abierto"

    @property
    def en_ajuste(self):
        return self.estado == "ajuste"


class MovCajaMayor(db.Model):
    """Caja mayor (tesorería): donde se guarda lo que sobra de la caja diaria, por medio de pago."""
    __tablename__ = "ts_caja_mayor"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    tipo = db.Column(db.String(10), nullable=False)          # entrada / salida
    valor = db.Column(db.Float, nullable=False)
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"), nullable=False)
    medio = db.relationship("MedioPago")
    concepto = db.Column(db.String(200), nullable=False)
    origen = db.Column(db.String(20), nullable=False, default="manual")   # traslado / devolucion / manual / ajuste / pago_compra / gasto
    categoria = db.Column(db.String(60))
    ref_id = db.Column(db.Integer)            # compra o gasto que se pagó desde la caja mayor
    numero = db.Column(db.Integer)            # comprobante de egreso CE-0001 (pagos y salidas)
    beneficiario = db.Column(db.String(140))  # quien recibe la plata

    @property
    def folio(self):
        return f"CE-{self.numero:04d}" if self.numero else ""
    mov_caja_id = db.Column(db.Integer, db.ForeignKey("ts_mov_caja.id"))  # movimiento enlazado en la caja diaria
    turno_id = db.Column(db.Integer, db.ForeignKey("ts_turnos.id"))
    turno = db.relationship("Turno")
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])


CATEGORIAS_CAJA_MAYOR = ["Consignación al banco", "Retiro de socio", "Préstamo", "Otra salida"]


MOTIVOS_AJUSTE_CAJA = ["Sobrante", "Faltante", "Corrección de un error", "Dinero de prueba", "Saldo inicial", "Otro"]


class AjusteCaja(db.Model):
    """Documento de ajuste de caja (AJC-0001): corrige la caja diaria o la caja mayor con una entrada
    o una salida y su motivo. Es distinto al traslado entre cajas."""
    __tablename__ = "ts_ajustes_caja"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False, index=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    caja = db.Column(db.String(10), nullable=False)      # diaria / mayor
    tipo = db.Column(db.String(10), nullable=False)      # ingreso / egreso
    valor = db.Column(db.Float, nullable=False)
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"), nullable=False)
    medio = db.relationship("MedioPago")
    motivo = db.Column(db.String(60), nullable=False)
    detalle = db.Column(db.String(300), nullable=False)
    turno_id = db.Column(db.Integer, db.ForeignKey("ts_turnos.id"))
    turno = db.relationship("Turno")
    mov_caja_id = db.Column(db.Integer, db.ForeignKey("ts_mov_caja.id"))
    mov_mayor_id = db.Column(db.Integer, db.ForeignKey("ts_caja_mayor.id"))
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    anulado_por_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    anulado_por = db.relationship("Usuario", foreign_keys=[anulado_por_id])
    anulado_fecha = db.Column(db.DateTime)
    motivo_anulacion = db.Column(db.String(200))

    @property
    def folio(self):
        return f"AJC-{self.numero:04d}"

    @property
    def nombre_caja(self):
        return "Caja mayor" if self.caja == "mayor" else "Caja diaria"


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
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])
    # discriminación de egresos
    proveedor_id = db.Column(db.Integer, db.ForeignKey("ts_proveedores.id"))
    proveedor = db.relationship("Proveedor")
    categoria = db.Column(db.String(60))      # caja menor
    beneficiario = db.Column(db.String(120))  # quien recibe la plata (vale de caja menor)
    vale = db.Column(db.Integer)              # consecutivo del vale de caja menor
    # reversión: quién, cuándo y por qué
    revertido_por_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    revertido_por = db.relationship("Usuario", foreign_keys=[revertido_por_id])
    revertido_fecha = db.Column(db.DateTime)
    revertido_motivo = db.Column(db.String(200))
    reverso_id = db.Column(db.Integer)        # movimiento contrario (si el turno ya estaba cerrado)

    @property
    def clase(self):
        return CLASE_EGRESO.get(self.origen, "Otras salidas") if self.tipo == "egreso" else "Entrada"

    @property
    def folio_vale(self):
        return f"VCM-{self.vale:04d}" if self.vale else ""

    @property
    def revertido(self):
        return bool(self.anulado or self.reverso_id)

    @property
    def reversible(self):
        return self.origen in ORIGENES_REVERSIBLES and not self.revertido


ORIGENES_REVERSIBLES = ("manual", "proveedor", "caja_menor", "ajuste")


class Mensaje(db.Model):
    """Bandeja del administrador: avisos de acciones sensibles (reversiones de caja, etc.)."""
    __tablename__ = "ts_mensajes"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    tipo = db.Column(db.String(30), nullable=False, default="aviso")
    titulo = db.Column(db.String(160), nullable=False)
    texto = db.Column(db.String(600))
    url = db.Column(db.String(200))
    autor_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    autor = db.relationship("Usuario", foreign_keys=[autor_id])
    leido = db.Column(db.Boolean, nullable=False, default=False, index=True)
    leido_por_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    leido_por = db.relationship("Usuario", foreign_keys=[leido_por_id])
    leido_fecha = db.Column(db.DateTime)
    # "admin": solo administradores (se marca leído para todos al leerlo uno) · "todos": cada usuario lo lee aparte
    para = db.Column(db.String(10), nullable=False, default="admin")
    lecturas = db.relationship("MensajeLectura", lazy="selectin", cascade="all, delete-orphan")


class MensajeLectura(db.Model):
    """Quién leyó cada mensaje enviado a todos los usuarios."""
    __tablename__ = "ts_mensajes_lecturas"
    id = db.Column(db.Integer, primary_key=True)
    mensaje_id = db.Column(db.Integer, db.ForeignKey("ts_mensajes.id"), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"), nullable=False, index=True)
    usuario = db.relationship("Usuario")
    fecha = db.Column(db.DateTime, nullable=False, default=ahora)
    __table_args__ = (db.UniqueConstraint("mensaje_id", "usuario_id"),)


def mensajes_de(usuario):
    """Mensajes que puede ver el usuario: el admin ve todos; los demás, los enviados a todos."""
    q = Mensaje.query
    if usuario.rol != "admin":
        q = q.filter(Mensaje.para == "todos")
    return q


def mensajes_sin_leer(usuario):
    leidos = db.session.query(MensajeLectura.mensaje_id).filter(MensajeLectura.usuario_id == usuario.id)
    return mensajes_de(usuario).filter(
        or_(Mensaje.autor_id.is_(None), Mensaje.autor_id != usuario.id),
        ~Mensaje.id.in_(leidos),
        or_(Mensaje.para == "todos", Mensaje.leido.is_(False)))


def marcar_leido(m, usuario):
    if m.para == "admin" and not m.leido:
        m.leido, m.leido_por_id, m.leido_fecha = True, usuario.id, ahora()
    if not MensajeLectura.query.filter_by(mensaje_id=m.id, usuario_id=usuario.id).first():
        db.session.add(MensajeLectura(mensaje_id=m.id, usuario_id=usuario.id))


def leido_por(m, usuario):
    if m.autor_id == usuario.id:
        return True
    if m.para == "admin" and m.leido:
        return True
    return any(l.usuario_id == usuario.id for l in m.lecturas)


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
    devuelto = db.Column(db.Float, nullable=False, default=0)   # valor devuelto al proveedor (al costo de la compra)
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
        return 0 if self.anulada else max((self.total or 0) - (self.devuelto or 0) - (self.pagado or 0), 0)

    @property
    def a_favor(self):
        """Lo que el proveedor nos debe: se le pagó más de lo que quedó después de las devoluciones."""
        return 0 if self.anulada else max((self.pagado or 0) - ((self.total or 0) - (self.devuelto or 0))
                                          - (self.reembolsado or 0), 0)

    @property
    def reembolsado(self):
        return sum(d.reembolso or 0 for d in self.devoluciones)


class CompraItem(db.Model):
    __tablename__ = "ts_compra_items"
    id = db.Column(db.Integer, primary_key=True)
    compra_id = db.Column(db.Integer, db.ForeignKey("ts_compras.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False)
    producto = db.relationship("Producto")
    cantidad = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)
    devuelto = db.Column(db.Float, nullable=False, default=0)

    @property
    def subtotal(self):
        return self.cantidad * self.costo_unitario


MOTIVOS_DEV_COMPRA = ["Llegó dañado", "No es la referencia", "Llegó de más", "Garantía", "Otro"]


class DevolucionCompra(db.Model):
    """Devolución de repuestos al proveedor (comprobante DP-00001)."""
    __tablename__ = "ts_devoluciones_compra"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    compra_id = db.Column(db.Integer, db.ForeignKey("ts_compras.id"), nullable=False, index=True)
    compra = db.relationship("Compra", backref=db.backref("devoluciones", order_by="DevolucionCompra.fecha"))
    motivo = db.Column(db.String(40), nullable=False)
    nota = db.Column(db.String(300))
    total = db.Column(db.Float, nullable=False, default=0)
    descontado = db.Column(db.Float, nullable=False, default=0)   # se restó de lo que se le debía al proveedor
    reembolso = db.Column(db.Float, nullable=False, default=0)    # el proveedor devolvió este dinero
    reembolso_medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"))
    reembolso_medio = db.relationship("MedioPago")
    mov_caja_id = db.Column(db.Integer)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")
    items = db.relationship("DevolucionCompraItem", backref="devolucion", cascade="all, delete-orphan")

    @property
    def folio(self):
        return f"DP-{self.numero:05d}"


class DevolucionCompraItem(db.Model):
    __tablename__ = "ts_devoluciones_compra_items"
    id = db.Column(db.Integer, primary_key=True)
    devolucion_id = db.Column(db.Integer, db.ForeignKey("ts_devoluciones_compra.id"), nullable=False, index=True)
    compra_item_id = db.Column(db.Integer, db.ForeignKey("ts_compra_items.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("ts_productos.id"), nullable=False)
    producto = db.relationship("Producto")
    cantidad = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)


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
    # soporte del pago (foto del comprobante de transferencia, recibo firmado o PDF)
    soporte = db.deferred(db.Column(db.LargeBinary))
    soporte_mime = db.Column(db.String(60))
    soporte_nombre = db.Column(db.String(160))


class CargaMasiva(db.Model):
    """Carga de archivo (CSV o Excel) por lotes, como en Stocka: un registro padre CSV-0001
    y un lote hijo por cada bloque de filas (CSV-0001-A, CSV-0001-B…)."""
    __tablename__ = "ts_cargas"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    archivo = db.Column(db.String(200))
    estado = db.Column(db.String(15), nullable=False, default="en_progreso")  # en_progreso / completo
    creados = db.Column(db.Integer, nullable=False, default=0)
    actualizados = db.Column(db.Integer, nullable=False, default=0)
    rechazados_json = db.Column(db.Text, nullable=False, default="[]")
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    usuario = db.relationship("Usuario")
    lotes = db.relationship("CargaLote", backref="carga", order_by="CargaLote.indice",
                            cascade="all, delete-orphan")

    @property
    def consecutivo(self):
        return f"CSV-{self.numero:04d}"

    @property
    def rechazados(self):
        import json
        try:
            return json.loads(self.rechazados_json or "[]")
        except ValueError:
            return []

    @property
    def total_rechazados(self):
        return len(self.rechazados)


class CargaLote(db.Model):
    __tablename__ = "ts_cargas_lotes"
    id = db.Column(db.Integer, primary_key=True)
    carga_id = db.Column(db.Integer, db.ForeignKey("ts_cargas.id"), nullable=False, index=True)
    indice = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora)
    filas = db.Column(db.Integer, nullable=False, default=0)
    creados = db.Column(db.Integer, nullable=False, default=0)
    actualizados = db.Column(db.Integer, nullable=False, default=0)
    rechazados = db.Column(db.Integer, nullable=False, default=0)

    @property
    def letra(self):
        return letra_lote(self.indice)

    @property
    def consecutivo(self):
        return f"{self.carga.consecutivo}-{self.letra}"


def letra_lote(i):
    """0→A … 25→Z, 26→AA, 27→AB…"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


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
    # venta de arrastre: incluye repuestos que no se tenían; se compran afuera para entregarlos
    arrastre = db.Column(db.Boolean, nullable=False, default=False)
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
    anulada_numero = db.Column(db.Integer)  # consecutivo del comprobante de anulación (AN-00001)
    anulada_por_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    anulada_por = db.relationship("Usuario", foreign_keys=[anulada_por_id])
    anulada_reembolso = db.Column(db.String(255))  # "Efectivo|50000;Nequi|20000": lo que se le devolvió al cliente
    # factura electrónica hecha en el facturador gratuito de la DIAN
    fe_numero = db.Column(db.String(40), index=True)
    fe_cufe = db.Column(db.String(120))
    fe_fecha = db.Column(db.DateTime)
    fe_por_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    fe_por = db.relationship("Usuario", foreign_keys=[fe_por_id])
    fe_nota_credito = db.Column(db.String(40))          # número de la nota crédito si la venta facturada se anula

    @property
    def reembolsos(self):
        out = []
        for parte in (self.anulada_reembolso or "").split(";"):
            if "|" in parte:
                medio, valor = parte.rsplit("|", 1)
                try:
                    out.append((medio, float(valor)))
                except ValueError:
                    pass
        return out
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

    @property
    def folio_anulacion(self):
        return f"AN-{self.anulada_numero:05d}" if self.anulada_numero else ""


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
    # bodega virtual: unidades vendidas que aún no existen físicamente y su costo estimado
    por_surtir = db.Column(db.Float, nullable=False, default=0)
    costo_estimado = db.Column(db.Float, nullable=False, default=0)
    arrastre = db.Column(db.Boolean, nullable=False, default=False)  # línea vendida como venta de arrastre

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
    gasto_fijo_id = db.Column(db.Integer, db.ForeignKey("ts_gastos_fijos.id"), index=True)
    gasto_fijo = db.relationship("GastoFijo")
    periodo = db.Column(db.String(7))   # "2026-11": mes al que corresponde el pago de un gasto fijo


class GastoFijo(db.Model):
    """Gasto que se repite cada mes (publicidad en Facebook, arriendo, internet…)."""
    __tablename__ = "ts_gastos_fijos"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    categoria = db.Column(db.String(60), nullable=False)
    valor = db.Column(db.Float, nullable=False)          # valor de referencia (al pagar se puede ajustar)
    dia = db.Column(db.Integer, nullable=False, default=1)  # día del mes en que se paga
    medio_id = db.Column(db.Integer, db.ForeignKey("ts_medios_pago.id"))
    medio = db.relationship("MedioPago")
    desde = db.Column(db.Date)                            # primer mes que aplica
    activo = db.Column(db.Boolean, nullable=False, default=True)
    nota = db.Column(db.String(200))


# ---------------------------------------------------------------- inversión inicial (fundación del negocio)
CATEGORIAS_INVERSION = {
    "Local": "gasto", "Adecuaciones del local": "activo", "Muebles y estanterías": "activo",
    "Equipos (computador, impresora, cajón)": "activo", "Mercancía inicial": "inventario",
    "Trámites y legalización": "gasto", "Imagen y publicidad de lanzamiento": "gasto",
    "Software y servicios": "gasto", "Herramientas": "activo", "Otros": "gasto",
}
TIPOS_INVERSION = {"activo": "Activo (dura años)", "gasto": "Gasto preoperativo", "inventario": "Inventario"}


class Socio(db.Model):
    __tablename__ = "ts_socios"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    documento = db.Column(db.String(30))
    telefono = db.Column(db.String(30))
    nota = db.Column(db.String(200))
    activo = db.Column(db.Boolean, nullable=False, default=True)
    # pareja o grupo con el que aporta (ej. "Carlos y Marisel"); los reportes sencillos suman por pareja
    pareja = db.Column(db.String(80))

    @property
    def grupo_nombre(self):
        return self.pareja or self.nombre


class AporteSocio(db.Model):
    """Dinero que un socio entrega al fondo común del negocio (no ligado a una compra puntual)."""
    __tablename__ = "ts_aportes_socios"
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer)   # consecutivo del recibo (AP-00001)
    fecha = db.Column(db.Date, nullable=False)
    socio_id = db.Column(db.Integer, db.ForeignKey("ts_socios.id"), nullable=False, index=True)
    socio = db.relationship("Socio")
    valor = db.Column(db.Float, nullable=False)
    medio = db.Column(db.String(60))
    nota = db.Column(db.String(200))
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    creado = db.Column(db.DateTime, nullable=False, default=ahora)
    usuario = db.relationship("Usuario")
    # aporte conjunto: cada socio queda con su parte (y su recibo); las partes comparten este código
    grupo = db.Column(db.String(16), index=True)

    @property
    def folio(self):
        return f"AP-{self.numero:05d}" if self.numero else f"AP-{self.id:05d}"

    @property
    def companeros(self):
        """Las otras partes del mismo aporte conjunto."""
        if not self.grupo:
            return []
        return AporteSocio.query.filter(AporteSocio.grupo == self.grupo, AporteSocio.id != self.id,
                                        AporteSocio.anulado.is_(False)).order_by(AporteSocio.id).all()


class Inversion(db.Model):
    """Cada cosa que se pagó para montar el negocio, antes de abrir."""
    __tablename__ = "ts_inversiones"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False, index=True)
    concepto = db.Column(db.String(200), nullable=False)
    categoria = db.Column(db.String(60), nullable=False)
    tipo = db.Column(db.String(12), nullable=False, default="gasto")   # activo / gasto / inventario
    valor = db.Column(db.Float, nullable=False)
    socio_id = db.Column(db.Integer, db.ForeignKey("ts_socios.id"), index=True)  # quién lo pagó (vacío = fondo común)
    socio = db.relationship("Socio")
    proveedor = db.Column(db.String(120))
    medio = db.Column(db.String(60))
    nota = db.Column(db.String(300))
    soporte = db.deferred(db.Column(db.LargeBinary))
    soporte_mime = db.Column(db.String(60))
    soporte_v = db.Column(db.Integer, nullable=False, default=0)
    anulado = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("ts_usuarios.id"))
    creado = db.Column(db.DateTime, nullable=False, default=ahora)


class InversionSoporte(db.Model):
    """Archivos de soporte de un registro de la inversión inicial (facturas, recibos, fotos, PDF)."""
    __tablename__ = "ts_inversiones_soportes"
    id = db.Column(db.Integer, primary_key=True)
    inversion_id = db.Column(db.Integer, db.ForeignKey("ts_inversiones.id"), nullable=False, index=True)
    nombre = db.Column(db.String(160))
    mime = db.Column(db.String(60), nullable=False)
    datos = db.deferred(db.Column(db.LargeBinary, nullable=False))
    creado = db.Column(db.DateTime, nullable=False, default=ahora)


class InversionPago(db.Model):
    """Parte que pagó cada socio cuando una compra de la inversión se pagó entre varios (pago conjunto)."""
    __tablename__ = "ts_inversiones_pagos"
    id = db.Column(db.Integer, primary_key=True)
    inversion_id = db.Column(db.Integer, db.ForeignKey("ts_inversiones.id"), nullable=False, index=True)
    socio_id = db.Column(db.Integer, db.ForeignKey("ts_socios.id"), nullable=False, index=True)
    socio = db.relationship("Socio")
    valor = db.Column(db.Float, nullable=False)


Inversion.pagos = db.relationship(InversionPago, order_by=InversionPago.id, cascade="all, delete-orphan", backref="inversion")


def _pagadores(self):
    """[(socio, valor)] de quienes pagaron de su bolsillo; vacío = fondo común."""
    if self.pagos:
        return [(p.socio, p.valor) for p in self.pagos]
    return [(self.socio, self.valor)] if self.socio else []


def _pago_txt(self):
    pg = self.pagadores
    if not pg:
        return "Fondo común"
    if len(pg) == 1:
        return pg[0][0].nombre
    return " + ".join(s.nombre for s, _ in pg) + " (conjunto)"


Inversion.pagadores = property(_pagadores)
Inversion.pago_txt = property(_pago_txt)

Inversion.soportes = db.relationship(InversionSoporte, order_by=InversionSoporte.id, cascade="all, delete-orphan",
                                     backref="inversion")


@event.listens_for(Producto, "before_insert")
@event.listens_for(Producto, "before_update")
def _busqueda_producto(mapper, conn, p):
    p.actualizar_busqueda()


def separar_equivalencias(texto):
    """Modelos separados por línea, coma o punto y coma; sin repetidos (sin importar mayúsculas), en el orden escrito."""
    import re as _re
    vistos, out = set(), []
    for x in _re.split(r"[\n,;]+", str(texto or "")):
        x = " ".join(x.split())
        if x and x.lower() not in vistos:
            vistos.add(x.lower())
            out.append(x[:80])
    return out


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


def formato_codigo_interno(n):
    return f"{n:04d}"


def asignar_codigo_interno(p):
    """Le da al repuesto el siguiente código interno si no tiene (0001, 0002…)."""
    if not (p.codigo_interno or "").strip():
        while True:
            c = db.session.query(Config).filter_by(id=1).with_for_update().one()
            n = c.sig_cod_interno or 0
            c.sig_cod_interno = n + 1
            cod = formato_codigo_interno(n)
            if not Producto.query.filter(Producto.codigo_interno == cod, Producto.id != p.id).first():
                break
        p.codigo_interno = cod
    return p.codigo_interno


def semilla():
    """Datos mínimos al instalar: configuración, efectivo y transferencias comunes, categorías."""
    cfg = config()
    if cfg.nombre and "top speed" in cfg.nombre.lower():  # cambio de marca
        cfg.nombre = "Moto Speed"
    # pagos y salidas de la caja mayor hechos antes de los comprobantes: les da su número CE para poder reimprimir
    sin_ce = (MovCajaMayor.query.filter(MovCajaMayor.tipo == "salida", MovCajaMayor.numero.is_(None),
                                        MovCajaMayor.origen.in_(["pago_compra", "gasto", "manual"]))
              .order_by(MovCajaMayor.id).all())
    for mm in sin_ce:
        mm.numero = cfg.sig_egreso_mayor or 1
        cfg.sig_egreso_mayor = mm.numero + 1
        if mm.origen == "pago_compra" and not mm.beneficiario and mm.ref_id:
            c = db.session.get(Compra, mm.ref_id)
            if c and c.proveedor:
                mm.beneficiario = c.proveedor.nombre[:140]
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
    # ventas anuladas antes de existir el consecutivo de anulación: se numeran en el orden en que se anularon
    for v in Venta.query.filter(Venta.estado == "anulada", Venta.anulada_numero.is_(None)) \
            .order_by(Venta.anulada_fecha, Venta.id).all():
        v.anulada_numero = siguiente("sig_anulacion")
    # soporte único de la inversión inicial (versión anterior) -> pasa a la lista de soportes
    for i in Inversion.query.filter(Inversion.soporte_v > 0).all():
        if i.soporte and not i.soportes:
            ext = "pdf" if i.soporte_mime == "application/pdf" else "jpg"
            i.soportes.append(InversionSoporte(nombre=f"soporte.{ext}", mime=i.soporte_mime or "image/jpeg", datos=i.soporte))
            i.soporte, i.soporte_v = None, 0
    # aportes de socios registrados antes de existir el recibo: se numeran en orden
    # parejas de socios de Moto Speed (una sola vez; después se cambian en Inversión inicial → Socios)
    socios = Socio.query.all()
    if socios and not any(x.pareja for x in socios):
        for x in socios:
            nom = normalizar(x.nombre)
            if nom.startswith("carlos") or nom.startswith("marisel"):
                x.pareja = "Carlos y Marisel"
            elif nom.startswith("roldan") or nom.startswith("yennyth") or nom.startswith("marcela"):
                x.pareja = "Roldán y Marcela"
    for a in AporteSocio.query.filter(AporteSocio.numero.is_(None)).order_by(AporteSocio.id).all():
        a.numero = siguiente("sig_aporte")
    # repuestos que se crearon antes de existir el código interno: se numeran en el orden en que se crearon
    with db.session.no_autoflush:
        for p in Producto.query.filter(or_(Producto.codigo_interno.is_(None), Producto.codigo_interno == "")) \
                .order_by(Producto.id).all():
            asignar_codigo_interno(p)
            db.session.flush()
    db.session.commit()


def migrar():
    """Agrega columnas nuevas a tablas que ya existían (create_all no lo hace).
    En Postgres cada ALTER espera como máximo unos segundos el bloqueo de la tabla (la versión anterior
    de la app puede seguir usándola durante el deploy) y reintenta, para no quedarse colgado al arrancar."""
    import time
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import DBAPIError
    pg = db.engine.dialect.name == "postgresql"
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
            defecto = col.default.arg if col.default is not None and not callable(col.default.arg) else None
            for intento in range(1, 13):
                try:
                    if pg:
                        db.session.execute(text("SET LOCAL lock_timeout = '8s'"))
                        db.session.execute(text(f'ALTER TABLE {tabla.name} ADD COLUMN IF NOT EXISTS {col.name} {tipo}'))
                    else:
                        db.session.execute(text(f'ALTER TABLE {tabla.name} ADD COLUMN {col.name} {tipo}'))
                    if defecto is not None:
                        db.session.execute(text(f"UPDATE {tabla.name} SET {col.name} = :v"), {"v": defecto})
                    db.session.commit()
                    print(f"migrar: columna {tabla.name}.{col.name} agregada", flush=True)
                    break
                except DBAPIError as e:
                    db.session.rollback()
                    if not pg or intento == 12:
                        raise
                    print(f"migrar: {tabla.name} ocupada ({e.orig.__class__.__name__}), reintento {intento}", flush=True)
                    time.sleep(3)
    db.session.commit()
