"""Persistence owned by the lifecycle module; receipts are append-only."""
from __future__ import annotations

import json
from typing import Any

from .models import DesignChange, LifecycleReceipt


SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS design_changes (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  base_canonical_revision TEXT NOT NULL,
  state TEXT NOT NULL,
  version INTEGER NOT NULL,
  aggregate_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_design_changes_repo
  ON design_changes(repo_id, created_at);

CREATE TABLE IF NOT EXISTS design_revisions (
  id TEXT PRIMARY KEY,
  change_id TEXT NOT NULL REFERENCES design_changes(id),
  revision_json TEXT NOT NULL,
  digest TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS design_lifecycle_receipts (
  id TEXT PRIMARY KEY,
  change_id TEXT NOT NULL REFERENCES design_changes(id),
  sequence INTEGER NOT NULL,
  command TEXT NOT NULL,
  actor TEXT NOT NULL,
  state_before TEXT,
  state_after TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  digest TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(change_id, sequence)
);

CREATE TABLE IF NOT EXISTS design_coverage_certificates (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  canonical_revision TEXT NOT NULL,
  subject TEXT NOT NULL,
  relation_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_design_coverage_lookup
  ON design_coverage_certificates(repo_id, canonical_revision, subject, relation_kind, created_at);
CREATE TRIGGER IF NOT EXISTS design_coverage_no_update
BEFORE UPDATE ON design_coverage_certificates
BEGIN SELECT RAISE(ABORT, 'coverage certificates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS design_coverage_no_delete
BEFORE DELETE ON design_coverage_certificates
BEGIN SELECT RAISE(ABORT, 'coverage certificates are immutable'); END;

CREATE TABLE IF NOT EXISTS design_test_receipts (
  id TEXT PRIMARY KEY, change_id TEXT NOT NULL REFERENCES design_changes(id),
  payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS design_approval_receipts (
  id TEXT PRIMARY KEY, change_id TEXT NOT NULL REFERENCES design_changes(id),
  payload_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS design_test_no_update
BEFORE UPDATE ON design_test_receipts
BEGIN SELECT RAISE(ABORT, 'test receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS design_test_no_delete
BEFORE DELETE ON design_test_receipts
BEGIN SELECT RAISE(ABORT, 'test receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS design_approval_no_update
BEFORE UPDATE ON design_approval_receipts
BEGIN SELECT RAISE(ABORT, 'approval receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS design_approval_no_delete
BEFORE DELETE ON design_approval_receipts
BEGIN SELECT RAISE(ABORT, 'approval receipts are immutable'); END;

CREATE TRIGGER IF NOT EXISTS design_receipts_no_update
BEFORE UPDATE ON design_lifecycle_receipts
BEGIN SELECT RAISE(ABORT, 'design lifecycle receipts are immutable'); END;

CREATE TRIGGER IF NOT EXISTS design_receipts_no_delete
BEFORE DELETE ON design_lifecycle_receipts
BEGIN SELECT RAISE(ABORT, 'design lifecycle receipts are immutable'); END;

CREATE TRIGGER IF NOT EXISTS design_revisions_no_update
BEFORE UPDATE ON design_revisions
BEGIN SELECT RAISE(ABORT, 'design revisions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS design_revisions_no_delete
BEFORE DELETE ON design_revisions
BEGIN SELECT RAISE(ABORT, 'design revisions are immutable'); END;
"""


class LifecycleRepository:
    """Narrow repository used only by :class:`DesignLifecycleService`.

    DESIGN-LIFECYCLE0 starts with the local SQLite substrate used by the FAC
    witnesses. PostgreSQL support is fail-closed rather than silently storing
    lifecycle state outside the configured Rintel database.
    """

    def __init__(self, store: Any):
        self.store = store
        self.conn = getattr(store, "conn", None)
        if self.conn is None:
            raise RuntimeError("design lifecycle persistence requires a SQL store")
        self.sqlite = self.conn.__class__.__module__ == "sqlite3"
        if self.sqlite:
            self.conn.executescript(SQLITE_DDL)

    def insert(self, change: DesignChange, receipt: LifecycleReceipt) -> None:
        raw = json.dumps(change.to_dict(), sort_keys=True, separators=(",", ":"))
        if not self.sqlite:
            from psycopg.types.json import Jsonb
            with self.conn.transaction():
                self.conn.execute(
                    "INSERT INTO design_changes(id, repo_id, base_canonical_revision,"
                    " state, version, aggregate_json, created_at, updated_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (change.id, change.repo_id, change.base_canonical_revision,
                     change.state.value, change.version, Jsonb(change.to_dict()),
                     change.created_at, change.updated_at))
                self._insert_revision(change)
                self._append_receipt(receipt)
            return
        try:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "INSERT INTO design_changes(id, repo_id, base_canonical_revision,"
                " state, version, aggregate_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (change.id, change.repo_id, change.base_canonical_revision,
                 change.state.value, change.version, raw,
                 change.created_at, change.updated_at),
            )
            self._insert_revision(change)
            self._append_receipt(receipt)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def get(self, change_id: str) -> DesignChange | None:
        if not self.sqlite:
            row = self.conn.execute(
                "SELECT aggregate_json FROM design_changes WHERE id=%s",
                (change_id,)).fetchone()
            return DesignChange.from_dict(row["aggregate_json"]) if row else None
        row = self.conn.execute(
            "SELECT aggregate_json FROM design_changes WHERE id=?", (change_id,)
        ).fetchone()
        return DesignChange.from_dict(json.loads(row["aggregate_json"])) if row else None

    def list(self, repo_id: str | None = None) -> list[DesignChange]:
        if not self.sqlite:
            if repo_id is None:
                rows = self.conn.execute(
                    "SELECT aggregate_json FROM design_changes"
                    " ORDER BY created_at, id").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT aggregate_json FROM design_changes WHERE repo_id=%s"
                    " ORDER BY created_at, id", (repo_id,)).fetchall()
            return [DesignChange.from_dict(r["aggregate_json"]) for r in rows]
        if repo_id is None:
            rows = self.conn.execute(
                "SELECT aggregate_json FROM design_changes ORDER BY created_at, id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT aggregate_json FROM design_changes WHERE repo_id=?"
                " ORDER BY created_at, id", (repo_id,),
            ).fetchall()
        return [DesignChange.from_dict(json.loads(r["aggregate_json"])) for r in rows]

    def update(self, previous_version: int, change: DesignChange,
               receipt: LifecycleReceipt) -> None:
        raw = json.dumps(change.to_dict(), sort_keys=True, separators=(",", ":"))
        if not self.sqlite:
            from psycopg.types.json import Jsonb
            with self.conn.transaction():
                updated = self.conn.execute(
                    "UPDATE design_changes SET state=%s, version=%s,"
                    " aggregate_json=%s, updated_at=%s WHERE id=%s AND version=%s",
                    (change.state.value, change.version, Jsonb(change.to_dict()),
                     change.updated_at, change.id, previous_version)).rowcount
                if updated != 1:
                    raise RuntimeError("concurrent design change update")
                current = self.conn.execute(
                    "SELECT 1 FROM design_revisions WHERE id=%s",
                    (change.design_revision.id,)).fetchone()
                if current is None:
                    self._insert_revision(change)
                self._append_receipt(receipt)
            return
        try:
            self.conn.execute("BEGIN")
            updated = self.conn.execute(
                "UPDATE design_changes SET state=?, version=?, aggregate_json=?,"
                " updated_at=? WHERE id=? AND version=?",
                (change.state.value, change.version, raw, change.updated_at,
                 change.id, previous_version),
            ).rowcount
            if updated != 1:
                raise RuntimeError("concurrent design change update")
            current = self.conn.execute(
                "SELECT 1 FROM design_revisions WHERE id=?",
                (change.design_revision.id,),
            ).fetchone()
            if current is None:
                self._insert_revision(change)
            self._append_receipt(receipt)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _append_receipt(self, receipt: LifecycleReceipt) -> None:
        if not self.sqlite:
            from psycopg.types.json import Jsonb
            self.conn.execute(
                "INSERT INTO design_lifecycle_receipts(id, change_id, sequence,"
                " command, actor, state_before, state_after, payload_json, digest,"
                " created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (receipt.id, receipt.change_id, receipt.sequence, receipt.command,
                 receipt.actor, receipt.state_before, receipt.state_after,
                 Jsonb(receipt.payload), receipt.digest, receipt.created_at))
            return
        self.conn.execute(
            "INSERT INTO design_lifecycle_receipts(id, change_id, sequence,"
            " command, actor, state_before, state_after, payload_json, digest,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (receipt.id, receipt.change_id, receipt.sequence, receipt.command,
             receipt.actor, receipt.state_before, receipt.state_after,
             json.dumps(receipt.payload, sort_keys=True), receipt.digest,
             receipt.created_at),
        )

    def _insert_revision(self, change: DesignChange) -> None:
        revision = change.to_dict()["design_revision"]
        if not self.sqlite:
            from psycopg.types.json import Jsonb
            self.conn.execute(
                "INSERT INTO design_revisions(id, change_id, revision_json, digest,"
                " created_at) VALUES (%s,%s,%s,%s,%s)",
                (change.design_revision.id, change.id, Jsonb(revision),
                 change.design_revision.digest, change.design_revision.created_at))
            return
        self.conn.execute(
            "INSERT INTO design_revisions(id, change_id, revision_json, digest,"
            " created_at) VALUES (?,?,?,?,?)",
            (change.design_revision.id, change.id,
             json.dumps(revision, sort_keys=True), change.design_revision.digest,
             change.design_revision.created_at),
        )

    def receipts(self, change_id: str) -> list[LifecycleReceipt]:
        if not self.sqlite:
            rows = self.conn.execute(
                "SELECT * FROM design_lifecycle_receipts WHERE change_id=%s"
                " ORDER BY sequence", (change_id,)).fetchall()
            return [LifecycleReceipt(
                id=r["id"], change_id=r["change_id"], sequence=r["sequence"],
                command=r["command"], actor=r["actor"],
                state_before=r["state_before"], state_after=r["state_after"],
                payload=r["payload_json"], digest=r["digest"],
                created_at=r["created_at"],
            ) for r in rows]
        rows = self.conn.execute(
            "SELECT * FROM design_lifecycle_receipts WHERE change_id=?"
            " ORDER BY sequence", (change_id,),
        ).fetchall()
        return [LifecycleReceipt(
            id=r["id"], change_id=r["change_id"], sequence=r["sequence"],
            command=r["command"], actor=r["actor"],
            state_before=r["state_before"], state_after=r["state_after"],
            payload=json.loads(r["payload_json"]), digest=r["digest"],
            created_at=r["created_at"],
        ) for r in rows]

    def revision(self, change_id: str, revision_id: str) -> dict[str, Any] | None:
        """Read an immutable design revision; never substitute current/latest."""
        if self.sqlite:
            row = self.conn.execute(
                "SELECT revision_json FROM design_revisions WHERE change_id=? AND id=?",
                (change_id, revision_id)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT revision_json FROM design_revisions WHERE change_id=%s AND id=%s",
                (change_id, revision_id)).fetchone()
        if row is None:
            return None
        raw = row["revision_json"]
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    def record_coverage_certificate(
        self, *, repo_id: str, revision: str, subject: str,
        relation_kind: str, build_context: str, provider: str,
        provider_config_digest: str,
        rule_version: str, excluded_domains: list[str],
        completeness: str, receipt_id: str,
    ) -> dict[str, Any]:
        """Legacy PARTIAL/UNKNOWN receipt only; not an absence authority seam."""
        import uuid
        if completeness not in {"COMPLETE", "PARTIAL", "UNKNOWN"}:
            raise ValueError("invalid coverage completeness")
        if completeness == "COMPLETE":
            raise ValueError("caller cannot issue COMPLETE; production CoverageIssuer required")
        snap = self.store.snapshot(revision)
        if not snap or snap.get("repo_id") != repo_id or snap.get(
                "publication_status") != "published":
            raise ValueError("certificate revision must be published in repo")
        meta = snap.get("meta_json", snap.get("meta", {}))
        meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        identity = meta.get("analysis_identity") or {}
        if (identity.get("build_context_id") != build_context or
                identity.get("provider_id") != provider or
                identity.get("provider_config_digest") != provider_config_digest):
            raise ValueError("certificate analysis identity mismatch")
        if not all((subject, relation_kind, rule_version, receipt_id,
                    provider_config_digest)):
            raise ValueError("coverage binding incomplete")
        if meta.get("run_id") != receipt_id:
            raise ValueError("coverage receipt does not identify publication run")
        previous = self.coverage_certificates(
            repo_id, revision, subject, relation_kind)
        if previous:
            raise ValueError("coverage domain already has an immutable certificate")
        created_at = max(self.store.now(),
                         previous[0]["created_at"] + 1 if previous else 0)
        certificate = {
            "id": f"coverage-{uuid.uuid4().hex}", "repo_id": repo_id,
            "canonical_revision": revision, "subject": subject,
            "relation_kind": relation_kind, "provider": provider,
            "build_context": build_context,
            "provider_config_digest": provider_config_digest,
            "rule_version": rule_version,
            "excluded_domains": list(excluded_domains),
            "completeness": completeness, "receipt_id": receipt_id,
            "created_at": created_at,
        }
        if self.sqlite:
            self.conn.execute(
                "INSERT INTO design_coverage_certificates VALUES (?,?,?,?,?,?,?)",
                (certificate["id"], repo_id, revision, subject, relation_kind,
                 json.dumps(certificate, sort_keys=True), certificate["created_at"]))
        else:
            from psycopg.types.json import Jsonb
            self.conn.execute(
                "INSERT INTO design_coverage_certificates"
                " (id, repo_id, canonical_revision, subject, relation_kind,"
                " payload_json, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (certificate["id"], repo_id, revision, subject, relation_kind,
                 Jsonb(certificate), certificate["created_at"]))
        self.conn.commit()
        return certificate

    def _record_issued_coverage_certificate(self, certificate: Any) -> dict[str, Any]:
        """Internal persistence of a sealed, real-analyzer certificate."""
        from rintel.coverage_authority.model import CoverageCertificate
        if not isinstance(certificate, CoverageCertificate):
            raise TypeError("production coverage requires sealed issuer receipt")
        value = certificate.to_dict()
        repo_id = value["repo_id"]
        revision = value["canonical_revision"]
        subject = value["subject"]
        relation = value["relation_kind"]
        snap = self.store.snapshot(revision)
        if not snap or snap.get("repo_id") != repo_id or snap.get(
                "publication_status") != "published":
            raise ValueError("issued certificate requires published revision")
        meta = snap.get("meta_json", snap.get("meta", {}))
        meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        if meta.get("run_id") != value.get("analyzer_run_id"):
            raise ValueError("issued certificate run does not match publication")
        support_ids = set(value.get("support_receipt_ids", []))
        supports = self.store.support_receipts(repo_id, revision, subject)
        if not any(item.get("support_receipt_id") in support_ids
                   and item.get("lane_id") == "clang_provider"
                   and item.get("provenance", {}).get("analysis_id") ==
                   value.get("analyzer_run_id") for item in supports):
            raise ValueError("issued certificate lacks analyzer support linkage")
        if self.coverage_certificates(repo_id, revision, subject, relation):
            raise ValueError("coverage domain already has an immutable certificate")
        if self.sqlite:
            self.conn.execute(
                "INSERT INTO design_coverage_certificates VALUES (?,?,?,?,?,?,?)",
                (value["id"], repo_id, revision, subject, relation,
                 json.dumps(value, sort_keys=True), value["created_at"]))
        else:
            from psycopg.types.json import Jsonb
            self.conn.execute(
                "INSERT INTO design_coverage_certificates"
                " (id, repo_id, canonical_revision, subject, relation_kind,"
                " payload_json, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (value["id"], repo_id, revision, subject, relation,
                 Jsonb(value), value["created_at"]))
        self.conn.commit()
        return value

    def coverage_certificates(self, repo_id: str, revision: str,
                              subject: str, relation_kind: str) -> list[dict[str, Any]]:
        marker = "?" if self.sqlite else "%s"
        rows = self.conn.execute(
            "SELECT payload_json FROM design_coverage_certificates WHERE"
            f" repo_id={marker} AND canonical_revision={marker} AND subject={marker}"
            f" AND relation_kind={marker} ORDER BY created_at DESC, id DESC",
            (repo_id, revision, subject, relation_kind)).fetchall()
        return [json.loads(row["payload_json"])
                if isinstance(row["payload_json"], str)
                else dict(row["payload_json"]) for row in rows]

    def append_independent_receipt(self, kind: str, receipt: dict[str, Any]) -> None:
        if kind not in {"test", "approval"}:
            raise ValueError("invalid independent receipt kind")
        table = f"design_{kind}_receipts"
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        import hashlib
        digest = hashlib.sha256(raw.encode()).hexdigest()
        marker = "?" if self.sqlite else "%s"
        if self.sqlite:
            payload = raw
        else:
            from psycopg.types.json import Jsonb
            payload = Jsonb(receipt)
        self.conn.execute(
            f"INSERT INTO {table} (id, change_id, payload_json, digest, created_at)"
            f" VALUES ({','.join([marker] * 5)})",
            (receipt["id"], receipt["change_id"], payload, digest,
             receipt["timestamp"]))
        self.conn.commit()

    def independent_receipts(self, kind: str, change_id: str) -> list[dict[str, Any]]:
        if kind not in {"test", "approval"}:
            raise ValueError("invalid independent receipt kind")
        marker = "?" if self.sqlite else "%s"
        rows = self.conn.execute(
            f"SELECT payload_json FROM design_{kind}_receipts"
            f" WHERE change_id={marker} ORDER BY created_at, id",
            (change_id,)).fetchall()
        return [json.loads(r["payload_json"])
                if isinstance(r["payload_json"], str) else dict(r["payload_json"])
                for r in rows]
