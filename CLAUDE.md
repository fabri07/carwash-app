# CLAUDE.md

Guía para Claude Code al trabajar en este repositorio.

## Qué es esto

SaaS multi-tenant para lavaderos y centros de detailing. Nace del sistema real que opera
**Sola CleanCars** (Google Sheet + Apps Script), que pasa a ser el tenant #1 y es la fuente de las
reglas de negocio.

**Criterio de éxito de la primera entrega:** Sola CleanCars opera **una semana completa** sin volver
a Google Sheets. El MVP **no** se declara terminado porque "la app funciona". Ante cualquier duda de
alcance, la pregunta es: *si esto falta, ¿lo obliga a abrir la planilla?* Si sí, entra. Si no, va a v2.

## Estado actual

**Fases 1 (spec) y 2 (infraestructura) completas en código.** Hay backend (FastAPI, auth con cookies
httpOnly, RLS en todas las tablas) y frontend (Next.js, login/registro/panel vacío, cola offline, PWA),
sin nada del dominio del lavadero todavía. Contrato de la fase: `docs/adr/` (13 ADRs),
`docs/adr/PORT-MANIFEST.md` y `docs/adr/FASE-2-ACEPTACION.md`. El checkpoint humano de F2 (registrarse
en la URL desplegada) depende de configurar GitHub, Railway y Vercel.

Roadmap completo de 8 fases: `docs/ROADMAP.md`.

```
F1 Spec → F2 Infra → F3 Dominio → F4 Onboarding → F5 Turnero
       → F6 Operación → F7 Caja/CRM → F7B Costos → F8 Migración/go-live
```

> ⚠️ **`docs/spec/` y `docs/legacy/` no están en este repositorio.** Son local-only (ver `.gitignore`):
> contienen datos personales de clientes reales y el código del sistema en producción de Sola CleanCars.
> Las referencias a esos archivos que siguen son válidas en la copia de trabajo, no en el repo publicado.

## Jerarquía de autoridad

Cuando dos fuentes se contradicen, gana la de más arriba:

1. **`docs/spec/05-decisiones-producto.md`** — decisiones del dueño. Mandan sobre todo lo demás.
2. **`docs/spec/02*-reglas-*.md`** — lo que el código legacy hace hoy, citado a función y offset.
3. **`docs/legacy/appsscript/turnero_publico/`** — la fuente canónica (10 archivos, ~179 KB).
4. Textos de ayuda y notas dentro de la planilla — **frecuentemente desactualizados**, no son autoridad.

Las copias sueltas en `docs/legacy/appsscript/*.gs` son una versión **anterior y parcial**. No las uses
como referencia; ver `docs/spec/00-fuente-canonica.md` para el veredicto y su evidencia.

> ⚠️ `Code.gs`, `CodeParte2.gs` y `Admin.gs` están **minificados en una sola línea**. Citalos por
> **nombre de función + offset de carácter**, nunca por número de línea.

## Cómo se trabaja

Cada fase corre el mismo ciclo, con equipos de agentes en paralelo acotados a directorios disjuntos:

```
T1 CONTRATO  → T2 CONSTRUCCIÓN (paralelo) → T3 VERIFICACIÓN → T3b /code-review → T4 CHECKPOINT humano
```

Reglas que no se negocian:

- **Un dueño por carpeta.** Dos agentes nunca escriben el mismo archivo al mismo tiempo.
- **El contrato va primero.** Schemas y tests de aceptación antes de implementar.
- **El frontend no adivina tipos** — `openapi-typescript` genera desde el OpenAPI del backend.
- **El tester de aislamiento es un agente distinto del implementador.**
- **`/code-review` es compuerta**, no sugerencia: ningún hallazgo llega abierto al checkpoint sin
  resolver o diferir con motivo escrito.
- **Ninguna fase cierra en rojo.**

## Origen de la infraestructura

La capa de infraestructura se porta **a mano** desde Véktor (`~/dev/vektor/Vektor/`), un SaaS
multi-tenant en producción del mismo dueño. **No es un fork ni un monorepo compartido.**

Piezas de alto valor identificadas (detalle y rutas en `docs/ROADMAP.md`, Fase 2):

- `_savepoint.py` — resuelve el flush implícito de `begin_nested()` y clasifica violaciones de unique
  en Postgres *y* SQLite. Caro de redescubrir.
- `conftest.py` — engine session-scoped + savepoint-rollback. Suite de minutos en vez de horas.
- `offlineQueueStore` + `useOfflineSubmit` — cola offline idempotente, hecha a medida para cargar un
  lavado sin señal.
- `lib/api.ts` — refresh de token single-flight.
- `migrate.sh` + `migrate_preflight.py` — deploy fail-safe que dice contra qué base migró.

**Deuda de Véktor que NO se hereda** (13 ADRs en `docs/adr/`): `TenantMixin` real, RLS cableado de
verdad, un solo flavor de soft-delete, PK siempre `id`, roles como Enum, paginación única,
`ruff format` desde el commit 1, cobertura desde 80%, cookie httpOnly con middleware real,
shadcn/ui + react-hook-form, PWA desde el día 1, staging real, tipos generados del OpenAPI.

## Decisiones de arquitectura ya tomadas

- **Ocupación por intervalo, no por slot.** `resources` (puestos) + `[start_at, end_at)` con
  `EXCLUDE USING gist` sobre `tstzrange`. Los servicios duran 60/90/120/300 min y puede haber varios
  puestos. Un `UNIQUE (business_id, slot)` **no sirve**.
- **`bookings` y `jobs` son dos tablas.** Un ingreso sin turno crea un job sin booking.
- **`job_events` es append-only y es la fuente de verdad** de las transiciones; `jobs.status` es caché
  del último evento. `occurred_at` lo pone el cliente (momento del toque), distinto de `created_at`.
- **La inspección es opcional** y vive en `job_inspections` (0..1 por job, nullable). Un job se abre,
  cierra y cobra sin tocarla. *No reconstruir `CONTROL_INICIO`.*
- **Aislamiento multi-tenant:** RLS de Postgres es la primera red, los tests la segunda. Recurso ajeno
  devuelve **404, nunca 403** (no filtrar existencia).

## Trampas del legacy: extraer para NO reproducir

Estas están verificadas contra el código, no supuestas. El sistema nuevo debe hacer lo contrario:

| Trampa | Qué hace el legacy |
|---|---|
| `parseMoney_` | `"20.000"` → `20`. Error de factor 1000 con formato argentino. `"abc"` → `0` silencioso. |
| Auth del panel | **No existe.** El PIN nunca se lee y el panel arranca solo sin mostrar login. |
| `CLIENTES` | Se borra entera y se reconstruye desde las transacciones en cada cierre de servicio. |
| Identidad de cliente | Tres políticas de normalización de teléfono en la misma operación. El teléfono **no debe ser PK**. |
| Parámetros | 7 valores de `CONFIGURACION` se muestran configurables y el código nunca los lee. |
| Comisiones | `getCommissionRate_` lee el rango hardcodeado `A28:C31`; insertar una fila rompe todo en silencio. |
| Cobro | No hay cobro parcial ni medios múltiples; un segundo cobro pisa al primero sin sumar. |

Detalle completo y citas en `docs/spec/02-reglas-negocio.md`.

## Zonas sin validación empírica

Reglas que salen **solo del código**, sin un dato real que las respalde. Confirmar contra la operación
antes de implementar:

- `CANCELACIONES`, `LISTA_ESPERA`, `DISPONIBILIDAD_SEMANAL` — vacías.
- `COTIZACIONES` — 1 fila, cargada a mano. **El flujo de cotización no existe en el código**: es
  insert-only y nunca se vuelve a leer. D-006 se diseña, no se copia.
- `EMPLEADOS`, `PAGOS_EMPLEADOS`, `RETIROS_DUEÑO` — sin una sola línea de código que las toque.
- La resolución de señas no tiene cierre: `DEVUELTA` y `RETENIDA` no existen en ningún `.gs`.

## Seguridad y datos

- **No versionar secretos.** El PIN de administración de la planilla está redactado como
  `«REDACTADO»` en los documentos. No reintroducirlo.
- **La patente es el PII fuerte de este dominio** — equivale al CUIT en Véktor. Va en el scrubbing de
  Sentry (`_PATENTE_VALUE_RE`).
- `docs/legacy/` es **evidencia de solo lectura**. No editar esos archivos.

## Comandos

El `Makefile` está en la raíz (`make help` lista todo):

```bash
make check          # formato, ruff/eslint, mypy strict + tsc, pre-commit
make test-cov       # pytest ≥80% + jest con cobertura
make test-pg        # migraciones y tests RLS contra Postgres real (docker)
make openapi-check  # openapi.json y tipos del frontend al día
cd frontend && npm run build
```

Si el puerto 5432 está ocupado (el Postgres de Véktor), `POSTGRES_PORT=5434 make test-pg`.

## Idioma

Documentación, commits y comunicación en **español rioplatense**. Identificadores de código y
mensajes de error de sistema en inglés.
