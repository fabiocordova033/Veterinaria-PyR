"""Configuración de la aplicación, leída de variables de entorno o de un archivo .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

# Carga .env desde la raíz del proyecto. Las variables que ya existan en el
# entorno tienen prioridad sobre las del archivo.
load_dotenv(RAIZ_PROYECTO / ".env")


def _texto(nombre: str, por_defecto: str) -> str:
    valor = os.getenv(nombre)
    return valor.strip() if valor and valor.strip() else por_defecto


def _entero(nombre: str, por_defecto: int) -> int:
    valor = os.getenv(nombre)
    return int(valor) if valor and valor.strip() else por_defecto


def _booleano(nombre: str, por_defecto: bool) -> bool:
    valor = os.getenv(nombre)
    if not valor or not valor.strip():
        return por_defecto
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _fallbacks() -> str | None:
    """Respaldo automático de modelo: activo ("default") salvo que se apague explícitamente."""
    valor = os.getenv("ANTHROPIC_FALLBACKS")
    if valor is None:
        return "default"
    valor = valor.strip()
    if valor.lower() in {"", "0", "no", "false", "off"}:
        return None
    return valor


def _url_base_datos() -> str:
    """Una ruta SQLite relativa (sqlite:///veterinaria.db) se toma desde la raíz del proyecto,
    así la base es la misma sin importar desde qué carpeta se ejecute."""
    url = _texto("DATABASE_URL", "sqlite:///veterinaria.db")
    prefijo = "sqlite:///"
    if url.startswith(prefijo):
        ruta = url[len(prefijo):]
        if ruta and ruta != ":memory:" and not Path(ruta).is_absolute():
            return f"{prefijo}{(RAIZ_PROYECTO / ruta).as_posix()}"
    return url


@dataclass(frozen=True)
class Ajustes:
    # --- Claude ---
    anthropic_api_key: str | None
    modelo: str
    esfuerzo: str | None  # low | medium | high | xhigh | max (vacío = el del modelo)
    fallbacks: str | None  # "default" activa el respaldo automático; vacío lo apaga
    herramientas_estrictas: bool
    max_tokens: int
    max_iteraciones_herramientas: int

    # --- Base de datos ---
    database_url: str

    # --- Clínica ---
    clinica_nombre: str
    clinica_direccion: str
    clinica_telefono: str
    clinica_telefono_urgencias: str
    clinica_horario: str
    zona_horaria: str
    moneda: str  # código ISO, p. ej. PEN
    simbolo_moneda: str  # p. ej. S/
    nota_precios: str

    # --- Reglas de agenda ---
    intervalo_minutos: int
    anticipacion_minutos: int
    dias_maximos_agenda: int

    # --- Sesiones de chat ---
    minutos_inactividad_sesion: int
    digitos_telefono_local: int

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.zona_horaria)

    def ahora(self) -> datetime:
        """Fecha y hora local de la clínica, sin zona horaria (así se guarda en la base)."""
        return datetime.now(self.tz).replace(tzinfo=None, second=0, microsecond=0)


def cargar_ajustes() -> Ajustes:
    return Ajustes(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        modelo=_texto("ANTHROPIC_MODEL", "claude-opus-5-5"),
        esfuerzo=_texto("ANTHROPIC_EFFORT", "") or None,
        fallbacks=_fallbacks(),
        herramientas_estrictas=_booleano("ANTHROPIC_STRICT_TOOLS", True),
        max_tokens=_entero("ANTHROPIC_MAX_TOKENS", 16000),
        max_iteraciones_herramientas=_entero("BOT_MAX_ITERACIONES", 8),
        database_url=_url_base_datos(),
        clinica_nombre=_texto("CLINICA_NOMBRE", "Clínica Veterinaria Patitas"),
        clinica_direccion=_texto("CLINICA_DIRECCION", "Av. Benavides 1520, Miraflores, Lima"),
        clinica_telefono=_texto("CLINICA_TELEFONO", "(01) 555 1234"),
        clinica_telefono_urgencias=_texto("CLINICA_TELEFONO_URGENCIAS", "987 555 911"),
        clinica_horario=_texto(
            "CLINICA_HORARIO", "Lunes a viernes de 8:00 a. m. a 6:00 p. m.; sábados de 8:00 a. m. a 1:00 p. m."
        ),
        zona_horaria=_texto("CLINICA_ZONA_HORARIA", "America/Lima"),
        moneda=_texto("CLINICA_MONEDA", "PEN"),
        simbolo_moneda=_texto("CLINICA_SIMBOLO_MONEDA", "S/"),
        nota_precios=_texto("CLINICA_NOTA_PRECIOS", "Precios en soles, incluyen IGV."),
        intervalo_minutos=_entero("AGENDA_INTERVALO_MINUTOS", 30),
        anticipacion_minutos=_entero("AGENDA_ANTICIPACION_MINUTOS", 60),
        dias_maximos_agenda=_entero("AGENDA_DIAS_MAXIMOS", 60),
        minutos_inactividad_sesion=_entero("SESION_MINUTOS_INACTIVIDAD", 720),
        # Celulares en Perú: 9 dígitos. Se usa para quitar el código de país (51 987111222 -> 987111222).
        digitos_telefono_local=_entero("TELEFONO_DIGITOS_LOCALES", 9),
    )


ajustes = cargar_ajustes()
