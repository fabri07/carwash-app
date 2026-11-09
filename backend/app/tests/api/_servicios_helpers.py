"""Armado compartido de los tests de servicios y plantillas (PR 4.3)."""

from typing import Any

from httpx import AsyncClient


async def crear_tamanos(
    client: AsyncClient, cookies: dict[str, str], *codigos: str
) -> dict[str, str]:
    """`{código: id}` de tamaños recién creados (por defecto AUTO y SUV)."""
    ids: dict[str, str] = {}
    for codigo in codigos or ("AUTO", "SUV"):
        r = await client.post(
            "/v1/vehicle-sizes", json={"code": codigo, "label": codigo.title()}, cookies=cookies
        )
        assert r.status_code == 201, r.text
        ids[codigo] = r.json()["id"]
    return ids


def precios(tamanos: dict[str, str], base: int = 2_000_000) -> list[dict[str, Any]]:
    """Un precio por tamaño: el primero a `base`, cada siguiente un 25 % más caro."""
    return [
        {
            "vehicle_size_id": size_id,
            "price_cents": base + i * base // 4,
            "duration_min": 60 + 30 * i,
        }
        for i, size_id in enumerate(tamanos.values())
    ]


def cuerpo_servicio(tamanos: dict[str, str], name: str = "Lavado exterior", **extra: Any) -> dict:
    return {"name": name, "pricing_mode": "PRECIO_FIJO", "prices": precios(tamanos), **extra}


async def crear_servicio(
    client: AsyncClient, cookies: dict[str, str], tamanos: dict[str, str], name: str, **extra: Any
) -> dict[str, Any]:
    r = await client.post(
        "/v1/services", json=cuerpo_servicio(tamanos, name, **extra), cookies=cookies
    )
    assert r.status_code == 201, r.text
    return r.json()
