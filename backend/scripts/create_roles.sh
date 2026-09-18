#!/bin/sh
# Crea (o re-afirma) los dos roles de Postgres del proyecto — ADR-0002.
#
#   carwash_owner  dueño de las tablas. Solo migra (DATABASE_URL_SYNC).
#   carwash_app    runtime de la API (DATABASE_URL). NOSUPERUSER, NOBYPASSRLS, no
#                  dueño de nada: es el rol sobre el que RLS se nota. Un superusuario
#                  o el dueño de la tabla con BYPASSRLS atraviesan las políticas, y
#                  entonces el aislamiento depende solo del WHERE del repositorio.
#
# Idempotente: se puede correr en cada arranque. Si el rol existe, se re-afirman
# sus atributos y su contraseña; nunca se borra nada.
#
# Lo usan: CI (ci-backend.yml), docker compose (servicio `db-roles`) y, a mano, una
# vez por ambiente de Railway (ver railway.toml).
#
# Entrada (todas obligatorias):
#   ADMIN_DATABASE_URL        postgresql://SUPERUSUARIO:CLAVE@HOST:PUERTO/BASE
#   CARWASH_OWNER_PASSWORD    contraseña de carwash_owner
#   CARWASH_APP_PASSWORD      contraseña de carwash_app
#
# Grants: acá solo CONNECT y el uso del schema. Los permisos sobre TABLAS no van en
# default privileges (le darían a carwash_app escritura sobre alembic_version y
# sobre cualquier tabla futura sin revisión): los da cada migración, tabla por
# tabla, con `grant_app_role` de app/persistence/db/rls.py. Como este script corre
# ANTES de migrar, ese GRANT siempre encuentra el rol.
set -eu

: "${ADMIN_DATABASE_URL:?falta ADMIN_DATABASE_URL (superusuario)}"
: "${CARWASH_OWNER_PASSWORD:?falta CARWASH_OWNER_PASSWORD}"
: "${CARWASH_APP_PASSWORD:?falta CARWASH_APP_PASSWORD}"

echo "[roles] creando/afirmando carwash_owner y carwash_app"

psql "$ADMIN_DATABASE_URL" -q -X -v ON_ERROR_STOP=1 \
  -v owner_pw="$CARWASH_OWNER_PASSWORD" \
  -v app_pw="$CARWASH_APP_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE carwash_owner'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'carwash_owner') \gexec
SELECT 'CREATE ROLE carwash_app'
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'carwash_app') \gexec

SELECT format('ALTER ROLE carwash_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD %L',
              :'owner_pw') \gexec
SELECT format('ALTER ROLE carwash_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
              :'app_pw') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO carwash_owner, carwash_app', current_database()) \gexec

-- Postgres 15+ ya no deja crear en `public` a cualquiera. El dueño migra ahí.
GRANT USAGE, CREATE ON SCHEMA public TO carwash_owner;
GRANT USAGE ON SCHEMA public TO carwash_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Si alguien le dio membresía de otro rol a carwash_app, NOINHERIT no alcanza para
-- ver el problema: se falla explícito si el runtime quedó con privilegios de más.
DO $$
DECLARE r record;
BEGIN
  SELECT rolsuper, rolbypassrls INTO r FROM pg_roles WHERE rolname = 'carwash_app';
  IF r.rolsuper OR r.rolbypassrls THEN
    RAISE EXCEPTION 'carwash_app no puede ser SUPERUSER ni BYPASSRLS';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
              JOIN pg_roles u ON u.oid = m.member
             WHERE u.rolname = 'carwash_app') THEN
    RAISE EXCEPTION 'carwash_app es miembro de otro rol: revisar pg_auth_members';
  END IF;
END $$;
SQL

echo "[roles] OK"
