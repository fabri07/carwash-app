"""Tester-aislamiento del PR 5.2 (turnero público) — FASE-5-CONTRATO §4.2, §6 y adenda §13.

Escrito por quien NO implementó el 5.2. Ataca `/v1/public/{slug}/*` **sin sesión**, sobre SQLite
(suite rápida). La carrera real, el `EXCLUDE` y el rol `carwash_app` con RLS están en
`test_aislamiento_turnero_pg.py`.

Cubre: enumeración de slugs (cuerpos, cabeceras y cantidad de consultas idénticos), límites
rotando slugs y `X-Forwarded-For`, ver/cancelar el turno de otro (variantes de WhatsApp y de
código, códigos de otro negocio), tope de 2 activos con variantes del mismo teléfono, nombre y
patente de un cliente existente, servicios y tamaños no reservables, horarios imposibles,
`Idempotency-Key`, inyección en los campos de texto, fuga de PII en respuestas y logs, y
CORS/cabeceras.

Datos sintéticos: `+54 9 351 000-xxxx`, patentes `ZZ999ZZ`, emails `@example.com`.
"""

import json
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from structlog.testing import capture_logs

from app.api.rate_limit import limiter
from app.api.v1.public import slug_key
from app.domain.enums import BookingStatus, ServiceBookingMode
from app.domain.roles import Role
from app.domain.void import VoidReason
from app.persistence.db.tenant_context import set_tenant_context
from app.persistence.models import Booking, Customer, Service, TenantSettings
from app.persistence.models.customer import CustomerVehicle, Vehicle
from app.persistence.models.idempotency_key import IdempotencyKey
from app.persistence.models.tenant import Tenant
from app.tests.application._armado import Lavadero, armar_catalogo
from app.tests.conftest import make_tenant, make_user

TZ = ZoneInfo("America/Argentina/Cordoba")
DIA = (datetime.now(TZ) + timedelta(days=2)).date()
TEL = "+54 9 351 000-0001"
TEL_E164 = "+5493510000001"
OTRO_TEL = "+54 9 351 000-0002"
PATENTE = "ZZ999ZZ"
SLUG_A = "aisla-norte"
SLUG_B = "aisla-sur"
#: Cabeceras que cambian por request y no dicen nada del negocio.
_VOLATILES = {"x-request-id", "x-trace-id", "date", "content-length"}


def _inicio(hora: int, minuto: int = 0, dia=DIA) -> str:
    return datetime.combine(dia, time(hora, minuto), tzinfo=TZ).astimezone(UTC).isoformat()


def _codigo(r) -> str:
    return r.json()["detail"]["code"]


async def _abrir(session, lav: Lavadero, *, slug: str) -> None:
    await set_tenant_context(session, lav.tenant_id)
    tenant = await session.get(Tenant, lav.tenant_id)
    tenant.slug = slug
    settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == lav.tenant_id)
    )
    settings.onboarding_completed_at = datetime.now(UTC)
    settings.cancellation_policy = "Cancelá con 12 h de anticipación."
    await session.flush()
    for weekday in range(1, 8):
        await lav.catalogo.add_business_hours(weekday, time(9, 0), time(18, 0))


@pytest_asyncio.fixture
async def lav(db_session, tenant_a, owner) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_a.id, owner.id)
    await _abrir(db_session, lv, slug=SLUG_A)
    return lv


@pytest_asyncio.fixture
async def lav_b(db_session, tenant_b) -> Lavadero:
    owner_b = await make_user(db_session, tenant_b, Role.OWNER, "owner-b-aisla52@example.com")
    lv = await armar_catalogo(db_session, tenant_b.id, owner_b.id)
    await _abrir(db_session, lv, slug=SLUG_B)
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


async def _reservar(client, lav: Lavadero, slug: str = SLUG_A, clave=None, **extra):
    return await client.post(
        f"/v1/public/{slug}/bookings",
        json=_reserva(lav, **extra),
        headers={"Idempotency-Key": clave or f"web-{uuid.uuid4()}"},
    )


async def _lookup(client, slug: str, code: str, phone: str):
    return await client.post(
        f"/v1/public/{slug}/bookings/lookup", json={"code": code, "phone": phone}
    )


async def _cancelar(client, slug: str, code: str, phone: str, reason: str | None = None):
    return await client.post(
        f"/v1/public/{slug}/bookings/cancel", json={"code": code, "phone": phone, "reason": reason}
    )


def _huella(r) -> tuple[int, bytes, list[tuple[str, str]]]:
    """Todo lo que un atacante ve de una respuesta, sin lo volátil."""
    cabeceras = sorted((k, v) for k, v in r.headers.items() if k.lower() not in _VOLATILES)
    return r.status_code, r.content, cabeceras


def _sin_limite() -> None:
    limiter.reset()


# ── 1. Enumeración de slugs (Z7/Z8) ───────────────────────────────────────────


async def test_inexistente_anulado_y_sin_alta_son_indistinguibles_en_todas_las_rutas(
    client, db_session, lav
):
    sin_alta = await make_tenant(db_session, "A medio configurar aisla52")
    anulado_t = await make_tenant(db_session, "Anulado aisla52")
    await set_tenant_context(db_session, anulado_t.id)
    settings = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == anulado_t.id)
    )
    settings.onboarding_completed_at = datetime.now(UTC)
    anulado_t.slug = "ya-no-existe-aisla"
    anulado_t.voided_at = datetime.now(UTC)
    anulado_t.void_reason = VoidReason.DESACTIVADO
    await db_session.flush()
    slugs = ["no-existe-aisla", sin_alta.slug, anulado_t.slug]
    cuerpo_reserva = _reserva(lav)
    peticiones = [
        ("GET", "", None, {}),
        ("GET", "/catalog", None, {}),
        (
            "GET",
            f"/availability?date={DIA}&vehicle_size_id={lav.auto}&service_ids={lav.lavado}",
            None,
            {},
        ),
        ("POST", "/bookings", cuerpo_reserva, {"Idempotency-Key": "enum-1"}),
        ("POST", "/bookings/lookup", {"code": "TUR-ZZZZZZ", "phone": TEL}, {}),
        ("POST", "/bookings/cancel", {"code": "TUR-ZZZZZZ", "phone": TEL}, {}),
    ]
    for metodo, sufijo, cuerpo, cabeceras in peticiones:
        _sin_limite()
        huellas = []
        for slug in slugs:
            r = await client.request(
                metodo, f"/v1/public/{slug}{sufijo}", json=cuerpo, headers=cabeceras
            )
            assert r.status_code == 404, (metodo, sufijo, slug, r.text)
            huellas.append(_huella(r))
        # Mismo cuerpo y mismas cabeceras: ni el texto ni el `content-type` delatan nada.
        assert huellas[0] == huellas[1] == huellas[2], (metodo, sufijo)
    # Y no creó nada: ni clientes ni claves en el negocio sin alta.
    assert (
        await db_session.scalar(
            select(func.count()).select_from(IdempotencyKey).where(IdempotencyKey.key == "enum-1")
        )
        == 0
    )


async def test_los_tres_404_hacen_las_mismas_consultas(client, db_session, lav):
    """Proxy de los tiempos: inexistente, anulado y sin alta recorren el mismo camino de SQL."""
    sin_alta = await make_tenant(db_session, "Sin alta aisla52 b")
    anulado_t = await make_tenant(db_session, "Anulado aisla52 b")
    anulado_t.slug = "anulado-aisla-b"
    anulado_t.voided_at = datetime.now(UTC)
    anulado_t.void_reason = VoidReason.DESACTIVADO
    await db_session.flush()
    motor = db_session.bind.sync_engine
    consultas: list[str] = []

    def contar(conn, cursor, statement, *a):
        consultas.append(statement)

    event.listen(motor, "before_cursor_execute", contar)
    try:
        cantidades = []
        for slug in ("no-existe-aisla-b", sin_alta.slug, anulado_t.slug):
            consultas.clear()
            _sin_limite()
            assert (await client.get(f"/v1/public/{slug}")).status_code == 404
            cantidades.append(len(consultas))
    finally:
        event.remove(motor, "before_cursor_execute", contar)
    assert len(set(cantidades)) == 1, cantidades


@pytest.mark.parametrize(
    "slug",
    [
        "%27%20OR%201%3D1--",  # ' OR 1=1--
        "%25",  # %
        "_",
        "a" * 500,
        "%00",
        "%E2%80%AE",  # RLO
        "..%2F..%2Fadmin",
        "SIN-ALTA%0D%0AX-Inyectado:1",
        "lavadero%20norte",
    ],
)
async def test_slugs_hostiles_dan_404_y_nunca_500(client, lav, slug):
    _sin_limite()
    r = await client.get(f"/v1/public/{slug}")
    assert r.status_code == 404, r.text
    assert "x-inyectado" not in {k.lower() for k in r.headers}


async def test_un_slug_con_comodines_no_resuelve_al_negocio(client, lav):
    _sin_limite()
    for patron in ("aisla-%", "aisla-nort_", "%25", "aisla-*"):
        assert (await client.get(f"/v1/public/{patron}")).status_code == 404, patron


# ── 2. Spam y límites ─────────────────────────────────────────────────────────


async def test_rotar_slugs_en_un_endpoint_gasta_el_mismo_cupo(client):
    _sin_limite()
    codigos = [(await client.get(f"/v1/public/rota-{i}/catalog")).status_code for i in range(61)]
    assert set(codigos[:60]) == {404} and codigos[60] == 429


async def test_x_forwarded_for_y_x_real_ip_no_dan_cupos_nuevos(client):
    """Fuera de producción la IP es la del socket; en producción solo cuenta `X-Real-IP` del edge.
    En ningún caso un `X-Forwarded-For` inventado por el atacante rota la cubeta."""
    _sin_limite()
    codigos = []
    for i in range(61):
        r = await client.get(
            f"/v1/public/xff-{i}",
            headers={"X-Forwarded-For": f"10.9.{i}.{i}", "X-Real-IP": f"10.8.{i}.{i}"},
        )
        codigos.append(r.status_code)
    assert set(codigos[:60]) == {404} and codigos[60] == 429


async def test_reservas_rotando_xff_siguen_cortando_a_las_cinco(client, lav):
    _sin_limite()
    codigos = []
    for i in range(6):
        r = await client.post(
            f"/v1/public/{SLUG_A}/bookings",
            json=_reserva(lav, start_at=_inicio(9 + i), phone=f"+54 9 351 000-03{i:02d}"),
            headers={"Idempotency-Key": f"xff-{i}", "X-Forwarded-For": f"203.0.113.{i}"},
        )
        codigos.append(r.status_code)
    assert codigos == [201] * 5 + [429]


async def test_el_cupo_por_ip_no_se_comparte_entre_rutas_pero_cada_ruta_corta(client, lav):
    """Cada ruta tiene su scope: con 4 GET distintos un atacante hace ~60/min en cada una. Cada una
    corta sola; que no se pisen entre sí no abre una ruta sin tope."""
    _sin_limite()
    for ruta, limite in (("/catalog", 60), ("", 60)):
        codigos = [
            (await client.get(f"/v1/public/{SLUG_A}{ruta}")).status_code for _ in range(limite + 1)
        ]
        assert codigos[-1] == 429, ruta


@pytest.mark.parametrize(
    "variante",
    ["ABC", " abc ", "\tabc", "Abc ", "aBC "],
)
def test_la_cubeta_por_negocio_normaliza_igual_que_la_resolucion_del_slug(variante):
    """Si dos escrituras del mismo slug resolvieran al mismo negocio con cubetas distintas, el
    tope por negocio se evadiría. `slug_key` y `find_public_tenant_id` usan `strip().lower()`."""

    class _Req:
        path_params = {"slug": variante}

    assert slug_key(_Req()) == "slug:abc"


# ── 3. Ver o cancelar el turno de otro ────────────────────────────────────────


async def _turno(client, lav: Lavadero, slug: str = SLUG_A, **extra) -> dict[str, Any]:
    r = await _reservar(client, lav, slug, **extra)
    assert r.status_code == 201, r.text
    return r.json()


async def test_whatsapp_erroneo_y_sus_variantes_dan_todos_el_mismo_404(client, lav):
    _sin_limite()
    turno = await _turno(client, lav)
    codigo = turno["code"]
    malos = [
        OTRO_TEL,
        "0001",
        "000-0001",
        "351 000 0003",
        "+54 351 000-0001",  # sin el 9: ambiguo, no se adivina
        "+54 9 351 000-000",  # truncado
        "+54 9 351 000-00011",  # de más
        "%",
        "' OR '1'='1",
        "+549351000000%",
        "0" * 40,
        "+54 9 351 000-0002; DROP TABLE bookings",
    ]
    huellas = set()
    for telefono in malos:
        _sin_limite()
        r = await _lookup(client, SLUG_A, codigo, telefono)
        assert r.status_code in (404,), (telefono, r.status_code, r.text)
        huellas.add(r.content)
        c = await _cancelar(client, SLUG_A, codigo, telefono)
        assert c.status_code == 404, (telefono, c.status_code)
        huellas.add(c.content)
    assert len(huellas) == 1, "los 404 de ver y cancelar tienen que ser byte a byte iguales"
    # La víctima sigue con su turno vivo.
    ok = await _lookup(client, SLUG_A, codigo, "351-000-0001")
    assert ok.status_code == 200 and ok.json()["can_cancel"] is True


async def test_los_formatos_del_mismo_numero_si_sirven_y_los_de_otro_no(client, lav):
    """Un número que normaliza igual ES el número: no hay nada que proteger. Lo que no puede pasar
    es que un número distinto coincida (sufijos, `15`, sin el 9)."""
    _sin_limite()
    codigo = (await _turno(client, lav))["code"]
    for mismo in ("+5493510000001", "0351 000 0001", "03510000001", "(351) 000-0001"):
        _sin_limite()
        assert (await _lookup(client, SLUG_A, codigo, mismo)).status_code == 200, mismo
    for distinto in ("351 15 000001", "+54 351 0000001", "3510000011", "9351000001"):
        _sin_limite()
        assert (await _lookup(client, SLUG_A, codigo, distinto)).status_code == 404, distinto


@pytest.mark.parametrize(
    "codigo",
    [
        "TUR-%",
        "%",
        "TUR-______",
        "TUR-ZZZZZZ",
        "tur-zzzzzz",
        "",
        " ",
        "TUR-000000",
        "'--",
        "A" * 20,
    ],
)
async def test_codigos_comodin_o_imposibles_son_404_sin_tocar_nada(client, db_session, lav, codigo):
    _sin_limite()
    turno = await _turno(client, lav)
    r = await _lookup(client, SLUG_A, codigo, TEL)
    c = await _cancelar(client, SLUG_A, codigo, TEL)
    # Vacío no pasa el esquema (422); el resto es el 404 de siempre.
    assert r.status_code in (404, 422) and c.status_code in (404, 422), (r.text, c.text)
    vivo = await db_session.scalar(select(Booking).where(Booking.code == turno["code"]))
    assert vivo.status != BookingStatus.CANCELADO_CLIENTE


async def test_el_codigo_de_un_negocio_no_sirve_en_el_slug_del_otro(client, db_session, lav, lav_b):
    _sin_limite()
    de_a = await _turno(client, lav, SLUG_A)
    de_b = await _turno(client, lav_b, SLUG_B)  # mismo teléfono en los dos negocios
    for slug, codigo in ((SLUG_B, de_a["code"]), (SLUG_A, de_b["code"])):
        assert (await _lookup(client, slug, codigo, TEL)).status_code == 404
        assert (await _cancelar(client, slug, codigo, TEL)).status_code == 404
    for codigo in (de_a["code"], de_b["code"]):
        fila = await db_session.scalar(select(Booking).where(Booking.code == codigo))
        assert fila.status != BookingStatus.CANCELADO_CLIENTE
    # El mismo teléfono es dos clientes distintos, uno por negocio.
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Customer).where(Customer.phone_e164 == TEL_E164)
        )
        >= 2
    )


async def test_cancelar_dos_veces_no_filtra_nada_y_un_ajeno_sigue_en_404(client, lav):
    _sin_limite()
    codigo = (await _turno(client, lav))["code"]
    assert (await _cancelar(client, SLUG_A, codigo, OTRO_TEL)).status_code == 404
    primera = await _cancelar(client, SLUG_A, codigo, TEL)
    segunda = await _cancelar(client, SLUG_A, codigo, TEL)
    assert primera.status_code == segunda.status_code == 200
    assert segunda.json()["status"] == "CANCELADO_CLIENTE"
    assert (await _cancelar(client, SLUG_A, codigo, OTRO_TEL)).status_code == 404


async def test_la_razon_de_la_cancelacion_se_guarda_literal_y_tiene_tope(client, lav):
    _sin_limite()
    codigo = (await _turno(client, lav))["code"]
    largo = await _cancelar(client, SLUG_A, codigo, TEL, "x" * 161)
    assert largo.status_code == 422
    r = await _cancelar(client, SLUG_A, codigo, TEL, "<script>alert(1)</script>'; --")
    assert r.status_code == 200, r.text


# ── 4. Tope de 2 activos ──────────────────────────────────────────────────────


async def test_el_tope_de_dos_activos_no_se_evade_con_formatos_del_mismo_telefono(client, lav):
    _sin_limite()
    formatos = ["+54 9 351 000-0001", "03510000001", "351 000 0001", "+5493510000001"]
    estados = []
    for i, formato in enumerate(formatos):
        r = await _reservar(client, lav, phone=formato, start_at=_inicio(9 + i), name=f"Nombre {i}")
        estados.append((r.status_code, _codigo(r) if r.status_code != 201 else None))
    assert estados == [
        (201, None),
        (201, None),
        (409, "TOO_MANY_ACTIVE_BOOKINGS"),
        (409, "TOO_MANY_ACTIVE_BOOKINGS"),
    ]


async def test_cancelar_libera_un_cupo_del_tope(client, lav):
    _sin_limite()
    uno = await _turno(client, lav, start_at=_inicio(9))
    await _turno(client, lav, start_at=_inicio(11))
    assert (await _reservar(client, lav, start_at=_inicio(13))).status_code == 409
    assert (await _cancelar(client, SLUG_A, uno["code"], TEL)).status_code == 200
    assert (await _reservar(client, lav, start_at=_inicio(13))).status_code == 201


async def test_el_tope_es_por_negocio_no_global(client, lav, lav_b):
    _sin_limite()
    await _turno(client, lav, SLUG_A, start_at=_inicio(9))
    await _turno(client, lav, SLUG_A, start_at=_inicio(11))
    assert (await _reservar(client, lav_b, SLUG_B, start_at=_inicio(9))).status_code == 201


# ── 5. Pisar a un cliente existente ───────────────────────────────────────────


async def test_reservar_con_el_whatsapp_de_otro_no_le_toca_ningun_dato(client, db_session, lav):
    _sin_limite()
    antes = await db_session.get(Customer, lav.cliente)
    snapshot = (antes.name, antes.phone_e164, antes.phone_raw, antes.acquisition_channel)
    r = await _reservar(
        client,
        lav,
        phone="11 4444-5555",
        name="  Impostor   <b>x</b>  ",
        plate=PATENTE,
    )
    assert r.status_code == 201, r.text
    await db_session.refresh(antes)
    assert (
        antes.name,
        antes.phone_e164,
        antes.phone_raw,
        antes.acquisition_channel,
    ) == snapshot
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.customer_id == lav.cliente
    assert turno.notes == "Nombre en la web: Impostor   <b>x</b>"
    # Y la respuesta pública nunca devuelve el nombre del cliente existente.
    assert "Cliente Uno" not in r.text


async def test_el_nombre_existente_no_aparece_en_ninguna_respuesta_del_flujo(client, lav):
    _sin_limite()
    r = await _reservar(client, lav, phone="11 4444-5555", name="Otro Nombre")
    codigo = r.json()["code"]
    rutas = [
        await _lookup(client, SLUG_A, codigo, "11 4444-5555"),
        await _cancelar(client, SLUG_A, codigo, "11 4444-5555"),
        r,
    ]
    for x in rutas:
        assert "Cliente Uno" not in x.text and "Otro Nombre" not in x.text


async def test_una_patente_ajena_no_se_vincula_ni_cambia_el_auto(client, db_session, lav):
    _sin_limite()
    auto = await db_session.get(Vehicle, lav.vehiculo)
    placa, tamano = auto.plate, auto.vehicle_size_id
    r = await _reservar(
        client, lav, phone=OTRO_TEL, plate="ab 123 cd", vehicle_size_id=str(lav.suv)
    )
    assert r.status_code == 201, r.text
    await db_session.refresh(auto)
    assert (auto.plate, auto.vehicle_size_id) == (placa, tamano)
    vinculos = (
        await db_session.scalars(
            select(CustomerVehicle).where(CustomerVehicle.vehicle_id == lav.vehiculo)
        )
    ).all()
    assert all(v.customer_id != r.json().get("customer_id") for v in vinculos)
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.vehicle_id == lav.vehiculo
    assert turno.customer_id != lav.cliente
    assert turno.customer_id not in {v.customer_id for v in vinculos}


async def test_la_patente_de_otro_negocio_crea_un_auto_propio_y_no_toca_el_ajeno(
    client, db_session, lav, lav_b
):
    _sin_limite()
    r = await _reservar(client, lav_b, SLUG_B, plate="AB 123 CD")  # existe en A y en B (armado)
    assert r.status_code == 201, r.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == r.json()["code"]))
    assert turno.tenant_id == lav_b.tenant_id and turno.vehicle_id == lav_b.vehiculo
    assert turno.vehicle_id != lav.vehiculo


async def test_cliente_anulado_con_ese_whatsapp_no_rompe_el_alta(client, db_session, lav):
    """Un cliente anulado (baja del negocio) con el mismo WhatsApp no puede dar un 500 ni dejar
    el turnero tirado para ese número."""
    _sin_limite()
    await set_tenant_context(db_session, lav.tenant_id)
    cliente = await db_session.get(Customer, lav.cliente)
    cliente.voided_at = datetime.now(UTC)
    cliente.void_reason = VoidReason.DESACTIVADO
    await db_session.flush()
    r = await _reservar(client, lav, phone="11 4444-5555")
    assert r.status_code < 500, r.text


# ── 6. Servicios y tamaños no reservables ─────────────────────────────────────


async def _modo(db_session, lav: Lavadero, service_id, modo: ServiceBookingMode) -> None:
    await set_tenant_context(db_session, lav.tenant_id)
    servicio = await db_session.get(Service, service_id)
    servicio.booking_mode = modo
    await db_session.flush()


async def test_ningun_servicio_no_reservable_se_reserva_ni_se_ofrece(
    client, db_session, lav, lav_b
):
    _sin_limite()
    await _modo(db_session, lav, lav.lavado_con_sena, ServiceBookingMode.SOLO_A_MANO)
    await set_tenant_context(db_session, lav.tenant_id)
    anulado = await db_session.get(Service, lav.lavado)
    anulado.voided_at = datetime.now(UTC)
    anulado.void_reason = VoidReason.DESACTIVADO
    await db_session.flush()
    no_reservables = {
        "anulado": lav.lavado,
        "solo a mano": lav.lavado_con_sena,
        "a cotizar": lav.tapizado,
        "de otro negocio": lav_b.lavado,
        "inexistente": uuid.uuid4(),
    }
    for nombre, servicio in no_reservables.items():
        r = await _reservar(client, lav, service_ids=[str(servicio)], start_at=_inicio(15))
        assert r.status_code == 422 and _codigo(r) == "SERVICE_NOT_BOOKABLE", (nombre, r.text)
        d = await client.get(
            f"/v1/public/{SLUG_A}/availability",
            params={
                "date": str(DIA),
                "vehicle_size_id": str(lav.auto),
                "service_ids": str(servicio),
            },
        )
        assert d.status_code == 422 and _codigo(d) == "SERVICE_NOT_BOOKABLE", (nombre, d.text)
    # Las respuestas de "de otro negocio" e "inexistente" son idénticas: no se sabe que existe.
    ajeno = await _reservar(client, lav, service_ids=[str(lav_b.lavado)], start_at=_inicio(15))
    fantasma = await _reservar(client, lav, service_ids=[str(uuid.uuid4())], start_at=_inicio(15))
    assert ajeno.content == fantasma.content
    # Ni el catálogo público los lista.
    ids = {s["id"] for s in (await client.get(f"/v1/public/{SLUG_A}/catalog")).json()["services"]}
    assert str(lav.lavado) not in ids and str(lav.lavado_con_sena) not in ids
    assert str(lav_b.lavado) not in ids
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Booking).where(Booking.tenant_id == lav.tenant_id)
        )
        == 0
    )


async def test_un_servicio_bueno_mezclado_con_uno_malo_no_reserva_ninguno(client, db_session, lav):
    _sin_limite()
    r = await _reservar(client, lav, service_ids=[str(lav.lavado), str(lav.tapizado)])
    assert r.status_code == 422
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Booking).where(Booking.tenant_id == lav.tenant_id)
        )
        == 0
    )


async def test_servicios_repetidos_y_tamanos_ajenos_son_422_sin_filtrar(client, lav, lav_b):
    _sin_limite()
    doble = await _reservar(client, lav, service_ids=[str(lav.lavado), str(lav.lavado)])
    assert doble.status_code == 422
    ajeno = await _reservar(client, lav, vehicle_size_id=str(lav_b.auto))
    fantasma = await _reservar(client, lav, vehicle_size_id=str(uuid.uuid4()))
    assert ajeno.status_code == fantasma.status_code == 422
    assert ajeno.content == fantasma.content
    mucho = await _reservar(client, lav, service_ids=[str(uuid.uuid4()) for _ in range(21)])
    assert mucho.status_code in (422,)
    vacio = await _reservar(client, lav, service_ids=[])
    assert vacio.status_code == 422


# ── 7. Horarios imposibles ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "inicio",
    [
        "pasado",
        "ahora",
        "fuera_horizonte",
        "muy_lejos",
        "fuera_de_horario",
        "no_alineado",
        "con_segundos",
        "sin_zona",
        "año_1",
        "año_9999",
    ],
)
async def test_un_start_at_fuera_de_free_starts_nunca_reserva_ni_da_500(
    client, db_session, lav, inicio
):
    _sin_limite()
    hoy = datetime.now(TZ).date()
    valores = {
        "pasado": _inicio(10, dia=hoy - timedelta(days=1)),
        "ahora": datetime.now(UTC).isoformat(),
        "fuera_horizonte": _inicio(10, dia=hoy + timedelta(days=15)),
        "muy_lejos": _inicio(10, dia=hoy + timedelta(days=400)),
        "fuera_de_horario": _inicio(3),
        "no_alineado": _inicio(10, 7),
        "con_segundos": (datetime.combine(DIA, time(10, 0, 30), tzinfo=TZ)).isoformat(),
        "sin_zona": datetime.combine(DIA, time(10)).isoformat(),
        "año_1": "0001-01-01T10:00:00+00:00",
        "año_9999": "9999-12-31T23:00:00+00:00",
    }
    r = await _reservar(client, lav, start_at=valores[inicio])
    assert r.status_code in (409, 422), (inicio, r.status_code, r.text)
    assert r.status_code != 201
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Booking).where(Booking.tenant_id == lav.tenant_id)
        )
        == 0
    )


@pytest.mark.parametrize(
    "fecha", ["0001-01-01", "9999-12-31", "2000-01-01", "no-es-fecha", "2026-13-45"]
)
async def test_la_disponibilidad_con_fechas_extremas_no_da_500(client, lav, fecha):
    _sin_limite()
    r = await client.get(
        f"/v1/public/{SLUG_A}/availability",
        params={"date": fecha, "vehicle_size_id": str(lav.auto), "service_ids": str(lav.lavado)},
    )
    assert r.status_code in (200, 422), (fecha, r.status_code, r.text)
    if r.status_code == 200:
        assert r.json()["slots"] == []


async def test_el_mismo_horario_dos_veces_la_segunda_es_slot_taken_o_otro_puesto(client, lav):
    """Con dos puestos entran dos; la tercera sobre el mismo horario no."""
    _sin_limite()
    codigos = [
        (await _reservar(client, lav, phone=f"+54 9 351 000-04{i:02d}")).status_code
        for i in range(3)
    ]
    assert codigos == [201, 201, 409]


# ── 8. Idempotency-Key ────────────────────────────────────────────────────────


async def test_la_misma_clave_con_otro_cuerpo_y_el_mismo_whatsapp_devuelve_el_turno_original(
    client, db_session, lav
):
    """Decisión 5.2-2: el reenvío con la misma clave y el mismo WhatsApp es el mismo turno. No crea
    un segundo turno aunque cambie el horario (documentado: el cuerpo no se compara)."""
    _sin_limite()
    uno = await _reservar(client, lav, clave="clave-fija")
    dos = await _reservar(client, lav, clave="clave-fija", start_at=_inicio(14))
    assert uno.status_code == 201 and dos.status_code in (201, 409, 422)
    if dos.status_code == 201:
        assert dos.json()["code"] == uno.json()["code"]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Booking).where(Booking.tenant_id == lav.tenant_id)
        )
        == 1
    )


async def test_la_misma_clave_con_otro_whatsapp_es_409_sin_datos_del_turno(client, lav):
    _sin_limite()
    uno = await _reservar(client, lav, clave="clave-de-la-victima")
    robo = await _reservar(client, lav, clave="clave-de-la-victima", phone=OTRO_TEL)
    assert robo.status_code == 409 and _codigo(robo) == "DUPLICATE_IDEMPOTENT"
    assert uno.json()["code"] not in robo.text and "start_at" not in robo.text


async def test_una_clave_de_un_negocio_no_es_replay_en_el_otro(client, db_session, lav, lav_b):
    _sin_limite()
    de_a = await _reservar(client, lav, SLUG_A, clave="clave-compartida")
    de_b = await _reservar(client, lav_b, SLUG_B, clave="clave-compartida")
    assert de_a.status_code == de_b.status_code == 201
    b = await db_session.scalar(select(Booking).where(Booking.code == de_b.json()["code"]))
    assert b.tenant_id == lav_b.tenant_id


@pytest.mark.parametrize("clave", ["", "k" * 201])
async def test_claves_vacias_o_enormes_son_422(client, lav, clave):
    _sin_limite()
    r = await client.post(
        f"/v1/public/{SLUG_A}/bookings", json=_reserva(lav), headers={"Idempotency-Key": clave}
    )
    assert r.status_code == 422


async def test_una_clave_del_panel_no_se_puede_reclamar_desde_la_web(client, db_session, lav):
    """Una clave ya usada por otra operación (acción distinta de `public_booking`) da 409 sin datos
    y no devuelve ningún turno."""
    _sin_limite()
    db_session.add(
        IdempotencyKey(tenant_id=lav.tenant_id, key="clave-del-panel", action="job_payment")
    )
    await db_session.flush()
    r = await _reservar(client, lav, clave="clave-del-panel")
    assert r.status_code == 409 and _codigo(r) == "DUPLICATE_IDEMPOTENT"


# ── 9. Inyección en los campos de texto ───────────────────────────────────────

CARGAS = [
    "'; DROP TABLE bookings; --",
    "<script>alert(1)</script>",
    "{{7*7}}${7*7}",
    "Robert');--",
    "line1\r\nX-Injected: 1",
    "../../etc/passwd",
    "‮evil",
    "%s%s%s%n",
]


@pytest.mark.parametrize("carga", CARGAS)
async def test_el_texto_hostil_se_guarda_literal_y_no_rompe_nada(client, db_session, lav, carga):
    _sin_limite()
    r = await _reservar(client, lav, phone=OTRO_TEL, name=carga[:80], plate=None)
    assert r.status_code == 201, r.text
    assert "x-injected" not in {k.lower() for k in r.headers}
    cliente = await db_session.scalar(
        select(Customer).where(Customer.phone_e164 == "+5493510000002")
    )
    assert cliente.name == carga[:80].strip()
    # Y con el cliente ya existente, el texto va a las notas, también literal.
    r2 = await _reservar(client, lav, phone=OTRO_TEL, name=carga[:80] + "!", start_at=_inicio(12))
    assert r2.status_code == 201, r2.text
    turno = await db_session.scalar(select(Booking).where(Booking.code == r2.json()["code"]))
    assert turno.notes == f"Nombre en la web: {(carga[:80] + '!').strip()}"
    assert (
        await db_session.scalar(select(func.count()).select_from(Booking)) >= 2
    ), "la tabla sigue ahí"


@pytest.mark.parametrize("campo", ["name", "phone", "plate"])
async def test_un_nul_en_los_campos_es_422_y_no_500(client, lav, campo):
    _sin_limite()
    r = await _reservar(client, lav, **{campo: "ab\x00cd"})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("plate", CARGAS)
async def test_la_patente_hostil_es_422_o_se_normaliza_y_no_se_refleja(client, lav, plate):
    _sin_limite()
    r = await _reservar(client, lav, plate=plate[:12])
    assert r.status_code in (201, 422), r.text
    assert plate[:12] not in r.text or r.status_code == 201


# ── 10. Fuga de PII ───────────────────────────────────────────────────────────

CLAVES_RESERVA = {
    "code",
    "status",
    "start_at",
    "end_at",
    "timezone",
    "vehicle_size_label",
    "services",
    "total_cents",
    "deposit_cents",
    "hold_expires_at",
    "can_cancel",
}


async def test_las_respuestas_traen_solo_las_claves_del_contrato(client, lav):
    _sin_limite()
    r = await _reservar(client, lav, plate=PATENTE, name="Nombre Secreto")
    assert r.status_code == 201
    assert set(r.json()) == CLAVES_RESERVA
    ver = await _lookup(client, SLUG_A, r.json()["code"], TEL)
    assert set(ver.json()) == CLAVES_RESERVA
    for x in (r, ver):
        assert PATENTE not in x.text and "Nombre Secreto" not in x.text
        assert TEL_E164 not in x.text and str(lav.tenant_id) not in x.text
    cancelada = await _cancelar(client, SLUG_A, r.json()["code"], TEL)
    assert set(cancelada.json()) == {"code", "status", "classification", "deposit_status"}
    assert PATENTE not in cancelada.text


async def test_ningun_error_refleja_lo_que_mando_el_cliente_ni_ids_internos(client, lav, lav_b):
    _sin_limite()
    secretos = [PATENTE, "Nombre Secreto", "+54 9 351 000-0777"]
    intentos = [
        await _reservar(client, lav, plate=PATENTE, name="Nombre Secreto", start_at=_inicio(3)),
        await _reservar(client, lav, plate=PATENTE + "ABCDEF", name="Nombre Secreto"),
        await _reservar(
            client,
            lav,
            plate=PATENTE,
            name="Nombre Secreto",
            phone="+54 9 351 000-0777",
            service_ids=[str(lav_b.lavado)],
        ),
        await _reservar(client, lav, phone="+54 9 351 000-0777", accept_policy=False),
        await _reservar(client, lav, phone="+54 351 0777"),
        await _lookup(client, SLUG_A, "TUR-ZZZZZZ", "+54 9 351 000-0777"),
        await _cancelar(client, SLUG_A, "TUR-ZZZZZZ", "+54 9 351 000-0777"),
    ]
    for r in intentos:
        assert r.status_code >= 400, r.text
        for secreto in secretos:
            assert secreto not in r.text, (r.status_code, r.text)
        assert str(lav.tenant_id) not in r.text and str(lav_b.tenant_id) not in r.text
        assert str(lav_b.lavado) not in r.text


async def test_el_catalogo_y_la_disponibilidad_no_filtran_clientes_ni_puestos(
    client, db_session, lav
):
    _sin_limite()
    await _reservar(client, lav, plate=PATENTE, name="Nombre Secreto")
    cat = await client.get(f"/v1/public/{SLUG_A}/catalog")
    disp = await client.get(
        f"/v1/public/{SLUG_A}/availability",
        params={"date": str(DIA), "vehicle_size_id": str(lav.auto), "service_ids": str(lav.lavado)},
    )
    biz = await client.get(f"/v1/public/{SLUG_A}")
    for x in (cat, disp, biz):
        assert x.status_code == 200
        for prohibido in (
            PATENTE,
            "Nombre Secreto",
            TEL_E164,
            str(lav.puesto),
            str(lav.puesto_2),
            str(lav.tenant_id),
            str(lav.cliente),
            "tenant_id",
            "resource",
            "customer",
        ):
            assert prohibido not in x.text, prohibido
    # El horario reservado deja de ofrecerse sin decir quién lo ocupa (con 2 puestos sigue 1 libre).
    assert set(disp.json()) == {"date", "timezone", "duration_min", "slots"}
    assert all(set(s) == {"start_at", "end_at"} for s in disp.json()["slots"])


async def test_los_logs_del_flujo_no_llevan_patente_ni_telefono_ni_nombre(client, lav):
    _sin_limite()
    with capture_logs() as logs:
        r = await _reservar(client, lav, plate=PATENTE, name="Nombre Secreto", phone="351 000 0888")
        codigo = r.json()["code"]
        await _lookup(client, SLUG_A, codigo, "351 000 0888")
        await _reservar(client, lav, plate=PATENTE, name="Nombre Secreto", accept_policy=None)
        await _reservar(client, lav, plate="ABCDEFGHIJKLMNOP", name="Nombre Secreto")
        await _cancelar(client, SLUG_A, codigo, "351 000 0888", "motivo reservado")
        await _lookup(client, SLUG_A, "TUR-ZZZZZZ", "351 000 0888")
    volcado = json.dumps(logs, default=str).lower()
    for secreto in (PATENTE.lower(), "nombre secreto", "0888", "motivo reservado", "abcdefghijk"):
        assert secreto not in volcado, secreto


# ── 11. CORS y cabeceras ──────────────────────────────────────────────────────


async def test_las_rutas_publicas_salen_con_las_cabeceras_de_seguridad(client, lav):
    _sin_limite()
    for r in (
        await client.get(f"/v1/public/{SLUG_A}"),
        await client.get("/v1/public/no-existe-aisla"),
        await _lookup(client, SLUG_A, "TUR-ZZZZZZ", TEL),
    ):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert r.headers["content-type"].startswith("application/json")


async def test_un_origen_ajeno_no_recibe_cabeceras_cors_ni_puede_mutar(client, lav):
    _sin_limite()
    evil = {"Origin": "https://evil.example.com"}
    lectura = await client.get(f"/v1/public/{SLUG_A}", headers=evil)
    assert "access-control-allow-origin" not in {k.lower() for k in lectura.headers}
    pre = await client.options(
        f"/v1/public/{SLUG_A}/bookings",
        headers={**evil, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in {k.lower() for k in pre.headers}
    escritura = await client.post(
        f"/v1/public/{SLUG_A}/bookings",
        json=_reserva(lav),
        headers={**evil, "Idempotency-Key": "origen-ajeno"},
    )
    assert escritura.status_code == 403 and _codigo(escritura) == "ORIGIN_NOT_ALLOWED"
    cancelar = await client.post(
        f"/v1/public/{SLUG_A}/bookings/cancel",
        json={"code": "TUR-ZZZZZZ", "phone": TEL},
        headers=evil,
    )
    assert cancelar.status_code == 403


async def test_el_origen_propio_si_recibe_cors(client, lav):
    _sin_limite()
    r = await client.get(f"/v1/public/{SLUG_A}", headers={"Origin": "https://app.carwash.test"})
    assert r.headers["access-control-allow-origin"] == "https://app.carwash.test"
    assert r.headers.get("vary", "").lower().find("origin") >= 0


async def test_las_rutas_publicas_no_usan_la_cookie_aunque_venga(
    client, db_session, lav, lav_b, cookies_b
):
    """Con la cookie de B y el slug de A, todo lo que se ve y se hace es de A."""
    _sin_limite()
    de_b = await _turno(client, lav_b, SLUG_B)
    r = await client.post(
        f"/v1/public/{SLUG_A}/bookings/lookup",
        json={"code": de_b["code"], "phone": TEL},
        cookies=cookies_b,
    )
    assert r.status_code == 404
    c = await client.post(
        f"/v1/public/{SLUG_A}/bookings/cancel",
        json={"code": de_b["code"], "phone": TEL},
        cookies=cookies_b,
    )
    assert c.status_code == 404
    biz = await client.get(f"/v1/public/{SLUG_A}", cookies=cookies_b)
    assert biz.json()["slug"] == SLUG_A
