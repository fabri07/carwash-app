# ruff: noqa: E501, F841, E731, C408
from datetime import time

import pytest_asyncio

from app.tests.application._armado import Lavadero, armar_catalogo


@pytest_asyncio.fixture
async def lav(db_session, tenant_a, owner) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_a.id, owner.id)
    for dia in range(1, 8):
        await lv.catalogo.add_business_hours(dia, time(0, 0), time(23, 59))
    return lv


@pytest_asyncio.fixture
async def lav_b(db_session, tenant_b, owner_b) -> Lavadero:
    lv = await armar_catalogo(db_session, tenant_b.id, owner_b.id)
    for dia in range(1, 8):
        await lv.catalogo.add_business_hours(dia, time(0, 0), time(23, 59))
    return lv
