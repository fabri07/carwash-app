"""El chequeo de esquema de `/ready` funciona con el ROL DE RUNTIME, no con el dueño.

Por qué existe este archivo. `/ready` compara la revisión aplicada contra el head del
código para que un deploy sin migrar salga en rojo, y corre esa consulta sobre el
engine de runtime, o sea `carwash_app`. Ese rol NO tiene privilegios por default sobre
ninguna tabla: `create_roles.sh` los declina a propósito, para que una tabla nueva no
quede legible sin que alguien lo decida. `alembic_version` caía en esa bolsa.

Resultado sin el GRANT de la migración 0002: `SELECT version_num FROM alembic_version`
tira `InsufficientPrivilege`, el `except` genérico lo convierte en `schema.ok = false`,
`/ready` queda en 503 PARA SIEMPRE y el smoke deja todos los deploys en rojo — además
culpando al Pre-Deploy Command, que es el único lugar donde el problema no está.

Los tests del resto de la suite parchean `_check_schema_ready`, así que ninguno toca
este camino: sin Postgres real y sin los roles de verdad, el bug es invisible. Por eso
va acá y con el marcador `postgres`.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.main import _check_schema_ready, head_de_alembic
from app.persistence.db.rls import APP_ROLE

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]


async def test_el_rol_de_runtime_puede_leer_la_revision_aplicada(pg_engine: AsyncEngine) -> None:
    """`pg_engine` es `carwash_app`: el mismo rol con el que `/ready` corre en Railway."""
    async with pg_engine.connect() as conn:
        aplicado = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    assert aplicado == head_de_alembic(), (
        "la base de tests no está en el head; si en cambio falló con InsufficientPrivilege, "
        f"le falta a {APP_ROLE} el GRANT SELECT de la migración 0002"
    )


async def test_el_rol_de_runtime_no_puede_escribir_la_revision(pg_engine: AsyncEngine) -> None:
    """El GRANT es de SOLO lectura: el runtime no puede mentir sobre qué esquema tiene."""
    from sqlalchemy.exc import DBAPIError  # noqa: PLC0415

    with pytest.raises(DBAPIError) as exc:
        async with pg_engine.begin() as conn:
            await conn.execute(text("UPDATE alembic_version SET version_num = 'mentira'"))
    assert "permission denied" in str(exc.value).lower(), exc.value


async def test_ready_da_verde_contra_una_base_migrada_de_verdad(
    pg_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El chequeo entero, sin parches, contra Postgres real y el rol de runtime."""
    monkeypatch.setattr("app.persistence.db.engine.engine", pg_engine)
    resultado = await _check_schema_ready()
    assert resultado.ok, resultado.error
