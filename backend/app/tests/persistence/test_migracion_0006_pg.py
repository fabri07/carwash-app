"""La migración 0006 sobre datos reales — FASE-4-CONTRATO §3.3 y A6.

Baja a 0005, carga servicios con SQL crudo (el ORM ya es el de 0006), sube y mira lo que quedó:

- `search_key` se rellena con `normalize(name)` (la copia congelada) y queda NOT NULL;
- `category_id` y `glossary_code` nacen NULL; el único de `glossary_code` es por tenant y vivos;
- RLS forzado de nuevo al terminar, también en la tabla nueva;
- el downgrade **corta, diciendo por qué**, si perdería categorías o vínculos, y baja limpio si no.

`finally` deja la base en el head para los tests que siguen (corren en serie, `-n 0`).
"""

import importlib
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.search import normalize
from app.main import head_de_alembic
from app.tests.conftest_pg import migrate_head, owner_url
from app.tests.persistence.test_migracion_0005_pg import _alembic, _ejecutar, _version

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

ANTERIOR = "0005_configuracion_precios"
NOMBRES = ["Limpieza de Tapizados", "Ñandú: Aseo   Premium", "ÁÉÍÓÚ ü 100% vinilo / ploteo", "x"]


@pytest_asyncio.fixture
async def en_0005(
    pg_admin_engine: AsyncEngine, pg_clean: None
) -> AsyncGenerator[AsyncEngine, None]:
    _alembic("downgrade", ANTERIOR)
    try:
        yield pg_admin_engine
    finally:
        async with pg_admin_engine.begin() as conn:
            await conn.execute(text("TRUNCATE tenants CASCADE"))
        migrate_head(owner_url())


async def _tenant(admin: AsyncEngine, nombre: str) -> uuid.UUID:
    """Un negocio como queda después de 0005: con `slug` (NN) y su configuración."""
    tenant_id = uuid.uuid4()
    await _ejecutar(
        admin,
        "INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)",
        id=tenant_id,
        n=nombre,
        s=f"demo-{tenant_id.hex[:8]}",
    )
    return tenant_id


async def _servicio(admin: AsyncEngine, tenant_id: uuid.UUID, nombre: str, **extra: object) -> None:
    columnas = ["id", "tenant_id", "name", "pricing_mode", *extra]
    valores = ", ".join(f":{c}" for c in columnas)
    await _ejecutar(
        admin,
        f"INSERT INTO services ({', '.join(columnas)}) VALUES ({valores})",
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=nombre,
        pricing_mode="PRECIO_FIJO",
        **extra,
    )


async def test_rellena_search_key_con_la_copia_congelada_de_normalize(en_0005: AsyncEngine):
    admin = en_0005
    uno = await _tenant(admin, "Lavadero Demo Uno")
    dos = await _tenant(admin, "Lavadero Demo Dos")
    for nombre in NOMBRES:
        await _servicio(admin, uno, nombre)
    await _servicio(admin, dos, "Pulido")
    # Un servicio anulado también se indexa: reaparece si se restaura.
    await _servicio(
        admin,
        dos,
        "Anulado Viejo",
        voided_at=datetime(2026, 1, 1, tzinfo=UTC),
        void_reason="ERROR_DE_CARGA",
    )

    migrate_head(owner_url())

    async with admin.connect() as conn:
        filas = (await conn.execute(text("SELECT name, search_key FROM services"))).all()
        columnas = {
            f[0]: f[1]
            for f in (
                await conn.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns"
                        " WHERE table_name = 'services'"
                        " AND column_name IN ('search_key', 'category_id', 'glossary_code')"
                    )
                )
            ).all()
        }
        sin_vinculo = await conn.scalar(
            text(
                "SELECT count(*) FROM services WHERE category_id IS NULL AND glossary_code IS NULL"
            )
        )
        forzadas = {
            f[0]: f[1]
            for f in (
                await conn.execute(
                    text(
                        "SELECT relname, relforcerowsecurity FROM pg_class"
                        " WHERE relname IN ('services', 'service_categories')"
                    )
                )
            ).all()
        }
    assert {f[0]: f[1] for f in filas} == {
        "Limpieza de Tapizados": "limpieza de tapizados",
        "Ñandú: Aseo   Premium": "nandu aseo premium",
        "ÁÉÍÓÚ ü 100% vinilo / ploteo": "aeiou u 100 vinilo ploteo",
        "x": "x",
        "Pulido": "pulido",
        "Anulado Viejo": "anulado viejo",
    }
    assert columnas == {"search_key": "NO", "category_id": "YES", "glossary_code": "YES"}
    assert sin_vinculo == len(filas)
    assert forzadas == {"services": True, "service_categories": True}
    assert await _version(admin) == head_de_alembic()


async def test_la_copia_congelada_de_normalize_es_la_de_search_py():
    """Si divergen, los `search_key` ya guardados no coinciden con los que arma el código."""
    migracion = importlib.import_module(
        "app.persistence.migrations.versions.20261105_0006_servicios_categorias"
    )
    for texto in (*NOMBRES, "", "   ", "Tratamiento cerámico", "PPF / film", "Pre-lavado", "ñ"):
        assert migracion._normalize(texto) == normalize(texto), texto


async def test_el_unico_de_glossary_code_es_por_tenant_y_entre_vivos(pg_admin_engine, pg_clean):
    uno = await _tenant(pg_admin_engine, "Lavadero Demo Uno")
    dos = await _tenant(pg_admin_engine, "Lavadero Demo Dos")
    await _servicio(pg_admin_engine, uno, "Pulido", glossary_code="pulido", search_key="pulido")
    # Otro tenant puede usar el mismo código; un servicio propio (NULL) no choca con nadie.
    await _servicio(pg_admin_engine, dos, "Pulido", glossary_code="pulido", search_key="pulido")
    await _servicio(pg_admin_engine, uno, "Propio 1", search_key="propio 1")
    await _servicio(pg_admin_engine, uno, "Propio 2", search_key="propio 2")
    with pytest.raises(Exception, match="ux_services_tenant_id_glossary_code"):
        await _servicio(
            pg_admin_engine, uno, "Otro pulido", glossary_code="pulido", search_key="otro pulido"
        )
    # Anulado, libera el código.
    await _ejecutar(
        pg_admin_engine,
        "UPDATE services SET voided_at = now(), void_reason = 'ERROR_DE_CARGA'"
        " WHERE tenant_id = :t AND glossary_code = 'pulido'",
        t=uno,
    )
    await _servicio(
        pg_admin_engine, uno, "Otro pulido", glossary_code="pulido", search_key="otro pulido"
    )


async def test_el_downgrade_corta_si_perderia_categorias_o_vinculos(pg_admin_engine, pg_clean):
    tenant = await _tenant(pg_admin_engine, "Lavadero Demo Norte")
    categoria = uuid.uuid4()
    await _ejecutar(
        pg_admin_engine,
        "INSERT INTO service_categories (id, tenant_id, name) VALUES (:i, :t, 'Lavado')",
        i=categoria,
        t=tenant,
    )
    await _servicio(
        pg_admin_engine, tenant, "Lavado exterior", category_id=categoria, search_key="lavado"
    )

    with pytest.raises(Exception, match="se perderian datos"):
        _alembic("downgrade", ANTERIOR)
    # El corte no dejó nada a medias: sigue en el head, con la tabla y las columnas.
    assert await _version(pg_admin_engine) == head_de_alembic()
    async with pg_admin_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('service_categories') IS NOT NULL"))

    # Con el vínculo y la categoría fuera, baja limpio; y vuelve a subir.
    await _ejecutar(pg_admin_engine, "UPDATE services SET category_id = NULL")
    with pytest.raises(Exception, match="se perderian datos"):  # la categoría sola también corta
        _alembic("downgrade", ANTERIOR)
    await _ejecutar(pg_admin_engine, "DELETE FROM service_categories")
    try:
        _alembic("downgrade", ANTERIOR)
        assert await _version(pg_admin_engine) == ANTERIOR
        async with pg_admin_engine.connect() as conn:
            assert not await conn.scalar(
                text("SELECT to_regclass('service_categories') IS NOT NULL")
            )
            assert await conn.scalar(text("SELECT count(*) FROM services")) == 1
    finally:
        migrate_head(owner_url())
    assert await _version(pg_admin_engine) == head_de_alembic()
