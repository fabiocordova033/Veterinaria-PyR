"""Piezas compartidas por los canales (web hoy; WhatsApp después)."""

from __future__ import annotations

import threading

from fastapi import HTTPException

from app.bot.motor import ErrorServicioIA, MotorBot, crear_motor_por_defecto

_motor: MotorBot | None = None
_candado = threading.Lock()


def obtener_motor() -> MotorBot:
    """Dependencia de FastAPI: un único motor para toda la aplicación, creado al primer uso.

    Si falta la API key, el endpoint responde 503 con un mensaje claro en lugar de fallar al arrancar.
    """
    global _motor
    with _candado:
        if _motor is None:
            try:
                _motor = crear_motor_por_defecto()
            except ErrorServicioIA as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        return _motor
