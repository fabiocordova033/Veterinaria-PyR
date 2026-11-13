# Chatbot para clínica veterinaria (prototipo)

Asistente por chat que responde preguntas frecuentes y gestiona citas de una clínica veterinaria. Usa Claude (API de Anthropic) con *tool use*. El modelo **no escribe SQL**: solo llama funciones del backend, y el backend hace todas las validaciones.

- Backend: Python 3 + FastAPI
- Base de datos: SQLite con SQLAlchemy 2.0. Se puede migrar a MySQL o PostgreSQL cambiando la URL.
- IA: SDK oficial `anthropic`, modelo configurable (por defecto `claude-opus-5-5`)
- Interfaz: una página HTML + JavaScript, sin frameworks
- Preparado para conectar WhatsApp (Meta Cloud API) sin reescribir la lógica del bot

## Qué hace el bot

- Al empezar pide el teléfono del cliente (como funcionaría por WhatsApp) y a partir de ahí solo muestra información de las mascotas de ese propietario.
- Responde sobre servicios, precios, horario de atención, dirección y veterinarios.
- Consulta disponibilidad real según el horario de cada veterinario, la duración del servicio y las citas ya agendadas.
- Agenda, cancela y reprograma citas, siempre después de pedir confirmación explícita.
- Muestra las próximas citas y la próxima vacuna de cada mascota.
- No da diagnósticos ni dosis de medicamentos.
- Ante una posible urgencia (envenenamiento, convulsiones, sangrado, dificultad para respirar…) da el teléfono de urgencias, indica que lo atenderá una persona y marca la conversación para el equipo.
- Si no sabe algo, lo dice y ofrece comunicar al cliente con la clínica.

## Instalación y ejecución

Requisitos: Python 3.10 o superior y una API key de Anthropic ([console.anthropic.com](https://console.anthropic.com)).

1. Crear y activar un entorno virtual (desde la carpeta del proyecto):

   ```bash
   python -m venv .venv
   ```

   Linux / macOS:

   ```bash
   source .venv/bin/activate
   ```

   Windows (PowerShell):

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

2. Instalar las dependencias:

   ```bash
   pip install -r requirements.txt
   ```

3. Crear el archivo `.env` a partir del ejemplo y poner la API key en `ANTHROPIC_API_KEY`:

   ```bash
   cp .env.example .env
   ```

   (En Windows: `copy .env.example .env`.)

4. Crear la base de datos con datos de prueba:

   ```bash
   python -m scripts.crear_base
   ```

   Para borrarla y volver a cargarla: `python -m scripts.crear_base --reiniciar`. Las fechas de las citas y vacunas de prueba se calculan a partir del día de hoy.

5. Iniciar el servidor:

   ```bash
   uvicorn app.main:app --reload
   ```

6. Abrir <http://localhost:8000> y empezar a chatear.

### Datos de prueba

| Teléfono   | Propietario           | Mascotas                    |
|------------|-----------------------|-----------------------------|
| 3001112233 | María Fernanda López  | Luna (perro), Michi (gato)  |
| 3102223344 | Carlos Andrés Ramírez | Rocky (perro)               |
| 3203334455 | Ana Sofía Martínez    | Nala (gata), Coco (conejo)  |
| 3154445566 | Jorge Iván Castro     | Max (perro)                 |
| 3015556677 | Lucía Herrera         | Simba (gato), Toby (perro)  |

También se cargan 3 veterinarios con sus horarios, 5 servicios, citas próximas y pasadas (una cancelada), vacunas (una atrasada: la de Michi), medicamentos, una consulta con receta y una factura.

Ideas para probar:

- "3001112233" → "¿Cuándo es la próxima cita de Luna?"
- "Quiero agendar baño para Michi el sábado"
- "¿Qué vacuna le toca a Michi?"
- "Necesito cambiar la cita de Luna para la tarde"
- "¿Cuánto cuesta la limpieza dental?" (no requiere identificarse)
- "Mi perro se comió un veneno para ratas" → urgencia
- "¿Qué dosis de amoxicilina le doy?" → no da dosis

## Pruebas

```bash
pytest
```

Las pruebas no llaman a la API de Anthropic. Usan un Claude simulado y una base SQLite temporal. Cubren:

- **Agenda** (`tests/test_agenda.py`):
  - Cruces exactos y parciales.
  - Una cita que envuelve a otra.
  - Citas contiguas permitidas.
  - Horario del veterinario: antes de abrir, en el almuerzo y en días sin turno.
  - Fechas pasadas.
  - Cancelación que libera el horario.
  - Reprogramación.
  - Disponibilidad.
  - Mascotas de otros clientes.
  - **Ocho solicitudes simultáneas por el mismo horario: solo una se agenda.**
- **Herramientas** (`tests/test_herramientas.py`): identificación obligatoria, aislamiento entre clientes, errores legibles para el modelo, derivación a una persona.
- **Motor del bot** (`tests/test_motor.py`): bucle de herramientas, historial guardado y reenviado sin cambios, rechazos del modelo, errores de la API, conversaciones nuevas tras inactividad.
- **SDK** (`tests/test_llm_sdk.py`): forma exacta de las solicitudes que arma el SDK `anthropic`, capturadas con un transporte HTTP simulado (sin red).
- **API web** (`tests/test_api.py`): endpoint `/chat`, sesiones y página de chat.

## Estructura del proyecto

```
app/
  config.py              Configuración (.env)
  main.py                Aplicación FastAPI
  db/
    base.py              Conexión, transacciones y bloqueo para SQLite
    modelos.py           Tablas (SQLAlchemy)
  servicios/             Lógica de negocio pura (sin IA ni HTTP)
    agenda.py            Disponibilidad, agendar, cancelar, reprogramar, ver citas
    clientes.py          Búsqueda por teléfono y verificación de pertenencia
    catalogo.py          Servicios, precios, horario, dirección, veterinarios
    vacunas.py           Próxima vacuna
  bot/                   Todo lo relacionado con Claude, independiente del canal
    motor.py             MotorBot.responder(sesion_id, texto, canal, ...)
    herramientas.py      Definición de las tools y su ejecución
    prompt.py            Reglas del bot (system prompt)
    llm.py               Cliente de la API de Anthropic
    sesiones.py          Historial por sesión
  canales/               Adaptadores de entrada/salida
    comun.py             Motor compartido por los canales
    web.py               POST /chat (página web)
scripts/crear_base.py    Crea la base y carga datos de prueba
static/                  Página de chat (HTML, JS y CSS)
tests/                   Pruebas (pytest)
```

## Cómo funciona

### Un turno de conversación

1. La página envía `POST /chat` con `{"sesion_id": ..., "mensaje": ...}`. En el primer mensaje no lleva `sesion_id`; el servidor crea uno y lo devuelve.
2. `MotorBot` carga el historial de la sesión y agrega el mensaje del cliente. Antes del mensaje pone un bloque `<contexto>` escrito por el backend, con la fecha y hora actuales, el canal y si el cliente ya está identificado.
3. Claude responde con texto o pide una herramienta. El backend la ejecuta en su propia transacción y le devuelve el resultado en JSON. Esto se repite hasta que Claude responde con texto.
4. Cada mensaje se guarda en las tablas `sesion_chat` y `mensaje_chat` apenas se produce. El historial solo crece y se reenvía exactamente igual en cada turno. Eso conserva el razonamiento del modelo entre turnos y aprovecha el caché de prompts.

### Herramientas disponibles para el modelo

| Herramienta | Qué hace |
|---|---|
| `buscar_cliente(telefono)` | Propietario y mascotas. Asocia la conversación a ese cliente. |
| `info_servicios()` | Servicios, precios, duración, horario, dirección, teléfonos y veterinarios. |
| `consultar_disponibilidad(servicio_id, fecha, veterinario_id?)` | Horarios libres por veterinario. |
| `agendar_cita(mascota_id, servicio_id, veterinario_id, fecha_hora, motivo)` | Crea la cita. |
| `cancelar_cita(cita_id)` | Cancela una cita programada. |
| `reprogramar_cita(cita_id, nueva_fecha_hora)` | Mueve la cita (mismo veterinario y servicio). |
| `ver_citas(mascota_id)` | Próximas citas de la mascota. |
| `proxima_vacuna(mascota_id)` | Próxima dosis, vacunas atrasadas y últimas aplicaciones. |
| `derivar_a_humano(motivo, urgente)` | Marca la conversación para que la atienda una persona. |

### Qué valida el backend (y no el modelo)

- **Pertenencia:** cada herramienta de mascotas o citas comprueba que pertenezcan al cliente identificado en la sesión. Si el modelo pidiera datos de otra mascota, recibiría "No encontré esa mascota".
- **Identificación:** sin `buscar_cliente` no se puede consultar ni modificar nada de un cliente, y una conversación no puede cambiar de cliente.
- **Agenda:**
  - La cita debe caber completa en un bloque de trabajo del veterinario.
  - No puede cruzarse con otra cita del veterinario ni de la mascota.
  - Debe estar en el futuro, con la anticipación mínima y dentro del máximo de días configurados.
- **Argumentos:** tipos, formatos de fecha y existencia de servicios y veterinarios. Además, las herramientas usan `strict: true`, así que la API garantiza que los argumentos cumplan el esquema.

### Citas que no se cruzan

Dos citas `[inicio, fin)` se cruzan si `inicio1 < fin2` y `inicio2 < fin1`. Una cita que termina a las 9:30 no choca con otra que empieza a las 9:30. Las citas canceladas no ocupan horario.

La columna `cita.fecha_hora_fin` se calcula al agendar. Con ella la consulta de cruces es una comparación simple, igual en todos los motores de base de datos, y cada cita conserva su duración aunque después cambie la del servicio.

Para que dos solicitudes simultáneas no tomen el mismo horario, agendar y reprogramar ocurren en una transacción que primero bloquea la mascota y el veterinario y después valida y escribe:

- **PostgreSQL / MySQL:** `SELECT ... FOR UPDATE` sobre esas filas.
- **SQLite:** como no tiene bloqueo por fila, cada transacción empieza con `BEGIN IMMEDIATE` (ver `app/db/base.py`), que serializa las escrituras.

La segunda solicitud espera, ve la cita recién creada y recibe un error claro. La prueba `test_dos_solicitudes_simultaneas_por_el_mismo_horario_solo_agenda_una` lo verifica con 8 hilos.

## Configuración (`.env`)

| Variable | Por defecto | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | API key de Anthropic (obligatoria para chatear). |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Modelo de Claude. |
| `ANTHROPIC_EFFORT` | `medium` | Profundidad de razonamiento: `low` es más rápido y barato, `high` más cuidadoso. |
| `ANTHROPIC_FALLBACKS` | `default` | Si el modelo rechaza una solicitud por sus filtros de seguridad, la API la reintenta con un modelo de respaldo. Déjalo vacío para apagarlo (por ejemplo, con modelos que no lo soportan). |
| `ANTHROPIC_STRICT_TOOLS` | `true` | Validación estricta de argumentos de las herramientas. |
| `DATABASE_URL` | `sqlite:///veterinaria.db` | Base de datos. Una ruta SQLite relativa se toma desde la raíz del proyecto. |
| `CLINICA_*` | — | Nombre, dirección, teléfonos, horario de atención y zona horaria (`America/Bogota`). |
| `AGENDA_INTERVALO_MINUTOS` | `30` | Cada cuánto se ofrecen horarios. |
| `AGENDA_ANTICIPACION_MINUTOS` | `60` | Anticipación mínima para agendar. |
| `AGENDA_DIAS_MAXIMOS` | `60` | Hasta cuántos días adelante se puede agendar. |
| `SESION_MINUTOS_INACTIVIDAD` | `720` | Tras este tiempo sin mensajes empieza una conversación nueva. |

Las fechas se guardan en hora local de la clínica, sin zona horaria. `dia_semana` usa 0 = lunes … 6 = domingo.

## Migrar a MySQL o PostgreSQL

1. Instalar el driver: `pip install "psycopg[binary]"` (PostgreSQL) o `pip install PyMySQL` (MySQL).
2. Cambiar `DATABASE_URL` en `.env`:
   - `postgresql+psycopg://usuario:clave@localhost:5432/veterinaria`
   - `mysql+pymysql://usuario:clave@localhost:3306/veterinaria`
3. Ejecutar `python -m scripts.crear_base`.

No hay SQL propio de SQLite en la lógica. Las restricciones (`CHECK`, llaves foráneas, `UNIQUE`) y los bloqueos funcionan en los tres motores. Para un sistema en producción conviene manejar los cambios de esquema con Alembic.

## Conectar WhatsApp (Meta Cloud API)

La lógica del bot está en `MotorBot` y no depende del canal. Para WhatsApp basta con un nuevo adaptador `app/canales/whatsapp.py` que:

1. **Verifique el webhook** (`GET`): si `hub.verify_token` coincide con tu token, responde con `hub.challenge`.
2. **Reciba mensajes** (`POST`): valide la firma `X-Hub-Signature-256` con el *app secret*, responda 200 de inmediato e ignore mensajes repetidos (por su `id`).
3. **Llame al motor** con el número del remitente como sesión y como teléfono verificado. Así el bot no pide el teléfono y solo puede consultar ese número:

   ```python
   respuesta = obtener_motor().responder(
       sesion_id=f"whatsapp-{numero}",
       texto=texto_del_mensaje,
       canal="whatsapp",
       telefono_verificado=numero,
   )
   ```

4. **Envíe la respuesta** con `POST https://graph.facebook.com/<versión>/<PHONE_NUMBER_ID>/messages` (token de acceso como Bearer):

   ```json
   {"messaging_product": "whatsapp", "to": "<numero>", "type": "text", "text": {"body": "<respuesta.texto>"}}
   ```

5. Se registre en `app/main.py` con `app.include_router(whatsapp.router)`.

Las conversaciones de WhatsApp se reinician solas tras `SESION_MINUTOS_INACTIVIDAD`, y el historial anterior queda guardado.

## Limitaciones del prototipo

- **Identificación en la web:** cualquiera que escriba un número registrado ve las mascotas y citas de ese cliente. Es solo una simulación de WhatsApp, donde el número sí está verificado. Para una web real hace falta verificar el número (por ejemplo, con un código por SMS).
- **Atención humana:** las derivaciones quedan marcadas en `sesion_chat` (`requiere_humano`, `urgente`, `motivo_derivacion`) y en el log, pero no hay un panel para el equipo.
- **Varios procesos:** el turno por sesión se serializa con un candado en memoria. Con varios procesos o servidores habría que usar un bloqueo en la base o en Redis.
- **Confirmaciones:** la regla de pedir confirmación antes de agendar, cancelar o reprogramar está en las instrucciones del modelo. El backend valida los datos, pero no puede saber si el cliente dijo "sí".
