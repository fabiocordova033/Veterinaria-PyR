"""Configuración común de las pruebas: base SQLite temporal y datos mínimos conocidos."""

import os

# Valores fijos para que las pruebas no dependan del .env de cada desarrollador.
# (Se definen antes de importar app.config; load_dotenv no sobrescribe variables existentes.)
os.environ.update(
    {
        "ANTHROPIC_API_KEY": "",
        "AGENDA_INTERVALO_MINUTOS": "30",
        "AGENDA_ANTICIPACION_MINUTOS": "60",
        "AGENDA_DIAS_MAXIMOS": "60",
        "SESION_MINUTOS_INACTIVIDAD": "720",
        "TELEFONO_DIGITOS_LOCALES": "10",
        "CLINICA_ZONA_HORARIA": "America/Bogota",
    }
)

from dataclasses import dataclass  # noqa: E402
from datetime import date, datetime, time  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from app.db.base import configurar_bd, crear_tablas, transaccion  # noqa: E402
from app.db.modelos import HorarioVeterinario, Mascota, Propietario, Servicio, Veterinario  # noqa: E402
from app.servicios import agenda  # noqa: E402

# Reloj fijo: lunes 7 de enero de 2030, 8:00 a. m.
AHORA = datetime(2030, 1, 7, 8, 0)
LUNES = date(2030, 1, 7)
MARTES = date(2030, 1, 8)
MIERCOLES = date(2030, 1, 9)


@dataclass
class Datos:
    vet1: int  # lunes y martes 8-12 y 14-18
    vet2: int  # lunes 8-12
    consulta: int  # 30 min
    bano: int  # 60 min
    limpieza: int  # 90 min
    propietario_a: int
    propietario_b: int
    a1: int  # mascotas de A
    a2: int
    b1: int  # mascota de B


@pytest.fixture
def datos(tmp_path) -> Datos:
    engine = configurar_bd(f"sqlite:///{tmp_path / 'prueba.db'}")
    crear_tablas()
    with transaccion() as bd:
        vet1 = Veterinario(nombre="Dra. Uno", especialidad="General", tarjeta_profesional="T-1")
        vet2 = Veterinario(nombre="Dr. Dos", especialidad="Cirugía", tarjeta_profesional="T-2")
        bd.add_all([vet1, vet2])
        for dia in (0, 1):
            for inicio, fin in (("08:00", "12:00"), ("14:00", "18:00")):
                bd.add(HorarioVeterinario(veterinario=vet1, dia_semana=dia,
                                          hora_inicio=time.fromisoformat(inicio), hora_fin=time.fromisoformat(fin)))
        bd.add(HorarioVeterinario(veterinario=vet2, dia_semana=0, hora_inicio=time(8), hora_fin=time(12)))

        consulta = Servicio(nombre="Consulta general", precio=Decimal("60000"), duracion_minutos=30)
        bano = Servicio(nombre="Baño", precio=Decimal("55000"), duracion_minutos=60)
        limpieza = Servicio(nombre="Limpieza dental", precio=Decimal("180000"), duracion_minutos=90)
        bd.add_all([consulta, bano, limpieza])

        propietario_a = Propietario(nombre="Ana", documento="1", telefono="3000000001")
        propietario_a.mascotas = [Mascota(nombre="Firulais", especie="Perro"), Mascota(nombre="Mishi", especie="Gato")]
        propietario_b = Propietario(nombre="Beto", documento="2", telefono="3000000002")
        propietario_b.mascotas = [Mascota(nombre="Rex", especie="Perro")]
        bd.add_all([propietario_a, propietario_b])
        bd.flush()

        resultado = Datos(
            vet1=vet1.id, vet2=vet2.id,
            consulta=consulta.id, bano=bano.id, limpieza=limpieza.id,
            propietario_a=propietario_a.id, propietario_b=propietario_b.id,
            a1=propietario_a.mascotas[0].id, a2=propietario_a.mascotas[1].id, b1=propietario_b.mascotas[0].id,
        )
    yield resultado
    engine.dispose()


def agendar(datos: Datos, inicio: str, *, mascota=None, veterinario=None, servicio=None, propietario=None, ahora=AHORA):
    """Atajo para agendar en una transacción propia, como lo hace el bot."""
    with transaccion() as bd:
        return agenda.agendar_cita(
            bd,
            propietario_id=propietario or datos.propietario_a,
            mascota_id=mascota or datos.a1,
            servicio_id=servicio or datos.consulta,
            veterinario_id=veterinario or datos.vet1,
            fecha_hora=datetime.fromisoformat(inicio),
            motivo="Prueba",
            ahora=ahora,
        )
