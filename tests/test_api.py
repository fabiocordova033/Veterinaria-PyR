"""Pruebas del canal web (FastAPI) con un motor simulado."""

import pytest
from fastapi.testclient import TestClient

from app.bot.motor import ErrorServicioIA, RespuestaBot
from app.canales import comun
from app.main import app


class MotorSimulado:
    def __init__(self):
        self.llamadas = []

    def responder(self, sesion_id, texto, *, canal="web", telefono_verificado=None):
        self.llamadas.append((sesion_id, texto, canal))
        return RespuestaBot(sesion_id=sesion_id, texto=f"Recibí: {texto}", urgencia=False)


@pytest.fixture
def cliente(datos):
    motor = MotorSimulado()
    app.dependency_overrides[comun.obtener_motor] = lambda: motor
    with TestClient(app) as cliente_http:
        cliente_http.motor = motor
        yield cliente_http
    app.dependency_overrides.clear()


def test_chat_crea_una_sesion_y_la_reutiliza(cliente):
    primera = cliente.post("/chat", json={"mensaje": "Hola"}).json()
    assert primera["respuesta"] == "Recibí: Hola"
    assert primera["sesion_id"].startswith("web-") and len(primera["sesion_id"]) == 36

    segunda = cliente.post("/chat", json={"sesion_id": primera["sesion_id"], "mensaje": "¿Y el horario?"}).json()
    assert segunda["sesion_id"] == primera["sesion_id"]


def test_chat_no_acepta_identificadores_de_otros_canales(cliente):
    respuesta = cliente.post("/chat", json={"sesion_id": "whatsapp-51900000001", "mensaje": "Hola"}).json()
    assert respuesta["sesion_id"].startswith("web-")
    assert cliente.motor.llamadas[-1][0] == respuesta["sesion_id"]


def test_chat_valida_el_mensaje(cliente):
    assert cliente.post("/chat", json={"mensaje": ""}).status_code == 422
    assert cliente.post("/chat", json={"mensaje": "   "}).status_code == 422
    assert cliente.post("/chat", json={"mensaje": "x" * 2001}).status_code == 422


def test_sin_api_key_responde_503_con_mensaje_claro(datos, monkeypatch):
    def sin_clave():
        raise ErrorServicioIA("Falta configurar ANTHROPIC_API_KEY en el archivo .env.")

    monkeypatch.setattr(comun, "_motor", None)
    monkeypatch.setattr(comun, "crear_motor_por_defecto", sin_clave)
    with TestClient(app) as cliente_http:
        respuesta = cliente_http.post("/chat", json={"mensaje": "Hola"})
    assert respuesta.status_code == 503
    assert "ANTHROPIC_API_KEY" in respuesta.json()["detail"]


def test_pagina_de_chat_y_datos_de_la_clinica(cliente):
    pagina = cliente.get("/")
    assert pagina.status_code == 200 and "chat.js" in pagina.text
    assert cliente.get("/chat.js").status_code == 200
    assert "nombre" in cliente.get("/clinica").json()
    assert cliente.get("/salud").json() == {"estado": "ok"}
