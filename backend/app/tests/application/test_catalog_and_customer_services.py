"""FASE-3-CONTRATO §1.1 (coherencia modalidad ↔ precio) y §1.2 (resolución por clave
normalizada). Datos sintéticos: ningún teléfono ni patente sale del spec."""

import uuid
from datetime import time

import pytest
import pytest_asyncio

from app.application.services import CatalogService, check_price_coherence
from app.application.services.errors import (
    AlreadyExistsError,
    CatalogIncoherentError,
    NotFoundError,
)
from app.domain.enums import Channel, PlateFormat, PricingMode
from app.domain.exceptions import GuardFailedError, InvalidAmountError, InvalidPlateError
from app.domain.phone import PhoneReason
from app.persistence.repositories.catalog import BusinessHoursRepository
from app.persistence.repositories.jobs import JobEventRepository
from app.tests.application._armado import PRECIO_FIJO, Lavadero, armar


@pytest_asyncio.fixture
async def lav(db_session) -> Lavadero:
    return await armar(db_session)


# ── Catálogo ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("modo", "precio", "bps", "error"),
    [
        (PricingMode.PRECIO_FIJO, 100, 0, None),
        (PricingMode.PRECIO_FIJO, 100, 5000, None),
        (PricingMode.PRECIO_FIJO, None, 0, CatalogIncoherentError),
        (PricingMode.A_COTIZAR, None, 0, None),
        (PricingMode.A_COTIZAR, 100, 0, CatalogIncoherentError),
        (PricingMode.A_COTIZAR, None, 1000, CatalogIncoherentError),
        (PricingMode.PRECIO_FIJO, 0, 0, InvalidAmountError),
        (PricingMode.PRECIO_FIJO, 100, 10_001, InvalidAmountError),
    ],
)
def test_coherencia_modalidad_precio(modo, precio, bps, error):
    if error is None:
        check_price_coherence(modo, precio, bps)
    else:
        with pytest.raises(error):
            check_price_coherence(modo, precio, bps)


async def test_set_price_crea_o_actualiza_y_valida(lav):
    precio = await lav.catalogo.set_price(
        lav.lavado, lav.auto, price_cents=2_500_000, duration_min=75, deposit_bps=1000
    )
    assert (precio.price_cents, precio.duration_min, precio.deposit_bps) == (2_500_000, 75, 1000)
    with pytest.raises(CatalogIncoherentError):  # [corregir] R-T-002: borrar el precio no
        await lav.catalogo.set_price(lav.lavado, lav.auto, price_cents=None, duration_min=60)
    with pytest.raises(InvalidAmountError):
        await lav.catalogo.set_price(lav.lavado, lav.auto, price_cents=1, duration_min=0)
    with pytest.raises(NotFoundError):
        await lav.catalogo.set_price(uuid.uuid4(), lav.auto, price_cents=1, duration_min=1)
    with pytest.raises(NotFoundError):
        await lav.catalogo.set_price(lav.lavado, uuid.uuid4(), price_cents=1, duration_min=1)


async def test_cambiar_la_modalidad_revalida_los_precios(lav):
    with pytest.raises(CatalogIncoherentError):
        await lav.catalogo.change_pricing_mode(lav.lavado, PricingMode.A_COTIZAR)
    servicio = await lav.catalogo.create_service("Pulido", PricingMode.A_COTIZAR, notes="Consultar")
    await lav.catalogo.set_price(servicio.id, lav.auto, price_cents=None, duration_min=240)
    with pytest.raises(CatalogIncoherentError):
        await lav.catalogo.change_pricing_mode(servicio.id, PricingMode.PRECIO_FIJO)
    await lav.catalogo.check_service(lav.lavado)
    sin_precios = await lav.catalogo.create_service("Nuevo", PricingMode.A_COTIZAR)
    cambiado = await lav.catalogo.change_pricing_mode(sin_precios.id, PricingMode.PRECIO_FIJO)
    assert cambiado.pricing_mode == PricingMode.PRECIO_FIJO


async def test_altas_duplicadas_chocan(lav):
    with pytest.raises(AlreadyExistsError):
        await lav.catalogo.create_vehicle_size("AUTO", "Otro auto")
    with pytest.raises(AlreadyExistsError):
        await lav.catalogo.create_service("Lavado completo", PricingMode.PRECIO_FIJO)
    with pytest.raises(AlreadyExistsError):
        await lav.catalogo.create_resource("Puesto 1")
    with pytest.raises(AlreadyExistsError):
        await lav.catalogo.create_payment_method("EFECTIVO", "Efectivo")


async def test_el_mismo_codigo_convive_en_otro_tenant(lav, db_session):
    otro = await armar(db_session, "Otro lavadero")  # repite AUTO, EFECTIVO, Puesto 1…
    assert otro.auto != lav.auto


async def test_franjas_y_medios_de_pago(lav):
    franja = await lav.catalogo.add_business_hours(1, time(9), time(13))
    await lav.catalogo.add_business_hours(1, time(15), time(19))
    pagina = await BusinessHoursRepository(lav.session).list_by_tenant(lav.tenant_id)
    assert pagina.total == 2
    assert franja.weekday == 1
    with pytest.raises(InvalidAmountError):
        await lav.catalogo.add_business_hours(8, time(9), time(13))
    with pytest.raises(InvalidAmountError):
        await lav.catalogo.add_business_hours(2, time(13), time(9))
    with pytest.raises(InvalidAmountError):
        await lav.catalogo.create_payment_method("X", "X", commission_bps=-1)
    medio = await lav.catalogo.find_payment_method("CREDITO")
    assert medio is not None and medio.commission_bps == 500
    assert await lav.catalogo.find_payment_method("NO_EXISTE") is None


async def test_catalogo_de_otro_tenant_no_se_toca(lav, db_session):
    otro = await armar(db_session, "Otro")
    with pytest.raises(NotFoundError):
        await otro.catalogo.set_price(lav.lavado, otro.auto, price_cents=1, duration_min=1)
    with pytest.raises(NotFoundError):
        await CatalogService(db_session, otro.tenant_id, otro.owner_id).check_service(lav.lavado)


# ── Clientes ─────────────────────────────────────────────────────────────────


async def test_mismo_telefono_con_otro_formato_es_el_mismo_cliente(lav):
    uno = await lav.clientes.resolve_by_phone("+54 9 11 4444-5555", name="Otro nombre")
    assert not uno.created
    assert uno.customer.id == lav.cliente
    assert uno.customer.name == "Cliente Uno"  # no se pisa el nombre
    assert uno.phone.e164 == "+5491144445555"


async def test_el_canal_de_origen_se_fija_una_sola_vez(lav):
    nuevo = await lav.clientes.resolve_by_phone("0351 155-1234", name="Sin canal")
    assert nuevo.created and nuevo.customer.acquisition_channel is None
    otra = await lav.clientes.resolve_by_phone("351 155 1234", name="x", channel=Channel.INSTAGRAM)
    assert otra.customer.acquisition_channel == Channel.INSTAGRAM
    tercera = await lav.clientes.resolve_by_phone("3511551234", name="x", channel=Channel.CALLE)
    assert tercera.customer.acquisition_channel == Channel.INSTAGRAM  # R-C-002: no el último


async def test_telefono_ambiguo_no_se_adivina(lav):
    uno = await lav.clientes.resolve_by_phone("54 11 4444 5555", name="Ambiguo")
    dos = await lav.clientes.resolve_by_phone("54 11 4444 5555", name="Ambiguo")
    assert uno.phone.ambiguous and uno.phone.reason == PhoneReason.MISSING_MOBILE_9
    assert uno.created and dos.created and uno.customer.id != dos.customer.id
    assert uno.customer.phone_e164 is None
    assert uno.customer.phone_raw == "54 11 4444 5555"


async def test_sin_telefono_y_nombre_invalido(lav):
    sin = await lav.clientes.resolve_by_phone(
        None, name="  Agenda interna  ", email="x@ejemplo.invalid"
    )
    assert sin.created and sin.customer.phone_raw is None and sin.customer.name == "Agenda interna"
    with pytest.raises(GuardFailedError):
        await lav.clientes.resolve_by_phone("1144445555", name=" ")
    with pytest.raises(GuardFailedError):
        await lav.clientes.resolve_by_phone("1144445555", name="x" * 81)


# ── Vehículos ────────────────────────────────────────────────────────────────


async def test_la_misma_patente_con_otro_formato_es_el_mismo_auto(lav):
    r = await lav.vehiculos.resolve_by_plate("ab-123-cd")
    assert not r.created
    assert r.vehicle.id == lav.vehiculo
    assert r.plate_format == PlateFormat.MERCOSUR


async def test_patente_nueva_vieja_y_rara(lav):
    vieja = await lav.vehiculos.resolve_by_plate("abc 123", brand_model="Sedán", color="Gris")
    assert vieja.created and vieja.plate_format == PlateFormat.AR_1994
    assert vieja.vehicle.plate == "abc 123"
    rara = await lav.vehiculos.resolve_by_plate("X1")
    assert rara.plate_format == PlateFormat.OTRO  # se acepta con advertencia
    with pytest.raises(InvalidPlateError):
        await lav.vehiculos.resolve_by_plate("ABCDEFGHIJK")
    with pytest.raises(GuardFailedError):
        await lav.vehiculos.resolve_by_plate("ZZ999ZZ", brand_model="x" * 41)
    with pytest.raises(NotFoundError):
        await lav.vehiculos.resolve_by_plate("ZZ999ZZ", vehicle_size_id=uuid.uuid4())


async def test_sin_patente_siempre_crea(lav):
    uno = await lav.vehiculos.resolve_by_plate("SIN000")
    dos = await lav.vehiculos.resolve_by_plate("")
    assert uno.created and dos.created and uno.vehicle.id != dos.vehicle.id
    assert uno.vehicle.plate is None and uno.vehicle.plate_normalized is None
    assert uno.plate_format is None


async def test_el_tamano_habitual_se_completa_pero_no_se_pisa(lav):
    nuevo = await lav.vehiculos.resolve_by_plate("CD456EF")
    assert nuevo.vehicle.vehicle_size_id is None
    completado = await lav.vehiculos.resolve_by_plate("CD456EF", vehicle_size_id=lav.suv)
    assert completado.vehicle.vehicle_size_id == lav.suv
    otra = await lav.vehiculos.resolve_by_plate("CD456EF", vehicle_size_id=lav.auto)
    assert otra.vehicle.vehicle_size_id == lav.suv


async def test_vinculo_cliente_vehiculo_idempotente(lav):
    uno = await lav.vehiculos.link_customer(lav.cliente, lav.vehiculo, is_primary=True)
    dos = await lav.vehiculos.link_customer(lav.cliente, lav.vehiculo)
    assert uno.id == dos.id and uno.is_primary
    otro = await lav.clientes.resolve_by_phone("1155556666", name="Familiar")
    familiar = await lav.vehiculos.link_customer(
        otro.customer.id, lav.vehiculo, valid_from=uno.valid_from
    )
    assert familiar.id != uno.id  # N clientes vigentes sobre el mismo auto
    with pytest.raises(NotFoundError):
        await lav.vehiculos.link_customer(uuid.uuid4(), lav.vehiculo)


# ── Repositorio de eventos (lectura) ─────────────────────────────────────────


async def test_repositorio_de_eventos_lista_y_busca(lav):
    job = await lav.walk_in(clave="rec-1")
    repo = JobEventRepository(lav.session)
    pagina = await repo.list_by_tenant(lav.tenant_id)
    assert pagina.total == 1
    evento = pagina.items[0]
    assert (await repo.get_by_id(evento.id, lav.tenant_id)) is evento
    assert await repo.get_by_id(evento.id, uuid.uuid4()) is None
    assert (await repo.get_by_key("rec-1", lav.tenant_id)) is evento
    assert evento.job_id == job.id
    assert job.base_price_cents == PRECIO_FIJO
