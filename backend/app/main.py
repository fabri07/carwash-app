"""
carwash.app API — factory de la aplicación FastAPI.

Entry point: uvicorn app.main:app

Adaptado de Véktor (`app/main.py`). Se conservan `SlowAPIMiddleware`, CORS,
`security_headers`, `request_logger`, los handlers de validación, el catch-all a
Sentry, `/health` y `/ready`. Se sacan `TenantMiddleware` (su trabajo lo hace el
`SET LOCAL` de `persistence/db/session.py`) y los handlers de dominio de Véktor.
Se agregan `/health` con `{status, env, commit, db_fingerprint}` (ADR-0012), la
comprobación de `Origin` en métodos mutadores (ADR-0009) y la forma única de
error `ErrorResponse` (ADR-0013).
"""

import hashlib
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import sentry_sdk
import structlog.contextvars
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.rate_limit import limiter
from app.bootstrap import shutdown, startup
from app.config.settings import get_settings
from app.domain.errors import ErrorCode
from app.observability.logger import get_logger
from app.observability.sentry import init_sentry
from app.observability.trace import set_trace_id
from app.schemas.common import ErrorResponse

logger = get_logger(__name__)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_STATUS_TO_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    # Nunca DUPLICATE_IDEMPOTENT por defecto: `useOfflineSubmit` lo trata como éxito.
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}


class HealthResponse(BaseModel):
    status: str
    env: str
    commit: str
    #: Huella de la base configurada (host/puerto/base, sin credenciales). Staging y
    #: prod tienen que dar distinto (ADR-0012). No conecta: `/health` no depende de la DB.
    db_fingerprint: str


class ReadyCheck(BaseModel):
    ok: bool
    error: str | None = None


class ReadyResponse(BaseModel):
    status: str
    env: str
    checks: dict[str, ReadyCheck]


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _valid_id(value: str | None) -> str | None:
    return value if value is not None and _SAFE_ID_RE.fullmatch(value) else None


def _safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Para el log: solo dónde y qué tipo de error. Nunca `input`, `ctx` ni `msg`."""
    return [{"loc": list(e.get("loc", ())), "type": e.get("type")} for e in errors]


def _public_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": e.get("type")}
        for e in errors
    ]


def _error_body(code: ErrorCode, message: str) -> dict[str, Any]:
    return ErrorResponse.model_validate({"detail": {"code": code, "message": message}}).model_dump(
        mode="json", exclude_none=True
    )


def db_fingerprint(database_url: str) -> str:
    parts = urlsplit(database_url)
    target = (
        f"{parts.scheme.split('+')[0]}://{parts.hostname}:{parts.port}/{parts.path.lstrip('/')}"
    )
    return hashlib.sha256(target.encode()).hexdigest()[:16]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("carwash.startup", environment=get_settings().APP_ENV)
    await startup()
    yield
    await shutdown()
    logger.info("carwash.shutdown")


def create_app() -> FastAPI:
    """Factory — instancia y configura FastAPI. Lee la config en cada llamada."""
    settings = get_settings()
    init_sentry("web")

    app = FastAPI(
        title="carwash.app API",
        version="0.1.0",
        description="SaaS multi-tenant para lavaderos. Fase 2: infraestructura.",
        # ADR-0013: apagados en producción; el contrato vive en `backend/openapi.json`.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    # ── Rate limiter ──────────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    # ── CSRF: Origin en métodos mutadores (ADR-0009) ─────────────────────────
    allowed_origins = frozenset(settings.CORS_ORIGINS)

    @app.middleware("http")
    async def origin_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # La cookie viaja sola: un POST desde otro sitio lleva la credencial. El
        # `Origin` no lo puede falsificar un navegador. Sin `Origin` (curl, server a
        # server) no hay navegador que pueda estar siendo usado de intermediario.
        origin = request.headers.get("origin")
        if request.method in _MUTATING_METHODS and origin and origin not in allowed_origins:
            logger.warning("csrf.origin_rejected", path=request.url.path)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=_error_body(ErrorCode.ORIGIN_NOT_ALLOWED, "Origin not allowed."),
            )
        return await call_next(request)

    # ── CORS ─────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Accept",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Trace-Id",
            "sentry-trace",
            "baggage",
        ],
        expose_headers=["X-Request-ID", "X-Trace-Id"],
    )

    # ── Security headers (los de la API; los de la app van en next.config.ts) ─
    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # ── Request logging + trace id ───────────────────────────────────────────
    @app.middleware("http")
    async def request_logger(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # L8: ids del cliente solo si tienen forma segura; si no, uno nuevo. Viajan a
        # logs, a Sentry y de vuelta en la respuesta.
        request_id = _valid_id(request.headers.get("X-Request-ID")) or str(uuid.uuid4())
        trace_id = _valid_id(request.headers.get("X-Trace-Id")) or request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            environment=settings.APP_ENV,
            request_id=request_id,
            trace_id=trace_id,
            method=request.method,
            endpoint=request.url.path,
        )
        set_trace_id(trace_id)
        sentry_sdk.set_tag("trace_id", trace_id)

        t0 = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-Id"] = trace_id
        logger.info(
            "http.request",
            status_code=response.status_code,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        return response

    # ── Routers ───────────────────────────────────────────────────────────────
    from app.api.v1.router import api_router  # noqa: PLC0415

    app.include_router(api_router, prefix="/v1")

    if not settings.is_production:
        from app.api.v1.debug import router as debug_router  # noqa: PLC0415

        app.include_router(debug_router, prefix="/v1/debug", include_in_schema=False)

    # ── Exception handlers ────────────────────────────────────────────────────
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            content: dict[str, Any] = {"detail": detail}
        else:
            code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
            content = _error_body(code, str(detail))
        return JSONResponse(
            status_code=exc.status_code, content=content, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=_error_body(ErrorCode.RATE_LIMITED, "Too many requests."),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # H2: se loguea SOLO `loc` y `type`. `input` y `ctx` traen lo que mandó el
        # cliente — una contraseña tipeada en el campo del email, por ejemplo.
        logger.warning(
            "request.body_validation_error",
            path=str(request.url.path),
            errors=_safe_errors(exc.errors()),
        )
        # Tampoco se devuelve `input`/`ctx`: no se refleja lo que mandó el cliente, y
        # `ctx.error` trae la excepción cruda (que además rompía la serialización).
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": _public_errors(exc.errors())},
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
        logger.warning(
            "request.validation_error",
            path=str(request.url.path),
            errors=_safe_errors(exc.errors()),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": _public_errors(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "request.unhandled_exception",
            path=str(request.url.path),
            exc_type=type(exc).__name__,
        )
        # Este handler INTERCEPTA la excepción, así que la integración de FastAPI
        # nunca la ve: sin esta línea, Sentry queda activo y con cero eventos.
        sentry_sdk.capture_exception(exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(ErrorCode.INTERNAL_ERROR, "Internal Server Error"),
        )

    # ── Health / readiness ────────────────────────────────────────────────────
    @app.get("/health", tags=["Infra"], response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        return HealthResponse(
            status="ok",
            env=settings.APP_ENV,
            commit=settings.GIT_COMMIT,
            db_fingerprint=db_fingerprint(settings.DATABASE_URL),
        )

    @app.get(
        "/ready",
        tags=["Infra"],
        response_model=ReadyResponse,
        responses={503: {"model": ReadyResponse}},
    )
    async def readiness_check() -> JSONResponse:
        checks = {"database": await _check_database_ready(), "redis": await _check_redis_ready()}
        ready = all(check.ok for check in checks.values())
        body = ReadyResponse(
            status="ready" if ready else "degraded", env=settings.APP_ENV, checks=checks
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=body.model_dump(mode="json"),
        )

    return app


async def _check_database_ready() -> ReadyCheck:
    try:
        from sqlalchemy import text  # noqa: PLC0415

        from app.persistence.db.engine import engine  # noqa: PLC0415

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return ReadyCheck(ok=True)
    except Exception as exc:
        return ReadyCheck(ok=False, error=type(exc).__name__)


async def _check_redis_ready() -> ReadyCheck:
    try:
        from app.persistence.db.redis import get_redis_pool  # noqa: PLC0415

        await get_redis_pool().ping()
        return ReadyCheck(ok=True)
    except Exception as exc:
        return ReadyCheck(ok=False, error=type(exc).__name__)


app = create_app()
