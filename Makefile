# Fachada de comandos del repo (dueño: deploy). No contiene lógica propia:
# llama a uv, npm y docker compose. Portado de Véktor (backend/Makefile).
#
# Diferencias con Véktor, a propósito:
#   - `make format` FORMATEA (ADR-0007). En Véktor fallaba a propósito porque el
#     backend nunca pasó por ruff format y ya no podía.
#   - `make test-cov` no declara el piso de cobertura: sale de
#     [tool.coverage.report] en backend/pyproject.toml, su única fuente (ADR-0008).
#   - Los comandos que tocan la base corren DENTRO del contenedor `backend`, cuyo
#     DATABASE_URL está fijado al Postgres local en docker-compose.yml. Un
#     DATABASE_URL exportado en tu shell no puede desviarlos a una base remota.

.DEFAULT_GOAL := help
SHELL := /bin/bash

DC       := docker compose
BACKEND  := cd backend &&
FRONTEND := cd frontend &&
UV       := uv run --frozen

# Base de tests Postgres, SEPARADA de la de desarrollo: test-pg hace
# `downgrade base`, y no queremos que eso borre lo que tenés cargado en `carwash`.
# Contrato con el backend (conftest_pg.py): PG_TEST_URL es postgresql:// SIN driver,
# con el superusuario del contenedor; los tests abren su propio engine como carwash_app.
PG_PORT           := $${POSTGRES_PORT:-5432}
PG_TEST_DB        := carwash_test
PG_TEST_URL       := postgresql://carwash:carwash@localhost:$(PG_PORT)/$(PG_TEST_DB)
PG_TEST_OWNER_URL := postgresql+psycopg2://carwash_owner:$${CARWASH_OWNER_PASSWORD:-carwash_owner_test}@localhost:$(PG_PORT)/$(PG_TEST_DB)

.PHONY: help dev dev-bg stop logs shell \
        migrate migrate-down migrate-create migrate-history db-reset roles seed-staging \
        test test-cov test-fast test-watch test-file test-pg test-frontend \
        format format-check lint fix typecheck check precommit \
        openapi gen-api openapi-check check-envs \
        build clean install setup

help: ## Lista los targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Desarrollo ────────────────────────────────────────────────────────────────
dev: ## Levanta postgres + redis + backend con hot reload
	$(DC) up --build

dev-bg: ## Igual que dev, en segundo plano (espera a los healthchecks)
	$(DC) up --build -d --wait

stop: ## Baja los servicios de docker compose
	$(DC) down

logs: ## Sigue los logs de todos los servicios
	$(DC) logs -f

shell: ## Shell dentro del contenedor backend
	$(DC) exec backend bash

# ── Base y migraciones (siempre contra el Postgres LOCAL del contenedor) ─────
migrate: ## alembic upgrade head (con preflight) contra la base local
	$(DC) exec backend python scripts/migrate_preflight.py
	$(DC) exec backend alembic upgrade head

migrate-down: ## Revierte la última migración (base local)
	$(DC) exec backend alembic downgrade -1

migrate-create: ## Nueva migración autogenerada: make migrate-create MSG="mensaje"
	@test -n "$(MSG)" || { echo 'Falta MSG: make migrate-create MSG="mensaje"'; exit 1; }
	$(DC) exec backend alembic revision --autogenerate -m "$(MSG)"

migrate-history: ## Historia de migraciones
	$(DC) exec backend alembic history --verbose

db-reset: ## PELIGRO: borra y recrea la base LOCAL, re-afirma roles y migra
	$(DC) exec postgres psql -U carwash -d postgres -c "DROP DATABASE IF EXISTS carwash WITH (FORCE);"
	$(DC) exec postgres psql -U carwash -d postgres -c "CREATE DATABASE carwash;"
	$(DC) run --rm db-roles
	$(DC) exec backend alembic upgrade head

roles: ## Crea/re-afirma carwash_owner y carwash_app en la base local (idempotente)
	$(DC) run --rm db-roles

seed-staging: ## Seed sintético (ADR-0012) contra la base LOCAL. Requiere SEED_STAGING_PASSWORD
	@test -n "$$SEED_STAGING_PASSWORD" || { echo 'Falta SEED_STAGING_PASSWORD (no hay default)'; exit 1; }
	$(DC) exec -e APP_ENV=staging -e SEED_STAGING_PASSWORD backend python scripts/seed_staging.py

# ── Tests ─────────────────────────────────────────────────────────────────────
test: ## Suite rápida, paralela, sin cobertura (SQLite)
	$(BACKEND) $(UV) pytest

test-fast: test ## Alias de test

test-cov: ## A3 + A4: suite con cobertura (el piso sale de pyproject.toml)
	$(BACKEND) $(UV) pytest --cov=app --cov-report=term-missing
	$(FRONTEND) npm run test:cov

test-watch: ## Re-corre la suite al guardar
	$(BACKEND) $(UV) --with watchfiles watchfiles "pytest" app

test-file: ## Un archivo: make test-file FILE=app/tests/api/test_auth_cookies.py
	@test -n "$(FILE)" || { echo 'Falta FILE=...'; exit 1; }
	$(BACKEND) $(UV) pytest $(FILE) -v

test-frontend: ## Jest del frontend, sin cobertura
	$(FRONTEND) npm test

test-pg: ## A5 + A6 + A7 + A8: roles, migraciones y tests `postgres` contra Postgres real, secuencial
	$(DC) up -d --wait postgres
	$(DC) exec postgres psql -U carwash -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$(PG_TEST_DB)'" | grep -q 1 \
		|| $(DC) exec postgres psql -U carwash -d postgres -c "CREATE DATABASE $(PG_TEST_DB);"
	$(DC) run --rm -e ADMIN_DATABASE_URL=postgresql://carwash:carwash@postgres:5432/$(PG_TEST_DB) db-roles
	@# Migra con el rol DUEÑO, igual que el preDeployCommand de Railway.
	$(BACKEND) unset DATABASE_URL; export APP_ENV=test DATABASE_URL_SYNC="$(PG_TEST_OWNER_URL)" && \
		$(UV) python scripts/migrate_preflight.py && \
		$(UV) alembic upgrade head && \
		$(UV) alembic check && \
		$(UV) alembic downgrade base && \
		$(UV) alembic upgrade head
	@# -n 0: todos los workers compartirían el MISMO Postgres; ver ci-backend.yml.
	@# CI=true: con Postgres inalcanzable el backend falla en vez de saltear.
	$(BACKEND) CI=true PG_TEST_URL="$(PG_TEST_URL)" $(UV) pytest app/tests -m postgres --no-cov -v -n 0

# ── Formato, lint y tipos (A1 + A2) ───────────────────────────────────────────
format: ## Formatea backend (ruff) y frontend (prettier)
	$(BACKEND) $(UV) ruff format . && $(UV) ruff check --fix .
	$(FRONTEND) npx prettier --write .

format-check: ## Verifica formato sin tocar nada (lo que exige el CI)
	$(BACKEND) $(UV) ruff format --check .
	$(FRONTEND) npx prettier --check .

lint: ## ruff check + eslint
	$(BACKEND) $(UV) ruff check .
	$(FRONTEND) npm run lint

fix: ## Autofix de lint (ruff --fix)
	$(BACKEND) $(UV) ruff check --fix .

typecheck: ## mypy strict + tsc
	$(BACKEND) $(UV) mypy app
	$(FRONTEND) npm run type-check

precommit: ## pre-commit run --all-files
	uvx pre-commit run --all-files

check: format-check lint typecheck precommit ## A1 + A2: formato, lint, tipos y pre-commit

# ── Contrato OpenAPI (ADR-0013) ───────────────────────────────────────────────
openapi: ## Regenera backend/openapi.json
	$(BACKEND) $(UV) python -m app.cli.dump_openapi

gen-api: ## Regenera frontend/src/types/api.generated.ts desde openapi.json
	$(FRONTEND) npm run gen:api

openapi-check: openapi gen-api ## A10: falla si el contrato o los tipos commiteados están vencidos
	git diff --exit-code backend/openapi.json frontend/src/types/api.generated.ts
	$(FRONTEND) npm run build

# ── Ambientes (ADR-0012) ──────────────────────────────────────────────────────
check-envs: ## A11: staging y prod existen, se saben distintos y no comparten base ni secreto
	sh scripts/check_envs.sh

# ── Build y utilidades ────────────────────────────────────────────────────────
build: ## Construye las imágenes de docker compose
	$(DC) build

clean: ## Borra cachés de Python y de tests
	find backend -type d -name __pycache__ -not -path '*/.venv/*' -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/htmlcov backend/.coverage backend/.mypy_cache backend/.ruff_cache

install: ## Instala dependencias de backend (uv) y frontend (npm)
	$(BACKEND) uv sync --frozen
	$(FRONTEND) npm ci

setup: install ## Primer setup: deps, .env locales y hooks de pre-commit
	cp -n .env.example .env || true
	cp -n backend/.env.example backend/.env 2>/dev/null || true
	uvx pre-commit install
	@echo "Listo. 'make dev' levanta el stack local."
