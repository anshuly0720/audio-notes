import logging
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import get_settings
from .db import engine
from .api.recordings import router as recordings_router

import asyncio
from .worker import queue as job_queue
from .worker.runner import worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("audio-notes")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("ffmpeg found at: %s", shutil.which("ffmpeg") or "NOT FOUND")
    stop = asyncio.Event()
    task = None
    if settings.embed_worker:
        # Free tier has no separate worker service, so the worker runs inside the API process.
        task = asyncio.create_task(worker_loop(stop))
    yield
    stop.set()
    job_queue.wake.set()
    if task:
        # Progress is checkpointed per part, so we don't wait for the job to finish:
        # cancel now; the job keeps its stale lock and the reaper requeues it on next start.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(title="Audio Notes API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(recordings_router)

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