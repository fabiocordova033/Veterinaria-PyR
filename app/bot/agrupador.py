"""Junta los mensajes que un cliente envía seguidos para responderlos una sola vez.

En WhatsApp es común escribir "hola" / "quiero una cita" / "para Luna" en tres
mensajes. Sin agrupar, el bot respondería tres veces. Con el agrupador:

- Cada mensaje nuevo de un cliente reinicia una espera corta (p. ej. 4 segundos).
- Cuando pasa esa espera sin mensajes nuevos, se procesan todos juntos.
- Si el cliente sigue escribiendo sin pausa, igual se procesa al cumplirse una
  espera máxima (p. ej. 15 segundos desde el primer mensaje del grupo).
- Mientras el bot responde a un grupo, los mensajes que lleguen de ese mismo
  cliente se juntan para el grupo siguiente. Así cada cliente recibe sus
  respuestas en orden y nunca hay dos respuestas en curso para el mismo chat.

Es independiente del canal: recibe una función `procesar(clave, elementos)`.
El estado vive en memoria, así que la aplicación debe correr en un solo proceso.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class _Grupo(Generic[T]):
    elementos: list[T] = field(default_factory=list)
    primero_en: float = 0.0  # cuándo llegó el primer elemento pendiente (time.monotonic)
    temporizador: threading.Timer | None = None
    version: int = 0  # invalida temporizadores viejos que ya se dispararon
    procesando: bool = False


class AgrupadorMensajes(Generic[T]):
    def __init__(
        self,
        procesar: Callable[[str, list[T]], None],
        espera_segundos: float = 4.0,
        espera_maxima_segundos: float = 15.0,
    ):
        if espera_segundos <= 0 or espera_maxima_segundos < espera_segundos:
            raise ValueError("Se requiere 0 < espera_segundos <= espera_maxima_segundos")
        self._procesar = procesar
        self._espera = espera_segundos
        self._espera_maxima = espera_maxima_segundos
        self._grupos: dict[str, _Grupo[T]] = {}
        self._condicion = threading.Condition()

    # ------------------------------------------------------------------ API

    def agregar(self, clave: str, elemento: T) -> None:
        """Agrega un mensaje del cliente `clave` y (re)programa el procesamiento de su grupo."""
        with self._condicion:
            grupo = self._grupos.setdefault(clave, _Grupo())
            if not grupo.elementos:
                grupo.primero_en = time.monotonic()
            grupo.elementos.append(elemento)
            if not grupo.procesando:
                self._programar(clave, grupo)
            # Si se está procesando, el grupo siguiente se programa al terminar.

    def pendientes(self) -> int:
        with self._condicion:
            return sum(len(g.elementos) for g in self._grupos.values())

    def esperar_inactivo(self, timeout: float = 30.0) -> bool:
        """Espera a que no quede nada pendiente ni en proceso. Útil en pruebas."""
        limite = time.monotonic() + timeout
        with self._condicion:
            while self._grupos:
                restante = limite - time.monotonic()
                if restante <= 0:
                    return False
                self._condicion.wait(restante)
            return True

    def vaciar(self) -> None:
        """Procesa ya, en este hilo, los grupos que estén esperando (p. ej., al apagar la aplicación)."""
        with self._condicion:
            claves = [c for c, g in self._grupos.items() if g.elementos and not g.procesando]
            for clave in claves:
                grupo = self._grupos[clave]
                grupo.version += 1
                if grupo.temporizador:
                    grupo.temporizador.cancel()
        for clave in claves:
            self._ejecutar(clave, None)

    # -------------------------------------------------------------- interno

    def _programar(self, clave: str, grupo: _Grupo[T]) -> None:
        """Debe llamarse con el candado tomado."""
        if grupo.temporizador:
            grupo.temporizador.cancel()
        grupo.version += 1
        transcurrido = time.monotonic() - grupo.primero_en
        demora = max(0.0, min(self._espera, self._espera_maxima - transcurrido))
        temporizador = threading.Timer(demora, self._ejecutar, args=(clave, grupo.version))
        temporizador.daemon = True
        grupo.temporizador = temporizador
        temporizador.start()

    def _ejecutar(self, clave: str, version: int | None) -> None:
        with self._condicion:
            grupo = self._grupos.get(clave)
            if grupo is None or grupo.procesando or not grupo.elementos:
                return
            if version is not None and version != grupo.version:
                return  # llegó otro mensaje después: este temporizador quedó viejo
            elementos, grupo.elementos = grupo.elementos, []
            grupo.procesando = True
            grupo.temporizador = None

        try:
            self._procesar(clave, elementos)
        except Exception:
            logger.exception("Error procesando los mensajes agrupados de %s", clave)
        finally:
            with self._condicion:
                grupo.procesando = False
                if grupo.elementos:
                    # Llegaron mensajes mientras se respondía: se agrupan y se programan ahora.
                    grupo.primero_en = time.monotonic()
                    self._programar(clave, grupo)
                else:
                    self._grupos.pop(clave, None)
                self._condicion.notify_all()
