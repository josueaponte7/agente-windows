"""
Agente Windows + Groq — PASO 1: Capa de percepción (tier LECTURA)

Esto NO conecta con Groq todavía. Es solo el cimiento: las 5 tools de lectura
que el agente usará para "ver" antes de actuar. Tier LECTURA = no hacen daño,
se pueden ejecutar siempre.

OBJETIVO DE ESTE PASO:
    Ejecutar este archivo y confirmar que las 5 tools devuelven datos correctos.
    Si una falla, lo arreglas AHORA, antes de que Groq dependa de ella.

INSTALACIÓN (PowerShell):
    pip install mss Pillow pytesseract pygetwindow psutil

    OCR aparte: pytesseract necesita el binario de Tesseract instalado.
    Descárgalo de https://github.com/UB-Mannheim/tesseract/wiki
    Si lo instalas en una ruta no estándar, ajusta TESSERACT_CMD abajo.
    Si no instalas Tesseract, todo lo demás funciona; solo el OCR avisará.

USO:
    python agente_paso1_percepcion.py
"""

import os
import time
from datetime import datetime
from pathlib import Path

# --- Dependencias (con aviso claro si falta alguna) ---
try:
    import mss
    import mss.tools
    from PIL import Image
    import pygetwindow as gw
    import psutil
except ImportError as e:
    raise SystemExit(
        f"Falta una dependencia: {e.name}\n"
        "Instala con: pip install mss Pillow pytesseract pygetwindow psutil"
    )

# OCR es opcional: si no está, las otras tools siguen funcionando.
try:
    import pytesseract
    # Si instalaste Tesseract en ruta no estándar, descomenta y ajusta:
    # pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    OCR_DISPONIBLE = True
except ImportError:
    OCR_DISPONIBLE = False


# ============================================================
# CONFIGURACIÓN
# ============================================================

# Carpetas que buscar_archivo tiene permitido tocar. Lista blanca.
INICIO = Path.home()
CARPETAS_PERMITIDAS = [
    INICIO / "Documents",
    INICIO / "Desktop",
    INICIO / "Downloads",
]

# Nombres en espanol (o variantes) -> carpeta real en disco. Permite pedir
# "Descargas" aunque la carpeta real se llame "Downloads".
ALIAS_CARPETAS = {
    "descargas": INICIO / "Downloads",
    "downloads": INICIO / "Downloads",
    "documentos": INICIO / "Documents",
    "documents": INICIO / "Documents",
    "mis documentos": INICIO / "Documents",
    "escritorio": INICIO / "Desktop",
    "desktop": INICIO / "Desktop",
}

# Dónde se guardan las capturas temporales.
CARPETA_CAPTURAS = Path(__file__).parent / "_capturas"
CARPETA_CAPTURAS.mkdir(exist_ok=True)


# ============================================================
# SISTEMA DE TIERS Y REGISTRO DE TOOLS
# ============================================================

LECTURA = "LECTURA"
REVERSIBLE = "REVERSIBLE"
DESTRUCTIVO = "DESTRUCTIVO"

# Aquí se registran todas las tools. En pasos futuros lo lee Groq.
REGISTRO = {}


def tool(nombre, tier, descripcion):
    """Decorador: registra una función como tool con su tier y descripción."""
    def envoltorio(func):
        REGISTRO[nombre] = {
            "func": func,
            "tier": tier,
            "descripcion": descripcion,
        }
        return func
    return envoltorio


def ok(resultado):
    """Respuesta estándar de éxito. TODA tool devuelve este formato."""
    return {"ok": True, "resultado": resultado, "error": None}


def fallo(motivo):
    """Respuesta estándar de error. Groq lee 'error' para reaccionar."""
    return {"ok": False, "resultado": None, "error": motivo}


# ============================================================
# TIER LECTURA — las 5 tools de percepción
# ============================================================

@tool("capturar_pantalla", LECTURA,
      "Toma una captura de la pantalla (o de una región) y la guarda como PNG. "
      "Úsala como primer paso para ver qué hay en pantalla antes de actuar. "
      "No la uses en bucle rápido; cuesta tiempo.")
def capturar_pantalla(region=None):
    try:
        with mss.mss() as sct:
            if region:
                area = {
                    "left": region["x"], "top": region["y"],
                    "width": region["ancho"], "height": region["alto"],
                }
            else:
                area = sct.monitors[1]  # monitor principal
            img = sct.grab(area)
            nombre = CARPETA_CAPTURAS / f"cap_{int(time.time())}.png"
            mss.tools.to_png(img.rgb, img.size, output=str(nombre))
            return ok(str(nombre))
    except Exception as e:
        return fallo(f"No se pudo capturar la pantalla: {e}")


@tool("leer_pantalla_ocr", LECTURA,
      "Extrae el texto visible de la pantalla o de una región mediante OCR. "
      "Úsala cuando solo te interese leer texto (un botón, un mensaje, un campo). "
      "Más barata que capturar_pantalla si no necesitas la imagen.")
def leer_pantalla_ocr(region=None):
    if not OCR_DISPONIBLE:
        return fallo("OCR no disponible: instala pytesseract y el binario de Tesseract.")
    try:
        with mss.mss() as sct:
            area = (
                {"left": region["x"], "top": region["y"],
                 "width": region["ancho"], "height": region["alto"]}
                if region else sct.monitors[1]
            )
            img = sct.grab(area)
            pil = Image.frombytes("RGB", img.size, img.rgb)
            texto = pytesseract.image_to_string(pil, lang="spa+eng")
            return ok(texto.strip())
    except pytesseract.TesseractNotFoundError:
        return fallo("Tesseract no encontrado. Instálalo o ajusta TESSERACT_CMD.")
    except Exception as e:
        return fallo(f"Error en OCR: {e}")


@tool("listar_ventanas", LECTURA,
      "Devuelve las ventanas abiertas: título, si está activa, posición y tamaño. "
      "Es la fuente de verdad del escritorio. Úsala antes de abrir, enfocar o "
      "cerrar para no actuar a ciegas.")
def listar_ventanas():
    try:
        ventanas = []
        for w in gw.getAllWindows():
            if not w.title.strip():
                continue  # ignora ventanas fantasma sin título
            ventanas.append({
                "titulo": w.title,
                "activa": w.isActive,
                "x": w.left, "y": w.top,
                "ancho": w.width, "alto": w.height,
            })
        return ok(ventanas)
    except Exception as e:
        return fallo(f"No se pudieron listar las ventanas: {e}")


@tool("estado_sistema", LECTURA,
      "Lee hora, batería (% y si está enchufado), uso de CPU y RAM. "
      "Úsala cuando el contexto temporal o de recursos importe.")
def estado_sistema():
    try:
        bateria = psutil.sensors_battery()
        return ok({
            "hora": datetime.now().strftime("%H:%M:%S"),
            "fecha": datetime.now().strftime("%Y-%m-%d"),
            "bateria_pct": bateria.percent if bateria else None,
            "enchufado": bateria.power_plugged if bateria else None,
            "cpu_pct": psutil.cpu_percent(interval=0.5),
            "ram_pct": psutil.virtual_memory().percent,
        })
    except Exception as e:
        return fallo(f"No se pudo leer el estado del sistema: {e}")


@tool("buscar_archivo", LECTURA,
      "Busca archivos por nombre o patrón DENTRO de las carpetas permitidas. "
      "El parámetro 'carpeta' acepta nombres en español: Descargas, Documentos, "
      "Escritorio. Úsala para localizar un archivo antes de abrirlo.")
def buscar_archivo(patron, carpeta=None):
    try:
        if carpeta:
            # Primero, ¿es un nombre conocido en espanol/ingles? (Descargas, etc.)
            clave = carpeta.strip().lower()
            if clave in ALIAS_CARPETAS:
                base = ALIAS_CARPETAS[clave]
            else:
                base = Path(carpeta)
            permitida = any(
                base == p or base in p.parents or p in base.parents
                for p in CARPETAS_PERMITIDAS
            )
            if not permitida:
                return fallo(f"Carpeta fuera de la lista blanca: {carpeta}")
            bases = [base]
        else:
            bases = CARPETAS_PERMITIDAS

        encontrados = []
        for b in bases:
            if not b.exists():
                continue
            for ruta in b.rglob(f"*{patron}*"):
                if ruta.is_file():
                    encontrados.append(str(ruta))
                    if len(encontrados) >= 50:  # tope para no saturar el contexto
                        return ok(encontrados)
        return ok(encontrados)
    except Exception as e:
        return fallo(f"Error al buscar: {e}")


@tool("leer_archivo", LECTURA,
      "Lee el contenido de texto de un archivo DENTRO de las carpetas permitidas "
      "y lo devuelve. El parametro 'ruta' puede ser la ruta completa (p.ej. la que "
      "devuelve buscar_archivo). Usala para decir que contiene un archivo.")
def leer_archivo(ruta):
    try:
        p = Path(ruta).resolve()
    except Exception:
        return fallo("Ruta no valida.")
    permitida = any(
        str(p).lower().startswith(str(Path(c).resolve()).lower())
        for c in CARPETAS_PERMITIDAS
    )
    if not permitida:
        return fallo(f"Archivo fuera de las carpetas permitidas: {ruta}")
    if not p.exists() or not p.is_file():
        return fallo(f"No existe el archivo: {ruta}")
    try:
        texto = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return fallo(f"No se pudo leer (¿es un archivo de texto?): {e}")
    if len(texto) > 5000:
        texto = texto[:5000] + "\n...[contenido truncado]"
    return ok(texto)


# ============================================================
# ARNÉS DE PRUEBA — ejecuta cada tool y enseña el resultado
# ============================================================

def _resumir(valor, limite=300):
    """Recorta resultados largos para que la consola sea legible."""
    texto = str(valor)
    return texto if len(texto) <= limite else texto[:limite] + " …[recortado]"


def probar_todas():
    print("=" * 60)
    print("PASO 1 — Prueba de la capa de percepción (tier LECTURA)")
    print(f"OCR disponible: {OCR_DISPONIBLE}")
    print("=" * 60)

    pruebas = [
        ("estado_sistema", lambda: estado_sistema()),
        ("listar_ventanas", lambda: listar_ventanas()),
        ("buscar_archivo (*.txt)", lambda: buscar_archivo(".txt")),
        ("capturar_pantalla", lambda: capturar_pantalla()),
        ("leer_pantalla_ocr", lambda: leer_pantalla_ocr()),
    ]

    for nombre, fn in pruebas:
        print(f"\n--- {nombre} ---")
        r = fn()
        estado = "OK" if r["ok"] else "FALLO"
        print(f"[{estado}] {_resumir(r['resultado'] if r['ok'] else r['error'])}")

    print("\n" + "=" * 60)
    print("Tools registradas:", list(REGISTRO.keys()))
    print("Si todas dicen OK (o el OCR avisa de que falta Tesseract),")
    print("el cimiento está listo para el PASO 2: conectar Groq.")
    print("=" * 60)


if __name__ == "__main__":
    probar_todas()
