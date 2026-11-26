"""Wompi (pasarela de Bancolombia): cobros en línea para la tienda y cobros con QR en la caja.

Llaves en las variables de entorno de Render (nunca en el código):
  WOMPI_PUBLIC_KEY        pub_test_… (pruebas) o pub_prod_… (real)
  WOMPI_PRIVATE_KEY       prv_test_… o prv_prod_… (para consultar pagos si el aviso de Wompi no llega)
  WOMPI_INTEGRITY_SECRET  "Secreto de integridad"  (Desarrolladores → Secretos para integración técnica)
  WOMPI_EVENTS_SECRET     "Secreto de eventos"     (mismo lugar)

URL de eventos que se pone en el panel de Wompi:  https://<tu-app>/wompi/eventos
"""
import hashlib
import json
import os
import urllib.parse
import urllib.request

CHECKOUT_URL = "https://checkout.wompi.co/p/"
ESTADOS = {"PENDING": "Pendiente", "APPROVED": "Aprobado", "DECLINED": "Rechazado",
           "VOIDED": "Anulado", "ERROR": "Error"}
METODOS = {"CARD": "Tarjeta", "NEQUI": "Nequi", "PSE": "PSE", "BANCOLOMBIA_TRANSFER": "Botón Bancolombia",
           "BANCOLOMBIA_QR": "QR Bancolombia", "BANCOLOMBIA_COLLECT": "Corresponsal Bancolombia",
           "DAVIPLATA": "Daviplata", "PCOL": "Puntos Colombia", "SU_PLUS": "SU+ Pay", "BNPL": "Compra a cuotas"}


def _env(nombre):
    return (os.getenv(nombre) or "").strip()


def llave_publica():
    return _env("WOMPI_PUBLIC_KEY")


def activo():
    """Wompi queda disponible cuando están la llave pública y el secreto de integridad."""
    return bool(llave_publica() and _env("WOMPI_INTEGRITY_SECRET"))


def es_prueba():
    return llave_publica().startswith("pub_test_")


def api_base():
    return "https://sandbox.wompi.co/v1" if es_prueba() else "https://production.wompi.co/v1"


def centavos(valor):
    return int(round(float(valor or 0))) * 100


def firma(referencia, monto_centavos, moneda="COP"):
    """Firma de integridad: SHA-256 de referencia + monto en centavos + moneda + secreto."""
    texto = f"{referencia}{monto_centavos}{moneda}{_env('WOMPI_INTEGRITY_SECRET')}"
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def url_checkout(referencia, valor, redirect_url, cliente=None):
    """Dirección del Web Checkout de Wompi para cobrar `valor` pesos con esa referencia."""
    cents = centavos(valor)
    params = {"public-key": llave_publica(), "currency": "COP", "amount-in-cents": str(cents),
              "reference": referencia, "signature:integrity": firma(referencia, cents),
              "redirect-url": redirect_url}
    for k, v in (cliente or {}).items():
        if v:
            params[f"customer-data:{k}"] = str(v)[:120]
    return CHECKOUT_URL + "?" + urllib.parse.urlencode(params)


def _get(ruta, privada=False):
    llave = _env("WOMPI_PRIVATE_KEY") if privada else (_env("WOMPI_PRIVATE_KEY") or llave_publica())
    req = urllib.request.Request(api_base() + ruta, headers={"Accept": "application/json"})
    if llave:
        req.add_header("Authorization", f"Bearer {llave}")
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def consultar(tx_id):
    """Consulta una transacción en Wompi por su id. Devuelve el diccionario de la transacción o None."""
    try:
        return (_get("/transactions/" + urllib.parse.quote(str(tx_id), safe="")) or {}).get("data")
    except Exception as e:   # Wompi caído o id malo: el aviso por eventos lo arregla después
        print(f"wompi: no se pudo consultar {tx_id}: {e}", flush=True)
        return None


def buscar_por_referencia(referencia):
    """Transacciones de una referencia (necesita la llave privada). Lista vacía si no se puede."""
    if not _env("WOMPI_PRIVATE_KEY"):
        return []
    try:
        data = (_get("/transactions?reference=" + urllib.parse.quote(referencia), privada=True) or {}).get("data")
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"wompi: no se pudo buscar {referencia}: {e}", flush=True)
        return []


def evento_valido(cuerpo, checksum_header=None):
    """Valida la firma de un evento: valores de signature.properties + timestamp + secreto de eventos."""
    secreto = _env("WOMPI_EVENTS_SECRET")
    if not secreto or not isinstance(cuerpo, dict):
        return False
    firma_ev = cuerpo.get("signature") or {}
    props = firma_ev.get("properties") or []
    esperado = (checksum_header or firma_ev.get("checksum") or "").strip().lower()
    if not props or not esperado:
        return False
    texto = ""
    for prop in props:
        valor = cuerpo.get("data") or {}
        for parte in str(prop).split("."):
            valor = valor.get(parte) if isinstance(valor, dict) else None
        texto += "" if valor is None else str(valor)
    texto += str(cuerpo.get("timestamp", "")) + secreto
    calculado = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    return calculado == esperado
