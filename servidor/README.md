# Agente Windows con IA

Agente que percibe (pantalla, cámara, micrófono), razona con un LLM y actúa sobre
Windows (abrir apps, escribir, operar la calculadora). Disparado por texto, por
presencia ante la cámara o por voz.

## De qué se partía

Había demos sueltas de percepción —cámara que reconoce, audio que detecta ruido—
pero nada que actuara. Se creó un agente que reacciona al mundo.

## Lo que se construyó, en orden

**Capa de percepción.** Cinco tools de lectura —pantalla, OCR, ventanas, estado
del sistema, buscar archivos— probadas en aislamiento antes de tocar la IA, con un
sistema de tiers de seguridad (lectura / reversible / destructivo) montado desde la
primera línea.

**Conexión del LLM con function calling.** La IA decide qué tool llamar, el código
la ejecuta y le devuelve el resultado. Circuito completo cerrado.

**Manos.** Abrir apps, enfocar ventanas, escribir texto, y tools de acción de cada
tier — incluida la primera destructiva (`cerrar_ventana`) con confirmación por
consola. Aquí el gating de seguridad se disparó de verdad por primera vez.

**Sentidos completos.** Cámara (capturar la imagen + describirla con un modelo de
visión) y micrófono (grabar + transcribir la voz a texto).

**Blindaje del rate limit.** El límite de peticiones por minuto que impone el
proveedor de la IA. Cuando se le mandan demasiadas seguidas, corta con un error (el
429); ahora el agente espera y reintenta solo en vez de caerse.

**Agente autónomo.** Vigilancia por cámara con detección de movimiento local, y el
modo principal: cámara detecta persona → se escucha la orden por voz → el agente
actúa, incluido operar la calculadora tecleando de verdad y escribir en el bloc de
notas.

## Lecciones de fondo

- Filtro local barato delante del modelo caro (movimiento y volumen como porteros
  antes de llamar a la IA).
- La fiabilidad del disparador manda sobre lo molona que sea la idea.
- Cuando la orden es vaga, el modelo no pregunta: adivina.
- Cimientos antes que planta alta.

## Lo que se tiene ahora

Un agente con tres sentidos y manos, gobernado por un LLM, con seguridad por tiers,
que aguanta el límite de peticiones de la IA, y tres modos de arranque:

- **Escribes** las órdenes en la terminal.
- **Vigila** la cámara y reacciona al movimiento.
- **Le hablas** y obedece (detección por presencia + voz).

## Cómo añadir una tool nueva

El patrón está fijo: una función con su decorador (`nombre`, tier, descripción),
su entrada en `SCHEMAS`, y —si abre puertas— su lista blanca. El bucle, el gating y
la conexión con la IA no se tocan.

## Posibles siguientes pasos

- Más tools siguiendo el patrón anterior.
- Sacar el agente del portátil con hardware externo (p. ej. un ESP32).
- Operar otras apps además de la calculadora y el bloc de notas.
