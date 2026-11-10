# Top Speed — POS, inventario y tienda online para repuestos de moto

App web para un almacén de repuestos de moto: **punto de venta con cuadre de caja**, **inventario con costo promedio ponderado y kardex**, compras a proveedores, cartera, nómina, gastos, reportes de ganancia y una **tienda online enlazada al mismo inventario**.

Hecha con Flask + PostgreSQL (Supabase), desplegable en Render. Todas las tablas usan el prefijo `ts_`, así que puede compartir base con otras apps.

## Módulos

| Módulo | Qué hace |
|---|---|
| **POS** | Busca por código, código de barras (lector USB), nombre, marca o moto. Precio detal, por mayor o taller (se aplica solo al elegir el cliente). Descuento, pago en **efectivo, transferencia (Nequi, Daviplata, Bancolombia, Bre-B…) o mixto**, cálculo de cambio, venta a crédito. Ticket de 58/80 mm y recibo por WhatsApp con enlace. Atajos: F1 buscar, F2 cobrar |
| **Caja y cuadre** | Apertura con base, entradas y salidas de dinero (domicilio, almuerzo…), cierre con **arqueo por billetes y monedas**: el sistema dice cuánto efectivo debe haber y cuánto entró por cada transferencia, y registra sobrante/faltante con observación. Historial de cierres imprimible |
| **Inventario** | Ficha del repuesto con compatibilidad de motos, ubicación en bodega, stock mínimo y foto. Cada **compra suma** y recalcula el **costo promedio ponderado**; cada **venta resta** y guarda el costo de ese momento (la ganancia es real). Ajustes (conteo, daño, garantía, uso interno), **kardex** por repuesto y general. Importar catálogo desde Excel |
| **Compras y proveedores** | Varias líneas por compra, de contado (opcionalmente desde la caja) o a crédito con plazo; pagos parciales y cuentas por pagar |
| **Ventas, devoluciones y cartera** | Detalle de cada venta, abonos, **devolución parcial** (el repuesto vuelve al inventario a su costo y el dinero sale de la caja o se descuenta de la deuda), anulación con reembolso. Cartera por cliente con recordatorio por WhatsApp |
| **Cotizaciones** | Se arman en el POS y se convierten en venta con un clic. Imprimibles y por WhatsApp |
| **Tienda online** | Catálogo público en la raíz del sitio con buscador por repuesto o moto, categorías, carrito y pedido. **Precios y stock salen del mismo inventario**. Los pedidos **apartan** el stock (48 h configurables) y llegan a «Pedidos web»; al confirmarlos se cobran en el POS y ahí se descuentan |
| **Nómina** | Sueldo fijo, comisión sobre ventas del usuario del POS, o mixto; aportes, prestaciones, auxilio de transporte; desprendible imprimible; pago opcional desde la caja |
| **Gastos y reportes** | Estado de resultados (ventas − costo − gastos − nómina − pérdidas = ganancia neta), más vendidos, «plata quieta» (stock sin rotación), ventas por vendedor y por medio de pago, Excel |
| **Usuarios** | Administrador (todo), Cajero/vendedor (POS, caja, ventas, clientes; no ve costos ni puede vender por debajo del costo) y Bodega (repuestos, compras, ajustes, kardex) |

> Los recibos son soporte de venta; **no reemplazan la factura electrónica DIAN**. La nómina es una ayuda de control; valida la planilla PILA con tu contador.

## Desplegar (todo desde la web)

### 1. Supabase
1. Usa tu proyecto actual o crea uno nuevo.
2. Botón **Connect** → pestaña **Session pooler** (puerto 5432) → copia la cadena y reemplaza `[YOUR-PASSWORD]` por la contraseña real.

### 2. Render
1. **New → Web Service** → conecta el repo `snake1515/top_speed_app`.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app --workers 2 --threads 4 --timeout 60`
4. Variables de entorno (**Environment**):
   - `DATABASE_URL` → la cadena de Supabase del paso 1
   - `SECRET_KEY` → un texto largo cualquiera
   - `ADMIN_USER` → `admin` (o el usuario que quieras)
   - `ADMIN_PASSWORD` → la clave del administrador
   - `PYTHON_VERSION` → `3.12.4`
5. Deploy. Las tablas, los medios de pago y las categorías se crean solos al primer arranque.

### 3. Primeros pasos en la app
1. Entra a `https://TU-APP.onrender.com/login` con el usuario administrador.
2. **Configuración** → datos del negocio, logo, WhatsApp (ahí llegan los pedidos de la tienda), medios de pago con su número de cuenta, impresora de 58 u 80 mm.
3. **Repuestos** → créalos uno por uno o descarga la plantilla e **importa desde Excel** (con stock inicial y costo).
4. **Usuarios** → crea los cajeros y, si pagas comisión, enlázalos en **Empleados**.
5. **Caja y cuadre** → abre la caja con la base y empieza a vender en **Vender (POS)**.

La tienda queda en la raíz del sitio (`https://TU-APP.onrender.com/`). Se puede apagar desde Configuración.

## Cómo se calcula la ganancia
1. Cada compra o entrada recalcula el costo promedio: `(stock × costo actual + cantidad × costo nuevo) ÷ (stock + cantidad)`.
2. Cada venta guarda ese costo en la línea → utilidad bruta = venta sin IVA − costo.
3. Ganancia neta = utilidad bruta − gastos − nómina (costo empresa) − pérdidas por ajustes.
