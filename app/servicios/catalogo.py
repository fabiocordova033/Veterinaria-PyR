"""Información general de la clínica: servicios, precios, horario, dirección y veterinarios."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ajustes
from app.db.modelos import Servicio, Veterinario
from app.servicios import formato


def info_servicios(sesion: Session) -> dict:
    servicios = sesion.scalars(select(Servicio).order_by(Servicio.id)).all()
    veterinarios = sesion.scalars(select(Veterinario).order_by(Veterinario.id)).all()
    return {
        "clinica": ajustes.clinica_nombre,
        "direccion": ajustes.clinica_direccion,
        "telefono": ajustes.clinica_telefono,
        "telefono_urgencias": ajustes.clinica_telefono_urgencias,
        "horario_atencion": ajustes.clinica_horario,
        "moneda": ajustes.moneda,
        "simbolo_moneda": ajustes.simbolo_moneda,
        "nota_precios": ajustes.nota_precios,
        "servicios": [
            {
                "id": s.id,
                "nombre": s.nombre,
                "precio": formato.dinero(s.precio),
                "duracion_minutos": s.duracion_minutos,
            }
            for s in servicios
        ],
        "veterinarios": [
            {"id": v.id, "nombre": v.nombre, "especialidad": v.especialidad} for v in veterinarios
        ],
    }
