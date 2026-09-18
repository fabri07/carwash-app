"""SQL de Row Level Security y de la búsqueda de identidad — ADR-0002. Lo usan las migraciones.

Vive acá y no inline en cada migración para que la forma de la política sea UNA:
`ENABLE` + `FORCE` + `USING` **y** `WITH CHECK`, las dos con la misma expresión y
sin excepciones. Referencia de lo que no se hace: Véktor
`…/versions/20260401_0001_security_fase0_rls_pending_actions_cipher.py:114,121-122`,
que crea `USING` sin `WITH CHECK` y usa `current_setting(...)::uuid` sin `NULLIF`.

`NULLIF(..., '')`: después de un `SET LOCAL` la conexión conserva el parámetro
definido con valor `''` al terminar la transacción, y `''::uuid` revienta. Con
`NULLIF` el "sin contexto" es `NULL` y la política no deja pasar ninguna fila:
falla cerrado, no con un error.

Login sin excepción en la política (BUG-1/M4)
---------------------------------------------
El login busca un usuario por email antes de conocer su tenant. Una versión
anterior abría para eso una "ventana" (un parámetro de sesión) en el `USING` de
`users`: era un GUC que cualquier sesión de `carwash_app` podía activar, y combinado
con un tenant en contexto permitía mudar un usuario ajeno al tenant propio. Se
eliminó. Ahora la búsqueda es la función `auth_lookup_user(email)`:

- `SECURITY DEFINER`, dueña el rol que corre las migraciones (`carwash_owner`), con
  `search_path` fijo y `REVOKE ALL FROM PUBLIC` + `GRANT EXECUTE` a `carwash_app`;
- devuelve solo `id, tenant_id, password_hash, token_version, role` de UNA fila;
- el rol de runtime sigue sin poder leer `users` sin tenant: la política de
  `users` es exactamente la misma que la de cualquier otra tabla.

Como la tabla tiene `FORCE ROW LEVEL SECURITY`, el dueño también pasa por RLS, así
que la función necesita que el dueño pueda LEER `users`: se le da una política
`FOR SELECT TO <dueño>`. No alcanza al rol de runtime (no es miembro del dueño; el
arranque lo verifica, H4) y no habilita escribir. El dueño ya puede, por ser dueño,
desactivar RLS de sus tablas: esa política no le da nada que no tenga.
"""

TENANT_SETTING_EXPR = "NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid"

#: Rol de runtime: no dueño, sin BYPASSRLS. Lo crea `deploy`.
APP_ROLE = "carwash_app"

AUTH_LOOKUP_FUNCTION = "auth_lookup_user"


def policy_name(table: str) -> str:
    return f"{table}_tenant_isolation"


def owner_lookup_policy_name(table: str) -> str:
    return f"{table}_owner_auth_lookup"


def enable_rls(table: str, *, column: str = "tenant_id") -> list[str]:
    """Aísla `table` por tenant. `column="id"` para la tabla `tenants` (L3)."""
    condition = f"{column} = {TENANT_SETTING_EXPR}"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        # DROP + CREATE: idempotente, y re-aplicar deja la política en su forma canónica.
        f"DROP POLICY IF EXISTS {policy_name(table)} ON {table}",
        f"CREATE POLICY {policy_name(table)} ON {table} "
        f"USING ({condition}) WITH CHECK ({condition})",
    ]


def disable_rls(table: str) -> list[str]:
    return [
        f"DROP POLICY IF EXISTS {owner_lookup_policy_name(table)} ON {table}",
        f"DROP POLICY IF EXISTS {policy_name(table)} ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]


def create_auth_lookup() -> list[str]:
    """Función de búsqueda de identidad para el login. Ver docstring del módulo."""
    fn = AUTH_LOOKUP_FUNCTION
    return [
        f"DROP POLICY IF EXISTS {owner_lookup_policy_name('users')} ON users",
        f"CREATE POLICY {owner_lookup_policy_name('users')} ON users "
        "FOR SELECT TO CURRENT_USER USING (true)",
        f"""
        CREATE OR REPLACE FUNCTION {fn}(p_email text)
        RETURNS TABLE (
            id uuid, tenant_id uuid, password_hash text, token_version integer, role public.role
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT u.id, u.tenant_id, u.password_hash, u.token_version, u.role
            FROM public.users AS u
            WHERE u.email = lower(p_email) AND u.voided_at IS NULL
            LIMIT 1
        $fn$
        """,
        f"REVOKE ALL ON FUNCTION {fn}(text) FROM PUBLIC",
        _if_app_role(f"GRANT EXECUTE ON FUNCTION {fn}(text) TO {APP_ROLE}"),
    ]


def drop_auth_lookup() -> list[str]:
    return [
        f"DROP FUNCTION IF EXISTS {AUTH_LOOKUP_FUNCTION}(text)",
        f"DROP POLICY IF EXISTS {owner_lookup_policy_name('users')} ON users",
    ]


def _if_app_role(statement: str) -> str:
    """Ejecuta `statement` solo si el rol de runtime existe (en CI/local puede no existir)."""
    return (
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"{statement}; "
        "END IF; END $$"
    )


def grant_app_role(tables: list[str]) -> list[str]:
    """Permisos del rol de runtime. Sin DELETE: anular (ADR-0003) es la única baja."""
    joined = ", ".join(tables)
    return [
        _if_app_role(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"),
        _if_app_role(f"GRANT SELECT, INSERT, UPDATE ON {joined} TO {APP_ROLE}"),
    ]
