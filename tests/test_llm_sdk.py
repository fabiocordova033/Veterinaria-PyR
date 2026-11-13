"""Prueba de integración con el SDK `anthropic` real, pero sin red.

Un transporte HTTP simulado captura lo que el SDK enviaría a la API y responde
con mensajes preparados. Así se verifica el formato de las solicitudes
(modelo, herramientas, respaldo de modelo, caché) y que el historial se
reenvía tal cual, sin gastar créditos.
"""

import json
from dataclasses import replace

import anthropic
import httpx2

from app.bot.llm import BETA_FALLBACK, ClienteAnthropic
from app.bot.motor import MotorBot
from app.config import ajustes
from tests.conftest import AHORA


def _mensaje(stop_reason, contenido):
    return {
        "id": "msg_prueba",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": contenido,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 80},
    }


class APISimulada:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.solicitudes = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.solicitudes.append((request, json.loads(request.content)))
        return httpx2.Response(200, json=self.respuestas.pop(0))


def _motor(api, **cambios):
    ajustes_prueba = replace(ajustes, anthropic_api_key="sk-ant-prueba", **cambios)
    sdk = anthropic.Anthropic(
        api_key="sk-ant-prueba",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(api)),
    )
    return MotorBot(ClienteAnthropic(ajustes_prueba, cliente_sdk=sdk), ajustes_prueba, reloj=lambda: AHORA)


def test_solicitudes_con_respaldo_de_modelo_y_herramientas(datos):
    api = APISimulada(
        _mensaje(
            "tool_use",
            [
                {"type": "thinking", "thinking": "", "signature": "firma-1"},
                {"type": "tool_use", "id": "toolu_1", "name": "info_servicios", "input": {}},
            ],
        ),
        _mensaje("end_turn", [{"type": "text", "text": "La consulta general cuesta S/ 70.00."}]),
    )
    resultado = _motor(api, fallbacks="default", esfuerzo="low").responder("web-" + "9" * 32, "¿Precio de la consulta?")

    assert resultado.texto == "La consulta general cuesta S/ 70.00."
    assert len(api.solicitudes) == 2

    peticion, cuerpo = api.solicitudes[0]
    assert peticion.url.path == "/v1/messages"
    assert BETA_FALLBACK in peticion.headers["anthropic-beta"]
    assert "sk-ant-prueba" in peticion.headers.get("authorization", "") + peticion.headers.get("x-api-key", "")
    assert cuerpo["model"] == "claude-opus-5-5"
    assert cuerpo["fallbacks"] == "default"
    assert cuerpo["output_config"] == {"effort": "low"}
    assert cuerpo["cache_control"] == {"type": "ephemeral"}
    assert "tool_choice" not in cuerpo and "temperature" not in cuerpo and "thinking" not in cuerpo
    assert {h["name"] for h in cuerpo["tools"]} >= {"buscar_cliente", "agendar_cita", "consultar_disponibilidad"}
    assert all(h["strict"] is True for h in cuerpo["tools"])

    # La segunda solicitud reenvía la respuesta del modelo sin cambios y agrega el resultado de la herramienta.
    _, cuerpo2 = api.solicitudes[1]
    asistente, resultados = cuerpo2["messages"][1], cuerpo2["messages"][2]
    assert asistente["content"][0] == {"type": "thinking", "thinking": "", "signature": "firma-1"}
    assert asistente["content"][1]["name"] == "info_servicios"
    assert resultados["content"][0]["tool_use_id"] == "toolu_1"
    assert json.loads(resultados["content"][0]["content"])["servicios"][0]["nombre"] == "Consulta general"


def test_sin_respaldo_usa_el_endpoint_estandar(datos):
    api = APISimulada(_mensaje("end_turn", [{"type": "text", "text": "¡Hola!"}]))
    _motor(api, fallbacks=None, esfuerzo=None).responder("web-" + "8" * 32, "Hola")
    peticion, cuerpo = api.solicitudes[0]
    assert "anthropic-beta" not in peticion.headers
    assert "fallbacks" not in cuerpo and "output_config" not in cuerpo
