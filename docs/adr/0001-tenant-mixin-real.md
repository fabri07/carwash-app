# ADR-0001 · `TenantMixin` real, no `tenant_id` copiado en cada modelo

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

En Véktor no existe un `TenantMixin`. La búsqueda `rg -n "TenantMixin" backend/app/` devuelve cero
resultados. Los únicos mixins compartidos viven en `backend/app/persistence/db/base.py:28`
(`TimestampMixin`) y `backend/app/persistence/db/base.py:50` (`UUIDPrimaryKeyMixin`), y ninguno declara
`tenant_id`.

El resultado es **51 declaraciones manuales de `tenant_id` repartidas en 37 archivos** de
`backend/app/persistence/models/`. Y como cada una se escribió a mano, divergieron — la misma columna,
con el mismo propósito (aislamiento multi-tenant), tiene hoy cuatro formas distintas:

| Forma | Ejemplo |
|---|---|
| FK + `ondelete="CASCADE"` + `index=True` + `NOT NULL` (mayoritaria, ~45) | `backend/app/persistence/models/user.py:23` |
| FK + `CASCADE` pero **`nullable=True`** | `backend/app/persistence/models/audit.py:29` |
| **Sin FK**, sin índice | `backend/app/persistence/models/maintenance_lock.py:26` |
| **Sin FK**, con índice | `backend/app/persistence/models/user_auth_identity.py:33` |

El caso más ilustrativo está dentro de un solo archivo: `backend/app/persistence/models/repair.py:19`
declara `tenant_id: Mapped[uuid.UUID | None] ... nullable=True`, y sesenta líneas más abajo,
`backend/app/persistence/models/repair.py:94` declara `tenant_id: Mapped[uuid.UUID] ... nullable=False`.
Dos modelos hermanos, dos contratos distintos para la columna de la que depende el aislamiento.

Por qué es un problema heredarlo: el `tenant_id` de este proyecto no es un campo más, es **la frontera de
seguridad** (ver ADR-0002). Una columna que se escribe a mano 51 veces es una columna que se va a olvidar
una vez, y "una tabla sin `tenant_id`" no se ve en un diff — se ve cuando un cliente ve el turno de otro
lavadero. Además, sin un lugar único que declare qué tablas son tenant-scoped, no hay forma de escribir
un test que diga *"esta tabla nueva no está aislada"*.

## Decisión

1. `TenantMixin` vive en `backend/app/persistence/db/mixins.py` y es la **única** forma de tener
   `tenant_id`:

   ```python
   class TenantMixin:
       @declared_attr
       def tenant_id(cls) -> Mapped[uuid.UUID]:
           return mapped_column(
               UUID(as_uuid=True),
               ForeignKey("tenants.id", ondelete="RESTRICT"),
               nullable=False,
               index=True,
           )
   ```

2. `ondelete="RESTRICT"`, no `CASCADE`. Borrar un tenant no puede ser una operación que vacíe media base
   sin que nadie la haya pedido explícitamente; la baja de un tenant es un `voided_at` (ADR-0003) más un
   procedimiento aparte.
3. Redeclarar `tenant_id` en un modelo está **prohibido**, y el test del punto 1 de "Cómo se verifica"
   lo hace fallar.
4. Toda tabla es tenant-scoped **salvo** que esté en una lista blanca explícita y corta, declarada en el
   propio test: `tenants`, `users` (que apunta a un tenant pero es la raíz de la identidad),
   `alembic_version`. Agregar una tabla a esa lista exige editar el test — o sea, es un acto deliberado
   con revisor, no un olvido.
5. `TenantMixin` es también el ancla de la migración: la política RLS de ADR-0002 se genera para toda
   tabla que use el mixin, y no para una lista mantenida a mano.

## Consecuencias

- **Gana:** agregar una tabla aislada cuesta una palabra (`class Booking(Base, TenantMixin, ...)`) y
  olvidarse cuesta un CI en rojo. La divergencia de `repair.py:19` vs `:94` es estructuralmente imposible.
- **Gana:** el conjunto "tablas con `tenant_id`" pasa a ser computable en tiempo de test, que es lo que
  hace verificables a ADR-0002 (RLS) y al test cruzado de la Fase 2.
- **Cuesta:** `declared_attr` en un mixin es una indirección más para quien lee el modelo, y los `FK` a
  `tenants.id` obligan a que `tenants` se cree primero en la cadena de migraciones.
- **Cuesta:** `RESTRICT` implica que el teardown de tests y los seeds tienen que borrar en orden. Lo paga
  el fixture, una vez.
- **Recordar:** el mixin garantiza que la columna *existe* y que es uniforme. **No** garantiza que las
  consultas filtren por ella — eso es ADR-0002. Son dos redes distintas y hacen falta las dos.

## Cómo se verifica

`backend/app/tests/meta/test_modelo_tenant.py`, que recorre `Base.metadata` y no depende de que alguien
se acuerde de mirar:

```python
TABLAS_SIN_TENANT = {"tenants", "users", "alembic_version"}  # editar esto es un acto deliberado

def test_toda_tabla_es_tenant_scoped():
    faltan = {t.name for t in Base.metadata.tables.values()
              if "tenant_id" not in t.c} - TABLAS_SIN_TENANT
    assert not faltan, f"tablas sin aislamiento: {faltan}"

def test_ningun_modelo_redeclara_tenant_id():
    # el mixin es el único dueño: si la clase declara tenant_id en su propio cuerpo, falla.
    culpables = [m.class_.__name__ for m in Base.registry.mappers
                 if "tenant_id" in vars(m.class_)]
    assert not culpables, f"redeclaran tenant_id en vez de usar TenantMixin: {culpables}"

def test_la_columna_tenant_id_es_identica_en_todas_las_tablas():
    for t in Base.metadata.tables.values():
        if (col := t.c.get("tenant_id")) is None:
            continue
        assert col.nullable is False,            f"{t.name}.tenant_id es nullable"
        assert col.index is True,                f"{t.name}.tenant_id sin índice"
        fks = list(col.foreign_keys)
        assert len(fks) == 1,                    f"{t.name}.tenant_id sin FK"
        assert fks[0].column.table.name == "tenants"
        assert fks[0].ondelete == "RESTRICT",    f"{t.name}.tenant_id con ondelete={fks[0].ondelete}"
```

El tercer test es el que hubiera atrapado a Véktor: corrido contra sus modelos, falla en `audit.py:29`
(nullable), en `maintenance_lock.py:26` (sin FK ni índice) y en `repair.py:19` y `:94`.
