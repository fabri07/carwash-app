# ADR-0013 · Los tipos del cliente se generan del OpenAPI, no se escriben

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

El frontend de Véktor mantiene a mano una copia de los schemas del backend.
`frontend/src/types/` tiene **un solo archivo**, `api.ts`, de **390 líneas**, con ~29 interfaces escritas a
mano que espejan respuestas de la API: `frontend/src/types/api.ts:21` (`TokenResponse`), `:28`
(`AuthUserResponse`), `:38` (`UserResponse extends AuthUserResponse`), `:44` (`MeResponse`), `:58`
(`AuthResponse`), y así con forecast, health score, notificaciones, archivos subidos y demás. El propio
mapa de arquitectura del repo lo declara sin eufemismo (`CLAUDE.md:462`):
*"`src/types/api.ts` — Tipos TypeScript de respuestas API"*.

No hay generación: `grep -n "openapi-typescript" frontend/package.json` devuelve cero, y no existe ningún
`schema.d.ts` generado en el repo.

Por qué no se hereda: **el modo de falla es silencioso**. Cuando el backend renombra un campo, agrega un
valor a un enum o vuelve opcional algo que era obligatorio, `tsc` del frontend sigue en verde — está
comparando el código contra una copia desactualizada, no contra el contrato. El error aparece en runtime,
en la pantalla del usuario, como un `undefined` donde iba un monto. Y al revés: un campo que el backend
dejó de mandar sigue existiendo en el tipo, así que el editor lo autocompleta y nadie sospecha.

Hay un agravante de secuencia: cuanto más grande es `api.ts`, más caro es reemplazarlo. 390 líneas todavía
se tiran; 2.000 se mantienen para siempre. Y la regla ya está escrita en el `CLAUDE.md` de este proyecto —
*"El frontend no adivina tipos — `openapi-typescript` genera desde el OpenAPI del backend"*. Esta ADR es
cómo se hace cumplir.

## Decisión

1. **El backend publica el contrato como un artefacto versionado.** `backend/openapi.json` se genera con
   `python -m app.cli.dump_openapi` (que instancia `create_app()` y vuelca `app.openapi()`) y **se
   commitea**. Es la fuente única; el frontend no necesita un backend corriendo para tipar.
2. **El frontend lo consume y no lo interpreta.**
   `npm run gen:api` → `openapi-typescript ../backend/openapi.json -o src/types/api.generated.ts`. El
   archivo generado **se commitea** y lleva un encabezado de "no editar a mano".
3. **Prohibido escribir a mano un tipo que ya está en el contrato.** `src/types/` solo puede contener tipos
   que **no** vengan de la API (estado de UI, props, la forma de la cola offline). Un `interface UserResponse`
   escrito a mano es un error de CI.
4. **La deriva es un fallo de CI, no un descubrimiento.** Dos pasos, los dos con `git diff --exit-code`:
   regenerar el `openapi.json` desde el código del backend y regenerar el `.ts` desde el `openapi.json`. Si
   cualquiera de los dos cambia algo, el commit está incompleto.
5. **Los enums cruzan tipados.** `Role` (ADR-0005) y `VoidReason` (ADR-0003) llegan al frontend como
   uniones de literales. Agregar un rol en el backend rompe cualquier `switch` exhaustivo del frontend que
   no lo cubra — que es precisamente el aviso que se quiere.
6. **`/openapi.json`, `/docs` y `/redoc` siguen deshabilitados en producción**, como en Véktor
   (`CLAUDE.md:253`). El artefacto versionado es lo que hace que apagarlos no cueste nada.
7. **Una sola forma para los errores.** El `ErrorResponse` del backend deja de ser el código muerto que es
   en Véktor (`backend/app/schemas/common.py:36-43`, sin un solo uso fuera de su archivo) y pasa a ser el
   tipo real de todas las respuestas de error, con un `ErrorCode` cerrado — así `DUPLICATE_IDEMPOTENT`
   llega al frontend como un literal tipado y `useOfflineSubmit` deja de compararlo contra un string suelto.

## Consecuencias

- **Gana:** el frontend no puede quedar desincronizado en silencio. La incompatibilidad pasa de ser un bug
  de runtime a ser un `tsc` en rojo, que es donde cuesta minutos y no una llamada del lavadero.
- **Gana:** el diff del PR muestra el cambio de contrato explícitamente — `openapi.json` cambia, y se ve.
  Un cambio de API deja de poder colarse como "un campo más".
- **Gana:** desaparecen las 390 líneas de mantenimiento manual antes de escribirse.
- **Cuesta:** dos archivos generados versionados, que van a producir conflictos de merge cuando dos ramas
  toquen la API. Se resuelven regenerando, no editando a mano — y el CI lo verifica.
- **Cuesta:** los tipos generados por `openapi-typescript` son más verbosos que los escritos a mano
  (`components["schemas"]["UserResponse"]`). Se acota con alias en un archivo chico:
  `export type User = components["schemas"]["UserResponse"]`. Los alias son propiedad del frontend.
- **Recordar:** el contrato es tan bueno como los `response_model` del backend. Un endpoint sin
  `response_model` genera un tipo inútil (`unknown`), así que el test del punto 3 de la verificación exige
  que todos lo declaren. Se apoya en ADR-0006, que ya obliga a `PaginatedResponse[T]` en las colecciones.

## Cómo se verifica

**1 · Que ninguno de los dos artefactos esté vencido** (los dos pasos van al CI):

```bash
cd backend  && uv run python -m app.cli.dump_openapi && git diff --exit-code openapi.json
cd frontend && npm run gen:api                        && git diff --exit-code src/types/api.generated.ts
```

**2 · Que nadie escriba a mano un tipo del contrato** —
`frontend/src/__tests__/meta/tipos-generados.test.ts`:

```ts
const generados = new Set(
  Object.keys(JSON.parse(fs.readFileSync("../backend/openapi.json", "utf8")).components.schemas)
);

it("src/types/ no redeclara ningún schema del backend", () => {
  const aMano = glob.sync("src/types/!(api.generated).ts")
    .flatMap((f) => [...fs.readFileSync(f, "utf8").matchAll(/(?:interface|type)\s+(\w+)/g)].map((m) => m[1]));
  expect(aMano.filter((n) => generados.has(n))).toEqual([]);  // TokenResponse, MeResponse… de Véktor caen acá
});

it("el archivo generado no fue editado a mano", () => {
  expect(fs.readFileSync("src/types/api.generated.ts", "utf8")).toMatch(/do not (modify|edit) it by hand/i);
});
```

**3 · Que el contrato sea generable y completo** —
`backend/app/tests/meta/test_contrato_openapi.py`:

```python
def test_todo_endpoint_declara_response_model():
    # sin response_model el tipo generado es unknown y el frontend vuelve a adivinar.
    culpables = [f"{r.methods} {r.path}" for r in create_app().routes
                 if isinstance(r, APIRoute) and r.response_model is None
                 and r.status_code not in (204,)]
    assert not culpables, f"sin response_model: {culpables}"

def test_todo_error_usa_el_envelope_unico():
    schemas = create_app().openapi()["components"]["schemas"]
    assert "ErrorResponse" in schemas and "ErrorCode" in schemas
    assert "DUPLICATE_IDEMPOTENT" in schemas["ErrorCode"]["enum"]

def test_openapi_esta_apagado_en_produccion(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for ruta in ("/openapi.json", "/docs", "/redoc"):
        assert TestClient(create_app()).get(ruta).status_code == 404
```

**4 · El chequeo de humo que cierra el círculo:** el `tsc` del frontend corre contra
`api.generated.ts`, así que `npm run type-check` en verde con el `openapi.json` recién regenerado **es** la
prueba de que cliente y servidor hablan el mismo idioma. Está en la lista de `FASE-2-ACEPTACION.md`.
