"""Turnero público (PR 5.2) — FASE-5-CONTRATO §4.2, §6, B7 y B9.

`/v1/public/{slug}/…` sin sesión, sobre SQLite (la suite rápida). Lo que necesita el `EXCLUDE` y
la carrera real está en `tests/api/test_turnero_publico_pg.py`.

Datos sintéticos: teléfonos `+54 9 351 000-xxxx`, patentes `AA000AA`, emails `@example.com`.
"""

import hashlib
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.api.rate_limit import limiter
from app.domain.booking_code import CODE_ALPHABET, CODE_PREFIX
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    Channel,
    ServiceBookingMode,
)
from app.domain.roles import Role
from app.domain.void import VoidReason
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models import Booking, Customer, Service, TenantSettings
from app.persistence.models.agenda import CustomerIncidentMark
from app.persistence.models.customer import CustomerVehicle, Vehicle
from app.persistence.models.tenant import Tenant
from app.tests.application._armado import (
    PRECIO_BASE_COTIZAR,
    PRECIO_CON_SENA,
    PRECIO_FIJO,
    Lavadero,
    armar_catalogo,
)
from app.tests.conftest import make_tenant, make_user

TZ = ZoneInfo("America/Argentina/Cordoba")
#: Pasado mañana en Córdoba: dentro del horizonte (14) y lejos de la anticipación mínima.
DIA = (datetime.now(TZ) + timedelta(days=2)).date()
TEL = "+54 9 351 000-0001"
TEL_E164 = "+5493510000001"
OTRO_TEL = "+54 9 351 000-0002"


def _inicio(hora: int, minuto: int = 0, dia=DIA) -> str:
    return datetime.combine(dia, time(hora, minuto), tzinfo=TZ).astimezone(UTC).isoformat()


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


async def _abrir(session, lav: Lavadero, *, slug: str) -> None:
    """Alta terminada, slug conocido y horario 09–18 todos los días."""
    await set_tenant_context(session, lav.tenant_id)
    tenant = await session.get(Tenant, lav.tenant_id)
    tenant.slug = slug
    settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == lav.tenant_id)
    )
    settings.onboarding_completed_at = datetime.now(UTC)
    settings.whatsapp_e164 = "+5493510009999"
    settings.cancellation_policy = "Cancelá con 12 h de anticipación."
    await session.flush()
    for weekday in range(1, 8):
        await lav.catalogo.add_business_hours(weekday, time(9, 0), time(18, 0))


@pytest_asyncio.fixture
async def lav(db_session, tenant_a, owner) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_a.id, owner.id)
    await _abrir(db_session, lv, slug="lavadero-norte")
    return lv


@pytest_asyncio.fixture
async def lav_b(db_session, tenant_b) -> Lavadero:
    owner_b = await make_user(db_session, tenant_b, Role.OWNER, "owner-b-turnero@example.com")
    lv = await armar_catalogo(db_session, tenant_b.id, owner_b.id)
    await _abrir(db_session, lv, slug="lavadero-sur")
    return lv


def _reserva(lav: Lavadero, **extra: Any) -> dict[str, Any]:
    cuerpo: dict[str, Any] = {
        "vehicle_size_id": str(lav.auto),
        "service_ids": [str(lav.lavado)],
        "start_at": _inicio(10),
        "name": "Cliente Web",
        "phone": TEL,
        "accept_policy": True,
    }
    cuerpo.update(extra)
    return cuerpo


async def _reservar(client, lav: Lavadero, slug: str = "lavadero-norte", clave=None, **extra):
    return await client.post(
        f"/v1/public/{slug}/bookings",
        json=_reserva(lav, **extra),
        headers={"Idempotency-Key": clave or f"web-{uuid.uuid4()}"},
    )


async def _contar(session, modelo, *condiciones) -> int:
    return int(await session.scalar(select(func.count()).select_from(modelo).where(*condiciones)))


# ── El negocio (Z7/Z8) ────────────────────────────────────────────────────────


async def test_el_negocio_publico_trae_marca_y_textos_sin_ids_internos(client, lav):
    r = await client.get("/v1/public/lavadero-norte")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["slug"] == "lavadero-norte"
    assert cuerpo["timezone"] == "America/Argentina/Cordoba"
    assert cuerpo["palette"]["code"] == "default" and cuerpo["palette"]["primary"]["hex"]
    assert cuerpo["font"]["code"] == "default"
    assert cuerpo["whatsapp_e164"] == "+5493510009999"
    assert cuerpo["deposit_instructions"] is None
    assert cuerpo["hold_min"] == 30 and cuerpo["booking_horizon_days"] == 14
    assert "tenant_id" not in r.text and str(lav.tenant_id) not in r.text


async def test_el_slug_ignora_mayusculas(client, lav):
    assert (await client.get("/v1/public/Lavadero-Norte")).status_code == 200


async def test_slug_inexistente_sin_alta_y_anulado_dan_el_mismo_404(client, db_session, lav):
    inexistente = await client.get("/v1/public/no-existe-este")
    sin_alta_tenant = await make_tenant(db_session, "A medio configurar")
    sin_alta = await client.get(f"/v1/public/{sin_alta_tenant.slug}")
    assert inexistente.status_code == sin_alta.status_code == 404
    assert inexistente.content == sin_alta.content
    assert (await client.get(f"/v1/public/{'x' * 80}")).content == inexistente.content
    for ruta in ("/catalog", "/availability?date=2026-01-01"):
        assert (await client.get(f"/v1/public/no-existe-este{ruta}")).status_code in (404, 422)

    tenant = await db_session.get(Tenant, lav.tenant_id)
    tenant.voided_at = datetime.now(UTC)
    tenant.void_reason = VoidReason.DESACTIVADO
    await db_session.flush()
    anulado = await client.get("/v1/public/lavadero-norte")
    assert anulado.status_code == 404 and anulado.content == inexistente.content


# ── Catálogo ──────────────────────────────────────────────────────────────────


async def test_el_catalogo_oculta_solo_a_mano_y_muestra_a_cotizar_sin_reserva(
    client, db_session, lav
):
    await lav.activar_sena()
    servicio = await db_session.get(Service, lav.lavado_con_sena)
    servicio.booking_mode = ServiceBookingMode.SOLO_A_MANO
    await db_session.flush()

    r = await client.get("/v1/public/lavadero-norte/catalog")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert [s["label"] for s in cuerpo["vehicle_sizes"]] == ["Auto", "SUV"]
    por_id = {s["id"]: s for s in cuerpo["services"]}
    assert str(lav.lavado_con_sena) not in por_id
    lavado, tapizado = por_id[str(lav.lavado)], por_id[str(lav.tapizado)]
    assert lavado["bookable"] is True and lavado["deposit_bps"] == 3000
    precio = next(p for p in lavado["prices"] if p["vehicle_size_id"] == str(lav.auto))
    assert precio == {
        "vehicle_size_id": str(lav.auto),
        "price_cents": PRECIO_FIJO,
        "duration_min": 60,
        "deposit_cents": PRECIO_FIJO * 3000 // 10000,
    }
    assert tapizado["bookable"] is False and tapizado["pricing_mode"] == "A_COTIZAR"
    assert {p["price_cents"] for p in tapizado["prices"]} == {PRECIO_BASE_COTIZAR}
    assert {p["deposit_cents"] for p in tapizado["prices"]} == {0}


# ── Disponibilidad ────────────────────────────────────────────────────────────


def _dispo(lav: Lavadero, servicio=None, dia=DIA) -> dict[str, Any]:
    return {
        "date": dia.isoformat(),
        "vehicle_size_id": str(lav.auto),
        "service_ids": [str(servicio or lav.lavado)],
    }


async def test_la_disponibilidad_devuelve_solo_horas(client, lav):
    r = await client.get("/v1/public/lavadero-norte/availability", params=_dispo(lav))
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["timezone"] == "America/Argentina/Cordoba" and cuerpo["duration_min"] == 60
    assert cuerpo["slots"][0]["start_at"] == _inicio(9).replace("+00:00", "Z")
    assert len(cuerpo["slots"]) == 17  # 09:00 … 17:00 cada 30 min
    assert set(cuerpo["slots"][0]) == {"start_at", "end_at"}
    assert str(lav.puesto) not in r.text


async def test_la_disponibilidad_aplica_el_horizonte(client, lav):
    lejos = DIA + timedelta(days=30)
    r = await client.get("/v1/public/lavadero-norte/availability", params=_dispo(lav, dia=lejos))
    assert r.status_code == 200 and r.json()["slots"] == []


async def test_disponibilidad_de_un_servicio_no_reservable_es_422(client, db_session, lav, lav_b):
    servicio = await db_session.get(Service, lav.lavado_con_sena)
    servicio.booking_mode = ServiceBookingMode.SOLO_A_MANO
    await db_session.flush()
    for otro in (lav.tapizado, lav.lavado_con_sena, lav_b.lavado, uuid.uuid4()):
        r = await client.get(
            "/v1/public/lavadero-norte/availability", params=_dispo(lav, servicio=otro)
        )
        assert r.status_code == 422 and _codigo(r) == "SERVICE_NOT_BOOKABLE", r.text
    tamano_ajeno = {**_dispo(lav), "vehicle_size_id": str(lav_b.auto)}
    r = await client.get("/v1/public/lavadero-norte/availability", params=tamano_ajeno)
    assert r.status_code == 422 and _codigo(r) == "VALIDATION_ERROR"


# ── Reservar (B7) ─────────────────────────────────────────────────────────────


async def test_reserva_normal_sin_sena_queda_confirmada(client, db_session, lav):
    r = await _reservar(client, lav)
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "CONFIRMADO"
    assert cuerpo["code"].startswith(CODE_PREFIX) and len(cuerpo["code"]) == 10
    assert set(cuerpo["code"][4:]) <= set(CODE_ALPHABET)
    assert cuerpo["deposit_cents"] == 0 and cuerpo["hold_expires_at"] is None
    assert cuerpo["total_cents"] == PRECIO_FIJO and cuerpo["vehicle_size_label"] == "Auto"
    assert cuerpo["services"] == [
        {"name": "Lavado completo", "duration_min": 60, "price_cents": PRECIO_FIJO}
    ]
    assert cuerpo["can_cancel"] is True

    turno = await db_session.scalar(select(Booking).where(Booking.code == cuerpo["code"]))
    assert turno.source == BookingSource.TURNERO_WEB and turno.channel == Channel.TURNERO_WEB
    assert turno.resource_id == lav.puesto  # D5-6: el primero por sort_order
    politica = "Cancelá con 12 h de anticipación."
    assert turno.terms_version == hashlib.sha256(politica.encode()).hexdigest()[:12]
    assert turno.terms_accepted_at is not None and turno.notes is None
    cliente = await db_session.get(Customer, turno.customer_id)
    assert cliente.phone_e164 == TEL_E164 and cliente.name == "Cliente Web"
    assert cliente.acquisition_channel == Channel.TURNERO_WEB


async def test_reserva_con_sena_queda_pendiente_con_hold(client, lav):
    await lav.activar_sena()
    r = await _reservar(client, lav, service_ids=[str(lav.lavado_con_sena)])
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "PENDIENTE_SEÑA"
    assert cuerpo["deposit_cents"] == PRECIO_CON_SENA * 3000 // 10000
    assert cuerpo["hold_expires_at"] is not None


async def test_dos_servicios_se_suman(client, lav):
    r = await _reservar(client, lav, service_ids=[str(lav.lavado), str(lav.lavado_con_sena)])
    assert r.status_code == 201, r.text
    assert r.json()["total_cents"] == PRECIO_FIJO + PRECIO_CON_SENA
    assert len(r.json()["services"]) == 2


async def test_con_dos_incidencias_va_a_la_lista_de_espera(client, db_session, lav):
    await lav.activar_sena()
    cliente = await lav.clientes.resolve_by_phone(TEL, name="Cliente Web")
    for motivo in ("No vino", "Llegó tarde"):
        db_session.add(
            CustomerIncidentMark(
                tenant_id=lav.tenant_id,
                customer_id=cliente.customer.id,
                reason=motivo,
                created_by_user_id=lav.owner_id,
            )
        )
    await db_session.flush()
    r = await _reservar(client, lav, service_ids=[str(lav.lavado_con_sena)])
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "PENDIENTE_REVISION"
    assert cuerpo["deposit_cents"] == 0 and cuerpo["hold_expires_at"] is None
    assert cuerpo["can_cancel"] is True


async def test_el_tercer_turno_activo_es_409(client, lav):
    for hora in (10, 12):
        assert (await _reservar(client, lav, start_at=_inicio(hora))).status_code == 201
    r = await _reservar(client, lav, start_at=_inicio(14))
    assert r.status_code == 409 and _codigo(r) == "TOO_MANY_ACTIVE_BOOKINGS"
    # Otro WhatsApp sí puede.
    assert (await _reservar(client, lav, start_at=_inicio(14), phone=OTRO_TEL)).status_code == 201


@pytest.mark.parametrize(
    "inicio",
    [
        _inicio(3),  # fuera del horario
        _inicio(10, 10),  # fuera de la grilla
        _inicio(17, 30),  # termina después del cierre
        _inicio(10, dia=DIA + timedelta(days=40)),  # fuera del horizonte
    ],
)
async def test_un_horario_que_el_motor_no_ofrece_es_slot_taken(client, lav, inicio):
    r = await _reservar(client, lav, start_at=inicio)
    assert r.status_code == 409 and _codigo(r) == "SLOT_TAKEN", r.text


async def test_un_horario_ocupado_es_slot_taken(client, lav):
    inicio = datetime.combine(DIA, time(10), tzinfo=TZ).astimezone(UTC)
    ahora = datetime.now(UTC)
    for puesto in (lav.puesto, lav.puesto_2):
        await lav.turno(inicio=inicio, puesto=puesto, ahora=ahora)
    r = await _reservar(client, lav)
    assert r.status_code == 409 and _codigo(r) == "SLOT_TAKEN"


async def test_con_un_puesto_ocupado_se_asigna_el_siguiente(client, db_session, lav):
    inicio = datetime.combine(DIA, time(10), tzinfo=TZ).astimezone(UTC)
    await lav.turno(inicio=inicio, puesto=lav.puesto, ahora=datetime.now(UTC))
    r = await _reservar(client, lav)
    assert r.status_code == 201, r.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.resource_id == lav.puesto_2


async def test_servicio_a_cotizar_o_solo_a_mano_no_se_reserva(client, db_session, lav):
    r = await _reservar(client, lav, service_ids=[str(lav.tapizado)])
    assert r.status_code == 422 and _codigo(r) == "SERVICE_NOT_BOOKABLE"
    servicio = await db_session.get(Service, lav.lavado)
    servicio.booking_mode = ServiceBookingMode.SOLO_A_MANO
    await db_session.flush()
    r = await _reservar(client, lav)
    assert r.status_code == 422 and _codigo(r) == "SERVICE_NOT_BOOKABLE"


@pytest.mark.parametrize("telefono", ["123", "54 351 000 0001", "15 5555 0001"])
async def test_un_telefono_invalido_o_ambiguo_es_422(client, db_session, lav, telefono):
    r = await _reservar(client, lav, phone=telefono)
    assert r.status_code == 422 and _codigo(r) == "INVALID_PHONE"
    assert await _contar(db_session, Booking, Booking.tenant_id == lav.tenant_id) == 0


async def test_sin_aceptar_la_politica_es_422(client, lav):
    r = await _reservar(client, lav, accept_policy=False)
    assert r.status_code == 422 and _codigo(r) == "VALIDATION_ERROR"


async def test_sin_idempotency_key_es_422(client, lav):
    r = await client.post("/v1/public/lavadero-norte/bookings", json=_reserva(lav))
    assert r.status_code == 422


async def test_el_reenvio_con_la_misma_clave_devuelve_el_mismo_turno(client, db_session, lav):
    primera = await _reservar(client, lav, clave="web-doble-toque")
    segunda = await _reservar(client, lav, clave="web-doble-toque")
    assert primera.status_code == 201 and segunda.status_code == 201
    assert primera.json() == segunda.json()
    assert await _contar(db_session, Booking, Booking.tenant_id == lav.tenant_id) == 1
    # La clave con otro WhatsApp no devuelve el turno de nadie.
    ajena = await _reservar(client, lav, clave="web-doble-toque", phone=OTRO_TEL)
    assert ajena.status_code == 409 and _codigo(ajena) == "DUPLICATE_IDEMPOTENT"
    assert "TUR-" not in ajena.text


async def test_cliente_existente_no_se_le_pisa_el_nombre(client, db_session, lav):
    existente = await lav.clientes.resolve_by_phone(TEL, name="Nombre Original")
    r = await _reservar(client, lav, name="Otro Nombre")
    assert r.status_code == 201, r.text
    await db_session.refresh(existente.customer)
    assert existente.customer.name == "Nombre Original"
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.customer_id == existente.customer.id
    assert turno.notes == "Nombre en la web: Otro Nombre"


async def test_la_patente_nueva_se_vincula_y_la_existente_no(client, db_session, lav):
    nueva = await _reservar(client, lav, plate="AA 000 AA")
    assert nueva.status_code == 201, nueva.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == nueva.json()["code"]))
    vehiculo = await db_session.get(Vehicle, turno.vehicle_id)
    assert vehiculo.plate_normalized == "AA000AA"
    assert (
        await _contar(db_session, CustomerVehicle, CustomerVehicle.vehicle_id == vehiculo.id) == 1
    )

    # `AB123CD` es del cliente del armado: el turno la referencia pero no se vincula.
    ajena = await _reservar(client, lav, plate="AB123CD", phone=OTRO_TEL, start_at=_inicio(12))
    assert ajena.status_code == 201, ajena.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == ajena.json()["code"]))
    assert turno.vehicle_id == lav.vehiculo
    vinculos = await db_session.scalars(
        select(CustomerVehicle.customer_id).where(CustomerVehicle.vehicle_id == lav.vehiculo)
    )
    assert turno.customer_id not in set(vinculos)


async def test_una_patente_larga_es_422_y_una_vacia_no_crea_vehiculo(client, db_session, lav):
    r = await _reservar(client, lav, plate="ABCDEFGHIJK")
    assert r.status_code == 422  # el schema corta en 10 (`MAX_PLATE_LENGTH`)
    r = await _reservar(client, lav, plate="SIN000")
    assert r.status_code == 201, r.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.vehicle_id is None


async def test_una_cookie_de_otro_negocio_no_cambia_el_negocio_del_slug(
    client, db_session, lav, lav_b, cookies_b
):
    r = await client.post(
        "/v1/public/lavadero-norte/bookings",
        json=_reserva(lav),
        headers={"Idempotency-Key": "web-con-cookie"},
        cookies=cookies_b,
    )
    assert r.status_code == 201, r.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.tenant_id == lav.tenant_id


# ── Ver y cancelar (B9, D5-8) ─────────────────────────────────────────────────


async def test_ver_el_turno_con_codigo_y_whatsapp(client, lav):
    codigo = (await _reservar(client, lav)).json()["code"]
    r = await client.post(
        "/v1/public/lavadero-norte/bookings/lookup",
        json={"code": codigo.lower().replace("tur-", ""), "phone": "351 000 0001"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["code"] == codigo and r.json()["can_cancel"] is True


async def test_whatsapp_ajeno_codigo_inexistente_o_mal_escrito_dan_el_mismo_404(client, lav):
    codigo = (await _reservar(client, lav)).json()["code"]
    ruta = "/v1/public/lavadero-norte/bookings/lookup"
    ajeno = await client.post(ruta, json={"code": codigo, "phone": OTRO_TEL})
    inexistente = await client.post(ruta, json={"code": "TUR-ZZZZZZ", "phone": TEL})
    mal_escrito = await client.post(ruta, json={"code": "TUR-00000", "phone": TEL})
    sin_telefono = await client.post(ruta, json={"code": codigo, "phone": "123"})
    assert ajeno.status_code == 404
    assert ajeno.content == inexistente.content == mal_escrito.content == sin_telefono.content


async def test_cancelar_con_codigo_y_whatsapp_es_idempotente(client, db_session, lav):
    codigo = (await _reservar(client, lav)).json()["code"]
    ruta = "/v1/public/lavadero-norte/bookings/cancel"
    cuerpo = {"code": codigo, "phone": TEL, "reason": "Me surgió algo"}
    r = await client.post(ruta, json=cuerpo)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "code": codigo,
        "status": "CANCELADO_CLIENTE",
        "classification": "NORMAL",  # pasado mañana: más de 12 h
        "deposit_status": "SIN_PAGO",
    }
    otra_vez = await client.post(ruta, json=cuerpo)
    assert otra_vez.status_code == 200 and otra_vez.json() == r.json()
    visto = await client.post(
        "/v1/public/lavadero-norte/bookings/lookup", json={"code": codigo, "phone": TEL}
    )
    assert visto.json()["status"] == "CANCELADO_CLIENTE" and visto.json()["can_cancel"] is False


async def test_cancelar_con_whatsapp_ajeno_es_404_y_no_toca_el_turno(client, db_session, lav):
    codigo = (await _reservar(client, lav)).json()["code"]
    r = await client.post(
        "/v1/public/lavadero-norte/bookings/cancel", json={"code": codigo, "phone": OTRO_TEL}
    )
    assert r.status_code == 404
    turno = await db_session.scalar(select(Booking).where(Booking.code == codigo))
    assert turno.status == BookingStatus.CONFIRMADO


async def test_cancelar_un_turno_ya_atendido_es_409(client, db_session, lav):
    codigo = (await _reservar(client, lav)).json()["code"]
    turno = await db_session.scalar(select(Booking).where(Booking.code == codigo))
    turno.status = BookingStatus.ATENDIDO
    await db_session.flush()
    r = await client.post(
        "/v1/public/lavadero-norte/bookings/cancel", json={"code": codigo, "phone": TEL}
    )
    assert r.status_code == 409 and _codigo(r) == "CONFLICT"


# ── Aislamiento: el slug A nunca ve ni cancela turnos de B ───────────────────


async def test_el_slug_de_un_negocio_no_ve_ni_cancela_turnos_del_otro(
    client, db_session, lav, lav_b
):
    codigo_b = (await _reservar(client, lav_b, slug="lavadero-sur")).json()["code"]
    inexistente = await client.post(
        "/v1/public/lavadero-norte/bookings/lookup", json={"code": "TUR-ZZZZZZ", "phone": TEL}
    )
    for accion in ("lookup", "cancel"):
        r = await client.post(
            f"/v1/public/lavadero-norte/bookings/{accion}", json={"code": codigo_b, "phone": TEL}
        )
        assert r.status_code == 404 and r.content == inexistente.content
    turno = await db_session.scalar(select(Booking).where(Booking.code == codigo_b))
    assert turno.tenant_id == lav_b.tenant_id and turno.status == BookingStatus.CONFIRMADO
    # Y el mismo WhatsApp en el otro negocio es otro cliente.
    assert await _contar(db_session, Customer, Customer.phone_e164 == TEL_E164) == 1


async def test_un_servicio_de_otro_negocio_no_se_reserva(client, lav, lav_b):
    r = await _reservar(client, lav, service_ids=[str(lav_b.lavado)])
    assert r.status_code == 422 and _codigo(r) == "SERVICE_NOT_BOOKABLE"


# ── Abuso (§6) ────────────────────────────────────────────────────────────────


async def test_enumerar_slugs_corta_por_ip(client):
    limiter.reset()
    codigos = [(await client.get(f"/v1/public/slug-{i}")).status_code for i in range(61)]
    assert set(codigos[:60]) == {404} and codigos[60] == 429


async def test_reservar_corta_a_las_cinco_por_minuto(client, lav):
    limiter.reset()
    codigos = [
        (
            await _reservar(client, lav, start_at=_inicio(9 + i), phone=f"+54 9 351 000-01{i:02d}")
        ).status_code
        for i in range(6)
    ]
    assert codigos == [201] * 5 + [429]


async def test_el_lookup_corta_a_las_diez_por_minuto(client, lav):
    limiter.reset()
    ruta = "/v1/public/lavadero-norte/bookings/lookup"
    codigos = [
        (await client.post(ruta, json={"code": "TUR-ZZZZZZ", "phone": TEL})).status_code
        for _ in range(11)
    ]
    assert codigos == [404] * 10 + [429]


# ── Ronda de arreglos de 5.2 (code-review y T3) ───────────────────────────────


async def test_la_misma_clave_con_otro_horario_servicios_o_tamano_es_409(client, db_session, lav):
    clave = "web-misma-clave"
    original = await _reservar(client, lav, clave=clave)
    assert original.status_code == 201, original.text
    variantes = [
        {"start_at": _inicio(12)},
        {"service_ids": [str(lav.lavado), str(lav.lavado_con_sena)]},
        {"vehicle_size_id": str(lav.suv)},
    ]
    for cambio in variantes:
        r = await _reservar(client, lav, clave=clave, **cambio)
        assert r.status_code == 409 and _codigo(r) == "DUPLICATE_IDEMPOTENT", (cambio, r.text)
        assert original.json()["code"] not in r.text
    assert await _contar(db_session, Booking, Booking.tenant_id == lav.tenant_id) == 1
    # El mismo pedido sigue devolviendo el turno original.
    assert (await _reservar(client, lav, clave=clave)).json() == original.json()


async def test_la_misma_clave_con_el_turno_ya_cancelado_es_409(client, lav):
    clave = "web-cancelado"
    codigo = (await _reservar(client, lav, clave=clave)).json()["code"]
    cancelado = await client.post(
        "/v1/public/lavadero-norte/bookings/cancel", json={"code": codigo, "phone": TEL}
    )
    assert cancelado.status_code == 200
    r = await _reservar(client, lav, clave=clave)
    assert r.status_code == 409 and _codigo(r) == "DUPLICATE_IDEMPOTENT"


async def test_si_se_agotan_los_reintentos_del_codigo_es_503_limpio(
    client, db_session, lav, monkeypatch
):
    from app.domain import booking_code

    ocupado = (await _reservar(client, lav)).json()["code"]
    monkeypatch.setattr(booking_code, "new_booking_code", lambda *a, **k: ocupado)
    r = await _reservar(client, lav, start_at=_inicio(14), phone=OTRO_TEL)
    assert r.status_code == 503 and _codigo(r) == "SERVICE_UNAVAILABLE", r.text
    assert ocupado not in r.text
    # Nada a medias: el cliente no quedó con un turno.
    assert await _contar(db_session, Booking, Booking.tenant_id == lav.tenant_id) == 1


async def test_la_lista_de_espera_se_guarda_sin_sena_y_conserva_el_porcentaje_de_los_items(
    client, db_session, lav
):
    from app.persistence.models.agenda import BookingItem

    await lav.activar_sena()
    cliente = await lav.clientes.resolve_by_phone(TEL, name="Cliente Web")
    for motivo in ("No vino", "Llegó tarde"):
        db_session.add(
            CustomerIncidentMark(
                tenant_id=lav.tenant_id,
                customer_id=cliente.customer.id,
                reason=motivo,
                created_by_user_id=lav.owner_id,
            )
        )
    await db_session.flush()
    r = await _reservar(client, lav, service_ids=[str(lav.lavado_con_sena)])
    assert r.status_code == 201, r.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.status == BookingStatus.PENDIENTE_REVISION
    assert turno.deposit_required_cents == 0 and turno.hold_expires_at is None
    items = list(
        await db_session.scalars(select(BookingItem).where(BookingItem.booking_id == turno.id))
    )
    # El % queda en el ítem: la aprobación (5.3, adenda 5.3-2) recalcula la seña con la
    # configuración vigente a partir de ahí.
    assert [i.deposit_bps for i in items] == [3000]
