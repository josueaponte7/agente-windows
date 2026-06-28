"""
Agente Windows + Groq — SERVIDOR con FastAPI (recibe ordenes por WiFi)

Abre una puerta en el portatil para recibir ordenes por la red. Desde el movil
(navegador o app Flutter) le mandas la orden y el portatil la ejecuta.

REQUISITOS:
    agente_paso2_groq.py en la misma carpeta + sus dependencias.
    pip install fastapi uvicorn
    En el .env:  AGENTE_TOKEN=tu_clave

USO:
    python agente_servidor.py
"""

import os
import socket

try:
    from fastapi import FastAPI
    from fastapi.responses import Response
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    raise SystemExit("Falta FastAPI. Instala con: pip install fastapi uvicorn")

from dotenv import load_dotenv
load_dotenv()

from groq import Groq

import agente_paso2_groq as agente


PUERTO = 5000
TOKEN = os.environ.get("AGENTE_TOKEN", "cambia-esto")


def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


app = FastAPI()


# Define la forma de lo que llega: un JSON con token y orden.
class Peticion(BaseModel):
    token: str
    orden: str = ""


@app.post("/orden")
def orden(p: Peticion):
    if p.token != TOKEN:
        return {"error": "Token incorrecto"}
    texto = (p.orden or "").strip()
    if not texto:
        return {"error": "Orden vacia"}
    print(f"-> Orden recibida: {texto}")
    try:
        respuesta = agente.responder(agente.CLIENTE, texto)
        return {"respuesta": respuesta}
    except Exception as e:
        return {"error": str(e)}


@app.get("/camara")
def camara(token: str = ""):
    if token != TOKEN:
        return Response(content="Token incorrecto", status_code=403)
    jpeg = agente.capturar_jpeg()
    if jpeg is None:
        return Response(content="No se pudo capturar", status_code=500)
    print("-> Foto enviada al movil")
    return Response(content=jpeg, media_type="image/jpeg")


@app.post("/camara_describe")
def camara_describe(p: Peticion):
    # Reusa el modelo Peticion (token + orden); aqui 'orden' puede venir vacia o
    # traer una pregunta concreta para la imagen.
    if p.token != TOKEN:
        return {"error": "Token incorrecto"}
    jpeg = agente.capturar_jpeg()
    if jpeg is None:
        return {"error": "No se pudo capturar de la camara"}
    import base64 as _b64
    imagen = _b64.b64encode(jpeg).decode()
    pregunta = (p.orden or "").strip() or None
    try:
        descripcion = agente.describir_jpeg(jpeg, pregunta)
    except Exception as e:
        descripcion = f"(no se pudo describir: {e})"
    print("-> Foto + descripcion enviada al movil")
    return {"imagen": imagen, "descripcion": descripcion}


def main():
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit("Falta GROQ_API_KEY en el .env")
    if TOKEN == "cambia-esto":
        print("AVISO: pon AGENTE_TOKEN=algo en el .env.\n")

    agente.CLIENTE = Groq()
    agente.MODO_AUTONOMO = True  # destructivas bloqueadas (sin confirmacion remota)

    ip = ip_local()
    print("Servidor en marcha.")
    print(f"Direccion del portatil: http://{ip}:{PUERTO}")
    print("Ctrl+C para parar.\n")
    uvicorn.run(app, host="0.0.0.0", port=PUERTO)


if __name__ == "__main__":
    main()
