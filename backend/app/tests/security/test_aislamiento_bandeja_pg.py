"""Tester-aislamiento del PR 5.3 (bandeja del dueño) contra **Postgres real**, como `carwash_app`.

Escrito por quien NO implementó el 5.3. Lo que SQLite no puede probar: RLS con el rol de runtime,
el `EXCLUDE` y las carreras entre transacciones de verdad. Complementa
`test_aislamiento_bandeja.py` (HTTP, SQLite) y `application/test_bandeja_pg.py` (servicios).

1. **HTTP con RLS real**: B recorre todas las rutas con `{id}` de la bandeja contra los recursos de
   A (turno, pedido en revisión, caso de seña, marca de incidencia) y recibe el 404 del id al azar;
   el testigo (superusuario) ve las filas de A sin cambios.
2. **SQL directo con `carwash_app`** sobre las tablas que toca la bandeja (`bookings`,
   `booking_items`, `cancellations`, `customer_incident_marks`, `payments`, `idempotency_keys`):
   con el contexto de B o sin contexto, leer/actualizar/borrar lo de A afecta 0 filas; insertar con
   el `tenant_id` de A o colgar una marca de un turno de A es un error.
3. **Carreras** (conexiones distintas, en paralelo): aprobar dos veces, aprobar contra rechazar,
   confirmar la seña dos veces (misma clave y claves distintas), reprogramar dos turnos al mismo
   horario, resolver la misma seña dos veces con devolución → un solo ganador, un solo pago.
4. **La clave no se quema**: una acción que falla (409) no consume su `Idempotency-Key`.

Datos sintéticos (`@example.com`, `+54 9 351 000-xxxx`).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.domain.enums import BookingStatus
from app.persistence.db.session import get_db_session, make_session_dependency
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models import Booking
from app.tests.application._armado import SENA, Lavadero, armar_catalogo
from app.tests.application._bandeja import en_revision
from app.tests.conftest import cookie_header
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

Factory = async_sessionmaker[AsyncSession]
AHORA = datetime.now(UTC)
#: Dentro de tres días, a una hora cualquiera: el horario del negocio no interviene (la
#: reprogramación fuera de reglas se guarda igual, `outside_rules`).
INICIO = (AHORA + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)

TABLAS_BANDEJA = (
    "bookings",
    "booking_items",
    "cancellations",
    "customer_incident_marks",
    "payments",
    "idempotency_keys",
)


def _sesion(user: uuid.UUID, tenant: uuid.UUID) -> dict[str, str]:
    token = create_access_token({"sub": str(user), "tenant_id": str(tenant), "ver": 0})
    return cookie_header({ACCESS_COOKIE: token})


def _clave() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{uuid.uuid4()}"}


@pytest_asyncio.fixture
async def fabrica(pg_admin_engine, pg_engine) -> AsyncGenerator[Factory, None]:
    # Los tests de la 0010 recrean el enum `service_booking_mode`: el pool compartido puede tener
    # el OID viejo.
    await pg_engine.dispose()
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def http(fabrica: Factory, pg_clean) -> AsyncGenerator[AsyncClient, None]:
    """La app con una sesión (y conexión) nueva por pedido: permite pedidos en paralelo."""
    from app.api.rate_limit import limiter  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    limiter.reset()
    app = create_app()
    app.dependency_overrides[get_db_session] = make_session_dependency(fabrica)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def dos(fabrica: Factory, pg_tenant_a, pg_tenant_b, pg_user_factory) -> dict[str, Any]:
    owner_a = await pg_user_factory(pg_tenant_a, "bandeja-owner-a@example.com")
    owner_b = await pg_user_factory(pg_tenant_b, "bandeja-owner-b@example.com")
    async with fabrica() as s, s.begin():
        a = await armar_catalogo(s, pg_tenant_a, owner_a)
    async with fabrica() as s, s.begin():
        b = await armar_catalogo(s, pg_tenant_b, owner_b)
    return {
        "a": a,
        "b": b,
        "ha": _sesion(owner_a, pg_tenant_a),
        "hb": _sesion(owner_b, pg_tenant_b),
        "ta": pg_tenant_a,
        "tb": pg_tenant_b,
    }


async def _en_tx[R](fabrica: Factory, lv: Lavadero, fn: Callable[[Lavadero], Awaitable[R]]) -> R:
    async with fabrica() as s, s.begin():
        return await fn(lv.en(s))


async def _conteos(admin: AsyncEngine, tenant: uuid.UUID) -> dict[str, int]:
    async with admin.connect() as conn:
        return {
            t: await conn.scalar(
                text(f"SELECT count(*) FROM {t} WHERE tenant_id = :t"),  # noqa: S608
                {"t": tenant},
            )
            for t in TABLAS_BANDEJA
        }


async def _foto_de_a(admin: AsyncEngine, tenant: uuid.UUID) -> dict[str, Any]:
    """Todo lo de A en las tablas de la bandeja, como texto ordenado: el testigo."""
    async with admin.connect() as conn:
        return {
            t: (
                await conn.execute(
                    text(  # noqa: S608
                        f"SELECT coalesce(json_agg(x ORDER BY x::text), '[]'::json)::text "
                        f"FROM {t} x WHERE tenant_id = :t"
                    ),
                    {"t": tenant},
                )
            ).scalar()
            for t in TABLAS_BANDEJA
        }


async def _escenario_a(
    fabrica: Factory, a: Lavadero, http: AsyncClient, ha: dict
) -> dict[str, Any]:
    """Un turno, un pedido en revisión, un caso de seña abierto y una marca de incidencia."""
    turno = await _en_tx(fabrica, a, lambda lv: lv.turno(inicio=INICIO, ahora=AHORA))
    pedido = await _en_tx(
        fabrica, a, lambda lv: en_revision(lv, inicio=INICIO + timedelta(hours=3), ahora=AHORA)
    )

    async def _caso(lv: Lavadero):
        t = await lv.turno_con_sena(inicio=INICIO + timedelta(hours=6), ahora=AHORA)
        await lv.turnos.confirm_deposit(t.id, lv.pago(SENA), now=AHORA)
        return t, await lv.turnos.cancel_operational(t.id, requested_at=AHORA, reason="Lluvia")

    con_sena, caso = await _en_tx(fabrica, a, _caso)
    marca = await http.post(
        f"/v1/bookings/{turno.id}/incident-marks", json={"reason": "x"}, headers={**ha, **_clave()}
    )
    assert marca.status_code == 201, marca.text
    return {
        "turno": turno,
        "pedido": pedido,
        "con_sena": con_sena,
        "caso": caso,
        "marca": marca.json()["id"],
    }


# ── 1 · HTTP con RLS real ────────────────────────────────────────────────────


async def test_b_recorre_toda_la_bandeja_de_a_y_no_mueve_una_fila(
    http, pg_admin_engine, fabrica, dos
):
    a, b, ha, hb = dos["a"], dos["b"], dos["ha"], dos["hb"]
    e = await _escenario_a(fabrica, a, http, ha)
    antes = await _foto_de_a(pg_admin_engine, dos["ta"])
    assert (await http.get("/v1/bookings", headers=hb)).json()["total"] == 0
    assert (await http.get("/v1/cancellations", headers=hb)).json()["total"] == 0
    contador = (await http.get("/v1/bookings/counts", headers=hb)).json()
    assert contador["total"] == 0 and contador["deposit_cases"] == 0

    intentos: list[tuple[str, str, Any, uuid.UUID]] = [
        ("GET", "/v1/bookings/{}", None, e["turno"].id),
        ("POST", "/v1/bookings/{}/cancel", {"reason": "x"}, e["turno"].id),
        ("POST", "/v1/bookings/{}/no-show", None, e["turno"].id),
        ("POST", "/v1/bookings/{}/reschedule", {"start_at": INICIO.isoformat()}, e["turno"].id),
        ("POST", "/v1/bookings/{}/reschedule", {"resource_id": str(b.puesto)}, e["turno"].id),
        ("POST", "/v1/bookings/{}/incident-marks", {"reason": "x"}, e["turno"].id),
        ("POST", "/v1/bookings/{}/approve", None, e["pedido"].id),
        ("POST", "/v1/bookings/{}/reject", {"reason": "x"}, e["pedido"].id),
        (
            "POST",
            "/v1/bookings/{}/confirm-deposit",
            {"payment_method_id": str(b.efectivo), "amount_cents": SENA},
            e["con_sena"].id,
        ),
        ("DELETE", "/v1/incident-marks/{}", None, uuid.UUID(e["marca"])),
        (
            "POST",
            "/v1/cancellations/{}/resolve",
            {"status": "REPROGRAMADA", "reason": "x"},
            e["caso"].id,
        ),
        (
            "POST",
            "/v1/cancellations/{}/resolve",
            {
                "status": "DEVUELTA",
                "reason": "x",
                "refund": {"payment_method_id": str(b.efectivo), "amount_cents": SENA},
            },
            e["caso"].id,
        ),
    ]
    for metodo, ruta, cuerpo, id_a in intentos:
        azar = uuid.uuid4()
        ajeno = await http.request(
            metodo, ruta.format(id_a), json=cuerpo, headers={**hb, **_clave()}
        )
        nada = await http.request(
            metodo, ruta.format(azar), json=cuerpo, headers={**hb, **_clave()}
        )
        assert ajeno.status_code == nada.status_code == 404, (metodo, ruta, ajeno.text)
        assert ajeno.content.replace(str(id_a).encode(), b"<id>") == nada.content.replace(
            str(azar).encode(), b"<id>"
        ), (metodo, ruta)
    assert await _foto_de_a(pg_admin_engine, dos["ta"]) == antes
    # B no dejó ni un rastro propio (ni claves de idempotencia consumidas por pedidos fallidos).
    assert set((await _conteos(pg_admin_engine, dos["tb"])).values()) == {0}


async def test_el_dueno_de_a_si_ve_lo_suyo_control(http, fabrica, dos):
    e = await _escenario_a(fabrica, dos["a"], http, dos["ha"])
    r = await http.get(f"/v1/bookings/{e['turno'].id}", headers=dos["ha"])
    assert r.status_code == 200, r.text
    assert (await http.get("/v1/cancellations", headers=dos["ha"])).json()["total"] == 1


# ── 2 · SQL directo con el rol de runtime ────────────────────────────────────


async def _tocar(fabrica: Factory, contexto: uuid.UUID | None, sql: str) -> None:
    """Corre `sql` como `carwash_app`: o no tiene el GRANT (error) o no alcanza ninguna fila."""
    try:
        async with fabrica() as s, s.begin():
            if contexto is not None:
                await set_tenant_context(s, contexto)
            r: Any = await s.execute(text(sql))
            assert r.rowcount == 0, (sql, contexto)
    except DBAPIError:
        pass


async def test_con_el_contexto_de_b_o_sin_contexto_lo_de_a_no_se_lee_ni_se_toca(
    http, fabrica, pg_admin_engine, dos
):
    await _escenario_a(fabrica, dos["a"], http, dos["ha"])
    antes = await _foto_de_a(pg_admin_engine, dos["ta"])
    sembrado = await _conteos(pg_admin_engine, dos["ta"])
    assert all(n > 0 for n in sembrado.values()), sembrado
    for contexto in (dos["tb"], None):
        for tabla in TABLAS_BANDEJA:
            async with fabrica() as s, s.begin():
                if contexto is not None:
                    await set_tenant_context(s, contexto)
                visibles = await s.scalar(text(f"SELECT count(*) FROM {tabla}"))  # noqa: S608
                assert visibles == 0, (tabla, contexto)
            await _tocar(fabrica, contexto, f"DELETE FROM {tabla}")  # noqa: S608
            col = "key" if tabla == "idempotency_keys" else "created_at"
            await _tocar(fabrica, contexto, f"UPDATE {tabla} SET {col} = {col}")  # noqa: S608
    assert await _foto_de_a(pg_admin_engine, dos["ta"]) == antes


async def test_b_no_inserta_una_marca_de_incidencia_en_a_ni_cuelga_la_suya_de_un_turno_de_a(
    http, fabrica, pg_admin_engine, dos
):
    e = await _escenario_a(fabrica, dos["a"], http, dos["ha"])
    marca = (
        "INSERT INTO customer_incident_marks "
        "(id, tenant_id, customer_id, booking_id, reason, created_by_user_id) "
        "VALUES (gen_random_uuid(), :t, :c, :b, 'x', :u)"
    )
    cliente_a, cliente_b = dos["a"].cliente, dos["b"].cliente
    owner_b = dos["b"].owner_id
    casos = [
        # tenant de A estando en el contexto de B
        {"t": dos["ta"], "c": cliente_a, "b": e["turno"].id, "u": owner_b},
        # tenant de B con el cliente y el turno de A (FK compuesta)
        {"t": dos["tb"], "c": cliente_a, "b": e["turno"].id, "u": owner_b},
        {"t": dos["tb"], "c": cliente_b, "b": e["turno"].id, "u": owner_b},
        {"t": dos["tb"], "c": cliente_a, "b": None, "u": owner_b},
    ]
    for params in casos:
        with pytest.raises(DBAPIError):
            async with fabrica() as s, s.begin():
                await set_tenant_context(s, dos["tb"])
                await s.execute(text(marca), params)
    assert (await _conteos(pg_admin_engine, dos["tb"]))["customer_incident_marks"] == 0


async def test_b_no_inserta_una_cancelacion_ni_un_pago_sobre_un_turno_de_a(
    http, fabrica, pg_admin_engine, dos
):
    e = await _escenario_a(fabrica, dos["a"], http, dos["ha"])
    for sql, params in (
        (
            "INSERT INTO cancellations (id, tenant_id, booking_id) "
            "VALUES (gen_random_uuid(), :t, :b)",
            {"t": dos["tb"], "b": e["turno"].id},
        ),
        (
            "INSERT INTO payments (id, tenant_id, booking_id, amount_cents, payment_method_id, "
            "commission_bps, commission_cents, occurred_at, kind, idempotency_key) "
            "VALUES (gen_random_uuid(), :t, :b, 1, :m, 0, 0, now(), 'SENA', 'k-sql')",
            {"t": dos["tb"], "b": e["turno"].id, "m": dos["b"].efectivo},
        ),
    ):
        with pytest.raises(DBAPIError):
            async with fabrica() as s, s.begin():
                await set_tenant_context(s, dos["tb"])
                await s.execute(text(sql), params)
    c = await _conteos(pg_admin_engine, dos["tb"])
    assert c["cancellations"] == c["payments"] == 0


# ── 3 · Carreras ─────────────────────────────────────────────────────────────


async def _pedido_futuro(fabrica: Factory, lv: Lavadero, horas: int = 0):
    return await _en_tx(
        fabrica, lv, lambda x: en_revision(x, inicio=INICIO + timedelta(hours=horas), ahora=AHORA)
    )


async def _estado(fabrica: Factory, lv: Lavadero, booking: uuid.UUID) -> BookingStatus:
    return await _en_tx(fabrica, lv, lambda x: _status(x, booking))


async def _status(lv: Lavadero, booking: uuid.UUID) -> BookingStatus:
    return (await lv.turnos.get(booking)).status


async def test_aprobar_dos_veces_a_la_vez_sin_clave_gana_uno(http, fabrica, dos):
    pedido = await _pedido_futuro(fabrica, dos["a"])
    rs = await asyncio.gather(
        *[http.post(f"/v1/bookings/{pedido.id}/approve", headers=dos["ha"]) for _ in range(4)]
    )
    codigos = sorted(r.status_code for r in rs)
    assert codigos.count(200) == 1, [r.text for r in rs]
    assert set(codigos) <= {200, 409}, [r.text for r in rs]
    assert await _estado(fabrica, dos["a"], pedido.id) in {
        BookingStatus.CONFIRMADO,
        BookingStatus.PENDIENTE_SENA,
    }


async def test_aprobar_dos_veces_a_la_vez_con_la_misma_clave_gana_uno_y_el_otro_es_duplicate(
    http, fabrica, dos
):
    pedido = await _pedido_futuro(fabrica, dos["a"])
    cab = {**dos["ha"], "Idempotency-Key": "k-aprobar"}
    rs = await asyncio.gather(
        *[http.post(f"/v1/bookings/{pedido.id}/approve", headers=cab) for _ in range(4)]
    )
    assert sorted(r.status_code for r in rs).count(200) == 1, [r.text for r in rs]
    assert all(r.status_code in {200, 409} for r in rs), [r.text for r in rs]


async def test_aprobar_contra_rechazar_a_la_vez_deja_un_estado_coherente(
    http, pg_admin_engine, fabrica, dos
):
    pedido = await _pedido_futuro(fabrica, dos["a"])
    ra, rr = await asyncio.gather(
        http.post(f"/v1/bookings/{pedido.id}/approve", headers=dos["ha"]),
        http.post(f"/v1/bookings/{pedido.id}/reject", json={"reason": "x"}, headers=dos["ha"]),
    )
    estado = await _estado(fabrica, dos["a"], pedido.id)
    async with pg_admin_engine.connect() as conn:
        rechazos = await conn.scalar(
            text("SELECT count(*) FROM cancellations WHERE booking_id = :b"), {"b": pedido.id}
        )
    if estado == BookingStatus.RECHAZADO:
        assert rr.status_code == 200 and ra.status_code == 409, (ra.text, rr.text)
        assert rechazos == 1
    else:
        assert ra.status_code == 200 and rr.status_code == 409, (ra.text, rr.text)
        assert estado in {BookingStatus.CONFIRMADO, BookingStatus.PENDIENTE_SENA}
        assert rechazos == 0, "un pedido aprobado no puede tener además su fila de rechazo"


async def test_confirmar_la_sena_a_la_vez_con_la_misma_clave_registra_un_solo_pago(
    http, pg_admin_engine, fabrica, dos
):
    a = dos["a"]
    turno = await _en_tx(
        fabrica, a, lambda lv: lv.turno_con_sena(inicio=INICIO, ahora=datetime.now(UTC))
    )
    cab = {**dos["ha"], "Idempotency-Key": "k-sena"}
    cuerpo = {"payment_method_id": str(a.efectivo)}
    rs = await asyncio.gather(
        *[
            http.post(f"/v1/bookings/{turno.id}/confirm-deposit", json=cuerpo, headers=cab)
            for _ in range(5)
        ]
    )
    assert all(r.status_code in {200, 409} for r in rs), [r.text for r in rs]
    assert any(r.status_code == 200 for r in rs)
    async with pg_admin_engine.connect() as conn:
        pagos = await conn.scalar(
            text("SELECT count(*) FROM payments WHERE booking_id = :b"), {"b": turno.id}
        )
    assert pagos == 1
    # y un reenvío posterior sigue siendo el mismo turno, 200.
    otra = await http.post(f"/v1/bookings/{turno.id}/confirm-deposit", json=cuerpo, headers=cab)
    assert otra.status_code == 200, otra.text


async def test_confirmar_la_sena_a_la_vez_con_claves_distintas_cobra_una_sola_vez(
    http, pg_admin_engine, fabrica, dos
):
    a = dos["a"]
    turno = await _en_tx(
        fabrica, a, lambda lv: lv.turno_con_sena(inicio=INICIO, ahora=datetime.now(UTC))
    )
    cuerpo = {"payment_method_id": str(a.efectivo)}
    rs = await asyncio.gather(
        *[
            http.post(
                f"/v1/bookings/{turno.id}/confirm-deposit",
                json=cuerpo,
                headers={**dos["ha"], **_clave()},
            )
            for _ in range(4)
        ]
    )
    assert sorted(r.status_code for r in rs).count(200) == 1, [r.text for r in rs]
    async with pg_admin_engine.connect() as conn:
        pagos = await conn.scalar(
            text("SELECT count(*) FROM payments WHERE booking_id = :b"), {"b": turno.id}
        )
    assert pagos == 1, "doble cobro de la seña por una carrera"


async def _carrera_de_reprogramaciones(
    http: AsyncClient, pg_admin_engine: AsyncEngine, fabrica: Factory, dos: dict[str, Any], n: int
) -> tuple[list[int], int]:
    """`n` rondas: dos turnos del mismo puesto se reprograman a la vez al MISMO horario libre.
    Devuelve los códigos de todas las respuestas y la cantidad de solapes que quedaron."""
    a = dos["a"]
    codigos: list[int] = []
    for ronda in range(n):
        destino = INICIO + timedelta(days=100 + ronda)

        async def _dos_turnos(lv: Lavadero, r: int = ronda) -> tuple[Booking, Booking]:
            uno = await lv.turno(
                inicio=INICIO + timedelta(days=30, hours=r * 3), ahora=AHORA, puesto=lv.puesto
            )
            otro = await lv.turno(
                inicio=INICIO + timedelta(days=60, hours=r * 3), ahora=AHORA, puesto=lv.puesto
            )
            return uno, otro

        t1, t2 = await _en_tx(fabrica, a, _dos_turnos)
        cuerpo = {"start_at": destino.isoformat(), "resource_id": str(a.puesto)}
        r1, r2 = await asyncio.gather(
            http.post(f"/v1/bookings/{t1.id}/reschedule", json=cuerpo, headers=dos["ha"]),
            http.post(f"/v1/bookings/{t2.id}/reschedule", json=cuerpo, headers=dos["ha"]),
        )
        codigos += [r1.status_code, r2.status_code]
        assert 200 in (r1.status_code, r2.status_code), (r1.text, r2.text)
    async with pg_admin_engine.connect() as conn:
        solapes = await conn.scalar(
            text(
                "SELECT count(*) FROM bookings x JOIN bookings y "
                "ON x.resource_id = y.resource_id AND x.id < y.id "
                "AND tstzrange(x.start_at, x.end_at) && tstzrange(y.start_at, y.end_at) "
                "WHERE x.tenant_id = :t AND x.status IN ('CONFIRMADO','PENDIENTE_SEÑA') "
                "AND y.status IN ('CONFIRMADO','PENDIENTE_SEÑA')"
            ),
            {"t": dos["ta"]},
        )
    return codigos, solapes


async def test_reprogramar_a_la_vez_al_mismo_horario_nunca_solapa_ni_gana_dos(
    http, pg_admin_engine, fabrica, dos
):
    """La invariante de datos (el `EXCLUDE` manda), sea cual sea la respuesta del perdedor."""
    codigos, solapes = await _carrera_de_reprogramaciones(http, pg_admin_engine, fabrica, dos, 6)
    assert solapes == 0
    assert set(codigos) <= {200, 409, 500}, codigos
    # Por ronda hay a lo sumo un 200 (dos 200 al mismo puesto y horario serían un solape).
    assert codigos.count(200) == len(codigos) // 2, codigos


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "HALLAZGO T3 5.3 (media): dos reprogramaciones concurrentes al mismo puesto y horario "
        "chocan en el EXCLUDE de `bookings` con DEADLOCK (40P01: ambos esperan la fila sin "
        "commitear del otro) y el perdedor recibe 500 INTERNAL_ERROR en vez de 409 SLOT_TAKEN. "
        "`guarded_savepoint(_OVERLAP)` solo traduce la violación 23P01, no 40P01. Sin pérdida ni "
        "solape de datos. Arreglo posible: traducir 40P01 a SlotTakenError (o reintentar) en "
        "`reschedule`/`approve_review`."
    ),
)
async def test_el_perdedor_de_una_carrera_de_reprogramacion_recibe_409_no_500(
    http, pg_admin_engine, fabrica, dos
):
    codigos, _ = await _carrera_de_reprogramaciones(http, pg_admin_engine, fabrica, dos, 40)
    assert 500 not in codigos, f"respuestas: {codigos}"
    assert codigos.count(409) == codigos.count(200), codigos


async def test_resolver_la_misma_sena_a_la_vez_con_devolucion_paga_una_sola_vez(
    http, pg_admin_engine, fabrica, dos
):
    a = dos["a"]

    async def _caso(lv: Lavadero):
        t = await lv.turno_con_sena(inicio=INICIO, ahora=AHORA)
        await lv.turnos.confirm_deposit(t.id, lv.pago(SENA), now=AHORA)
        return t, await lv.turnos.cancel_operational(t.id, requested_at=AHORA, reason="Lluvia")

    turno, caso = await _en_tx(fabrica, a, _caso)
    cuerpo = {
        "status": "DEVUELTA",
        "reason": "Transferido",
        "refund": {"payment_method_id": str(a.efectivo), "amount_cents": SENA},
    }
    rs = await asyncio.gather(
        *[
            http.post(
                f"/v1/cancellations/{caso.id}/resolve",
                json=cuerpo,
                headers={**dos["ha"], **_clave()},
            )
            for _ in range(4)
        ]
    )
    assert all(r.status_code in {200, 409} for r in rs), [r.text for r in rs]
    async with pg_admin_engine.connect() as conn:
        devoluciones = await conn.scalar(
            text("SELECT count(*) FROM payments WHERE booking_id = :b AND kind = 'DEVOLUCION'"),
            {"b": turno.id},
        )
    assert devoluciones == 1, "doble devolución de la seña por una carrera"


# ── 4 · La clave no se quema por un error ────────────────────────────────────


async def test_una_accion_que_falla_no_consume_su_clave(http, fabrica, dos):
    a = dos["a"]
    ocupante = await _en_tx(
        fabrica,
        a,
        lambda lv: lv.turno(inicio=INICIO + timedelta(days=1), ahora=AHORA, puesto=lv.puesto),
    )
    turno = await _en_tx(
        fabrica, a, lambda lv: lv.turno(inicio=INICIO, ahora=AHORA, puesto=lv.puesto)
    )
    cab = {**dos["ha"], "Idempotency-Key": "k-reintento"}
    choca = await http.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={
            "start_at": (ocupante.start_at + timedelta(minutes=30)).isoformat(),
            "resource_id": str(a.puesto),
        },
        headers=cab,
    )
    assert choca.status_code == 409 and choca.json()["detail"]["code"] == "SLOT_TAKEN", choca.text
    libre = await http.post(
        f"/v1/bookings/{turno.id}/reschedule",
        json={"start_at": (INICIO + timedelta(days=5)).isoformat(), "resource_id": str(a.puesto)},
        headers=cab,
    )
    assert libre.status_code == 200, libre.text  # no DUPLICATE_IDEMPOTENT


async def test_la_sena_que_falla_no_consume_su_clave_de_pago(http, pg_admin_engine, fabrica, dos):
    """El pago con importe inválido (409/422) no puede dejar la clave tomada: el reintento con el
    importe bueno, misma clave, cobra."""
    a = dos["a"]
    turno = await _en_tx(
        fabrica, a, lambda lv: lv.turno_con_sena(inicio=INICIO, ahora=datetime.now(UTC))
    )
    cab = {**dos["ha"], "Idempotency-Key": "k-pago-malo"}
    malo = await http.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json={"payment_method_id": str(a.efectivo), "amount_cents": -5},
        headers=cab,
    )
    assert malo.status_code in {409, 422}, malo.text
    bueno = await http.post(
        f"/v1/bookings/{turno.id}/confirm-deposit",
        json={"payment_method_id": str(a.efectivo)},
        headers=cab,
    )
    assert bueno.status_code == 200, bueno.text
    async with pg_admin_engine.connect() as conn:
        pagos = await conn.scalar(
            text("SELECT count(*) FROM payments WHERE booking_id = :b"), {"b": turno.id}
        )
    assert pagos == 1
