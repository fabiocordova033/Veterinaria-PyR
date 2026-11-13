"""Lógica de agenda: disponibilidad, agendar, cancelar, reprogramar y consultar citas.

Regla central: un veterinario no puede tener dos citas que se crucen (tampoco
una mascota). Agendar y reprogramar ocurren dentro de una transacción que
primero bloquea la mascota y el veterinario y después valida y escribe:

- PostgreSQL / MySQL: SELECT ... FOR UPDATE sobre esas filas.
- SQLite: no tiene bloqueo por fila; la transacción arranca con BEGIN IMMEDIATE
  (ver app/db/base.py), que serializa las escrituras.

Así, si llegan dos solicitudes por el mismo horario al mismo tiempo, la segunda
espera a que termine la primera, ve la cita recién creada y es rechazada.

Todas las funciones reciben `ahora` (hora local de la clínica) para que las
pruebas puedan fijar el reloj.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ajustes
from app.db.modelos import Cita, HorarioVeterinario, Mascota, Servicio, Veterinario
from app.servicios import formato
from app.servicios.clientes import obtener_cita_del_propietario, obtener_mascota_del_propietario
from app.servicios.errores import ErrorNegocio

# Una cita cancelada libera su horario; una atendida o programada lo ocupa.
ESTADOS_QUE_OCUPAN = ("programada", "atendida")


# ---------------------------------------------------------------------------
# Interpretación de fechas que llegan como texto
# ---------------------------------------------------------------------------


def interpretar_fecha(texto: str) -> date:
    try:
        return date.fromisoformat(texto.strip())
    except (AttributeError, ValueError):
        raise ErrorNegocio(f"La fecha '{texto}' no es válida. Usa el formato AAAA-MM-DD.") from None


def interpretar_fecha_hora(texto: str) -> datetime:
    """Acepta 'AAAA-MM-DDTHH:MM' (o con espacio). Si trae zona horaria, la pasa a la hora local de la clínica."""
    try:
        valor = datetime.fromisoformat(texto.strip())
    except (AttributeError, ValueError):
        raise ErrorNegocio(f"La fecha y hora '{texto}' no es válida. Usa el formato AAAA-MM-DDTHH:MM.") from None
    if valor.tzinfo is not None:
        valor = valor.astimezone(ajustes.tz).replace(tzinfo=None)
    return valor.replace(second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Ayudantes internos
# ---------------------------------------------------------------------------


def _obtener_servicio(sesion: Session, servicio_id: int) -> Servicio:
    servicio = sesion.get(Servicio, servicio_id)
    if servicio is None:
        raise ErrorNegocio("Ese servicio no existe. Consulta la lista de servicios.")
    return servicio


def _obtener_veterinario(sesion: Session, veterinario_id: int) -> Veterinario:
    veterinario = sesion.get(Veterinario, veterinario_id)
    if veterinario is None:
        raise ErrorNegocio("Ese veterinario no existe. Consulta la lista de veterinarios.")
    return veterinario


def _bloquear(sesion: Session, modelo, id_: int) -> None:
    """Bloquea la fila hasta el final de la transacción (en SQLite ya está bloqueada la base)."""
    sesion.execute(select(modelo.id).where(modelo.id == id_).with_for_update())


def _validar_ventana(inicio: datetime, ahora: datetime) -> None:
    minimo = ahora + timedelta(minutes=ajustes.anticipacion_minutos)
    if inicio < minimo:
        if inicio <= ahora:
            raise ErrorNegocio("Esa fecha y hora ya pasó. Elige un horario futuro.")
        raise ErrorNegocio(
            f"Las citas se agendan con al menos {ajustes.anticipacion_minutos} minutos de anticipación."
        )
    if inicio.date() > ahora.date() + timedelta(days=ajustes.dias_maximos_agenda):
        raise ErrorNegocio(f"Solo se agenda con máximo {ajustes.dias_maximos_agenda} días de anticipación.")


def _bloques_del_dia(sesion: Session, veterinario_id: int, dia: date) -> list[HorarioVeterinario]:
    return list(
        sesion.scalars(
            select(HorarioVeterinario)
            .where(
                HorarioVeterinario.veterinario_id == veterinario_id,
                HorarioVeterinario.dia_semana == dia.weekday(),
            )
            .order_by(HorarioVeterinario.hora_inicio)
        )
    )


def _dentro_de_horario(sesion: Session, veterinario_id: int, inicio: datetime, fin: datetime) -> bool:
    """La cita completa debe caber en un solo bloque de trabajo del veterinario."""
    if inicio.date() != fin.date():
        return False
    return any(
        b.hora_inicio <= inicio.time() and fin.time() <= b.hora_fin
        for b in _bloques_del_dia(sesion, veterinario_id, inicio.date())
    )


def _citas_que_se_cruzan(
    sesion: Session,
    inicio: datetime,
    fin: datetime,
    *,
    veterinario_id: int | None = None,
    mascota_id: int | None = None,
    excluir_cita_id: int | None = None,
) -> list[Cita]:
    # Dos intervalos [a, b) y [c, d) se cruzan si a < d y c < b.
    # Por eso una cita que termina a las 9:30 no choca con otra que empieza a las 9:30.
    consulta = select(Cita).where(
        Cita.estado.in_(ESTADOS_QUE_OCUPAN),
        Cita.fecha_hora < fin,
        Cita.fecha_hora_fin > inicio,
    )
    if veterinario_id is not None:
        consulta = consulta.where(Cita.veterinario_id == veterinario_id)
    if mascota_id is not None:
        consulta = consulta.where(Cita.mascota_id == mascota_id)
    if excluir_cita_id is not None:
        consulta = consulta.where(Cita.id != excluir_cita_id)
    return list(sesion.scalars(consulta.order_by(Cita.fecha_hora)))


def _validar_disponible(
    sesion: Session,
    veterinario: Veterinario,
    mascota: Mascota,
    inicio: datetime,
    fin: datetime,
    excluir_cita_id: int | None = None,
) -> None:
    if not _dentro_de_horario(sesion, veterinario.id, inicio, fin):
        raise ErrorNegocio(
            f"{veterinario.nombre} no atiende en ese horario ({inicio:%H:%M}-{fin:%H:%M} del "
            f"{formato.fecha_larga(inicio.date())}). Consulta la disponibilidad para ver horarios libres."
        )
    cruce = _citas_que_se_cruzan(
        sesion, inicio, fin, veterinario_id=veterinario.id, excluir_cita_id=excluir_cita_id
    )
    if cruce:
        c = cruce[0]
        raise ErrorNegocio(
            f"{veterinario.nombre} ya tiene una cita de {c.fecha_hora:%H:%M} a {c.fecha_hora_fin:%H:%M} "
            "ese día. Consulta la disponibilidad para ofrecer otro horario."
        )
    cruce = _citas_que_se_cruzan(sesion, inicio, fin, mascota_id=mascota.id, excluir_cita_id=excluir_cita_id)
    if cruce:
        c = cruce[0]
        raise ErrorNegocio(
            f"{mascota.nombre} ya tiene otra cita de {c.fecha_hora:%H:%M} a {c.fecha_hora_fin:%H:%M} ese día."
        )


def describir_cita(cita: Cita) -> dict:
    return {
        "cita_id": cita.id,
        "mascota": cita.mascota.nombre,
        "servicio": cita.servicio.nombre,
        "precio": formato.dinero(cita.servicio.precio),
        "veterinario": cita.veterinario.nombre,
        "fecha_hora": formato.fecha_hora_iso(cita.fecha_hora),
        "fecha_hora_texto": formato.fecha_hora_larga(cita.fecha_hora),
        "hora_fin": f"{cita.fecha_hora_fin:%H:%M}",
        "motivo": cita.motivo,
        "estado": cita.estado,
    }


# ---------------------------------------------------------------------------
# Operaciones públicas
# ---------------------------------------------------------------------------


def consultar_disponibilidad(
    sesion: Session,
    *,
    servicio_id: int,
    fecha: date,
    ahora: datetime,
    veterinario_id: int | None = None,
) -> dict:
    """Horarios de inicio libres para un servicio en una fecha, por veterinario.

    Un horario está libre si la cita completa (según la duración del servicio)
    cabe en un bloque de trabajo del veterinario, no se cruza con otra cita y
    respeta la anticipación mínima.
    """
    servicio = _obtener_servicio(sesion, servicio_id)
    if fecha < ahora.date():
        raise ErrorNegocio("Esa fecha ya pasó. Elige una fecha futura.")
    if fecha > ahora.date() + timedelta(days=ajustes.dias_maximos_agenda):
        raise ErrorNegocio(f"Solo se agenda con máximo {ajustes.dias_maximos_agenda} días de anticipación.")

    if veterinario_id is not None:
        veterinarios = [_obtener_veterinario(sesion, veterinario_id)]
    else:
        veterinarios = list(sesion.scalars(select(Veterinario).order_by(Veterinario.id)))

    inicio_dia = datetime.combine(fecha, time.min)
    ocupadas: dict[int, list[Cita]] = defaultdict(list)
    for cita in _citas_que_se_cruzan(sesion, inicio_dia, inicio_dia + timedelta(days=1)):
        ocupadas[cita.veterinario_id].append(cita)

    duracion = timedelta(minutes=servicio.duracion_minutos)
    paso = timedelta(minutes=ajustes.intervalo_minutos)
    limite = ahora + timedelta(minutes=ajustes.anticipacion_minutos)

    disponibilidad = []
    for veterinario in veterinarios:
        bloques = _bloques_del_dia(sesion, veterinario.id, fecha)
        libres = []
        for bloque in bloques:
            inicio = datetime.combine(fecha, bloque.hora_inicio)
            fin_bloque = datetime.combine(fecha, bloque.hora_fin)
            while inicio + duracion <= fin_bloque:
                fin = inicio + duracion
                choca = any(c.fecha_hora < fin and c.fecha_hora_fin > inicio for c in ocupadas[veterinario.id])
                if inicio >= limite and not choca:
                    libres.append(f"{inicio:%H:%M}")
                inicio += paso
        disponibilidad.append(
            {
                "veterinario_id": veterinario.id,
                "veterinario": veterinario.nombre,
                "especialidad": veterinario.especialidad,
                "atiende_ese_dia": bool(bloques),
                "horarios_libres": libres,
            }
        )

    return {
        "fecha": fecha.isoformat(),
        "fecha_texto": formato.fecha_larga(fecha),
        "servicio": {
            "id": servicio.id,
            "nombre": servicio.nombre,
            "duracion_minutos": servicio.duracion_minutos,
            "precio": formato.dinero(servicio.precio),
        },
        "disponibilidad": disponibilidad,
        "hay_horarios_libres": any(d["horarios_libres"] for d in disponibilidad),
    }


def agendar_cita(
    sesion: Session,
    *,
    propietario_id: int,
    mascota_id: int,
    servicio_id: int,
    veterinario_id: int,
    fecha_hora: datetime,
    motivo: str | None,
    ahora: datetime,
) -> dict:
    mascota = obtener_mascota_del_propietario(sesion, propietario_id, mascota_id)
    servicio = _obtener_servicio(sesion, servicio_id)
    veterinario = _obtener_veterinario(sesion, veterinario_id)
    _validar_ventana(fecha_hora, ahora)
    fin = fecha_hora + timedelta(minutes=servicio.duracion_minutos)

    # Bloqueo siempre en el mismo orden (mascota y luego veterinario) para evitar interbloqueos.
    _bloquear(sesion, Mascota, mascota.id)
    _bloquear(sesion, Veterinario, veterinario.id)
    _validar_disponible(sesion, veterinario, mascota, fecha_hora, fin)

    cita = Cita(
        mascota=mascota,
        veterinario=veterinario,
        servicio=servicio,
        fecha_hora=fecha_hora,
        fecha_hora_fin=fin,
        motivo=(motivo or "").strip()[:255] or None,
        estado="programada",
    )
    sesion.add(cita)
    sesion.flush()  # asigna el id sin cerrar la transacción
    return describir_cita(cita)


def cancelar_cita(sesion: Session, *, propietario_id: int, cita_id: int, ahora: datetime) -> dict:
    cita = obtener_cita_del_propietario(sesion, propietario_id, cita_id, bloquear=True)
    if cita.estado != "programada":
        raise ErrorNegocio(f"La cita no se puede cancelar porque está {cita.estado}.")
    if cita.fecha_hora <= ahora:
        raise ErrorNegocio("La cita ya pasó, así que no se puede cancelar por este medio.")
    cita.estado = "cancelada"
    return describir_cita(cita)


def reprogramar_cita(
    sesion: Session, *, propietario_id: int, cita_id: int, nueva_fecha_hora: datetime, ahora: datetime
) -> dict:
    """Mueve la cita a otra fecha y hora con el mismo veterinario y servicio."""
    cita = obtener_cita_del_propietario(sesion, propietario_id, cita_id, bloquear=True)
    if cita.estado != "programada":
        raise ErrorNegocio(f"La cita no se puede reprogramar porque está {cita.estado}.")
    if nueva_fecha_hora == cita.fecha_hora:
        raise ErrorNegocio("La cita ya está agendada en ese horario.")
    _validar_ventana(nueva_fecha_hora, ahora)

    duracion = cita.fecha_hora_fin - cita.fecha_hora  # conserva la duración con que se agendó
    nuevo_fin = nueva_fecha_hora + duracion

    _bloquear(sesion, Mascota, cita.mascota_id)
    _bloquear(sesion, Veterinario, cita.veterinario_id)
    # Se excluye la propia cita: puede moverse a un horario que se solapa con el suyo actual.
    _validar_disponible(sesion, cita.veterinario, cita.mascota, nueva_fecha_hora, nuevo_fin, excluir_cita_id=cita.id)

    anterior = cita.fecha_hora
    cita.fecha_hora = nueva_fecha_hora
    cita.fecha_hora_fin = nuevo_fin
    return {**describir_cita(cita), "fecha_hora_anterior": formato.fecha_hora_larga(anterior)}


def ver_citas(sesion: Session, *, propietario_id: int, mascota_id: int, ahora: datetime) -> dict:
    """Próximas citas programadas de una mascota."""
    mascota = obtener_mascota_del_propietario(sesion, propietario_id, mascota_id)
    citas = sesion.scalars(
        select(Cita)
        .where(Cita.mascota_id == mascota.id, Cita.estado == "programada", Cita.fecha_hora >= ahora)
        .order_by(Cita.fecha_hora)
    )
    return {"mascota": mascota.nombre, "proximas_citas": [describir_cita(c) for c in citas]}
