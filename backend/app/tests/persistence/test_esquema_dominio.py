"""FASE-3-CONTRATO §1 — el esquema del dominio visto desde la metadata y desde SQLite.

Lo que depende de Postgres (EXCLUDE, CHECKs con regex, RLS, trigger) está en
`test_esquema_dominio_pg.py`. Acá: lo que se puede afirmar sin base real y rápido, más un
alta completa por ORM que prueba que los modelos se pueden usar tal como están declarados.
"""

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.persistence.models  # noqa: F401
from app.domain.enums import (
    BookingSource,
    BookingStatus,
    Channel,
    JobEventType,
    JobStatus,
    PaymentKind,
    PricingMode,
    QuoteStatus,
    SuppliesPurchaser,
)
from app.domain.void import VoidReason
from app.persistence.db.base import Base
from app.persistence.models import (
    Booking,
    BusinessHours,
    Customer,
    Job,
    JobEvent,
    Payment,
    PaymentMethod,
    Quote,
    Resource,
    Service,
    ServicePrice,
    Vehicle,
    VehicleSize,
)
from app.persistence.models.agenda import BLOCKING_STATUSES

#: Postgres trunca en silencio los identificadores de más de 63 bytes (NAMEDATALEN - 1).
PG_MAX_IDENTIFIER = 63

TABLAS = list(Base.metadata.sorted_tables)


def _nombres() -> list[tuple[str, str]]:
    return [(t.name, str(c.name)) for t in TABLAS for c in t.constraints] + [
        (t.name, str(i.name)) for t in TABLAS for i in t.indexes
    ]


def test_ningun_nombre_supera_el_limite_de_postgres():
    largos = [(t, n, len(n.encode())) for t, n in _nombres() if len(n.encode()) > PG_MAX_IDENTIFIER]
    assert not largos, f"Postgres los truncaría y no coincidirían con el ORM: {largos}"


def test_los_nombres_de_indices_no_se_repiten():
    # Postgres comparte el espacio de nombres de índices en el esquema: dos iguales en
    # tablas distintas harían fallar la migración, no el `create_all` de SQLite.
    nombres = [str(i.name) for t in TABLAS for i in t.indexes]
    repetidos = {n for n in nombres if nombres.count(n) > 1}
    assert not repetidos, repetidos


def _cubre(tabla_indices: list[tuple[list[str], bool]], columnas: list[str]) -> bool:
    n = len(columnas)
    return any(set(cols[:n]) == set(columnas) and not parcial for cols, parcial in tabla_indices)


def test_toda_fk_tiene_un_indice_no_parcial_que_la_cubre():
    """Más estricto que B6: un único parcial (`WHERE voided_at IS NULL`) no sirve para el
    chequeo de `RESTRICT` ni para un `JOIN` que incluya filas anuladas."""
    faltan = []
    for tabla in TABLAS:
        indices: list[tuple[list[str], bool]] = [
            ([c.name for c in i.columns], i.dialect_options["postgresql"]["where"] is not None)
            for i in tabla.indexes
        ]
        indices += [
            ([c.name for c in u.columns], False)
            for u in tabla.constraints
            if isinstance(u, UniqueConstraint)
        ]
        indices.append(([c.name for c in tabla.primary_key.columns], False))
        for fk in tabla.foreign_key_constraints:
            columnas = [c.name for c in fk.columns]
            if not _cubre(indices, columnas):
                faltan.append(f"{tabla.name}({', '.join(columnas)})")
    assert not faltan, f"FKs sin índice no parcial: {faltan}"


def test_los_unicos_entre_vivos_usan_la_misma_condicion_en_los_dos_dialectos():
    parciales = [i for t in TABLAS for i in t.indexes if i.unique and i.name.startswith("ux_")]
    assert len(parciales) >= 10
    for indice in parciales:
        pg = str(indice.dialect_options["postgresql"]["where"])
        sqlite = str(indice.dialect_options["sqlite"]["where"])
        assert pg == sqlite, indice.name
        assert "voided_at IS NULL" in pg, f"{indice.name}: anular no liberaría el único (X7)"


def test_job_events_no_es_anulable_ni_actualizable():
    tabla = Base.metadata.tables["job_events"]
    assert not {"updated_at", "voided_at", "void_reason"} & set(tabla.c.keys())
    assert tabla.c.created_at.server_default is not None


def test_los_checks_solo_de_postgres_son_los_que_sqlite_no_entiende():
    solo_pg = {
        str(c.name)
        for t in TABLAS
        for c in t.constraints
        if isinstance(c, CheckConstraint) and c._ddl_if is not None
    }
    assert solo_pg == {
        "ck_tenants_currency_iso",
        "ck_vehicle_sizes_code_formato",
        "ck_payment_methods_code_formato",
        "ck_customers_telefono_e164",
        "ck_vehicles_patente_normalizada",
        "ck_job_events_reversion_con_motivo",
        "ck_cancellations_cierre_con_motivo",
    }


def test_el_exclude_solo_se_emite_en_postgres():
    tabla = Base.metadata.tables["bookings"]
    (exclusion,) = [
        c
        for c in tabla.constraints
        if not isinstance(c, CheckConstraint | ForeignKeyConstraint | UniqueConstraint)
        and c.name == "xc_bookings_sin_solapamiento"
    ]
    assert exclusion._ddl_if is not None and exclusion._ddl_if.dialect == "postgresql"


def test_los_estados_que_bloquean_son_los_del_contrato():
    # La migración 0002 los congela en el WHERE del EXCLUDE: si el dominio cambia, este test
    # avisa que hace falta una migración nueva.
    assert {s.value for s in BLOCKING_STATUSES} == {
        "PENDIENTE_SEÑA",
        "PENDIENTE_COTIZACION",
        "CONFIRMADO",
        "RECIBIDO",
    }


def test_indices_compuestos_con_nombre_explicito():
    # `ix_%(column_0_label)s` daría `ix_<tabla>_tenant_id` a TODOS los compuestos (X5).
    for tabla in TABLAS:
        for indice in tabla.indexes:
            assert isinstance(indice, Index)
            if len(indice.columns) > 1:
                esperado = "_".join(c.name for c in indice.columns)
                assert str(indice.name).endswith(esperado), indice.name


# ── Alta completa por ORM (SQLite) ────────────────────────────────────────────


async def alta_por_orm(
    session: AsyncSession, t: uuid.UUID, user_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """Una fila válida por tabla principal, usando solo defaults del ORM donde los hay.

    La reusa `test_esquema_dominio_pg.py` para correr lo mismo contra Postgres con RLS.
    """
    size = VehicleSize(tenant_id=t, code="AUTO", label="Auto")
    service = Service(tenant_id=t, name="Lavado completo", pricing_mode=PricingMode.PRECIO_FIJO)
    resource = Resource(tenant_id=t, name="Puesto 1")
    method = PaymentMethod(tenant_id=t, code="EFECTIVO", label="Efectivo")
    customer = Customer(tenant_id=t, name="Ana", phone_e164="+5493511234567")
    session.add_all([size, service, resource, method, customer])
    await session.flush()
    vehicle = Vehicle(tenant_id=t, plate="AB 123 CD", plate_normalized="AB123CD")
    price = ServicePrice(
        tenant_id=t,
        service_id=service.id,
        vehicle_size_id=size.id,
        price_cents=2_000_000,
        duration_min=60,
    )
    hours = BusinessHours(tenant_id=t, weekday=1, opens_at=time(9), closes_at=time(13))
    session.add_all([vehicle, price, hours])
    await session.flush()
    start = datetime(2026, 9, 21, 10, tzinfo=UTC)
    booking = Booking(
        tenant_id=t,
        code="TUR-0001",
        source=BookingSource.PANEL,
        channel=Channel.WHATSAPP,
        status=BookingStatus.CONFIRMADO,
        resource_id=resource.id,
        start_at=start,
        end_at=start + timedelta(minutes=60),
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        service_id=service.id,
        vehicle_size_id=size.id,
        service_name_snapshot=service.name,
        duration_min=60,
        price_cents=2_000_000,
    )
    session.add(booking)
    await session.flush()
    quote = Quote(
        tenant_id=t,
        customer_id=customer.id,
        service_id=service.id,
        vehicle_size_id=size.id,
        status=QuoteStatus.PENDIENTE,
        requested_at=start,
    )
    job = Job(
        tenant_id=t,
        booking_id=booking.id,
        customer_id=customer.id,
        vehicle_id=vehicle.id,
        vehicle_size_id=size.id,
        service_id=service.id,
        responsible_user_id=user_id,
        channel=Channel.WHATSAPP,
        status=JobStatus.PRESENTE,
        service_name_snapshot=service.name,
        base_price_cents=2_000_000,
        scheduled_at=start,
        arrived_at=start + timedelta(minutes=5),
        arrival_delay_min=5,
    )
    session.add_all([quote, job])
    await session.flush()
    event = JobEvent(
        tenant_id=t,
        job_id=job.id,
        event_type=JobEventType.JOB_RECEIVED,
        to_status=JobStatus.PRESENTE,
        occurred_at=job.arrived_at,
        actor_user_id=user_id,
        idempotency_key="k-1",
    )
    payment = Payment(
        tenant_id=t,
        job_id=job.id,
        booking_id=booking.id,
        kind=PaymentKind.SENA,
        amount_cents=2_000_000,
        payment_method_id=method.id,
        commission_bps=0,
        commission_cents=0,
        occurred_at=start,
        actor_user_id=user_id,
        idempotency_key="p-1",
    )
    session.add_all([event, payment])
    await session.flush()
    return {
        "booking": booking.id,
        "job": job.id,
        "quote": quote.id,
        "event": event.id,
        "payment": payment.id,
    }


async def test_el_grafo_del_dominio_se_da_de_alta_por_orm(db_session, tenant_a, owner):
    ids = await alta_por_orm(db_session, tenant_a.id, owner.id)
    event = await db_session.get(JobEvent, ids["event"])
    assert event is not None and event.event_metadata == {}
    quote = await db_session.get(Quote, ids["quote"])
    assert quote is not None and quote.supplies_purchaser is SuppliesPurchaser.A_DEFINIR
    job = await db_session.get(Job, ids["job"])
    assert job is not None and (job.surcharge_cents, job.discount_cents) == (0, 0)
    assert tenant_a.timezone == "America/Argentina/Cordoba" and tenant_a.currency == "ARS"


async def test_sqlite_aplica_los_checks_portables(db_session, tenant_a, owner):
    await alta_por_orm(db_session, tenant_a.id, owner.id)
    method = PaymentMethod(tenant_id=tenant_a.id, code="DEBITO", label="Débito")
    db_session.add(method)
    await db_session.flush()
    huerfano = Payment(
        tenant_id=tenant_a.id,
        kind=PaymentKind.SENA,
        amount_cents=100,
        payment_method_id=method.id,
        commission_bps=0,
        commission_cents=0,
        occurred_at=datetime.now(UTC),
        actor_user_id=owner.id,
        idempotency_key="huerfano",
    )
    with pytest.raises(IntegrityError, match="con_job_o_turno"):
        async with db_session.begin_nested():
            db_session.add(huerfano)
            await db_session.flush()


async def test_sqlite_anular_libera_el_unico_entre_vivos(db_session, tenant_a):
    primero = Resource(tenant_id=tenant_a.id, name="Puesto 1")
    db_session.add(primero)
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(Resource(tenant_id=tenant_a.id, name="Puesto 1"))
            await db_session.flush()
    primero.voided_at = datetime.now(UTC)
    primero.void_reason = VoidReason.ERROR_DE_CARGA
    await db_session.flush()
    db_session.add(Resource(tenant_id=tenant_a.id, name="Puesto 1"))
    await db_session.flush()
