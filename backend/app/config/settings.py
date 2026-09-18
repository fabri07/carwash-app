"""Configuración de la app — `pydantic-settings`, `get_settings()` cacheado.

Reescrito desde Véktor (`app/config/settings.py`, 575 líneas de flags de rollout).
Se conserva la forma: `BaseSettings`, `get_settings()` con `lru_cache` y la
property `is_production`, de la que depende `deps.client_ip`.
"""

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]

#: Secreto de desarrollo. Fuera de `local` está prohibido (M1).
DEFAULT_JWT_SECRET = "dev-only-change-me-dev-only-change-me"
MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: AppEnv = "local"
    DEBUG: bool = False
    #: SHA del commit desplegado; Railway lo inyecta como RAILWAY_GIT_COMMIT_SHA.
    GIT_COMMIT: str = Field(default="unknown", validation_alias="RAILWAY_GIT_COMMIT_SHA")

    DATABASE_URL: str = "postgresql+asyncpg://carwash_app:carwash_app_test@localhost:5432/carwash"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = DEFAULT_JWT_SECRET
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    #: 7 días (M7). Sin lista de `jti` revocados: la revocación es `token_version`
    #: (logout global). El refresh por dispositivo se difiere a F8.
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    #: Dominio de las cookies (ADR-0009). Vacío = host-only (local).
    COOKIE_DOMAIN: str | None = None
    #: Prefijo de los nombres de cookie (`<prefijo>access_token`). Contrato compartido
    #: con `frontend` y `deploy`: permite separar staging de prod bajo el mismo dominio.
    COOKIE_NAME_PREFIX: str = ""

    #: Orígenes permitidos para CORS y para la comprobación de `Origin` (ADR-0009).
    #: CSV (`a,b`) o JSON (`["a","b"]`). `NoDecode`: el validador decide el formato.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            if value.strip().startswith("["):
                return json.loads(value)
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @model_validator(mode="after")
    def _secreto_jwt_fuera_de_local(self) -> "Settings":
        """M1: fuera de `local`, el secreto no puede ser el default ni corto."""
        if self.APP_ENV != "local" and (
            self.JWT_SECRET_KEY == DEFAULT_JWT_SECRET
            or len(self.JWT_SECRET_KEY) < MIN_JWT_SECRET_LENGTH
        ):
            raise ValueError(
                f"JWT_SECRET_KEY inválido para APP_ENV={self.APP_ENV}: debe tener al menos "
                f"{MIN_JWT_SECRET_LENGTH} caracteres y no ser el valor de desarrollo."
            )
        return self

    @property
    def jwt_issuer(self) -> str:
        """M2: el emisor y la audiencia dependen del ambiente. Un token de staging no
        valida en producción aunque alguien reutilice el secreto."""
        return f"carwash-api:{self.APP_ENV}"

    @property
    def jwt_audience(self) -> str:
        return f"carwash-app:{self.APP_ENV}"

    @property
    def uses_shared_rate_limit_storage(self) -> bool:
        """Fuera de local/test el limiter guarda en Redis: con varias réplicas, la
        memoria de proceso haría el límite N veces más laxo (M6)."""
        return self.APP_ENV in ("staging", "production")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def cookie_secure(self) -> bool:
        """`Secure` en todo ambiente salvo `local` (en http://localhost no se setea)."""
        return self.APP_ENV != "local"

    @property
    def async_database_url(self) -> str:
        """`DATABASE_URL` con driver async. Railway entrega `postgresql://`."""
        url = self.DATABASE_URL
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix) :]
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
