# agente-windows — Agente de IA para Windows

Agente de escritorio que percibe el entorno (pantalla, cámara y micrófono),
razona con un LLM mediante function calling, y ejecuta acciones reales en Windows.
Incluye un servidor para controlarlo en remoto desde una app móvil.

## Qué demuestra

- Integración de un LLM con function calling para decidir y ejecutar acciones
- Percepción multimodal: captura de pantalla, cámara y micrófono
- Modelo de seguridad en tres niveles para las acciones: LECTURA, REVERSIBLE y DESTRUCTIVA
- Servidor FastAPI con endpoints protegidos por token
- App móvil complementaria para control remoto, con entrada y salida de voz

## Arquitectura

El sistema tiene dos partes:

- Agente + servidor (Python): percibe, razona con el LLM, ejecuta acciones en Windows y expone una API FastAPI protegida por token
- App móvil (Flutter): controla el agente en remoto, envía voz, recibe respuestas habladas y muestra la cámara

## Niveles de seguridad

Cada acción que el agente puede ejecutar se clasifica por su riesgo:

- LECTURA: solo consulta información, no cambia nada
- REVERSIBLE: cambia algo que se puede deshacer
- DESTRUCTIVA: cambia algo que no se puede deshacer (requiere confirmación)

## Stack

- Python (agente y servidor)
- Groq (LLM y Whisper para voz)
- FastAPI + uvicorn (servidor local con autenticación por token)
- Flutter / Dart (app móvil de control remoto)

## Cómo ejecutarlo

1. Instalar dependencias de Python del servidor
2. Configurar la clave de Groq como variable de entorno (en un archivo .env)
3. Arrancar el servidor FastAPI
4. Para el control remoto: compilar y ejecutar la app Flutter, apuntándola a la IP del servidor