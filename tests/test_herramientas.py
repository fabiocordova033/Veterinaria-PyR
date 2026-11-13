"""Pruebas de las herramientas: validación de argumentos y aislamiento entre clientes."""

import json

import pytest

from app.bot import sesiones
from app.bot.herramientas import DEFINICIONES, ContextoHerramienta, definiciones_herramientas, ejecutar_herramienta
from app.db.base import transaccion
from app.db.modelos import SesionChat
from tests.conftest import AHORA


@pytest.fixture
def ctx(datos):
    with transaccion() as bd:
        sesiones.obtener_o_crear(bd, "web-prueba", "web", AHORA)
    return ContextoHerramienta(sesion_id="web-prueba", ahora=AHORA)


def ejecutar(nombre, entrada, ctx):
    resultado = ejecutar_herramienta(nombre, entrada, ctx)
    return json.loads(resultado.contenido), resultado.es_error


def test_definiciones_validas_para_modo_estricto():
    for definicion in definiciones_herramientas(estrictas=True):
        esquema = definicion["input_schema"]
        assert definicion["strict"] is True
        assert esquema["additionalProperties"] is False
        assert set(esquema["required"]) <= set(esquema["properties"])
    assert len({d["name"] for d in DEFINICIONES}) == len(DEFINICIONES)


@pytest.mark.parametrize("nombre", ["ver_citas", "proxima_vacuna", "cancelar_cita", "agendar_cita"])
def test_las_herramientas_de_cliente_exigen_identificacion(ctx, nombre):
    entrada = {
        "mascota_id": 1, "cita_id": 1, "servicio_id": 1, "veterinario_id": 1,
        "fecha_hora": "2030-01-08T09:00", "motivo": "x",
    }
    datos, es_error = ejecutar(nombre, entrada, ctx)
    assert es_error
    assert "identificado" in datos["error"]


def test_buscar_cliente_asocia_la_sesion(datos, ctx):
    resultado, es_error = ejecutar("buscar_cliente", {"telefono": "+57 300 000 0001"}, ctx)
    assert not es_error
    assert resultado["encontrado"] is True
    assert [m["nombre"] for m in resultado["mascotas"]] == ["Firulais", "Mishi"]
    assert ctx.propietario_id == datos.propietario_a
    with transaccion() as bd:
        assert bd.get(SesionChat, "web-prueba").propietario_id == datos.propietario_a


def test_buscar_cliente_inexistente_no_asocia(ctx):
    resultado, es_error = ejecutar("buscar_cliente", {"telefono": "3999999999"}, ctx)
    assert not es_error and resultado["encontrado"] is False
    assert ctx.propietario_id is None


def test_no_se_puede_cambiar_de_cliente_en_la_misma_conversacion(datos, ctx):
    ejecutar("buscar_cliente", {"telefono": "3000000001"}, ctx)
    resultado, es_error = ejecutar("buscar_cliente", {"telefono": "3000000002"}, ctx)
    assert es_error and "otro cliente" in resultado["error"]
    assert ctx.propietario_id == datos.propietario_a


def test_en_whatsapp_solo_se_consulta_el_numero_verificado(datos):
    with transaccion() as bd:
        sesiones.obtener_o_crear(bd, "whatsapp-573000000001", "whatsapp", AHORA, "573000000001")
    ctx = ContextoHerramienta(sesion_id="whatsapp-573000000001", ahora=AHORA, telefono_verificado="573000000001")
    resultado, es_error = ejecutar("buscar_cliente", {"telefono": "3000000002"}, ctx)
    assert es_error and "solo se puede consultar" in resultado["error"]
    resultado, es_error = ejecutar("buscar_cliente", {"telefono": "3000000001"}, ctx)
    assert not es_error and resultado["propietario"]["nombre"] == "Ana"


def test_no_se_ven_las_mascotas_de_otro_cliente(datos, ctx):
    ejecutar("buscar_cliente", {"telefono": "3000000001"}, ctx)
    resultado, es_error = ejecutar("ver_citas", {"mascota_id": datos.b1}, ctx)
    assert es_error and "No encontré esa mascota" in resultado["error"]


def test_agendar_por_herramienta_y_error_legible_si_choca(datos, ctx):
    ejecutar("buscar_cliente", {"telefono": "3000000001"}, ctx)
    entrada = {
        "mascota_id": datos.a1, "servicio_id": datos.consulta, "veterinario_id": datos.vet1,
        "fecha_hora": "2030-01-08T09:00", "motivo": "Vacuna anual",
    }
    resultado, es_error = ejecutar("agendar_cita", entrada, ctx)
    assert not es_error and resultado["agendada"] is True
    resultado, es_error = ejecutar("agendar_cita", {**entrada, "mascota_id": datos.a2}, ctx)
    assert es_error and "ya tiene una cita" in resultado["error"]


def test_argumentos_invalidos_devuelven_error_y_no_excepcion(datos, ctx):
    ejecutar("buscar_cliente", {"telefono": "3000000001"}, ctx)
    resultado, es_error = ejecutar(
        "consultar_disponibilidad", {"servicio_id": datos.consulta, "fecha": "el lunes"}, ctx
    )
    assert es_error and "no es válida" in resultado["error"]
    resultado, es_error = ejecutar("ver_citas", {"mascota_id": "uno"}, ctx)
    assert es_error and "número entero" in resultado["error"]
    resultado, es_error = ejecutar("borrar_todo", {}, ctx)
    assert es_error and "no existe" in resultado["error"]


def test_derivar_a_humano_marca_la_sesion(ctx):
    resultado, es_error = ejecutar("derivar_a_humano", {"motivo": "Convulsiones", "urgente": True}, ctx)
    assert not es_error and resultado["derivado"] is True
    assert ctx.requiere_humano is True
    with transaccion() as bd:
        chat = bd.get(SesionChat, "web-prueba")
        assert chat.requiere_humano and chat.urgente and chat.motivo_derivacion == "Convulsiones"
