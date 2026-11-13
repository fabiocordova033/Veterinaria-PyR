"""Crea las tablas y carga datos de prueba.

Uso (desde la raíz del proyecto):
    python -m scripts.crear_base              # crea la base si está vacía
    python -m scripts.crear_base --reiniciar  # borra todo y vuelve a cargar

Las fechas de citas y vacunas se calculan a partir de hoy, para que siempre
haya citas próximas con las que probar el bot.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.config import ajustes
from app.db.base import Base, configurar_bd, transaccion
from app.db.modelos import (
    Cita,
    Consulta,
    DetalleFactura,
    DetalleReceta,
    Factura,
    HorarioVeterinario,
    Mascota,
    Medicamento,
    Propietario,
    Servicio,
    Vacunacion,
    Veterinario,
)
from app.servicios import agenda

LUNES, MARTES, MIERCOLES, JUEVES, VIERNES, SABADO = range(6)


def proximo(dia_semana: int, desde: date) -> date:
    """El siguiente día de la semana indicado, siempre después de `desde`."""
    dias = (dia_semana - desde.weekday()) % 7 or 7
    return desde + timedelta(days=dias)


def anterior(dia_semana: int, desde: date, semanas_atras: int = 0) -> date:
    """El día de la semana indicado antes de `desde` (semanas_atras = semanas adicionales hacia atrás)."""
    dias = (desde.weekday() - dia_semana) % 7 or 7
    return desde - timedelta(days=dias + 7 * semanas_atras)


def en(dia: date, hora: str) -> datetime:
    return datetime.combine(dia, time.fromisoformat(hora))


def horario(veterinario: Veterinario, dias: list[int], *bloques: tuple[str, str]) -> list[HorarioVeterinario]:
    return [
        HorarioVeterinario(
            veterinario=veterinario,
            dia_semana=dia,
            hora_inicio=time.fromisoformat(inicio),
            hora_fin=time.fromisoformat(fin),
        )
        for dia in dias
        for inicio, fin in bloques
    ]


def cargar_datos() -> None:
    ahora = ajustes.ahora()
    hoy = ahora.date()

    with transaccion() as bd:
        # --- Veterinarios y horarios -------------------------------------------------
        # En Perú el registro profesional es la colegiatura del CMVP (Colegio Médico Veterinario del Perú).
        patricia = Veterinario(
            nombre="Dra. Patricia Mendoza", especialidad="Medicina general", tarjeta_profesional="CMVP 10234"
        )
        andres = Veterinario(
            nombre="Dr. Andrés Rojas", especialidad="Cirugía y odontología", tarjeta_profesional="CMVP 20871"
        )
        camila = Veterinario(
            nombre="Dra. Camila Torres", especialidad="Dermatología y vacunación", tarjeta_profesional="CMVP 31452"
        )
        bd.add_all([patricia, andres, camila])
        bd.add_all(
            horario(patricia, [LUNES, MARTES, MIERCOLES, JUEVES, VIERNES], ("08:00", "12:00"), ("14:00", "18:00"))
            + horario(andres, [LUNES, MIERCOLES, VIERNES], ("09:00", "13:00"), ("14:00", "17:00"))
            + horario(andres, [SABADO], ("08:00", "13:00"))
            + horario(camila, [MARTES, JUEVES], ("08:00", "12:00"), ("14:00", "18:00"))
            + horario(camila, [SABADO], ("08:00", "13:00"))
        )

        # --- Servicios (precios en soles, IGV incluido) ---------------------------------
        consulta = Servicio(nombre="Consulta general", precio=Decimal("70.00"), duracion_minutos=30)
        vacunacion = Servicio(nombre="Vacunación", precio=Decimal("60.00"), duracion_minutos=30)
        desparasitacion = Servicio(nombre="Desparasitación", precio=Decimal("35.00"), duracion_minutos=30)
        bano = Servicio(nombre="Baño y corte", precio=Decimal("60.00"), duracion_minutos=60)
        limpieza = Servicio(nombre="Profilaxis dental", precio=Decimal("280.00"), duracion_minutos=90)
        bd.add_all([consulta, vacunacion, desparasitacion, bano, limpieza])

        # --- Propietarios y mascotas ---------------------------------------------------
        def propietario(nombre, documento, telefono, email, *mascotas):
            p = Propietario(nombre=nombre, documento=documento, telefono=telefono, email=email)
            p.mascotas = [
                Mascota(nombre=n, especie=e, raza=r, fecha_nacimiento=date.fromisoformat(f)) for n, e, r, f in mascotas
            ]
            bd.add(p)
            return p

        # documento = DNI (8 dígitos); telefono = celular (9 dígitos, sin el código de país 51).
        rosa = propietario(
            "Rosa Quispe Mamani", "45678912", "987111222", "rosa.quispe@example.com",
            ("Luna", "Perro", "Labrador", "2021-03-15"),
            ("Michi", "Gato", "Mestizo", "2022-07-01"),
        )
        luis = propietario(
            "Luis Alberto Huamán", "41234567", "986222333", "luis.huaman@example.com",
            ("Rocky", "Perro", "Perro sin pelo del Perú", "2020-11-20"),
        )
        carmen = propietario(
            "Carmen Rojas Vargas", "70123456", "985333444", None,
            ("Nala", "Gato", "Siamés", "2023-01-10"),
            ("Coco", "Conejo", "Cabeza de león", "2023-05-05"),
        )
        diego = propietario(
            "Diego Salazar Torres", "43219876", "984444555", "diego.salazar@example.com",
            ("Max", "Perro", "Golden retriever", "2019-06-30"),
        )
        lucia = propietario(
            "Lucía Fernández Ramos", "72345678", "983555666", "lucia.fernandez@example.com",
            ("Simba", "Gato", "Persa", "2021-09-12"),
            ("Toby", "Perro", "Beagle", "2024-02-18"),
        )
        bd.flush()
        luna, michi = rosa.mascotas
        (rocky,) = luis.mascotas
        nala, _coco = carmen.mascotas
        (max_,) = diego.mascotas
        simba, toby = lucia.mascotas

        # --- Medicamentos ----------------------------------------------------------------
        amoxicilina = Medicamento(
            nombre="Amoxicilina 250 mg (tabletas)", precio=Decimal("25.00"), stock=40, vencimiento=hoy + timedelta(days=365)
        )
        meloxicam = Medicamento(
            nombre="Meloxicam 1,5 mg/ml (suspensión)", precio=Decimal("45.00"), stock=15, vencimiento=hoy + timedelta(days=240)
        )
        pipeta = Medicamento(
            nombre="Pipeta antipulgas", precio=Decimal("55.00"), stock=25, vencimiento=hoy + timedelta(days=500)
        )
        desparasitante = Medicamento(
            nombre="Desparasitante interno (tabletas)", precio=Decimal("15.00"), stock=60, vencimiento=hoy + timedelta(days=400)
        )
        bd.add_all([amoxicilina, meloxicam, pipeta, desparasitante])

        # --- Citas pasadas (atendidas) con su consulta -----------------------------------
        def cita_pasada(mascota, veterinario, servicio, inicio, motivo):
            c = Cita(
                mascota=mascota,
                veterinario=veterinario,
                servicio=servicio,
                fecha_hora=inicio,
                fecha_hora_fin=inicio + timedelta(minutes=servicio.duracion_minutos),
                motivo=motivo,
                estado="atendida",
            )
            bd.add(c)
            return c

        c1 = cita_pasada(luna, patricia, consulta, en(anterior(MARTES, hoy, 3), "09:00"), "Otitis")
        c2 = cita_pasada(michi, patricia, consulta, en(anterior(JUEVES, hoy, 1), "15:00"), "Control de peso")
        c3 = cita_pasada(rocky, andres, consulta, en(anterior(VIERNES, hoy, 2), "10:00"), "Cojera")
        bd.flush()

        consulta_luna = Consulta(
            cita_id=c1.id, mascota_id=luna.id, veterinario_id=patricia.id, fecha=c1.fecha_hora,
            peso=Decimal("28.4"), diagnostico="Otitis externa leve", tratamiento="Limpieza de oídos y antibiótico oral",
        )
        consulta_michi = Consulta(
            cita_id=c2.id, mascota_id=michi.id, veterinario_id=patricia.id, fecha=c2.fecha_hora,
            peso=Decimal("4.9"), diagnostico="Sobrepeso leve", tratamiento="Ajuste de dieta y control en 3 meses",
        )
        consulta_rocky = Consulta(
            cita_id=c3.id, mascota_id=rocky.id, veterinario_id=andres.id, fecha=c3.fecha_hora,
            peso=Decimal("12.1"), diagnostico="Distensión muscular", tratamiento="Reposo y antiinflamatorio",
        )
        bd.add_all([consulta_luna, consulta_michi, consulta_rocky])
        bd.flush()
        bd.add_all(
            [
                DetalleReceta(consulta_id=consulta_luna.id, medicamento_id=amoxicilina.id, dosis="1 tableta", frecuencia="Cada 12 horas por 7 días"),
                DetalleReceta(consulta_id=consulta_rocky.id, medicamento_id=meloxicam.id, dosis="Según peso", frecuencia="Cada 24 horas por 5 días"),
            ]
        )

        # --- Factura de la consulta de Luna ----------------------------------------------
        factura = Factura(propietario_id=rosa.id, fecha=c1.fecha_hora_fin, total=Decimal("95.00"))
        bd.add(factura)
        bd.flush()
        bd.add_all(
            [
                DetalleFactura(factura_id=factura.id, servicio_id=consulta.id, cantidad=1, subtotal=Decimal("70.00")),
                DetalleFactura(factura_id=factura.id, medicamento_id=amoxicilina.id, cantidad=1, subtotal=Decimal("25.00")),
            ]
        )

        # --- Vacunas -----------------------------------------------------------------------
        def vacuna(mascota, nombre, hace_dias, proxima_en_dias):
            bd.add(
                Vacunacion(
                    mascota_id=mascota.id,
                    vacuna=nombre,
                    fecha_aplicacion=hoy - timedelta(days=hace_dias),
                    proxima_dosis=hoy + timedelta(days=proxima_en_dias) if proxima_en_dias is not None else None,
                )
            )

        vacuna(luna, "Rabia", 335, 30)
        vacuna(luna, "Polivalente (DHPPi+L)", 60, 305)
        vacuna(michi, "Triple felina", 395, -30)  # atrasada
        vacuna(rocky, "Rabia", 180, 185)
        vacuna(nala, "Triple felina", 360, (proximo(JUEVES, hoy) - hoy).days)
        vacuna(max_, "Rabia", 60, 305)
        vacuna(max_, "Polivalente (DHPPi+L)", 345, 20)
        vacuna(simba, "Leucemia felina", 200, 165)
        vacuna(toby, "Polivalente cachorro (2.ª dosis)", 21, 7)
        bd.flush()

    # --- Citas futuras: se crean con la misma lógica del bot, así quedan validadas ------------
    def agendar(mascota, veterinario, servicio, inicio, motivo):
        with transaccion() as bd:
            return agenda.agendar_cita(
                bd,
                propietario_id=bd.get(Mascota, mascota.id).propietario_id,
                mascota_id=mascota.id,
                servicio_id=servicio.id,
                veterinario_id=veterinario.id,
                fecha_hora=inicio,
                motivo=motivo,
                ahora=ahora,
            )

    martes = proximo(MARTES, hoy)
    agendar(luna, patricia, consulta, en(martes, "09:00"), "Control anual")
    agendar(max_, patricia, consulta, en(martes, "09:30"), "Revisión de piel")
    agendar(rocky, andres, limpieza, en(proximo(MIERCOLES, hoy), "09:00"), "Sarro dental")
    agendar(nala, camila, vacunacion, en(proximo(JUEVES, hoy), "10:00"), "Refuerzo triple felina")
    agendar(simba, camila, bano, en(proximo(SABADO, hoy), "08:00"), "Baño y corte")
    cancelada = agendar(toby, patricia, consulta, en(martes, "10:00"), "Diarrea")
    with transaccion() as bd:
        agenda.cancelar_cita(bd, propietario_id=lucia.id, cita_id=cancelada["cita_id"], ahora=ahora)


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea la base de datos y carga datos de prueba.")
    parser.add_argument("--reiniciar", action="store_true", help="Borra todas las tablas antes de cargar los datos.")
    args = parser.parse_args()

    engine = configurar_bd(ajustes.database_url)
    if args.reiniciar:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with transaccion() as bd:
        if bd.scalar(select(func.count()).select_from(Propietario)):
            print("La base ya tiene datos. Usa --reiniciar para borrarla y volver a cargarla.")
            return 0

    cargar_datos()

    with transaccion() as bd:
        conteos = {
            modelo.__tablename__: bd.scalar(select(func.count()).select_from(modelo))
            for modelo in (Veterinario, HorarioVeterinario, Servicio, Propietario, Mascota, Cita, Vacunacion)
        }
    print(f"Base creada en {engine.url.render_as_string(hide_password=True)}")
    for tabla, total in conteos.items():
        print(f"  {tabla:<20} {total}")
    print("Celulares de prueba: 987111222, 986222333, 985333444, 984444555, 983555666")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
