# ADR-0012 · Staging real, con base propia y datos sintéticos

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

Véktor no tiene staging, y lo dice por escrito. `grep -rni "staging" .github/workflows/ backend/railway.toml
frontend/.vercel/` devuelve **cero resultados**. La única mención en todo el repositorio está en un runbook
de rollout, `docs/runbooks/purchase_cost_rollout.md:29`:

> *"El fail-safe importa más que de costumbre: este repo no tiene staging, así que la variable se setea en
> producción desde el minuto cero y sin ensayo previo."*

La tabla de deploy (`CLAUDE.md:553-572`) confirma la topología: `vektor-api` y `vektor-worker` en Railway,
Postgres en Neon, frontend en Vercel. Prod, y los previews de Vercel — que son solo frontend y apuntan a la
API de producción.

La consecuencia de no tener staging es visible en el diseño del resto del sistema: Véktor tuvo que invertir
mucho en que producción sea segura de tocar. `backend/railway.toml:9` define
`preDeployCommand = "sh scripts/migrate.sh"` con `set -eu` y fail-safe; existe
`backend/scripts/migrate_preflight.py` (91 líneas) cuya única razón de ser es **decirte contra qué base
estás migrando**, escrito después de un incidente donde no se podía saber; y hay siete migraciones que
tuvieron que volverse idempotentes a mano después de que un `DuplicateColumn` abortara un deploy
(documentado en `CLAUDE.md:83`). Todo eso es excelente ingeniería, y toda es compensación por no tener
dónde ensayar.

Por qué no se hereda: el `preDeployCommand` corre `alembic upgrade head` contra la base de producción en
**cada** deploy. La primera vez que una migración de este proyecto se ejecute contra datos reales va a ser
la primera vez que se ejecute, punto. Y los datos reales acá son la operación de un lavadero que dejó de
usar su planilla: si la migración falla a la mitad, no hay Sheet al que volver.

## Objeción

**"Staging real" a secas produce, en la práctica, uno de dos ambientes inútiles**, y el roadmap no dice
cuál quiere evitar.

Si staging se alimenta con un dump de producción, es un **segundo ambiente con datos personales de
clientes reales** —patentes, teléfonos, nombres— con controles de acceso más flojos que los de prod. Es la
peor combinación posible bajo la Ley 25.326, y es lo que pasa por default cuando nadie decide.

Si staging se deja sin datos, nadie lo usa, se vuelve una URL que nadie abre, y su verde deja de
significar algo. Un ambiente en el que no se confía es peor que no tenerlo: da permiso para saltearlo.

Por eso la decisión no es "crear staging" sino tres cosas juntas: base propia, **datos sintéticos generados
por un script versionado** (nunca un dump de prod), y una **regla de promoción** que hace que saltear
staging sea imposible en vez de desaconsejable. Sin las tres, esta ADR es una URL más.

## Decisión

1. **Dos ambientes de backend en Railway**, cada uno con su **propia base de datos**:
   `api-staging.carwash.app` y `api.carwash.app`. Staging no comparte ni la instancia ni las credenciales.
2. **Dos proyectos/entornos de Vercel**: `staging.carwash.app` y `app.carwash.app`, cada uno apuntando a su
   API. Los previews de PR siguen existiendo y apuntan a **staging**, nunca a prod.
3. **Datos sintéticos, versionados.** `backend/scripts/seed_staging.py` genera dos tenants de mentira con
   datos inventados. Está **prohibido** restaurar un dump de producción en staging: nombres, teléfonos y
   patentes son PII bajo Ley 25.326, y la patente es el PII fuerte de este dominio.
4. **Regla de promoción, mecánica:** merge a `main` → deploy automático a staging. Producción se despliega
   desde un **tag** y a través de un GitHub Environment con revisor requerido. No hay camino que llegue a
   prod sin pasar por staging primero.
5. **Ninguna migración llega a prod sin haber corrido en staging.** El job de deploy a producción verifica
   que el `head` de alembic que va a aplicar ya esté aplicado en staging; si no, corta.
6. **`/health` dice quién es.** Responde `{"status", "env", "commit", "db_fingerprint"}`, donde `env` ∈
   `{local, test, staging, production}` y `db_fingerprint` son los primeros 8 hex de un
   `sha256(host + "/" + dbname)` — identifica la base **sin** exponerla en un endpoint público.
7. **Secretos separados.** Ninguna variable de entorno se comparte entre ambientes. Staging tiene su propio
   `JWT_SECRET_KEY`: un token de staging no puede valer en producción.
8. **El preflight de migración se porta igual** (`migrate_preflight.py`). Tener staging no lo hace
   innecesario; lo hace, por fin, ensayable.

## Consecuencias

- **Gana:** la migración que toca la tabla de turnos se ejecuta dos veces, y la primera es contra datos que
  a nadie le importan. En la Fase 8 (migración y go-live) eso deja de ser comodidad y pasa a ser la
  diferencia entre un ensayo y un estreno.
- **Gana:** el `db_fingerprint` de `/health` responde de un vistazo la pregunta que costó un incidente en
  Véktor: *¿contra qué base está corriendo esto?*
- **Cuesta:** plata. Un servicio de Railway y una instancia de Postgres más, todos los meses. Es el costo
  explícito de la decisión y hay que aceptarlo como tal, no descubrirlo en la factura.
- **Cuesta:** los deploys a prod dejan de ser un `git push`. Pasan a ser un tag y una aprobación. Es a
  propósito.
- **Cuesta:** el seed sintético hay que mantenerlo. Cuando la Fase 3 agregue tablas, el seed las tiene que
  cubrir o staging queda a medias — que es exactamente cómo un ambiente se vuelve el que nadie usa. El
  chequeo del punto 2 de la verificación lo ata.
- **Recordar:** staging **no** es un ambiente de pruebas de carga ni de datos de prueba manuales de largo
  plazo. Se puede resetear entero en cualquier momento; el seed es la fuente, no lo que alguien cargó a
  mano.

## Cómo se verifica

**1 · Los dos ambientes existen, son distintos y se saben distintos** —
`make check-envs` (script `scripts/check_envs.sh`, dueño: `deploy`):

```bash
set -eu
s=$(curl -fsS https://api-staging.carwash.app/health)
p=$(curl -fsS https://api.carwash.app/health)

[ "$(jq -r .env <<<"$s")" = "staging" ]     || { echo "staging no se identifica como staging"; exit 1; }
[ "$(jq -r .env <<<"$p")" = "production" ]  || { echo "prod no se identifica como production"; exit 1; }

# la comprobación que importa: NO comparten base. Si alguien apuntó staging a la base de prod
# "por un rato", esto se pone rojo.
[ "$(jq -r .db_fingerprint <<<"$s")" != "$(jq -r .db_fingerprint <<<"$p")" ] \
  || { echo "staging y prod comparten la misma base"; exit 1; }

# y tampoco comparten secreto de firma: un token emitido por staging no puede entrar en prod.
tok=$(curl -fsS -X POST https://api-staging.carwash.app/v1/auth/login -d "$CRED" | jq -r .token_para_test)
[ "$(curl -s -o /dev/null -w '%{http_code}' https://api.carwash.app/v1/dummy-resources \
      -H "Cookie: access_token=$tok")" = "401" ] || { echo "el JWT de staging vale en prod"; exit 1; }
```

**2 · Que el seed cubra el esquema entero** — `backend/app/tests/scripts/test_seed_staging.py`:

```python
async def test_el_seed_deja_al_menos_una_fila_en_toda_tabla_de_negocio(pg_session):
    # es lo que impide que staging quede a medias cuando la Fase 3 agregue tablas.
    faltan = [t.name for t in Base.metadata.tables.values()
              if "tenant_id" in t.c
              and await pg_session.scalar(text(f"SELECT count(*) FROM {t.name}")) == 0]
    assert not faltan, f"el seed de staging no cubre: {faltan}"

def test_el_seed_no_lee_datos_de_produccion():
    src = Path("scripts/seed_staging.py").read_text()
    for prohibido in ("pg_restore", "pg_dump", "DATABASE_URL_PROD", "neon.tech"):
        assert prohibido not in src, f"el seed toca producción: {prohibido}"
```

**3 · Que el camino a prod pase por staging** — `backend/app/tests/meta/test_pipeline_de_deploy.py`:

```python
def test_prod_exige_aprobacion_y_tag():
    wf = yaml.safe_load(Path("../.github/workflows/deploy-prod.yml").read_text())
    assert wf[True] == {"push": {"tags": ["v*"]}}, "prod no puede dispararse desde un push a rama"
    assert wf["jobs"]["deploy"]["environment"] == "production"   # GitHub Environment con revisor

def test_staging_se_despliega_solo_desde_main():
    wf = yaml.safe_load(Path("../.github/workflows/deploy-staging.yml").read_text())
    assert wf[True]["push"]["branches"] == ["main"]

def test_el_deploy_a_prod_verifica_el_head_de_alembic_en_staging():
    assert "verificar-migracion-en-staging" in Path("../.github/workflows/deploy-prod.yml").read_text()
```
