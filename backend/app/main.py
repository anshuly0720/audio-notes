import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import get_settings
from .db import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("audio-notes")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loudly at startup if ffmpeg is missing: the worker cannot cut audio without it
    log.info("ffmpeg found at: %s", shutil.which("ffmpeg") or "NOT FOUND")
    yield
    await engine.dispose()


app = FastAPI(title="Audio Notes API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """Liveness only. Never touches the DB, so pings don't keep Neon awake."""
    return {"status": "ok"}


@app.get("/api/health/db")
async def health_db():
    """Readiness: proves we can reach Postgres."""
    async with engine.connect() as conn:
        await conn.execute(text("select 1"))
    return {"database": "ok"}