"""Catálogo de vehículos en la capa de aplicación — FASE-4-CONTRATO Y3, A6 y A10 (PR 4.4).

- `provision_tenant` copia la base entera (Y3, A6) y dos negocios no comparten filas;
- elegir un modelo sugiere el tamaño por `body_type_sizes` (A10) y una marca agregada por A no
  aparece en B;
- `VehicleCatalogService` lleva el tenant del constructor, nunca de un argumento.

SQLite, datos sintéticos.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.application.services.catalog_service import CatalogService
from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.application.services.tenant_provisioning import provision_tenant
from app.application.services.vehicle_catalog_service import VehicleCatalogService
from app.domain.exceptions import InvalidAmountError
from app.domain.vehicle_catalog import VehicleBodyType, load_vehicle_base
from app.persistence.models import VehicleColor, VehicleMake, VehicleModel

BASE = load_vehicle_base()


async def _alta(session, nombre: str):
    return await provision_tenant(
        session,
        name=nombre,
        owner_email=f"{uuid.uuid4().hex[:8]}@example.com",
        owner_password_hash="no-es-un-hash",
    )


async def _cuenta(session, modelo, tenant_id) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(modelo).where(modelo.tenant_id == tenant_id)
        )
        or 0
    )


async def test_provision_copia_la_base_y_cada_negocio_tiene_la_suya(db_session):
    a = await _alta(db_session, "Lavadero Sintético A")
    b = await _alta(db_session, "Lavadero Sintético B")
    for alta in (a, b):
        tid = alta.tenant.id
        assert await _cuenta(db_session, VehicleMake, tid) == len(BASE.makes)
        assert await _cuenta(db_session, VehicleModel, tid) == BASE.model_count
        assert await _cuenta(db_session, VehicleColor, tid) == len(BASE.colors)
        assert (alta.vehicle_base.makes, alta.vehicle_base.models, alta.vehicle_base.colors) == (
            len(BASE.makes),
            BASE.model_count,
            len(BASE.colors),
        )
    ids_a = set(
        await db_session.scalars(select(VehicleMake.id).where(VehicleMake.tenant_id == a.tenant.id))
    )
    ids_b = set(
        await db_session.scalars(select(VehicleMake.id).where(VehicleMake.tenant_id == b.tenant.id))
    )
    assert ids_a and ids_b and not ids_a & ids_b
    # Cada modelo cuelga de una marca de su propio negocio.
    cruzados = await db_session.scalar(
        select(func.count())
        .select_from(VehicleModel)
        .join(VehicleMake, VehicleMake.id == VehicleModel.make_id)
        .where(VehicleMake.tenant_id != VehicleModel.tenant_id)
    )
    assert cruzados == 0


async def test_elegir_modelo_sugiere_el_tamano_y_lo_de_a_no_aparece_en_b(db_session):
    a = await _alta(db_session, "Lavadero Sintético C")
    b = await _alta(db_session, "Lavadero Sintético D")
    tamano = await CatalogService(db_session, a.tenant.id, a.owner.id).create_vehicle_size(
        "PICKUP", "Pickup"
    )
    servicio_a = VehicleCatalogService(db_session, a.tenant.id, a.owner.id)
    servicio_b = VehicleCatalogService(db_session, b.tenant.id, b.owner.id)
    await servicio_a.replace_body_type_sizes([(VehicleBodyType.PICKUP, tamano.id)])

    vistas, total = await servicio_a.list_models(limit=10, offset=0, q="amarok")
    assert total == 1 and vistas[0].suggested_vehicle_size_id == tamano.id
    assert vistas[0].make_name == "Volkswagen"
    vistas_b, _ = await servicio_b.list_models(limit=10, offset=0, q="amarok")
    assert vistas_b[0].suggested_vehicle_size_id is None

    propia = await servicio_a.create_make("Marca Sintética")
    assert propia.is_custom
    assert (await servicio_b.list_makes(limit=50, offset=0, q="sintetica")).total == 0
    with pytest.raises(NotFoundError):
        await servicio_b.get_make(propia.id)
    with pytest.raises(NotFoundError):
        await servicio_b.create_model(propia.id, "Intruso", VehicleBodyType.SUV)
    # B no mapea su carrocería a un tamaño de A.
    with pytest.raises(InvalidAmountError):
        await servicio_b.replace_body_type_sizes([(VehicleBodyType.PICKUP, tamano.id)])


async def test_reglas_de_unicidad_y_del_mapa(db_session):
    a = await _alta(db_session, "Lavadero Sintético E")
    servicio = VehicleCatalogService(db_session, a.tenant.id, a.owner.id)
    with pytest.raises(AlreadyExistsError):
        await servicio.create_make("Peugeot")
    with pytest.raises(AlreadyExistsError):
        await servicio.create_color("Plata")
    with pytest.raises(InvalidAmountError):
        await servicio.replace_body_type_sizes(
            [(VehicleBodyType.SUV, None), (VehicleBodyType.SUV, None)]
        )
    tabla = await servicio.list_body_type_sizes()
    assert [v.body_type for v in tabla] == list(VehicleBodyType)
