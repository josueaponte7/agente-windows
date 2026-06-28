"""
Agente Windows + Groq — CONTROL POR VOZ v3: pulsa-y-habla (fiable)

Por que cambia respecto a v2: la palabra de activacion por transcripcion ("di
agente") era fragil: Whisper cortaba la frase tras "agente" y alucinaba con el
audio corto ("Gracias", "Bye"...). El "siempre escuchando" bien hecho necesita
un detector de wake word dedicado (otro proyecto). Asi que volvemos a lo fiable:

    Pulsas Enter -> hablas la orden -> se transcribe entera -> se ejecuta.

Sin palabra magica, sin cortes, sin alucinaciones de audio corto.

Sigue el patron de siempre: graba hasta que dejas de hablar (no segundos fijos).

REQUISITOS:
    agente_paso2_groq.py en la misma carpeta + sus dependencias.
    pip install numpy sounddevice soundfile

USO:
    python agente_voz.py
    Pulsa Enter, di "abre la calculadora", y espera.
"""

import os
import time
import tempfile
from pathlib import Path

try:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    raise SystemExit("Faltan dependencias. Instala: pip install numpy sounddevice soundfile")

from groq import Groq

import agente_paso2_groq as agente


# ============================================================
# CONFIGURACIÓN
# ============================================================

SAMPLERATE = 16000
BLOQUE = 0.4                 # s: tamano de cada trozo
UMBRAL_ENERGIA = 0.02        # volumen que cuenta como voz. AJUSTA si hace falta.
BLOQUES_SILENCIO_FIN = 3     # trozos de silencio seguidos = fin de la orden (~1.2s)
MAX_BLOQUES = 30             # tope (~12s) por si no paras de hablar
ESPERA_INICIAL = 6           # s para empezar a hablar tras pulsar Enter


def _rms(audio):
    return float(np.sqrt(np.mean(np.square(audio))))


def _grabar_bloque():
    b = sd.rec(int(BLOQUE * SAMPLERATE), samplerate=SAMPLERATE, channels=1, dtype="float32")
    sd.wait()
    return b


def _grabar_orden():
    """Graba desde que empiezas a hablar hasta que dejas de hablar."""
    trozos = []
    hubo_voz = False
    silencios = 0
    inicio = time.time()

    while len(trozos) < MAX_BLOQUES:
        b = _grabar_bloque()
        tiene_voz = _rms(b) >= UMBRAL_ENERGIA

        if tiene_voz:
            hubo_voz = True
            silencios = 0
            trozos.append(b)
        elif hubo_voz:
            trozos.append(b)        # silencio despues de hablar: cuenta para el fin
            silencios += 1
            if silencios >= BLOQUES_SILENCIO_FIN:
                break
        else:
            if time.time() - inicio > ESPERA_INICIAL:
                return None         # no empezaste a hablar a tiempo

    return np.concatenate(trozos) if hubo_voz else None


def _transcribir(audio):
    ruta = Path(tempfile.gettempdir()) / f"voz_{int(time.time() * 1000)}.wav"
    try:
        sf.write(str(ruta), audio, SAMPLERATE)
        contenido = ruta.read_bytes()
        tr = agente.con_reintento(lambda: agente.CLIENTE.audio.transcriptions.create(
            model=agente.MODELO_AUDIO,
            file=("voz.wav", contenido),
            language="es",
        ))
        return (tr.text or "").strip()
    except Exception as e:
        print(f"  [ERROR al transcribir] {e}")
        return ""
    finally:
        try:
            ruta.unlink(missing_ok=True)
        except Exception:
            pass


def main():
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("Falta GROQ_API_KEY en el .env")

    agente.CLIENTE = Groq()
    # Estas presente (pulsas Enter), asi que las destructivas se permiten con
    # confirmacion por teclado. Pon True si prefieres bloquearlas del todo.
    agente.MODO_AUTONOMO = False

    print("Control por voz listo. Pulsa Enter, habla tu orden, y espera.")
    print("Ejemplo: 'abre la calculadora'. Ctrl+C para salir.\n")

    while True:
        try:
            input("Pulsa Enter para hablar...")
        except (EOFError, KeyboardInterrupt):
            print("\nHasta luego.")
            break

        print("  Habla ahora...")
        audio = _grabar_orden()
        if audio is None:
            print("  [no te oi nada]\n")
            continue

        print("  [procesando...]")
        texto = _transcribir(audio)
        if not texto:
            print("  [no se entendio nada]\n")
            continue

        print(f"  [has dicho] {texto}")
        try:
            respuesta = agente.responder(agente.CLIENTE, texto)
            print(f"  [AGENTE] {respuesta}\n")
        except Exception as e:
            print(f"  [ERROR] {e}\n")


if __name__ == "__main__":
    main()
