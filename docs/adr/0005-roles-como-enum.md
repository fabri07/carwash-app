# ADR-0005 · Roles como Enum, único y nativo en Postgres

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor tiene un Enum de roles y no lo usa. Las dos cosas son ciertas al mismo tiempo, y eso es peor que no
tenerlo.

**La columna es texto libre.** `backend/app/persistence/models/user.py:32`:

```python
role_code: Mapped[str] = mapped_column(Text, nullable=False, default="OWNER")
```

`Text`, sin constraint. La base acepta `"PRESIDENTE"`, `"owner "` con espacio al final, o `""`.

**El Enum existe, en otro lado, y no coincide.** `backend/app/domain/user.py:13-17`:

```python
class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"
```

`rg -c "UserRole" backend/app/` devuelve **un solo archivo**: el que lo define. No lo usa `deps.py`, ni los
modelos, ni un endpoint. Y como nunca se usó, derivó sin que nadie se enterara en dos direcciones a la vez:
sus valores están en **minúscula** mientras todo el código usa mayúscula, y **no incluye `SUPERADMIN`**,
que sí existe en producción (`backend/app/api/v1/admin.py:44` →
`dependencies=[Depends(require_role("SUPERADMIN"))]`, repetido en otros 12 endpoints entre `admin.py` y
`access_requests.py`).

**La compuerta compara strings.** `backend/app/api/v1/deps.py:100-108`:

```python
def require_role(*roles: str) -> Callable:
    """Dependency factory that enforces role-based access. Pass uppercase role codes."""
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role_code not in roles:
```

La firma es `*roles: str`. "Pass uppercase role codes" es un docstring, no un tipo: mypy strict acepta
`require_role("Owner")`, `require_role("OWNERS")` y `require_role("")` sin una queja. Los tres literales
reales (`"OWNER"`, `"ADMIN"`, `"SUPERADMIN"`) están repartidos en ~25 archivos, incluyendo comparaciones
sueltas fuera de la compuerta: `backend/app/api/v1/deps.py:255`
(`if not (current_user.role_code == "OWNER" or …)`), `deps.py:274` (`!= "OWNER"`),
`backend/app/jobs/generate_insight.py:50` (`User.role_code == "OWNER"`).

Por qué no se hereda: un typo en un literal de rol no rompe nada, **abre**. `require_role("ADMlN")` (con
ele minúscula) no matchea ningún rol, el `not in` da `True`, y el endpoint queda… cerrado, en ese caso.
Pero `if user.role_code != "OWNER": raise 403` con un typo en la constante deja pasar a todos. El modo de
falla depende del signo de la comparación, y hay comparaciones de los dos signos en el repo. Eso no es un
control de acceso, es una lotería.

## Decisión

1. **Un solo Enum, en un solo lugar**: `Role(StrEnum)` en `backend/app/domain/roles.py`. Valores en
   mayúscula, que es lo que ya usa todo el mundo. Para la Fase 2 alcanzan dos: `OWNER` y `STAFF`. El
   dominio del lavadero va a pedir más (Fase 6); entran por migración, no por literal.
2. **La columna es un enum nativo de Postgres**, no `Text`:
   `sa.Enum(Role, name="role", native_enum=True, validate_strings=True)`. La base rechaza un rol que no
   exista. Es la diferencia entre "el código intenta validar" y "el dato no puede ser inválido".
3. **`require_role` se tipa con el Enum**: `def require_role(*roles: Role) -> Callable`. Con mypy strict,
   `require_role("OWNER")` deja de compilar. El error se ve en el CI, no en producción.
4. **Prohibido el literal de rol en cualquier parte del código**, incluidas las comparaciones sueltas tipo
   `deps.py:255` y `:274`. Se compara `user.role == Role.OWNER`.
5. **El frontend no reescribe la lista.** Los valores llegan por el tipo generado del OpenAPI (ADR-0013);
   agregar un rol en el backend rompe el `tsc` del frontend si hay un `switch` sin cubrir, que es
   exactamente lo que se quiere.
6. **No hay `SUPERADMIN` en la Fase 2.** El rol que cruza tenants es la excepción más peligrosa de un SaaS
   multi-tenant y se diseña con su propia ADR, cuando haga falta, con su propio test de aislamiento.

## Consecuencias

- **Gana:** tres capas independientes atajan un rol inválido — mypy en el CI, el enum nativo en el INSERT
  y el tipo generado en el frontend. Véktor no tiene ninguna de las tres.
- **Gana:** `SELECT unnest(enum_range(NULL::role))` responde "qué roles existen" sin leer código.
- **Cuesta:** agregar un rol es una migración (`ALTER TYPE role ADD VALUE`), no un commit de una línea. Es
  a propósito: agregar un rol cambia quién puede hacer qué, y eso merece una migración revisable.
- **Cuesta:** `ALTER TYPE … ADD VALUE` no corre dentro de un bloque transaccional en versiones viejas de
  Postgres y no se puede revertir en el `downgrade`. **Recordar:** la migración que agrega un rol se
  escribe con `op.execute` y su `downgrade` es un no-op documentado.
- **Recordar:** `StrEnum` serializa como su valor, así que el JWT y el JSON siguen llevando `"OWNER"` y el
  contrato HTTP no cambia. Lo que cambia es que ahora hay alguien que lo valida.

## Cómo se verifica

`backend/app/tests/security/test_roles.py`:

```python
def test_el_enum_de_python_y_el_tipo_de_postgres_dicen_lo_mismo(pg_conn):
    # este es el test que hubiera atrapado la divergencia de Véktor (minúsculas + SUPERADMIN faltante):
    # agregar un valor al StrEnum sin migración deja el CI en rojo.
    en_db = set(pg_conn.execute(text("SELECT unnest(enum_range(NULL::role))")).scalars())
    assert en_db == {r.value for r in Role}

@pytest.mark.postgres
async def test_la_base_rechaza_un_rol_inventado(pg_session, tenant_a):
    with pytest.raises(DBAPIError):
        await pg_session.execute(
            text("INSERT INTO users (id, tenant_id, email, role) VALUES (:i, :t, :e, 'PRESIDENTE')"),
            {...})

def test_no_hay_literales_de_rol_en_el_codigo():
    # comparar roles por string es el agujero de deps.py:255 y :274.
    patron = re.compile(r'(require_role\(\s*["\']|role\s*(==|!=)\s*["\'])')
    culpables = [f"{p}:{n}" for p in Path("app").rglob("*.py")
                 for n, l in enumerate(p.read_text().splitlines(), 1)
                 if patron.search(l)]
    assert not culpables, f"literales de rol: {culpables}"

async def test_staff_no_entra_donde_pide_owner(client, staff_headers):
    assert (await client.delete("/v1/dummy-resources/x", headers=staff_headers)).status_code == 403
```

Y una comprobación de tipos, que corre en el CI y no es un test:

```bash
# require_role("OWNER") con un string literal tiene que ser un error de tipos, no un warning.
cd backend && uv run mypy app   # arg-type: Argument 1 has incompatible type "str"; expected "Role"
```
