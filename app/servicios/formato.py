"""Utilidades para convertir datos de la base en valores simples (JSON) y legibles en español."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def dinero(valor: Decimal | None) -> int | float | None:
    if valor is None:
        return None
    return int(valor) if valor == valor.to_integral_value() else float(valor)


def fecha_iso(valor: date | None) -> str | None:
    return valor.isoformat() if valor else None


def fecha_hora_iso(valor: datetime) -> str:
    return valor.strftime("%Y-%m-%dT%H:%M")


def fecha_larga(valor: date) -> str:
    """'martes 18 de noviembre de 2026'"""
    return f"{DIAS[valor.weekday()]} {valor.day} de {MESES[valor.month - 1]} de {valor.year}"


def fecha_hora_larga(valor: datetime) -> str:
    """'martes 18 de noviembre de 2026, 09:00'"""
    return f"{fecha_larga(valor)}, {valor:%H:%M}"


def edad(fecha_nacimiento: date | None, hoy: date) -> str | None:
    if not fecha_nacimiento:
        return None
    meses = (hoy.year - fecha_nacimiento.year) * 12 + hoy.month - fecha_nacimiento.month
    if hoy.day < fecha_nacimiento.day:
        meses -= 1
    if meses < 12:
        return f"{max(meses, 0)} meses"
    anios = meses // 12
    return f"{anios} año" if anios == 1 else f"{anios} años"
