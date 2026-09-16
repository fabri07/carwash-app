# ADR-0007 · `ruff format` + pre-commit desde el commit 1

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor nunca pasó por un formateador, y a esta altura ya no puede.

- `backend/pyproject.toml:1-15` configura `[tool.ruff]` y `[tool.ruff.lint]` (`select = ["E","F","I","N","UP","B","C4","SIM","ANN"]`),
  pero **no existe** una sección `[tool.ruff.format]`.
- El CI corre solo el linter: `.github/workflows/ci-backend.yml:69-70` → `ruff check .`. No hay
  `ruff format --check` en ningún workflow.
- No existe `.pre-commit-config.yaml` en el repositorio (búsqueda hasta `-maxdepth 3`, sin resultados).
- `backend/Makefile:94-110` define un target `format` que **falla a propósito** y explica por qué; la
  normalización quedó apartada en `backend/Makefile:115-117` (`format-normalize-global`).
- El propio `CLAUDE.md:527` de Véktor lo documenta como regla de trabajo: *"El backend NO está
  normalizado con `ruff format`. No correr `ruff format` ni `make format` durante un cambio funcional"*.

El costo de revertir esto hoy en Véktor: **869 archivos `.py` / 231.346 líneas** bajo `backend/app`. Un
formateo global es un commit gigante que invalida todas las ramas abiertas, y por eso se posterga
indefinidamente. Mientras tanto, cada revisión de código paga el impuesto: diffs con ruido de estilo,
discusiones de formato en los PRs y un `make format` prohibido por escrito.

Heredar esto significa heredar la decisión de **no** poder formatear nunca. Es la deuda más barata de
evitar y la más cara de arreglar después: su costo crece linealmente con el tamaño del repo, y el
momento de costo cero es exactamente ahora, cuando el repo tiene cero archivos `.py`.

## Decisión

1. `backend/pyproject.toml` declara `[tool.ruff.format]` desde el primer commit que introduzca Python.
   Se adopta el default de `ruff format` (sin `skip-magic-trailing-comma`, comillas dobles) — el punto
   es que haya **un** formato, no cuál.
2. `.pre-commit-config.yaml` en la raíz del repositorio, con al menos: `ruff-format`, `ruff` (`--fix`),
   `end-of-file-fixer`, `trailing-whitespace`, `check-merge-conflict`, `detect-private-key`, y
   `prettier` + `eslint` para `frontend/`.
3. El CI corre `ruff format --check .` como paso **bloqueante**, antes de `ruff check .`.
4. Se prohíbe el target `format` que falla: `make format` formatea. El equivalente a
   `format-normalize-global` no existe porque no hace falta.
5. Se pinea Python **3.12** explícitamente: `requires-python = ">=3.12,<3.13"` en `backend/pyproject.toml`,
   `.python-version` con `3.12`, `python:3.12-slim` en el `Dockerfile` y `python-version: "3.12"` en el
   workflow. El Python del sistema de la máquina de desarrollo es 3.14; sin pin, el entorno local y el
   de CI divergen desde el día 1. (Véktor pinea 3.12 en `Dockerfile:2,18,40` y en
   `.github/workflows/ci-backend.yml:51`, pero **no** tiene `requires-python` — no hay sección `[project]`
   en su `pyproject.toml`. Ese hueco no se hereda.)

## Consecuencias

- **Gana:** los diffs solo muestran cambios de intención. La revisión de código —que en este proyecto es
  compuerta, no sugerencia— deja de competir con ruido de estilo. Nadie discute formato nunca más.
- **Gana:** el pre-commit atrapa el secreto antes del push (`detect-private-key`), que en un repo con
  reglas de Ley 25.326 no es un detalle de estilo.
- **Cuesta:** un hook más que instalar en cada clon (`uv run pre-commit install`, incluido en `make setup`).
  Y que el formateador y el linter pueden pelearse si se agregan reglas de `select` que tocan formato
  (`E501` en particular): se resuelve dejando el largo de línea en manos del formateador y no del linter.
- **Recordar:** el pre-commit es una conveniencia local, **no** la compuerta. La compuerta es el CI. Un
  hook salteado con `--no-verify` tiene que morir en el workflow igual.

## Cómo se verifica

```bash
# 1. El formato está aplicado y el linter está limpio.
cd backend && uv run ruff format --check . && uv run ruff check .

# 2. El pre-commit existe, instala y pasa sobre todo el árbol.
uv run pre-commit run --all-files

# 3. El paso bloqueante existe en el CI (si esto falla, la ADR no está aplicada aunque el repo
#    esté formateado: mañana entra un commit sin formatear y nadie se entera).
grep -q 'ruff format --check' .github/workflows/ci-backend.yml

# 4. El pin de Python es consistente en los cuatro lugares.
test "$(cat .python-version)" = "3.12"
grep -q 'requires-python = ">=3.12,<3.13"' backend/pyproject.toml
grep -q 'python:3.12-slim'                 backend/Dockerfile
grep -q 'python-version: "3.12"'           .github/workflows/ci-backend.yml
```

Test automatizado que acompaña a los greps, para que el chequeo no dependa de que alguien corra el script
a mano — `backend/app/tests/meta/test_tooling_contract.py`:

- `test_ruff_format_configurado` — parsea `pyproject.toml` y falla si no existe la tabla `[tool.ruff.format]`.
- `test_ci_corre_format_check` — lee `.github/workflows/ci-backend.yml` y falla si ningún `run:` contiene
  `ruff format --check`.
- `test_precommit_incluye_ruff_format` — parsea `.pre-commit-config.yaml` y falla si no hay un hook con
  `id: ruff-format`.
- `test_pin_de_python_consistente` — falla si las cuatro fuentes del punto 4 no declaran todas `3.12`.
