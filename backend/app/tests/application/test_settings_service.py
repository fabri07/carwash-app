"""Configuración del negocio y alta por etapas — FASE-4-CONTRATO §3.2, Y3 y A6 (PR 4.2)."""

import uuid

import pytest

from app.application.services.errors import AlreadyExistsError, NotFoundError
from app.application.services.settings_service import SettingsService, load_settings
from app.application.services.tenant_provisioning import provision_tenant
from app.persistence.models.tenant import (
    DEFAULT_DEPOSIT_BPS,
    DEFAULT_HOLD_MIN,
    DEFAULT_LATE_CANCEL_THRESHOLD_MIN,
    DEFAULT_MIN_ADVANCE_MIN,
    DEFAULT_TOLERANCE_MIN,
)


async def _alta(session, nombre="Lavadero Ñandú", email=None):
    return await provision_tenant(
        session,
        name=nombre,
        owner_email=email or f"duenio-{uuid.uuid4().hex[:8]}@ejemplo.invalid",
        owner_password_hash="no-es-un-hash",
    )


async def test_el_alta_crea_slug_y_configuracion_con_los_defaults(db_session):
    alta = await _alta(db_session)
    assert alta.tenant.slug == "lavadero-nandu"
    ajustes = await load_settings(db_session, alta.tenant.id)
    assert ajustes.deposit_enabled is False
    assert (
        ajustes.deposit_bps,
        ajustes.tolerance_min,
        ajustes.late_cancel_threshold_min,
        ajustes.hold_min,
        ajustes.min_advance_min,
    ) == (
        DEFAULT_DEPOSIT_BPS,
        DEFAULT_TOLERANCE_MIN,
        DEFAULT_LATE_CANCEL_THRESHOLD_MIN,
        DEFAULT_HOLD_MIN,
        DEFAULT_MIN_ADVANCE_MIN,
    )
    assert ajustes.effective_deposit_bps == 0
    assert ajustes.onboarding_completed_at is None


async def test_dos_negocios_con_el_mismo_nombre_tienen_slugs_distintos(db_session):
    a = await _alta(db_session, "Lavadero Centro")
    b = await _alta(db_session, "Lavadero Centro")
    assert a.tenant.slug == "lavadero-centro"
    assert b.tenant.slug.startswith("lavadero-centro-")
    assert a.tenant.slug != b.tenant.slug


async def test_actualizar_es_parcial_y_conserva_el_porcentaje_con_la_sena_apagada(db_session):
    alta = await _alta(db_session)
    servicio = SettingsService(db_session, alta.tenant.id, alta.owner.id)
    await servicio.update({"deposit_enabled": True, "deposit_bps": 2500, "address": "Calle 1"})
    negocio = await servicio.update({"deposit_enabled": False})
    assert negocio.settings.deposit_bps == 2500
    assert negocio.settings.address == "Calle 1"
    assert negocio.settings.effective_deposit_bps == 0
    negocio = await servicio.update({"address": None})
    assert negocio.settings.address is None


async def test_cambiar_el_slug_a_uno_tomado_es_already_exists(db_session):
    a = await _alta(db_session, "Lavadero Uno")
    b = await _alta(db_session, "Lavadero Dos")
    servicio = SettingsService(db_session, b.tenant.id, b.owner.id)
    with pytest.raises(AlreadyExistsError):
        await servicio.update({}, slug=a.tenant.slug)
    negocio = await servicio.update({}, slug="lavadero-dos-centro")
    assert negocio.tenant.slug == "lavadero-dos-centro"


async def test_un_campo_no_editable_se_rechaza(db_session):
    alta = await _alta(db_session)
    with pytest.raises(ValueError, match="not editable"):
        await SettingsService(db_session, alta.tenant.id, alta.owner.id).update({"logo_key": "x"})


async def test_sin_fila_de_configuracion_es_not_found(db_session):
    with pytest.raises(NotFoundError):
        await load_settings(db_session, uuid.uuid4())
