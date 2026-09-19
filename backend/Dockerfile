# syntax=docker/dockerfile:1
#
# Portado de Véktor (backend/Dockerfile). Se conserva el multi-stage
# builder/dev/runtime, el usuario no-root, el healthcheck sobre /health y el CMD
# por scripts/start.sh. Se cambia pip → uv (con uv.lock), y se saca tesseract-ocr.
#
# Python 3.12 PINEADO (ADR-0007, punto 5): tiene que coincidir con
# .python-version, requires-python y el workflow. El Python del sistema de la
# máquina de desarrollo es 3.14 y rompe dependencias.

# ── uv pineado: misma versión en todas las stages ────────────────────────────
FROM ghcr.io/astral-sh/uv:0.11.19 AS uv

# ── Stage 1: builder — dependencias de producción en un venv ─────────────────
FROM python:3.12-slim AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# Solo los manifiestos primero: la capa de dependencias se cachea mientras el
# lock no cambie. `--frozen` falla si uv.lock no corresponde a pyproject.toml.
# `--no-install-project`: la app se usa desde /app vía PYTHONPATH, no instalada.
#
# Sin `RUN --mount=type=cache`: Railway rechaza el build si el cache mount no
# lleva `id=s/<service-id>-<ruta>`, y el service-id es distinto en staging y en
# producción, así que no hay un id que sirva para los dos. El primer deploy de
# staging (merge del PR #1) falló por esto a los 7 segundos, sin compilar nada.
# UV_NO_CACHE evita que el caché de uv quede dentro de la capa.
COPY pyproject.toml uv.lock ./
RUN UV_NO_CACHE=1 uv sync --frozen --no-dev --no-install-project

# ── Stage 2: dev / test — incluye el grupo dev (pytest, ruff, mypy) ──────────
FROM python:3.12-slim AS dev

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN UV_NO_CACHE=1 uv sync --frozen --no-install-project

COPY . .

# ── Stage 3: runtime — lo que corre en Railway ───────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# APP_ENV NO tiene default en la imagen, a propósito: lo declara cada ambiente
# (Railway / compose). Una imagen que "supone" un ambiente es cómo staging termina
# creyéndose producción, o al revés.

# Non-root user
RUN addgroup --gid 1001 appgroup && \
    adduser --uid 1001 --gid 1001 --disabled-password --gecos "" appuser

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

CMD ["sh", "scripts/start.sh"]
