from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """All ORM models inherit from this; Alembic reads Base.metadata."""


engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,  # Neon sleeps after 5 min idle; ping drops dead connections first
    pool_size=5,
    max_overflow=5,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session():
    """FastAPI dependency: one session per request, always closed."""
    async with SessionLocal() as session:
        yield session