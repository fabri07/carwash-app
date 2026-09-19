"""B8 (y B9 a nivel repositorio) · FASE-3-CONTRATO — los repositorios no cruzan lavaderos.

Escrito por el Tester-aislamiento de T3, que no escribió los repositorios. Corre sobre
**SQLite** (suite rápida): sin RLS, lo único que separa a A de B es el `WHERE tenant_id = …`
de cada consulta, la **tercera red**. Si un repositorio se olvida el filtro, acá se ve.

El test es genérico, por introspección de `app.persistence.repositories`:

- todo `BaseRepository` descubierto pasa por `get_by_id`, `get_for_update`, `list_by_tenant`
  (y su `total`) y `void` con ids de A pedidos por B;
- todo método **propio** de un repositorio que recibe `tenant_id` tiene que tener un caso en
  `CASOS_PROPIOS`; uno que no lo recibe tiene que estar en `EXENTOS` con el motivo. Un
  repositorio o un método nuevo sin cubrir hace fallar `test_todo_*` con su nombre.

Cada caso propio corre dos veces con los mismos argumentos de A: con el tenant A tiene que
encontrar algo (control: el caso no es trivialmente vacío) y con el tenant B, nada.

Datos: una fila por tabla de tenant con las recetas de `_poblar_dominio` (sintéticas).
"""

import importlib
import inspect
import pkgutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.persistence.repositories as paquete_repos
from app.domain.void import VoidReason
from app.persistence.db.base import Base
from app.persistence.repositories.base import BaseRepository, Page
from app.tests.conftest import make_tenant
from app.tests.security._poblar_dominio import RECETAS, _Contexto, tablas_tenant

#: Un "ahora" posterior a todo hold sembrado: con el tenant correcto, vence el de A.
MUY_TARDE = datetime(2030, 1, 1, tzinfo=UTC)


# ── Descubrimiento ────────────────────────────────────────────────────────────


def _clases_de_repositorios() -> list[type[Any]]:
    """Toda clase `*Repository` definida en `app.persistence.repositories.*`."""
    clases: list[type[Any]] = []
    for info in pkgutil.iter_modules(paquete_repos.__path__):
        modulo = importlib.import_module(f"{paquete_repos.__name__}.{info.name}")
        for _, obj in inspect.getmembers(modulo, inspect.isclass):
            if obj.__module__ == modulo.__name__ and obj.__name__.endswith("Repository"):
                clases.append(obj)
    return sorted(clases, key=lambda c: c.__name__)


CLASES = _clases_de_repositorios()
REPOS_BASE: list[type[BaseRepository[Any]]] = [
    c for c in CLASES if issubclass(c, BaseRepository) and c is not BaseRepository
]
#: Repositorios que NO heredan de `BaseRepository` y que este archivo cubre a mano.
#: `JobEventRepository`: `job_events` es append-only, sin anulación (X8).
REPOS_SIN_BASE_CUBIERTOS = {"JobEventRepository"}
#: Métodos del repositorio sin base que pasan por los tests genéricos de lectura.
GENERICOS_SIN_BASE = {("JobEventRepository", "get_by_id"), ("JobEventRepository", "list_by_tenant")}

METODOS_BASE = {n for n, _ in inspect.getmembers(BaseRepository, inspect.isfunction)}


def _metodos_propios(cls: type[Any]) -> list[tuple[str, bool]]:
    """(nombre, recibe `tenant_id`) de los métodos públicos async que la clase define."""
    propios = []
    for nombre, fn in vars(cls).items():
        if nombre.startswith("_") or not inspect.iscoroutinefunction(fn):
            continue
        if issubclass(cls, BaseRepository) and nombre in METODOS_BASE:
            continue
        propios.append((nombre, "tenant_id" in inspect.signature(fn).parameters))
    return propios


# ── Siembra (SQLite, sin RLS) ─────────────────────────────────────────────────


@dataclass
class Lavadero:
    tenant_id: uuid.UUID
    ids: dict[str, uuid.UUID]
    #: Claves naturales de A: teléfono, patente, código de medio, claves de idempotencia.
    claves: dict[str, str]
    #: Pago de A sin job (seña antes de recibir): control de `lock_unlinked_for_booking`.
    pago_sin_job: uuid.UUID


async def _poblar_en_sesion(session: AsyncSession, tenant_id: uuid.UUID) -> Lavadero:
    """Lo mismo que `_poblar_dominio.poblar`, sobre la sesión del test (SQLite, rollback)."""
    ctx = _Contexto(tenant_id)
    for tabla in tablas_tenant():
        fila_id = uuid.uuid4()
        valores = {"id": fila_id, "tenant_id": tenant_id, **RECETAS[tabla.name](ctx)}
        await session.execute(insert(tabla).values(**valores))
        ctx.ids[tabla.name] = fila_id
    bookings = Base.metadata.tables["bookings"]
    await session.execute(
        update(bookings).where(bookings.c.id == ctx["bookings"]).values(quote_id=ctx["quotes"])
    )
    pagos = Base.metadata.tables["payments"]
    pago_sin_job = uuid.uuid4()
    await session.execute(
        insert(pagos).values(
            id=pago_sin_job,
            **{
                **RECETAS["payments"](ctx),
                "tenant_id": tenant_id,
                "job_id": None,
                "idempotency_key": f"sena-sin-job-{ctx.tag}",
                "legacy_id": None,
            },
        )
    )
    receta = {t: RECETAS[t](ctx) for t in RECETAS}
    claves = {
        "phone": receta["customers"]["phone_e164"],
        "plate": receta["vehicles"]["plate_normalized"],
        "code": receta["payment_methods"]["code"],
        "payment_key": receta["payments"]["idempotency_key"],
        "event_key": receta["job_events"]["idempotency_key"],
    }
    return Lavadero(tenant_id, dict(ctx.ids), claves, pago_sin_job)


@pytest_asyncio.fixture
async def dos(db_session: AsyncSession) -> tuple[Lavadero, Lavadero]:
    a = await make_tenant(db_session, "Lavadero A")
    b = await make_tenant(db_session, "Lavadero B")
    return await _poblar_en_sesion(db_session, a.id), await _poblar_en_sesion(db_session, b.id)


async def _filas_de(session: AsyncSession, tabla: str, tenant_id: uuid.UUID) -> set[uuid.UUID]:
    t = Base.metadata.tables[tabla]
    return set((await session.scalars(select(t.c.id).where(t.c.tenant_id == tenant_id))).all())


def _ids(resultado: Any) -> set[uuid.UUID]:
    if resultado is None:
        return set()
    if isinstance(resultado, Page):
        return {x.id for x in resultado.items}
    if isinstance(resultado, list):
        return {x.id for x in resultado}
    return {resultado.id}


# ── Cobertura: nada queda afuera sin que se note ─────────────────────────────


def test_todo_repositorio_es_base_o_esta_cubierto_a_mano() -> None:
    sueltos = {c.__name__ for c in CLASES if not issubclass(c, BaseRepository)}
    assert sueltos == REPOS_SIN_BASE_CUBIERTOS, (
        f"repositorios que no heredan de BaseRepository y no tienen casos acá: "
        f"{sorted(sueltos - REPOS_SIN_BASE_CUBIERTOS)}"
    )
    assert len(REPOS_BASE) >= 18  # piso: si la introspección se rompe, no pasa en silencio


def test_todo_repositorio_tiene_una_fila_de_prueba() -> None:
    sin_receta = sorted(c.__name__ for c in REPOS_BASE if c.model.__tablename__ not in RECETAS)
    assert not sin_receta, f"repos cuyo modelo no tiene receta en _poblar_dominio: {sin_receta}"


# ── Métodos genéricos (BaseRepository) ───────────────────────────────────────


@pytest.mark.parametrize("repo_cls", REPOS_BASE, ids=lambda c: c.__name__)
async def test_get_by_id_de_un_id_ajeno_es_none(
    db_session: AsyncSession, dos: tuple[Lavadero, Lavadero], repo_cls: type[BaseRepository[Any]]
) -> None:
    a, b = dos
    repo = repo_cls(db_session)
    id_a = a.ids[repo_cls.model.__tablename__]
    assert (await repo.get_by_id(id_a, a.tenant_id)) is not None  # control
    assert await repo.get_by_id(id_a, b.tenant_id) is None
    assert await repo.get_by_id(id_a, b.tenant_id, include_voided=True) is None
    assert await repo.get_for_update(id_a, b.tenant_id) is None


@pytest.mark.parametrize("repo_cls", REPOS_BASE, ids=lambda c: c.__name__)
async def test_list_by_tenant_no_trae_ni_cuenta_filas_ajenas(
    db_session: AsyncSession, dos: tuple[Lavadero, Lavadero], repo_cls: type[BaseRepository[Any]]
) -> None:
    a, b = dos
    tabla = repo_cls.model.__tablename__
    de_a = await _filas_de(db_session, tabla, a.tenant_id)
    de_b = await _filas_de(db_session, tabla, b.tenant_id)
    assert de_a and de_b
    for include_voided in (False, True):
        page = await repo_cls(db_session).list_by_tenant(
            b.tenant_id, limit=1000, include_voided=include_voided
        )
        assert _ids(page) == de_b
        assert page.total == len(de_b)
        assert not _ids(page) & de_a


@pytest.mark.parametrize("repo_cls", REPOS_BASE, ids=lambda c: c.__name__)
async def test_void_de_un_id_ajeno_es_none_y_no_anula(
    db_session: AsyncSession, dos: tuple[Lavadero, Lavadero], repo_cls: type[BaseRepository[Any]]
) -> None:
    a, b = dos
    tabla = Base.metadata.tables[repo_cls.model.__tablename__]
    id_a = a.ids[tabla.name]
    assert await repo_cls(db_session).void(id_a, b.tenant_id, VoidReason.ERROR_DE_CARGA) is None
    voided = await db_session.scalar(select(tabla.c.voided_at).where(tabla.c.id == id_a))
    assert voided is None
    # Control positivo: con el tenant A, la misma llamada sí anula.
    assert await repo_cls(db_session).void(id_a, a.tenant_id, VoidReason.ERROR_DE_CARGA)
    voided = await db_session.scalar(select(tabla.c.voided_at).where(tabla.c.id == id_a))
    assert voided is not None


async def test_job_events_get_by_id_y_list_by_tenant_no_cruzan(
    db_session: AsyncSession, dos: tuple[Lavadero, Lavadero]
) -> None:
    from app.persistence.repositories.jobs import JobEventRepository  # noqa: PLC0415

    a, b = dos
    repo = JobEventRepository(db_session)
    assert await repo.get_by_id(a.ids["job_events"], a.tenant_id) is not None
    assert await repo.get_by_id(a.ids["job_events"], b.tenant_id) is None
    page = await repo.list_by_tenant(b.tenant_id, limit=1000)
    de_b = await _filas_de(db_session, "job_events", b.tenant_id)
    assert _ids(page) == de_b and page.total == len(de_b)


# ── Métodos propios ──────────────────────────────────────────────────────────

Caso = Callable[[Any, Lavadero, uuid.UUID], Awaitable[Any]]


async def _ocupa_el_horario(repo: Any, a: Lavadero, tenant_id: uuid.UUID) -> Any:
    """`slot_taken` con un turno sonda en el puesto y el horario del turno de A: devuelve el
    turno de A si lo ve ocupando (bool → fila, para compararlo como los demás casos)."""
    from app.persistence.models import Booking  # noqa: PLC0415

    turno = await repo._session.get(Booking, a.ids["bookings"])
    sonda = Booking(
        id=uuid.uuid4(),
        resource_id=turno.resource_id,
        start_at=turno.start_at,
        end_at=turno.end_at,
    )
    return turno if await repo.slot_taken(sonda, tenant_id) else None


#: Cada caso recibe (repo, datos de A, tenant con el que se pregunta).
CASOS_PROPIOS: dict[tuple[str, str], Caso] = {
    ("BookingRepository", "lock_expired_holds"): lambda r, a, t: r.lock_expired_holds(
        a.ids["resources"], MUY_TARDE, t
    ),
    ("BookingRepository", "slot_taken"): _ocupa_el_horario,
    ("CancellationRepository", "find_by_booking"): lambda r, a, t: r.find_by_booking(
        a.ids["bookings"], t
    ),
    ("ServicePriceRepository", "find_for"): lambda r, a, t: r.find_for(
        a.ids["services"], a.ids["vehicle_sizes"], t
    ),
    ("ServicePriceRepository", "list_for_service"): lambda r, a, t: r.list_for_service(
        a.ids["services"], t
    ),
    ("PaymentMethodRepository", "find_by_code"): lambda r, a, t: r.find_by_code(
        a.claves["code"], t
    ),
    ("CustomerRepository", "find_by_phone"): lambda r, a, t: r.find_by_phone(a.claves["phone"], t),
    ("VehicleRepository", "find_by_plate"): lambda r, a, t: r.find_by_plate(a.claves["plate"], t),
    ("CustomerVehicleRepository", "find_active_link"): lambda r, a, t: r.find_active_link(
        a.ids["customers"], a.ids["vehicles"], t
    ),
    ("JobRepository", "find_by_booking"): lambda r, a, t: r.find_by_booking(a.ids["bookings"], t),
    ("JobInspectionRepository", "find_by_job"): lambda r, a, t: r.find_by_job(a.ids["jobs"], t),
    ("JobEventRepository", "get_by_key"): lambda r, a, t: r.get_by_key(a.claves["event_key"], t),
    ("JobEventRepository", "list_for_job"): lambda r, a, t: r.list_for_job(a.ids["jobs"], t),
    ("PaymentRepository", "get_by_key"): lambda r, a, t: r.get_by_key(a.claves["payment_key"], t),
    ("PaymentRepository", "list_for_job"): lambda r, a, t: r.list_for_job(a.ids["jobs"], t),
    ("PaymentRepository", "list_for_booking"): lambda r, a, t: r.list_for_booking(
        a.ids["bookings"], t
    ),
    ("PaymentRepository", "lock_unlinked_for_booking"): lambda r, a, t: (
        r.lock_unlinked_for_booking(a.ids["bookings"], t)
    ),
    ("CashMovementRepository", "find_by_payment"): lambda r, a, t: r.find_by_payment(
        a.ids["payments"], t
    ),
    ("UserRepository", "get_active"): lambda r, a, t: r.get_active(a.ids["users"], t),
}

#: Métodos propios sin `tenant_id`, con el motivo por el que no cruzan.
EXENTOS: dict[tuple[str, str], str] = {
    ("UserRepository", "find_login_candidate"): (
        "login: identidad antes de conocer el tenant, por la función SECURITY DEFINER "
        "(test_aislamiento_tenants*.py de F2)"
    ),
    ("JobEventRepository", "add"): (
        "inserta el evento con el tenant_id que trae; WITH CHECK y la FK compuesta lo "
        "cubren (B7)"
    ),
}


def test_todo_metodo_propio_esta_cubierto_o_exento() -> None:
    con_tenant: set[tuple[str, str]] = set()
    sin_tenant: set[tuple[str, str]] = set()
    for cls in CLASES:
        if cls is BaseRepository:
            continue
        for nombre, recibe_tenant in _metodos_propios(cls):
            (con_tenant if recibe_tenant else sin_tenant).add((cls.__name__, nombre))
    faltan = con_tenant - set(CASOS_PROPIOS) - GENERICOS_SIN_BASE
    assert not faltan, f"métodos con tenant_id sin caso de aislamiento: {sorted(faltan)}"
    sin_motivo = sin_tenant - set(EXENTOS)
    assert not sin_motivo, f"métodos sin tenant_id y sin motivo en EXENTOS: {sorted(sin_motivo)}"
    viejos = (set(CASOS_PROPIOS) | set(EXENTOS)) - con_tenant - sin_tenant
    assert not viejos, f"casos de métodos que ya no existen: {sorted(viejos)}"


def _clase(nombre: str) -> type[Any]:
    return next(c for c in CLASES if c.__name__ == nombre)


@pytest.mark.parametrize("clave", sorted(CASOS_PROPIOS), ids=lambda k: f"{k[0]}.{k[1]}")
async def test_metodo_propio_con_datos_de_a_y_tenant_b_no_encuentra_nada(
    db_session: AsyncSession, dos: tuple[Lavadero, Lavadero], clave: tuple[str, str]
) -> None:
    a, b = dos
    caso = CASOS_PROPIOS[clave]
    repo = _clase(clave[0])(db_session)
    propio = _ids(await caso(repo, a, a.tenant_id))
    assert propio, "control: con el tenant A el caso tiene que encontrar algo"
    assert _ids(await caso(repo, a, b.tenant_id)) == set()


# ── B9 en el repositorio: la misma clave en A y B, cada uno recibe la suya ────

#: (clave de `Lavadero.claves`, tabla, columna, método de búsqueda).
CLAVES_COMPARTIDAS = [
    ("phone", "customers", "phone_e164", ("CustomerRepository", "find_by_phone")),
    ("plate", "vehicles", "plate_normalized", ("VehicleRepository", "find_by_plate")),
    ("code", "payment_methods", "code", ("PaymentMethodRepository", "find_by_code")),
    ("payment_key", "payments", "idempotency_key", ("PaymentRepository", "get_by_key")),
    ("event_key", "job_events", "idempotency_key", ("JobEventRepository", "get_by_key")),
]


@pytest.mark.parametrize(
    ("clave", "tabla", "columna", "metodo"),
    CLAVES_COMPARTIDAS,
    ids=[c[0] for c in CLAVES_COMPARTIDAS],
)
async def test_la_misma_clave_en_a_y_b_devuelve_la_fila_propia(
    db_session: AsyncSession,
    dos: tuple[Lavadero, Lavadero],
    clave: str,
    tabla: str,
    columna: str,
    metodo: tuple[str, str],
) -> None:
    a, b = dos
    t = Base.metadata.tables[tabla]
    valor = a.claves[clave]
    await db_session.execute(update(t).where(t.c.id == b.ids[tabla]).values({columna: valor}))
    cuantas = await db_session.scalar(
        select(func.count()).select_from(t).where(t.c[columna] == valor)
    )
    assert cuantas == 2  # conviven: el único es por tenant
    buscar = getattr(_clase(metodo[0])(db_session), metodo[1])
    assert (await buscar(valor, a.tenant_id)).id == a.ids[tabla]
    assert (await buscar(valor, b.tenant_id)).id == b.ids[tabla]
