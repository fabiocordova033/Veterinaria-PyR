"""Modelos de la base de datos (SQLAlchemy 2.0).

Convenciones:
- Fechas y horas se guardan en hora local de la clínica, sin zona horaria.
- dia_semana: 0 = lunes ... 6 = domingo (igual que date.weekday() de Python).
- cita.fecha_hora_fin se calcula al agendar (fecha_hora + duración del servicio).
  Así la búsqueda de cruces es una comparación simple y portable entre motores, y
  la cita conserva su duración aunque después cambie la del servicio.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ESTADOS_CITA = ("programada", "atendida", "cancelada")


# ---------------------------------------------------------------------------
# Clínica
# ---------------------------------------------------------------------------


class Propietario(Base):
    __tablename__ = "propietario"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    documento: Mapped[str] = mapped_column(String(20), index=True)
    telefono: Mapped[str] = mapped_column(String(20), unique=True)
    email: Mapped[str | None] = mapped_column(String(120))

    mascotas: Mapped[list[Mascota]] = relationship(back_populates="propietario", order_by="Mascota.id")


class Mascota(Base):
    __tablename__ = "mascota"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    especie: Mapped[str] = mapped_column(String(40))
    raza: Mapped[str | None] = mapped_column(String(80))
    fecha_nacimiento: Mapped[date | None] = mapped_column(Date)
    propietario_id: Mapped[int] = mapped_column(ForeignKey("propietario.id"), index=True)

    propietario: Mapped[Propietario] = relationship(back_populates="mascotas")


class Veterinario(Base):
    __tablename__ = "veterinario"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    especialidad: Mapped[str] = mapped_column(String(80))
    tarjeta_profesional: Mapped[str] = mapped_column(String(30), unique=True)

    horarios: Mapped[list[HorarioVeterinario]] = relationship(
        back_populates="veterinario", order_by="(HorarioVeterinario.dia_semana, HorarioVeterinario.hora_inicio)"
    )


class HorarioVeterinario(Base):
    __tablename__ = "horario_veterinario"
    __table_args__ = (
        CheckConstraint("dia_semana BETWEEN 0 AND 6", name="ck_horario_dia_semana"),
        CheckConstraint("hora_inicio < hora_fin", name="ck_horario_rango"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    veterinario_id: Mapped[int] = mapped_column(ForeignKey("veterinario.id"), index=True)
    dia_semana: Mapped[int] = mapped_column(Integer)
    hora_inicio: Mapped[time] = mapped_column(Time)
    hora_fin: Mapped[time] = mapped_column(Time)

    veterinario: Mapped[Veterinario] = relationship(back_populates="horarios")


class Servicio(Base):
    __tablename__ = "servicio"
    __table_args__ = (CheckConstraint("duracion_minutos > 0", name="ck_servicio_duracion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    precio: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    duracion_minutos: Mapped[int] = mapped_column(Integer)


class Cita(Base):
    __tablename__ = "cita"
    __table_args__ = (
        CheckConstraint("estado IN ('programada', 'atendida', 'cancelada')", name="ck_cita_estado"),
        CheckConstraint("fecha_hora < fecha_hora_fin", name="ck_cita_rango"),
        Index("ix_cita_veterinario_fecha", "veterinario_id", "fecha_hora"),
        Index("ix_cita_mascota_fecha", "mascota_id", "fecha_hora"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mascota_id: Mapped[int] = mapped_column(ForeignKey("mascota.id"))
    veterinario_id: Mapped[int] = mapped_column(ForeignKey("veterinario.id"))
    servicio_id: Mapped[int] = mapped_column(ForeignKey("servicio.id"))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime)
    fecha_hora_fin: Mapped[datetime] = mapped_column(DateTime)
    motivo: Mapped[str | None] = mapped_column(String(255))
    estado: Mapped[str] = mapped_column(String(20), default="programada")

    mascota: Mapped[Mascota] = relationship()
    veterinario: Mapped[Veterinario] = relationship()
    servicio: Mapped[Servicio] = relationship()


class Consulta(Base):
    __tablename__ = "consulta"

    id: Mapped[int] = mapped_column(primary_key=True)
    cita_id: Mapped[int | None] = mapped_column(ForeignKey("cita.id"), unique=True)
    mascota_id: Mapped[int] = mapped_column(ForeignKey("mascota.id"), index=True)
    veterinario_id: Mapped[int] = mapped_column(ForeignKey("veterinario.id"))
    fecha: Mapped[datetime] = mapped_column(DateTime)
    peso: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    diagnostico: Mapped[str | None] = mapped_column(Text)
    tratamiento: Mapped[str | None] = mapped_column(Text)


class Vacunacion(Base):
    __tablename__ = "vacunacion"

    id: Mapped[int] = mapped_column(primary_key=True)
    mascota_id: Mapped[int] = mapped_column(ForeignKey("mascota.id"), index=True)
    vacuna: Mapped[str] = mapped_column(String(80))
    fecha_aplicacion: Mapped[date] = mapped_column(Date)
    proxima_dosis: Mapped[date | None] = mapped_column(Date)


class Medicamento(Base):
    __tablename__ = "medicamento"
    __table_args__ = (CheckConstraint("stock >= 0", name="ck_medicamento_stock"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    precio: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    vencimiento: Mapped[date | None] = mapped_column(Date)


class DetalleReceta(Base):
    __tablename__ = "detalle_receta"

    id: Mapped[int] = mapped_column(primary_key=True)
    consulta_id: Mapped[int] = mapped_column(ForeignKey("consulta.id"), index=True)
    medicamento_id: Mapped[int] = mapped_column(ForeignKey("medicamento.id"))
    dosis: Mapped[str] = mapped_column(String(80))
    frecuencia: Mapped[str] = mapped_column(String(80))


class Factura(Base):
    __tablename__ = "factura"

    id: Mapped[int] = mapped_column(primary_key=True)
    propietario_id: Mapped[int] = mapped_column(ForeignKey("propietario.id"), index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class DetalleFactura(Base):
    __tablename__ = "detalle_factura"
    __table_args__ = (
        # Cada línea es un servicio o un medicamento, nunca ambos ni ninguno.
        CheckConstraint(
            "(servicio_id IS NOT NULL AND medicamento_id IS NULL)"
            " OR (servicio_id IS NULL AND medicamento_id IS NOT NULL)",
            name="ck_detalle_factura_item",
        ),
        CheckConstraint("cantidad > 0", name="ck_detalle_factura_cantidad"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("factura.id"), index=True)
    servicio_id: Mapped[int | None] = mapped_column(ForeignKey("servicio.id"))
    medicamento_id: Mapped[int | None] = mapped_column(ForeignKey("medicamento.id"))
    cantidad: Mapped[int] = mapped_column(Integer, default=1)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))


# ---------------------------------------------------------------------------
# Chat (no forma parte del modelo de la clínica; guarda las conversaciones)
# ---------------------------------------------------------------------------


class SesionChat(Base):
    """Una sesión por usuario de un canal: "web-<uuid>" o "whatsapp-<número>".

    Cuando pasa mucho tiempo sin actividad se abre una conversación nueva
    (numero_conversacion + 1) y el historial anterior queda guardado.
    """

    __tablename__ = "sesion_chat"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canal: Mapped[str] = mapped_column(String(20))
    telefono_verificado: Mapped[str | None] = mapped_column(String(20))
    numero_conversacion: Mapped[int] = mapped_column(Integer, default=1)
    propietario_id: Mapped[int | None] = mapped_column(ForeignKey("propietario.id"))
    requiere_humano: Mapped[bool] = mapped_column(Boolean, default=False)
    urgente: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo_derivacion: Mapped[str | None] = mapped_column(String(500))
    creada_en: Mapped[datetime] = mapped_column(DateTime)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime)


class MensajeChat(Base):
    """Cada mensaje se guarda tal como se envió o recibió de la API de Claude (formato JSON)."""

    __tablename__ = "mensaje_chat"
    __table_args__ = (Index("ix_mensaje_sesion_conversacion", "sesion_id", "numero_conversacion", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sesion_id: Mapped[str] = mapped_column(ForeignKey("sesion_chat.id"))
    numero_conversacion: Mapped[int] = mapped_column(Integer)
    rol: Mapped[str] = mapped_column(String(20))
    contenido: Mapped[list] = mapped_column(JSON)
    creado_en: Mapped[datetime] = mapped_column(DateTime)
