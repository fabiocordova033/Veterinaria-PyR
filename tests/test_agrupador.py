"""Pruebas del agrupador de mensajes seguidos (paso 4)."""

import threading
import time

import pytest

from app.bot.agrupador import AgrupadorMensajes


class Registro:
    def __init__(self, bloquear_primero=False):
        self.llamadas = []
        self.candado = threading.Lock()
        self.puede_seguir = threading.Event()
        self.empezo = threading.Event()
        self.bloquear_primero = bloquear_primero

    def __call__(self, clave, elementos):
        self.empezo.set()
        if self.bloquear_primero and not self.llamadas:
            self.puede_seguir.wait(5)
        with self.candado:
            self.llamadas.append((clave, list(elementos)))


def test_mensajes_seguidos_se_responden_juntos():
    registro = Registro()
    agrupador = AgrupadorMensajes(registro, espera_segundos=0.3, espera_maxima_segundos=3)
    for texto in ("hola", "quiero una cita", "para Luna"):
        agrupador.agregar("51987111222", texto)
        time.sleep(0.05)
    assert agrupador.esperar_inactivo(5)
    assert registro.llamadas == [("51987111222", ["hola", "quiero una cita", "para Luna"])]


def test_mensajes_separados_por_una_pausa_se_responden_por_separado():
    registro = Registro()
    agrupador = AgrupadorMensajes(registro, espera_segundos=0.1, espera_maxima_segundos=1)
    agrupador.agregar("a", "primero")
    assert agrupador.esperar_inactivo(5)
    agrupador.agregar("a", "segundo")
    assert agrupador.esperar_inactivo(5)
    assert registro.llamadas == [("a", ["primero"]), ("a", ["segundo"])]


def test_cada_cliente_tiene_su_propio_grupo():
    registro = Registro()
    agrupador = AgrupadorMensajes(registro, espera_segundos=0.2, espera_maxima_segundos=2)
    agrupador.agregar("a", "hola de A")
    agrupador.agregar("b", "hola de B")
    agrupador.agregar("a", "otra de A")
    assert agrupador.esperar_inactivo(5)
    assert sorted(registro.llamadas) == [("a", ["hola de A", "otra de A"]), ("b", ["hola de B"])]


def test_lo_que_llega_mientras_responde_va_en_el_grupo_siguiente():
    registro = Registro(bloquear_primero=True)
    agrupador = AgrupadorMensajes(registro, espera_segundos=0.1, espera_maxima_segundos=1)
    agrupador.agregar("a", "uno")
    assert registro.empezo.wait(5)  # el bot está "respondiendo" el primer grupo
    agrupador.agregar("a", "dos")
    agrupador.agregar("a", "tres")
    time.sleep(0.3)
    assert registro.llamadas == []  # nunca dos respuestas en curso para el mismo cliente
    registro.puede_seguir.set()
    assert agrupador.esperar_inactivo(5)
    assert registro.llamadas == [("a", ["uno"]), ("a", ["dos", "tres"])]


def test_si_el_cliente_no_para_de_escribir_se_responde_al_llegar_a_la_espera_maxima():
    registro = Registro()
    agrupador = AgrupadorMensajes(registro, espera_segundos=0.3, espera_maxima_segundos=0.5)
    enviados = [f"m{i}" for i in range(12)]
    for texto in enviados:  # un mensaje cada 0,1 s durante 1,2 s: nunca hay 0,3 s de pausa
        agrupador.agregar("a", texto)
        time.sleep(0.1)
    assert agrupador.esperar_inactivo(5)
    assert len(registro.llamadas) >= 2
    assert [t for _, grupo in registro.llamadas for t in grupo] == enviados  # nada se pierde ni se desordena


def test_vaciar_procesa_lo_pendiente_de_inmediato():
    registro = Registro()
    agrupador = AgrupadorMensajes(registro, espera_segundos=10, espera_maxima_segundos=20)
    agrupador.agregar("a", "pendiente")
    agrupador.vaciar()
    assert registro.llamadas == [("a", ["pendiente"])]
    assert agrupador.pendientes() == 0


def test_un_error_al_procesar_no_bloquea_al_cliente():
    llamadas = []

    def procesar(clave, elementos):
        llamadas.append(elementos)
        if len(llamadas) == 1:
            raise RuntimeError("falló")

    agrupador = AgrupadorMensajes(procesar, espera_segundos=0.1, espera_maxima_segundos=1)
    agrupador.agregar("a", "uno")
    assert agrupador.esperar_inactivo(5)
    agrupador.agregar("a", "dos")
    assert agrupador.esperar_inactivo(5)
    assert llamadas == [["uno"], ["dos"]]


def test_valida_los_tiempos():
    with pytest.raises(ValueError):
        AgrupadorMensajes(lambda c, e: None, espera_segundos=5, espera_maxima_segundos=2)
