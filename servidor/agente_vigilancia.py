"""
Agente Windows + Groq — CASO 2: vigilancia autonoma

El agente vigila la camara SOLO, sin que escribas nada. Cuando detecta
movimiento, se activa y ejecuta una instruccion fija.

LA CLAVE (no llamar a Groq en bucle):
    Mirar la camara con Groq cada frame quemaria el rate limit en segundos.
    Por eso el "portero" es una deteccion de movimiento LOCAL y gratis: comparar
    dos frames seguidos en el PC. Solo cuando hay movimiento real se llama al
    agente (que entonces si usa Groq para ver y actuar). Tras disparar, un
    COOLDOWN evita reaccionar 50 veces al mismo movimiento.

SEGURIDAD:
    MODO_AUTONOMO bloquea las tools destructivas: como no estas delante para
    confirmar, el agente NO puede cerrar ventanas ni nada destructivo. Solo
    mira, abre apps y escribe notas.

REQUISITOS:
    El paso 8 (agente_paso2_groq.py) en la misma carpeta, y sus dependencias.

USO:
    python agente_vigilancia.py
    (Ctrl+C para parar.)
"""

import os
import time

try:
    import cv2
except ImportError:
    raise SystemExit("Falta OpenCV. Instala con: pip install opencv-python")

from groq import Groq

# Reutilizamos TODO el agente del paso 8.
import agente_paso2_groq as agente


# ============================================================
# CONFIGURACIÓN DE VIGILANCIA
# ============================================================

# Que hace el agente cuando se dispara. Solo lectura/reversible (las destructivas
# estan bloqueadas de todos modos por MODO_AUTONOMO).
INSTRUCCION = (
    "Se ha detectado movimiento delante de la camara. Mira la camara, describe "
    "brevemente que o quien hay, y apunta esa descripcion en una nota nueva del "
    "Bloc de notas."
)

# Cuantos pixeles tienen que cambiar entre dos frames para contar como movimiento.
# AJUSTA segun tu camara/escena: el bucle imprime cuantos px cambian, asi calibras.
UMBRAL_MOVIMIENTO = 5000

# Segundos minimos entre reacciones (protege el rate limit y evita repetir).
COOLDOWN = 30

# Cada cuanto se mira un frame para comparar (segundos).
INTERVALO = 0.5


# ============================================================
# DETECCIÓN DE MOVIMIENTO (local, gratis)
# ============================================================

def diferencia_px(frame_a, frame_b):
    """Devuelve cuantos pixeles han cambiado entre dos frames."""
    g1 = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
    g1 = cv2.GaussianBlur(g1, (21, 21), 0)
    g2 = cv2.GaussianBlur(g2, (21, 21), 0)
    diff = cv2.absdiff(g1, g2)
    _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(th)


def _abrir_camara_con_warmup():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
    for _ in range(8):  # descarta primeros frames (suelen salir negros)
        cap.read()
        time.sleep(0.05)
    return cap


# ============================================================
# BUCLE PRINCIPAL DE VIGILANCIA
# ============================================================

def main():
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("Falta GROQ_API_KEY en el .env")

    # Activamos el cliente compartido y el modo autonomo (destructivas bloqueadas).
    agente.CLIENTE = Groq()
    agente.MODO_AUTONOMO = True

    cap = _abrir_camara_con_warmup()
    if cap is None:
        raise SystemExit("No se pudo abrir la camara (¿la usa otra app?).")

    leido, frame_prev = cap.read()
    if not leido:
        cap.release()
        raise SystemExit("No se pudo leer de la camara.")

    ultimo_disparo = 0.0
    print("Vigilancia iniciada. El agente reaccionara al detectar movimiento.")
    print(f"(Umbral: {UMBRAL_MOVIMIENTO} px | Cooldown: {COOLDOWN}s | Ctrl+C para parar)\n")

    try:
        while True:
            time.sleep(INTERVALO)
            leido, frame = cap.read()
            if not leido:
                continue

            cambiados = diferencia_px(frame_prev, frame)
            frame_prev = frame
            ahora = time.time()

            if cambiados > UMBRAL_MOVIMIENTO and (ahora - ultimo_disparo) > COOLDOWN:
                print(f"[!] Movimiento detectado ({cambiados} px cambiados). Activando agente...")

                # La tool mirar_camara necesita abrir la camara; soltamos la nuestra
                # para que no choquen, dejamos actuar al agente, y reabrimos despues.
                cap.release()
                try:
                    respuesta = agente.responder(agente.CLIENTE, INSTRUCCION)
                    print(f"[AGENTE] {respuesta}\n")
                except Exception as e:
                    print(f"[ERROR] {e}\n")

                cap = _abrir_camara_con_warmup()
                if cap is None:
                    print("[ERROR] No se pudo reabrir la camara tras reaccionar.")
                    break
                leido, frame_prev = cap.read()  # frame base nuevo, sin falso movimiento
                ultimo_disparo = time.time()

            elif cambiados > UMBRAL_MOVIMIENTO:
                # Hay movimiento pero seguimos en cooldown: lo ignoramos.
                pass

    except KeyboardInterrupt:
        print("\nVigilancia detenida.")
    finally:
        if cap is not None:
            cap.release()


if __name__ == "__main__":
    main()
