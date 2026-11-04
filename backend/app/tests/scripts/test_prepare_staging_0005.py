"""PR 4.2.0 — `prepare_staging_0005.py` deja a staging listo para la migración 0005.

La 0005 exige `price_cents NOT NULL` y corta si queda algún NULL. Acá se corre el seed
real (que los deja a propósito), después el script, y se comprueba que:

- no queda ningún NULL, y los ítems con cotización aceptada toman el precio acordado;
- es idempotente: una segunda corrida no cambia ningún valor;
- se niega fuera de staging, sin tocar la base.

Como en `test_seed_staging.py`: subproceso real, rol `carwash_app` con RLS forzado, y el
superusuario como único testigo que ve todos los tenants.
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

_NULOS = """
    SELECT (SELECT count(*) FROM service_prices WHERE price_cents IS NULL) AS precios,
           (SELECT count(*) FROM booking_items WHERE price_cents IS NULL) AS items
"""


def _correr_prepare(app_env: str = "staging", *extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "APP_ENV": app_env,
        "DATABASE_URL": _as_app_role(PG_TEST_URL),
        "JWT_SECRET_KEY": "seed-test-secret-key-0123456789-abcdefghij",
    }
    env.pop("DATABASE_URL_SYNC", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


async def _nulos(admin) -> tuple[int, int]:
    async with admin.connect() as conn:
        fila = (await conn.execute(text(_NULOS))).one()
    return fila.precios, fila.items


async def _foto(admin) -> list[tuple]:
    async with admin.connect() as conn:
        precios = (
            await conn.execute(text("SELECT id, price_cents FROM service_prices ORDER BY id"))
        ).all()
        items = (
            await conn.execute(text("SELECT id, price_cents FROM booking_items ORDER BY id"))
        ).all()
    return [tuple(f) for f in precios] + [tuple(f) for f in items]


async def test_el_script_existe():
    assert SCRIPT.exists(), f"falta {SCRIPT}"


async def test_completa_los_precios_nulos_del_seed_y_es_idempotente(pg_admin_engine, pg_clean):
    sembrado = _correr_seed(PASSWORD_PRIMERA)
    assert sembrado.returncode == 0, sembrado.stdout + sembrado.stderr
    precios_antes, items_antes = await _nulos(pg_admin_engine)
    # Si el seed deja de generar NULLs el test no prueba nada: que lo diga.
    assert precios_antes > 0, "el seed ya no deja service_prices NULL: este script sobra"
    assert items_antes > 0, "el seed ya no deja booking_items NULL: este script sobra"

    primera = _correr_prepare()
    assert primera.returncode == 0, primera.stdout + primera.stderr
    assert "OK" in primera.stdout
    assert await _nulos(pg_admin_engine) == (0, 0)

    # El ítem de un turno con cotización aceptada toma el precio ACORDADO, no el base.
    async with pg_admin_engine.connect() as conn:
        cruce = (
            await conn.execute(
                text(
                    """
                    SELECT count(*) AS con_cotizacion,
                           count(*) FILTER (WHERE bi.price_cents <> q.agreed_price_cents)
                               AS desparejos
                      FROM booking_items bi
                      JOIN quotes q ON q.booking_id = bi.booking_id
                                   AND q.service_id = bi.service_id
                                   AND q.status = 'ACEPTADO'
                     WHERE q.agreed_price_cents IS NOT NULL
                    """
                )
            )
        ).one()
    assert cruce.con_cotizacion > 0, "el seed no tiene ítems con cotización aceptada: no se probó"
    assert cruce.desparejos == 0, "un ítem con cotización aceptada no tomó el precio acordado"

    foto = await _foto(pg_admin_engine)
    segunda = _correr_prepare()
    assert segunda.returncode == 0, segunda.stdout + segunda.stderr
    assert "0 precios base y 0 ítems completados" in segunda.stdout
    assert await _foto(pg_admin_engine) == foto, "la segunda corrida cambió datos"


@pytest.mark.parametrize(("app_env", "codigo"), [("production", 1), ("development", 0), ("", 0)])
async def test_se_niega_fuera_de_staging_y_no_toca_la_base(
    pg_admin_engine, pg_clean, app_env, codigo
):
    sembrado = _correr_seed(PASSWORD_PRIMERA)
    assert sembrado.returncode == 0, sembrado.stdout + sembrado.stderr
    antes = await _nulos(pg_admin_engine)

    corrida = _correr_prepare(app_env)
    assert corrida.returncode == codigo, corrida.stdout + corrida.stderr
    assert "solo corre en staging" in corrida.stdout
    assert await _nulos(pg_admin_engine) == antes


async def test_un_dueño_inexistente_se_omite_sin_romper(pg_admin_engine, pg_clean):
    sembrado = _correr_seed(PASSWORD_PRIMERA)
    assert sembrado.returncode == 0, sembrado.stdout + sembrado.stderr

    corrida = _correr_prepare("staging", "nadie@no-existe.invalid")
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert "no existe ese dueño" in corrida.stdout
    assert await _nulos(pg_admin_engine) == (0, 0)
