"""FASE-3-CONTRATO §1, B9–B11 — el esquema del dominio contra Postgres real.

- **B10** `xc_bookings_sin_solapamiento`: la garantía de no solapamiento vive en la base.
- **B11** `job_events` append-only: ni el runtime ni el dueño pueden modificar la historia.
- Los CHECKs del contrato, uno por regla (no exhaustivos).
- **B9** unicidad por tenant y "entre vivos" (X7).
- X11: sin `btree_gist`, la migración corta con el mensaje accionable.

Las filas se insertan con SQL crudo desde el superusuario (los constraints aplican igual; RLS
se prueba en `security/`), salvo donde lo que se afirma es el rol: B11 y el alta por ORM.
"""

import importlib.util
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain import enums
from app.domain.enums import BookingStatus, PaymentKind
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models import Payment
from app.tests.conftest_pg import _with_driver, owner_url
from app.tests.persistence.test_esquema_dominio import alta_por_orm

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

MIGRACION = (
    Path(__file__).resolve().parents[2] / "persistence/migrations/versions/20260918_0002_dominio.py"
)
DIEZ = datetime(2026, 9, 21, 10, tzinfo=UTC)


def _migracion() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migracion_0002", MIGRACION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Semilla ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Lavadero:
    tenant: uuid.UUID
    user: uuid.UUID
    size: uuid.UUID
    service: uuid.UUID
    resource: uuid.UUID
    resource_2: uuid.UUID
    customer: uuid.UUID
    vehicle: uuid.UUID
    method: uuid.UUID


async def _insert(conn: AsyncConnection, table: str, **values: Any) -> uuid.UUID:
    values.setdefault("id", uuid.uuid4())
    columnas = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    await conn.execute(text(f"INSERT INTO {table} ({columnas}) VALUES ({params})"), values)
    return uuid.UUID(str(values["id"]))


async def _sembrar(engine: AsyncEngine, tenant: uuid.UUID, user: uuid.UUID) -> Lavadero:
    async with engine.begin() as conn:
        t = {"tenant_id": tenant}
        return Lavadero(
            tenant=tenant,
            user=user,
            size=await _insert(conn, "vehicle_sizes", **t, code="AUTO", label="Auto"),
            service=await _insert(
                conn, "services", **t, name="Lavado completo", pricing_mode="PRECIO_FIJO"
            ),
            resource=await _insert(conn, "resources", **t, name="Puesto 1"),
            resource_2=await _insert(conn, "resources", **t, name="Puesto 2"),
            customer=await _insert(conn, "customers", **t, name="Ana"),
            vehicle=await _insert(conn, "vehicles", **t, plate_normalized=None),
            method=await _insert(conn, "payment_methods", **t, code="EFECTIVO", label="Efectivo"),
        )


@pytest_asyncio.fixture
async def lavaderos(
    pg_admin_engine: AsyncEngine, pg_tenant_a: uuid.UUID, pg_tenant_b: uuid.UUID, pg_user_factory
) -> tuple[Lavadero, Lavadero]:
    user_a = await pg_user_factory(pg_tenant_a, f"a-{uuid.uuid4()}@example.com")
    user_b = await pg_user_factory(pg_tenant_b, f"b-{uuid.uuid4()}@example.com")
    return (
        await _sembrar(pg_admin_engine, pg_tenant_a, user_a),
        await _sembrar(pg_admin_engine, pg_tenant_b, user_b),
    )


def _turno(
    lav: Lavadero, inicio: datetime = DIEZ, minutos: int = 60, **over: Any
) -> dict[str, Any]:
    valores: dict[str, Any] = {
        "tenant_id": lav.tenant,
        "code": f"TUR-{uuid.uuid4().hex[:8]}",
        "source": "PANEL",
        "channel": "WHATSAPP",
        "status": "CONFIRMADO",
        "resource_id": lav.resource,
        "start_at": inicio,
        "end_at": inicio + timedelta(minutes=minutos),
        "customer_id": lav.customer,
        "service_id": lav.service,
        "vehicle_size_id": lav.size,
        "service_name_snapshot": "Lavado completo",
        "duration_min": minutos,
        "price_cents": 2_000_000,
    }
    return valores | over


def _job(lav: Lavadero, **over: Any) -> dict[str, Any]:
    valores: dict[str, Any] = {
        "tenant_id": lav.tenant,
        "vehicle_id": lav.vehicle,
        "vehicle_size_id": lav.size,
        "service_id": lav.service,
        "responsible_user_id": lav.user,
        "channel": "CALLE",
        "status": "PRESENTE",
        "service_name_snapshot": "Lavado completo",
        "base_price_cents": 2_000_000,
        "arrived_at": DIEZ,
    }
    return valores | over


def _evento(lav: Lavadero, job: uuid.UUID, **over: Any) -> dict[str, Any]:
    valores: dict[str, Any] = {
        "tenant_id": lav.tenant,
        "job_id": job,
        "event_type": "JOB_RECEIVED",
        "to_status": "PRESENTE",
        "occurred_at": DIEZ,
        "actor_user_id": lav.user,
        "idempotency_key": f"k-{uuid.uuid4()}",
    }
    return valores | over


def _pago(lav: Lavadero, **over: Any) -> dict[str, Any]:
    valores: dict[str, Any] = {
        "tenant_id": lav.tenant,
        "kind": "SEÑA",
        "amount_cents": 500_000,
        "payment_method_id": lav.method,
        "commission_bps": 0,
        "commission_cents": 0,
        "occurred_at": DIEZ,
        "actor_user_id": lav.user,
        "idempotency_key": f"p-{uuid.uuid4()}",
    }
    return valores | over


async def _alta(engine: AsyncEngine, table: str, valores: dict[str, Any]) -> uuid.UUID:
    async with engine.begin() as conn:
        return await _insert(conn, table, **valores)


async def _rechaza(engine: AsyncEngine, table: str, valores: dict[str, Any], motivo: str) -> None:
    with pytest.raises(IntegrityError, match=motivo):
        await _alta(engine, table, valores)


# ── B10: sin solapamiento ─────────────────────────────────────────────────────

XC = "xc_bookings_sin_solapamiento"


async def test_dos_turnos_solapados_en_el_mismo_puesto_chocan(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _alta(pg_admin_engine, "bookings", _turno(a))
    await _rechaza(pg_admin_engine, "bookings", _turno(a, DIEZ + timedelta(minutes=30)), XC)
    # un turno largo que contiene al otro también
    await _rechaza(pg_admin_engine, "bookings", _turno(a, DIEZ - timedelta(hours=1), 300), XC)


async def test_en_puestos_distintos_no_chocan(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _alta(pg_admin_engine, "bookings", _turno(a))
    await _alta(pg_admin_engine, "bookings", _turno(a, resource_id=a.resource_2))


async def test_tocarse_en_el_borde_no_es_solaparse(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _alta(pg_admin_engine, "bookings", _turno(a))  # [10:00, 11:00)
    await _alta(pg_admin_engine, "bookings", _turno(a, DIEZ + timedelta(hours=1)))  # [11:00, 12:00)
    await _alta(pg_admin_engine, "bookings", _turno(a, DIEZ - timedelta(hours=1)))  # [09:00, 10:00)


#: §2.1: estos no ocupan el puesto.
LIBERAN = [
    BookingStatus.ATENDIDO,
    BookingStatus.VENCIDO,
    BookingStatus.CANCELADO_CLIENTE,
    BookingStatus.AUSENTE_CON_AVISO_POSTERIOR,
    BookingStatus.CANCELADO_OPERATIVO,
    BookingStatus.CANCELADO_DEMORA,
    BookingStatus.NO_ASISTIO,
]


@pytest.mark.parametrize("estado", LIBERAN, ids=lambda s: s.value)
async def test_un_turno_que_no_bloquea_libera_el_puesto(pg_admin_engine, lavaderos, estado):
    a, _ = lavaderos
    await _alta(pg_admin_engine, "bookings", _turno(a, status=estado.value))
    await _alta(pg_admin_engine, "bookings", _turno(a))


@pytest.mark.parametrize(
    "estado",
    [BookingStatus.PENDIENTE_SENA, BookingStatus.PENDIENTE_COTIZACION, BookingStatus.RECIBIDO],
    ids=lambda s: s.value,
)
async def test_los_estados_que_bloquean_bloquean(pg_admin_engine, lavaderos, estado):
    a, _ = lavaderos
    await _alta(
        pg_admin_engine,
        "bookings",
        _turno(a, status=estado.value, hold_expires_at=DIEZ - timedelta(hours=1)),
    )
    await _rechaza(pg_admin_engine, "bookings", _turno(a), XC)


async def test_un_turno_anulado_no_bloquea(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _alta(
        pg_admin_engine,
        "bookings",
        _turno(a, voided_at=DIEZ, void_reason="ERROR_DE_CARGA"),
    )
    await _alta(pg_admin_engine, "bookings", _turno(a))


async def test_tenants_distintos_con_el_mismo_horario_no_chocan(pg_admin_engine, lavaderos):
    a, b = lavaderos
    await _alta(pg_admin_engine, "bookings", _turno(a))
    await _alta(pg_admin_engine, "bookings", _turno(b))


async def test_el_vencimiento_perezoso_deja_reservar_sobre_un_hold_vencido(
    pg_admin_engine, lavaderos
):
    """§1.3: el `WHERE` del EXCLUDE no puede usar `now()`. El hold vencido bloquea hasta que
    el servicio lo vence EN LA MISMA transacción; después, el horario está libre."""
    a, _ = lavaderos
    vencido = DIEZ - timedelta(minutes=1)
    await _alta(
        pg_admin_engine,
        "bookings",
        _turno(a, status="PENDIENTE_SEÑA", hold_expires_at=vencido),
    )
    await _rechaza(pg_admin_engine, "bookings", _turno(a), XC)
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE bookings SET status = 'VENCIDO' "
                "WHERE tenant_id = :t AND resource_id = :r "
                "AND status IN ('PENDIENTE_SEÑA', 'PENDIENTE_COTIZACION') "
                "AND hold_expires_at <= :ahora"
            ),
            {"t": a.tenant, "r": a.resource, "ahora": DIEZ},
        )
        await _insert(conn, "bookings", **_turno(a))


# ── B11: job_events append-only ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def evento_de_a(pg_admin_engine, lavaderos) -> tuple[Lavadero, uuid.UUID]:
    a, _ = lavaderos
    job = await _alta(pg_admin_engine, "jobs", _job(a))
    return a, await _alta(pg_admin_engine, "job_events", _evento(a, job))


@pytest_asyncio.fixture
async def pg_owner_engine(pg_admin_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(_with_driver(owner_url(), "asyncpg"), poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE job_events SET idempotency_key = 'x' WHERE id = :id",
        "DELETE FROM job_events WHERE id = :id",
    ],
)
async def test_el_runtime_no_modifica_job_events(pg_session_factory, evento_de_a, sentencia):
    a, evento = evento_de_a
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError, match="permission denied"):
            async with session.begin():
                await set_tenant_context(session, a.tenant)
                await session.execute(text(sentencia), {"id": evento})


@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE job_events SET idempotency_key = 'x' WHERE id = :id",
        "DELETE FROM job_events WHERE id = :id",
    ],
)
async def test_ni_el_duenio_modifica_job_events(pg_owner_engine, evento_de_a, sentencia):
    a, evento = evento_de_a
    with pytest.raises(DBAPIError, match="append-only"):
        async with pg_owner_engine.begin() as conn:
            # El dueño también pasa por RLS (FORCE): sin contexto no vería la fila y el
            # UPDATE afectaría 0 filas sin llegar al trigger.
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(a.tenant)}
            )
            await conn.execute(text(sentencia), {"id": evento})


async def test_el_runtime_si_inserta_y_lee_job_events(pg_session_factory, evento_de_a):
    a, evento = evento_de_a
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, a.tenant)
        job = await session.scalar(
            text("SELECT job_id FROM job_events WHERE id = :id"), {"id": evento}
        )
        valores = _evento(
            a, job, event_type="JOB_STARTED", from_status="PRESENTE", to_status="EN_PROCESO"
        )
        await session.execute(
            text(
                "INSERT INTO job_events (id, tenant_id, job_id, event_type, from_status, "
                "to_status, occurred_at, actor_user_id, idempotency_key) "
                "VALUES (:id, :tenant_id, :job_id, "
                ":event_type, :from_status, :to_status, :occurred_at, :actor_user_id, "
                ":idempotency_key)"
            ),
            {"id": uuid.uuid4(), **valores},
        )
        assert await session.scalar(text("SELECT count(*) FROM job_events")) == 2


async def test_idempotency_key_de_eventos_por_tenant(pg_admin_engine, lavaderos):
    a, b = lavaderos
    job_a = await _alta(pg_admin_engine, "jobs", _job(a))
    job_b = await _alta(pg_admin_engine, "jobs", _job(b))
    await _alta(pg_admin_engine, "job_events", _evento(a, job_a, idempotency_key="cola-1"))
    await _alta(pg_admin_engine, "job_events", _evento(b, job_b, idempotency_key="cola-1"))
    await _rechaza(
        pg_admin_engine,
        "job_events",
        _evento(a, job_a, idempotency_key="cola-1"),
        "uq_job_events_tenant_id_idempotency_key",
    )


@pytest.mark.parametrize("metadata", [{}, {"reason": ""}, {"reason": "   "}, {"reason": None}])
async def test_revertir_la_retencion_sin_motivo_falla(pg_admin_engine, lavaderos, metadata):
    a, _ = lavaderos
    job = await _alta(pg_admin_engine, "jobs", _job(a))
    await _rechaza(
        pg_admin_engine,
        "job_events",
        _evento(
            a,
            job,
            event_type="DEPOSIT_RETENTION_REVERSED",
            to_status=None,
            metadata=json.dumps(metadata),
        ),
        "ck_job_events_reversion_con_motivo",
    )


async def test_revertir_la_retencion_con_motivo_pasa(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    job = await _alta(pg_admin_engine, "jobs", _job(a))
    await _alta(
        pg_admin_engine,
        "job_events",
        _evento(
            a,
            job,
            event_type="DEPOSIT_RETENTION_REVERSED",
            to_status=None,
            metadata=json.dumps({"reason": "el cliente avisó por WhatsApp"}),
        ),
    )


async def test_un_evento_con_origen_exige_destino(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    job = await _alta(pg_admin_engine, "jobs", _job(a))
    await _rechaza(
        pg_admin_engine,
        "job_events",
        _evento(a, job, from_status="PRESENTE", to_status=None),
        "ck_job_events_estados_coherentes",
    )


# ── CHECKs del contrato, uno por regla ────────────────────────────────────────


def _precio(lav: Lavadero, **over: Any) -> dict[str, Any]:
    return {
        "tenant_id": lav.tenant,
        "service_id": lav.service,
        "vehicle_size_id": lav.size,
        "duration_min": 60,
    } | over


async def test_precio_mayor_a_cero_o_nulo(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "service_prices",
        _precio(a, price_cents=0),
        "ck_service_prices_precio_positivo",
    )
    await _alta(pg_admin_engine, "service_prices", _precio(a, price_cents=None))  # A_COTIZAR


@pytest.mark.parametrize("bps", [-1, 10_001])
async def test_sena_en_puntos_basicos_entre_0_y_10000(pg_admin_engine, lavaderos, bps):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "service_prices",
        _precio(a, deposit_bps=bps),
        "ck_service_prices_sena_bps_rango",
    )


@pytest.mark.parametrize("estado", ["PENDIENTE_SEÑA", "PENDIENTE_COTIZACION"])
async def test_un_turno_retenido_exige_vencimiento(pg_admin_engine, lavaderos, estado):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "bookings",
        _turno(a, status=estado, hold_expires_at=None),
        "ck_bookings_hold_obligatorio",
    )


async def test_sin_precio_no_hay_sena(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "bookings",
        _turno(a, price_cents=None, deposit_required_cents=100),
        "ck_bookings_sin_precio_sin_sena",
    )


async def test_descuento_con_motivo(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine, "jobs", _job(a, discount_cents=100), "ck_jobs_descuento_con_motivo"
    )


async def test_el_total_pactado_es_positivo(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "jobs",
        _job(a, base_price_cents=1000, discount_cents=1000, discount_reason="cortesía"),
        "ck_jobs_total_positivo",
    )


async def test_un_cobro_es_de_un_job_o_de_un_turno(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _rechaza(pg_admin_engine, "payments", _pago(a), "ck_payments_con_job_o_turno")


async def _cancelacion(engine: AsyncEngine, lav: Lavadero, **over: Any) -> dict[str, Any]:
    turno = await _alta(engine, "bookings", _turno(lav, status="CANCELADO_CLIENTE"))
    return {
        "tenant_id": lav.tenant,
        "booking_id": turno,
        "initiator": "CLIENTE",
        "classification": "NORMAL",
        "requested_at": DIEZ - timedelta(days=1),
        "anticipation_min": 1440,
        "previous_status": "CONFIRMADO",
        "resulting_status": "CANCELADO_CLIENTE",
        "deposit_paid_cents": 500_000,
        "deposit_status": "DEVOLUCION_PENDIENTE",
    } | over


async def test_cerrar_una_cancelacion_exige_actor_y_motivo(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    sin_actor = await _cancelacion(
        pg_admin_engine, a, deposit_status="RETENIDA", resolution_reason="pidió que se retenga"
    )
    await _rechaza(pg_admin_engine, "cancellations", sin_actor, "ck_cancellations_cierre_con_actor")
    for motivo in (None, "", "  "):
        sin_motivo = await _cancelacion(
            pg_admin_engine,
            a,
            deposit_status="RETENIDA",
            resolved_at=DIEZ,
            resolved_by_user_id=a.user,
            resolution_reason=motivo,
        )
        await _rechaza(
            pg_admin_engine, "cancellations", sin_motivo, "ck_cancellations_cierre_con_motivo"
        )
    completa = await _cancelacion(
        pg_admin_engine,
        a,
        deposit_status="RETENIDA",
        resolved_at=DIEZ,
        resolved_by_user_id=a.user,
        resolution_reason="pidió que se retenga",
    )
    await _alta(pg_admin_engine, "cancellations", completa)


async def test_devuelta_exige_el_pago_de_devolucion(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    valores = await _cancelacion(
        pg_admin_engine,
        a,
        deposit_status="DEVUELTA",
        resolved_at=DIEZ,
        resolved_by_user_id=a.user,
        resolution_reason="transferido",
    )
    await _rechaza(pg_admin_engine, "cancellations", valores, "ck_cancellations_devuelta_con_pago")


async def test_una_cancelacion_por_turno(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    valores = await _cancelacion(pg_admin_engine, a)
    await _alta(pg_admin_engine, "cancellations", valores)
    await _rechaza(
        pg_admin_engine,
        "cancellations",
        valores | {"id": uuid.uuid4()},
        "uq_cancellations_tenant_id_booking_id",
    )


@pytest.mark.parametrize("estado", ["COTIZADO", "ACEPTADO"])
async def test_cotizado_exige_precio_y_duracion(pg_admin_engine, lavaderos, estado):
    a, _ = lavaderos
    base = {
        "tenant_id": a.tenant,
        "customer_id": a.customer,
        "service_id": a.service,
        "vehicle_size_id": a.size,
        "status": estado,
        "requested_at": DIEZ,
    }
    await _rechaza(
        pg_admin_engine,
        "quotes",
        base | {"agreed_duration_min": 90},
        "ck_quotes_cotizado_completo",
    )
    await _rechaza(
        pg_admin_engine,
        "quotes",
        base | {"agreed_price_cents": 5_000_000},
        "ck_quotes_cotizado_completo",
    )
    await _alta(
        pg_admin_engine,
        "quotes",
        base | {"agreed_price_cents": 5_000_000, "agreed_duration_min": 90},
    )


@pytest.mark.parametrize(
    "telefono", ["3511234567", "+054935112345", "+549351", "+5493511234567890"]
)
async def test_telefono_en_e164(pg_admin_engine, lavaderos, telefono):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "customers",
        {"tenant_id": a.tenant, "name": "X", "phone_e164": telefono},
        "ck_customers_telefono_e164",
    )


@pytest.mark.parametrize("patente", ["ab123cd", "AB-123-CD", "AB 123 CD", "", "ABCDEFGHIJK"])
async def test_patente_normalizada(pg_admin_engine, lavaderos, patente):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "vehicles",
        {"tenant_id": a.tenant, "plate_normalized": patente},
        "ck_vehicles_patente_normalizada",
    )


async def test_codigos_de_catalogo_y_moneda(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    await _rechaza(
        pg_admin_engine,
        "vehicle_sizes",
        {"tenant_id": a.tenant, "code": "auto", "label": "Auto"},
        "ck_vehicle_sizes_code_formato",
    )
    with pytest.raises(IntegrityError, match="ck_tenants_currency_iso"):
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET currency = 'ars' WHERE id = :t"), {"t": a.tenant}
            )


# ── B9: unicidad por tenant y entre vivos ─────────────────────────────────────

_ANULAR = "UPDATE {tabla} SET voided_at = now(), void_reason = 'ERROR_DE_CARGA' WHERE id = :id"


async def test_misma_patente_telefono_codigo_y_clave_conviven_en_dos_tenants(
    pg_admin_engine, lavaderos
):
    for lav in lavaderos:
        await _alta(
            pg_admin_engine, "vehicles", {"tenant_id": lav.tenant, "plate_normalized": "AB123CD"}
        )
        await _alta(
            pg_admin_engine,
            "customers",
            {"tenant_id": lav.tenant, "name": "Ana", "phone_e164": "+5493511234567"},
        )
        turno = await _alta(pg_admin_engine, "bookings", _turno(lav, code="TUR-0001"))
        await _alta(
            pg_admin_engine, "payments", _pago(lav, booking_id=turno, idempotency_key="c-1")
        )


@pytest.mark.parametrize(
    ("tabla", "valores", "indice"),
    [
        ("vehicles", {"plate_normalized": "AB123CD"}, "ux_vehicles_tenant_id_plate_normalized"),
        (
            "customers",
            {"name": "Ana", "phone_e164": "+5493511234567"},
            "ux_customers_tenant_id_phone_e164",
        ),
        ("resources", {"name": "Puesto 9"}, "ux_resources_tenant_id_name"),
    ],
)
async def test_unico_dentro_del_tenant_y_anular_lo_libera(
    pg_admin_engine, lavaderos, tabla, valores, indice
):
    a, _ = lavaderos
    fila = await _alta(pg_admin_engine, tabla, {"tenant_id": a.tenant, **valores})
    await _rechaza(pg_admin_engine, tabla, {"tenant_id": a.tenant, **valores}, indice)
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(_ANULAR.format(tabla=tabla)),
            {"id": fila},
        )
    await _alta(pg_admin_engine, tabla, {"tenant_id": a.tenant, **valores})


async def test_codigo_de_turno_unico_entre_vivos(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    fila = await _alta(pg_admin_engine, "bookings", _turno(a, code="TUR-7"))
    otro_horario = DIEZ + timedelta(days=1)
    await _rechaza(
        pg_admin_engine,
        "bookings",
        _turno(a, otro_horario, code="TUR-7"),
        "ux_bookings_tenant_id_code",
    )
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(_ANULAR.format(tabla="bookings")),
            {"id": fila},
        )
    await _alta(pg_admin_engine, "bookings", _turno(a, otro_horario, code="TUR-7"))


@pytest.mark.parametrize("tabla", ["payments", "cash_movements"])
async def test_idempotency_key_de_dinero_no_se_libera_al_anular(pg_admin_engine, lavaderos, tabla):
    """Un cobro anulado y reenviado por la cola offline NO puede volver a entrar."""
    a, _ = lavaderos
    turno = await _alta(pg_admin_engine, "bookings", _turno(a))
    if tabla == "payments":
        valores = _pago(a, booking_id=turno, idempotency_key="dup")
    else:
        valores = {
            "tenant_id": a.tenant,
            "direction": "ENTRADA",
            "kind": "COBRO",
            "amount_cents": 100,
            "payment_method_id": a.method,
            "occurred_at": DIEZ,
            "actor_user_id": a.user,
            "idempotency_key": "dup",
        }
    fila = await _alta(pg_admin_engine, tabla, valores)
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(_ANULAR.format(tabla=tabla)),
            {"id": fila},
        )
    await _rechaza(
        pg_admin_engine,
        tabla,
        valores | {"id": uuid.uuid4()},
        f"uq_{tabla}_tenant_id_idempotency_key",
    )


async def test_recibir_dos_veces_el_mismo_turno_choca(pg_admin_engine, lavaderos):
    a, _ = lavaderos
    turno = await _alta(pg_admin_engine, "bookings", _turno(a, status="RECIBIDO"))
    await _alta(pg_admin_engine, "jobs", _job(a, booking_id=turno))
    await _rechaza(
        pg_admin_engine, "jobs", _job(a, booking_id=turno), "ux_jobs_tenant_id_booking_id"
    )


# ── X11: sin btree_gist la migración corta ────────────────────────────────────


async def test_sin_btree_gist_la_migracion_corta_con_el_mensaje_del_contrato(pg_admin_engine):
    from alembic.migration import MigrationContext  # noqa: PLC0415
    from alembic.operations import Operations  # noqa: PLC0415

    migracion = _migracion()

    def _upgrade_sin_extension(conn: Connection) -> None:
        # DDL transaccional: el rollback de abajo devuelve la extensión y el EXCLUDE.
        conn.execute(text("DROP EXTENSION btree_gist CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            migracion.upgrade()

    async with pg_admin_engine.connect() as conn:
        trans = await conn.begin()
        try:
            with pytest.raises(RuntimeError) as error:
                await conn.run_sync(_upgrade_sin_extension)
        finally:
            await trans.rollback()
    assert str(error.value) == (
        "falta la extensión btree_gist: correr backend/scripts/create_roles.sh "
        "como superusuario (ver railway.toml)"
    )
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM pg_constraint WHERE conname = :n"), {"n": XC}
            )
            == 1
        )


# ── Enums congelados y ORM contra Postgres ────────────────────────────────────


PYTHON_POR_TIPO: dict[str, type[StrEnum]] = {
    enums.PRICING_MODE_ENUM: enums.PricingMode,
    enums.CHANNEL_ENUM: enums.Channel,
    enums.PLATE_FORMAT_ENUM: enums.PlateFormat,
    enums.BOOKING_SOURCE_ENUM: enums.BookingSource,
    enums.BOOKING_STATUS_ENUM: enums.BookingStatus,
    enums.JOB_STATUS_ENUM: enums.JobStatus,
    enums.JOB_EVENT_TYPE_ENUM: enums.JobEventType,
    enums.DIRT_LEVEL_ENUM: enums.DirtLevel,
    enums.QUOTE_STATUS_ENUM: enums.QuoteStatus,
    enums.SUPPLIES_PURCHASER_ENUM: enums.SuppliesPurchaser,
    enums.CANCELLATION_INITIATOR_ENUM: enums.CancellationInitiator,
    enums.CANCELLATION_CLASSIFICATION_ENUM: enums.CancellationClassification,
    enums.DEPOSIT_STATUS_ENUM: enums.DepositStatus,
    enums.PAYMENT_KIND_ENUM: enums.PaymentKind,
    enums.CASH_DIRECTION_ENUM: enums.CashDirection,
    enums.CASH_MOVEMENT_KIND_ENUM: enums.CashMovementKind,
}


async def test_los_enums_de_la_base_son_los_del_dominio(pg_admin_engine):
    congelados = _migracion().ENUMS
    declarados = {getattr(enums, n) for n in dir(enums) if n.endswith("_ENUM")}
    assert set(congelados) == set(PYTHON_POR_TIPO) == declarados
    async with pg_admin_engine.connect() as conn:
        for tipo, clase in PYTHON_POR_TIPO.items():
            en_base = list(
                (await conn.execute(text(f"SELECT unnest(enum_range(NULL::{tipo}))"))).scalars()
            )
            assert en_base == [m.value for m in clase], tipo
            assert list(congelados[tipo]) == en_base, tipo


async def test_el_alta_por_orm_funciona_como_runtime_con_rls(
    pg_session_factory, pg_tenant_a, pg_tenant_b, pg_user_factory
):
    user = await pg_user_factory(pg_tenant_a, f"orm-{uuid.uuid4()}@example.com")
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, pg_tenant_a)
        ids = await alta_por_orm(session, pg_tenant_a, user)
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, pg_tenant_a)
        pago = await session.scalar(select(Payment).where(Payment.id == ids["payment"]))
        assert pago is not None and pago.kind is PaymentKind.SENA  # el valor con Ñ ida y vuelta
    async with pg_session_factory() as session, session.begin():
        await set_tenant_context(session, pg_tenant_b)
        assert await session.scalar(select(Payment).where(Payment.id == ids["payment"])) is None
