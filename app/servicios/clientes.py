"""Clientes (propietarios), sus mascotas y la verificación de pertenencia."""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ajustes
from app.db.modelos import Cita, Mascota, Propietario
from app.servicios import formato
from app.servicios.errores import ErrorNegocio


def normalizar_telefono(telefono: str) -> str:
    """Deja solo dígitos y quita el indicativo de país.

    "+57 300 111 2233", "573001112233" y "300-111-2233" quedan como "3001112233".
    """
    digitos = re.sub(r"\D", "", telefono or "")
    if len(digitos) < 7:
        raise ErrorNegocio("El número de teléfono no es válido. Pide al cliente que lo escriba completo.")
    largo = ajustes.digitos_telefono_local
    return digitos[-largo:] if len(digitos) > largo else digitos


def buscar_propietario_por_telefono(sesion: Session, telefono: str) -> Propietario | None:
    return sesion.scalar(select(Propietario).where(Propietario.telefono == normalizar_telefono(telefono)))


def describir_cliente(propietario: Propietario, hoy: date) -> dict:
    return {
        "propietario": {"id": propietario.id, "nombre": propietario.nombre},
        "mascotas": [
            {
                "id": m.id,
                "nombre": m.nombre,
                "especie": m.especie,
                "raza": m.raza,
                "edad": formato.edad(m.fecha_nacimiento, hoy),
            }
            for m in propietario.mascotas
        ],
    }


def obtener_mascota_del_propietario(sesion: Session, propietario_id: int, mascota_id: int) -> Mascota:
    mascota = sesion.get(Mascota, mascota_id)
    if mascota is None or mascota.propietario_id != propietario_id:
        # Mismo mensaje si no existe o si es de otro cliente: no revelamos datos ajenos.
        raise ErrorNegocio("No encontré esa mascota entre las mascotas registradas de este cliente.")
    return mascota


def obtener_cita_del_propietario(
    sesion: Session, propietario_id: int, cita_id: int, *, bloquear: bool = False
) -> Cita:
    """Devuelve la cita si es de una mascota del propietario. Con bloquear=True la fila queda
    bloqueada hasta el final de la transacción (evita que dos cambios simultáneos se pisen)."""
    cita = sesion.get(Cita, cita_id, with_for_update=bloquear)
    if cita is None or cita.mascota.propietario_id != propietario_id:
        raise ErrorNegocio("No encontré esa cita entre las citas de las mascotas de este cliente.")
    return cita
