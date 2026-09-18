# ADR-0003 · Un solo flavor de soft-delete: `voided_at` + `void_reason` + CHECK

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor tiene **cuatro** maneras distintas de decir "esta fila ya no cuenta", y conviven sin un criterio
que diga cuál usar:

| Flavor | Modelos | Ejemplo |
|---|---|---|
| `deleted_at` (timestamp nullable) | 1 | `backend/app/persistence/models/file.py:83` |
| `is_active` (boolean) | 3 | `…/models/user.py:36`, `…/models/product.py:88`, `…/models/score.py:91` |
| `voided_at` + `void_reason` | 4 | `…/models/transaction.py:78-79` (SaleEntry) y `:164-165` (ExpenseEntry), `…/models/inventory.py:116`, `…/models/product_supplier_link.py:68` |
| `status` como texto libre | 2 | `…/models/tenant.py:29` (`status: Mapped[str] = mapped_column(Text, ... default="ACTIVE")`), usado como compuerta en `backend/app/api/v1/deps.py:92` (`if tenant.status not in ("ACTIVE", "TRIAL")`) |

Dos de esos cuatro ni siquiera son soft-delete: `…/models/score.py:91` usa `is_active` para marcar qué
conjunto de reglas está **vigente** (no hay borrado en juego), y `tenant.py:29` usa `status` como estado
de suscripción. Y de los tres modelos con `voided_at`, solo dos tienen `void_reason`
(`transaction.py:78-79` y `:164-165`); `inventory.py:116` y `product_supplier_link.py:68` tienen el
timestamp pelado, sin motivo y sin CHECK que los ate.

Por qué no se hereda: cada query que quiera saber "¿qué está vivo?" tiene que acordarse de cuál de las
cuatro convenciones aplica a esa tabla. En un dominio con plata — cobros, señas, retiros — una fila que se
cuenta cuando no debería es plata mal reportada, y el bug no tiene síntoma hasta el cierre de caja. Un
reporte que suma `WHERE deleted_at IS NULL` sobre una tabla que usa `voided_at` **no falla**: devuelve un
número, y el número está mal.

## Objeción

**La decisión del roadmap, tomada al pie de la letra, pierde información.** Los cuatro flavors de Véktor
no son cuatro accidentes: al menos dos codifican significados genuinamente distintos.

- `voided_at` sobre una `SaleEntry` quiere decir *"este asiento nunca debió existir"* — se anula, deja de
  sumar, y queda para auditoría.
- `is_active=False` sobre un `Product` o un `User` quiere decir *"esta entidad existe y es válida, pero ya
  no se usa"* — el producto sigue apareciendo en las ventas históricas, el usuario sigue firmando los
  registros que creó.

Colapsar los dos en un `voided_at` sin más pierde la distinción, y la vamos a necesitar en la Fase 6: dar
de baja a un empleado no es anular sus lavados. Así que la decisión no es "un solo nombre de columna" sino
**un solo mecanismo, con la semántica adentro del motivo**: `void_reason` es un Enum cerrado, y es el que
distingue `DESACTIVADO` de `ERROR_DE_CARGA`. Se gana la uniformidad sin perder el significado.

La segunda corrección: `score.py:91` no es soft-delete y no debe colapsarse. "Qué versión está vigente" es
un problema de **vigencia temporal** (`effective_from` / `effective_to`), no de borrado, y merece su propio
patrón, distinto y con otro nombre, para que nadie los confunda.

## Decisión

1. **Un único mixin.** `VoidableMixin` en `backend/app/persistence/db/mixins.py`:
   - `voided_at: TIMESTAMPTZ NULL`
   - `void_reason: Enum(VoidReason) NULL`
   - `CHECK ((voided_at IS NULL) = (void_reason IS NULL))`, nombrado
     `ck_<tabla>_void_coherente` vía la `naming_convention` de la `MetaData`.
2. **`VoidReason` es un `StrEnum` cerrado**, en `backend/app/domain/void.py`, nativo en Postgres
   (mismo tratamiento que los roles en ADR-0005). Arranca con tres valores y crece solo por migración:
   `ERROR_DE_CARGA`, `PEDIDO_DEL_USUARIO`, `DESACTIVADO`.
3. **Prohibidas** las columnas `deleted_at`, `is_deleted`, `is_active`, `archived_at`, y el uso de
   `status` como compuerta de existencia. El test del punto 1 las hace fallar por nombre.
4. **`BaseRepository` filtra `voided_at IS NULL` por defecto.** Ver lo anulado exige pedirlo:
   `include_voided=True`. El default es el caso seguro, no el completo.
5. **Anular es una operación, no un `UPDATE`.** `repo.void(id, tenant_id, reason)` setea las dos columnas
   juntas. No existe un camino que setee una sola — y si alguien lo escribe, el CHECK lo rechaza en la
   base.
6. **La vigencia temporal es otro patrón.** Cuando haga falta (no en la Fase 2), se usa
   `effective_from`/`effective_to` y se documenta en su propia ADR. Nunca `is_active`.

## Consecuencias

- **Gana:** "¿qué está vivo?" tiene una sola respuesta en todo el esquema, y el default del repositorio ya
  la aplica. Un reporte nuevo no puede sumar filas anuladas por olvido.
- **Gana:** el CHECK convierte en imposible el estado que Véktor sí puede representar —
  `voided_at` seteado y `void_reason` en `NULL` — que es exactamente "alguien anuló esto y no sabemos por
  qué".
- **Gana:** `void_reason` como Enum nativo hace que el catálogo de motivos sea inspeccionable desde la
  base, no un conjunto de literales repartidos por el código.
- **Cuesta:** dos columnas en vez de una en cada tabla anulable, y un tipo enum más que migrar cuando se
  agregue un motivo. Es el precio de que el motivo sea obligatorio.
- **Cuesta:** todo `SELECT` escrito a mano fuera del repositorio tiene que acordarse del filtro. Se acota
  exigiendo que las lecturas pasen por el repositorio.
- **Recordar:** anular **no** es borrar. Ninguna fila anulada se elimina físicamente en la Fase 2; el
  borrado real (Ley 25.326, derecho de supresión) es un procedimiento aparte y va a necesitar su ADR.

## Cómo se verifica

`backend/app/tests/meta/test_soft_delete.py` — estructura, y `…/tests/persistence/test_voidable.py` —
comportamiento.

```python
PROHIBIDAS = {"deleted_at", "is_deleted", "is_active", "archived_at"}

def test_no_existe_otro_flavor_de_soft_delete():
    encontradas = {f"{t.name}.{c}" for t in Base.metadata.tables.values()
                   for c in t.c.keys() if c in PROHIBIDAS}
    assert not encontradas, f"otro flavor de soft-delete: {encontradas}"

def test_voided_at_y_void_reason_van_siempre_juntos():
    for t in Base.metadata.tables.values():
        assert ("voided_at" in t.c) == ("void_reason" in t.c), f"{t.name}: mitad del mixin"

def test_toda_tabla_anulable_tiene_su_check():
    for t in Base.metadata.tables.values():
        if "voided_at" not in t.c:
            continue
        nombres = {c.name for c in t.constraints if isinstance(c, CheckConstraint)}
        assert f"ck_{t.name}_void_coherente" in nombres, f"{t.name}: sin CHECK de coherencia"
```

```python
async def test_la_base_rechaza_el_medio_anulado(db_session, dummy):
    # el estado que Véktor sí puede representar (inventory.py:116 no tiene void_reason).
    with pytest.raises(IntegrityError):
        await db_session.execute(
            update(DummyResource).where(...).values(voided_at=utcnow()))  # sin void_reason
        await db_session.flush()

async def test_lo_anulado_no_aparece_por_defecto(repo, tenant_a, dummy):
    await repo.void(dummy.id, tenant_a.id, VoidReason.PEDIDO_DEL_USUARIO)
    assert dummy.id not in {d.id for d in (await repo.list_by_tenant(tenant_a.id)).items}
    assert dummy.id in {d.id for d in (await repo.list_by_tenant(tenant_a.id, include_voided=True)).items}
```

```bash
# El catálogo de motivos que ve la base es exactamente el del Enum de Python.
psql "$DATABASE_URL" -tAc "SELECT unnest(enum_range(NULL::void_reason)) ORDER BY 1"
```
