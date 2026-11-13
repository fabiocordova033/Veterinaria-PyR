# Chatbot para clínica veterinaria (prototipo)

Asistente por chat que responde preguntas frecuentes y gestiona citas de una clínica veterinaria en **Lima, Perú**. Usa Claude (API de Anthropic) con *tool use*. El modelo **no escribe SQL**: solo llama funciones del backend, y el backend hace todas las validaciones.

Configurado para Perú: hora de Lima (`America/Lima`), precios en soles (S/) con IGV incluido, celulares de 9 dígitos (acepta `+51`), DNI como documento y colegiatura CMVP para los veterinarios.

- Backend: Python 3 + FastAPI
- Base de datos: SQLite con SQLAlchemy 2.0. Se puede migrar a MySQL o PostgreSQL cambiando la URL.
- IA: SDK oficial `anthropic`, modelo configurable (por defecto `claude-opus-5-5`)
- Interfaz: una página HTML + JavaScript, sin frameworks
- Preparado para conectar WhatsApp (Meta Cloud API) sin reescribir la lógica del bot

## Qué hace el bot

- Funciona por su cuenta: nadie del equipo tiene que leer ni contestar el chat. Lo que no puede resolver lo redirige al teléfono de la clínica.
- Funciona en la página web y en **WhatsApp** (Meta Cloud API). En WhatsApp no pide el celular: usa el número verificado desde el que escribe el cliente.
- En la web pide el número de celular al empezar. A partir de ahí solo muestra información de las mascotas de ese propietario.
- Responde sobre servicios, precios, horario de atención, dirección y veterinarios.
- Consulta disponibilidad real según el horario de cada veterinario, la duración del servicio y las citas ya agendadas.
- Agenda, cancela y reprograma citas, siempre después de pedir confirmación explícita.
- Muestra las próximas citas y la próxima vacuna de cada mascota.
- No da diagnósticos ni dosis de medicamentos.
- Ante una posible urgencia (envenenamiento, convulsiones, sangrado, dificultad para respirar…) registra la urgencia, da el teléfono de urgencias y pide llamar ya: en esa llamada lo atiende una persona.
- Si no sabe algo, lo dice y da el teléfono y el horario de la clínica. Nunca promete que alguien responderá por el chat.
- En WhatsApp junta los mensajes seguidos: si el cliente escribe "hola" / "quiero una cita" / "para Luna", espera unos segundos y responde una sola vez.
- Si recibe una nota de voz, foto o sticker, explica que por ahora solo lee texto y pide que lo escriban.

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

| Celular   | Propietario           | Mascotas                                 |
|-----------|-----------------------|------------------------------------------|
| 987111222 | Rosa Quispe Mamani    | Luna (perro), Michi (gato)               |
| 986222333 | Luis Alberto Huamán   | Rocky (perro sin pelo del Perú)          |
| 985333444 | Carmen Rojas Vargas   | Nala (gata), Coco (conejo)               |
| 984444555 | Diego Salazar Torres  | Max (perro)                              |
| 983555666 | Lucía Fernández Ramos | Simba (gato), Toby (perro)               |

También se cargan:

- 3 veterinarios con sus horarios.
- 5 servicios, en soles: consulta general S/ 70, vacunación S/ 60, desparasitación S/ 35, baño y corte S/ 60, profilaxis dental S/ 280.
- Citas próximas y pasadas (una cancelada).
- Vacunas, una de ellas atrasada (la de Michi).
- Medicamentos, una consulta con receta y una factura.

Todos los datos son de ejemplo. Antes de usarlo con clientes, pon en `.env` el nombre, la dirección y los teléfonos reales de la clínica.

Ideas para probar:

- "987111222" (o "+51 987 111 222") → "¿Cuándo es la próxima cita de Luna?"
- "Quiero sacar cita de baño para Michi el sábado"
- "¿Qué vacuna le toca a Michi?"
- "Necesito cambiar la cita de Luna para la tarde"
- "¿Cuánto cuesta la profilaxis dental?" (no requiere identificarse)
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
- **Herramientas** (`tests/test_herramientas.py`): identificación obligatoria, aislamiento entre clientes, errores legibles para el modelo, registro de urgencias, celulares peruanos.
- **Motor del bot** (`tests/test_motor.py`): bucle de herramientas, historial guardado y reenviado sin cambios, rechazos del modelo, errores de la API, conversaciones nuevas tras inactividad.
- **SDK** (`tests/test_llm_sdk.py`): forma exacta de las solicitudes que arma el SDK `anthropic`, capturadas con un transporte HTTP simulado (sin red).
- **API web** (`tests/test_api.py`): endpoint `/chat`, sesiones y página de chat.
- **Mensajes seguidos** (`tests/test_agrupador.py`): se juntan los que llegan con poca pausa, cada cliente por separado, nunca dos respuestas en curso para el mismo chat, espera máxima si el cliente no para de escribir.
- **WhatsApp** (`tests/test_whatsapp.py`): webhooks simulados de Meta. Cubre verificación, firma, mensajes repetidos, audios y fotos, formato de envío a la API de WhatsApp y un recorrido completo con el motor del bot.

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
    clientes.py          Búsqueda por celular y verificación de pertenencia
    catalogo.py          Servicios, precios, horario, dirección, veterinarios
    vacunas.py           Próxima vacuna
  bot/                   Todo lo relacionado con Claude, independiente del canal
    motor.py             MotorBot.responder(sesion_id, texto, canal, ...)
    herramientas.py      Definición de las tools y su ejecución
    prompt.py            Reglas del bot (system prompt)
    llm.py               Cliente de la API de Anthropic
    sesiones.py          Historial por sesión
    agrupador.py         Junta los mensajes seguidos de un cliente (WhatsApp)
  canales/               Adaptadores de entrada/salida
    comun.py             Motor compartido por los canales
    web.py               POST /chat (página web)
    whatsapp.py          Webhook de WhatsApp (Meta Cloud API) y envío de respuestas
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
| `registrar_urgencia(motivo)` | Registra una posible urgencia en la conversación y devuelve el teléfono de urgencias. |

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
| `CLINICA_*` | — | Nombre, dirección, teléfonos y horario de atención de la clínica. |
| `CLINICA_ZONA_HORARIA` | `America/Lima` | Zona horaria para "hoy", "mañana" y la disponibilidad. |
| `CLINICA_MONEDA` / `CLINICA_SIMBOLO_MONEDA` | `PEN` / `S/` | Moneda de los precios. |
| `CLINICA_NOTA_PRECIOS` | `Precios en soles, incluyen IGV.` | Aclaración que acompaña los precios. |
| `TELEFONO_DIGITOS_LOCALES` | `9` | Dígitos de un celular en Perú. Con esto, `+51 987 111 222` y `987111222` son el mismo cliente. |
| `AGENDA_INTERVALO_MINUTOS` | `30` | Cada cuánto se ofrecen horarios. |
| `AGENDA_ANTICIPACION_MINUTOS` | `60` | Anticipación mínima para agendar. |
| `AGENDA_DIAS_MAXIMOS` | `60` | Hasta cuántos días adelante se puede agendar. |
| `SESION_MINUTOS_INACTIVIDAD` | `720` | Tras este tiempo sin mensajes empieza una conversación nueva. |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | — | Credenciales de WhatsApp. El canal se activa cuando las cuatro tienen valor. |
| `WHATSAPP_VERSION_API` | `v24.0` | Versión de la Graph API de Meta. |
| `WHATSAPP_ESPERA_SEGUNDOS` | `4` | Pausa sin mensajes nuevos tras la que se responde. |
| `WHATSAPP_ESPERA_MAXIMA_SEGUNDOS` | `15` | Si el cliente no deja de escribir, se responde igual tras este tiempo. |

Las fechas se guardan en hora local de la clínica, sin zona horaria. `dia_semana` usa 0 = lunes … 6 = domingo.

## Migrar a MySQL o PostgreSQL

1. Instalar el driver: `pip install "psycopg[binary]"` (PostgreSQL) o `pip install PyMySQL` (MySQL).
2. Cambiar `DATABASE_URL` en `.env`:
   - `postgresql+psycopg://usuario:clave@localhost:5432/veterinaria`
   - `mysql+pymysql://usuario:clave@localhost:3306/veterinaria`
3. Ejecutar `python -m scripts.crear_base`.

No hay SQL propio de SQLite en la lógica. Las restricciones (`CHECK`, llaves foráneas, `UNIQUE`) y los bloqueos funcionan en los tres motores. Para un sistema en producción conviene manejar los cambios de esquema con Alembic.

## Conectar WhatsApp (Meta Cloud API)

El canal ya está implementado en `app/canales/whatsapp.py`. Solo falta configurarlo en Meta y poner las credenciales en el `.env`.

### Cómo responde en WhatsApp

1. Meta envía cada mensaje a `POST /whatsapp/webhook`. El servidor valida la firma `X-Hub-Signature-256` (solo Meta puede llamarlo), ignora los repetidos y los avisos de entrega, y responde 200 de inmediato.
2. **Los mensajes seguidos se juntan.** Cada mensaje nuevo reinicia una espera de `WHATSAPP_ESPERA_SEGUNDOS` (4 s). Cuando el cliente deja de escribir, todos sus mensajes van juntos al bot y recibe una sola respuesta. Si no para de escribir, se responde igual a los `WHATSAPP_ESPERA_MAXIMA_SEGUNDOS` (15 s). Lo que escriba mientras el bot responde se junta para la respuesta siguiente.
3. El bot usa como sesión `whatsapp-<número>` y como celular verificado el número del remitente (`51987111222`; se le quita el `51` para buscarlo). Por eso no pide el celular y solo puede consultar los datos de ese número.
4. Las notas de voz, fotos, videos y stickers llegan al bot como una marca `[Adjunto: ...]`; el bot explica que por ahora solo lee texto. Si una foto trae texto (leyenda), también lo lee.
5. La respuesta se envía con la API de WhatsApp. Si pasa de 4096 caracteres, se parte en varios mensajes. El mensaje del cliente se marca como leído.

### Configuración en Meta

1. Crea una app en [Meta for Developers](https://developers.facebook.com/) y agrégale el producto **WhatsApp**. Meta te da un número de prueba al que puedes escribir desde hasta 5 celulares que registres.
2. Copia estos datos en tu `.env`:
   - `WHATSAPP_PHONE_NUMBER_ID`: el identificador del número, en la sección de WhatsApp de la app.
   - `WHATSAPP_TOKEN`: el token de acceso. El que aparece en la configuración inicial dura 24 horas. Para uso real, crea un usuario del sistema en Meta Business y genera un token permanente con permiso para enviar mensajes de WhatsApp.
   - `WHATSAPP_APP_SECRET`: la clave secreta de la app (configuración básica de la app).
   - `WHATSAPP_VERIFY_TOKEN`: una palabra secreta que inventas tú.
3. Publica el servidor con una dirección HTTPS (ver "Puesta en producción"). Para probar desde tu computadora puedes usar un túnel como ngrok o Cloudflare Tunnel.
4. En la configuración de WhatsApp de la app, registra el webhook:
   - URL: `https://<tu-dominio>/whatsapp/webhook`
   - Token de verificación: el mismo de `WHATSAPP_VERIFY_TOKEN`.
   - Suscríbete al campo **messages**.
5. Escribe al número desde tu celular.
6. Para atender clientes reales: agrega el número propio de la clínica y completa la verificación del negocio en Meta Business (puede pedir datos como el RUC).

Costos y reglas de Meta (revísalos al configurar, porque cambian):

- Responder dentro de las 24 horas desde el último mensaje del cliente no tiene costo.
- Los mensajes que inicia la clínica (por ejemplo, recordatorios) necesitan plantillas aprobadas y sí tienen costo.
- Meta no permite asistentes de IA de uso general en esta API, pero sí bots de atención de un negocio como este.

## Puesta en producción

Lo mínimo para que funcione con clientes reales:

| Pieza | Recomendación | Por qué |
|---|---|---|
| **Servidor de la aplicación** | Una plataforma administrada (Railway, Render, Fly.io…) o un servidor virtual (VPS) | Debe estar encendido todo el día y tener HTTPS, que WhatsApp exige. Las plataformas dan HTTPS y despliegan directo desde GitHub; un VPS da más control, pero tú mantienes el sistema, el HTTPS y los reinicios. |
| **Base de datos** | PostgreSQL administrado (el de la misma plataforma, Neon, Supabase…) | En las plataformas el disco se borra en cada despliegue, así que un archivo SQLite se perdería. PostgreSQL administrado incluye copias de seguridad. |
| **Secretos** | Variables de entorno en el panel de la plataforma | `ANTHROPIC_API_KEY`, `DATABASE_URL`, `WHATSAPP_*` y `CLINICA_*`. El `.env` nunca se sube al repositorio. |
| **Anthropic** | Cuenta con saldo y un límite de gasto mensual | Cada mensaje consume tokens. El log del bot registra cuántos usa cada respuesta. |
| **Meta / WhatsApp** | App, número propio y verificación del negocio | Ver la sección anterior. |

Comando de arranque en el servidor (un solo proceso: el agrupador de mensajes y los candados por sesión viven en memoria):

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Para PostgreSQL agrega `psycopg[binary]` a `requirements.txt` y usa `DATABASE_URL=postgresql+psycopg://...`. Las tablas se crean solas al arrancar la aplicación. **En producción no ejecutes `scripts/crear_base.py`**, porque carga datos de prueba.

### Datos reales de la clínica

El bot lee veterinarios, horarios, servicios, clientes, mascotas, citas y vacunas de la base, pero **este proyecto no tiene un panel para administrarlos**. Antes de abrirlo a clientes hay que decidir cómo se cargan y se mantienen al día. Por ejemplo, marcar citas como atendidas, registrar vacunas aplicadas y dar de alta clientes nuevos. Opciones:

- Importar desde Excel o CSV.
- Un panel de administración sencillo para el personal.
- Conectarlo al sistema que la clínica ya use.

### Ruta sugerida

1. Probar en tu computadora con tu API key (página web).
2. Desplegar en una plataforma con PostgreSQL y probar la página pública.
3. Conectar WhatsApp con el número de prueba de Meta.
4. Cargar los datos reales de la clínica.
5. Número propio, verificación del negocio y un piloto con pocos clientes.
6. Abrirlo a todos los clientes.

## Limitaciones del prototipo

- **Identificación en la web:** cualquiera que escriba un número registrado ve las mascotas y citas de ese cliente. Es solo una simulación de WhatsApp, donde el número sí está verificado. Para una web real hace falta verificar el número (por ejemplo, con un código por SMS).
- **Protección de datos personales:** el sistema guarda datos de clientes y el texto de las conversaciones. En Perú esto está regulado por la Ley N.° 29733, de Protección de Datos Personales. Antes de usarlo con clientes reales, revisa con un asesor qué aviso de privacidad y consentimiento necesitas.
- **Feriados:** la agenda no conoce los feriados (Fiestas Patrias, Navidad, etc.) y ofrecería horarios esos días si el veterinario tiene turno.
- **Urgencias:** quedan registradas en `sesion_chat` (`urgente`, `motivo_urgencia`) y en el log, pero no hay un panel para verlas.
- **Clientes nuevos:** el bot no registra clientes ni mascotas; si el número no existe, pide llamar o acercarse a la clínica.
- **Un solo proceso:** el turno por sesión, el agrupador de mensajes y el control de repetidos de WhatsApp viven en memoria. Con varios procesos o servidores habría que pasarlos a la base o a Redis. Si el servidor se cae, se pierden los mensajes que estaban esperando esos pocos segundos.
- **Bases creadas con una versión anterior:** si creaste la base antes de este cambio, vuelve a crearla con `python -m scripts.crear_base --reiniciar` (cambiaron columnas de `sesion_chat`).
- **Confirmaciones:** la regla de pedir confirmación antes de agendar, cancelar o reprogramar está en las instrucciones del modelo. El backend valida los datos, pero no puede saber si el cliente dijo "sí".
