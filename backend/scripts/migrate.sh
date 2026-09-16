#!/bin/sh
# Deja la base lista para la versión que está por recibir tráfico.
#
# Portado de Véktor (backend/scripts/migrate.sh). Lo invoca Railway como
# preDeployCommand (ver railway.toml): corre UNA sola vez por deploy, en un
# contenedor one-off con la misma imagen y el mismo env, ANTES de que la versión
# nueva reciba tráfico.
#
# Secuencia fail-safe:
#   1. preflight  → dice contra qué base y con qué rol se migra. NUNCA corta.
#   2. alembic    → la autoridad, con DATABASE_URL_SYNC (rol carwash_owner). Si
#                   falla, `set -e` sale != 0, Railway aborta el deploy y la versión
#                   vieja sigue sirviendo (A14 lo ensaya).
#   3. seed       → SOLO en staging, idempotente, con DATABASE_URL (rol carwash_app,
#                   con RLS activo: el seed pasa por las mismas políticas que la API).
set -eu

# Sin default a propósito: el ambiente se declara, no se supone (M1).
: "${APP_ENV:?APP_ENV es obligatorio (staging | production)}"
# Las migraciones corren con el rol DUEÑO. Si faltara, alembic_url caería a
# DATABASE_URL — el rol de runtime — y la migración fallaría a mitad de camino
# por permisos, o peor, alguien le daría permisos de DDL al runtime para "arreglarlo".
: "${DATABASE_URL_SYNC:?DATABASE_URL_SYNC es obligatorio (rol carwash_owner)}"

python scripts/migrate_preflight.py

echo "[migrate] alembic upgrade head"
alembic upgrade head
echo "[migrate] OK"

# El seed va DESPUÉS de las migraciones: la tabla que escribe puede ser la que
# acaba de crear una migración de este mismo deploy.
if [ "$APP_ENV" = "staging" ]; then
  echo "[migrate] seed sintético de staging"
  python scripts/seed_staging.py
  echo "[migrate] seed OK"
fi
