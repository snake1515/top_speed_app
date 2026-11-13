"""Lógica de negocio: inventario con costo promedio ponderado, caja, ventas, cartera,
compras, ajustes, devoluciones y pedidos de la tienda.

Cada función valida y mueve inventario y dinero. Si algo no cuadra lanza ErrorNegocio
con los mensajes para el usuario. El commit lo hace quien llama (todo o nada).
"""
import json
import secrets
from datetime import timedelta

from models import (CATEGORIAS_CAJA_MENOR, CLASE_EGRESO, Gasto, RESULTADOS_VISITA, Visita, MOTIVOS_AJUSTE, Ajuste, AjusteItem, Cliente, Compra, CompraItem, Cotizacion,
                    Devolucion, MedioPago, MovCaja, Movimiento, PagoCompra, PagoVenta, Pedido,
                    PedidoItem, Producto, Turno, Venta, VentaItem, ahora, config, db, siguiente)

TOL = 1e-6


class ErrorNegocio(Exception):
    def __init__(self, errores):
        self.errores = errores if isinstance(errores, list) else [errores]
        super().__init__("; ".join(self.errores))


def pesos(v):
    return "$ " + f"{round(v or 0):,.0f}".replace(",", ".")


def cant(v):
    v = v or 0
    return f"{v:,.0f}".replace(",", ".") if abs(v - round(v)) < TOL else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ================================================================ inventario
def promedio_ponderado(stock, costo_actual, cantidad, costo_unitario):
    """Costo promedio ponderado al recibir una entrada."""
    stock = max(stock or 0, 0)
    nuevo = stock + cantidad
    if stock <= TOL or nuevo <= TOL:
        return costo_unitario
    return (stock * (costo_actual or 0) + cantidad * costo_unitario) / nuevo


def quitar_de_promedio(stock, costo_prom, cantidad, costo_unit):
    """Inverso: saca una entrada del costo promedio (al anular una compra)."""
    restante = (stock or 0) - cantidad
    if restante <= TOL:
        return costo_prom
    return max(((stock or 0) * (costo_prom or 0) - cantidad * costo_unit) / restante, 0)


def bloquear(ids):
    """Trae los productos bloqueados para que dos cajas no descuenten el mismo stock a la vez."""
    ids = sorted(set(int(i) for i in ids))
    if not ids:
        return {}
    filas = Producto.query.filter(Producto.id.in_(ids)).order_by(Producto.id).with_for_update().all()
    return {p.id: p for p in filas}


def _kardex(p, tipo, cantidad, costo, doc, doc_tipo, doc_id, usuario_id, nota=None, fecha=None):
    db.session.add(Movimiento(fecha=fecha or ahora(), producto=p, tipo=tipo, cantidad=cantidad,
                              costo_unitario=costo, saldo=p.stock, costo_promedio=p.costo_promedio,
                              documento=doc, doc_tipo=doc_tipo, doc_id=doc_id, usuario_id=usuario_id,
                              nota=(nota or "")[:255] or None))


def entrada(p, cantidad, costo_unit, tipo, doc, doc_tipo, doc_id, usuario_id, nota=None, fecha=None,
            actualizar_ultimo=False):
    """Suma al inventario y recalcula el costo promedio."""
    if cantidad <= 0:
        raise ErrorNegocio(f"La cantidad de «{p.nombre}» debe ser mayor a 0.")
    if costo_unit < 0:
        raise ErrorNegocio(f"El costo de «{p.nombre}» no puede ser negativo.")
    if p.virtual:
        p.virtual = False   # llegaron unidades reales: sale de la bodega virtual
    p.costo_promedio = round(promedio_ponderado(p.stock, p.costo_promedio, cantidad, costo_unit), 4)
    p.stock = (p.stock or 0) + cantidad
    if actualizar_ultimo:
        p.ultimo_costo = costo_unit
    _kardex(p, tipo, cantidad, costo_unit, doc, doc_tipo, doc_id, usuario_id, nota, fecha)


def salida(p, cantidad, tipo, doc, doc_tipo, doc_id, usuario_id, nota=None, permitir_negativo=False):
    """Resta del inventario al costo promedio vigente. Devuelve el costo unitario usado."""
    if cantidad <= 0:
        raise ErrorNegocio(f"La cantidad de «{p.nombre}» debe ser mayor a 0.")
    if not permitir_negativo and (p.stock or 0) + TOL < cantidad:
        raise ErrorNegocio(f"No hay suficiente «{p.nombre}» ({p.codigo}): hay {cant(p.stock)} y se piden {cant(cantidad)}.")
    costo = p.costo_promedio or 0
    p.stock = (p.stock or 0) - cantidad
    _kardex(p, tipo, -cantidad, costo, doc, doc_tipo, doc_id, usuario_id, nota)
    return costo


def reservado_por_producto(excluir_pedido=None):
    """Unidades apartadas por pedidos web pendientes y vigentes."""
    cfg = config()
    limite = ahora() - timedelta(hours=cfg.reserva_horas or 48)
    q = (db.session.query(PedidoItem.producto_id, db.func.sum(PedidoItem.cantidad))
         .join(Pedido).filter(Pedido.estado == "pendiente", Pedido.fecha >= limite))
    if excluir_pedido:
        q = q.filter(Pedido.id != excluir_pedido)
    return {pid: float(c or 0) for pid, c in q.group_by(PedidoItem.producto_id).all()}


# ====================================================================== caja
def turno_abierto():
    return Turno.query.filter_by(estado="abierto").order_by(Turno.id.desc()).first()


def efectivo():
    m = MedioPago.query.filter_by(tipo="efectivo").order_by(MedioPago.id).first()
    if m is None:
        m = MedioPago(nombre="Efectivo", tipo="efectivo", orden=0)
        db.session.add(m)
        db.session.flush()
    return m


def abrir_turno(usuario, base, nota=""):
    if turno_abierto():
        raise ErrorNegocio("Ya hay una caja abierta. Ciérrala antes de abrir otra.")
    if base < 0:
        raise ErrorNegocio("La base no puede ser negativa.")
    t = Turno(numero=siguiente("sig_turno"), usuario_id=usuario.id, base=round(base),
              nota_apertura=(nota or "").strip()[:255] or None)
    db.session.add(t)
    db.session.flush()
    return t


def resumen_turno(t):
    """Totales del turno por medio de pago y efectivo que debería haber."""
    movs = t.movimientos.filter_by(anulado=False).all()
    medios = {}
    for m in movs:
        d = medios.setdefault(m.medio_id, {"medio": m.medio, "ingresos": 0.0, "egresos": 0.0})
        d["ingresos" if m.tipo == "ingreso" else "egresos"] += m.valor
    for d in medios.values():
        d["neto"] = d["ingresos"] - d["egresos"]
    ef = efectivo()
    d_ef = medios.get(ef.id, {"ingresos": 0.0, "egresos": 0.0, "neto": 0.0})
    esperado = (t.base or 0) + d_ef["neto"]
    por_origen = {}
    for m in movs:
        k = (m.origen, m.tipo)
        por_origen[k] = por_origen.get(k, 0) + m.valor
    ventas = Venta.query.filter(Venta.turno_id == t.id, Venta.estado != "anulada").all()
    salidas = {}
    for m in movs:
        if m.tipo == "egreso":
            salidas[m.clase] = salidas.get(m.clase, 0) + m.valor
    orden = list(dict.fromkeys(CLASE_EGRESO.values()))
    return {
        "salidas_clase": sorted(salidas.items(), key=lambda kv: orden.index(kv[0]) if kv[0] in orden else 99),
        "medios": sorted(medios.values(), key=lambda d: (not d["medio"].es_efectivo, d["medio"].orden)),
        "efectivo_esperado": round(esperado),
        "ingresos": sum(d["ingresos"] for d in medios.values()),
        "egresos": sum(d["egresos"] for d in medios.values()),
        "por_origen": por_origen,
        "num_ventas": len(ventas),
        "ventas_total": sum(v.total for v in ventas),
        "ventas_credito": sum(max(v.total - v.pagado, 0) for v in ventas if v.credito),
        "movs": movs,
    }


def mov_caja(tipo, valor, medio, concepto, origen, ref_id, usuario_id, turno=None, exigir=True):
    """Registra dinero que entra o sale de la caja abierta."""
    turno = turno or turno_abierto()
    if turno is None:
        if exigir:
            raise ErrorNegocio("No hay caja abierta. Abre la caja para registrar movimientos de dinero.")
        return None
    valor = round(valor)
    if valor <= 0:
        raise ErrorNegocio("El valor debe ser mayor a 0.")
    if tipo == "egreso" and medio.es_efectivo:
        disp = resumen_turno(turno)["efectivo_esperado"]
        if valor > disp + 0.5:
            raise ErrorNegocio(f"En la caja solo debería haber {pesos(disp)} en efectivo; no alcanza para sacar {pesos(valor)}.")
    m = MovCaja(turno_id=turno.id, tipo=tipo, valor=valor, medio=medio, concepto=concepto[:200],
                origen=origen, ref_id=ref_id, usuario_id=usuario_id)
    db.session.add(m)
    db.session.flush()
    return m


def anular_mov_caja(mov_id, usuario_id, concepto):
    """Revierte un movimiento: si su turno sigue abierto lo marca anulado; si ya cerró,
    registra el movimiento contrario en la caja abierta."""
    if not mov_id:
        return
    m = db.session.get(MovCaja, mov_id)
    if m is None or m.anulado:
        return
    if m.turno and m.turno.abierto:
        m.anulado = True
        return
    contrario = "egreso" if m.tipo == "ingreso" else "ingreso"
    mov_caja(contrario, m.valor, m.medio, concepto, "reverso", m.ref_id, usuario_id)


def egreso_caja(usuario, clase, valor, medio, concepto, proveedor=None, compra=None, categoria=None,
                beneficiario=""):
    """Salida de dinero discriminada: pago a proveedor, caja menor u otra salida."""
    concepto = (concepto or "").strip()
    if clase == "proveedor":
        if compra is not None:
            if compra.anulada or compra.saldo <= 0.5:
                raise ErrorNegocio("Esa compra ya no tiene saldo pendiente.")
            m = pagar_compra(compra, usuario, valor, medio, True)
            if concepto:
                m.concepto = (m.concepto + " · " + concepto)[:200]
            return m
        if proveedor is None:
            raise ErrorNegocio("Elige el proveedor al que le pagas.")
        if len(concepto) < 3:
            raise ErrorNegocio("Escribe el concepto del pago (ej. anticipo pedido de llantas).")
        m = mov_caja("egreso", valor, medio, f"Pago a {proveedor.nombre}: {concepto}", "proveedor", None, usuario.id)
        m.proveedor_id = proveedor.id
        return m
    if clase == "caja_menor":
        if categoria not in CATEGORIAS_CAJA_MENOR:
            raise ErrorNegocio("Elige en qué se gastó la caja menor.")
        if len(concepto) < 3:
            raise ErrorNegocio("Escribe el detalle del gasto de caja menor.")
        g = Gasto(categoria=f"Caja menor · {categoria}"[:60], descripcion=concepto[:200], valor=round(valor),
                  medio=medio, usuario_id=usuario.id)
        db.session.add(g)
        db.session.flush()
        m = mov_caja("egreso", valor, medio, f"Caja menor · {categoria}: {concepto}", "caja_menor", g.id, usuario.id)
        m.categoria = categoria
        m.beneficiario = (beneficiario or "").strip()[:120] or None
        m.vale = siguiente("sig_vale")
        g.mov_caja_id = m.id
        return m
    if clase == "otro":
        if len(concepto) < 3:
            raise ErrorNegocio("Escribe el concepto (ej. consignación al banco, retiro del dueño).")
        return mov_caja("egreso", valor, medio, concepto, "manual", None, usuario.id)
    raise ErrorNegocio("Elige el tipo de salida: pago a proveedor, caja menor u otra.")


def anular_egreso(m, usuario):
    if m.anulado:
        raise ErrorNegocio("Ese movimiento ya estaba anulado.")
    if m.origen not in ("manual", "proveedor", "caja_menor") or not m.turno or not m.turno.abierto:
        raise ErrorNegocio("Aquí solo se anulan entradas y salidas manuales, pagos a proveedor sin compra y caja menor "
                           "de la caja abierta. Los pagos de compras se anulan desde la compra.")
    m.anulado = True
    if m.origen == "caja_menor" and m.ref_id:
        g = db.session.get(Gasto, m.ref_id)
        if g:
            g.anulado = True


def cerrar_turno(t, usuario, contado, denominaciones, nota=""):
    if not t.abierto:
        raise ErrorNegocio("Esta caja ya estaba cerrada.")
    if contado < 0:
        raise ErrorNegocio("El efectivo contado no puede ser negativo.")
    r = resumen_turno(t)
    t.efectivo_esperado = r["efectivo_esperado"]
    t.efectivo_contado = round(contado)
    t.diferencia = round(contado - r["efectivo_esperado"])
    t.denominaciones = json.dumps(denominaciones or {})
    t.resumen = json.dumps([{"medio": d["medio"].nombre, "tipo": d["medio"].tipo, "ingresos": d["ingresos"],
                             "egresos": d["egresos"], "neto": d["neto"]} for d in r["medios"]])
    t.nota_cierre = (nota or "").strip()[:300] or None
    t.cierre = ahora()
    t.cerrado_por_id = usuario.id
    t.estado = "cerrado"
    if abs(t.diferencia) > 0 and not t.nota_cierre:
        raise ErrorNegocio("Hay diferencia en el efectivo: escribe una observación explicando el sobrante o faltante.")
    return t


# =================================================================== ventas
def registrar_venta(usuario, items, pagos, cliente=None, cliente_nombre="", cliente_doc="", cliente_tel="",
                    lista="detal", descuento=0.0, credito=False, nota="", pedido=None, cotizacion=None):
    """items: [{producto_id, cantidad, precio}] · pagos: [{medio_id, valor, referencia}]"""
    cfg = config()
    errores = []
    turno = turno_abierto()
    if turno is None:
        raise ErrorNegocio("La caja está cerrada. Ábrela para poder vender.")
    limpios = []
    for it in items or []:
        try:
            pid, q, pr = int(it["producto_id"]), float(it["cantidad"]), float(it["precio"])
        except (KeyError, TypeError, ValueError):
            errores.append("Hay una línea del carrito con datos inválidos.")
            continue
        if q <= 0:
            errores.append("Todas las cantidades deben ser mayores a 0.")
        if pr < 0:
            errores.append("Ningún precio puede ser negativo.")
        limpios.append((pid, q, round(pr)))
    if not limpios:
        errores.append("El carrito está vacío.")
    if errores:
        raise ErrorNegocio(sorted(set(errores)))
    prods = bloquear([pid for pid, _, _ in limpios])
    # junta líneas repetidas del mismo producto y precio
    agrupado = {}
    for pid, q, pr in limpios:
        if pid not in prods or not prods[pid].activo:
            raise ErrorNegocio("Uno de los productos ya no existe o está inactivo.")
        agrupado[(pid, pr)] = agrupado.get((pid, pr), 0) + q
    bruto = sum(q * pr for (pid, pr), q in agrupado.items())
    descuento = round(max(descuento or 0, 0))
    if descuento > bruto:
        raise ErrorNegocio("El descuento no puede ser mayor que el total.")
    total = round(bruto - descuento)
    if not usuario.puede("admin"):
        for (pid, pr), q in agrupado.items():
            p = prods[pid]
            factor = (1 - descuento / bruto) if bruto else 1
            if p.costo_ref and pr * factor + 0.5 < p.costo_ref:
                errores.append(f"«{p.nombre}» quedaría por debajo del costo. Solo un administrador puede vender así.")
        if errores:
            raise ErrorNegocio(errores)

    # pagos
    medios = {m.id: m for m in MedioPago.query.all()}
    pagos_ok = []
    for pg in pagos or []:
        try:
            mid, val = int(pg["medio_id"]), round(float(pg["valor"]))
        except (KeyError, TypeError, ValueError):
            raise ErrorNegocio("Hay un pago con datos inválidos.")
        if val <= 0:
            continue
        if mid not in medios:
            raise ErrorNegocio("Medio de pago desconocido.")
        pagos_ok.append((medios[mid], val, (pg.get("referencia") or "").strip()[:80] or None))
    recibido = sum(v for _, v, _ in pagos_ok)
    no_efectivo = sum(v for m, v, _ in pagos_ok if not m.es_efectivo)
    efectivo_rec = recibido - no_efectivo
    if no_efectivo > total + 0.5:
        raise ErrorNegocio("Lo pagado por transferencia o tarjeta supera el total; el cambio solo se da en efectivo.")
    cambio = max(recibido - total, 0)
    if cambio > efectivo_rec + 0.5:
        raise ErrorNegocio("El cambio no puede ser mayor que el efectivo recibido.")
    pagado = min(recibido, total)
    if pagado + 0.5 < total:
        if not credito:
            raise ErrorNegocio(f"Falta por pagar {pesos(total - pagado)}. Completa el pago o marca la venta a crédito.")
        if cliente is None:
            raise ErrorNegocio("Para vender a crédito elige un cliente registrado.")
        if cliente.cupo:
            deuda = saldo_cliente(cliente)
            if deuda + (total - pagado) > cliente.cupo + 0.5:
                raise ErrorNegocio(f"{cliente.nombre} superaría su cupo de {pesos(cliente.cupo)} (debe {pesos(deuda)}).")
    else:
        credito = False

    v = Venta(numero=siguiente("sig_venta"), token=secrets.token_urlsafe(16), cliente=cliente,
              cliente_nombre=((cliente.nombre if cliente else cliente_nombre) or "").strip()[:140] or None,
              cliente_doc=((cliente.documento if cliente else cliente_doc) or "").strip()[:30] or None,
              cliente_tel=((cliente.telefono if cliente else cliente_tel) or "").strip()[:40] or None,
              lista=lista if lista in ("detal", "mayor", "taller") else "detal", bruto=bruto, descuento=descuento,
              total=total, credito=credito, estado="credito" if credito else "pagada",
              origen=("preventa" if pedido.es_preventa else "tienda") if pedido else "pos",
              pedido_id=pedido.id if pedido else None,
              nota=(nota or "").strip()[:300] or None, turno_id=turno.id, usuario_id=usuario.id,
              vendedor_id=(pedido.vendedor_id if pedido and pedido.vendedor_id else usuario.id))
    if credito and cliente:
        v.vence = ahora().date() + timedelta(days=cliente.plazo_dias or 30)
    db.session.add(v)
    db.session.flush()
    doc = v.folio
    costo_total = iva_total = 0.0
    lineas = list(agrupado.items())
    acumulado_desc = 0
    for i, ((pid, pr), q) in enumerate(lineas):
        p = prods[pid]
        linea_total = q * pr
        if i == len(lineas) - 1:
            desc_linea = descuento - acumulado_desc
        else:
            desc_linea = round(descuento * linea_total / bruto) if bruto else 0
            acumulado_desc += desc_linea
        neto = linea_total - desc_linea
        iva_pct = p.iva_pct if cfg.cobra_iva else 0
        iva_total += neto - neto / (1 + (iva_pct or 0) / 100)
        if p.virtual:
            # bodega virtual: no hay unidades físicas; queda "por surtir" con el costo virtual
            costo = p.costo_virtual or 0
            pend = q
        else:
            costo = salida(p, q, "venta", doc, "venta", v.id, usuario.id, permitir_negativo=cfg.permitir_sin_stock)
            pend = 0
        costo_total += costo * q
        db.session.add(VentaItem(venta_id=v.id, producto=p, codigo=p.codigo, nombre=p.nombre, cantidad=q,
                                 precio=pr, total=linea_total, neto=neto, iva_pct=iva_pct or 0,
                                 costo_unitario=costo, por_surtir=pend, costo_estimado=costo if pend else 0))
    v.costo = round(costo_total, 2)
    v.iva = round(iva_total)
    # registra el dinero: el efectivo entra neto del cambio
    resto_cambio = cambio
    aplicado = 0
    for m, val, ref in sorted(pagos_ok, key=lambda x: not x[0].es_efectivo):
        entra = val
        if m.es_efectivo and resto_cambio:
            quita = min(resto_cambio, val)
            entra -= quita
            resto_cambio -= quita
        entra = min(entra, total - aplicado)
        if entra <= 0:
            continue
        aplicado += entra
        mov = mov_caja("ingreso", entra, m, f"Venta {doc}" + (f" · {v.cliente_nombre}" if v.cliente_nombre else ""),
                       "venta", v.id, usuario.id, turno)
        db.session.add(PagoVenta(venta_id=v.id, tipo="contado", medio=m, valor=entra,
                                 recibido=val if m.es_efectivo else None, referencia=ref, turno_id=turno.id,
                                 mov_caja_id=mov.id, usuario_id=usuario.id))
    v.pagado = aplicado
    v.cambio = cambio
    if pedido:
        pedido.estado = "confirmado"
        pedido.venta_id = v.id
    if cotizacion:
        cotizacion.estado = "vendida"
        cotizacion.venta_id = v.id
    return v


def saldo_cliente(cliente):
    return sum(v.saldo for v in Venta.query.filter(Venta.cliente_id == cliente.id, Venta.estado == "credito").all())


def registrar_abono(venta, usuario, valor, medio, referencia=""):
    if venta.anulada:
        raise ErrorNegocio("La venta está anulada.")
    valor = round(valor)
    if valor <= 0:
        raise ErrorNegocio("El abono debe ser mayor a 0.")
    if valor > venta.saldo + 0.5:
        raise ErrorNegocio(f"El abono supera el saldo ({pesos(venta.saldo)}).")
    turno = turno_abierto()
    mov = mov_caja("ingreso", valor, medio, f"Abono {venta.folio} · {venta.cliente_nombre or ''}", "abono",
                   venta.id, usuario.id, turno)
    db.session.add(PagoVenta(venta_id=venta.id, tipo="abono", medio=medio, valor=valor,
                             referencia=(referencia or "").strip()[:80] or None, turno_id=turno.id,
                             mov_caja_id=mov.id, usuario_id=usuario.id))
    venta.pagado = (venta.pagado or 0) + valor
    if venta.saldo <= 0:
        venta.estado = "pagada"
    return mov


def anular_venta(venta, usuario, motivo):
    if venta.anulada:
        raise ErrorNegocio("Esta venta ya estaba anulada.")
    if not (motivo or "").strip():
        raise ErrorNegocio("Escribe el motivo de la anulación.")
    if venta.devuelto:
        raise ErrorNegocio("La venta ya tiene devoluciones; usa devolución para el resto de productos.")
    pendientes = [p for p in venta.pagos if not p.anulado]
    cerradas = [p for p in pendientes if not (db.session.get(Turno, p.turno_id) or Turno()).abierto]
    if cerradas and not turno_abierto():
        raise ErrorNegocio("La venta se cobró en una caja ya cerrada: abre la caja para registrar el reembolso.")
    prods = bloquear([it.producto_id for it in venta.items])
    for it in venta.items:
        q = it.cantidad - (it.devuelto or 0) - (it.por_surtir or 0)   # lo por surtir nunca salió de bodega
        it.por_surtir = 0
        if q > TOL:
            entrada(prods[it.producto_id], q, it.costo_unitario, "anula_venta", venta.folio, "venta", venta.id,
                    usuario.id, nota=motivo)
    for p in pendientes:
        anular_mov_caja(p.mov_caja_id, usuario.id, f"Reembolso por anulación {venta.folio}")
        p.anulado = True
    venta.estado = "anulada"
    venta.anulada_fecha = ahora()
    venta.anulada_motivo = motivo.strip()[:255]
    if venta.pedido_id:
        ped = db.session.get(Pedido, venta.pedido_id)
        if ped:
            ped.estado = "cancelado"
            ped.gestion_nota = f"Venta {venta.folio} anulada: {motivo.strip()[:200]}"


def devolver(venta, usuario, cantidades, medio, motivo):
    """Devolución parcial: cantidades = {venta_item_id: cantidad}."""
    if venta.anulada:
        raise ErrorNegocio("La venta está anulada.")
    if not (motivo or "").strip():
        raise ErrorNegocio("Escribe el motivo de la devolución (ej. no le sirvió a la moto).")
    items = {it.id: it for it in venta.items}
    lineas = []
    for iid, q in cantidades.items():
        it = items.get(int(iid))
        if it is None or q <= 0:
            continue
        disp = it.cantidad - (it.devuelto or 0)
        if q > disp + TOL:
            raise ErrorNegocio(f"De «{it.nombre}» solo quedan {cant(disp)} por devolver.")
        lineas.append((it, q))
    if not lineas:
        raise ErrorNegocio("Indica cuántas unidades devuelve el cliente.")
    prods = bloquear([it.producto_id for it, _ in lineas])
    valor = iva = costo = 0.0
    detalle = []
    for it, q in lineas:
        v_l = it.neto_unitario * q
        valor += v_l
        iva += v_l - v_l / (1 + (it.iva_pct or 0) / 100)
        costo += it.costo_unitario * q
        pend = min(q, it.por_surtir or 0)   # primero se cancela lo que aún no se había surtido
        it.por_surtir = (it.por_surtir or 0) - pend
        if q - pend > TOL:
            entrada(prods[it.producto_id], q - pend, it.costo_unitario, "devolucion", venta.folio, "venta", venta.id,
                    usuario.id, nota=motivo)
        it.devuelto = (it.devuelto or 0) + q
        detalle.append({"codigo": it.codigo, "nombre": it.nombre, "cantidad": q, "valor": round(v_l)})
    valor = round(valor)
    saldo_antes = venta.saldo
    venta.devuelto = (venta.devuelto or 0) + valor
    venta.iva_devuelto = (venta.iva_devuelto or 0) + round(iva)
    venta.costo_devuelto = (venta.costo_devuelto or 0) + costo
    # primero se descuenta de lo que debe; lo demás se le devuelve en dinero
    reembolso = max(valor - saldo_antes, 0)
    if reembolso > (venta.pagado or 0):
        reembolso = venta.pagado or 0
    dev = Devolucion(venta_id=venta.id, valor=valor, iva=round(iva), costo=costo, reembolso=reembolso,
                     medio=medio if reembolso else None, motivo=motivo.strip()[:255],
                     detalle=json.dumps(detalle), usuario_id=usuario.id)
    db.session.add(dev)
    db.session.flush()
    if reembolso:
        mov_caja("egreso", reembolso, medio, f"Devolución {venta.folio}", "devolucion", venta.id, usuario.id)
        venta.pagado = (venta.pagado or 0) - reembolso
    if venta.saldo <= 0 and venta.estado == "credito":
        venta.estado = "pagada"
    return dev


# ================================================================== compras
def registrar_compra(usuario, proveedor, items, factura="", nota="", fecha=None, pago=None, plazo_dias=0):
    """items: [{producto_id, cantidad, costo}] · pago: {modo: contado|credito, medio, desde_caja, valor}"""
    errores = []
    limpios = []
    for it in items or []:
        try:
            pid, q, c = int(it["producto_id"]), float(it["cantidad"]), float(it["costo"])
        except (KeyError, TypeError, ValueError):
            errores.append("Hay una línea con datos inválidos.")
            continue
        if q <= 0:
            errores.append("Las cantidades deben ser mayores a 0.")
        if c < 0:
            errores.append("Los costos no pueden ser negativos.")
        limpios.append((pid, q, c))
    if not limpios:
        errores.append("Agrega al menos un repuesto a la compra.")
    if errores:
        raise ErrorNegocio(sorted(set(errores)))
    prods = bloquear([pid for pid, _, _ in limpios])
    c = Compra(numero=siguiente("sig_compra"), fecha=fecha or ahora(), proveedor=proveedor,
               factura=(factura or "").strip()[:60] or None, nota=(nota or "").strip()[:300] or None,
               usuario_id=usuario.id)
    db.session.add(c)
    db.session.flush()
    doc = f"Compra C-{c.numero:05d}" + (f" · Fact. {c.factura}" if c.factura else "")
    total = 0.0
    for pid, q, cu in limpios:
        p = prods.get(pid)
        if p is None:
            raise ErrorNegocio("Uno de los productos no existe.")
        entrada(p, q, cu, "compra", doc[:60], "compra", c.id, usuario.id, fecha=c.fecha, actualizar_ultimo=True)
        c.items.append(CompraItem(producto=p, cantidad=q, costo_unitario=cu))
        total += q * cu
        surtir_pendientes(p, usuario.id)
    c.total = round(total)
    pago = pago or {}
    if pago.get("modo") == "credito":
        c.vence = (c.fecha.date() + timedelta(days=int(plazo_dias or 30)))
    elif c.total > 0:
        pagar_compra(c, usuario, c.total, pago.get("medio") or efectivo(), bool(pago.get("desde_caja")))
    return c


def pendientes_por_surtir(producto_id=None):
    q = (VentaItem.query.join(Venta).filter(VentaItem.por_surtir > TOL, Venta.estado != "anulada")
         .order_by(Venta.fecha, VentaItem.id))
    if producto_id:
        q = q.filter(VentaItem.producto_id == producto_id)
    return q.all()


def surtir_pendientes(p, usuario_id):
    """Con las unidades que acaban de llegar, entrega las ventas de la bodega virtual que estaban pendientes
    (las más antiguas primero) y les cambia el costo virtual por el costo real."""
    surtidas = 0.0
    for it in pendientes_por_surtir(p.id):
        n = min(it.por_surtir, p.stock or 0)
        if n <= TOL:
            break
        v = it.venta
        real = salida(p, n, "venta", v.folio, "venta", v.id, usuario_id, nota="Surtido de venta de bodega virtual")
        ajuste = n * (real - (it.costo_estimado or 0))
        it.costo_unitario = (it.costo_unitario or 0) + (ajuste / it.cantidad if it.cantidad else 0)
        v.costo = round((v.costo or 0) + ajuste, 2)
        it.por_surtir -= n
        surtidas += n
    return surtidas


def pagar_compra(c, usuario, valor, medio, desde_caja):
    if c.anulada:
        raise ErrorNegocio("La compra está anulada.")
    valor = round(valor)
    if valor <= 0:
        raise ErrorNegocio("El pago debe ser mayor a 0.")
    if valor > c.saldo + 0.5:
        raise ErrorNegocio(f"El pago supera el saldo ({pesos(c.saldo)}).")
    mov = None
    if desde_caja:
        prov = c.proveedor.nombre if c.proveedor else "proveedor"
        mov = mov_caja("egreso", valor, medio, f"Pago compra C-{c.numero:05d} · {prov}", "compra", c.id, usuario.id)
    c.pagos.append(PagoCompra(valor=valor, medio=medio, desde_caja=bool(mov), mov_caja_id=mov.id if mov else None,
                              usuario_id=usuario.id))
    c.pagado = (c.pagado or 0) + valor
    if mov:
        mov.proveedor_id = c.proveedor_id
    return mov


def anular_compra(c, usuario):
    if c.anulada:
        raise ErrorNegocio("Esta compra ya estaba anulada.")
    prods = bloquear([it.producto_id for it in c.items])
    for it in c.items:
        p = prods[it.producto_id]
        if (p.stock or 0) + TOL < it.cantidad:
            raise ErrorNegocio(f"No se puede anular: de «{p.nombre}» ya se vendieron unidades de esta compra "
                               f"(quedan {cant(p.stock)}). Haz un ajuste en su lugar.")
    for it in c.items:
        p = prods[it.producto_id]
        p.costo_promedio = round(quitar_de_promedio(p.stock, p.costo_promedio, it.cantidad, it.costo_unitario), 4)
        p.stock -= it.cantidad
        _kardex(p, "anula_compra", -it.cantidad, it.costo_unitario, f"Compra C-{c.numero:05d}", "compra", c.id,
                usuario.id)
    for pg in c.pagos:
        if pg.desde_caja:
            anular_mov_caja(pg.mov_caja_id, usuario.id, f"Reverso pago compra C-{c.numero:05d}")
    c.anulada = True


# ================================================================== ajustes
def registrar_ajuste(usuario, motivo, items, nota=""):
    if motivo not in MOTIVOS_AJUSTE:
        raise ErrorNegocio("Elige el motivo del ajuste.")
    sentido = MOTIVOS_AJUSTE[motivo][1]
    limpios = []
    for it in items or []:
        try:
            pid, q = int(it["producto_id"]), float(it["cantidad"])
            cu = float(it.get("costo") or 0)
        except (KeyError, TypeError, ValueError):
            raise ErrorNegocio("Hay una línea con datos inválidos.")
        if q <= 0:
            raise ErrorNegocio("Las cantidades deben ser mayores a 0.")
        limpios.append((pid, q, cu))
    if not limpios:
        raise ErrorNegocio("Agrega al menos un repuesto.")
    prods = bloquear([pid for pid, _, _ in limpios])
    a = Ajuste(numero=siguiente("sig_ajuste"), motivo=motivo, nota=(nota or "").strip()[:300] or None,
               usuario_id=usuario.id)
    db.session.add(a)
    db.session.flush()
    doc = f"Ajuste A-{a.numero:05d}"
    for pid, q, cu in limpios:
        p = prods[pid]
        if sentido == "entrada":
            costo = cu if cu > 0 else (p.costo_promedio or p.ultimo_costo or p.costo_virtual or 0)
            entrada(p, q, costo, "inicial" if motivo == "inicial" else "ajuste_entrada", doc, "ajuste", a.id,
                    usuario.id, nota=MOTIVOS_AJUSTE[motivo][0])
            surtir_pendientes(p, usuario.id)
        else:
            costo = salida(p, q, "ajuste_salida", doc, "ajuste", a.id, usuario.id, nota=MOTIVOS_AJUSTE[motivo][0])
        a.items.append(AjusteItem(producto=p, cantidad=q, costo_unitario=costo))
    return a


def anular_ajuste(a, usuario):
    if a.anulado:
        raise ErrorNegocio("Este ajuste ya estaba anulado.")
    prods = bloquear([it.producto_id for it in a.items])
    doc = f"Ajuste A-{a.numero:05d}"
    for it in a.items:
        p = prods[it.producto_id]
        if a.sentido == "entrada":
            if (p.stock or 0) + TOL < it.cantidad:
                raise ErrorNegocio(f"No se puede anular: de «{p.nombre}» ya salieron unidades.")
            p.costo_promedio = round(quitar_de_promedio(p.stock, p.costo_promedio, it.cantidad, it.costo_unitario), 4)
            p.stock -= it.cantidad
            _kardex(p, "anula_ajuste", -it.cantidad, it.costo_unitario, doc, "ajuste", a.id, usuario.id)
        else:
            entrada(p, it.cantidad, it.costo_unitario, "anula_ajuste", doc, "ajuste", a.id, usuario.id)
    a.anulado = True


# ============================================================ tienda online
def crear_pedido(datos, items):
    """Pedido desde la tienda pública. Los precios los pone el servidor (precio detal)."""
    cfg = config()
    errores = []
    nombre = (datos.get("nombre") or "").strip()
    tel = "".join(ch for ch in (datos.get("telefono") or "") if ch.isdigit())
    entrega = datos.get("entrega") if datos.get("entrega") in ("recoger", "domicilio", "envio") else "recoger"
    if len(nombre) < 3:
        errores.append("Escribe tu nombre.")
    if not 10 <= len(tel) <= 13:
        errores.append("Escribe un número de WhatsApp válido (10 dígitos).")
    if entrega != "recoger" and len((datos.get("direccion") or "").strip()) < 5:
        errores.append("Escribe la dirección de entrega.")
    if not isinstance(items, list) or not items:
        errores.append("Tu carrito está vacío.")
    elif len(items) > 60:
        errores.append("Demasiados productos en un solo pedido; escríbenos por WhatsApp.")
    if errores:
        raise ErrorNegocio(errores)
    pedidos = {}
    for it in items:
        try:
            pid, q = int(it["producto_id"]), int(it["cantidad"])
        except (KeyError, TypeError, ValueError):
            raise ErrorNegocio("Hay un producto inválido en el carrito.")
        if 0 < q <= 500:
            pedidos[pid] = pedidos.get(pid, 0) + q
    prods = bloquear(list(pedidos))
    reservado = reservado_por_producto()
    p_items = []
    total = 0.0
    for pid, q in pedidos.items():
        p = prods.get(pid)
        if p is None or not p.activo or not p.en_tienda or not p.precio_detal:
            raise ErrorNegocio("Uno de los productos ya no está disponible. Actualiza la página.")
        disp = p.disponible(reservado)
        if q > disp + TOL:
            errores.append(f"De «{p.nombre}» solo hay {cant(max(disp, 0))} disponibles.")
            continue
        p_items.append(PedidoItem(producto=p, codigo=p.codigo, nombre=p.nombre, cantidad=q, precio=p.precio_detal))
        total += q * p.precio_detal
    if errores:
        raise ErrorNegocio(errores)
    if not p_items:
        raise ErrorNegocio("Tu carrito está vacío.")
    ped = Pedido(numero=siguiente("sig_pedido"), token=secrets.token_urlsafe(16), nombre=nombre[:140],
                 telefono=tel, documento=(datos.get("documento") or "").strip()[:30] or None,
                 email=(datos.get("email") or "").strip()[:120] or None, entrega=entrega,
                 direccion=(datos.get("direccion") or "").strip()[:200] or None,
                 ciudad=(datos.get("ciudad") or "").strip()[:80] or None,
                 medio_preferido=(datos.get("medio") or "").strip()[:60] or None,
                 nota=(datos.get("nota") or "").strip()[:400] or None, total=round(total), items=p_items)
    db.session.add(ped)
    db.session.flush()
    _ = cfg
    return ped


def pedido_vencido(ped):
    cfg = config()
    return ped.estado == "pendiente" and ped.fecha < ahora() - timedelta(hours=cfg.reserva_horas or 48)


def guardar_cotizacion(usuario, items, cliente=None, cliente_nombre="", cliente_tel="", lista="detal",
                       descuento=0.0, nota="", vigencia=8):
    from models import CotizacionItem
    limpios = []
    for it in items or []:
        try:
            limpios.append((int(it["producto_id"]), float(it["cantidad"]), round(float(it["precio"]))))
        except (KeyError, TypeError, ValueError):
            raise ErrorNegocio("Hay una línea inválida.")
    if not limpios:
        raise ErrorNegocio("El carrito está vacío.")
    prods = {p.id: p for p in Producto.query.filter(Producto.id.in_([x[0] for x in limpios])).all()}
    c = Cotizacion(numero=siguiente("sig_cotizacion"), cliente=cliente,
                   cliente_nombre=((cliente.nombre if cliente else cliente_nombre) or "").strip()[:140] or None,
                   cliente_tel=((cliente.telefono if cliente else cliente_tel) or "").strip()[:40] or None,
                   lista=lista, descuento=round(max(descuento or 0, 0)), nota=(nota or "").strip()[:300] or None,
                   vigencia_dias=int(vigencia or 8), usuario_id=usuario.id)
    total = 0
    for pid, q, pr in limpios:
        p = prods.get(pid)
        if p is None or q <= 0:
            continue
        c.items.append(CotizacionItem(producto=p, nombre=p.nombre, codigo=p.codigo, cantidad=q, precio=pr))
        total += q * pr
    c.total = max(round(total - c.descuento), 0)
    db.session.add(c)
    db.session.flush()
    return c


__all__ = ["ErrorNegocio", "Cliente"]


# ================================================================ preventa
def registrar_visita(usuario, cliente, resultado, nota="", lat=None, lng=None, pedido=None):
    if resultado not in RESULTADOS_VISITA:
        raise ErrorNegocio("Elige el resultado de la visita.")
    v = Visita(usuario_id=usuario.id, cliente_id=cliente.id, ruta_id=cliente.ruta_id, resultado=resultado,
               nota=(nota or "").strip()[:300] or None, pedido_id=pedido.id if pedido else None,
               lat=lat, lng=lng)
    db.session.add(v)
    return v


def crear_pedido_preventa(usuario, cliente, items, nota="", lat=None, lng=None):
    """Pedido tomado en el local del cliente. El precio sale de la lista del cliente (no lo pone el preventista)."""
    if cliente is None or not cliente.activo:
        raise ErrorNegocio("Elige el cliente.")
    pedidos = {}
    for it in items or []:
        try:
            pid, q = int(it["producto_id"]), float(it["cantidad"])
        except (KeyError, TypeError, ValueError):
            raise ErrorNegocio("Hay una línea inválida en el pedido.")
        if q <= 0:
            raise ErrorNegocio("Las cantidades deben ser mayores a 0.")
        pedidos[pid] = pedidos.get(pid, 0) + q
    if not pedidos:
        raise ErrorNegocio("El pedido está vacío.")
    prods = bloquear(list(pedidos))
    reservado = reservado_por_producto()
    errores, p_items, total = [], [], 0.0
    for pid, q in pedidos.items():
        p = prods.get(pid)
        if p is None or not p.activo:
            raise ErrorNegocio("Uno de los repuestos ya no está disponible.")
        disp = p.disponible(reservado)
        if q > disp + TOL:
            errores.append(f"De «{p.nombre}» solo hay {cant(max(disp, 0))} disponibles.")
            continue
        precio = p.precio(cliente.lista)
        p_items.append(PedidoItem(producto=p, codigo=p.codigo, nombre=p.nombre, cantidad=q, precio=precio))
        total += q * precio
    if errores:
        raise ErrorNegocio(errores)
    ped = Pedido(numero=siguiente("sig_pedido"), token=secrets.token_urlsafe(16), origen="preventa",
                 cliente_id=cliente.id, vendedor_id=usuario.id, lista=cliente.lista, nombre=cliente.nombre[:140],
                 telefono="".join(ch for ch in (cliente.telefono or "") if ch.isdigit())[:20],
                 documento=cliente.documento, entrega="domicilio", direccion=cliente.direccion,
                 ciudad=cliente.barrio, nota=(nota or "").strip()[:400] or None, total=round(total), items=p_items)
    db.session.add(ped)
    db.session.flush()
    registrar_visita(usuario, cliente, "pedido", nota, lat, lng, ped)
    return ped
