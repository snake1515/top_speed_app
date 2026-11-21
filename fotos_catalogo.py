"""Fotos de catálogo en buena calidad que se ponen solas a los repuestos al arrancar la app.

static/fotos_catalogo/manifest.json  →  {"Tiger": {"DS 13": "tiger/ds-13.jpg", ...}}
Cada repuesto cuyo nombre diga «<Marca> <modelo>» (ej. «Llanta Tiger DS 13 90/90-18 TL») recibe la foto
de su modelo. Se aplica una sola vez por foto (imagen_lote guarda cuál se puso); si después alguien sube
otra foto a mano para ese repuesto, no se vuelve a cambiar.
"""
import hashlib
import json
import os
import re

from models import Producto, db

CARPETA = os.path.join(os.path.dirname(__file__), "static", "fotos_catalogo")


def _patron(marca, modelo):
    letras, num, suf = re.match(r"([A-Za-z]+)\s*0*(\d+)\s*([A-Za-z]?)", modelo).groups()
    return re.compile(rf"\b{re.escape(marca)}\s+{letras}\s*0*{num}\s*{suf}\b", re.I)


def aplicar():
    ruta = os.path.join(CARPETA, "manifest.json")
    if not os.path.exists(ruta):
        return 0
    with open(ruta, encoding="utf-8") as fh:
        manifest = json.load(fh)
    reglas = []
    for marca, modelos in manifest.items():
        for modelo, archivo in modelos.items():
            with open(os.path.join(CARPETA, archivo), "rb") as fh:
                datos = fh.read()
            clave = f"{marca} {modelo}@{hashlib.sha1(datos).hexdigest()[:10]}"
            reglas.append((_patron(marca, modelo), clave, datos))
    cambios = 0
    for p in (Producto.query.all() if reglas else []):
        for patron, clave, datos in reglas:
            if patron.search(p.nombre or ""):
                previa = (p.imagen_lote or "").split("@")[0]
                if p.imagen_lote != clave and (not p.imagen_lote or previa == clave.split("@")[0]):
                    p.imagen = datos
                    p.imagen_mime = "image/jpeg"
                    p.imagen_v = (p.imagen_v or 0) + 1
                    p.imagen_lote = clave
                    cambios += 1
                break
    if cambios:
        db.session.commit()
        print(f"Fotos de catálogo puestas a {cambios} repuesto(s).", flush=True)
    return cambios
