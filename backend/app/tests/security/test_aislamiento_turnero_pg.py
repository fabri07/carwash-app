"""Tester-aislamiento del PR 5.2 (turnero público) contra **Postgres real** (rol `carwash_app`).

Escrito por quien NO implementó el 5.2. Lo que SQLite no puede probar (ver
`test_aislamiento_turnero.py` para el resto de la superficie):

1. **Rollback real por request**: un intento fallido (409/422) no deja la `Idempotency-Key`
   reclamada ni clientes a medio crear. En la suite rápida la sesión es una sola y no hay rollback
   por pedido.
2. **Carreras**: cinco reservas simultáneas al mismo horario con dos puestos (ganan exactamente
   dos); la misma clave con dos WhatsApp distintos (uno solo crea); y el tope de 2 activos con
   pedidos simultáneos del mismo WhatsApp (hallazgo T3-5.2-A, cerrado con un lock por cliente,
   adenda 5.2-13).
3. **RLS con el rol de runtime**: después de reservar sin sesión, `carwash_app` sin contexto no ve
   nada y con el contexto de B no ve nada de A, en cada tabla que toca el turnero; la función
   pública no se deja usar para listar negocios.
4. **HTTP con la función real**: un negocio anulado es el mismo 404 que uno inexistente en todas las
   rutas, y una cookie de otro negocio no cambia a qué negocio pega el slug.

Datos sintéticos (`@example.com`, `+54 9 351 000-xxxx`).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.persistence.db.rls import PUBLIC_TENANT_LOOKUP_FUNCTION
from app.persistence.db.session import get_db_session, make_session_dependency
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.application._armado import Lavadero, armar_catalogo
from app.tests.conftest import cookie_header
from app.tests.conftest_pg import PG_TEST_URL, _as_app_role, _with_driver
from app.utils.cookies import ACCESS_COOKIE
from app.utils.security import create_access_token

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

TZ = ZoneInfo("America/Argentina/Cordoba")
DIA = (datetime.now(TZ) + timedelta(days=2)).date()
SLUG_A, SLUG_B = "aisla-pg-a", "aisla-pg-b"
FN = PUBLIC_TENANT_LOOKUP_FUNCTION
#: Tablas que toca el turnero al reservar, ver o cancelar.
TABLAS_DEL_TURNERO = (
    "bookings",
    "booking_items",
    "customers",
    "vehicles",
    "customer_vehicles",
    "idempotency_keys",
    "cancellations",
    "tenant_settings",
)


def _inicio(hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(DIA, time(hora, minuto), tzinfo=TZ).astimezone(UTC)


async def _abrir(admin, tenant: uuid.UUID, slug: str) -> None:
    async with admin.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET slug = :s WHERE id = :t"), {"s": slug, "t": tenant}
        )
        await conn.execute(
            text(
                "UPDATE tenant_settings SET onboarding_completed_at = now(), "
                "cancellation_policy = 'Cancelá con 12 h.' WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )
        for weekday in range(1, 8):
            await conn.execute(
                text(
                    "INSERT INTO business_hours (id, tenant_id, weekday, opens_at, closes_at) "
                    "VALUES (gen_random_uuid(), :t, :w, '09:00', '18:00')"
                ),
                {"t": tenant, "w": weekday},
            )


@pytest_asyncio.fixture
async def http(pg_admin_engine, pg_clean) -> AsyncGenerator[AsyncClient, None]:
    """La app con un engine de runtime sin pool: cada request, su conexión y su transacción."""
    from app.api.rate_limit import limiter  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    limiter.reset()
    app = create_app()
    app.dependency_overrides[get_db_session] = make_session_dependency(factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        yield ac
    await engine.dispose()


@pytest_asyncio.fixture
async def runtime(pg_admin_engine, pg_clean):
    """Una conexión del rol `carwash_app` por consulta (sin pool): el atacante con la base."""
    engine = create_async_engine(
        _as_app_role(_with_driver(PG_TEST_URL, "asyncpg")), poolclass=NullPool
    )
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def lavaderos(
    pg_engine, pg_session_factory, pg_admin_engine, pg_tenant_a, pg_tenant_b, pg_user_factory
) -> dict[str, Lavadero]:
    await pg_engine.dispose()  # ver `test_turnero_publico_pg`: los enums de 0010 y el pool
    out: dict[str, Lavadero] = {}
    for nombre, tenant, slug in (("a", pg_tenant_a, SLUG_A), ("b", pg_tenant_b, SLUG_B)):
        owner = await pg_user_factory(tenant, f"aisla52-owner-{nombre}@example.com")
        async with pg_session_factory() as s, s.begin():
            out[nombre] = await armar_catalogo(s, tenant, owner)
        await _abrir(pg_admin_engine, tenant, slug)
    return out


def _cuerpo(lav: Lavadero, telefono: str, inicio: datetime, **extra: Any) -> dict[str, Any]:
    cuerpo: dict[str, Any] = {
        "vehicle_size_id": str(lav.auto),
        "service_ids": [str(lav.lavado)],
        "start_at": inicio.isoformat(),
        "name": "Cliente Web",
        "phone": telefono,
        "accept_policy": True,
    }
    cuerpo.update(extra)
    return cuerpo


async def _reservar(http, lav, telefono, inicio=None, slug=SLUG_A, clave=None, **extra):
    return await http.post(
        f"/v1/public/{slug}/bookings",
        json=_cuerpo(lav, telefono, inicio or _inicio(10), **extra),
        headers={"Idempotency-Key": clave or f"web-{uuid.uuid4()}"},
    )


async def _contar(admin, tabla: str, tenant: uuid.UUID | None = None) -> int:
    sql = f"SELECT count(*) FROM {tabla}"
    params: dict[str, Any] = {}
    if tenant is not None:
        sql += " WHERE tenant_id = :t"
        params["t"] = tenant
    async with admin.connect() as conn:
        return int(await conn.scalar(text(sql), params))


# ── 1. Rollback real por request ─────────────────────────────────────────────


async def test_un_intento_fallido_no_quema_la_clave_ni_deja_clientes(
    http, lavaderos, pg_admin_engine
):
    a = lavaderos["a"]
    clientes = await _contar(pg_admin_engine, "customers", a.tenant_id)
    claves = await _contar(pg_admin_engine, "idempotency_keys", a.tenant_id)
    fuera_de_horario = await _reservar(
        http, a, "+54 9 351 000-0501", _inicio(3), clave="clave-reintento"
    )
    assert fuera_de_horario.status_code == 409
    assert fuera_de_horario.json()["detail"]["code"] == "SLOT_TAKEN"
    servicio_malo = await _reservar(
        http, a, "+54 9 351 000-0501", clave="clave-reintento", service_ids=[str(a.tapizado)]
    )
    assert servicio_malo.status_code == 422
    telefono_malo = await _reservar(http, a, "123", clave="clave-reintento")
    assert telefono_malo.status_code == 422
    # Nada quedó: ni la clave ni el cliente del WhatsApp.
    assert await _contar(pg_admin_engine, "customers", a.tenant_id) == clientes
    assert await _contar(pg_admin_engine, "idempotency_keys", a.tenant_id) == claves
    bueno = await _reservar(http, a, "+54 9 351 000-0501", clave="clave-reintento")
    assert bueno.status_code == 201, bueno.text


async def test_el_tope_de_activos_no_deja_ni_turno_ni_vehiculo_a_medias(
    http, lavaderos, pg_admin_engine
):
    a = lavaderos["a"]
    tel = "+54 9 351 000-0601"
    for hora in (9, 11):
        assert (await _reservar(http, a, tel, _inicio(hora))).status_code == 201
    turnos = await _contar(pg_admin_engine, "bookings", a.tenant_id)
    vehiculos = await _contar(pg_admin_engine, "vehicles", a.tenant_id)
    claves = await _contar(pg_admin_engine, "idempotency_keys", a.tenant_id)
    tercero = await _reservar(http, a, tel, _inicio(13), plate="ZZ 999 ZZ")
    assert tercero.status_code == 409
    assert tercero.json()["detail"]["code"] == "TOO_MANY_ACTIVE_BOOKINGS"
    assert await _contar(pg_admin_engine, "bookings", a.tenant_id) == turnos
    assert await _contar(pg_admin_engine, "vehicles", a.tenant_id) == vehiculos
    assert await _contar(pg_admin_engine, "idempotency_keys", a.tenant_id) == claves


# ── 2. Carreras ──────────────────────────────────────────────────────────────


async def test_cinco_reservas_simultaneas_al_mismo_horario_con_dos_puestos_ganan_dos(
    http, lavaderos, pg_admin_engine
):
    a = lavaderos["a"]
    respuestas = await asyncio.gather(
        *(_reservar(http, a, f"+54 9 351 000-07{i:02d}") for i in range(5))
    )
    codigos = sorted(r.status_code for r in respuestas)
    assert codigos == [201, 201, 409, 409, 409], [r.text for r in respuestas]
    for perdedor in (r for r in respuestas if r.status_code == 409):
        assert perdedor.json()["detail"]["code"] == "SLOT_TAKEN"
    async with pg_admin_engine.connect() as conn:
        filas = (
            await conn.execute(
                text(
                    "SELECT resource_id FROM bookings WHERE tenant_id = :t "
                    "AND status <> 'CANCELADO_CLIENTE'"
                ),
                {"t": a.tenant_id},
            )
        ).all()
    assert len(filas) == 2 and len({f.resource_id for f in filas}) == 2
    # Los perdedores no dejaron claves ni turnos: reintentan con la misma clave sin 409 de clave.
    assert await _contar(pg_admin_engine, "idempotency_keys", a.tenant_id) == 2


async def test_la_misma_clave_con_dos_whatsapp_distintos_en_simultaneo_crea_un_solo_turno(
    http, lavaderos, pg_admin_engine
):
    a = lavaderos["a"]
    r1, r2 = await asyncio.gather(
        _reservar(http, a, "+54 9 351 000-0801", clave="clave-disputada"),
        _reservar(http, a, "+54 9 351 000-0802", clave="clave-disputada"),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409], (r1.text, r2.text)
    perdedor = r1 if r1.status_code == 409 else r2
    ganador = r2 if perdedor is r1 else r1
    assert perdedor.json()["detail"]["code"] == "DUPLICATE_IDEMPOTENT"
    assert ganador.json()["code"] not in perdedor.text
    assert await _contar(pg_admin_engine, "bookings", a.tenant_id) == 1


async def test_el_tope_de_dos_activos_aguanta_pedidos_simultaneos_del_mismo_whatsapp(
    http, lavaderos, pg_admin_engine
):
    a = lavaderos["a"]
    tel = "+54 9 351 000-0901"
    respuestas = await asyncio.gather(
        *(_reservar(http, a, tel, _inicio(9 + 2 * i)) for i in range(5))
    )
    creados = [r for r in respuestas if r.status_code == 201]
    assert len(creados) <= 2, f"{len(creados)} turnos activos para el mismo WhatsApp"


# ── 3. RLS con el rol de runtime ─────────────────────────────────────────────


async def test_el_runtime_sin_contexto_no_ve_nada_de_lo_que_reservo_el_publico(
    http, lavaderos, runtime
):
    a = lavaderos["a"]
    r = await _reservar(http, a, "+54 9 351 000-1001", plate="ZZ 999 ZZ", name="Nombre Secreto")
    assert r.status_code == 201, r.text
    codigo = r.json()["code"]
    ver = await http.post(
        f"/v1/public/{SLUG_A}/bookings/lookup", json={"code": codigo, "phone": "+54 9 351 000-1001"}
    )
    assert ver.status_code == 200
    cancelada = await http.post(
        f"/v1/public/{SLUG_A}/bookings/cancel", json={"code": codigo, "phone": "+54 9 351 000-1001"}
    )
    assert cancelada.status_code == 200
    async with runtime() as s, s.begin():
        for tabla in TABLAS_DEL_TURNERO:
            n = await s.scalar(text(f"SELECT count(*) FROM {tabla}"))
            assert n == 0, f"{tabla}: el runtime sin contexto leyó {n} filas"
        assert await s.scalar(text("SELECT count(*) FROM tenants")) == 0


async def test_con_el_contexto_de_b_no_se_ve_nada_de_a_pero_con_el_de_a_si(
    http, lavaderos, runtime
):
    a, b = lavaderos["a"], lavaderos["b"]
    assert (await _reservar(http, a, "+54 9 351 000-1101", plate="ZZ 999 ZZ")).status_code == 201
    assert (await _reservar(http, b, "+54 9 351 000-1102", slug=SLUG_B)).status_code == 201
    async with runtime() as s, s.begin():
        await set_tenant_context(s, b.tenant_id)
        for tabla in ("bookings", "customers", "idempotency_keys", "booking_items"):
            ajenos = await s.scalar(
                text(f"SELECT count(*) FROM {tabla} WHERE tenant_id <> :t"), {"t": b.tenant_id}
            )
            assert ajenos == 0, tabla
        # El cliente web de A y su patente no se ven desde B.
        assert (
            await s.scalar(
                text("SELECT count(*) FROM customers WHERE phone_e164 = '+5493510001101'")
            )
            == 0
        )
        assert (
            await s.scalar(text("SELECT count(*) FROM vehicles WHERE plate_normalized = 'ZZ999ZZ'"))
            == 0
        )
        # Con el contexto de B, un UPDATE sobre A no toca ninguna fila.
        res = await s.execute(
            text("UPDATE bookings SET notes = 'pisado' WHERE tenant_id = :t"), {"t": a.tenant_id}
        )
        assert res.rowcount == 0
    async with runtime() as s, s.begin():
        await set_tenant_context(s, a.tenant_id)
        assert await s.scalar(text("SELECT count(*) FROM bookings")) == 1
        assert await s.scalar(text("SELECT count(*) FROM bookings WHERE notes = 'pisado'")) == 0


async def test_la_funcion_publica_no_sirve_para_listar_negocios(lavaderos, runtime):
    async with runtime() as s, s.begin():
        propio = await s.scalar(text(f"SELECT {FN}(:s)"), {"s": SLUG_A})
        assert propio == lavaderos["a"].tenant_id
        for patron in ("%", "aisla-pg-%", "aisla-pg-_", "", "*", "AISLA-PG-A%"):
            assert await s.scalar(text(f"SELECT {FN}(:s)"), {"s": patron}) is None, patron
        # Devuelve una columna de un uuid: no hay forma de pedirle más filas ni más datos.
        filas = (await s.execute(text(f"SELECT * FROM {FN}(:s)"), {"s": SLUG_A})).all()
        assert len(filas) == 1 and len(filas[0]) == 1


# ── 4. HTTP sobre la función real ────────────────────────────────────────────


async def test_un_negocio_anulado_es_el_mismo_404_que_uno_inexistente_en_todas_las_rutas(
    http, lavaderos, pg_admin_engine
):
    from app.api.rate_limit import limiter  # noqa: PLC0415

    b = lavaderos["b"]
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET voided_at = now(), void_reason = 'DESACTIVADO' WHERE id = :t"),
            {"t": b.tenant_id},
        )
    cuerpo = _cuerpo(b, "+54 9 351 000-1201", _inicio(10))
    peticiones = [
        ("GET", "", None, {}),
        ("GET", "/catalog", None, {}),
        (
            "GET",
            f"/availability?date={DIA}&vehicle_size_id={b.auto}&service_ids={b.lavado}",
            None,
            {},
        ),
        ("POST", "/bookings", cuerpo, {"Idempotency-Key": "anulado-1"}),
        ("POST", "/bookings/lookup", {"code": "TUR-ZZZZZZ", "phone": "+54 9 351 000-1201"}, {}),
        ("POST", "/bookings/cancel", {"code": "TUR-ZZZZZZ", "phone": "+54 9 351 000-1201"}, {}),
    ]
    for metodo, sufijo, json_body, cabeceras in peticiones:
        limiter.reset()
        anulado = await http.request(
            metodo, f"/v1/public/{SLUG_B}{sufijo}", json=json_body, headers=cabeceras
        )
        inexistente = await http.request(
            metodo, f"/v1/public/no-existe-aisla{sufijo}", json=json_body, headers=cabeceras
        )
        assert anulado.status_code == inexistente.status_code == 404, (metodo, sufijo)
        assert anulado.content == inexistente.content
        assert anulado.headers["content-type"] == inexistente.headers["content-type"]
    assert await _contar(pg_admin_engine, "bookings", b.tenant_id) == 0


async def test_una_cookie_de_b_con_el_slug_de_a_crea_el_turno_en_a(
    http, lavaderos, pg_admin_engine, pg_user_factory, pg_tenant_b
):
    a, b = lavaderos["a"], lavaderos["b"]
    owner_b = await pg_user_factory(pg_tenant_b, "aisla52-otro-owner-b@example.com")
    token = create_access_token({"sub": str(owner_b), "tenant_id": str(b.tenant_id), "ver": 0})
    r = await http.post(
        f"/v1/public/{SLUG_A}/bookings",
        json=_cuerpo(a, "+54 9 351 000-1301", _inicio(10)),
        headers={"Idempotency-Key": "con-cookie-de-b", **cookie_header({ACCESS_COOKIE: token})},
    )
    assert r.status_code == 201, r.text
    assert await _contar(pg_admin_engine, "bookings", a.tenant_id) == 1
    assert await _contar(pg_admin_engine, "bookings", b.tenant_id) == 0
    assert await _contar(pg_admin_engine, "customers", b.tenant_id) == 1  # solo el del armado


async def test_el_mismo_whatsapp_en_dos_negocios_no_cruza_turnos_ni_clientes(
    http, lavaderos, pg_admin_engine
):
    a, b = lavaderos["a"], lavaderos["b"]
    tel = "+54 9 351 000-1401"
    de_a = await _reservar(http, a, tel)
    de_b = await _reservar(http, b, tel, slug=SLUG_B)
    assert de_a.status_code == de_b.status_code == 201
    for slug, codigo in ((SLUG_B, de_a.json()["code"]), (SLUG_A, de_b.json()["code"])):
        cancel = await http.post(
            f"/v1/public/{slug}/bookings/cancel", json={"code": codigo, "phone": tel}
        )
        assert cancel.status_code == 404
    async with pg_admin_engine.connect() as conn:
        estados = (
            await conn.execute(
                text("SELECT status FROM bookings WHERE status::text LIKE 'CANCELADO%'")
            )
        ).all()
        clientes = (
            await conn.execute(
                text("SELECT tenant_id FROM customers WHERE phone_e164 = '+5493510001401'")
            )
        ).all()
    assert estados == []
    assert {c.tenant_id for c in clientes} == {a.tenant_id, b.tenant_id}
