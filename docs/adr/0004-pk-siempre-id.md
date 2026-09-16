# ADR-0004 · La clave primaria se llama `id`

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor tiene un `UUIDPrimaryKeyMixin` (`backend/app/persistence/db/base.py:50-56`) que declara una PK
llamada `id`… y **siete nombres distintos de columna PK** conviviendo, porque muchos modelos no lo usan y
declaran la suya:

| Nombre de la PK | Ejemplo |
|---|---|
| `id` (vía mixin) | `backend/app/persistence/models/activity.py:21`, `…/models/job_run.py:51` |
| `user_id` | `backend/app/persistence/models/user.py:21` |
| `token_id` | `…/models/auth_token.py:18` y `:43` (dos clases), `…/models/access_request.py:220-221` |
| `subscription_id` | `…/models/tenant.py:46` |
| `profile_id` | `…/models/business.py:47` |
| `conversation_id` | `…/models/conversation_context.py:20` |
| `tenant_id` | `…/models/memory.py:47-50`, `…/models/business.py:157-160` (`BusinessSnapshot`) |

`rg -n "primary_key=True" backend/app/persistence/models/` devuelve 24 líneas explícitas. No hay PKs
compuestas: `rg -n "PrimaryKeyConstraint"` da cero.

El caso peor es `tenant_id` como PK (`memory.py:47-50`, `business.py:157-160`). Ahí la misma palabra
significa dos cosas según la tabla: en 49 tablas es *"a qué tenant pertenece esta fila"* y en dos es
*"esta fila es el tenant"*. Cualquier helper genérico que reciba un modelo y quiera filtrar por tenant —
`BaseRepository`, el `TenantMixin` de ADR-0001, la política RLS de ADR-0002 — tiene que tratar esas dos
tablas como excepción, y nada en el código lo dice.

Por qué no se hereda: el costo no es estético. `BaseRepository[ModelT].get_by_id` de Véktor
(`backend/app/persistence/repositories/base.py:30-37`) accede a `self.model.id` con
`# type: ignore[attr-defined]` porque `Base` no declara `id` — y ese `type: ignore` está ahí justamente
porque para varios modelos el atributo **no existe**. Un genérico que no puede tipar su propia PK deja de
ser genérico: cada modelo con PK rara necesita su repositorio a mano.

## Objeción

**"PK siempre `id`" está bien como regla y mal como absoluto.** Escrita sin excepción, obliga a poner una
PK sustituta en las tablas puente puras — las que solo relacionan dos entidades, tipo
`booking_services(booking_id, service_id)`. Ahí la clave natural *es* el par, y agregarle un `id` no
agrega nada: agrega la posibilidad de insertar el mismo par dos veces con `id` distinto. Después hay que
tapar el agujero con un `UNIQUE(booking_id, service_id)` que es la PK que no dejamos declarar.

Así que la regla se escribe con su excepción adentro, y la excepción es verificable (una lista blanca en
el test, editarla es un acto deliberado) en vez de quedar librada al criterio de quien escriba la tabla.

## Decisión

1. **Toda tabla-entidad tiene una PK de una sola columna, llamada `id`, de tipo `UUID`**, provista por
   `UUIDPrimaryKeyMixin`. Declarar `primary_key=True` en el cuerpo de un modelo está prohibido.
2. **`<algo>_id` es siempre una foreign key, nunca una primary key.** Es la regla que hace legible el
   esquema: si una columna termina en `_id` y no es `id`, apunta a otra tabla.
3. **Las tablas puente puras pueden tener PK compuesta** (`PrimaryKeyConstraint(a_id, b_id)`), y solo
   ellas. Tienen que estar en la lista `TABLAS_PUENTE` del test, que es corta y se revisa al agregar una.
   Una tabla puente que gana un atributo propio (una fecha, un precio) deja de ser puente: se convierte en
   entidad y pasa a tener `id`.
4. **`tenant_id` nunca es PK.** La tabla `tenants` tiene `id`; todas las demás tienen `tenant_id` como FK
   (ADR-0001). Una relación 1-a-1 con el tenant se modela con `id` propio y `UNIQUE(tenant_id)`.
5. El valor lo genera Python con `uuid.uuid4()` (default del mixin), no la base — así el código conoce el
   `id` antes del flush, que es lo que hace posible el patrón de `_savepoint.py`.

## Consecuencias

- **Gana:** `BaseRepository[ModelT]` pierde sus `# type: ignore[attr-defined]`: `ModelT` puede acotarse a
  un `Protocol` que declara `id: UUID` y `tenant_id: UUID`, y mypy strict lo verifica de verdad.
- **Gana:** rutas, schemas y el cliente de TS quedan uniformes: `/v1/bookings/{id}`, nunca
  `/v1/bookings/{booking_id}`. Un hook genérico de frontend no necesita saber cómo se llama la PK de cada
  recurso.
- **Cuesta:** `uuid4` no es ordenado en el tiempo, así que el índice de PK se fragmenta y no sirve como
  criterio de orden estable para paginar. **Recordar:** paginar por `(created_at, id)`, nunca por `id`
  solo. Migrar a UUIDv7 es una decisión posterior con su propia ADR; se difiere acá a propósito porque
  Python 3.12 no lo trae en la stdlib y no quiero una dependencia más en la Fase 2.
- **Cuesta:** los joins se leen `a.id == b.a_id` en vez de `a.a_id == b.a_id`. Es un cambio de costumbre,
  no un costo real.

## Cómo se verifica

`backend/app/tests/meta/test_claves_primarias.py`:

```python
TABLAS_PUENTE: set[str] = set()   # vacío en Fase 2. Agregar acá es un acto deliberado, con revisor.

def test_toda_tabla_entidad_tiene_pk_id():
    for t in Base.metadata.tables.values():
        if t.name in TABLAS_PUENTE or t.name == "alembic_version":
            continue
        cols = tuple(c.name for c in t.primary_key.columns)
        assert cols == ("id",), f"{t.name}: PK {cols}, se esperaba ('id',)"

def test_ninguna_columna_terminada_en_id_es_pk():
    for t in Base.metadata.tables.values():
        for c in t.primary_key.columns:
            assert c.name == "id" or t.name in TABLAS_PUENTE, \
                f"{t.name}.{c.name}: '<algo>_id' es una FK, no una PK"

def test_tenant_id_nunca_es_pk():
    for t in Base.metadata.tables.values():
        assert "tenant_id" not in {c.name for c in t.primary_key.columns}, \
            f"{t.name}: tenant_id como PK rompe TenantMixin y la política RLS"

def test_las_tablas_puente_declaran_pk_compuesta_y_no_tienen_columnas_propias():
    # una tabla puente que gana atributos deja de serlo: sacala de la lista y dale un id.
    for nombre in TABLAS_PUENTE:
        t = Base.metadata.tables[nombre]
        assert len(t.primary_key.columns) == 2
        propias = set(t.c.keys()) - {c.name for c in t.primary_key.columns} - {"tenant_id", "created_at", "updated_at"}
        assert not propias, f"{nombre}: tiene atributos propios ({propias}), ya no es puente"

def test_ningun_modelo_declara_su_propia_pk():
    culpables = [m.class_.__name__ for m in Base.registry.mappers
                 if any(getattr(v, "primary_key", False) for v in vars(m.class_).values())]
    assert not culpables, f"declaran primary_key= a mano en vez de usar el mixin: {culpables}"
```

Corridos contra los modelos de Véktor, el primero falla en siete tablas y el tercero en `memory.py` y
`business.py`.
