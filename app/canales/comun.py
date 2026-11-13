"""Piezas compartidas por los canales (web y WhatsApp)."""

from __future__ import annotations

import threading

from fastapi import HTTPException

from app.bot.motor import ErrorServicioIA, MotorBot, crear_motor_por_defecto

_motor: MotorBot | None = None
_candado = threading.Lock()


def motor_compartido() -> MotorBot:
    """Un único motor para toda la aplicación, creado al primer uso.

    Lanza ErrorServicioIA si falta la API key (la aplicación arranca igual).
    """
    global _motor
    with _candado:
        if _motor is None:
            _motor = crear_motor_por_defecto()
        return _motor


def obtener_motor() -> MotorBot:
    """Dependencia de FastAPI: si falta la API key, el endpoint responde 503 con un mensaje claro."""
    try:
        return motor_compartido()
    except ErrorServicioIA as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
