"""Cliente de la API de Claude (SDK oficial `anthropic`).

El motor del bot depende solo de la interfaz ClienteLLM, así las pruebas pueden
usar un cliente simulado sin llamar a la API.
"""

from __future__ import annotations

from typing import Any, Protocol

import anthropic

from app.config import Ajustes

BETA_FALLBACK = "server-side-fallback-2026-07-01"


class ClienteLLM(Protocol):
    def crear_mensaje(self, *, system: list[dict], tools: list[dict], messages: list[dict]) -> Any:
        """Devuelve un objeto con .content (bloques) y .stop_reason, como anthropic.types.Message."""
        ...


class ClienteAnthropic:
    def __init__(self, ajustes: Ajustes, cliente_sdk: anthropic.Anthropic | None = None):
        # La API key se lee de ANTHROPIC_API_KEY (cargada desde .env en app/config.py).
        self._cliente = cliente_sdk or anthropic.Anthropic(
            api_key=ajustes.anthropic_api_key, timeout=120.0, max_retries=3
        )
        self._ajustes = ajustes

    def crear_mensaje(self, *, system: list[dict], tools: list[dict], messages: list[dict]) -> Any:
        parametros: dict[str, Any] = {
            "model": self._ajustes.modelo,
            "max_tokens": self._ajustes.max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
            # Caché automático: reutiliza instrucciones, herramientas e historial entre turnos.
            "cache_control": {"type": "ephemeral"},
        }
        if self._ajustes.esfuerzo:
            parametros["output_config"] = {"effort": self._ajustes.esfuerzo}

        if self._ajustes.fallbacks:
            # Si el modelo principal rechaza la solicitud por sus filtros de seguridad,
            # la API la reintenta con un modelo de respaldo dentro de la misma llamada.
            fallbacks: Any = self._ajustes.fallbacks
            return self._cliente.beta.messages.create(betas=[BETA_FALLBACK], fallbacks=fallbacks, **parametros)
        return self._cliente.messages.create(**parametros)
