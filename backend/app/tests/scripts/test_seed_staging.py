"""ADR-0012 punto 3 — el seed sintético de staging cubre el esquema y es idempotente.

Lo cita `backend/scripts/seed_staging.py` (de `deploy`): cuando una fase agregue una
tabla con `tenant_id`, el seed la tiene que poblar o este test se pone rojo.

Se corre el script de verdad, en un subproceso, con los roles reales: `carwash_app`
(RLS forzado) como `DATABASE_URL`, contra la base migrada por `carwash_owner`. Los
conteos se leen con el superusuario, que es el único testigo que ve todos los tenants.
"""

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

import app.persistence.models  # noqa: F401
from app.persistence.db.base import Base
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

BACKEND = Path(__file__).resolve().parents[3]
SCRIPT = BACKEND / "scripts" / "seed_staging.py"
PASSWORD_PRIMERA = "staging-password-uno-123"
PASSWORD_SEGUNDA = "staging-password-dos-456"

#: Toda tabla con tenant_id, más `tenants`. Se deriva del ORM: una tabla nueva entra sola.
TABLAS = sorted({t.name for t in Base.metadata.tables.values() if "tenant_id" in t.c} | {"tenants"})


def _correr_seed(password: str, app_env: str = "staging") -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "APP_ENV": app_env,
        "DATABASE_URL": _as_app_role(PG_TEST_URL),
        "SEED_STAGING_PASSWORD": password,
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


async def _conteos(admin) -> dict[str, int]:
    async with admin.connect() as conn:
        return {t: await conn.scalar(text(f"SELECT count(*) FROM {t}")) for t in TABLAS}


async def _hashes(admin) -> dict[str, str]:
    async with admin.connect() as conn:
        filas = (await conn.execute(text("SELECT email, password_hash FROM users"))).all()
    return {f.email: f.password_hash for f in filas}


async def test_el_script_existe():
    assert SCRIPT.exists(), f"falta {SCRIPT} (de `deploy`)"


async def test_seed_puebla_toda_tabla_con_tenant_y_no_duplica(pg_admin_engine, pg_clean):
    primera = _correr_seed(PASSWORD_PRIMERA)
    assert primera.returncode == 0, primera.stdout + primera.stderr
    despues_de_una = await _conteos(pg_admin_engine)
    vacias = [t for t, n in despues_de_una.items() if n == 0]
    assert not vacias, f"el seed no puebla: {vacias}"
    hashes_uno = await _hashes(pg_admin_engine)
    assert all(email.endswith(".invalid") for email in hashes_uno), "emails no sintéticos"

    segunda = _correr_seed(PASSWORD_SEGUNDA)
    assert segunda.returncode == 0, segunda.stdout + segunda.stderr
    assert await _conteos(pg_admin_engine) == despues_de_una, "correrlo dos veces duplicó filas"
    # idempotente pero no congelado: la contraseña se re-aplica
    hashes_dos = await _hashes(pg_admin_engine)
    assert hashes_dos.keys() == hashes_uno.keys()
    assert all(hashes_dos[e] != hashes_uno[e] for e in hashes_uno)


async def test_seed_usa_telefonos_y_patentes_inconfundiblemente_falsos(pg_admin_engine, pg_clean):
    """F11 de T3: `11 0000-00xx` y `ZZ0xxZZ` no son de nadie ni se emitirán en décadas; los
    datos de staging no pueden confundirse con un cliente o un auto reales."""
    corrida = _correr_seed(PASSWORD_PRIMERA)
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    async with pg_admin_engine.connect() as conn:
        telefonos = (await conn.scalars(text("SELECT phone_e164 FROM customers"))).all()
        patentes = (await conn.scalars(text("SELECT plate_normalized FROM vehicles"))).all()
    assert telefonos and all(re.fullmatch(r"\+549110000\d{4}", t) for t in telefonos), telefonos
    assert patentes and all(re.fullmatch(r"ZZ\d{3}ZZ", p) for p in patentes), patentes


async def test_seed_se_niega_fuera_de_staging(pg_admin_engine, pg_clean):
    prod = _correr_seed(PASSWORD_PRIMERA, app_env="production")
    assert prod.returncode == 1
    assert all(n == 0 for n in (await _conteos(pg_admin_engine)).values())


async def test_seed_exige_password(pg_admin_engine, pg_clean):
    corta = _correr_seed("corta")
    assert corta.returncode == 1
    assert all(n == 0 for n in (await _conteos(pg_admin_engine)).values())


async def test_seed_completa_el_dominio_de_un_staging_sembrado_en_fase_2(pg_admin_engine, pg_clean):
    """Staging ya tiene los tenants de la Fase 2 (usuarios y dummies, sin dominio): re-correr
    el seed les agrega el lavadero sin duplicar lo que ya estaba (A9)."""
    from app.utils.security import hash_password  # noqa: PLC0415

    sys.path.insert(0, str(BACKEND / "scripts"))
    import seed_staging  # noqa: PLC0415

    async with pg_admin_engine.begin() as conn:
        for spec in seed_staging.TENANTS:
            tenant_id = uuid.uuid4()
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
                {"id": tenant_id, "name": spec["name"]},
            )
            for email, role in ((spec["owner"], "OWNER"), (spec["staff"], "STAFF")):
                await conn.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, password_hash, role) "
                        "VALUES (:id, :t, :email, :hash, :role)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "t": tenant_id,
                        "email": email,
                        "hash": hash_password("x"),
                        "role": role,
                    },
                )

    corrida = _correr_seed(PASSWORD_PRIMERA)
    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert "dominio sembrado" in corrida.stdout
    conteos = await _conteos(pg_admin_engine)
    assert conteos["tenants"] == len(seed_staging.TENANTS)
    vacias = [
        t for t, n in conteos.items() if n == 0 and t not in {"dummy_resources", "idempotency_keys"}
    ]
    assert not vacias, f"el seed no completó: {vacias}"
    async with pg_admin_engine.connect() as conn:
        por_tenant = await conn.scalar(text("SELECT count(DISTINCT tenant_id) FROM job_events"))
    assert por_tenant == len(seed_staging.TENANTS)
