"""Engine async de SQLAlchemy — una instancia compartida por proceso."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import get_settings

settings = get_settings()

if settings.is_sqlite:
    engine: AsyncEngine = create_async_engine(settings.async_database_url, echo=settings.DEBUG)
else:  # pragma: no cover  # el engine de Postgres se ejercita en los tests `postgres` y en deploy
    engine = create_async_engine(
        settings.async_database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,  # detecta conexiones muertas
        pool_recycle=3600,  # recicla conexiones cada hora
        echo=settings.DEBUG,
    )
