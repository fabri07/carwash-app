"""Unidades portadas: alembic_url, datetime_utils, settings, redis, logger."""

from datetime import date

import pytest

from app.config.settings import Settings
from app.persistence.db import redis as redis_mod
from app.persistence.db.alembic_url import describe_target, resolve_sync_url
from app.utils import datetime_utils as dt


def test_resolve_sync_url_prioridad():
    assert resolve_sync_url("ini", env={"DATABASE_URL_SYNC": "s", "DATABASE_URL": "d"}).source == (
        "DATABASE_URL_SYNC"
    )
    url = resolve_sync_url(
        "ini", env={"DATABASE_URL": "postgresql://u:p@h:5432/db?channel_binding=require"}
    )
    assert url == ("postgresql+psycopg2://u:p@h:5432/db", "DATABASE_URL")
    assert resolve_sync_url("ini", env={}).source == "alembic.ini"
    assert resolve_sync_url(None).url is not None


def test_describe_target_no_filtra_credenciales():
    assert describe_target("postgresql://u:secreto@db.host:5432/carwash") == "db.host:5432/carwash"
    assert describe_target("sqlite://") == "(sin host)/(sin base)"


def test_datetime_utils():
    assert dt.utcnow().tzinfo is not None
    assert dt.start_of_month(date(2026, 2, 17)) == date(2026, 2, 1)
    assert dt.end_of_month(date(2026, 2, 17)) == date(2026, 2, 28)
    assert dt.start_of_week(date(2026, 9, 16)) == date(2026, 9, 14)
    assert len(dt.date_range(date(2026, 1, 1), date(2026, 1, 3))) == 3
    assert dt.days_between(date(2026, 1, 3), date(2026, 1, 1)) == 0


def test_settings_cors_csv_y_json_y_url_async(monkeypatch):
    assert Settings(CORS_ORIGINS="https://a, https://b").CORS_ORIGINS == ["https://a", "https://b"]
    assert Settings(CORS_ORIGINS='["https://a"]').CORS_ORIGINS == ["https://a"]
    s = Settings(DATABASE_URL="postgresql://u:p@h/db")
    assert s.async_database_url == "postgresql+asyncpg://u:p@h/db"
    assert Settings(DATABASE_URL="postgres://h/db").async_database_url.startswith(
        "postgresql+asyncpg://"
    )
    assert Settings(APP_ENV="local").cookie_secure is False


async def test_redis_pool_unico_y_cierre(monkeypatch):
    await redis_mod.close_redis_pool()
    a = await redis_mod.get_redis()
    b = await redis_mod.get_redis()
    assert a.connection_pool is b.connection_pool
    await redis_mod.close_redis_pool()
    assert redis_mod._pool is None


def test_log_job_ok_y_error():
    from app.observability.logger import bind_request_context, log_job

    bind_request_context(tenant_id="t", user_id="u", trace_id="tr")
    with log_job("jobs.test", tenant_id="t") as log:
        log.info("adentro")
    with pytest.raises(ValueError), log_job("jobs.falla"):
        raise ValueError("x")
