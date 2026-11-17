# ruff: noqa: E501, F841, E731, C408
"""Ayudas del tester T3 de la F6 (6.1). Datos sintéticos: `@example.com`, `+54 9 351 000-xxxx`."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.permissions import Permission
from app.domain.roles import Role
from app.tests.application._armado import Lavadero
from app.tests.conftest import make_profile, make_user, session_cookies

CORDOBA = ZoneInfo("America/Argentina/Cordoba")


def clave() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{uuid.uuid4()}"}


def iso(instante: datetime) -> str:
    return instante.astimezone(UTC).isoformat()


def dt(texto: str) -> datetime:
    instante = datetime.fromisoformat(texto)
    return instante if instante.tzinfo else instante.replace(tzinfo=UTC)


def codigo(r) -> str:
    return r.json()["detail"]["code"]


def local(dia, hora: int, minuto: int = 0, segundo: int = 0, zona=CORDOBA) -> datetime:
    return datetime.combine(dia, time(hora, minuto, segundo), tzinfo=zona)


async def empleado(db_session, tenant, nombre: str, permisos: set[Permission]):
    perfil = await make_profile(db_session, tenant, nombre, permisos)
    user = await make_user(
        db_session,
        tenant,
        Role.STAFF,
        f"{nombre.lower().replace(' ', '')}@example.com",
        profile=perfil,
    )
    return session_cookies(user)


async def ingreso(client, cookies, lv: Lavadero, *, cuando: datetime | None = None, **extra):
    cuerpo = {
        "occurred_at": iso(cuando or datetime.now(UTC)),
        "plate": "AB123CD",
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Demo",
        "customer_phone": "+54 9 351 000-0101",
        **extra,
    }
    return await client.post("/v1/jobs", json=cuerpo, cookies=cookies, headers=clave())


async def accion(
    client,
    cookies,
    job_id,
    nombre: str,
    cuando: datetime | None = None,
    k: str | None = None,
    **extra,
):
    cuerpo = {"occurred_at": iso(cuando or datetime.now(UTC)), **extra}
    headers = {"Idempotency-Key": k} if k is not None else clave()
    return await client.post(
        f"/v1/jobs/{job_id}/{nombre}", json=cuerpo, cookies=cookies, headers=headers
    )


async def cobrar(
    client, cookies, lv, job_id, monto: int | None = None, k: str | None = None, **extra
):
    cuerpo: dict[str, Any] = {
        "occurred_at": iso(datetime.now(UTC)),
        "payment_method_id": str(lv.efectivo),
        **extra,
    }
    if monto is not None:
        cuerpo["amount_cents"] = monto
    return await client.post(
        f"/v1/jobs/{job_id}/payments",
        json=cuerpo,
        cookies=cookies,
        headers={"Idempotency-Key": k or f"k-{uuid.uuid4()}"},
    )


async def job_en(client, cookies, lv, estado: str) -> dict:
    """Un job de ingreso llevado hasta `estado` (PRESENTE, EN_PROCESO, FINALIZADO, COBRADO, RETIRADO)."""
    j = (await ingreso(client, cookies, lv, cuando=datetime.now(UTC) - timedelta(hours=2))).json()
    orden = ["PRESENTE", "EN_PROCESO", "FINALIZADO", "COBRADO", "RETIRADO"]
    if orden.index(estado) >= 1:
        j = (await accion(client, cookies, j["id"], "start")).json()
    if orden.index(estado) >= 2:
        j = (await accion(client, cookies, j["id"], "finish")).json()
    if orden.index(estado) >= 3:
        j = (await cobrar(client, cookies, lv, j["id"])).json()
    if orden.index(estado) >= 4:
        j = (await accion(client, cookies, j["id"], "pick-up")).json()
    assert j["status"] == estado, j
    return j


async def eventos(client, cookies, job_id) -> list[dict]:
    r = await client.get(f"/v1/jobs/{job_id}/events", params={"limit": 200}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def retro(lv: Lavadero, dia, desde=(10, 0), hasta=(11, 0), **extra) -> dict[str, Any]:
    return {
        "started_at": iso(local(dia, *desde)),
        "finished_at": iso(local(dia, *hasta)),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Ayer",
        "plate": "XY987ZW",
        **extra,
    }


def a_mano(lv: Lavadero, inicio: datetime, **extra) -> dict[str, Any]:
    return {
        "start_at": iso(inicio),
        "resource_id": str(lv.puesto),
        "vehicle_size_id": str(lv.auto),
        "service_ids": [str(lv.lavado)],
        "customer_name": "Cliente Mostrador",
        "customer_phone": "+54 9 351 000-0202",
        **extra,
    }
