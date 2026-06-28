"""
Agente Windows + Groq — MODO CAMARA + VOZ (v4: captura con pyaudio, como tu codigo)

Por que cambia: grabar con sounddevice en bloques sueltos dejaba huecos y daba
audio pobre -> Whisper alucinaba ("Gracias"). Adoptamos tu metodo, que SI
funciona: pyaudio con un STREAM CONTINUO (se lee el micro sin parar, sin huecos).
Sobre ese stream, grabamos hasta que dejas de hablar (~1.5s de silencio).

Flujo: camara detecta movimiento -> confirma persona -> graba tu orden a tu ritmo
-> transcribe -> el agente actua (calculadora, bloc de notas, etc.).

SEGURIDAD: MODO_AUTONOMO activo -> tools destructivas bloqueadas.

REQUISITOS:
    agente_paso2_groq.py en la misma carpeta + sus dependencias.
    pip install opencv-python numpy pyaudio

USO:
    python agente_camara_voz.py
"""

import os
import time
import tempfile
import wave
from pathlib import Path

try:
    import cv2
    import numpy as np
    import pyaudio
except ImportError:
    raise SystemExit("Faltan dependencias. Instala: pip install opencv-python numpy pyaudio")

from groq import Groq

import agente_paso2_groq as agente


# ============================================================
# CONFIGURACIÓN
# ============================================================

# Camara / movimiento
UMBRAL_MOVIMIENTO = 5000
INTERVALO = 0.5
COOLDOWN = 20

# Voz (pyaudio, int16, stream continuo)
RATE = 16000
CHUNK = 1024               # muestras por lectura (~0.064 s)
UMBRAL_VOZ = 500           # pico minimo para considerar "hay voz" (como tu UMBRAL_SILENCIO)
SEG_SILENCIO_FIN = 1.5     # s de silencio seguido que marcan el fin (tolera pausas)
ESPERA_INICIAL = 8         # s para empezar a hablar
MAX_SEG = 15               # tope de grabacion


# ============================================================
# CÁMARA / MOVIMIENTO (local)
# ============================================================

def diferencia_px(a, b):
    g1 = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    g1 = cv2.GaussianBlur(g1, (21, 21), 0)
    g2 = cv2.GaussianBlur(g2, (21, 21), 0)
    diff = cv2.absdiff(g1, g2)
    _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(th)


def abrir_camara():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
    for _ in range(8):
        cap.read()
        time.sleep(0.05)
    return cap


def hay_persona():
    r = agente.mirar_camara(
        pregunta="¿Hay alguna persona visible en la imagen? Empieza tu respuesta con SI o con NO."
    )
    if not r["ok"]:
        print(f"  [vision fallo: {r['error']}]")
        return False, ""
    resp = (r["resultado"] or "").strip()
    return resp.lower().startswith("s"), resp


# ============================================================
# VOZ — pyaudio, stream continuo, graba hasta que callas
# ============================================================

def grabar_orden():
    """Abre el micro como stream continuo (como tu codigo) y graba hasta que
    dejas de hablar. Devuelve los samples int16 o None si no hubo voz."""
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=RATE,
                    input=True, frames_per_buffer=CHUNK)

    print("  Di tu orden ahora (habla a tu ritmo, para cuando termines)...")

    chunks_por_seg = RATE / CHUNK
    chunks_silencio_fin = int(SEG_SILENCIO_FIN * chunks_por_seg)
    espera_chunks = int(ESPERA_INICIAL * chunks_por_seg)
    max_chunks = int(MAX_SEG * chunks_por_seg)

    frames = []
    hubo_voz = False
    chunks_silencio = 0
    leidos = 0

    try:
        while leidos < max_chunks:
            data = stream.read(CHUNK, exception_on_overflow=False)
            arr = np.frombuffer(data, dtype=np.int16)
            leidos += 1
            pico = int(np.max(np.abs(arr.astype(np.int32))))

            if pico >= UMBRAL_VOZ:
                hubo_voz = True
                chunks_silencio = 0
                frames.append(arr)
            elif hubo_voz:
                frames.append(arr)               # silencio tras hablar
                chunks_silencio += 1
                if chunks_silencio >= chunks_silencio_fin:
                    break
            elif leidos > espera_chunks:
                break                            # no empezaste a hablar
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

    if not hubo_voz or not frames:
        return None
    return np.concatenate(frames)


def transcribir(samples):
    if samples is None:
        print("  [no se capto voz]")
        return ""
    ruta = Path(tempfile.gettempdir()) / f"voz_{int(time.time() * 1000)}.wav"
    try:
        with wave.open(str(ruta), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)               # 16 bits
            wf.setframerate(RATE)
            wf.writeframes(samples.tobytes())

        contenido = ruta.read_bytes()
        tr = agente.con_reintento(lambda: agente.CLIENTE.audio.transcriptions.create(
            model=agente.MODELO_AUDIO, file=("voz.wav", contenido), language="es"))
        return (tr.text or "").strip()
    except Exception as e:
        print(f"  [ERROR al transcribir] {e}")
        return ""
    finally:
        try:
            ruta.unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================
# BUCLE PRINCIPAL
# ============================================================

def main():
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("Falta GROQ_API_KEY en el .env")

    agente.CLIENTE = Groq()
    agente.MODO_AUTONOMO = True

    cap = abrir_camara()
    if cap is None:
        raise SystemExit("No se pudo abrir la camara (¿la usa otra app?).")
    leido, frame_prev = cap.read()
    if not leido:
        cap.release()
        raise SystemExit("No se pudo leer de la camara.")

    ultimo_evento = 0.0
    print("Vigilando. Al detectar una persona, te pedira la orden por voz.")
    print(f"(Umbral movimiento: {UMBRAL_MOVIMIENTO} | Cooldown: {COOLDOWN}s | Ctrl+C para parar)\n")

    try:
        while True:
            time.sleep(INTERVALO)
            leido, frame = cap.read()
            if not leido:
                continue
            cambiados = diferencia_px(frame_prev, frame)
            frame_prev = frame
            ahora = time.time()

            if cambiados > UMBRAL_MOVIMIENTO and (ahora - ultimo_evento) > COOLDOWN:
                print(f"[!] Movimiento ({cambiados} px). Comprobando si es una persona...")

                cap.release()
                es_persona, desc = hay_persona()

                if es_persona:
                    print(f"[PERSONA] {desc}")
                    samples = grabar_orden()
                    texto = transcribir(samples)
                    if texto:
                        print(f"  [orden] {texto}")
                        try:
                            resp = agente.responder(agente.CLIENTE, texto)
                            print(f"  [AGENTE] {resp}\n")
                        except Exception as e:
                            print(f"  [ERROR] {e}\n")
                    else:
                        print("  [no se entendio la orden]\n")
                else:
                    print("  [movimiento, pero no parece una persona]\n")

                cap = abrir_camara()
                if cap is None:
                    print("[ERROR] No se pudo reabrir la camara.")
                    break
                leido, frame_prev = cap.read()
                ultimo_evento = time.time()

    except KeyboardInterrupt:
        print("\nDetenido.")
    finally:
        if cap is not None:
            cap.release()


if __name__ == "__main__":
    main()
