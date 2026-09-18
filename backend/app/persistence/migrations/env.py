"""Entorno de Alembic — modo online (sync) y offline.

Adaptado de Véktor: se conservan `resolve_sync_url` y `compare_type=True`; se
agrega `compare_server_default=True`. La `naming_convention` viaja con
`Base.metadata`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Cargar todos los modelos para que el autogenerate los vea.
import app.persistence.models  # noqa: F401
from app.persistence.db.alembic_url import resolve_sync_url
from app.persistence.db.base import Base

config = context.config

# La resolución vive en `app.persistence.db.alembic_url` porque el preflight de
# `scripts/migrate.sh` tiene que resolver EXACTAMENTE lo mismo.
config.set_main_option(
    "sqlalchemy.url",
    resolve_sync_url(config.get_main_option("sqlalchemy.url")).url,
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
