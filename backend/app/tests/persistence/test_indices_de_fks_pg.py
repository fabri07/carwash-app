"""B6 · FASE-3-CONTRATO, X5 — toda FK tiene un índice que la cubre.

Postgres no indexa las FKs por su cuenta. Sin índice, cada `JOIN` por la FK y cada
chequeo de `RESTRICT` sobre el padre recorre la tabla hija entera. Se mira el
catálogo de la base migrada, no el ORM: lo que importa es lo que quedó creado.

"Cubre" = las primeras N columnas de algún índice son exactamente las N columnas de
la FK, en cualquier orden. Un índice `(tenant_id, customer_id)` cubre la FK
compuesta `(tenant_id, customer_id)`; un `(customer_id, tenant_id, created_at)` también.
"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

FKS_SIN_INDICE = text(
    """
    SELECT c.conrelid::regclass::text AS tabla,
           c.conname AS fk,
           (SELECT array_agg(a.attname ORDER BY a.attname)
              FROM pg_attribute a
             WHERE a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)) AS columnas
      FROM pg_constraint c
      JOIN pg_namespace n ON n.oid = c.connamespace
     WHERE c.contype = 'f'
       AND n.nspname = 'public'
       AND NOT EXISTS (
           SELECT 1
             FROM pg_index i
            WHERE i.indrelid = c.conrelid
              AND i.indnatts >= cardinality(c.conkey)
              AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] @> c.conkey
              AND c.conkey @> (i.indkey::int2[])[0:cardinality(c.conkey) - 1]
       )
     ORDER BY 1, 2
    """
)


async def test_toda_fk_tiene_indice(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        faltan = (await conn.execute(FKS_SIN_INDICE)).all()
    assert not faltan, "FKs sin índice que las cubra: " + "; ".join(
        f"{f.tabla}.{f.fk} ({', '.join(f.columnas)})" for f in faltan
    )


async def test_el_detector_ve_una_fk_sin_indice(pg_admin_engine):
    # El test de arriba en verde no dice nada si la consulta nunca encuentra nada.
    async with pg_admin_engine.begin() as conn:
        await conn.execute(text("CREATE TEMP TABLE padre_tmp (id int PRIMARY KEY)"))
        await conn.execute(
            text(
                "CREATE TEMP TABLE hija_tmp (id int PRIMARY KEY, padre_id int REFERENCES padre_tmp)"
            )
        )
        consulta = text(
            str(FKS_SIN_INDICE).replace("n.nspname = 'public'", "n.nspname LIKE 'pg_temp%'")
        )
        faltan = (await conn.execute(consulta)).all()
        assert [f.columnas for f in faltan] == [["padre_id"]]
        await conn.execute(text("CREATE INDEX ON hija_tmp (padre_id)"))
        assert (await conn.execute(consulta)).all() == []
