"""Aplicación FastAPI. Ejecutar con:  uvicorn app.main:app --reload"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.canales import web
from app.config import RAIZ_PROYECTO
from app.db.base import crear_tablas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    crear_tablas()  # no borra nada; solo crea las tablas que falten
    yield


app = FastAPI(title="Chatbot Clínica Veterinaria", lifespan=ciclo_de_vida)
app.include_router(web.router)


@app.get("/salud")
def salud() -> dict:
    return {"estado": "ok"}


# La página de chat. Se monta al final para no tapar las rutas de la API.
app.mount("/", StaticFiles(directory=RAIZ_PROYECTO / "static", html=True), name="static")
