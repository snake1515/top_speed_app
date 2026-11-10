"""Liquidación de nómina: sueldo fijo, comisión sobre ventas, aportes y prestaciones.

Porcentajes de ley en Colombia (2026), editables desde Configuración. Es una ayuda para
controlar el costo de personal; no reemplaza la planilla PILA ni la liquidación del contador.
"""
from datetime import datetime, timedelta

from models import PagoNomina, Venta, ahora, db
from services import ErrorNegocio, mov_caja, anular_mov_caja

SALUD_EMPLEADO = 4.0
PENSION_EMPLEADO = 4.0
PENSION_EMPLEADOR = 12.0
CAJA = 4.0
SALUD_EMPLEADOR = 8.5
CESANTIAS = 8.33
INTERESES_CES = 1.0
PRIMA = 8.33
VACACIONES = 4.17


def ventas_del_periodo(empleado, desde, hasta):
    """Ventas netas sin IVA hechas por el usuario del empleado en el periodo."""
    if not empleado.usuario_id:
        return 0.0
    ini = datetime.combine(desde, datetime.min.time())
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time())
    vs = Venta.query.filter(Venta.usuario_id == empleado.usuario_id, Venta.estado != "anulada",
                            Venta.fecha >= ini, Venta.fecha < fin).all()
    return sum(v.neto - ((v.iva or 0) - (v.iva_devuelto or 0)) for v in vs)


def calcular(empleado, cfg, dias, ventas_base, horas_extra=0.0, bonificaciones=0.0, otras_deducciones=0.0,
             seguridad_social=True, prestaciones=True):
    dias = max(min(int(dias or 0), 31), 0)
    basico = (empleado.salario or 0) * dias / 30 if empleado.tipo_pago in ("fijo", "mixto") else 0.0
    comisiones = ventas_base * (empleado.comision_pct or 0) / 100 if empleado.tipo_pago in ("comision", "mixto") else 0.0
    ibc = basico + comisiones + horas_extra
    mensual = ibc * 30 / dias if dias else 0
    aux = 0.0
    if empleado.aux_transporte and dias and mensual <= 2 * (cfg.smmlv or 0):
        aux = (cfg.aux_transporte or 0) * dias / 30
    salud = pension = aportes = 0.0
    if seguridad_social:
        salud = ibc * SALUD_EMPLEADO / 100
        pension = ibc * PENSION_EMPLEADO / 100
        pct = PENSION_EMPLEADOR + (cfg.nom_arl_pct or 0) + CAJA
        if cfg.nom_salud_empleador:
            pct += SALUD_EMPLEADOR
        aportes = ibc * pct / 100
    prov = 0.0
    if prestaciones:
        prov = (ibc + aux) * (CESANTIAS + INTERESES_CES + PRIMA) / 100 + (basico + comisiones) * VACACIONES / 100
    devengado = basico + aux + comisiones + horas_extra + bonificaciones
    deducciones = salud + pension + otras_deducciones
    r = dict(dias=dias, basico=basico, aux_transporte=aux, ventas_base=ventas_base, comisiones=comisiones,
             horas_extra=horas_extra, bonificaciones=bonificaciones, deduccion_salud=salud,
             deduccion_pension=pension, otras_deducciones=otras_deducciones, devengado=devengado,
             deducciones=deducciones, neto=devengado - deducciones, aportes=aportes, provisiones=prov,
             costo_empresa=devengado + aportes + prov)
    return {k: (round(v) if isinstance(v, float) else v) for k, v in r.items()}


def registrar(empleado, cfg, desde, hasta, fecha_pago, dias, horas_extra=0.0, bonificaciones=0.0,
              otras_deducciones=0.0, deducciones_detalle="", seguridad_social=True, prestaciones=True,
              medio=None, desde_caja=False, nota="", usuario_id=None):
    errores = []
    if not empleado:
        errores.append("Elige el empleado.")
    if not desde or not hasta or hasta < desde:
        errores.append("Revisa el periodo: la fecha final debe ser igual o posterior a la inicial.")
    if min(horas_extra, bonificaciones, otras_deducciones) < 0:
        errores.append("Los valores no pueden ser negativos.")
    if otras_deducciones > 0 and not (deducciones_detalle or "").strip():
        errores.append("Escribe el concepto de las otras deducciones (anticipo, préstamo…).")
    if errores:
        raise ErrorNegocio(errores)
    cruce = PagoNomina.query.filter(PagoNomina.empleado_id == empleado.id, PagoNomina.anulada.is_(False),
                                    PagoNomina.desde <= hasta, PagoNomina.hasta >= desde).first()
    if cruce:
        raise ErrorNegocio(f"Ya hay una nómina de {empleado.nombre} que cruza esas fechas "
                           f"({cruce.desde:%d/%m} a {cruce.hasta:%d/%m}). Anúlala o cambia el periodo.")
    base = ventas_del_periodo(empleado, desde, hasta)
    c = calcular(empleado, cfg, dias, base, horas_extra, bonificaciones, otras_deducciones,
                 seguridad_social, prestaciones)
    if c["devengado"] <= 0:
        raise ErrorNegocio("No hay nada que pagar en ese periodo.")
    if c["neto"] < 0:
        raise ErrorNegocio("Las deducciones superan lo devengado.")
    pago = PagoNomina(empleado=empleado, desde=desde, hasta=hasta, fecha_pago=fecha_pago or ahora().date(),
                      medio=medio, nota=(nota or "").strip()[:255] or None, usuario_id=usuario_id,
                      deducciones_detalle=(deducciones_detalle or "").strip()[:255] or None,
                      **{k: c[k] for k in ("dias", "basico", "aux_transporte", "ventas_base", "comisiones",
                                           "horas_extra", "bonificaciones", "deduccion_salud",
                                           "deduccion_pension", "otras_deducciones", "neto", "aportes",
                                           "provisiones", "costo_empresa")})
    db.session.add(pago)
    db.session.flush()
    if desde_caja and medio and pago.neto > 0:
        mov = mov_caja("egreso", pago.neto, medio, f"Nómina {empleado.nombre} {desde:%d/%m}–{hasta:%d/%m}",
                       "nomina", pago.id, usuario_id)
        pago.mov_caja_id = mov.id
    return pago


def anular(pago, usuario_id):
    if pago.anulada:
        raise ErrorNegocio("Esta nómina ya estaba anulada.")
    anular_mov_caja(pago.mov_caja_id, usuario_id, f"Reverso nómina {pago.empleado.nombre}")
    pago.anulada = True
