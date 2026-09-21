#!/bin/sh
# Smoke post-deploy. Dos preguntas, las dos obligatorias:
#
#   1. /health  → ¿este ambiente sirve EL COMMIT de este deploy?
#   2. /ready   → ¿la base tiene aplicado EL ESQUEMA que ese commit espera?
#
#   sh scripts/smoke_health.sh <base-url> <ambiente-esperado> <sha-esperado>
#
# Por qué (1) exige el commit y no solo el ambiente: si la migración del
# preDeployCommand falla, Railway aborta el deploy y la versión ANTERIOR sigue
# sirviendo (el fail-safe de migrate.sh). Esa versión también responde
# `env: staging`, así que un smoke que mirara solo el ambiente daría verde con un
# deploy fallado. El commit lo fija el workflow en la variable GIT_COMMIT_SHA antes
# de `railway up`: `railway up` no inyecta RAILWAY_GIT_COMMIT_SHA (eso es solo para
# servicios conectados a un repo, y los nuestros no lo están a propósito).
#
# Por qué (2) existe: el 2026-09-21 staging estuvo dos días con la base VACÍA y
# todos los deploys en verde. El `Pre-Deploy Command` no estaba cargado en el panel
# de Railway — que ignora el `preDeployCommand` de backend/railway.toml — así que
# las migraciones NUNCA corrieron. `/health` no toca ninguna tabla y la conexión de
# `/ready` abría igual contra una base sin una sola tabla, de modo que (1) pasaba
# perfecto. El síntoma le llegó al dueño recién al intentar entrar a la app.
# `/ready` compara ahora la revisión aplicada contra el head del código.
set -eu

base="${1%/}"
env_esperado="$2"
sha_esperado="$3"
intentos="${SMOKE_INTENTOS:-40}"   # × 15 s = 10 min: build + migración + healthcheck
pausa="${SMOKE_PAUSA:-15}"
# Archivo propio por corrida: con uno fijo, dos smokes en paralelo se pisan la respuesta.
cuerpo=$(mktemp)
trap 'rm -f "$cuerpo"' EXIT

# ── 1. /health: ambiente y commit ────────────────────────────────────────────
i=1
servido=""
while [ "$i" -le "$intentos" ]; do
  if curl -fsS -m 10 "$base/health" -o "$cuerpo" 2>/dev/null; then
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
      servido="si"
      break
    fi
    echo "responde $sha_actual, esperando $sha_esperado ($i/$intentos)"
  else
    echo "sin respuesta todavía ($i/$intentos)"
  fi
  i=$((i + 1))
  sleep "$pausa"
done

if [ -z "$servido" ]; then
  echo "el deploy de $sha_esperado nunca llegó a servir: mirar los Deploy Logs del servicio en Railway"
  echo "(si falló la migración, la versión anterior sigue sirviendo: es el fail-safe, no una caída)"
  exit 1
fi

# ── 2. /ready: base, esquema y redis ─────────────────────────────────────────
# Pocos reintentos: /health ya confirmó que la versión nueva recibe tráfico, así que
# acá solo se le da margen a una dependencia que tarde en levantar, no al deploy.
i=1
while :; do
  codigo=$(curl -sS -m 10 -o "$cuerpo" -w '%{http_code}' "$base/ready" 2>/dev/null || echo 000)
  [ "$codigo" = "200" ] && break
  if [ "$i" -ge "${SMOKE_READY_INTENTOS:-3}" ]; then
    cat "$cuerpo" 2>/dev/null || true
    echo
    echo "/ready respondió $codigo: el deploy sirve el commit pero NO está sano"
    # El modo de falla que costó dos días: sin este mensaje, el que lo lea vuelve a
    # buscar el problema en la app y no en el pipeline.
    if jq -e '.checks.schema.ok == false' "$cuerpo" >/dev/null 2>&1; then
      echo "   → el ESQUEMA no es el que espera este commit: $(jq -r .checks.schema.error "$cuerpo")"
      echo "   → las migraciones no corrieron. Railway IGNORA el preDeployCommand de"
      echo "     backend/railway.toml: revisar Settings → Deploy → Pre-Deploy Command"
      echo "     del servicio, que tiene que decir 'sh scripts/migrate.sh'."
    fi
    exit 1
  fi
  echo "/ready responde $codigo, reintento ($i)"
  i=$((i + 1))
  sleep "$pausa"
done

cat "$cuerpo"
echo
echo "OK: $env_esperado está listo (base, esquema y redis)"
