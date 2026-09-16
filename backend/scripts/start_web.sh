#!/bin/sh
set -eu

: "${PORT:=8000}"
: "${UVICORN_WORKERS:=1}"

# Las migraciones NO corren acá: corren en el preDeployCommand de Railway
# (scripts/migrate.sh), una sola vez por deploy en vez de una por réplica.

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers "$UVICORN_WORKERS"
