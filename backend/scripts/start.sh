#!/bin/sh
# Despachador único del arranque (Dockerfile CMD y startCommand de railway.toml).
#
# En Véktor despachaba por rol (web/worker/beat) leyendo VEKTOR_RUNTIME,
# SERVICE_TYPE y RAILWAY_SERVICE_NAME. En la Fase 2 solo hay servicio web, así
# que ese despacho se saca; se conserva el punto único de entrada para que,
# cuando aparezca un segundo proceso, haya un solo lugar donde agregarlo.
set -eu

exec sh scripts/start_web.sh
