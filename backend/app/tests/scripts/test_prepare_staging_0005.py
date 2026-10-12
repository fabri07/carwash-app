"""FASE-4-CONTRATO §3.2 — `prepare_staging_0005.py` deja staging sin precios NULL.

Se corre el script de verdad, en un subproceso y como `carwash_app` (RLS forzado), sobre
lo que deja el seed de la Fase 3: el servicio a cotizar sin precio y su turno cotizado
con el ítem sin precio. Los conteos se leen con el superusuario.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.tests.conftest_pg import PG_TEST_URL, _as_app_role
from app.tests.scripts.test_seed_staging import PASSWORD_PRIMERA, _correr_seed

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

BACKEND = Path(__file__).resolve().parents[3]
SCRIPT = BACKEND / "scripts" / "prepare_staging_0005.py"

_NULOS = {
    "service_prices": "SELECT count(*) FROM service_prices WHERE price_cents IS NULL",
    "booking_items": "SELECT count(*) FROM booking_items WHERE price_cents IS NULL",
    "bookings": "SELECT count(*) FROM bookings WHERE price_cents IS NULL",
}


def _correr(app_env: str = "staging") -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "APP_ENV": app_env,
        "DATABASE_URL": _as_app_role(PG_TEST_URL),
        "JWT_SECRET_KEY": "seed-test-secret-key-0123456789-abcdefghij",
    }
    env.pop("DATABASE_URL_SYNC", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


async def _nulos(admin) -> dict[str, int]:
    async with admin.connect() as conn:
        return {t: await conn.scalar(text(sql)) for t, sql in _NULOS.items()}


async def test_completa_los_precios_que_deja_el_seed_y_es_idempotente(pg_admin_engine, pg_clean):
    seed = _correr_seed(PASSWORD_PRIMERA)
    assert seed.returncode == 0, seed.stdout + seed.stderr
    antes = await _nulos(pg_admin_engine)
    assert antes["service_prices"] > 0, "el seed ya no deja precios NULL: este test no prueba nada"
    assert antes["booking_items"] > 0

    primera = _correr()
    assert primera.returncode == 0, primera.stdout + primera.stderr
    assert await _nulos(pg_admin_engine) == dict.fromkeys(_NULOS, 0)

    async with pg_admin_engine.connect() as conn:
        # El ítem del turno cotizado y aceptado toma el precio acordado, no el base.
        acordado = await conn.scalar(
            text(
                "SELECT bi.price_cents FROM booking_items bi JOIN quotes q "
                "ON q.booking_id = bi.booking_id AND q.service_id = bi.service_id "
                "WHERE q.status = 'ACEPTADO' LIMIT 1"
            )
        )
    assert acordado == 4_500_000

    segunda = _correr()
    assert segunda.returncode == 0, segunda.stdout + segunda.stderr
    assert "OK: 0 filas cambiadas" in segunda.stdout


async def test_se_niega_fuera_de_staging(pg_admin_engine, pg_clean):
    seed = _correr_seed(PASSWORD_PRIMERA)
    assert seed.returncode == 0, seed.stdout + seed.stderr
    antes = await _nulos(pg_admin_engine)

    prod = _correr(app_env="production")
    assert prod.returncode == 1
    assert await _nulos(pg_admin_engine) == antes


async def test_sin_tenants_del_seed_no_hace_nada(pg_admin_engine, pg_clean):
    corrida = _correr()
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert "OK: 0 filas cambiadas" in corrida.stdout
