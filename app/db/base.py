"""Conexión a la base de datos y manejo de transacciones.

Todo el acceso a datos pasa por SQLAlchemy, así que para migrar de SQLite a
MySQL o PostgreSQL basta con cambiar DATABASE_URL (y instalar el driver).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import ajustes


class Base(DeclarativeBase):
    pass


FabricaSesion = sessionmaker(expire_on_commit=False)
_engine: Engine | None = None


def crear_engine(url: str) -> Engine:
    if not url.startswith("sqlite"):
        # pool_pre_ping evita errores por conexiones cerradas por el servidor (MySQL/PostgreSQL).
        return create_engine(url, pool_pre_ping=True)

    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _al_conectar(conexion_dbapi, _registro):
        # Dejamos que SQLAlchemy emita el BEGIN (en lugar del driver sqlite3)
        # y activamos las llaves foráneas, que SQLite trae apagadas.
        conexion_dbapi.isolation_level = None
        cursor = conexion_dbapi.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _al_iniciar(conexion):
        # SQLite no tiene bloqueo por fila (SELECT ... FOR UPDATE). BEGIN IMMEDIATE
        # toma el bloqueo de escritura al iniciar la transacción, de modo que dos
        # transacciones nunca validan y escriben la agenda al mismo tiempo.
        conexion.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def configurar_bd(url: str | None = None) -> Engine:
    """Crea el engine y lo asocia a la fábrica de sesiones. Las pruebas lo usan con una base temporal."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = crear_engine(url or ajustes.database_url)
    FabricaSesion.configure(bind=_engine)
    return _engine


def obtener_engine() -> Engine:
    if _engine is None:
        configurar_bd()
    assert _engine is not None
    return _engine


def crear_tablas() -> None:
    from app.db import modelos  # noqa: F401  (registra los modelos en Base.metadata)

    Base.metadata.create_all(obtener_engine())


@contextmanager
def transaccion() -> Iterator[Session]:
    """Abre una sesión en una transacción: confirma si todo sale bien, revierte si hay una excepción."""
    obtener_engine()
    with FabricaSesion.begin() as sesion:
        yield sesion
