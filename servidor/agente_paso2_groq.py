"""
Agente Windows + Groq — PASO 8: aguantar el rate limit (error 429)

Cuando agotas el limite de Groq (tokens/min o peticiones/min del free tier), la
API devuelve un error 429. Hasta ahora eso reventaba el programa. Ahora el agente
ESPERA y REINTENTA solo: lee cuanto hay que esperar (cabecera retry-after) y, si
no la hay, usa una espera creciente (backoff). Aplica a las tres llamadas a Groq:
cerebro, vision y audio.

NOTA: no hay "recorte de historial" porque el bucle no guarda memoria entre
peticiones; cada orden tuya arranca con historial limpio.

REQUISITOS:
    pip install groq python-dotenv pyautogui pyperclip pygetwindow opencv-python sounddevice soundfile

USO:
    python agente_paso2_groq.py
"""

import os
import json
import time
import base64
import tempfile
import subprocess
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

try:
    import groq
    from groq import Groq
except ImportError:
    raise SystemExit("Falta el SDK de Groq. Instala con: pip install groq")

try:
    import pygetwindow as gw
    import pyautogui
    import pyperclip
except ImportError:
    raise SystemExit("Faltan dependencias. Instala: pip install pyautogui pyperclip pygetwindow")

pyautogui.FAILSAFE = True

from agente_paso1_percepcion import (
    REGISTRO, tool, LECTURA, REVERSIBLE, DESTRUCTIVO, ok, fallo,
    CARPETAS_PERMITIDAS,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

MODELO = "openai/gpt-oss-120b"
MODELO_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
MODELO_AUDIO = "whisper-large-v3"
DRY_RUN = False

# Cuando es True (modo vigilancia, sin humano delante), las tools DESTRUCTIVAS
# se bloquean: no hay nadie para teclear 's', asi que no se ejecutan nunca.
MODO_AUTONOMO = False

CLIENTE = None

PROMPT_SISTEMA = (
    "Eres un agente que opera un PC Windows. Usa las tools para observar y actuar. "
    "Para escribir texto en el Bloc de notas: 1) abrir_nota_nueva, 2) enfocar_ventana "
    "con el nombre del archivo devuelto, 3) escribir_texto. Para ver usa mirar_camara. "
    "Para oir usa escuchar_microfono. Para cerrar usa cerrar_ventana. Para HACER "
    "una operacion en la calculadora: 1) abrela con abrir_app('calculadora'), "
    "2) tecleala con teclear_calculo (p.ej. expresion '2+2'). Puedes tambien "
    "controlar el volumen (control_volumen), la musica (control_medios), el brillo "
    "(ajustar_brillo), gestionar ventanas (gestionar_ventana) y abrir archivos "
    "(abrir_archivo, normalmente tras buscar_archivo). Para saber que contiene un "
    "archivo de texto, usa buscar_archivo y luego leer_archivo con la ruta. Para "
    "decir algo en voz alta por los altavoces usa hablar. Nunca inventes "
    "datos: si no lo has hecho con una tool, no lo afirmes. Responde en espanol, breve, "
    "en texto plano (sin markdown ni emojis)."
)

APPS_PERMITIDAS = {
    "calculadora": "calc.exe",
    "bloc de notas": "notepad.exe",
    "notepad": "notepad.exe",
    "explorador": "explorer.exe",
    "paint": "mspaint.exe",
}

CARPETA_NOTAS = Path(tempfile.gettempdir())


# ============================================================
# REINTENTO ANTE RATE LIMIT (429)
# ============================================================

def _segundos_de_espera(e, intento):
    # Groq manda la cabecera retry-after con los segundos exactos a esperar.
    try:
        ra = e.response.headers.get("retry-after")
        if ra:
            return float(ra) + 0.5
    except Exception:
        pass
    # Si no la hay, espera creciente: 2, 4, 8... con tope de 30s.
    return min(2 ** intento, 30)


def con_reintento(llamada, max_intentos=4):
    """Ejecuta una llamada a Groq; si da 429, espera y reintenta."""
    for intento in range(max_intentos):
        try:
            return llamada()
        except groq.RateLimitError as e:
            espera = _segundos_de_espera(e, intento)
            print(f"  [RATE LIMIT] Limite de Groq alcanzado. Esperando {espera:.0f}s "
                  f"(intento {intento + 1}/{max_intentos})...")
            time.sleep(espera)
    # Ultimo intento sin red de seguridad: si vuelve a fallar, que se vea el error.
    return llamada()


# ============================================================
# TOOLS DE PERCEPCIÓN AVANZADA (tier LECTURA)
# ============================================================

@tool("mirar_camara", LECTURA,
      "Captura una foto de la webcam y la analiza con un modelo de vision. "
      "Devuelve una descripcion en texto de lo que se ve. Puedes pasar una "
      "pregunta concreta sobre la imagen.")
def capturar_jpeg():
    """Hace una foto con la webcam y devuelve los bytes JPEG (o None si falla)."""
    try:
        import cv2
    except ImportError:
        return None
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return None
        frame = None
        for _ in range(6):
            leido, f = cap.read()
            if leido:
                frame = f
            time.sleep(0.05)
        cap.release()
        if frame is None:
            return None
        leido, buf = cv2.imencode(".jpg", frame)
        if not leido:
            return None
        return buf.tobytes()
    except Exception:
        return None


def describir_jpeg(jpeg, pregunta=None):
    """Describe una imagen JPEG (bytes) usando el modelo de vision. Devuelve texto."""
    b64 = base64.b64encode(jpeg).decode()
    texto = pregunta or "Describe brevemente que o quien aparece en esta imagen."
    r = con_reintento(lambda: CLIENTE.chat.completions.create(
        model=MODELO_VISION,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": texto},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        max_completion_tokens=300,
    ))
    return r.choices[0].message.content


def mirar_camara(pregunta=None):
    jpeg = capturar_jpeg()
    if jpeg is None:
        return fallo("No se pudo capturar de la camara (¿la usa otra app?).")
    try:
        return ok(describir_jpeg(jpeg, pregunta))
    except Exception as e:
        return fallo(f"Error al analizar la imagen: {e}")


@tool("escuchar_microfono", LECTURA,
      "Graba unos segundos del microfono y los transcribe a texto. Devuelve lo "
      "que se dijo. Por defecto graba 5 segundos.")
def escuchar_microfono(segundos=5):
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        return fallo("Faltan dependencias de audio. Instala: pip install sounddevice soundfile")

    SAMPLERATE = 16000
    try:
        segundos = max(1, min(int(segundos), 30))
    except (TypeError, ValueError):
        segundos = 5

    ruta = CARPETA_NOTAS / f"audio_{int(time.time())}.wav"
    try:
        print(f"  [MIC] Grabando {segundos}s... habla ahora.")
        grabacion = sd.rec(int(segundos * SAMPLERATE), samplerate=SAMPLERATE, channels=1)
        sd.wait()
        print("  [MIC] Grabacion terminada.")
        sf.write(str(ruta), grabacion, SAMPLERATE)
    except Exception as e:
        return fallo(f"Error al grabar audio (¿micro disponible?): {e}")

    try:
        contenido = ruta.read_bytes()  # leemos a bytes para poder reintentar sin reabrir
        tr = con_reintento(lambda: CLIENTE.audio.transcriptions.create(
            model=MODELO_AUDIO,
            file=("audio.wav", contenido),
            language="es",
        ))
        texto = (tr.text or "").strip()
        return ok(texto or "(no se entendio nada)")
    except Exception as e:
        return fallo(f"Error al transcribir: {e}")
    finally:
        try:
            ruta.unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================
# TOOLS DE ACCIÓN
# ============================================================

@tool("abrir_app", REVERSIBLE,
      "Abre una aplicacion de la lista permitida. Para ESCRIBIR texto en el Bloc "
      "de notas no uses esta; usa abrir_nota_nueva.")
def abrir_app(nombre):
    clave = (nombre or "").strip().lower()
    if clave not in APPS_PERMITIDAS:
        return fallo(f"App no permitida: '{nombre}'. Permitidas: {list(APPS_PERMITIDAS)}")
    try:
        subprocess.Popen([APPS_PERMITIDAS[clave]])
        return ok(f"Abierta: {clave}")
    except Exception as e:
        return fallo(f"No se pudo abrir {clave}: {e}")


@tool("abrir_nota_nueva", REVERSIBLE,
      "Crea un archivo de texto NUEVO y vacio (nombre unico, en carpeta temporal) "
      "y lo abre en el Bloc de notas. Devuelve el nombre del archivo. Usa ese "
      "nombre con enfocar_ventana antes de escribir.")
def abrir_nota_nueva():
    try:
        nombre = f"nota_{int(time.time())}.txt"
        ruta = CARPETA_NOTAS / nombre
        ruta.write_text("", encoding="utf-8")
        subprocess.Popen(["notepad.exe", str(ruta)])
        return ok({"archivo": nombre, "ruta": str(ruta)})
    except Exception as e:
        return fallo(f"No se pudo crear/abrir la nota: {e}")


@tool("enfocar_ventana", REVERSIBLE,
      "Trae una ventana al frente buscandola por parte de su titulo. ESPERA a que "
      "aparezca. Tras abrir_nota_nueva, pasa el nombre del archivo devuelto.")
def enfocar_ventana(titulo, intentos=15, espera=0.3):
    objetivo = (titulo or "").strip().lower()
    for _ in range(intentos):
        candidatas = [w for w in gw.getAllWindows()
                      if w.title.strip() and objetivo in w.title.lower()]
        if candidatas:
            win = candidatas[0]
            try:
                win.activate()
            except Exception:
                try:
                    win.minimize()
                    win.restore()
                except Exception:
                    pass
            time.sleep(0.25)
            return ok(f"Enfocada: {win.title}")
        time.sleep(espera)
    return fallo(f"No aparecio ninguna ventana con '{titulo}' tras esperar.")


@tool("escribir_texto", REVERSIBLE,
      "Escribe texto en la ventana enfocada. Usala SOLO tras enfocar la nota nueva. "
      "Soporta acentos y enie. No pulsa Enter.")
def escribir_texto(texto):
    try:
        anterior = None
        try:
            anterior = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(texto)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
        if anterior is not None:
            try:
                pyperclip.copy(anterior)
            except Exception:
                pass
        return ok(f"Escrito: {texto}")
    except Exception as e:
        return fallo(f"No se pudo escribir: {e}")


@tool("teclear_calculo", REVERSIBLE,
      "Teclea una operacion matematica en la Calculadora de Windows (que debe estar "
      "ya abierta) y muestra el resultado en ella. Pasa la expresion como texto, "
      "p.ej. '2+2' o '15*3'. Usa + - * / y punto decimal. La tool enfoca la "
      "calculadora, la limpia y teclea; el resultado aparece al pulsar igual.")
def teclear_calculo(expresion):
    # Buscar y enfocar la calculadora (es/en)
    candidatas = [w for w in gw.getAllWindows()
                  if w.title.strip() and ("calculadora" in w.title.lower()
                                          or "calculator" in w.title.lower())]
    if not candidatas:
        return fallo("La calculadora no esta abierta. Abrela antes con abrir_app.")
    win = candidatas[0]
    # La Calculadora de Win11 (UWP) a veces ignora activate(); forzamos el foco
    # con el truco minimizar/restaurar y damos margen a que venga al frente.
    try:
        win.activate()
        time.sleep(0.3)
    except Exception:
        pass
    try:
        if not win.isActive:
            win.minimize()
            time.sleep(0.2)
            win.restore()
    except Exception:
        pass
    time.sleep(0.6)  # margen extra: que la ventana este de verdad al frente

    # Mapa de operadores a teclas del teclado numerico (sin ambiguedad de Shift)
    mapa = {"+": "add", "-": "subtract", "*": "multiply", "x": "multiply",
            "/": "divide", ".": "decimal", ",": "decimal", "=": "enter"}
    try:
        pyautogui.press("escape")  # limpia lo que hubiera
        time.sleep(0.4)            # margen para que la calc procese el Esc
        for ch in (expresion or ""):
            if ch.isdigit():
                pyautogui.press(ch)
            elif ch.lower() in mapa:
                pyautogui.press(mapa[ch.lower()])
            else:
                continue  # ignora espacios u otros
            time.sleep(0.12)       # respiro entre teclas
        pyautogui.press("enter")  # '=' para mostrar el resultado
        return ok(f"Tecleado en la calculadora: {expresion}")
    except Exception as e:
        return fallo(f"No se pudo teclear: {e}")


@tool("control_volumen", REVERSIBLE,
      "Controla el volumen del sistema. accion: 'subir', 'bajar' o 'silenciar'. "
      "Para subir/bajar puedes indicar cuantos 'pasos' (cada paso ~2%).")
def control_volumen(accion, pasos=4):
    accion = (accion or "").strip().lower()
    teclas = {"subir": "volumeup", "bajar": "volumedown"}
    try:
        if accion == "silenciar":
            pyautogui.press("volumemute")
            return ok("Volumen silenciado/activado")
        if accion not in teclas:
            return fallo(f"Accion no valida: {accion}. Usa subir, bajar o silenciar.")
        for _ in range(max(1, min(int(pasos), 25))):
            pyautogui.press(teclas[accion])
        return ok(f"Volumen: {accion}")
    except Exception as e:
        return fallo(f"No se pudo cambiar el volumen: {e}")


@tool("control_medios", REVERSIBLE,
      "Controla la reproduccion multimedia (Spotify, YouTube, reproductor, etc.). "
      "accion: 'play_pausa', 'siguiente' o 'anterior'.")
def control_medios(accion):
    accion = (accion or "").strip().lower()
    teclas = {"play_pausa": "playpause", "siguiente": "nexttrack", "anterior": "prevtrack"}
    if accion not in teclas:
        return fallo(f"Accion no valida: {accion}. Usa play_pausa, siguiente o anterior.")
    try:
        pyautogui.press(teclas[accion])
        return ok(f"Medios: {accion}")
    except Exception as e:
        return fallo(f"No se pudo controlar la reproduccion: {e}")


@tool("ajustar_brillo", REVERSIBLE,
      "Fija el brillo de la pantalla a un nivel de 0 a 100.")
def ajustar_brillo(nivel):
    try:
        import screen_brightness_control as sbc
    except ImportError:
        return fallo("Falta la libreria. Instala: pip install screen-brightness-control")
    try:
        n = max(0, min(int(nivel), 100))
        sbc.set_brightness(n)
        return ok(f"Brillo al {n}%")
    except Exception as e:
        return fallo(f"No se pudo ajustar el brillo (¿pantalla compatible?): {e}")


@tool("gestionar_ventana", REVERSIBLE,
      "Maximiza, minimiza o restaura una ventana, buscandola por parte de su "
      "titulo. accion: 'maximizar', 'minimizar' o 'restaurar'.")
def gestionar_ventana(titulo, accion):
    objetivo = (titulo or "").strip().lower()
    accion = (accion or "").strip().lower()
    candidatas = [w for w in gw.getAllWindows()
                  if w.title.strip() and objetivo in w.title.lower()]
    if not candidatas:
        return fallo(f"No hay ninguna ventana con '{titulo}'.")
    if len(candidatas) > 1:
        return fallo(f"Varias ventanas coinciden con '{titulo}': "
                     f"{[w.title for w in candidatas]}. Se mas concreto.")
    win = candidatas[0]
    try:
        if accion == "maximizar":
            win.maximize()
        elif accion == "minimizar":
            win.minimize()
        elif accion == "restaurar":
            win.restore()
        else:
            return fallo(f"Accion no valida: {accion}. Usa maximizar, minimizar o restaurar.")
        return ok(f"{accion}: {win.title}")
    except Exception as e:
        return fallo(f"No se pudo {accion} la ventana: {e}")


@tool("abrir_archivo", REVERSIBLE,
      "Abre un archivo con su aplicacion por defecto (PDF, imagen, documento...). "
      "La ruta debe estar dentro de las carpetas permitidas. Util tras buscar_archivo.")
def abrir_archivo(ruta):
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
    if not p.exists():
        return fallo(f"No existe el archivo: {ruta}")
    try:
        os.startfile(str(p))  # abre con la app asociada en Windows
        return ok(f"Abierto: {p.name}")
    except Exception as e:
        return fallo(f"No se pudo abrir: {e}")


@tool("hablar", REVERSIBLE,
      "Reproduce un texto por los altavoces del portatil con voz. Usala cuando el "
      "usuario pida decir algo en voz alta, o para responder hablando.")
def hablar(texto):
    try:
        import pyttsx3
    except ImportError:
        return fallo("Falta pyttsx3. Instala: pip install pyttsx3")
    try:
        engine = pyttsx3.init()
        # Si hay una voz en espanol instalada en Windows, la usamos.
        for v in engine.getProperty("voices"):
            nombre = (v.name or "").lower()
            if "spanish" in nombre or "español" in nombre or "helena" in nombre or "sabina" in nombre:
                engine.setProperty("voice", v.id)
                break
        engine.say(texto or "")
        engine.runAndWait()
        engine.stop()
        return ok(f"Dicho en voz alta: {texto}")
    except Exception as e:
        return fallo(f"No se pudo reproducir la voz: {e}")


@tool("cerrar_ventana", DESTRUCTIVO,
      "Cierra una ventana buscandola por parte de su titulo. DESTRUCTIVA: puede "
      "perder trabajo no guardado. Si varias ventanas coinciden, no adivina: pide "
      "un titulo mas concreto.")
def cerrar_ventana(titulo):
    objetivo = (titulo or "").strip().lower()
    candidatas = [w for w in gw.getAllWindows()
                  if w.title.strip() and objetivo in w.title.lower()]
    if not candidatas:
        return fallo(f"No hay ninguna ventana con '{titulo}'.")
    if len(candidatas) > 1:
        titulos = [w.title for w in candidatas]
        return fallo(f"Varias ventanas coinciden con '{titulo}': {titulos}. Se mas especifico.")
    win = candidatas[0]
    try:
        win.close()
        return ok(f"Cerrada: {win.title}")
    except Exception as e:
        return fallo(f"No se pudo cerrar {win.title}: {e}")


# ============================================================
# SCHEMAS
# ============================================================

_SIN_ARGS = {"type": "object", "properties": {}, "required": []}
_REGION = {
    "type": "object",
    "description": "Region opcional. Si se omite, toda la pantalla.",
    "properties": {
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "ancho": {"type": "integer"}, "alto": {"type": "integer"},
    },
}

SCHEMAS = {
    "capturar_pantalla": {"type": "object", "properties": {"region": _REGION}, "required": []},
    "leer_pantalla_ocr": {"type": "object", "properties": {"region": _REGION}, "required": []},
    "listar_ventanas": _SIN_ARGS,
    "estado_sistema": _SIN_ARGS,
    "buscar_archivo": {
        "type": "object",
        "properties": {
            "patron": {"type": "string", "description": "Nombre o fragmento a buscar."},
            "carpeta": {"type": "string", "description": "Carpeta concreta (opcional, permitida)."},
        },
        "required": ["patron"],
    },
    "mirar_camara": {
        "type": "object",
        "properties": {
            "pregunta": {"type": "string",
                         "description": "Pregunta concreta sobre la imagen (opcional)."},
        },
        "required": [],
    },
    "escuchar_microfono": {
        "type": "object",
        "properties": {
            "segundos": {"type": "integer",
                         "description": "Segundos a grabar (1-30, por defecto 5)."},
        },
        "required": [],
    },
    "abrir_app": {
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "enum": list(APPS_PERMITIDAS.keys()),
                       "description": "App a abrir. SOLO las del enum."},
        },
        "required": ["nombre"],
    },
    "abrir_nota_nueva": _SIN_ARGS,
    "enfocar_ventana": {
        "type": "object",
        "properties": {
            "titulo": {"type": "string",
                       "description": "Parte del titulo de la ventana (p.ej. el nombre del archivo)."},
        },
        "required": ["titulo"],
    },
    "escribir_texto": {
        "type": "object",
        "properties": {
            "texto": {"type": "string", "description": "Texto a escribir en la ventana enfocada."},
        },
        "required": ["texto"],
    },
    "teclear_calculo": {
        "type": "object",
        "properties": {
            "expresion": {"type": "string",
                          "description": "Operacion a teclear, p.ej. '2+2' o '15*3'."},
        },
        "required": ["expresion"],
    },
    "control_volumen": {
        "type": "object",
        "properties": {
            "accion": {"type": "string", "enum": ["subir", "bajar", "silenciar"]},
            "pasos": {"type": "integer", "description": "Cuantos pasos subir/bajar (cada uno ~2%)."},
        },
        "required": ["accion"],
    },
    "control_medios": {
        "type": "object",
        "properties": {
            "accion": {"type": "string", "enum": ["play_pausa", "siguiente", "anterior"]},
        },
        "required": ["accion"],
    },
    "ajustar_brillo": {
        "type": "object",
        "properties": {
            "nivel": {"type": "integer", "description": "Brillo de 0 a 100."},
        },
        "required": ["nivel"],
    },
    "gestionar_ventana": {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Parte del titulo de la ventana."},
            "accion": {"type": "string", "enum": ["maximizar", "minimizar", "restaurar"]},
        },
        "required": ["titulo", "accion"],
    },
    "abrir_archivo": {
        "type": "object",
        "properties": {
            "ruta": {"type": "string", "description": "Ruta completa del archivo a abrir."},
        },
        "required": ["ruta"],
    },
    "hablar": {
        "type": "object",
        "properties": {
            "texto": {"type": "string", "description": "Texto a decir en voz alta."},
        },
        "required": ["texto"],
    },
    "leer_archivo": {
        "type": "object",
        "properties": {
            "ruta": {"type": "string", "description": "Ruta completa del archivo de texto a leer."},
        },
        "required": ["ruta"],
    },
    "cerrar_ventana": {
        "type": "object",
        "properties": {
            "titulo": {"type": "string", "description": "Parte del titulo de la ventana a cerrar."},
        },
        "required": ["titulo"],
    },
}


def construir_tools_para_groq():
    tools = []
    for nombre, meta in REGISTRO.items():
        tools.append({
            "type": "function",
            "function": {
                "name": nombre,
                "description": meta["descripcion"],
                "parameters": SCHEMAS[nombre],
            },
        })
    return tools


# ============================================================
# EJECUTOR — gating por tier
# ============================================================

def ejecutar_tool(nombre, args):
    if nombre not in REGISTRO:
        return fallo(f"Tool desconocida: {nombre}")

    tier = REGISTRO[nombre]["tier"]

    if tier == DESTRUCTIVO:
        if MODO_AUTONOMO:
            return fallo("Accion destructiva bloqueada: nadie puede confirmar (modo autonomo).")
        if DRY_RUN:
            return ok(f"[SECO] iba a ejecutar {nombre}({args})")
        if not _confirmar(nombre, args):
            return fallo("Cancelado por el usuario")
    elif tier == REVERSIBLE and DRY_RUN:
        return ok(f"[SECO] iba a ejecutar {nombre}({args})")

    try:
        resultado = REGISTRO[nombre]["func"](**args)
    except TypeError as e:
        return fallo(f"Args invalidos para {nombre}: {e}")

    if tier != LECTURA:
        print(f"  [LOG] {nombre}({args}) -> {resultado}")
    return resultado


def _confirmar(nombre, args):
    print(f"  !! ACCION DESTRUCTIVA: {nombre}({args})")
    r = input(f"     Confirmar? [s/N] ").strip().lower()
    return r == "s"


# ============================================================
# BUCLE PRINCIPAL
# ============================================================

def responder(cliente, peticion):
    mensajes = [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": peticion},
    ]
    tools = construir_tools_para_groq()

    for _ in range(8):
        respuesta = con_reintento(lambda: cliente.chat.completions.create(
            model=MODELO, messages=mensajes, tools=tools, tool_choice="auto",
        ))
        msg = respuesta.choices[0].message

        if not msg.tool_calls:
            return msg.content

        mensajes.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            nombre = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"-> Groq pide: {nombre}({args})")
            resultado = ejecutar_tool(nombre, args)
            mensajes.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(resultado, ensure_ascii=False),
            })

    return "(Se alcanzo el limite de rondas de tools sin respuesta final.)"


def main():
    global CLIENTE
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("Falta GROQ_API_KEY en el .env")

    CLIENTE = Groq()
    modo = "SECO (no actua)" if DRY_RUN else "REAL (actua de verdad)"
    print(f"Agente listo — modo {modo}. Escribe una peticion (o 'salir').\n")
    print("AVISO: camara y microfono suben datos a Groq cuando se usan.")
    print("Si ves [RATE LIMIT], el agente esta esperando para no pasarse del free tier.\n")

    while True:
        try:
            peticion = input("Tu> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if peticion.lower() in {"salir", "exit", "quit"}:
            break
        if not peticion:
            continue
        print(f"\nAgente> {responder(CLIENTE, peticion)}\n")


if __name__ == "__main__":
    main()
