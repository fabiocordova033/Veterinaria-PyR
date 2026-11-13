"""Canal WhatsApp (Meta WhatsApp Cloud API).

Rutas:
- GET  /whatsapp/webhook   verificación del webhook (Meta envía hub.challenge).
- POST /whatsapp/webhook   mensajes entrantes.

Flujo de un mensaje:
1. Se valida la firma X-Hub-Signature-256 con el App Secret: solo Meta puede llamar al webhook.
2. Se ignoran mensajes repetidos (Meta reintenta si no respondemos rápido) y avisos de entrega.
3. El texto, o una marca [Adjunto: ...] para audios, fotos, etc., pasa al agrupador.
4. Cuando el cliente deja de escribir unos segundos, sus mensajes van juntos al motor del bot
   y la respuesta se envía con la API de WhatsApp. El webhook responde 200 de inmediato.

El número del remitente llega verificado por WhatsApp: se usa como sesión
("whatsapp-<número>") y como teléfono verificado, así el bot no lo pide y solo
puede consultar los datos de ese número.

Se activa cuando WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN y
WHATSAPP_APP_SECRET tienen valor. El agrupador y el control de repetidos viven en
memoria: la aplicación debe correr en un solo proceso.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.bot.agrupador import AgrupadorMensajes
from app.bot.motor import ErrorServicioIA, RespuestaBot
from app.canales.comun import motor_compartido
from app.config import Ajustes, ajustes

logger = logging.getLogger(__name__)

LIMITE_TEXTO_WHATSAPP = 4096
LIMITE_TEXTO_AL_BOT = 4000  # tope para un grupo de mensajes de un cliente

# Tipos de mensaje que el bot no puede leer: se le avisa al modelo con una marca de texto.
ADJUNTOS = {
    "audio": "una nota de voz o audio",
    "image": "una foto",
    "video": "un video",
    "sticker": "un sticker",
    "document": "un documento",
    "location": "una ubicación",
    "contacts": "un contacto",
}
# Tipos que no necesitan respuesta.
IGNORADOS = {"reaction", "system"}


class MotorLike(Protocol):
    def responder(
        self, sesion_id: str, texto: str, *, canal: str = "web", telefono_verificado: str | None = None
    ) -> RespuestaBot: ...


@dataclass(frozen=True)
class ConfigWhatsApp:
    token: str
    phone_number_id: str
    verify_token: str
    app_secret: str
    version_api: str = "v24.0"
    espera_segundos: float = 4.0
    espera_maxima_segundos: float = 15.0

    @classmethod
    def desde_ajustes(cls, a: Ajustes) -> ConfigWhatsApp | None:
        if not (a.whatsapp_token and a.whatsapp_phone_number_id and a.whatsapp_verify_token and a.whatsapp_app_secret):
            return None
        return cls(
            token=a.whatsapp_token,
            phone_number_id=a.whatsapp_phone_number_id,
            verify_token=a.whatsapp_verify_token,
            app_secret=a.whatsapp_app_secret,
            version_api=a.whatsapp_version_api,
            espera_segundos=a.whatsapp_espera_segundos,
            espera_maxima_segundos=a.whatsapp_espera_maxima_segundos,
        )


@dataclass(frozen=True)
class MensajeEntrante:
    id: str
    numero: str  # wa_id, con código de país (p. ej. 51987111222)
    texto: str


# ---------------------------------------------------------------------------
# Lectura de los webhooks de Meta
# ---------------------------------------------------------------------------


def texto_de_mensaje(mensaje: dict) -> str | None:
    """Convierte un mensaje de WhatsApp en texto para el bot (None si no requiere respuesta)."""
    tipo = mensaje.get("type")
    if tipo == "text":
        return ((mensaje.get("text") or {}).get("body") or "").strip() or None
    if tipo in IGNORADOS:
        return None
    if tipo == "button":
        return (mensaje.get("button") or {}).get("text")
    if tipo == "interactive":
        interactivo = mensaje.get("interactive") or {}
        respuesta = interactivo.get("button_reply") or interactivo.get("list_reply") or {}
        return respuesta.get("title")

    descripcion = ADJUNTOS.get(tipo, "un tipo de mensaje que no puedes leer")
    texto = f"[Adjunto: el cliente envió {descripcion}.]"
    detalle = mensaje.get(tipo)
    leyenda = detalle.get("caption") if isinstance(detalle, dict) else None
    if leyenda:
        texto += f"\n{leyenda.strip()}"
    return texto


def extraer_mensajes(datos: dict, phone_number_id: str) -> list[MensajeEntrante]:
    mensajes = []
    for entrada in datos.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            if cambio.get("field") != "messages":
                continue
            valor = cambio.get("value") or {}
            destino = (valor.get("metadata") or {}).get("phone_number_id")
            if destino is not None and str(destino) != phone_number_id:
                continue  # mensaje para otro número de la misma cuenta
            for mensaje in valor.get("messages") or []:  # los "statuses" (entregado, leído) se ignoran
                numero = str(mensaje.get("from") or "")
                texto = texto_de_mensaje(mensaje)
                if texto and mensaje.get("id") and numero.isdigit() and len(numero) <= 15:
                    mensajes.append(MensajeEntrante(id=str(mensaje["id"]), numero=numero, texto=texto))
    return mensajes


def partir_texto(texto: str, limite: int = LIMITE_TEXTO_WHATSAPP) -> list[str]:
    """WhatsApp acepta hasta 4096 caracteres por mensaje; se corta preferentemente en un salto de línea."""
    partes = []
    while len(texto) > limite:
        corte = texto.rfind("\n", 0, limite)
        if corte <= 0:
            corte = limite
        partes.append(texto[:corte].rstrip())
        texto = texto[corte:].lstrip("\n")
    if texto.strip():
        partes.append(texto)
    return partes


def ocultar_numero(numero: str) -> str:
    """Para los logs: 51987111222 -> 51987***222."""
    return numero[:-6] + "***" + numero[-3:] if len(numero) > 6 else "***"


# ---------------------------------------------------------------------------
# Envío con la API de WhatsApp
# ---------------------------------------------------------------------------


class ClienteWhatsApp:
    def __init__(self, config: ConfigWhatsApp, http: httpx.Client | None = None):
        self._config = config
        self._http = http or httpx.Client(timeout=20.0)

    @property
    def url(self) -> str:
        return f"https://graph.facebook.com/{self._config.version_api}/{self._config.phone_number_id}/messages"

    def _enviar(self, cuerpo: dict[str, Any]) -> None:
        respuesta = self._http.post(self.url, json=cuerpo, headers={"Authorization": f"Bearer {self._config.token}"})
        if respuesta.status_code >= 400:
            raise RuntimeError(f"La API de WhatsApp respondió {respuesta.status_code}: {respuesta.text[:300]}")

    def enviar_texto(self, numero: str, texto: str) -> None:
        for parte in partir_texto(texto):
            self._enviar(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": numero,
                    "type": "text",
                    "text": {"preview_url": False, "body": parte},
                }
            )

    def marcar_leido(self, message_id: str) -> None:
        self._enviar({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})


# ---------------------------------------------------------------------------
# Canal
# ---------------------------------------------------------------------------


def mensaje_no_disponible(a: Ajustes) -> str:
    return (
        "Disculpa, en este momento no puedo responder. Intenta de nuevo en unos minutos o llama a la clínica "
        f"al {a.clinica_telefono}. Si es una urgencia, llama ya al {a.clinica_telefono_urgencias}."
    )


class CanalWhatsApp:
    def __init__(
        self,
        config: ConfigWhatsApp,
        cliente: ClienteWhatsApp,
        motor: Callable[[], MotorLike],
        ajustes_clinica: Ajustes = ajustes,
    ):
        self.config = config
        self._cliente = cliente
        self._motor = motor
        self._ajustes = ajustes_clinica
        self._vistos: OrderedDict[str, None] = OrderedDict()
        self._candado = threading.Lock()
        self.agrupador: AgrupadorMensajes[MensajeEntrante] = AgrupadorMensajes(
            self._procesar, config.espera_segundos, config.espera_maxima_segundos
        )

    def firma_valida(self, cuerpo: bytes, firma: str | None) -> bool:
        if not firma or not firma.startswith("sha256="):
            return False
        esperada = hmac.new(self.config.app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
        return hmac.compare_digest(firma.removeprefix("sha256=").encode(), esperada.encode())

    def recibir(self, datos: dict) -> int:
        """Encola los mensajes nuevos del webhook. Devuelve cuántos se encolaron."""
        nuevos = 0
        for mensaje in extraer_mensajes(datos, self.config.phone_number_id):
            if self._ya_visto(mensaje.id):
                continue
            self.agrupador.agregar(mensaje.numero, mensaje)
            nuevos += 1
        return nuevos

    def cerrar(self) -> None:
        """Al apagar la aplicación: responde los grupos que estaban esperando."""
        self.agrupador.vaciar()

    def _ya_visto(self, mensaje_id: str) -> bool:
        with self._candado:
            if mensaje_id in self._vistos:
                return True
            self._vistos[mensaje_id] = None
            if len(self._vistos) > 5000:
                self._vistos.popitem(last=False)
            return False

    def _procesar(self, numero: str, mensajes: list[MensajeEntrante]) -> None:
        """Se ejecuta en un hilo del agrupador con todos los mensajes seguidos del cliente."""
        texto = "\n".join(m.texto for m in mensajes)[:LIMITE_TEXTO_AL_BOT]
        logger.info("WhatsApp: %d mensaje(s) de %s", len(mensajes), ocultar_numero(numero))
        try:
            self._cliente.marcar_leido(mensajes[-1].id)
        except Exception as error:
            logger.warning("No se pudo marcar como leído: %s", error)

        try:
            respuesta = self._motor().responder(
                f"whatsapp-{numero}", texto, canal="whatsapp", telefono_verificado=numero
            ).texto
        except ErrorServicioIA as error:
            logger.error("El bot no pudo responder por WhatsApp: %s", error)
            respuesta = mensaje_no_disponible(self._ajustes)
        except Exception:
            logger.exception("Fallo inesperado respondiendo por WhatsApp")
            respuesta = mensaje_no_disponible(self._ajustes)

        try:
            self._cliente.enviar_texto(numero, respuesta)
        except Exception:
            logger.exception("No se pudo enviar la respuesta por WhatsApp a %s", ocultar_numero(numero))


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
_canal: CanalWhatsApp | None = None
_candado_canal = threading.Lock()


def obtener_canal() -> CanalWhatsApp:
    global _canal
    with _candado_canal:
        if _canal is None:
            config = ConfigWhatsApp.desde_ajustes(ajustes)
            if config is None:
                raise HTTPException(status_code=404, detail="El canal de WhatsApp no está configurado.")
            _canal = CanalWhatsApp(config, ClienteWhatsApp(config), motor_compartido)
        return _canal


def cerrar_canal() -> None:
    if _canal is not None:
        _canal.cerrar()


@router.get("/webhook", response_class=PlainTextResponse)
def verificar_webhook(
    modo: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    desafio: str = Query("", alias="hub.challenge"),
    canal: CanalWhatsApp = Depends(obtener_canal),
) -> str:
    if modo == "subscribe" and token and hmac.compare_digest(token.encode(), canal.config.verify_token.encode()):
        return desafio
    raise HTTPException(status_code=403, detail="Token de verificación incorrecto.")


@router.post("/webhook")
async def recibir_webhook(request: Request, canal: CanalWhatsApp = Depends(obtener_canal)) -> dict:
    cuerpo = await request.body()
    if not canal.firma_valida(cuerpo, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="Firma inválida.")
    try:
        datos = json.loads(cuerpo)
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON inválido.") from None
    canal.recibir(datos if isinstance(datos, dict) else {})
    return {"ok": True}
