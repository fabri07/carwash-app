"""ADR-0003 — comportamiento de la anulación."""

import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.domain.void import VoidReason
from app.persistence.models import DummyResource
from app.persistence.repositories.dummy_resource_repository import DummyResourceRepository
from app.utils.datetime_utils import utcnow


async def test_la_base_rechaza_el_medio_anulado(db_session, dummy_a):
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(DummyResource)
                .where(DummyResource.id == dummy_a.id)
                .values(voided_at=utcnow())
            )


async def test_lo_anulado_no_aparece_por_defecto(db_session, tenant_a, dummy_a):
    repo = DummyResourceRepository(db_session)
    await repo.void(dummy_a.id, tenant_a.id, VoidReason.PEDIDO_DEL_USUARIO)
    assert dummy_a.id not in {d.id for d in (await repo.list_by_tenant(tenant_a.id)).items}
    con_anulados = await repo.list_by_tenant(tenant_a.id, include_voided=True)
    assert dummy_a.id in {d.id for d in con_anulados.items}
    assert await repo.get_by_id(dummy_a.id, tenant_a.id) is None
    assert await repo.get_by_id(dummy_a.id, tenant_a.id, include_voided=True) is not None


async def test_anular_ajeno_o_inexistente_devuelve_none(db_session, tenant_b, dummy_a):
    repo = DummyResourceRepository(db_session)
    assert await repo.void(dummy_a.id, tenant_b.id, VoidReason.ERROR_DE_CARGA) is None
    assert await repo.void(uuid.uuid4(), tenant_b.id, VoidReason.ERROR_DE_CARGA) is None


async def test_el_repositorio_filtra_por_tenant(db_session, tenant_a, tenant_b, dummy_a):
    repo = DummyResourceRepository(db_session)
    assert await repo.get_by_id(dummy_a.id, tenant_b.id) is None
    page = await repo.list_by_tenant(tenant_b.id)
    assert page.items == [] and page.total == 0
