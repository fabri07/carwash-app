"""B8 · FASE-3-CONTRATO §4 — los servicios no cruzan lavaderos, en la suite rápida (SQLite).

Tester-aislamiento de T3. Sin RLS: lo único que separa a A de B es el `WHERE tenant_id = …`
de los repositorios que usan los servicios (la **tercera red**). La misma tabla de intentos
corre contra Postgres con el rol real en `test_aislamiento_servicios_pg.py`; acá corre en cada
`make test-cov`, y es la que se rompe primero si un servicio o un repositorio pierde el filtro.

Por cada intento (`_intentos_servicios.INTENTOS`): B pasa un id de A a una operación que con
ese id funcionaría → `NotFoundError` de la tabla esperada, igual al de un id inexistente, y
ninguna fila de A cambia (se hace `flush` aunque haya fallado: el peor llamador).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.errors import NotFoundError
from app.domain.enums import BookingStatus
from app.tests.application._armado import Lavadero, T, armar
from app.tests.security._intentos_servicios import (
    CONTROL_NO_APLICA,
    CUBIERTAS_APARTE,
    INTENTOS,
    Ids,
    armar_a,
    armar_b,
    operaciones_con_id,
    sin_id,
)
from app.tests.security._poblar_dominio import tablas_tenant


@dataclass
class Escenario:
    session: AsyncSession
    a: Lavadero
    b: Lavadero
    ids_a: Ids
    ids_b: Ids


@pytest_asyncio.fixture
async def esc(db_session: AsyncSession) -> Escenario:
    a = await armar(db_session, "Lavadero A")
    b = await armar(db_session, "Lavadero B")
    return Escenario(db_session, a, b, await armar_a(a), await armar_b(b))


async def _foto(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, list[str]]:
    await session.flush()
    foto: dict[str, list[str]] = {}
    for tabla in tablas_tenant():
        filas = await session.execute(
            select(tabla).where(tabla.c.tenant_id == tenant_id).order_by(tabla.c.id)
        )
        foto[tabla.name] = [repr(tuple(f)) for f in filas.all()]
    return foto


async def _intentar(
    session: AsyncSession, fn: Callable[[], Awaitable[Any]]
) -> BaseException | None:
    """Corre `fn` en un SAVEPOINT y lo **libera igual** si falló (el peor llamador)."""
    savepoint = await session.begin_nested()
    error: BaseException | None = None
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001
        error = exc
    try:
        await session.flush()
        await savepoint.commit()
    except DBAPIError:
        await savepoint.rollback()
    return error


def test_toda_operacion_publica_con_id_esta_cubierta() -> None:
    cubiertas = {i.cubre for i in INTENTOS.values()} | set(CUBIERTAS_APARTE)
    con_id = operaciones_con_id()
    assert len(con_id) >= 25  # piso: si la introspección se rompe, no pasa en silencio
    faltan = con_id - cubiertas
    assert (
        not faltan
    ), f"operaciones que reciben un id y no se atacan con uno ajeno: {sorted(faltan)}"
    assert not cubiertas - con_id, f"casos de operaciones que ya no existen: {cubiertas - con_id}"


@pytest.mark.parametrize("nombre", sorted(INTENTOS))
async def test_b_con_un_id_de_a_recibe_not_found_indistinguible_y_a_no_cambia(
    esc: Escenario, nombre: str
) -> None:
    intento = INTENTOS[nombre]
    id_a = esc.ids_a[intento.de_a]
    inexistente = uuid.uuid4()
    antes = await _foto(esc.session, esc.a.tenant_id)

    ajeno = await _intentar(esc.session, lambda: intento.llamar(esc.b, esc.ids_b, id_a))
    nada = await _intentar(esc.session, lambda: intento.llamar(esc.b, esc.ids_b, inexistente))

    assert await _foto(esc.session, esc.a.tenant_id) == antes, "B cambió filas de A"
    assert isinstance(ajeno, NotFoundError), f"{nombre}: {type(ajeno).__name__}: {ajeno}"
    assert ajeno.entity == intento.tabla
    assert type(nada) is type(ajeno)
    assert isinstance(nada, NotFoundError) and nada.entity == ajeno.entity
    assert sin_id(str(ajeno), id_a) == sin_id(str(nada), inexistente)


@pytest.mark.parametrize("nombre", sorted(set(INTENTOS) - CONTROL_NO_APLICA))
async def test_el_control_con_el_tenant_a_funciona(esc: Escenario, nombre: str) -> None:
    """Con su propio tenant, la fila de A sirve para la operación: el `NotFoundError` de B
    viene del tenant, no del estado."""
    intento = INTENTOS[nombre]
    savepoint = await esc.session.begin_nested()
    try:
        await intento.llamar(esc.a, esc.ids_a, esc.ids_a[intento.de_a])
    finally:
        await savepoint.rollback()


async def test_expire_holds_con_el_puesto_de_a_no_vence_nada(esc: Escenario) -> None:
    antes = await _foto(esc.session, esc.a.tenant_id)
    vencidos = await esc.b.turnos.expire_holds(esc.ids_a["puesto"], T + timedelta(days=30))
    assert vencidos == []
    assert await _foto(esc.session, esc.a.tenant_id) == antes
    turno = await esc.a.turnos.get(esc.ids_a["booking_pend_sena"])
    assert turno.status == BookingStatus.PENDIENTE_SENA
