"""Pruebas de la lógica de agenda, con énfasis en que no se crucen las citas."""

import threading
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.db.base import transaccion
from app.db.modelos import Cita, Mascota
from app.servicios import agenda
from app.servicios.errores import ErrorNegocio
from tests.conftest import AHORA, LUNES, MARTES, MIERCOLES, agendar

# ---------------------------------------------------------------------------
# Agendar
# ---------------------------------------------------------------------------


def test_agendar_calcula_el_fin_con_la_duracion_del_servicio(datos):
    cita = agendar(datos, "2030-01-08T09:00", servicio=datos.limpieza)
    assert cita["estado"] == "programada"
    assert cita["fecha_hora"] == "2030-01-08T09:00"
    assert cita["hora_fin"] == "10:30"  # 90 minutos


def test_rechaza_otra_cita_a_la_misma_hora_con_el_mismo_veterinario(datos):
    agendar(datos, "2030-01-08T09:00", mascota=datos.a1)
    with pytest.raises(ErrorNegocio, match="ya tiene una cita"):
        agendar(datos, "2030-01-08T09:00", mascota=datos.b1, propietario=datos.propietario_b)


@pytest.mark.parametrize(
    ("inicio", "servicio"),
    [
        ("2030-01-08T09:15", "consulta"),  # empieza en medio de la existente
        ("2030-01-08T08:45", "consulta"),  # termina en medio de la existente
        ("2030-01-08T08:30", "limpieza"),  # 08:30-10:00 envuelve a la existente
    ],
)
def test_rechaza_cruces_parciales(datos, inicio, servicio):
    agendar(datos, "2030-01-08T09:00")  # 09:00-09:30
    with pytest.raises(ErrorNegocio, match="ya tiene una cita"):
        agendar(datos, inicio, mascota=datos.b1, propietario=datos.propietario_b, servicio=getattr(datos, servicio))


def test_rechaza_una_cita_corta_dentro_de_una_larga(datos):
    agendar(datos, "2030-01-08T09:00", servicio=datos.limpieza)  # 09:00-10:30
    with pytest.raises(ErrorNegocio, match="ya tiene una cita"):
        agendar(datos, "2030-01-08T09:30", mascota=datos.b1, propietario=datos.propietario_b)


def test_permite_citas_contiguas(datos):
    agendar(datos, "2030-01-08T09:00", mascota=datos.a1)  # 09:00-09:30
    agendar(datos, "2030-01-08T09:30", mascota=datos.a2)  # empieza justo cuando termina la anterior
    agendar(datos, "2030-01-08T08:30", mascota=datos.b1, propietario=datos.propietario_b)  # termina a las 09:00
    with transaccion() as bd:
        assert bd.scalar(select(func.count()).select_from(Cita)) == 3


def test_otro_veterinario_puede_atender_a_la_misma_hora(datos):
    agendar(datos, "2030-01-07T10:00", veterinario=datos.vet1, mascota=datos.a1)
    cita = agendar(datos, "2030-01-07T10:00", veterinario=datos.vet2, mascota=datos.b1, propietario=datos.propietario_b)
    assert cita["veterinario"] == "Dr. Dos"


def test_una_mascota_no_puede_tener_dos_citas_que_se_crucen(datos):
    agendar(datos, "2030-01-07T10:00", veterinario=datos.vet1, mascota=datos.a1)
    with pytest.raises(ErrorNegocio, match="Firulais ya tiene otra cita"):
        agendar(datos, "2030-01-07T10:00", veterinario=datos.vet2, mascota=datos.a1)


@pytest.mark.parametrize(
    ("inicio", "servicio"),
    [
        ("2030-01-08T07:30", "consulta"),  # antes de que empiece la jornada
        ("2030-01-08T11:45", "consulta"),  # terminaría 12:15, después del bloque de la mañana
        ("2030-01-08T11:00", "limpieza"),  # 11:00-12:30 atraviesa el almuerzo
        ("2030-01-08T12:30", "consulta"),  # en el almuerzo
        ("2030-01-08T17:45", "consulta"),  # terminaría 18:15
        ("2030-01-09T09:00", "consulta"),  # miércoles: no trabaja
    ],
)
def test_rechaza_citas_fuera_del_horario_del_veterinario(datos, inicio, servicio):
    with pytest.raises(ErrorNegocio, match="no atiende en ese horario"):
        agendar(datos, inicio, servicio=getattr(datos, servicio))


def test_acepta_una_cita_que_termina_justo_al_cierre(datos):
    cita = agendar(datos, "2030-01-08T17:30")
    assert cita["hora_fin"] == "18:00"


@pytest.mark.parametrize(
    ("inicio", "mensaje"),
    [
        ("2030-01-06T09:00", "ya pasó"),
        ("2030-01-07T07:30", "ya pasó"),
        ("2030-01-07T08:30", "anticipación"),  # faltan solo 30 minutos
        ("2030-04-01T09:00", "máximo 60 días"),
    ],
)
def test_rechaza_fechas_pasadas_o_fuera_de_la_ventana(datos, inicio, mensaje):
    with pytest.raises(ErrorNegocio, match=mensaje):
        agendar(datos, inicio)


def test_no_se_puede_agendar_con_la_mascota_de_otro_cliente(datos):
    with pytest.raises(ErrorNegocio, match="No encontré esa mascota"):
        agendar(datos, "2030-01-08T09:00", mascota=datos.b1, propietario=datos.propietario_a)


def test_dos_solicitudes_simultaneas_por_el_mismo_horario_solo_agenda_una(datos):
    # Varias mascotas intentan a la vez la misma hora con la misma veterinaria.
    with transaccion() as bd:
        ids = []
        for i in range(8):
            mascota = Mascota(nombre=f"Concurrente {i}", especie="Perro", propietario_id=datos.propietario_a)
            bd.add(mascota)
            bd.flush()
            ids.append(mascota.id)

    barrera = threading.Barrier(len(ids))
    exitos, rechazos, otros = [], [], []

    def intentar(mascota_id):
        barrera.wait()
        try:
            exitos.append(agendar(datos, "2030-01-08T10:00", mascota=mascota_id))
        except ErrorNegocio as error:
            rechazos.append(error)
        except Exception as error:  # cualquier otro error haría fallar la prueba
            otros.append(error)

    hilos = [threading.Thread(target=intentar, args=(m,)) for m in ids]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)

    assert otros == []
    assert len(exitos) == 1
    assert len(rechazos) == len(ids) - 1
    with transaccion() as bd:
        en_ese_horario = bd.scalar(
            select(func.count()).select_from(Cita).where(Cita.fecha_hora == datetime(2030, 1, 8, 10, 0))
        )
    assert en_ese_horario == 1


# ---------------------------------------------------------------------------
# Cancelar y reprogramar
# ---------------------------------------------------------------------------


def test_cancelar_libera_el_horario(datos):
    cita = agendar(datos, "2030-01-08T09:00", mascota=datos.a1)
    with transaccion() as bd:
        cancelada = agenda.cancelar_cita(bd, propietario_id=datos.propietario_a, cita_id=cita["cita_id"], ahora=AHORA)
    assert cancelada["estado"] == "cancelada"
    otra = agendar(datos, "2030-01-08T09:00", mascota=datos.b1, propietario=datos.propietario_b)
    assert otra["estado"] == "programada"


def test_no_se_cancela_dos_veces_ni_una_cita_ajena(datos):
    cita = agendar(datos, "2030-01-08T09:00")
    with pytest.raises(ErrorNegocio, match="No encontré esa cita"), transaccion() as bd:
        agenda.cancelar_cita(bd, propietario_id=datos.propietario_b, cita_id=cita["cita_id"], ahora=AHORA)
    with transaccion() as bd:
        agenda.cancelar_cita(bd, propietario_id=datos.propietario_a, cita_id=cita["cita_id"], ahora=AHORA)
    with pytest.raises(ErrorNegocio, match="está cancelada"), transaccion() as bd:
        agenda.cancelar_cita(bd, propietario_id=datos.propietario_a, cita_id=cita["cita_id"], ahora=AHORA)


def test_no_se_cancela_una_cita_que_ya_paso(datos):
    cita = agendar(datos, "2030-01-08T09:00")
    despues = datetime(2030, 1, 8, 9, 5)
    with pytest.raises(ErrorNegocio, match="ya pasó"), transaccion() as bd:
        agenda.cancelar_cita(bd, propietario_id=datos.propietario_a, cita_id=cita["cita_id"], ahora=despues)


def _reprogramar(datos, cita_id, nueva, propietario=None):
    with transaccion() as bd:
        return agenda.reprogramar_cita(
            bd,
            propietario_id=propietario or datos.propietario_a,
            cita_id=cita_id,
            nueva_fecha_hora=datetime.fromisoformat(nueva),
            ahora=AHORA,
        )


def test_reprogramar_a_un_horario_libre_conserva_la_duracion(datos):
    cita = agendar(datos, "2030-01-08T09:00", servicio=datos.limpieza)
    movida = _reprogramar(datos, cita["cita_id"], "2030-01-08T14:00")
    assert movida["fecha_hora"] == "2030-01-08T14:00"
    assert movida["hora_fin"] == "15:30"
    # El horario anterior quedó libre.
    agendar(datos, "2030-01-08T09:00", mascota=datos.b1, propietario=datos.propietario_b)


def test_reprogramar_a_un_horario_ocupado_falla_y_no_cambia_nada(datos):
    cita = agendar(datos, "2030-01-08T09:00", mascota=datos.a1)
    agendar(datos, "2030-01-08T11:00", mascota=datos.b1, propietario=datos.propietario_b)
    with pytest.raises(ErrorNegocio, match="ya tiene una cita"):
        _reprogramar(datos, cita["cita_id"], "2030-01-08T11:00")
    with transaccion() as bd:
        assert bd.get(Cita, cita["cita_id"]).fecha_hora == datetime(2030, 1, 8, 9, 0)


def test_reprogramar_puede_solaparse_con_su_propio_horario_anterior(datos):
    cita = agendar(datos, "2030-01-08T09:00", servicio=datos.limpieza)  # 09:00-10:30
    movida = _reprogramar(datos, cita["cita_id"], "2030-01-08T09:30")  # 09:30-11:00
    assert movida["hora_fin"] == "11:00"


def test_reprogramar_respeta_el_horario_del_veterinario(datos):
    cita = agendar(datos, "2030-01-08T09:00")
    with pytest.raises(ErrorNegocio, match="no atiende"):
        _reprogramar(datos, cita["cita_id"], "2030-01-09T09:00")  # miércoles


# ---------------------------------------------------------------------------
# Disponibilidad y consultas
# ---------------------------------------------------------------------------


def _disponibilidad(datos, fecha, servicio=None, veterinario=None, ahora=AHORA):
    with transaccion() as bd:
        return agenda.consultar_disponibilidad(
            bd, servicio_id=servicio or datos.consulta, fecha=fecha, veterinario_id=veterinario, ahora=ahora
        )


def test_disponibilidad_excluye_horarios_ocupados_y_respeta_la_duracion(datos):
    agendar(datos, "2030-01-08T09:00")  # 09:00-09:30 ocupado
    resultado = _disponibilidad(datos, MARTES, servicio=datos.limpieza, veterinario=datos.vet1)
    libres = resultado["disponibilidad"][0]["horarios_libres"]
    # Mañana (08-12): 90 minutos no caben antes de las 9:00 ni pasan sobre la cita;
    # 10:30 es el último inicio que termina a las 12:00.
    assert libres[:3] == ["09:30", "10:00", "10:30"]
    assert "08:00" not in libres and "11:00" not in libres
    # Tarde (14-18): último inicio 16:30.
    assert libres[3:] == ["14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]


def test_disponibilidad_coincide_con_lo_que_se_puede_agendar(datos):
    agendar(datos, "2030-01-08T10:00", servicio=datos.bano)  # 10:00-11:00
    libres = _disponibilidad(datos, MARTES, veterinario=datos.vet1)["disponibilidad"][0]["horarios_libres"]
    for hora in ("08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30"):
        inicio = f"2030-01-08T{hora}"
        if hora in libres:
            agendar(datos, inicio, mascota=datos.b1, propietario=datos.propietario_b)
            with transaccion() as bd:  # se cancela para seguir probando el resto
                cita = bd.scalars(select(Cita).where(Cita.fecha_hora == datetime.fromisoformat(inicio))).one()
                cita.estado = "cancelada"
        else:
            with pytest.raises(ErrorNegocio):
                agendar(datos, inicio, mascota=datos.b1, propietario=datos.propietario_b)


def test_disponibilidad_de_hoy_no_ofrece_horas_pasadas_ni_sin_anticipacion(datos):
    resultado = _disponibilidad(datos, LUNES, veterinario=datos.vet1)  # ahora: lunes 08:00
    assert resultado["disponibilidad"][0]["horarios_libres"][0] == "09:00"


def test_disponibilidad_en_un_dia_que_no_trabaja(datos):
    resultado = _disponibilidad(datos, MIERCOLES)
    assert resultado["hay_horarios_libres"] is False
    assert all(not d["atiende_ese_dia"] for d in resultado["disponibilidad"])


def test_disponibilidad_sin_veterinario_lista_a_todos(datos):
    resultado = _disponibilidad(datos, LUNES)
    nombres = [d["veterinario"] for d in resultado["disponibilidad"]]
    assert nombres == ["Dra. Uno", "Dr. Dos"]


def test_una_cita_cancelada_no_ocupa_la_agenda(datos):
    cita = agendar(datos, "2030-01-08T09:00")
    with transaccion() as bd:
        agenda.cancelar_cita(bd, propietario_id=datos.propietario_a, cita_id=cita["cita_id"], ahora=AHORA)
    libres = _disponibilidad(datos, MARTES, veterinario=datos.vet1)["disponibilidad"][0]["horarios_libres"]
    assert "09:00" in libres


def test_ver_citas_muestra_solo_proximas_programadas_y_de_mascotas_propias(datos):
    agendar(datos, "2030-01-08T09:00", mascota=datos.a1)
    cancelada = agendar(datos, "2030-01-08T10:00", mascota=datos.a1)
    with transaccion() as bd:
        agenda.cancelar_cita(bd, propietario_id=datos.propietario_a, cita_id=cancelada["cita_id"], ahora=AHORA)
        resultado = agenda.ver_citas(bd, propietario_id=datos.propietario_a, mascota_id=datos.a1, ahora=AHORA)
    assert [c["fecha_hora"] for c in resultado["proximas_citas"]] == ["2030-01-08T09:00"]
    with pytest.raises(ErrorNegocio), transaccion() as bd:
        agenda.ver_citas(bd, propietario_id=datos.propietario_b, mascota_id=datos.a1, ahora=AHORA)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("2030-01-08T09:00", datetime(2030, 1, 8, 9, 0)),
        ("2030-01-08 09:00", datetime(2030, 1, 8, 9, 0)),
        ("2030-01-08T14:00:00Z", datetime(2030, 1, 8, 9, 0)),  # UTC -> hora de Bogotá
    ],
)
def test_interpretar_fecha_hora(texto, esperado):
    assert agenda.interpretar_fecha_hora(texto) == esperado


def test_interpretar_fecha_hora_invalida():
    with pytest.raises(ErrorNegocio, match="no es válida"):
        agenda.interpretar_fecha_hora("mañana a las 9")
