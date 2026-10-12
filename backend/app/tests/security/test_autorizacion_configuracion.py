"""A3 · A4 · FASE-4-CONTRATO §4 (filas 4.2), §2.3 y Y11 — autorización de la configuración.

Escrito por el tester de aceptación del PR 4.2, en paralelo con el implementador y a partir
del contrato. Dentro de **un** negocio (lo cruzado entre negocios está en
`test_aislamiento_tenants.py`):

1. **Catálogos** (`/business-hours`, `/payment-methods`, `/resources`, `/vehicle-sizes`):
   escribir (POST/PATCH/DELETE) exige `CATALOGO_EDITAR`; leer, solo una sesión. Un STAFF con
   todos los permisos menos ese recibe 403 y no cambia nada; uno que solo tiene ese, 2xx.
2. **`/settings`**: `GET` para cualquier sesión; `PATCH` es del OWNER (Y5): un Encargado
   (todos los permisos) recibe 403.

Datos sintéticos: `@example.invalid`, nombres inventados.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from typing import Any

import pytest
from sqlalchemy import select

from app.domain.permissions import ALL_PERMISSIONS, DEFAULT_PROFILES, Permission
from app.domain.roles import Role
from app.persistence.models import BusinessHours, PaymentMethod, Resource, VehicleSize
from app.persistence.models.tenant import TenantSettings
from app.tests.conftest import make_profile, make_user, session_cookies

SETTINGS_URL = "/v1/settings"


@dataclass(frozen=True)
class Catalogo:
    nombre: str
    url: str
    modelo: type[Any]
    fila: Callable[[int], dict[str, Any]]
    alta: Callable[[int], dict[str, Any]]
    cambio: dict[str, Any]
    #: Atributo del modelo que el PATCH cambia, y el valor que tiene que quedar.
    campo_cambiado: tuple[str, Any]


CATALOGOS = [
    Catalogo(
        nombre="vehicle-sizes",
        url="/v1/vehicle-sizes",
        modelo=VehicleSize,
        fila=lambda i: {"code": f"TAM_{i}", "label": f"Tamaño {i}", "sort_order": i},
        alta=lambda i: {"code": f"NUEVO_{i}", "label": f"Nuevo {i}", "sort_order": i},
        cambio={"label": "Cambiado"},
        campo_cambiado=("label", "Cambiado"),
    ),
    Catalogo(
        nombre="resources",
        url="/v1/resources",
        modelo=Resource,
        fila=lambda i: {"name": f"Puesto {i}", "sort_order": i},
        alta=lambda i: {"name": f"Puesto nuevo {i}", "sort_order": i},
        cambio={"name": "Cambiado"},
        campo_cambiado=("name", "Cambiado"),
    ),
    Catalogo(
        nombre="business-hours",
        url="/v1/business-hours",
        modelo=BusinessHours,
        fila=lambda i: {"weekday": 1 + i % 7, "opens_at": time(9), "closes_at": time(18)},
        alta=lambda i: {"weekday": 1 + i % 7, "opens_at": "08:00:00", "closes_at": "12:00:00"},
        cambio={"closes_at": "23:00:00"},
        campo_cambiado=("closes_at", time(23)),
    ),
    Catalogo(
        nombre="payment-methods",
        url="/v1/payment-methods",
        modelo=PaymentMethod,
        fila=lambda i: {"code": f"MEDIO_{i}", "label": f"Medio {i}"},
        alta=lambda i: {"code": f"NUEVO_{i}", "label": f"Medio nuevo {i}"},
        cambio={"label": "Cambiado"},
        campo_cambiado=("label", "Cambiado"),
    ),
]
CATALOGO_IDS = [c.nombre for c in CATALOGOS]

#: Perfiles sin `CATALOGO_EDITAR`: el más cargado posible y los por defecto que no lo traen.
SIN_CATALOGO: dict[str, frozenset[Permission]] = {
    "todo-menos-catalogo": ALL_PERMISSIONS - {Permission.CATALOGO_EDITAR},
    "Cajero": DEFAULT_PROFILES["Cajero"],
    "Lavador": DEFAULT_PROFILES["Lavador"],
    "solo-precios": frozenset({Permission.PRECIOS_EDITAR}),
}


def _codigo(r: Any) -> str:
    return str(r.json()["detail"]["code"])


async def _empleado(db_session: Any, tenant: Any, permisos: frozenset[Permission]) -> Any:
    perfil = await make_profile(db_session, tenant, f"perfil {uuid.uuid4().hex[:8]}", permisos)
    return await make_user(
        db_session,
        tenant,
        Role.STAFF,
        f"staff-{uuid.uuid4().hex[:8]}@example.invalid",
        profile=perfil,
    )


async def _fila_de(db_session: Any, cat: Catalogo, tenant: Any, i: int = 0) -> Any:
    fila = cat.modelo(id=uuid.uuid4(), tenant_id=tenant.id, **cat.fila(i))
    db_session.add(fila)
    await db_session.flush()
    return fila


async def _asegurar_settings(db_session: Any, tenant: Any) -> TenantSettings:
    fila = await db_session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )
    if fila is None:
        fila = TenantSettings(id=uuid.uuid4(), tenant_id=tenant.id)
        db_session.add(fila)
        await db_session.flush()
    return fila


# ── Catálogos: escribir exige CATALOGO_EDITAR ──────────────────────────────────


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
@pytest.mark.parametrize("perfil", list(SIN_CATALOGO), ids=list(SIN_CATALOGO))
async def test_staff_sin_catalogo_editar_recibe_403_al_escribir_y_no_cambia_nada(
    client, db_session, tenant_a, cat, perfil
):
    empleado = await _empleado(db_session, tenant_a, SIN_CATALOGO[perfil])
    cookies = session_cookies(empleado)
    fila = await _fila_de(db_session, cat, tenant_a)
    campo, _ = cat.campo_cambiado
    valor_antes = getattr(fila, campo)

    alta = await client.post(cat.url, json=cat.alta(1), cookies=cookies)
    cambio = await client.patch(f"{cat.url}/{fila.id}", json=cat.cambio, cookies=cookies)
    baja = await client.delete(f"{cat.url}/{fila.id}", cookies=cookies)
    for r in (alta, cambio, baja):
        assert r.status_code == 403, r.text
        assert _codigo(r) == "FORBIDDEN"

    await db_session.refresh(fila)
    assert getattr(fila, campo) == valor_antes and fila.voided_at is None
    filas = (
        await db_session.scalars(select(cat.modelo).where(cat.modelo.tenant_id == tenant_a.id))
    ).all()
    assert [f.id for f in filas] == [fila.id], "el POST con 403 creó una fila"


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
async def test_el_403_de_permiso_no_distingue_id_propio_ajeno_ni_inexistente(
    client, db_session, tenant_a, tenant_b, cat
):
    """Y11: el 403 sale antes de buscar el recurso, igual para cualquier id."""
    empleado = await _empleado(db_session, tenant_a, SIN_CATALOGO["todo-menos-catalogo"])
    cookies = session_cookies(empleado)
    propia = await _fila_de(db_session, cat, tenant_a)
    ajena = await _fila_de(db_session, cat, tenant_b)
    for metodo, kwargs in (("PATCH", {"json": cat.cambio}), ("DELETE", {})):
        respuestas = [
            await client.request(metodo, f"{cat.url}/{i}", cookies=cookies, **kwargs)
            for i in (propia.id, ajena.id, uuid.uuid4())
        ]
        assert {r.status_code for r in respuestas} == {403}
        assert len({r.content for r in respuestas}) == 1


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
async def test_staff_con_solo_catalogo_editar_crea_edita_y_anula(client, db_session, tenant_a, cat):
    empleado = await _empleado(db_session, tenant_a, frozenset({Permission.CATALOGO_EDITAR}))
    cookies = session_cookies(empleado)

    alta = await client.post(
        cat.url, json=cat.alta(1), headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}, cookies=cookies
    )
    assert alta.status_code == 201, alta.text
    nuevo = alta.json()["id"]

    cambio = await client.patch(f"{cat.url}/{nuevo}", json=cat.cambio, cookies=cookies)
    assert cambio.status_code == 200, cambio.text
    campo, valor = cat.campo_cambiado
    fila = await db_session.scalar(select(cat.modelo).where(cat.modelo.id == uuid.UUID(nuevo)))
    assert fila is not None and fila.tenant_id == tenant_a.id
    await db_session.refresh(fila)
    assert getattr(fila, campo) == valor

    baja = await client.delete(f"{cat.url}/{nuevo}", cookies=cookies)
    assert baja.status_code == 204, baja.text
    await db_session.refresh(fila)
    assert fila.voided_at is not None, "DELETE anula (VoidableMixin), no borra"
    # anulado = no existe para la API
    assert (await client.get(f"{cat.url}/{nuevo}", cookies=cookies)).status_code == 404
    assert nuevo not in (await client.get(cat.url, cookies=cookies)).text


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
async def test_el_owner_escribe_catalogos_sin_perfil(client, db_session, tenant_a, cookies_a, cat):
    alta = await client.post(cat.url, json=cat.alta(2), cookies=cookies_a)
    assert alta.status_code == 201, alta.text
    nuevo = alta.json()["id"]
    assert (
        await client.patch(f"{cat.url}/{nuevo}", json=cat.cambio, cookies=cookies_a)
    ).status_code == 200
    assert (await client.delete(f"{cat.url}/{nuevo}", cookies=cookies_a)).status_code == 204


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
@pytest.mark.parametrize(
    "permisos",
    [frozenset({Permission.AGENDA_VER}), DEFAULT_PROFILES["Lavador"]],
    ids=["solo-agenda", "Lavador"],
)
async def test_cualquier_staff_lee_los_catalogos(client, db_session, tenant_a, cat, permisos):
    empleado = await _empleado(db_session, tenant_a, permisos)
    cookies = session_cookies(empleado)
    fila = await _fila_de(db_session, cat, tenant_a)

    listado = await client.get(cat.url, cookies=cookies)
    assert listado.status_code == 200, listado.text
    assert [i["id"] for i in listado.json()["items"]] == [str(fila.id)]
    uno = await client.get(f"{cat.url}/{fila.id}", cookies=cookies)
    assert uno.status_code == 200, uno.text
    assert uno.json()["id"] == str(fila.id)


@pytest.mark.parametrize("cat", CATALOGOS, ids=CATALOGO_IDS)
async def test_sin_sesion_los_catalogos_son_401(client, db_session, tenant_a, cat):
    fila = await _fila_de(db_session, cat, tenant_a)
    for metodo, ruta, kwargs in (
        ("GET", cat.url, {}),
        ("GET", f"{cat.url}/{fila.id}", {}),
        ("POST", cat.url, {"json": cat.alta(1)}),
        ("PATCH", f"{cat.url}/{fila.id}", {"json": cat.cambio}),
        ("DELETE", f"{cat.url}/{fila.id}", {}),
    ):
        r = await client.request(metodo, ruta, **kwargs)
        assert r.status_code == 401, (metodo, ruta, r.text)


async def test_un_cambio_de_perfil_rige_en_el_request_siguiente_para_catalogos(
    client, db_session, tenant_a
):
    """§2.3: el permiso se lee de la base en cada request, no del token."""
    perfil = await make_profile(db_session, tenant_a, "cambiante", {Permission.AGENDA_VER})
    empleado = await make_user(
        db_session, tenant_a, Role.STAFF, "cambiante@example.invalid", profile=perfil
    )
    cookies = session_cookies(empleado)
    url = CATALOGOS[1].url
    assert (await client.post(url, json={"name": "P1"}, cookies=cookies)).status_code == 403
    perfil.permissions = [Permission.AGENDA_VER.value, Permission.CATALOGO_EDITAR.value]
    await db_session.flush()
    assert (await client.post(url, json={"name": "P1"}, cookies=cookies)).status_code == 201


# ── /settings: leer cualquiera, editar solo el OWNER ───────────────────────────


@pytest.mark.parametrize(
    "permisos",
    [ALL_PERMISSIONS, DEFAULT_PROFILES["Lavador"], frozenset({Permission.AGENDA_VER})],
    ids=["Encargado", "Lavador", "solo-agenda"],
)
async def test_cualquier_staff_lee_settings(client, db_session, tenant_a, permisos):
    await _asegurar_settings(db_session, tenant_a)
    empleado = await _empleado(db_session, tenant_a, permisos)
    r = await client.get(SETTINGS_URL, cookies=session_cookies(empleado))
    assert r.status_code == 200, r.text
    body = r.json()
    for campo in ("deposit_enabled", "deposit_bps", "hold_min", "min_advance_min"):
        assert campo in body, campo


@pytest.mark.parametrize(
    "body",
    [
        {"deposit_enabled": True},
        {"deposit_bps": 5000},
        {"address": "Otra dirección"},
        {"tolerance_min": 60},
    ],
    ids=["sena-on", "sena-bps", "direccion", "tolerancia"],
)
async def test_un_encargado_no_edita_settings(client, db_session, tenant_a, body):
    """Y5/A4: aun con TODOS los permisos, configurar el negocio es del OWNER."""
    fila = await _asegurar_settings(db_session, tenant_a)
    antes = (fila.deposit_enabled, fila.deposit_bps, fila.address, fila.tolerance_min)
    encargado = await _empleado(db_session, tenant_a, ALL_PERMISSIONS)
    r = await client.patch(SETTINGS_URL, json=body, cookies=session_cookies(encargado))
    assert r.status_code == 403, r.text
    assert _codigo(r) == "FORBIDDEN"
    await db_session.refresh(fila)
    assert (fila.deposit_enabled, fila.deposit_bps, fila.address, fila.tolerance_min) == antes


async def test_el_owner_edita_settings_y_se_ve_en_el_get(client, db_session, tenant_a, cookies_a):
    await _asegurar_settings(db_session, tenant_a)
    r = await client.patch(
        SETTINGS_URL,
        json={"deposit_enabled": True, "deposit_bps": 2500, "address": "Calle Sintética 1"},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert r.json()["deposit_enabled"] is True and r.json()["deposit_bps"] == 2500
    leido = (await client.get(SETTINGS_URL, cookies=cookies_a)).json()
    assert leido["address"] == "Calle Sintética 1" and leido["deposit_bps"] == 2500


async def test_owner_apaga_la_sena_y_el_porcentaje_se_conserva(
    client, db_session, tenant_a, cookies_a
):
    """§3.2: `deposit_bps` se conserva con la seña apagada (nunca 0)."""
    await _asegurar_settings(db_session, tenant_a)
    await client.patch(
        SETTINGS_URL, json={"deposit_enabled": True, "deposit_bps": 3000}, cookies=cookies_a
    )
    r = await client.patch(SETTINGS_URL, json={"deposit_enabled": False}, cookies=cookies_a)
    assert r.status_code == 200, r.text
    assert r.json()["deposit_enabled"] is False and r.json()["deposit_bps"] == 3000


@pytest.mark.parametrize("bps", [0, -1, 10001], ids=["cero", "negativo", "mas-de-100"])
async def test_owner_no_puede_poner_un_porcentaje_fuera_de_rango(
    client, db_session, tenant_a, cookies_a, bps
):
    await _asegurar_settings(db_session, tenant_a)
    r = await client.patch(SETTINGS_URL, json={"deposit_bps": bps}, cookies=cookies_a)
    assert r.status_code == 422, r.text


async def test_sin_sesion_settings_es_401(client):
    assert (await client.get(SETTINGS_URL)).status_code == 401
    assert (await client.patch(SETTINGS_URL, json={"address": "x"})).status_code == 401
