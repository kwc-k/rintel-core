"""Alembic environment (sync, psycopg dialect) — SPEC-P1 §16-5.

The baseline revision executes the frozen `pgschema.DDL_STATEMENTS`; later
schema changes must be new revisions, never edits to released ones.
"""
from __future__ import annotations

import os

from alembic import context

config = context.config


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        url = os.environ.get("RINTEL_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "no database url: set RINTEL_DATABASE_URL or sqlalchemy.url")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(_url().replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
