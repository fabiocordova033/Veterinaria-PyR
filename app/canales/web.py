"""Canal web: endpoint POST /chat que usa la página de chat.

Un canal solo traduce entre su protocolo y el motor del bot. Un futuro
app/canales/whatsapp.py haría lo mismo con los webhooks de Meta.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.bot.motor import ErrorServicioIA, MotorBot
from app.bot.sesiones import SesionDeOtroCanal
from app.canales.comun import obtener_motor
from app.config import ajustes

router = APIRouter()

_PATRON_SESION_WEB = re.compile(r"^web-[0-9a-f]{32}$")


class SolicitudChat(BaseModel):
    sesion_id: str | None = Field(default=None, max_length=64)
    mensaje: str = Field(min_length=1, max_length=2000)


class RespuestaChat(BaseModel):
    sesion_id: str
    respuesta: str
    urgencia: bool  # el bot registró una posible urgencia en este mensaje


@router.post("/chat", response_model=RespuestaChat)
def chat(solicitud: SolicitudChat, motor: MotorBot = Depends(obtener_motor)) -> RespuestaChat:
    # Solo se aceptan identificadores con el formato que genera el servidor ("web-" + 32 hex),
    # así un cliente web no puede usar el de una conversación de WhatsApp.
    sesion_id = solicitud.sesion_id
    if not sesion_id or not _PATRON_SESION_WEB.match(sesion_id):
        sesion_id = f"web-{uuid.uuid4().hex}"

    if not solicitud.mensaje.strip():
        raise HTTPException(status_code=422, detail="El mensaje está vacío.")

    try:
        respuesta = motor.responder(sesion_id, solicitud.mensaje, canal="web")
    except SesionDeOtroCanal:
        raise HTTPException(status_code=404, detail="Sesión no encontrada.") from None
    except ErrorServicioIA as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return RespuestaChat(
        sesion_id=respuesta.sesion_id, respuesta=respuesta.texto, urgencia=respuesta.urgencia
    )


@router.get("/clinica")
def clinica() -> dict:
    """Datos públicos para la cabecera de la página de chat."""
    return {"nombre": ajustes.clinica_nombre, "telefono_urgencias": ajustes.clinica_telefono_urgencias}
