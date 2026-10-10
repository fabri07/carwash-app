"""FASE-4-CONTRATO §3.1 y A6 — cuentas y perfiles contra Postgres real (PR 4.1).

Escrito por el Tester-aislamiento del PR 4.1, que no escribió la migración. Cubre lo que
SQLite no puede: los CHECK de `users` y `permission_profiles` (el de permisos y el de formato
de `username` son solo Postgres), la FK compuesta `users → permission_profiles`, la función
`auth_lookup_user(identifier)` con el rol de runtime y el **relleno de la migración 0004**
sobre datos con el esquema de 0003.

Las filas de los CHECK se insertan con el superusuario (los constraints aplican igual; RLS se
prueba en `security/`); la función del login y la FK compuesta, con `carwash_app`.

Datos sintéticos: `@ejemplo.invalid`, usuarios inventados.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.permissions import DEFAULT_PROFILES, LEGACY_STAFF_PROFILE, Permission
from app.persistence.db.tenant_context import set_tenant_context
from app.tests.conftest_pg import _with_driver, migrate_head, owner_url

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio(loop_scope="session")]

HASH = "no-es-un-hash"


async def _perfil(
    admin: AsyncEngine, tenant: uuid.UUID, name: str, permisos: list[str] | None = None
) -> uuid.UUID:
    perfil = uuid.uuid4()
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO permission_profiles (id, tenant_id, name, permissions) "
                "VALUES (:i, :t, :n, :p)"
            ),
            {"i": perfil, "t": tenant, "n": name, "p": permisos or ["AGENDA_VER"]},
        )
    return perfil


async def _usuario(
    admin: AsyncEngine,
    tenant: uuid.UUID,
    *,
    role: str,
    email: str | None = None,
    username: str | None = None,
    perfil: uuid.UUID | None = None,
    voided: bool = False,
) -> uuid.UUID:
    user = uuid.uuid4()
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, username, password_hash, role, "
                "permission_profile_id, voided_at, void_reason) "
                "VALUES (:i, :t, :e, :u, :h, :r, :p, "
                + ("now(), 'DESACTIVADO'" if voided else "NULL, NULL")
                + ")"
            ),
            {
                "i": user,
                "t": tenant,
                "e": email,
                "u": username,
                "h": HASH,
                "r": role,
                "p": perfil,
            },
        )
    return user


# ── CHECKs de `users` ─────────────────────────────────────────────────────────


async def test_owner_sin_email_falla(pg_admin_engine, pg_tenant_a):
    with pytest.raises(IntegrityError, match="ck_users_owner_con_email"):
        await _usuario(pg_admin_engine, pg_tenant_a, role="OWNER", username="duenio.sin.mail")


async def test_staff_sin_perfil_falla(pg_admin_engine, pg_tenant_a):
    with pytest.raises(IntegrityError, match="ck_users_perfil_segun_rol"):
        await _usuario(pg_admin_engine, pg_tenant_a, role="STAFF", username="sin.perfil")


async def test_owner_con_perfil_falla(pg_admin_engine, pg_tenant_a):
    perfil = await _perfil(pg_admin_engine, pg_tenant_a, "Encargado")
    with pytest.raises(IntegrityError, match="ck_users_perfil_segun_rol"):
        await _usuario(
            pg_admin_engine,
            pg_tenant_a,
            role="OWNER",
            email="duenio-con-perfil@ejemplo.invalid",
            perfil=perfil,
        )


@pytest.mark.parametrize(
    "username",
    ["Juan", "juan@ejemplo.invalid", "ab", "x" * 41, "con espacio", "ñandu", ""],
    ids=["mayuscula", "arroba", "corto", "largo", "espacio", "no-ascii", "vacio"],
)
async def test_username_mal_formado_falla(pg_admin_engine, pg_tenant_a, username):
    perfil = await _perfil(pg_admin_engine, pg_tenant_a, "Lavador")
    with pytest.raises(IntegrityError, match="ck_users_username_formato"):
        await _usuario(pg_admin_engine, pg_tenant_a, role="STAFF", username=username, perfil=perfil)


async def test_sin_email_ni_username_falla(pg_admin_engine, pg_tenant_a):
    perfil = await _perfil(pg_admin_engine, pg_tenant_a, "Lavador")
    with pytest.raises(IntegrityError, match="ck_users_con_identificador"):
        await _usuario(pg_admin_engine, pg_tenant_a, role="STAFF", perfil=perfil)


async def test_staff_con_solo_username_y_owner_con_email_valen(pg_admin_engine, pg_tenant_a):
    perfil = await _perfil(pg_admin_engine, pg_tenant_a, "Lavador")
    await _usuario(pg_admin_engine, pg_tenant_a, role="STAFF", username="lav.ok_1-2", perfil=perfil)
    await _usuario(pg_admin_engine, pg_tenant_a, role="OWNER", email="duenio@ejemplo.invalid")
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT must_change_password FROM users WHERE username = 'lav.ok_1-2'")
            )
            is False
        )


async def test_username_es_unico_global_entre_tenants(pg_admin_engine, pg_tenant_a, pg_tenant_b):
    perfil_a = await _perfil(pg_admin_engine, pg_tenant_a, "Lavador")
    perfil_b = await _perfil(pg_admin_engine, pg_tenant_b, "Lavador")
    await _usuario(pg_admin_engine, pg_tenant_a, role="STAFF", username="repetido", perfil=perfil_a)
    with pytest.raises(IntegrityError, match="uq_users_username"):
        await _usuario(
            pg_admin_engine, pg_tenant_b, role="STAFF", username="repetido", perfil=perfil_b
        )


async def test_varios_empleados_sin_email_no_chocan(pg_admin_engine, pg_tenant_a, pg_tenant_b):
    """`email` pasó a nullable y sigue único: los NULL no chocan entre sí."""
    for tenant, nombre in ((pg_tenant_a, "uno.a"), (pg_tenant_a, "dos.a"), (pg_tenant_b, "uno.b")):
        perfil = await _perfil(pg_admin_engine, tenant, f"perfil {nombre}")
        await _usuario(pg_admin_engine, tenant, role="STAFF", username=nombre, perfil=perfil)


# ── FK compuesta users → permission_profiles ──────────────────────────────────


async def test_staff_de_b_no_puede_apuntar_a_un_perfil_de_a(
    pg_admin_engine, pg_tenant_a, pg_tenant_b
):
    perfil_a = await _perfil(pg_admin_engine, pg_tenant_a, "Encargado", list(Permission))
    with pytest.raises(IntegrityError, match="fk_users_tenant_id_permission_profile_id"):
        await _usuario(
            pg_admin_engine, pg_tenant_b, role="STAFF", username="colado.b", perfil=perfil_a
        )


async def test_el_runtime_no_mueve_a_su_empleado_al_perfil_de_otro_tenant(
    pg_admin_engine, pg_session_factory, pg_tenant_a, pg_tenant_b
):
    """Con contexto B y el rol de runtime: el UPDATE pasa RLS (la fila es de B) pero la FK
    compuesta `(tenant_id, permission_profile_id)` lo corta."""
    perfil_a = await _perfil(pg_admin_engine, pg_tenant_a, "Encargado", list(Permission))
    perfil_b = await _perfil(pg_admin_engine, pg_tenant_b, "Lavador")
    staff_b = await _usuario(
        pg_admin_engine, pg_tenant_b, role="STAFF", username="lavador.b", perfil=perfil_b
    )
    async with pg_session_factory() as session:
        with pytest.raises(DBAPIError, match="fk_users_tenant_id_permission_profile_id"):
            async with session.begin():
                await set_tenant_context(session, pg_tenant_b)
                await session.execute(
                    text("UPDATE users SET permission_profile_id = :p WHERE id = :u"),
                    {"p": perfil_a, "u": staff_b},
                )
    async with pg_admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT permission_profile_id FROM users WHERE id = :u"), {"u": staff_b}
            )
            == perfil_b
        )


async def test_un_perfil_en_uso_no_se_borra(pg_admin_engine, pg_tenant_a):
    perfil = await _perfil(pg_admin_engine, pg_tenant_a, "Lavador")
    await _usuario(pg_admin_engine, pg_tenant_a, role="STAFF", username="usa.perfil", perfil=perfil)
    with pytest.raises(IntegrityError, match="fk_users_tenant_id_permission_profile_id"):
        async with pg_admin_engine.begin() as conn:
            await conn.execute(text("DELETE FROM permission_profiles WHERE id = :p"), {"p": perfil})


# ── CHECKs de `permission_profiles` ───────────────────────────────────────────


@pytest.mark.parametrize(
    "permisos",
    [["SUPERPODER"], ["AGENDA_VER", "DAR_PERMISOS"], ["agenda_ver"], ["OWNER"]],
    ids=["inventado", "uno-bueno-uno-malo", "minuscula", "rol-como-permiso"],
)
async def test_permiso_desconocido_falla(pg_admin_engine, pg_tenant_a, permisos):
    with pytest.raises(IntegrityError, match=r"ck_permission_profiles_\w*permisos"):
        await _perfil(pg_admin_engine, pg_tenant_a, "con basura", permisos)


async def test_todos_los_permisos_del_enum_y_ninguno_valen(pg_admin_engine, pg_tenant_a):
    await _perfil(pg_admin_engine, pg_tenant_a, "todo", [p.value for p in Permission])
    await _perfil(pg_admin_engine, pg_tenant_a, "nada", [])


@pytest.mark.parametrize("nombre", ["", "x" * 41], ids=["vacio", "largo"])
async def test_nombre_de_perfil_fuera_de_rango_falla(pg_admin_engine, pg_tenant_a, nombre):
    with pytest.raises(IntegrityError, match=r"ck_permission_profiles_\w*nombre_largo"):
        await _perfil(pg_admin_engine, pg_tenant_a, nombre)


async def test_nombre_unico_entre_vivos_y_por_tenant(pg_admin_engine, pg_tenant_a, pg_tenant_b):
    viejo = await _perfil(pg_admin_engine, pg_tenant_a, "Cajero")
    await _perfil(pg_admin_engine, pg_tenant_b, "Cajero")  # otro tenant: no choca
    with pytest.raises(IntegrityError, match="ux_permission_profiles_tenant_id_name"):
        await _perfil(pg_admin_engine, pg_tenant_a, "Cajero")
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE permission_profiles SET voided_at = now(), "
                "void_reason = 'PEDIDO_DEL_USUARIO' WHERE id = :i"
            ),
            {"i": viejo},
        )
    await _perfil(pg_admin_engine, pg_tenant_a, "Cajero")  # anulado: el nombre se libera


async def test_permissions_tiene_default_vacio_en_la_base(pg_admin_engine, pg_tenant_a):
    perfil = uuid.uuid4()
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO permission_profiles (id, tenant_id, name) VALUES (:i, :t, 'x')"),
            {"i": perfil, "t": pg_tenant_a},
        )
        assert (
            await conn.scalar(
                text("SELECT permissions FROM permission_profiles WHERE id = :i"), {"i": perfil}
            )
            == []
        )


async def test_los_check_de_permission_profiles_tienen_el_nombre_del_modelo(pg_admin_engine):
    async with pg_admin_engine.connect() as conn:
        nombres = set(
            (
                await conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'permission_profiles'::regclass AND contype = 'c'"
                    )
                )
            ).scalars()
        )
    assert nombres == {
        "ck_permission_profiles_void_coherente",
        "ck_permission_profiles_nombre_largo",
        "ck_permission_profiles_permisos_conocidos",
    }


# ── auth_lookup_user(identifier) con el rol de runtime ────────────────────────


async def _buscar(pg_session_factory, identificador: str) -> list[tuple[uuid.UUID, uuid.UUID]]:
    async with pg_session_factory() as session, session.begin():
        filas = (
            await session.execute(
                text("SELECT id, tenant_id FROM auth_lookup_user(:i)"), {"i": identificador}
            )
        ).all()
        # la función no deja `users` abierta al rol de runtime
        assert await session.scalar(text("SELECT count(*) FROM users")) == 0
    return [(f.id, f.tenant_id) for f in filas]


async def test_auth_lookup_encuentra_por_username_y_por_email(
    pg_admin_engine, pg_session_factory, pg_tenant_a, pg_tenant_b
):
    perfil = await _perfil(pg_admin_engine, pg_tenant_a, "Lavador")
    emp = await _usuario(
        pg_admin_engine,
        pg_tenant_a,
        role="STAFF",
        username="lavador.lookup",
        email="lavador.lookup@ejemplo.invalid",
        perfil=perfil,
    )
    duenio_b = await _usuario(
        pg_admin_engine, pg_tenant_b, role="OWNER", email="duenio.lookup@ejemplo.invalid"
    )
    for identificador in (
        "lavador.lookup",
        "LAVADOR.LOOKUP",
        "lavador.lookup@ejemplo.invalid",
        "Lavador.Lookup@Ejemplo.INVALID",
    ):
        assert await _buscar(pg_session_factory, identificador) == [(emp, pg_tenant_a)]
    assert await _buscar(pg_session_factory, "DUENIO.lookup@ejemplo.invalid") == [
        (duenio_b, pg_tenant_b)
    ]
    for nada in ("lavador", "%", "lavador.%", "", "lavador.lookup@"):
        assert await _buscar(pg_session_factory, nada) == [], nada


async def test_auth_lookup_no_devuelve_anulados(pg_admin_engine, pg_session_factory, pg_tenant_a):
    perfil = await _perfil(pg_admin_engine, pg_tenant_a, "Lavador")
    await _usuario(
        pg_admin_engine,
        pg_tenant_a,
        role="STAFF",
        username="ya.no.trabaja",
        email="ya.no.trabaja@ejemplo.invalid",
        perfil=perfil,
        voided=True,
    )
    assert await _buscar(pg_session_factory, "ya.no.trabaja") == []
    assert await _buscar(pg_session_factory, "ya.no.trabaja@ejemplo.invalid") == []


async def test_auth_lookup_tiene_una_sola_firma(pg_admin_engine):
    """La de 0001 (`p_email`) se reemplazó: no quedan dos sobrecargas."""
    async with pg_admin_engine.connect() as conn:
        firmas = (
            (
                await conn.execute(
                    text(
                        "SELECT pg_get_function_identity_arguments(oid) FROM pg_proc "
                        "WHERE proname = 'auth_lookup_user'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert firmas == ["p_identifier text"]


# ── Login de punta a punta con el rol de runtime ──────────────────────────────


async def test_login_http_por_username_con_rls(pg_client, pg_admin_engine, pg_tenant_a):
    from app.utils.security import hash_password  # noqa: PLC0415

    perfil = await _perfil(
        pg_admin_engine, pg_tenant_a, "Cajero", [p.value for p in DEFAULT_PROFILES["Cajero"]]
    )
    emp = uuid.uuid4()
    async with pg_admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, username, password_hash, role, "
                "permission_profile_id, must_change_password) "
                "VALUES (:i, :t, 'cajero.pg', :h, 'STAFF', :p, true)"
            ),
            {"i": emp, "t": pg_tenant_a, "h": hash_password("clave-inicial-123"), "p": perfil},
        )
    r = await pg_client.post(
        "/v1/auth/login", json={"identifier": " Cajero.PG ", "password": "clave-inicial-123"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["id"] == str(emp) and body["must_change_password"] is True
    assert set(body["permissions"]) == {p.value for p in DEFAULT_PROFILES["Cajero"]}
    # la compuerta también corta contra Postgres
    r = await pg_client.get("/v1/permission-profiles")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    r = await pg_client.post(
        "/v1/auth/change-password",
        json={"current_password": "clave-inicial-123", "new_password": "otra-clave-456"},
    )
    assert r.status_code == 200, r.text
    # ya puede operar, pero perfiles es OWNER-only
    r = await pg_client.get("/v1/permission-profiles")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "FORBIDDEN"


# ── Relleno de la migración 0004 (A6) ─────────────────────────────────────────


def _alembic(comando: str, revision: str) -> None:
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "app/persistence/migrations"))
    previo = os.environ.get("DATABASE_URL_SYNC")
    os.environ["DATABASE_URL_SYNC"] = _with_driver(owner_url(), "psycopg2")
    try:
        getattr(command, comando)(cfg, revision)
    finally:
        if previo is None:
            os.environ.pop("DATABASE_URL_SYNC", None)
        else:  # pragma: no cover  # solo si el entorno ya traía la variable
            os.environ["DATABASE_URL_SYNC"] = previo


@contextmanager
def _en_0003() -> Iterator[None]:
    """Baja a 0003 y, pase lo que pase, deja la base en head (los demás tests la usan)."""
    _alembic("downgrade", "0003_dominio")
    try:
        yield
    finally:
        migrate_head(owner_url())


async def _sembrar_esquema_0003(admin: AsyncEngine) -> dict[str, dict[str, uuid.UUID]]:
    """Dos tenants con OWNER, STAFF vivo y STAFF anulado, con las columnas de 0003."""
    ids: dict[str, dict[str, uuid.UUID]] = {}
    async with admin.begin() as conn:
        for lado in ("a", "b"):
            fila = {
                "tenant": uuid.uuid4(),
                "owner": uuid.uuid4(),
                "staff": uuid.uuid4(),
                "staff_anulado": uuid.uuid4(),
            }
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:i, :n)"),
                {"i": fila["tenant"], "n": f"Lavadero previo {lado}"},
            )
            for clave, role, extra in (
                ("owner", "OWNER", "NULL, NULL"),
                ("staff", "STAFF", "NULL, NULL"),
                ("staff_anulado", "STAFF", "now(), 'DESACTIVADO'"),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO users (id, tenant_id, email, password_hash, role, "
                        f"voided_at, void_reason) VALUES (:i, :t, :e, :h, :r, {extra})"
                    ),
                    {
                        "i": fila[clave],
                        "t": fila["tenant"],
                        "e": f"{clave}-{lado}@ejemplo.invalid",
                        "h": HASH,
                        "r": role,
                    },
                )
            ids[lado] = fila
    return ids


async def _perfiles_por_tenant(admin: AsyncEngine) -> dict[uuid.UUID, dict[str, set[str]]]:
    async with admin.connect() as conn:
        filas = (
            await conn.execute(text("SELECT tenant_id, name, permissions FROM permission_profiles"))
        ).all()
    salida: dict[uuid.UUID, dict[str, set[str]]] = {}
    for f in filas:
        assert f.name not in salida.setdefault(f.tenant_id, {}), f"perfil duplicado: {f.name}"
        salida[f.tenant_id][f.name] = set(f.permissions)
    return salida


async def test_la_migracion_0004_completa_los_tenants_existentes(
    pg_admin_engine, pg_engine, pg_clean
):
    esperado = {nombre: {p.value for p in perms} for nombre, perms in DEFAULT_PROFILES.items()}
    with _en_0003():
        async with pg_admin_engine.connect() as conn:
            assert await conn.scalar(text("SELECT to_regclass('public.dummy_resources')"))
            assert not await conn.scalar(text("SELECT to_regclass('public.permission_profiles')"))
        ids = await _sembrar_esquema_0003(pg_admin_engine)
        migrate_head(owner_url())

        perfiles = await _perfiles_por_tenant(pg_admin_engine)
        assert set(perfiles) == {ids["a"]["tenant"], ids["b"]["tenant"]}
        for tenant_perfiles in perfiles.values():
            assert tenant_perfiles == esperado

        async with pg_admin_engine.connect() as conn:
            usuarios = {
                f.id: f
                for f in (
                    await conn.execute(
                        text(
                            "SELECT u.id, u.tenant_id, u.role, u.username, u.email, "
                            "u.must_change_password, p.name AS perfil, p.tenant_id AS perfil_de "
                            "FROM users u LEFT JOIN permission_profiles p "
                            "ON p.id = u.permission_profile_id"
                        )
                    )
                ).all()
            }
            assert await conn.scalar(text("SELECT to_regclass('public.dummy_resources')")) is None
            forzadas = (
                (
                    await conn.execute(
                        text(
                            "SELECT relname FROM pg_class WHERE relname IN "
                            "('tenants', 'users', 'permission_profiles') "
                            "AND relrowsecurity AND relforcerowsecurity"
                        )
                    )
                )
                .scalars()
                .all()
            )
        # el relleno corre con NO FORCE y lo vuelve a forzar en la misma transacción
        assert set(forzadas) == {"tenants", "users", "permission_profiles"}

        for lado in ("a", "b"):
            fila = ids[lado]
            owner = usuarios[fila["owner"]]
            assert owner.perfil is None and owner.role == "OWNER"
            for clave in ("staff", "staff_anulado"):
                staff = usuarios[fila[clave]]
                # Encargado del SU tenant: no pierden acceso (y el anulado también, para el CHECK)
                assert staff.perfil == LEGACY_STAFF_PROFILE, clave
                assert staff.perfil_de == fila["tenant"], clave
            for u in (owner, usuarios[fila["staff"]]):
                assert u.username is None and u.must_change_password is False
                assert (
                    u.email == f"{'owner' if u.role == 'OWNER' else 'staff'}-{lado}@ejemplo.invalid"
                )

        # Re-aplicar con el stamp atrasado (A6): no duplica perfiles ni reasigna.
        async with pg_admin_engine.begin() as conn:
            await conn.execute(text("UPDATE alembic_version SET version_num = '0003_dominio'"))
        migrate_head(owner_url())
        assert await _perfiles_por_tenant(pg_admin_engine) == perfiles
    # Las tablas se recrearon: que el pool del runtime no arrastre planes viejos.
    await pg_engine.dispose()


async def test_despues_de_migrar_el_staff_previo_entra_con_su_email(
    pg_admin_engine, pg_engine, pg_client, pg_clean
):
    """El empleado que existía antes de F4 sigue entrando con su email y con permisos de
    Encargado (no pierde acceso)."""
    from app.utils.security import hash_password  # noqa: PLC0415

    with _en_0003():
        tenant, staff = uuid.uuid4(), uuid.uuid4()
        async with pg_admin_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:i, 'Previo')"), {"i": tenant}
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, password_hash, role) "
                    "VALUES (:i, :t, 'empleado-previo@ejemplo.invalid', :h, 'STAFF')"
                ),
                {"i": staff, "t": tenant, "h": hash_password("correct-horse-battery")},
            )
        migrate_head(owner_url())
    await pg_engine.dispose()

    r = await pg_client.post(
        "/v1/auth/login",
        json={"identifier": "Empleado-Previo@ejemplo.invalid", "password": "correct-horse-battery"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == str(staff)
    assert r.json()["permissions"] == [p.value for p in Permission]
    assert r.json()["must_change_password"] is False
