"""Vacunación: próxima dosis pendiente de una mascota."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import Vacunacion
from app.servicios import formato
from app.servicios.clientes import obtener_mascota_del_propietario


def proxima_vacuna(sesion: Session, *, propietario_id: int, mascota_id: int, hoy: date) -> dict:
    """Para cada vacuna se toma la última aplicación; su próxima dosis es la que cuenta.

    Devuelve la próxima dosis pendiente, las que están atrasadas y la última
    aplicación de cada vacuna.
    """
    mascota = obtener_mascota_del_propietario(sesion, propietario_id, mascota_id)
    aplicaciones = sesion.scalars(
        select(Vacunacion)
        .where(Vacunacion.mascota_id == mascota.id)
        .order_by(Vacunacion.fecha_aplicacion, Vacunacion.id)
    )
    ultima_por_vacuna: dict[str, Vacunacion] = {}
    for aplicacion in aplicaciones:
        ultima_por_vacuna[aplicacion.vacuna.strip().lower()] = aplicacion

    def resumen(v: Vacunacion) -> dict:
        return {
            "vacuna": v.vacuna,
            "fecha_aplicacion": formato.fecha_iso(v.fecha_aplicacion),
            "proxima_dosis": formato.fecha_iso(v.proxima_dosis),
            "proxima_dosis_texto": formato.fecha_larga(v.proxima_dosis) if v.proxima_dosis else None,
        }

    con_refuerzo = sorted(
        (v for v in ultima_por_vacuna.values() if v.proxima_dosis), key=lambda v: v.proxima_dosis
    )
    pendientes = [v for v in con_refuerzo if v.proxima_dosis >= hoy]
    atrasadas = [v for v in con_refuerzo if v.proxima_dosis < hoy]

    return {
        "mascota": mascota.nombre,
        "proxima": resumen(pendientes[0]) if pendientes else None,
        "atrasadas": [resumen(v) for v in atrasadas],
        "ultimas_aplicaciones": [resumen(v) for v in sorted(ultima_por_vacuna.values(), key=lambda v: v.vacuna)],
    }
