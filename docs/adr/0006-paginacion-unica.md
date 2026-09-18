# ADR-0006 · Una sola convención de paginación: `PaginatedResponse` siempre

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor tiene tres abstracciones de paginación y cuatro comportamientos, y la abstracción buena se usa en
un solo endpoint.

**Las abstracciones que existen:**
- `PaginatedResponse[T]` — `backend/app/schemas/common.py:21-29` (`items`, `total`, `limit`, `offset`,
  property `has_more`). Correcta. Usada en **un** endpoint de todo el backend:
  `backend/app/api/v1/access_requests.py:305-329`.
- `PaginationParams` — `backend/app/utils/pagination.py:6-17`, un dataclass con clamp de `limit` a
  `[1, 200]`. `rg -n "PaginationParams" app` devuelve **una sola línea: su propia definición**. Código
  muerto desde que se escribió.

**Los cuatro comportamientos que conviven de verdad:**

1. `limit` + `offset` como `Query`, devolviendo una **lista plana sin `total`** — ~9 endpoints:
   `backend/app/api/v1/sales.py:158-159` con `response_model=list[SaleEntryResponse]` (`sales.py:153`),
   y lo mismo en `customers.py:124-125`, `suppliers.py:136-137`, `products.py:270-271`,
   `expenses.py:148-149`, `ingestion.py:596-597`, `others.py:171-172`, `admin.py:301-302`.
2. `limit` **sin** `offset`, con un envelope inventado para ese endpoint:
   `backend/app/api/v1/notifications.py:91-92` devolviendo `NotificationListResponse`
   (`notifications.py:34-36`: `{notifications, unread_count}` — ni `total` ni `has_more`, y la lista no se
   llama `items`). Variante sin envelope en `health_scores.py:117,144,218`.
3. `PaginatedResponse[T]` — el único caso correcto, ya citado.
4. **Sin paginar.** De los 33 endpoints con `response_model=list[...]`, unos 24 no tienen ningún parámetro
   de paginación: `backend/app/api/v1/automations.py:33`, `cash_closes.py:57`, `fields.py:52`. Devuelven
   la tabla entera del tenant y crecen sin techo.

Por qué no se hereda: el punto 1 es el peor de los cuatro, porque *parece* paginado. Un cliente que recibe
50 elementos no puede distinguir "hay 50 en total" de "hay 4.000 y te di la primera página" — no hay
`total` ni `has_more`. La consecuencia visible es un frontend que nunca muestra la segunda página porque
no sabe que existe. El punto 4 es un incidente esperando el primer tenant grande: la lista de turnos de un
lavadero con dos años de historia entra completa en una respuesta JSON.

Y el costo compuesto: cada forma de paginar necesita su propio hook en el frontend. Cuatro formas, cuatro
hooks, y ninguno reutilizable.

## Decisión

1. **Todo endpoint que devuelva una colección devuelve `PaginatedResponse[T]`.** Sin excepciones,
   incluidas las colecciones que hoy son chicas. `response_model=list[...]` está prohibido.
2. **El contrato es uno solo:** `{items: T[], total: int, limit: int, offset: int, has_more: bool}`.
   `items` siempre se llama `items` (no `notifications`, no `results`).
3. **`limit` y `offset`, no `page`/`page_size`, no `skip`, no cursor.** `limit` por defecto 50, máximo
   200; `offset` ≥ 0. Los límites se declaran una vez, en una dependencia compartida
   (`Paginacion = Annotated[PaginationParams, Depends()]`), no repitiendo `Query(default=50, ge=1, le=200)`
   en cada firma. La paginación por cursor puede hacer falta cuando la agenda tenga volumen: entra por una
   ADR propia, no por un endpoint que improvisa.
4. **`total` se calcula siempre**, con un `COUNT(*)` dentro de la misma transacción. Un `total` opcional
   vuelve a poner al cliente en la duda del punto 1.
5. **`BaseRepository.list_by_tenant` devuelve la página y el total juntos**, así el endpoint no puede
   devolver una sin el otro.
6. **El frontend tiene un solo hook** (`usePaginatedQuery`) tipado con el `PaginatedResponse` generado del
   OpenAPI (ADR-0013).

## Consecuencias

- **Gana:** un hook, un componente de paginación, un contrato. El frontend deja de tener que saber cómo
  pagina cada recurso.
- **Gana:** ningún endpoint puede devolver una tabla entera por accidente — el `limit` máximo es parte de
  la dependencia compartida, no de la buena memoria de quien escribe la ruta.
- **Cuesta:** el envelope es ceremonia sobre colecciones genuinamente acotadas (los servicios de un
  lavadero son diez, no diez mil). Lo acepto a propósito: la alternativa es un criterio ("¿esto va a
  crecer?") que se aplica mal, y las colecciones acotadas dejan de serlo sin avisar. Una forma, siempre.
- **Cuesta:** un `COUNT(*)` por request. Con índice sobre `(tenant_id, created_at)` y volúmenes de
  lavadero es irrelevante; si algún día deja de serlo, esa es la señal para la ADR de cursor.
- **Recordar:** `offset` sobre datos que cambian salta y repite filas. Por eso el orden por defecto es
  `(created_at DESC, id DESC)` — determinista — y no `id` solo (ver ADR-0004).

## Cómo se verifica

`backend/app/tests/meta/test_paginacion.py`, recorriendo las rutas de la app real. Es un test que no se
puede satisfacer a medias: cualquier endpoint nuevo entra en el barrido automáticamente.

```python
from typing import get_origin, get_args

def test_ningun_get_devuelve_una_lista_plana():
    app = create_app()
    culpables = [
        f"{r.path}" for r in app.routes
        if isinstance(r, APIRoute) and "GET" in r.methods and r.response_model is not None
        and get_origin(r.response_model) is list
    ]
    assert not culpables, f"devuelven list[...] en vez de PaginatedResponse: {culpables}"

def test_toda_coleccion_usa_el_envelope_unico():
    for r in _rutas_de_coleccion(create_app()):
        assert get_origin(r.response_model) is PaginatedResponse, \
            f"{r.path}: envelope propio ({r.response_model}), no PaginatedResponse"

def test_toda_coleccion_declara_limit_y_offset_con_los_mismos_limites():
    for r in _rutas_de_coleccion(create_app()):
        params = {p.name: p for p in r.dependant.query_params}
        assert {"limit", "offset"} <= params.keys(), f"{r.path}: sin limit/offset"
        assert params["limit"].field_info.le == 200 and params["limit"].field_info.ge == 1
        assert params["offset"].field_info.ge == 0
```

Y el comportamiento, sobre el recurso dummy:

```python
async def test_la_segunda_pagina_es_alcanzable(client, headers):
    await _crear_dummies(60)
    p1 = (await client.get("/v1/dummy-resources?limit=50&offset=0", headers=headers)).json()
    assert p1["total"] == 60 and p1["has_more"] is True and len(p1["items"]) == 50
    p2 = (await client.get("/v1/dummy-resources?limit=50&offset=50", headers=headers)).json()
    assert p2["has_more"] is False and len(p2["items"]) == 10
    assert not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})  # sin solapamiento

async def test_limit_por_encima_del_maximo_es_422(client, headers):
    assert (await client.get("/v1/dummy-resources?limit=100000", headers=headers)).status_code == 422
```
