#!/bin/sh
# Smoke post-deploy: espera a que /health diga el ambiente Y el commit de ESTE deploy.
#
#   sh scripts/smoke_health.sh <url-de-health> <ambiente-esperado> <sha-esperado>
#
# Por qué exige el commit y no solo el ambiente: si la migración del preDeployCommand
# falla, Railway aborta el deploy y la versión ANTERIOR sigue sirviendo (el fail-safe de
# migrate.sh). Esa versión también responde `env: staging`, así que un smoke que mirara
# solo el ambiente daría verde con un deploy fallado. El commit lo fija el workflow en la
# variable GIT_COMMIT_SHA antes de `railway up`: `railway up` no inyecta
# RAILWAY_GIT_COMMIT_SHA (eso es solo para servicios conectados a un repo, y los nuestros
# no lo están a propósito).
set -eu

url="$1"
env_esperado="$2"
sha_esperado="$3"
intentos="${SMOKE_INTENTOS:-40}"   # × 15 s = 10 min: build + migración + healthcheck
pausa="${SMOKE_PAUSA:-15}"
# Archivo propio por corrida: con uno fijo, dos smokes en paralelo se pisan la respuesta.
cuerpo=$(mktemp)
trap 'rm -f "$cuerpo"' EXIT

i=1
while [ "$i" -le "$intentos" ]; do
  if curl -fsS -m 10 "$url" -o "$cuerpo" 2>/dev/null; then
    env_actual=$(jq -r .env "$cuerpo")
    sha_actual=$(jq -r .commit "$cuerpo")
    if [ "$env_actual" != "$env_esperado" ]; then
      cat "$cuerpo"
      echo "el ambiente responde '$env_actual', se esperaba '$env_esperado': algo apunta mal"
      exit 1
    fi
    if [ "$sha_actual" = "$sha_esperado" ]; then
      cat "$cuerpo"
      echo "OK: $env_esperado sirve $sha_esperado"
      exit 0
    fi
    echo "responde $sha_actual, esperando $sha_esperado ($i/$intentos)"
  else
    echo "sin respuesta todavía ($i/$intentos)"
  fi
  i=$((i + 1))
  sleep "$pausa"
done

echo "el deploy de $sha_esperado nunca llegó a servir: mirar los Deploy Logs del servicio en Railway"
echo "(si falló la migración, la versión anterior sigue sirviendo: es el fail-safe, no una caída)"
exit 1
