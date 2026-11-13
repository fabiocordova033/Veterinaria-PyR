"""Pruebas del canal WhatsApp con webhooks simulados de Meta (sin red)."""

import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bot.motor import ErrorServicioIA, MotorBot, RespuestaBot
from app.canales import whatsapp
from app.canales.whatsapp import CanalWhatsApp, ClienteWhatsApp, ConfigWhatsApp, partir_texto
from app.main import app
from tests.conftest import AHORA
from tests.test_motor import LLMSimulado, respuesta, texto

NUMERO = "51987111222"
CONFIG = ConfigWhatsApp(
    token="token-de-prueba",
    phone_number_id="1234567890",
    verify_token="verificar-123",
    app_secret="secreto-de-la-app",
    version_api="v24.0",
    espera_segundos=0.2,
    espera_maxima_segundos=2,
)


class APIWhatsAppSimulada:
    """Captura lo que el canal envía a graph.facebook.com."""

    def __init__(self):
        self.solicitudes = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.solicitudes.append((request, json.loads(request.content)))
        return httpx.Response(200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.respuesta"}]})

    def textos_enviados(self):
        return [c["text"]["body"] for _, c in self.solicitudes if c.get("type") == "text"]


class MotorSimulado:
    def __init__(self, error=None):
        self.llamadas = []
        self.error = error

    def responder(self, sesion_id, texto, *, canal="web", telefono_verificado=None):
        self.llamadas.append({"sesion_id": sesion_id, "texto": texto, "canal": canal, "telefono": telefono_verificado})
        if self.error:
            raise self.error
        return RespuestaBot(sesion_id=sesion_id, texto="¡Hola! ¿En qué te ayudo?", urgencia=False)


def webhook(*mensajes, phone_number_id=CONFIG.phone_number_id):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "51015551234", "phone_number_id": phone_number_id},
                            "contacts": [{"profile": {"name": "Rosa"}, "wa_id": NUMERO}],
                            "messages": list(mensajes),
                        },
                    }
                ],
            }
        ],
    }


def mensaje_texto(id_, cuerpo, numero=NUMERO):
    return {"from": numero, "id": id_, "timestamp": "1700000000", "type": "text", "text": {"body": cuerpo}}


def firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(CONFIG.app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()


@pytest.fixture
def entorno(datos):
    api = APIWhatsAppSimulada()
    motor = MotorSimulado()
    cliente = ClienteWhatsApp(CONFIG, http=httpx.Client(transport=httpx.MockTransport(api)))
    canal = CanalWhatsApp(CONFIG, cliente, lambda: motor)
    app.dependency_overrides[whatsapp.obtener_canal] = lambda: canal
    with TestClient(app) as http:
        yield http, canal, motor, api
    app.dependency_overrides.clear()


def publicar(http, carga):
    cuerpo = json.dumps(carga).encode()
    return http.post(
        "/whatsapp/webhook",
        content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": firmar(cuerpo)},
    )


# ---------------------------------------------------------------------------


def test_verificacion_del_webhook(entorno):
    http, *_ = entorno
    ok = http.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verificar-123", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    mal = http.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "42"})
    assert mal.status_code == 403


def test_sin_configuracion_el_canal_no_existe(datos):
    with TestClient(app) as http:
        assert http.get("/whatsapp/webhook").status_code == 404


def test_rechaza_webhooks_sin_firma_valida(entorno):
    http, canal, motor, _ = entorno
    cuerpo = json.dumps(webhook(mensaje_texto("wamid.1", "hola"))).encode()
    respuesta_http = http.post("/whatsapp/webhook", content=cuerpo, headers={"X-Hub-Signature-256": "sha256=falsa"})
    assert respuesta_http.status_code == 401
    assert canal.agrupador.pendientes() == 0 and motor.llamadas == []


def test_mensajes_seguidos_reciben_una_sola_respuesta(entorno):
    http, canal, motor, api = entorno
    assert publicar(http, webhook(mensaje_texto("wamid.1", "hola"))).status_code == 200
    assert publicar(http, webhook(mensaje_texto("wamid.2", "quiero una cita"))).status_code == 200
    assert publicar(http, webhook(mensaje_texto("wamid.3", "para Luna"))).status_code == 200
    assert canal.agrupador.esperar_inactivo(5)

    assert len(motor.llamadas) == 1
    llamada = motor.llamadas[0]
    assert llamada["texto"] == "hola\nquiero una cita\npara Luna"
    assert llamada["sesion_id"] == f"whatsapp-{NUMERO}" and llamada["canal"] == "whatsapp"
    assert llamada["telefono"] == NUMERO  # número verificado por WhatsApp
    assert api.textos_enviados() == ["¡Hola! ¿En qué te ayudo?"]
    # Se marca como leído el último mensaje del grupo.
    leidos = [c for _, c in api.solicitudes if c.get("status") == "read"]
    assert leidos == [{"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.3"}]


def test_formato_de_la_solicitud_a_la_api_de_whatsapp(entorno):
    http, canal, _, api = entorno
    publicar(http, webhook(mensaje_texto("wamid.1", "hola")))
    assert canal.agrupador.esperar_inactivo(5)
    peticion, cuerpo = [(r, c) for r, c in api.solicitudes if c.get("type") == "text"][0]
    assert str(peticion.url) == "https://graph.facebook.com/v24.0/1234567890/messages"
    assert peticion.headers["Authorization"] == "Bearer token-de-prueba"
    assert cuerpo == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": NUMERO,
        "type": "text",
        "text": {"preview_url": False, "body": "¡Hola! ¿En qué te ayudo?"},
    }


def test_mensajes_repetidos_por_meta_se_ignoran(entorno):
    http, canal, motor, _ = entorno
    carga = webhook(mensaje_texto("wamid.1", "hola"))
    publicar(http, carga)
    publicar(http, carga)  # Meta reintenta el mismo webhook
    assert canal.agrupador.esperar_inactivo(5)
    assert [ll["texto"] for ll in motor.llamadas] == ["hola"]


def test_audios_y_fotos_llegan_al_bot_como_una_marca(entorno):
    http, canal, motor, _ = entorno
    audio = {"from": NUMERO, "id": "wamid.a", "type": "audio", "audio": {"id": "media1", "mime_type": "audio/ogg"}}
    foto = {"from": NUMERO, "id": "wamid.f", "type": "image", "image": {"id": "media2", "caption": "mira su patita"}}
    publicar(http, webhook(audio, foto))
    assert canal.agrupador.esperar_inactivo(5)
    texto_al_bot = motor.llamadas[0]["texto"]
    assert "[Adjunto: el cliente envió una nota de voz o audio.]" in texto_al_bot
    assert "[Adjunto: el cliente envió una foto.]\nmira su patita" in texto_al_bot


def test_avisos_de_entrega_reacciones_y_otros_numeros_se_ignoran(entorno):
    http, canal, motor, _ = entorno
    estados = webhook()
    estados["entry"][0]["changes"][0]["value"]["statuses"] = [{"id": "wamid.x", "status": "delivered"}]
    publicar(http, estados)
    reaccion = {"from": NUMERO, "id": "wamid.r", "type": "reaction", "reaction": {"emoji": "👍"}}
    publicar(http, webhook(reaccion))
    publicar(http, webhook(mensaje_texto("wamid.o", "hola"), phone_number_id="otro-numero"))
    assert canal.agrupador.esperar_inactivo(5)
    assert motor.llamadas == []


def test_si_el_bot_falla_el_cliente_recibe_los_telefonos(entorno):
    http, canal, motor, api = entorno
    motor.error = ErrorServicioIA("sin servicio")
    publicar(http, webhook(mensaje_texto("wamid.1", "hola")))
    assert canal.agrupador.esperar_inactivo(5)
    (enviado,) = api.textos_enviados()
    assert "no puedo responder" in enviado and "urgencia" in enviado


def test_respuestas_largas_se_parten_en_varios_mensajes():
    texto_largo = ("Línea de prueba.\n" * 400).strip()
    partes = partir_texto(texto_largo)
    assert len(partes) == 2 and all(len(p) <= 4096 for p in partes)
    assert "\n".join(partes) == texto_largo


def test_de_punta_a_punta_con_el_motor_real(datos):
    """Webhook -> agrupador -> MotorBot (Claude simulado) -> respuesta por WhatsApp."""
    api = APIWhatsAppSimulada()
    llm = LLMSimulado(respuesta("end_turn", texto("¡Hola, Ana! ¿En qué te ayudo con Firulais o Mishi?")))
    motor = MotorBot(llm, reloj=lambda: AHORA)
    cliente = ClienteWhatsApp(CONFIG, http=httpx.Client(transport=httpx.MockTransport(api)))
    canal = CanalWhatsApp(CONFIG, cliente, lambda: motor)

    canal.recibir(webhook(mensaje_texto("wamid.1", "hola", numero="51900000001"), mensaje_texto("wamid.2", "soy Ana", numero="51900000001")))
    assert canal.agrupador.esperar_inactivo(5)

    contenido = llm.solicitudes[0]["messages"][0]["content"]
    assert "Canal: whatsapp." in contenido[0]["text"]
    assert "Celular verificado por el canal: 51900000001." in contenido[0]["text"]
    assert contenido[1]["text"] == "hola\nsoy Ana"
    assert api.textos_enviados() == ["¡Hola, Ana! ¿En qué te ayudo con Firulais o Mishi?"]
