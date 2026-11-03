from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import router
from app.api.e2e import router as e2e_router
from app.api.model_lab import router as model_lab_router
from app.api.packs import router as packs_router
from app.api.reports import router as reports_router
from app.config import settings
from app.db import engine

app = FastAPI(
    title="Aftertime Platform API",
    version="0.4.0-rc.6",
    description="Physical world model, simulation and planning API",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(model_lab_router)
app.include_router(e2e_router)
app.include_router(reports_router)
app.include_router(packs_router)


@app.get("/health")
def health() -> dict:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok", "service": "aftertime-api", "environment": settings.environment}
