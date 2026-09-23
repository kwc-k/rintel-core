"""FastAPI dependencies: per-request Store + repo/snapshot resolution.

One store per request (short-lived connections; PG read transactions are
`read committed`, SPEC-P1 §6.1-5).  Index jobs build their own store via the
same factory, so the API threadpool never shares a psycopg connection.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from ..db import Database
from ..pg.pgstore import PgStore
from ..store import Store
from .errors import (ApiError, repo_not_found, repo_not_indexed,
                     snapshot_not_found)
from .settings import get_settings


def build_store() -> Store:
    s = get_settings()
    if s.database_url:
        return PgStore(s.database_url, schema=s.pg_schema)
    return Database(s.sqlite_path, read_only=s.e2e_read_only)


def get_store(request: Request) -> Iterator[Store]:
    """Dependency: a Store for the lifetime of one request."""
    factory = getattr(request.app.state, "store_factory", build_store)
    store = factory()
    try:
        yield store
    finally:
        store.close()


def require_repo(store: Store, repo_id: str) -> dict:
    r = store.repo(repo_id)
    if not r:
        raise repo_not_found(repo_id)
    return r


def resolve_snapshot(store: Store, repo_id: str,
                     snapshot: str | None) -> str:
    """`?snapshot=` (default: latest) → snapshot id (SPEC-P1 §7)."""
    sid = snapshot or store.current_snapshot(repo_id)
    if sid is None:
        raise repo_not_indexed(repo_id)
    if snapshot is not None and not store.snapshot(sid):
        raise snapshot_not_found(snapshot)
    return sid


def bad_request(code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(400, code, message, details)
