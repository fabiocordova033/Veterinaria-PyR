"""Herramientas (tools) que el modelo puede llamar.

El modelo nunca toca la base de datos: solo elige una herramienta y sus
argumentos. Aquí se validan los argumentos, se comprueba que los datos
pedidos pertenezcan al cliente identificado en la sesión y se llama a la
capa de servicios. Cada llamada corre en su propia transacción.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.bot import sesiones
from app.config import ajustes
from app.db.base import transaccion
from app.servicios import agenda, catalogo, vacunas
from app.servicios.clientes import buscar_propietario_por_telefono, describir_cliente, normalizar_telefono
from app.servicios.errores import ErrorNegocio

logger = logging.getLogger(__name__)


@dataclass
class ContextoHerramienta:
    """Lo que el backend sabe de la conversación y el modelo no puede cambiar a su antojo."""

    sesion_id: str
    ahora: datetime
    propietario_id: int | None = None
    # En WhatsApp el número lo entrega el canal (verificado); en la web se pide al cliente.
    telefono_verificado: str | None = None
    requiere_humano: bool = False


@dataclass
class ResultadoHerramienta:
    contenido: str  # JSON
    es_error: bool = False


# ---------------------------------------------------------------------------
# Definiciones que se envían a la API
# ---------------------------------------------------------------------------


def _esquema(propiedades: dict, requeridas: list[str]) -> dict:
    return {
        "type": "object",
        "properties": propiedades,
        "required": requeridas,
        "additionalProperties": False,
    }


_ID_MASCOTA = {"type": "integer", "description": "id de la mascota, tomado de buscar_cliente."}
_ID_CITA = {"type": "integer", "description": "id de la cita (cita_id), tomado de ver_citas o de agendar_cita."}
_FECHA_HORA = (
    "Fecha y hora de inicio en hora local de la clínica, formato AAAA-MM-DDTHH:MM "
    "(por ejemplo 2026-11-18T09:30). Usa uno de los horarios libres que devolvió consultar_disponibilidad."
)

DEFINICIONES: list[dict[str, Any]] = [
    {
        "name": "buscar_cliente",
        "description": (
            "Busca al cliente por su número de celular y devuelve su nombre y sus mascotas (con sus id). "
            "Llámala en cuanto el cliente te dé su celular, antes de hablar de mascotas, citas o vacunas. "
            "La conversación queda asociada a ese cliente y solo podrás ver la información de sus mascotas."
        ),
        "input_schema": _esquema(
            {"telefono": {"type": "string", "description": "Número de celular tal como lo escribió el cliente."}},
            ["telefono"],
        ),
    },
    {
        "name": "info_servicios",
        "description": (
            "Devuelve los servicios con precio y duración, el horario de atención, la dirección, los teléfonos "
            "y la lista de veterinarios. Llámala para cualquier pregunta sobre servicios, precios, horario, "
            "ubicación o veterinarios, y para conocer los id de servicio antes de consultar disponibilidad."
        ),
        "input_schema": _esquema({}, []),
    },
    {
        "name": "consultar_disponibilidad",
        "description": (
            "Devuelve los horarios libres para un servicio en una fecha, por veterinario, según su horario de "
            "trabajo, la duración del servicio y las citas ya agendadas. Llámala antes de proponer horarios: "
            "nunca inventes disponibilidad."
        ),
        "input_schema": _esquema(
            {
                "servicio_id": {"type": "integer", "description": "id del servicio (de info_servicios)."},
                "fecha": {"type": "string", "format": "date", "description": "Fecha a consultar, AAAA-MM-DD."},
                "veterinario_id": {
                    "type": "integer",
                    "description": "Opcional: id del veterinario si el cliente prefiere uno.",
                },
            },
            ["servicio_id", "fecha"],
        ),
    },
    {
        "name": "agendar_cita",
        "description": (
            "Agenda una cita. Llámala SOLO después de mostrar al cliente el resumen (mascota, servicio, "
            "veterinario, fecha, hora y precio) y de que él lo confirme explícitamente en su último mensaje. "
            "El backend valida el horario y que no se cruce con otras citas."
        ),
        "input_schema": _esquema(
            {
                "mascota_id": _ID_MASCOTA,
                "servicio_id": {"type": "integer", "description": "id del servicio."},
                "veterinario_id": {"type": "integer", "description": "id del veterinario."},
                "fecha_hora": {"type": "string", "description": _FECHA_HORA},
                "motivo": {"type": "string", "description": "Motivo de la cita en pocas palabras."},
            },
            ["mascota_id", "servicio_id", "veterinario_id", "fecha_hora", "motivo"],
        ),
    },
    {
        "name": "cancelar_cita",
        "description": (
            "Cancela una cita programada. Llámala SOLO después de decirle al cliente qué cita vas a cancelar "
            "(mascota, servicio, fecha y hora) y de que lo confirme explícitamente."
        ),
        "input_schema": _esquema({"cita_id": _ID_CITA}, ["cita_id"]),
    },
    {
        "name": "reprogramar_cita",
        "description": (
            "Mueve una cita programada a otra fecha y hora con el mismo veterinario y servicio. Consulta antes "
            "la disponibilidad. Llámala SOLO después de mostrar el cambio (de qué fecha y hora a cuál) y de que "
            "el cliente lo confirme explícitamente."
        ),
        "input_schema": _esquema(
            {"cita_id": _ID_CITA, "nueva_fecha_hora": {"type": "string", "description": _FECHA_HORA}},
            ["cita_id", "nueva_fecha_hora"],
        ),
    },
    {
        "name": "ver_citas",
        "description": "Devuelve las próximas citas programadas de una mascota del cliente (con su cita_id).",
        "input_schema": _esquema({"mascota_id": _ID_MASCOTA}, ["mascota_id"]),
    },
    {
        "name": "proxima_vacuna",
        "description": (
            "Devuelve la próxima vacuna pendiente de una mascota del cliente, las vacunas atrasadas y la última "
            "aplicación de cada vacuna."
        ),
        "input_schema": _esquema({"mascota_id": _ID_MASCOTA}, ["mascota_id"]),
    },
    {
        "name": "derivar_a_humano",
        "description": (
            "Marca la conversación para que la atienda una persona de la clínica. Llámala de inmediato ante "
            "una posible urgencia (urgente=true), cuando el cliente pida hablar con alguien, o cuando no "
            "puedas resolver lo que necesita."
        ),
        "input_schema": _esquema(
            {
                "motivo": {"type": "string", "description": "Resumen breve de lo que necesita el cliente."},
                "urgente": {"type": "boolean", "description": "true si es una posible urgencia médica."},
            },
            ["motivo", "urgente"],
        ),
    },
]


def definiciones_herramientas(estrictas: bool | None = None) -> list[dict[str, Any]]:
    """Lista de herramientas para la API. Siempre igual y en el mismo orden (ayuda al caché de prompts)."""
    estrictas = ajustes.herramientas_estrictas if estrictas is None else estrictas
    return [{**d, "strict": True} if estrictas else dict(d) for d in DEFINICIONES]


# ---------------------------------------------------------------------------
# Validación de argumentos
# ---------------------------------------------------------------------------


def _entero(entrada: dict, campo: str, opcional: bool = False) -> int | None:
    valor = entrada.get(campo)
    if valor is None:
        if opcional:
            return None
        raise ErrorNegocio(f"Falta el dato '{campo}'.")
    if isinstance(valor, bool):
        raise ErrorNegocio(f"El dato '{campo}' debe ser un número entero.")
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    raise ErrorNegocio(f"El dato '{campo}' debe ser un número entero.")


def _texto(entrada: dict, campo: str, max_largo: int = 500) -> str:
    valor = entrada.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise ErrorNegocio(f"Falta el dato '{campo}'.")
    return valor.strip()[:max_largo]


def _requiere_cliente(ctx: ContextoHerramienta) -> int:
    if ctx.propietario_id is None:
        raise ErrorNegocio(
            "Todavía no se ha identificado al cliente. Pide su número de celular y usa buscar_cliente."
        )
    return ctx.propietario_id


# ---------------------------------------------------------------------------
# Manejadores
# ---------------------------------------------------------------------------


def _buscar_cliente(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    telefono = normalizar_telefono(_texto(entrada, "telefono", 40))
    if ctx.telefono_verificado and telefono != normalizar_telefono(ctx.telefono_verificado):
        raise ErrorNegocio("En este canal solo se puede consultar el número desde el que escribe el cliente.")
    propietario = buscar_propietario_por_telefono(bd, telefono)
    if propietario is None:
        return {
            "encontrado": False,
            "mensaje": "No hay ningún cliente registrado con ese número. Pide que lo verifique; si no está "
            "registrado, puede responder preguntas generales u ofrecer comunicarlo con la clínica.",
        }
    if ctx.propietario_id is not None and ctx.propietario_id != propietario.id:
        raise ErrorNegocio(
            "Esta conversación ya está asociada a otro cliente. Para consultar otro número hay que iniciar "
            "una conversación nueva."
        )
    sesiones.vincular_propietario(bd, ctx.sesion_id, propietario.id)
    ctx.propietario_id = propietario.id
    return {"encontrado": True, **describir_cliente(propietario, ctx.ahora.date())}


def _info_servicios(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    return catalogo.info_servicios(bd)


def _consultar_disponibilidad(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    return agenda.consultar_disponibilidad(
        bd,
        servicio_id=_entero(entrada, "servicio_id"),
        fecha=agenda.interpretar_fecha(_texto(entrada, "fecha", 30)),
        veterinario_id=_entero(entrada, "veterinario_id", opcional=True),
        ahora=ctx.ahora,
    )


def _agendar_cita(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    cita = agenda.agendar_cita(
        bd,
        propietario_id=_requiere_cliente(ctx),
        mascota_id=_entero(entrada, "mascota_id"),
        servicio_id=_entero(entrada, "servicio_id"),
        veterinario_id=_entero(entrada, "veterinario_id"),
        fecha_hora=agenda.interpretar_fecha_hora(_texto(entrada, "fecha_hora", 40)),
        motivo=entrada.get("motivo") if isinstance(entrada.get("motivo"), str) else None,
        ahora=ctx.ahora,
    )
    return {"agendada": True, "cita": cita}


def _cancelar_cita(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    cita = agenda.cancelar_cita(
        bd, propietario_id=_requiere_cliente(ctx), cita_id=_entero(entrada, "cita_id"), ahora=ctx.ahora
    )
    return {"cancelada": True, "cita": cita}


def _reprogramar_cita(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    cita = agenda.reprogramar_cita(
        bd,
        propietario_id=_requiere_cliente(ctx),
        cita_id=_entero(entrada, "cita_id"),
        nueva_fecha_hora=agenda.interpretar_fecha_hora(_texto(entrada, "nueva_fecha_hora", 40)),
        ahora=ctx.ahora,
    )
    return {"reprogramada": True, "cita": cita}


def _ver_citas(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    return agenda.ver_citas(
        bd, propietario_id=_requiere_cliente(ctx), mascota_id=_entero(entrada, "mascota_id"), ahora=ctx.ahora
    )


def _proxima_vacuna(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    return vacunas.proxima_vacuna(
        bd, propietario_id=_requiere_cliente(ctx), mascota_id=_entero(entrada, "mascota_id"), hoy=ctx.ahora.date()
    )


def _derivar_a_humano(bd: Session, entrada: dict, ctx: ContextoHerramienta) -> dict:
    motivo = _texto(entrada, "motivo")
    urgente = entrada.get("urgente") is True
    sesiones.marcar_derivacion(bd, ctx.sesion_id, motivo, urgente)
    ctx.requiere_humano = True
    logger.warning("Conversación %s derivada a una persona (urgente=%s): %s", ctx.sesion_id, urgente, motivo)
    return {
        "derivado": True,
        "telefono_urgencias": ajustes.clinica_telefono_urgencias,
        "telefono_clinica": ajustes.clinica_telefono,
        "horario_atencion": ajustes.clinica_horario,
    }


_MANEJADORES: dict[str, Callable[[Session, dict, ContextoHerramienta], dict]] = {
    "buscar_cliente": _buscar_cliente,
    "info_servicios": _info_servicios,
    "consultar_disponibilidad": _consultar_disponibilidad,
    "agendar_cita": _agendar_cita,
    "cancelar_cita": _cancelar_cita,
    "reprogramar_cita": _reprogramar_cita,
    "ver_citas": _ver_citas,
    "proxima_vacuna": _proxima_vacuna,
    "derivar_a_humano": _derivar_a_humano,
}

assert set(_MANEJADORES) == {d["name"] for d in DEFINICIONES}, "Cada herramienta definida necesita su manejador"


def _json(datos: dict) -> str:
    return json.dumps(datos, ensure_ascii=False, default=str)


def ejecutar_herramienta(nombre: str, entrada: Any, ctx: ContextoHerramienta) -> ResultadoHerramienta:
    manejador = _MANEJADORES.get(nombre)
    if manejador is None:
        return ResultadoHerramienta(_json({"error": f"La herramienta '{nombre}' no existe."}), es_error=True)
    if not isinstance(entrada, dict):
        return ResultadoHerramienta(_json({"error": "Los argumentos deben ser un objeto JSON."}), es_error=True)

    propietario_antes, humano_antes = ctx.propietario_id, ctx.requiere_humano
    try:
        with transaccion() as bd:
            datos = manejador(bd, entrada, ctx)
        logger.info("Herramienta %s ejecutada (sesión %s)", nombre, ctx.sesion_id)
        return ResultadoHerramienta(_json(datos))
    except ErrorNegocio as error:
        ctx.propietario_id, ctx.requiere_humano = propietario_antes, humano_antes
        logger.info("Herramienta %s rechazada (sesión %s): %s", nombre, ctx.sesion_id, error)
        return ResultadoHerramienta(_json({"error": str(error)}), es_error=True)
    except Exception:
        ctx.propietario_id, ctx.requiere_humano = propietario_antes, humano_antes
        logger.exception("Fallo inesperado en la herramienta %s (sesión %s)", nombre, ctx.sesion_id)
        return ResultadoHerramienta(
            _json({"error": "Error interno del sistema. Ofrece al cliente comunicarlo con la clínica."}),
            es_error=True,
        )
