#!/bin/sh
# A11 / ADR-0012 — los dos ambientes existen, se saben distintos y no comparten
# ni base ni secreto de firma. Lo corre `make check-envs`.
#
# Requiere: curl, jq ≥ 1.6 (@base64d). Y las credenciales de un usuario SINTÉTICO de staging
# (backend/scripts/seed_staging.py) en CHECK_ENVS_EMAIL / CHECK_ENVS_PASSWORD
# (se leen del entorno o de ./.env). Sin ellas el chequeo del JWT no se puede
# hacer, y sin ese chequeo A11 no está aprobado: el script falla, no lo saltea.
set -eu

STAGING_API="${STAGING_API:-https://api-staging.carwashdetailapp.com}"
PROD_API="${PROD_API:-https://api.carwashdetailapp.com}"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

fail() { echo "✗ $1" >&2; exit 1; }
ok()   { echo "✓ $1"; }

command -v jq   >/dev/null || fail "falta jq"
command -v curl >/dev/null || fail "falta curl"

s=$(curl -fsS "$STAGING_API/health") || fail "staging /health no responde ($STAGING_API)"
p=$(curl -fsS "$PROD_API/health")    || fail "prod /health no responde ($PROD_API)"

[ "$(printf '%s' "$s" | jq -r .env)" = "staging" ]    || fail "staging no se identifica como staging: $s"
ok "staging se identifica como staging"
[ "$(printf '%s' "$p" | jq -r .env)" = "production" ] || fail "prod no se identifica como production: $p"
ok "prod se identifica como production"

# La comprobación que importa: NO comparten base. Si alguien apuntó staging a la
# base de prod "por un rato", esto se pone rojo.
sf=$(printf '%s' "$s" | jq -r .db_fingerprint)
pf=$(printf '%s' "$p" | jq -r .db_fingerprint)
{ [ -n "$sf" ] && [ "$sf" != "null" ]; } || fail "staging no expone db_fingerprint"
{ [ -n "$pf" ] && [ "$pf" != "null" ]; } || fail "prod no expone db_fingerprint"
[ "$sf" != "$pf" ] || fail "staging y prod comparten la misma base (db_fingerprint $sf)"
ok "bases distintas (staging $sf ≠ prod $pf)"

# Y una sesión de staging no puede entrar en prod.
#
# Un 401 a secas NO prueba eso: el usuario sintético de staging no existe en prod,
# así que prod daría 401 por "usuario inexistente" aunque compartieran
# JWT_SECRET_KEY. Por eso se afirma sobre el CONTENIDO del token (M2): el backend
# emite `iss = carwash-api:<APP_ENV>` y `aud = carwash-app:<APP_ENV>`, y los valida
# al decodificar, ANTES de buscar al usuario. Si el token de staging dice
# `carwash-api:staging`, prod (que exige `carwash-api:production`) lo rechaza por
# emisor sin importar el secreto ni el usuario. Lo que queda sin poder observarse
# desde afuera es que los dos JWT_SECRET_KEY sean distintos: eso se revisa en el
# panel de Railway y se anota en el acta.
{ [ -n "${CHECK_ENVS_EMAIL:-}" ] && [ -n "${CHECK_ENVS_PASSWORD:-}" ]; } \
  || fail "faltan CHECK_ENVS_EMAIL / CHECK_ENVS_PASSWORD (usuario sintético de staging)"

jar=$(mktemp)
trap 'rm -f "$jar"' EXIT

body=$(jq -n --arg e "$CHECK_ENVS_EMAIL" --arg p "$CHECK_ENVS_PASSWORD" '{email:$e, password:$p}')
curl -fsS -o /dev/null -c "$jar" -X POST "$STAGING_API/v1/auth/login" \
  -H 'Content-Type: application/json' -H "Origin: https://staging.carwashdetailapp.com" \
  -d "$body" || fail "no se pudo iniciar sesión en staging"

# En staging la cookie lleva el prefijo del ambiente (COOKIE_NAME_PREFIX=stg_, M3).
STAGING_COOKIE="${STAGING_COOKIE_PREFIX-stg_}access_token"
PROD_COOKIE="${PROD_COOKIE_PREFIX-}access_token"
token=$(awk -v n="$STAGING_COOKIE" '$6 == n {print $7}' "$jar" | tail -1)
[ -n "$token" ] || fail "el login de staging no devolvió la cookie $STAGING_COOKIE"
ok "staging emite la cookie con prefijo ($STAGING_COOKIE)"

claims=$(printf '%s' "$token" | jq -R '
  split(".")[1] | gsub("-"; "+") | gsub("_"; "/")
  | (. + ("===" | .[0:((4 - (length % 4)) % 4)])) as $pad | $pad | @base64d | fromjson' 2>/dev/null) \
  || fail "no se pudo decodificar el payload del JWT de staging"
iss=$(printf '%s' "$claims" | jq -r '.iss // empty')
aud=$(printf '%s' "$claims" | jq -r 'if (.aud|type)=="array" then .aud|join(",") else (.aud // empty) end')
[ "$iss" = "carwash-api:staging" ] || fail "el JWT de staging tiene iss='$iss' (se esperaba carwash-api:staging)"
[ "$aud" = "carwash-app:staging" ] || fail "el JWT de staging tiene aud='$aud' (se esperaba carwash-app:staging)"
ok "el JWT de staging se identifica: iss=$iss aud=$aud"

code=$(curl -s -o /dev/null -w '%{http_code}' "$PROD_API/v1/dummy-resources" \
  -H "Cookie: $PROD_COOKIE=$token")
[ "$code" = "401" ] || fail "el JWT de staging NO da 401 en prod (dio $code)"
ok "el JWT de staging da 401 en prod (rechazo por emisor/audiencia, no por usuario)"

echo "A11 OK"
