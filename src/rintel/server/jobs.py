"""Index background jobs (SPEC-P1 §5 / §6.2: worker thread + jobs table).

- JobManager: in-memory registry (single process), thread-safe; each
  transition is mirrored to the PG `jobs` table through an optional
  persister (best-effort; the registry is the truth for the process).
- SQLite backend has no jobs table (SPEC §6.2 jobs is a PG-table) →
  memory-only is the S2 contract there.
- Per-repo serialization of the actual index work is guaranteed by
  `Store.begin(repo_id)` → `pg_advisory_xact_lock` (SPEC-P1 §6.1-5).
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import sqlite3
import threading
import time
import traceback
import uuid
from typing import Callable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..indexer import Indexer
from ..store import Store


def _now_ms() -> int:
    return int(time.time() * 1000)


class JobManager:
    """Thread-safe registry of index jobs (SPEC-P1 §7-2 / §21-7)."""

    def __init__(self, persist: Callable[[dict], None] | None = None,
                 load: Callable[[str], dict | None] | None = None):
        self._persist = persist
        self._load = load
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- registry ------------------------------------------------------
    def create(self, kind: str, repo_id: str) -> dict:
        job = {"id": str(uuid.uuid4()), "kind": kind, "repo_id": repo_id,
               "status": "pending", "progress": 0, "result": None,
               "error": None, "created_at": _now_ms(), "finished_at": None}
        with self._lock:
            self._jobs[job["id"]] = job
        self._emit(job)
        return job

    def get(self, job_id: str) -> Optional[dict]:
        # Durable storage is authoritative where configured; a best-effort
        # in-memory transition must never become adoption proof.
        if self._load:
            return self._load(job_id)
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def list(self, repo_id: str | None = None,
             status: str | None = None) -> list[dict]:
        with self._lock:
            out = [dict(j) for j in self._jobs.values()]
        if repo_id:
            out = [j for j in out if j["repo_id"] == repo_id]
        if status:
            out = [j for j in out if j["status"] == status]
        out.sort(key=lambda j: j["created_at"], reverse=True)
        return out

    # -- lifecycle -----------------------------------------------------
    def enqueue(self, job: dict,
                runner: Callable[[dict, Callable[[int, str], None]], dict]) -> None:
        """Run `runner(job, tick)` in a daemon thread (SPEC §5 to_thread)."""
        t = threading.Thread(target=self._run, args=(job, runner),
                             daemon=True, name=f"job-{job['id'][:8]}")
        t.start()

    def _run(self, job: dict,
             runner: Callable[[dict, Callable[[int, str], None]], dict]) -> None:
        self._update(job["id"], status="running", progress=1, phase="start")

        def tick(pct: int, phase: str) -> None:
            self._update(job["id"], progress=pct, phase=phase)

        try:
            result = runner(job, tick)
            self._update(job["id"], status="done", progress=100,
                         result=result)
        except Exception as exc:  # noqa: BLE001 - a job failure must not
            # kill the process; the repo keeps its last good snapshot
            self._update(job["id"], status="failed", error={
                "code": type(exc).__name__, "message": str(exc),
                "traceback": traceback.format_exc(limit=5)})

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return
            j.update(fields)
            if fields.get("status") in ("done", "failed"):
                j["finished_at"] = _now_ms()
            snap = dict(j)
        # An index transaction can hold the SQLite writer lock. Persisting
        # progress from inside its tick callback would block the indexer;
        # terminal state is persisted after the runner commits/closes.
        if "status" in fields:
            self._emit(snap)

    def _emit(self, job: dict) -> None:
        if self._persist:
            try:
                self._persist(job)
            except Exception:  # noqa: BLE001 - persistence is best-effort
                pass


def make_index_runner(store_factory: Callable[[], Store], repo_id: str,
                      force: bool = False):
    """Build the job runner closure for `POST /repos/{id}/index`."""
    def runner(job: dict, tick) -> dict:
        store = store_factory()
        try:
            root = store.repo(repo_id)["root_path"]
            idx = Indexer(store, root, repo_id=repo_id, force=force)
            res = idx.index(on_progress=tick)
            return dataclasses.asdict(res)
        finally:
            store.close()
    return runner


class PgJobsPersister:
    """Best-effort mirror of job rows into the PG `jobs` table (§6.2)."""

    def __init__(self, database_url: str, schema: str | None = None):
        self._url = database_url
        self._schema = schema

    def persist(self, job: dict) -> None:
        conn = psycopg.connect(self._url, autocommit=True)
        try:
            if self._schema:
                conn.execute(f'SET search_path TO "{self._schema}"')
            conn.execute(
                "INSERT INTO jobs (id, kind, repo_id, status, progress,"
                " result, error, created_at, finished_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (id) DO UPDATE SET"
                " status=EXCLUDED.status, progress=EXCLUDED.progress,"
                " result=EXCLUDED.result, error=EXCLUDED.error,"
                " finished_at=EXCLUDED.finished_at",
                (job["id"], job["kind"], job["repo_id"], job["status"],
                 job["progress"],
                 Jsonb(job["result"]) if job["result"] is not None else None,
                 Jsonb(job["error"]) if job["error"] is not None else None,
                 job["created_at"], job["finished_at"]))
        finally:
            conn.close()

    def get(self, job_id: str) -> dict | None:
        conn = psycopg.connect(self._url, autocommit=True,
                               row_factory=dict_row)
        try:
            if self._schema:
                conn.execute(f'SET search_path TO "{self._schema}"')
            row = conn.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


class SqliteJobsPersister:
    """Project-local durable mirror for the existing generic index jobs."""

    _ddl = """CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, repo_id TEXT NOT NULL,
      status TEXT NOT NULL, progress INTEGER NOT NULL, phase TEXT,
      result TEXT, error TEXT, created_at INTEGER NOT NULL,
      finished_at INTEGER
    )"""

    def __init__(self, path: str | Path):
        self.path = str(path)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute(self._ddl)
        return conn

    def persist(self, job: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, kind, repo_id, status, progress, phase,"
                " result, error, created_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET status=excluded.status,"
                " progress=excluded.progress, phase=excluded.phase,"
                " result=excluded.result, error=excluded.error,"
                " finished_at=excluded.finished_at",
                (job["id"], job["kind"], job["repo_id"], job["status"],
                 job["progress"], job.get("phase"), json.dumps(job.get("result")),
                 json.dumps(job.get("error")), job["created_at"],
                 job.get("finished_at")))

    def get(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            for field in ("result", "error"):
                result[field] = json.loads(result[field]) if result[field] else None
            return result


class StoreJobsPersister:
    """Select the existing backing store at request time, including test overrides."""

    def __init__(self, store_factory):
        self.store_factory = store_factory
        self._sqlite_adapter = None

    def _adapter(self):
        if self._sqlite_adapter is not None:
            return self._sqlite_adapter
        store = self.store_factory()
        if hasattr(store, "path"):
            path = store.path
            store.close()
            self._sqlite_adapter = SqliteJobsPersister(path)
            return self._sqlite_adapter
        store.close()
        return None

    def persist(self, job: dict) -> None:
        adapter = self._adapter()
        if adapter:
            adapter.persist(job)
        else:
            raise RuntimeError("PostgreSQL jobs require PgJobsPersister")

    def get(self, job_id: str) -> dict | None:
        adapter = self._adapter()
        return adapter.get(job_id) if adapter else None
