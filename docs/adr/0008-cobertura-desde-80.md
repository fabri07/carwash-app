# ADR-0008 · La cobertura arranca en 80% y solo puede subir

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor tiene 832 tests (`grep -rn "^def test_" backend/app/tests | wc -l`) y una compuerta de cobertura
en **60%**:

- `.github/workflows/ci-backend.yml:103` → `--cov-fail-under=60`
- `backend/Makefile:78-79` → `test-cov: pytest --cov=app --cov-report=term-missing --cov-fail-under=60`
- `README.md:263` → *"pytest — tests con cobertura mínima 60%"*
- `CLAUDE.md:545` → *"`make test` no la mide (default rápido); `make test-cov` exige 60%, igual que CI"*

El número está repetido en tres lugares que se editan por separado, y el servicio MCP tiene su propia
compuerta en **15%** (`.github/workflows/ci-mcp.yml:94`), documentada en `ci-mcp.yml:65-69` como *"un piso
ANTI-REGRESIÓN, no una meta"*. Esa frase es el diagnóstico: cuando el piso se fija donde está el código en
vez de donde debería estar, deja de tirar para arriba.

El frontend ni siquiera tiene piso: `frontend/jest.config.ts` (15 líneas, leído entero) **no declara
`coverageThreshold`**, y `frontend/package.json:11` define `"test": "jest"` sin flag de cobertura. Hay 61
archivos `*.test.ts*` y ninguna medición obligatoria de qué cubren.

Por qué no se hereda: un piso bajo no es neutral, es una autorización. 60% significa que 4 de cada 10
líneas pueden entrar sin una sola prueba, y como el número solo se toca cuando molesta, el movimiento
histórico es siempre hacia abajo. Subirlo después es caro (hay que escribir los tests que faltan de golpe)
y por eso nunca se sube. Ahora, con cero líneas de código, el 80% cuesta cero.

## Objeción

**El número no es el control; el trinquete sí.** Un porcentaje global es un instrumento flojo: se sube
testeando lo trivial —getters, `__repr__`, schemas Pydantic— y se puede llegar a 80% sin tocar el camino
que importa. Y el problema real de Véktor no es 60 versus 80: es que el 60 nunca fue un piso, fue un
techo que bajó hasta donde estaba el código. Con un solo número no hay forma de distinguir "bajó porque
entró un módulo grande sin tests" de "bajó porque alguien ajustó la config".

Por eso la decisión incluye tres cosas que el roadmap no menciona y sin las cuales el 80% se degrada a un
60% con otro nombre: (a) el número vive en **un** lugar y las demás fuentes se verifican contra él,
(b) hay un piso histórico versionado que no puede bajar sin que se vea en el diff, y (c) el módulo que es
la frontera de seguridad —el aislamiento multi-tenant— no se mide por porcentaje sino por comportamiento
(ADR-0002), porque una línea ejecutada no es una línea probada.

## Decisión

1. **El piso es 80%**, para backend (`--cov-fail-under=80`) y para frontend
   (`coverageThreshold.global` en 80 para `statements`, `branches`, `functions` y `lines`).
2. **Una sola fuente del número.** El backend lo declara en `backend/pyproject.toml`
   (`[tool.coverage.report] fail_under = 80`); `make test-cov` y el workflow **no lo repiten**: corren
   `pytest --cov=app` y dejan que la config mande. Repetir el número en tres archivos es cómo se desincronizó
   en Véktor.
3. **Trinquete versionado.** `backend/.coverage-floor` contiene el piso histórico más alto alcanzado. Un
   test falla si `fail_under <` ese valor. Subir el piso es editar un archivo de una línea; bajarlo
   también — pero aparece en el diff con nombre y apellido y lo tiene que aprobar el revisor.
4. **`make test` (rápido, sin cobertura) sigue existiendo** para el ciclo corto. La compuerta es
   `make test-cov` y el CI.
5. **Cobertura sobre `app/`, no sobre `scripts/`.** Y `# pragma: no cover` requiere un comentario con el
   motivo en la misma línea; un test cuenta los pragmas y falla si alguno no lo tiene.
6. **El aislamiento no se mide en porcentaje.** Los tests de ADR-0002 son obligatorios por nombre: si el
   archivo de tests cruzados no existe o no corre, el CI falla, aunque la cobertura diera 100%.

## Consecuencias

- **Gana:** el código no testeado no entra. En un proyecto donde el criterio de éxito es que un lavadero
  real opere una semana sin volver al Sheet, la cobertura no es higiene: es lo que permite tocar la caja
  sin romper el turnero.
- **Gana:** el trinquete convierte "la cobertura bajó" de un hecho invisible a un conflicto de merge.
- **Cuesta:** el ciclo de desarrollo es más lento desde el primer día. Es el costo que el roadmap ya
  aceptó al poner `/code-review` como compuerta y no como sugerencia.
- **Cuesta:** con poco código, el 80% se alcanza sin esfuerzo y da una sensación falsa de seguridad. Por
  eso el punto 6: los tests que importan están listados por nombre, no por porcentaje.
- **Recordar:** el piso sube cuando la fase cierra, no en el medio. Subirlo con una rama abierta pone en
  rojo trabajo ajeno.

## Cómo se verifica

```bash
cd backend && uv run pytest --cov=app                  # falla bajo 80 (fail_under sale de pyproject.toml)
cd frontend && npm run test:cov                        # falla bajo 80 (coverageThreshold.global)
```

`backend/app/tests/meta/test_compuerta_de_cobertura.py`:

```python
def test_el_piso_es_al_menos_80():
    cfg = tomllib.loads(Path("pyproject.toml").read_text())
    assert cfg["tool"]["coverage"]["report"]["fail_under"] >= 80

def test_el_piso_no_bajo_respecto_del_historico():
    historico = int(Path(".coverage-floor").read_text().strip())
    actual = tomllib.loads(Path("pyproject.toml").read_text())["tool"]["coverage"]["report"]["fail_under"]
    assert actual >= historico, f"el piso bajó de {historico} a {actual}"

def test_nadie_repite_el_numero_fuera_de_pyproject():
    # el modo exacto en que Véktor terminó con 60 en tres archivos distintos.
    for p in (Path("../Makefile"), Path("../.github/workflows/ci-backend.yml")):
        assert "cov-fail-under" not in p.read_text(), f"{p}: el piso se declara sólo en pyproject.toml"

def test_todo_pragma_no_cover_explica_por_que():
    culpables = [f"{p}:{n}" for p in Path("app").rglob("*.py")
                 for n, l in enumerate(p.read_text().splitlines(), 1)
                 if "pragma: no cover" in l and "#" not in l.split("pragma: no cover")[1]]
    assert not culpables, f"pragma sin motivo: {culpables}"

def test_los_tests_de_aislamiento_existen_y_estan_habilitados():
    # el porcentaje no puede tapar la ausencia del test que importa.
    ruta = Path("app/tests/security/test_aislamiento_tenants.py")
    assert ruta.exists(), "falta el test cruzado de tenants (ADR-0002)"
    assert "skip" not in ruta.read_text()
```

Frontend — `frontend/src/__tests__/meta/cobertura.test.ts`:

```ts
it("el umbral global de jest es 80 en las cuatro métricas", async () => {
  const { default: config } = await import("../../../jest.config");
  for (const m of ["statements", "branches", "functions", "lines"] as const) {
    expect(config.coverageThreshold!.global[m]).toBeGreaterThanOrEqual(80);
  }
});
```
