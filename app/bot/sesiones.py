"""Persistencia de las sesiones de chat y de su historial."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ajustes
from app.db.modelos import MensajeChat, SesionChat


class SesionDeOtroCanal(Exception):
    """El identificador de sesión pertenece a otro canal (p. ej., una sesión de WhatsApp pedida desde la web)."""


def obtener_o_crear(
    sesion_bd: Session,
    sesion_id: str,
    canal: str,
    ahora: datetime,
    telefono_verificado: str | None = None,
) -> SesionChat:
    chat = sesion_bd.get(SesionChat, sesion_id)
    if chat is None:
        chat = SesionChat(
            id=sesion_id,
            canal=canal,
            telefono_verificado=telefono_verificado,
            numero_conversacion=1,
            requiere_humano=False,
            urgente=False,
            creada_en=ahora,
            actualizada_en=ahora,
        )
        sesion_bd.add(chat)
        sesion_bd.flush()
        return chat

    if chat.canal != canal:
        raise SesionDeOtroCanal(sesion_id)

    inactividad = timedelta(minutes=ajustes.minutos_inactividad_sesion)
    if ahora - chat.actualizada_en > inactividad:
        # Conversación nueva: el historial anterior se conserva, pero ya no se envía al modelo,
        # y el cliente debe identificarse otra vez.
        chat.numero_conversacion += 1
        chat.propietario_id = None
    if telefono_verificado:
        chat.telefono_verificado = telefono_verificado
    chat.actualizada_en = ahora
    return chat


def cargar_historial(sesion_bd: Session, chat: SesionChat) -> list[dict]:
    mensajes = sesion_bd.scalars(
        select(MensajeChat)
        .where(MensajeChat.sesion_id == chat.id, MensajeChat.numero_conversacion == chat.numero_conversacion)
        .order_by(MensajeChat.id)
    )
    return [{"role": m.rol, "content": m.contenido} for m in mensajes]


def agregar_mensaje(
    sesion_bd: Session, sesion_id: str, numero_conversacion: int, mensaje: dict, ahora: datetime
) -> None:
    sesion_bd.add(
        MensajeChat(
            sesion_id=sesion_id,
            numero_conversacion=numero_conversacion,
            rol=mensaje["role"],
            contenido=mensaje["content"],
            creado_en=ahora,
        )
    )


def vincular_propietario(sesion_bd: Session, sesion_id: str, propietario_id: int) -> None:
    chat = sesion_bd.get(SesionChat, sesion_id)
    if chat is not None:
        chat.propietario_id = propietario_id


def marcar_derivacion(sesion_bd: Session, sesion_id: str, motivo: str, urgente: bool) -> None:
    chat = sesion_bd.get(SesionChat, sesion_id)
    if chat is not None:
        chat.requiere_humano = True
        chat.urgente = chat.urgente or urgente
        chat.motivo_derivacion = motivo[:500]
