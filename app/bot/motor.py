"""Motor del bot: recibe el texto de un cliente y devuelve la respuesta.

No sabe nada de HTTP ni de canales. La página web (app/canales/web.py) lo usa
hoy, y un webhook de WhatsApp lo usaría igual:

    motor.responder(sesion_id="whatsapp-573001112233", texto="Hola",
                    canal="whatsapp", telefono_verificado="573001112233")

Flujo de un turno:
1. Carga la sesión y su historial (formato de mensajes de la API de Claude).
2. Agrega el mensaje del cliente con un bloque <contexto> (fecha, canal...).
3. Llama al modelo. Mientras pida herramientas, las ejecuta y le devuelve los
   resultados. Cuando responde con texto, termina.
4. Cada mensaje se guarda en la base apenas se produce. El historial solo crece
   (nunca se edita), así el modelo conserva su razonamiento entre turnos.
"""

from __future__ import annotations

import logging
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anthropic

from app.bot import sesiones
from app.bot.herramientas import ContextoHerramienta, definiciones_herramientas, ejecutar_herramienta
from app.bot.llm import ClienteAnthropic, ClienteLLM
from app.bot.prompt import construir_prompt_sistema
from app.config import Ajustes
from app.config import ajustes as ajustes_globales
from app.db.base import transaccion
from app.servicios import formato

logger = logging.getLogger(__name__)

MENSAJE_NO_PUEDO = (
    "Lo siento, con eso no puedo ayudarte por este medio. Si quieres, te comunico con el equipo de la clínica."
)
MENSAJE_PROBLEMA = (
    "Disculpa, tuve un problema procesando tu solicitud. ¿Me lo puedes repetir? "
    "Si prefieres, te comunico con el equipo de la clínica."
)


class ErrorServicioIA(Exception):
    """No se pudo obtener respuesta del modelo (configuración, red o API)."""


@dataclass
class RespuestaBot:
    sesion_id: str
    texto: str
    requiere_humano: bool


def _texto_cliente_seguro(texto: str) -> str:
    # Evita que el cliente se haga pasar por el sistema escribiendo un bloque <contexto>.
    return texto.replace("<contexto", "(contexto").replace("</contexto", "(/contexto").strip()


def _texto_de(bloques: list[Any]) -> str:
    return "\n\n".join(b.text.strip() for b in bloques if b.type == "text" and b.text.strip())


class MotorBot:
    def __init__(
        self,
        cliente_llm: ClienteLLM,
        ajustes: Ajustes = ajustes_globales,
        reloj: Callable[[], datetime] | None = None,
    ):
        self._llm = cliente_llm
        self._ajustes = ajustes
        self._reloj = reloj or ajustes.ahora
        # Instrucciones y herramientas fijas: el prefijo de cada solicitud es idéntico (caché de prompts).
        self._system = [{"type": "text", "text": construir_prompt_sistema(ajustes)}]
        self._tools = definiciones_herramientas(ajustes.herramientas_estrictas)
        # Diccionario débil: el candado de una sesión desaparece cuando nadie lo está usando.
        self._candados: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
        self._candado_candados = threading.Lock()

    # ------------------------------------------------------------------ API

    def responder(
        self, sesion_id: str, texto: str, *, canal: str = "web", telefono_verificado: str | None = None
    ) -> RespuestaBot:
        # Un turno a la vez por sesión, para que dos mensajes seguidos no mezclen su historial.
        with self._candado(sesion_id):
            return self._responder(sesion_id, texto, canal, telefono_verificado)

    # -------------------------------------------------------------- interno

    def _candado(self, sesion_id: str) -> threading.Lock:
        with self._candado_candados:
            candado = self._candados.get(sesion_id)
            if candado is None:
                candado = threading.Lock()
                self._candados[sesion_id] = candado
            return candado

    def _responder(self, sesion_id: str, texto: str, canal: str, telefono_verificado: str | None) -> RespuestaBot:
        ahora = self._reloj()
        with transaccion() as bd:
            chat = sesiones.obtener_o_crear(bd, sesion_id, canal, ahora, telefono_verificado)
            mensajes = sesiones.cargar_historial(bd, chat)
            numero_conversacion = chat.numero_conversacion
            ctx = ContextoHerramienta(
                sesion_id=chat.id,
                ahora=ahora,
                propietario_id=chat.propietario_id,
                telefono_verificado=chat.telefono_verificado,
                requiere_humano=chat.requiere_humano,
            )

        def anotar(mensaje: dict) -> None:
            mensajes.append(mensaje)
            with transaccion() as bd:
                sesiones.agregar_mensaje(bd, sesion_id, numero_conversacion, mensaje, self._reloj())

        anotar(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self._contexto(ctx, canal)},
                    {"type": "text", "text": _texto_cliente_seguro(texto)},
                ],
            }
        )

        texto_final = MENSAJE_PROBLEMA
        for _ in range(self._ajustes.max_iteraciones_herramientas):
            respuesta = self._llamar_modelo(mensajes)
            motivo = respuesta.stop_reason
            # Los bloques se guardan tal como llegaron (incluidos los de razonamiento),
            # porque la API exige recibirlos sin cambios en los turnos siguientes.
            contenido = [bloque.to_dict(mode="json") for bloque in respuesta.content]

            if motivo == "tool_use":
                anotar({"role": "assistant", "content": contenido})
                anotar({"role": "user", "content": self._ejecutar_herramientas(respuesta.content, ctx)})
                continue

            if motivo == "pause_turn":
                anotar({"role": "assistant", "content": contenido})
                continue

            texto = _texto_de(respuesta.content)
            if motivo in ("end_turn", "stop_sequence") and texto:
                texto_final = texto
                anotar({"role": "assistant", "content": contenido})
            else:
                # refusal, max_tokens o respuesta vacía: se guarda un mensaje simple en lugar
                # de una respuesta incompleta (que podría traer una herramienta a medias).
                logger.warning("Respuesta sin texto utilizable (stop_reason=%s, sesión %s)", motivo, sesion_id)
                texto_final = MENSAJE_NO_PUEDO if motivo == "refusal" else MENSAJE_PROBLEMA
                anotar({"role": "assistant", "content": [{"type": "text", "text": texto_final}]})
            break
        else:
            logger.warning("Se alcanzó el máximo de llamadas a herramientas (sesión %s)", sesion_id)
            anotar({"role": "assistant", "content": [{"type": "text", "text": texto_final}]})

        return RespuestaBot(sesion_id=sesion_id, texto=texto_final, requiere_humano=ctx.requiere_humano)

    def _contexto(self, ctx: ContextoHerramienta, canal: str) -> str:
        lineas = [
            f"Fecha y hora actual en la clínica: {formato.fecha_hora_larga(ctx.ahora)} "
            f"({formato.fecha_hora_iso(ctx.ahora)}).",
            f"Canal: {canal}.",
            f"Cliente identificado en esta conversación: {'sí' if ctx.propietario_id else 'no'}.",
        ]
        if ctx.telefono_verificado:
            lineas.append(f"Teléfono verificado por el canal: {ctx.telefono_verificado}.")
        return "<contexto>\n" + "\n".join(lineas) + "\n</contexto>"

    def _ejecutar_herramientas(self, bloques: list[Any], ctx: ContextoHerramienta) -> list[dict]:
        # Todas las respuestas a herramientas van juntas en un solo mensaje del usuario.
        resultados = []
        for bloque in bloques:
            if bloque.type != "tool_use":
                continue
            resultado = ejecutar_herramienta(bloque.name, bloque.input, ctx)
            item = {"type": "tool_result", "tool_use_id": bloque.id, "content": resultado.contenido}
            if resultado.es_error:
                item["is_error"] = True
            resultados.append(item)
        return resultados

    def _llamar_modelo(self, mensajes: list[dict]) -> Any:
        try:
            respuesta = self._llm.crear_mensaje(system=self._system, tools=self._tools, messages=mensajes)
        except anthropic.AuthenticationError as error:
            logger.error("La API key de Anthropic no es válida: %s", error)
            raise ErrorServicioIA("La API key de Anthropic no es válida. Revisa ANTHROPIC_API_KEY.") from error
        except anthropic.RateLimitError as error:
            logger.warning("Límite de uso de la API alcanzado: %s", error)
            raise ErrorServicioIA("El asistente está muy ocupado. Intenta de nuevo en un momento.") from error
        except anthropic.APIStatusError as error:
            logger.error("Error de la API de Anthropic (%s): %s", error.status_code, error)
            raise ErrorServicioIA("El asistente no está disponible en este momento.") from error
        except anthropic.APIConnectionError as error:
            logger.error("No se pudo conectar con la API de Anthropic: %s", error)
            raise ErrorServicioIA("No hay conexión con el asistente en este momento.") from error

        uso = getattr(respuesta, "usage", None)
        if uso is not None:
            logger.info(
                "Uso de tokens: entrada=%s, caché leída=%s, salida=%s",
                getattr(uso, "input_tokens", None),
                getattr(uso, "cache_read_input_tokens", None),
                getattr(uso, "output_tokens", None),
            )
        return respuesta


def crear_motor_por_defecto(ajustes: Ajustes = ajustes_globales) -> MotorBot:
    if not ajustes.anthropic_api_key:
        raise ErrorServicioIA("Falta configurar ANTHROPIC_API_KEY en el archivo .env.")
    return MotorBot(ClienteAnthropic(ajustes), ajustes)
