"""SQLite evidence-graph store: WAL + FTS5, snapshots, incremental updates.

Implements the `Store` protocol (SPEC-P1 §4) so the indexer / harness / CLI
run identically on SQLite (this class) and PostgreSQL (`pg.PgStore`).

Design (spec §2, §26, §40):
- one canonical graph; snapshots are complete per-snapshot copies of the
  node/edge rows, so `Graph(commit A) vs Graph(commit B)` diffs are plain
  set differences;
- incremental indexing only *reparses changed files*; unchanged rows are
  copied cheaply into the new snapshot;
- resolution-derived edges (CALLS / IMPORTS / INCLUDES / REFERENCES) are
  recomputed per snapshot from stored facts, so cross-file identities never
  go stale;
- FTS5 external-content table keeps node identity searchable.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .archmodel import build_baseline
from .model import (Callsite, EdgeSpec, ImportBinding, IncludeBinding, Node,
                    Ref)
from .schema import ARCH_DDL, DDL, FLOW_DDL
from .store import ArchError, FlowError
from .evidence_authority.support import CanonicalSupportReceipt, _reuse_receipt

RESOLUTION_EDGE_KINDS = ("CALLS", "IMPORTS", "INCLUDES", "REFERENCES")
SCHEMA_VERSION = 1


class Database:
    """SQLite implementation of the `Store` protocol (P0, unchanged behavior)."""

    def __init__(self, path: str | Path, *, read_only: bool = False):
        self.path = str(path)
        # FastAPI may resume a yielded sync dependency on another worker thread.
        # Requests never share a Database instance, but its own request can move.
        self.conn = (sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True,
                                     check_same_thread=False)
                     if read_only else sqlite3.connect(self.path,
                                                      check_same_thread=False))
        self.conn.row_factory = sqlite3.Row
        if read_only:
            self.conn.execute("PRAGMA foreign_keys=ON")
            return
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")  # arch-plane FKs (R1/R5)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"SQLite schema {version} is newer than this Rintel release")
            self._apply_ddl(DDL)
            # Additive migration for databases created before INDEX-INCREMENTAL0.
            file_columns = {r["name"] for r in self.conn.execute(
                "PRAGMA table_info(files)").fetchall()}
            if "semantic_digest" not in file_columns:
                self.conn.execute("ALTER TABLE files ADD COLUMN semantic_digest TEXT")
            snapshot_columns = {r["name"] for r in self.conn.execute(
                "PRAGMA table_info(snapshots)").fetchall()}
            if "publication_status" not in snapshot_columns:
                self.conn.execute(
                    "ALTER TABLE snapshots ADD COLUMN publication_status TEXT "
                    "NOT NULL DEFAULT 'published'")
            self._apply_ddl(ARCH_DDL)
            self._apply_ddl(FLOW_DDL)
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            self.conn.close()
            raise

    def _apply_ddl(self, script: str) -> None:
        """Execute schema statements within the caller's migration transaction."""
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                if pending.strip():
                    self.conn.execute(pending)
                pending = ""
        if pending.strip():
            raise sqlite3.OperationalError("incomplete SQLite schema statement")

    # ------------------------------------------------------------------
    def begin(self, repo_id: str | None = None) -> None:
        """No-op for SQLite (implicit per-statement transactions)."""

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.commit()
        self.conn.close()

    def now(self) -> int:
        return int(time.time() * 1000)

    # -- repos / snapshots ---------------------------------------------
    def upsert_repo(self, repo_id: str, root_path: str) -> None:
        self.conn.execute(
            "INSERT INTO repos(id, root_path, created_at) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET root_path=excluded.root_path",
            (repo_id, root_path, self.now()))

    def repos(self) -> list[dict]:
        """All repositories (server query surface, SPEC-P1 §7-1)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM repos ORDER BY created_at").fetchall()]

    def repo(self, repo_id: str) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM repos WHERE id=?",
                              (repo_id,)).fetchone()
        return dict(r) if r else None

    def new_snapshot(self, repo_id: str, parent_id: str | None,
                     commit: str | None = None,
                     meta: dict | None = None) -> str:
        sid = f"s-{uuid.uuid4().hex[:12]}"
        prior = self.conn.execute(
            "SELECT MAX(created_at) FROM snapshots WHERE repo_id=?",
            (repo_id,)).fetchone()[0]
        created_at = max(self.now(), int(prior or 0) + 1)
        self.conn.execute(
            "INSERT INTO snapshots(id, repo_id, parent_id, commit_sha,"
            " publication_status, created_at, meta_json) VALUES (?,?,?,?,?,?,?)",
            (sid, repo_id, parent_id, commit, "staging", created_at,
             json.dumps(meta or {})))
        return sid

    def snapshots(self, repo_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM snapshots WHERE repo_id=? ORDER BY created_at",
            (repo_id,)).fetchall()
        return [dict(r) for r in rows]

    def snapshot(self, sid: str) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM snapshots WHERE id=?",
                              (sid,)).fetchone()
        return dict(r) if r else None

    def current_snapshot(self, repo_id: str) -> Optional[str]:
        r = self.conn.execute(
            "SELECT id FROM snapshots WHERE repo_id=?"
            " AND publication_status='published' ORDER BY created_at DESC, rowid DESC "
            "LIMIT 1", (repo_id,)).fetchone()
        return r["id"] if r else None

    def publish_snapshot(self, repo_id: str, snapshot_id: str) -> None:
        updated = self.conn.execute(
            "UPDATE snapshots SET publication_status='published' "
            "WHERE repo_id=? AND id=? AND publication_status='staging'",
            (repo_id, snapshot_id)).rowcount
        if updated != 1:
            raise RuntimeError(f"snapshot not publishable: {snapshot_id}")

    # -- files ----------------------------------------------------------
    def upsert_file(self, repo_id: str, path: str, hash_: str, size: int,
                    parser_version: str, indexer_version: str,
                    status: str = "ok",
                    semantic_digest: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO files(repo_id, path, hash, size, parser_version,"
            " indexer_version, semantic_digest, status, last_indexed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(repo_id, path) DO UPDATE SET hash=excluded.hash,"
            " size=excluded.size, parser_version=excluded.parser_version,"
            " indexer_version=excluded.indexer_version,"
            " semantic_digest=excluded.semantic_digest, status=excluded.status,"
            " last_indexed_at=excluded.last_indexed_at",
            (repo_id, path, hash_, size, parser_version, indexer_version,
             semantic_digest, status, self.now()))

    def file_state(self, repo_id: str, path: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM files WHERE repo_id=? AND path=?",
            (repo_id, path)).fetchone()
        return dict(r) if r else None

    def files(self, repo_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM files WHERE repo_id=? ORDER BY path",
            (repo_id,)).fetchall()]

    # -- nodes ----------------------------------------------------------
    def upsert_node(self, node: Node, repo_id: str,
                    snapshot_id: str) -> tuple[str, bool]:
        """Insert a definition node.  Same (kind, qname) merges into one
        canonical node (cross-file identity, e.g. C decl+def); locations are
        unioned in meta.locations.  Returns (node_id, created)."""
        row = self.conn.execute(
            "SELECT id, path, meta_json FROM nodes WHERE repo_id=? AND snapshot_id=?"
            " AND kind=? AND qname=?",
            (repo_id, snapshot_id, node.kind, node.qname)).fetchone()
        loc = {"path": node.path, "start_line": node.start_line,
               "start_col": node.start_col, "end_line": node.end_line,
               "end_col": node.end_col}
        if row:
            meta = json.loads(row["meta_json"])
            locs = meta.get("locations", [])
            if row["path"] == node.path:
                locs = [old for old in locs if old.get("path") != node.path]
            if loc not in locs:
                locs.append(loc)
            meta["locations"] = locs
            meta = {**node.meta, **meta}
            if row["path"] == node.path:
                self.conn.execute(
                    "UPDATE nodes SET name=?, language=?, path=?, start_line=?,"
                    " start_col=?, end_line=?, end_col=?, meta_json=? WHERE"
                    " repo_id=? AND snapshot_id=? AND id=?",
                    (node.name, node.language, node.path, node.start_line,
                     node.start_col, node.end_line, node.end_col, json.dumps(meta),
                     repo_id, snapshot_id, row["id"]))
            else:
                self.conn.execute(
                    "UPDATE nodes SET meta_json=? WHERE repo_id=? AND"
                    " snapshot_id=? AND id=?",
                    (json.dumps(meta), repo_id, snapshot_id, row["id"]))
            return row["id"], False
        nid = node.canonical_id
        k = 2
        while self.conn.execute(
                "SELECT 1 FROM nodes WHERE repo_id=? AND snapshot_id=? AND id=?",
                (repo_id, snapshot_id, nid)).fetchone():
            nid = f"{node.canonical_id}#{k}"
            k += 1
        meta = dict(node.meta)
        meta.setdefault("locations", []).append(loc)
        self.conn.execute(
            "INSERT INTO nodes(id, repo_id, snapshot_id, kind, name, qname,"
            " language, path, start_line, start_col, end_line, end_col,"
            " meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, repo_id, snapshot_id, node.kind, node.name, node.qname,
             node.language, node.path, node.start_line, node.start_col,
             node.end_line, node.end_col, json.dumps(meta)))
        return nid, True

    def node_by_qname(self, repo_id: str, snapshot_id: str,
                      qname: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM nodes WHERE repo_id=? AND snapshot_id=? AND qname=?",
            (repo_id, snapshot_id, qname)).fetchone()
        return dict(r) if r else None

    def node_by_id(self, repo_id: str, snapshot_id: str,
                   nid: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM nodes WHERE repo_id=? AND snapshot_id=? AND id=?",
            (repo_id, snapshot_id, nid)).fetchone()
        return dict(r) if r else None

    def all_nodes(self, repo_id: str, snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM nodes WHERE repo_id=? AND snapshot_id=?",
            (repo_id, snapshot_id)).fetchall()]

    def nodes_by_path(self, repo_id: str, snapshot_id: str,
                      path: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM nodes WHERE repo_id=? AND snapshot_id=? AND path=?",
            (repo_id, snapshot_id, path)).fetchall()]

    def nodes_by_path_prefix(self, repo_id: str, snapshot_id: str,
                             path_prefix: str) -> list[dict]:
        """All nodes under `path_prefix` (the dir itself + every descendant).

        Mirrors PG `path = prefix OR path LIKE prefix || '/%'` semantics
        (case-sensitive; `src/foo` never matches `src/foobar/x.py`).
        SQLite uses substr() instead of LIKE because LIKE is
        ASCII-case-insensitive by default and would break PG parity.
        """
        prefix = path_prefix.rstrip("/")
        marker = prefix + "/"
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM nodes WHERE repo_id=? AND snapshot_id=? AND"
            " (path = ? OR substr(path, 1, ?) = ?)",
            (repo_id, snapshot_id, prefix, len(marker), marker)).fetchall()]

    def delete_nodes_in_paths(self, repo_id: str, snapshot_id: str,
                              paths: Iterable[str]) -> int:
        if not paths:
            return 0
        marks = ",".join("?" * len(list(paths)))
        return self.conn.execute(
            f"DELETE FROM nodes WHERE repo_id=? AND snapshot_id=? AND path IN "
            f"({marks})", (repo_id, snapshot_id, *paths)).rowcount

    def delete_nodes_in_paths_except(self, repo_id: str, snapshot_id: str,
                                     paths: Iterable[str],
                                     keep_ids: Iterable[str]) -> int:
        paths, keep_ids = list(paths), list(keep_ids)
        if not paths:
            return 0
        path_marks = ",".join("?" * len(paths))
        sql = (f"DELETE FROM nodes WHERE repo_id=? AND snapshot_id=? AND "
               f"path IN ({path_marks})")
        args: list = [repo_id, snapshot_id, *paths]
        if keep_ids:
            keep_marks = ",".join("?" * len(keep_ids))
            sql += f" AND id NOT IN ({keep_marks})"
            args.extend(keep_ids)
        return self.conn.execute(sql, args).rowcount

    # -- edges ----------------------------------------------------------
    def add_edge(self, edge: EdgeSpec, src_id: str, dst_id: str,
                 repo_id: str, snapshot_id: str,
                 evidence: dict | None = None) -> bool:
        eid = f"edge:{edge.kind}:{src_id}:{dst_id}"
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO edges(id, repo_id, snapshot_id, kind,"
            " src_id, dst_id, confidence, meta_json) VALUES (?,?,?,?,?,?,?,?)",
            (eid, repo_id, snapshot_id, edge.kind, src_id, dst_id,
             edge.confidence, json.dumps(edge.meta)))
        if cur.rowcount == 0:
            return False
        if evidence:
            self.add_evidence(repo_id, snapshot_id, "edge", eid, **evidence)
        return True

    def add_edge_raw(self, kind: str, src_id: str, dst_id: str,
                     repo_id: str, snapshot_id: str, confidence: float = 1.0,
                     meta: dict | None = None,
                     evidence: dict | None = None) -> bool:
        eid = f"edge:{kind}:{src_id}:{dst_id}"
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO edges(id, repo_id, snapshot_id, kind,"
            " src_id, dst_id, confidence, meta_json) VALUES (?,?,?,?,?,?,?,?)",
            (eid, repo_id, snapshot_id, kind, src_id, dst_id, confidence,
             json.dumps(meta or {})))
        if cur.rowcount == 0:
            return False
        if evidence:
            self.add_evidence(repo_id, snapshot_id, "edge", eid, **evidence)
        return True

    def edges_for_node(self, repo_id: str, snapshot_id: str, nid: str,
                       direction: str = "both",
                       relation: str | None = None) -> list[dict]:
        if direction == "out":
            sql = ("SELECT e.*, n.kind AS other_kind, n.name AS other_name,"
                   " n.qname AS other_qname, n.path AS other_path FROM edges e"
                   " JOIN nodes n ON n.id=e.dst_id AND n.snapshot_id=?"
                   " WHERE e.repo_id=? AND e.snapshot_id=? AND e.src_id=?")
            args: list = [snapshot_id, repo_id, snapshot_id, nid]
        elif direction == "in":
            sql = ("SELECT e.*, n.kind AS other_kind, n.name AS other_name,"
                   " n.qname AS other_qname, n.path AS other_path FROM edges e"
                   " JOIN nodes n ON n.id=e.src_id AND n.snapshot_id=?"
                   " WHERE e.repo_id=? AND e.snapshot_id=? AND e.dst_id=?")
            args = [snapshot_id, repo_id, snapshot_id, nid]
        else:
            sql = ("SELECT e.*, CASE WHEN e.src_id=? THEN (SELECT kind FROM"
                   " nodes WHERE id=e.dst_id AND snapshot_id=?) ELSE (SELECT"
                   " kind FROM nodes WHERE id=e.src_id AND snapshot_id=?) END"
                   " AS other_kind FROM edges e WHERE e.repo_id=? AND"
                   " e.snapshot_id=? AND (e.src_id=? OR e.dst_id=?)")
            args = [nid, snapshot_id, snapshot_id, repo_id, snapshot_id,
                    nid, nid]
        if relation:
            sql += " AND e.kind=?"
            args.append(relation)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def delete_edges_in_snapshot(self, repo_id: str, snapshot_id: str,
                                 kinds: Iterable[str]) -> int:
        marks = ",".join("?" * len(list(kinds)))
        return self.conn.execute(
            f"DELETE FROM edges WHERE repo_id=? AND snapshot_id=? AND kind IN"
            f" ({marks})", (repo_id, snapshot_id, *kinds)).rowcount

    def delete_edges_touching_paths(self, repo_id: str, snapshot_id: str,
                                    paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        n = self.conn.execute(
            f"DELETE FROM edges WHERE repo_id=? AND snapshot_id=? AND"
            f" (src_id IN (SELECT id FROM nodes WHERE repo_id=? AND"
            f" snapshot_id=? AND path IN ({marks})) OR dst_id IN (SELECT id"
            f" FROM nodes WHERE repo_id=? AND snapshot_id=? AND path IN"
            f" ({marks})))",
            (repo_id, snapshot_id, repo_id, snapshot_id, *paths, repo_id,
             snapshot_id, *paths)).rowcount
        return n

    def delete_edges_from_paths(self, repo_id: str, snapshot_id: str,
                                paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM edges WHERE repo_id=? AND snapshot_id=? AND "
            f"src_id IN (SELECT id FROM nodes WHERE repo_id=? AND "
            f"snapshot_id=? AND path IN ({marks}))",
            (repo_id, snapshot_id, repo_id, snapshot_id, *paths)).rowcount

    # -- evidence -------------------------------------------------------
    def add_evidence(self, repo_id: str, snapshot_id: str, entity_type: str,
                     entity_id: str, source: str, confidence: float,
                     location: dict | None = None,
                     payload: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO evidence(repo_id, snapshot_id, entity_type,"
            " entity_id, source, confidence, location_json, ts, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (repo_id, snapshot_id, entity_type, entity_id, source, confidence,
             json.dumps(location) if location else None, self.now(),
             json.dumps(payload or {})))

    def evidence_for(self, repo_id: str, snapshot_id: str,
                     entity_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM evidence WHERE repo_id=? AND snapshot_id=? AND"
            " entity_id=?", (repo_id, snapshot_id, entity_id)).fetchall()]

    def delete_evidence_in_paths(self, repo_id: str, snapshot_id: str,
                                 paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM evidence WHERE repo_id=? AND snapshot_id=? AND"
            f" (entity_type='node' AND entity_id IN (SELECT id FROM nodes"
            f" WHERE repo_id=? AND snapshot_id=? AND path IN ({marks})) OR"
            f" entity_type='edge' AND entity_id IN (SELECT id FROM edges"
            f" WHERE repo_id=? AND snapshot_id=? AND (src_id IN (SELECT id"
            f" FROM nodes WHERE repo_id=? AND snapshot_id=? AND path IN"
            f" ({marks})) OR dst_id IN (SELECT id FROM nodes WHERE repo_id=?"
            f" AND snapshot_id=? AND path IN ({marks})))))",
            (repo_id, snapshot_id, repo_id, snapshot_id, *paths, repo_id,
             snapshot_id, repo_id, snapshot_id, *paths, repo_id, snapshot_id,
             *paths)).rowcount

    def delete_evidence_owned_by_paths(self, repo_id: str, snapshot_id: str,
                                       paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM evidence WHERE repo_id=? AND snapshot_id=? AND "
            f"((entity_type='node' AND entity_id IN (SELECT id FROM nodes "
            f"WHERE repo_id=? AND snapshot_id=? AND path IN ({marks}))) OR "
            f"(entity_type='edge' AND entity_id IN (SELECT id FROM edges "
            f"WHERE repo_id=? AND snapshot_id=? AND src_id IN (SELECT id "
            f"FROM nodes WHERE repo_id=? AND snapshot_id=? AND path IN "
            f"({marks})))))",
            (repo_id, snapshot_id, repo_id, snapshot_id, *paths,
             repo_id, snapshot_id, repo_id, snapshot_id, *paths)).rowcount

    # -- callsites / imports / includes ---------------------------------
    def add_callsite(self, cs: Callsite, repo_id: str,
                     snapshot_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO callsites(repo_id, snapshot_id, file_path, line, col,"
            " callee, candidates_json, shape) VALUES (?,?,?,?,?,?,?,?)",
            (repo_id, snapshot_id, cs.path, cs.line, cs.col, cs.callee,
             json.dumps(cs.candidates), json.dumps(cs.shape) if cs.shape
             else None))
        return cur.lastrowid

    def all_callsites(self, repo_id: str, snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM callsites WHERE repo_id=? AND snapshot_id=?"
            " ORDER BY file_path,line,col,id",
            (repo_id, snapshot_id)).fetchall()]

    def callsites_in_paths(self, repo_id: str, snapshot_id: str,
                           paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        marks = ",".join("?" * len(paths))
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM callsites WHERE repo_id=? AND snapshot_id=? "
            f"AND file_path IN ({marks}) ORDER BY file_path,line,col,id",
            (repo_id, snapshot_id, *paths)
        ).fetchall()]

    def update_callsite(self, cid: int, resolved_node_id: str | None,
                        resolve_kind: str | None, confidence: float | None,
                        edge_id: str | None) -> None:
        self.conn.execute(
            "UPDATE callsites SET resolved_node_id=?, resolve_kind=?,"
            " confidence=?, edge_id=? WHERE id=?",
            (resolved_node_id, resolve_kind, confidence, edge_id, cid))

    def callsite_row(self, cid: int) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT file_path, line, callee, shape FROM callsites WHERE id=?",
            (cid,)).fetchone()
        return dict(r) if r else None

    def delete_callsites_in_paths(self, repo_id: str, snapshot_id: str,
                                  paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM callsites WHERE repo_id=? AND snapshot_id=? AND"
            f" file_path IN ({marks})",
            (repo_id, snapshot_id, *paths)).rowcount

    def add_import_row(self, imp: ImportBinding, src_qname: str,
                       repo_id: str, snapshot_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO imports(repo_id, snapshot_id, file_path, line,"
            " src_qname, module_qname, local, only_names_json, meta_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (repo_id, snapshot_id, imp.path, imp.line, src_qname,
             imp.module_qname, imp.local,
             json.dumps(imp.only_names or []),
             json.dumps(imp.meta)))
        return cur.lastrowid

    def all_imports(self, repo_id: str, snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM imports WHERE repo_id=? AND snapshot_id=?"
            " ORDER BY file_path,line,id",
            (repo_id, snapshot_id)).fetchall()]

    def imports_in_paths(self, repo_id: str, snapshot_id: str,
                         paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        marks = ",".join("?" * len(paths))
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM imports WHERE repo_id=? AND snapshot_id=? "
            f"AND file_path IN ({marks}) ORDER BY file_path,line,id",
            (repo_id, snapshot_id, *paths)
        ).fetchall()]

    def delete_imports_in_paths(self, repo_id: str, snapshot_id: str,
                                paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM imports WHERE repo_id=? AND snapshot_id=? AND"
            f" file_path IN ({marks})",
            (repo_id, snapshot_id, *paths)).rowcount

    def set_import_external(self, row_id: int) -> None:
        self.conn.execute(
            "UPDATE imports SET external=1 WHERE id=?", (row_id,))

    def add_include_row(self, inc: IncludeBinding, repo_id: str,
                        snapshot_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO includes(repo_id, snapshot_id, file_path, line,"
            " target, meta_json) VALUES (?,?,?,?,?,?)",
            (repo_id, snapshot_id, inc.path, inc.line, inc.target,
             json.dumps(inc.meta)))
        return cur.lastrowid

    def all_includes(self, repo_id: str, snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM includes WHERE repo_id=? AND snapshot_id=?"
            " ORDER BY file_path,line,id",
            (repo_id, snapshot_id)).fetchall()]

    def includes_in_paths(self, repo_id: str, snapshot_id: str,
                          paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        marks = ",".join("?" * len(paths))
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM includes WHERE repo_id=? AND snapshot_id=? "
            f"AND file_path IN ({marks}) ORDER BY file_path,line,id",
            (repo_id, snapshot_id, *paths)
        ).fetchall()]

    def delete_includes_in_paths(self, repo_id: str, snapshot_id: str,
                                 paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM includes WHERE repo_id=? AND snapshot_id=? AND"
            f" file_path IN ({marks})",
            (repo_id, snapshot_id, *paths)).rowcount

    def set_include_external(self, row_id: int) -> None:
        self.conn.execute(
            "UPDATE includes SET external=1 WHERE id=?", (row_id,))

    # -- pending edges --------------------------------------------------
    def add_pending_edge(self, edge: EdgeSpec, file_path: str,
                         repo_id: str, snapshot_id: str,
                         meta: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO pending_edges(repo_id, snapshot_id, kind, file_path,"
            " line, src_mode, src_value, dst_mode, dst_value, confidence,"
            " meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (repo_id, snapshot_id, edge.kind, file_path, edge.line,
             edge.src.mode, edge.src.value, edge.dst.mode, edge.dst.value,
             edge.confidence, json.dumps(meta if meta is not None
                                         else edge.meta)))
        return cur.lastrowid

    def all_pending_edges(self, repo_id: str,
                          snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM pending_edges WHERE repo_id=? AND snapshot_id=?"
            " ORDER BY file_path,line,id",
            (repo_id, snapshot_id)).fetchall()]

    def resolution_input_count(self, repo_id: str, snapshot_id: str) -> int:
        row = self.conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM pending_edges WHERE repo_id=? AND snapshot_id=?) + "
            "(SELECT COUNT(*) FROM callsites WHERE repo_id=? AND snapshot_id=?) + "
            "(SELECT COUNT(*) FROM imports WHERE repo_id=? AND snapshot_id=?) + "
            "(SELECT COUNT(*) FROM includes WHERE repo_id=? AND snapshot_id=?) AS n",
            (repo_id, snapshot_id, repo_id, snapshot_id,
             repo_id, snapshot_id, repo_id, snapshot_id)).fetchone()
        return int(row["n"])

    def pending_edges_in_paths(self, repo_id: str, snapshot_id: str,
                               paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        marks = ",".join("?" * len(paths))
        return [dict(r) for r in self.conn.execute(
            f"SELECT * FROM pending_edges WHERE repo_id=? AND snapshot_id=? "
            f"AND file_path IN ({marks}) ORDER BY file_path,line,id",
            (repo_id, snapshot_id, *paths)
        ).fetchall()]

    def delete_pending_in_paths(self, repo_id: str, snapshot_id: str,
                                paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM pending_edges WHERE repo_id=? AND snapshot_id=? AND"
            f" file_path IN ({marks})",
            (repo_id, snapshot_id, *paths)).rowcount

    # -- snapshot copy / rebuild for incremental indexing ---------------
    def delete_edges_all(self, repo_id: str, snapshot_id: str) -> int:
        return self.conn.execute(
            "DELETE FROM edges WHERE repo_id=? AND snapshot_id=?",
            (repo_id, snapshot_id)).rowcount

    def all_edges(self, repo_id: str, snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM edges WHERE repo_id=? AND snapshot_id=?",
            (repo_id, snapshot_id)).fetchall()]

    def copy_nodes_edges(self, repo_id: str, parent_sid: str, sid: str) -> int:
        """Copy unchanged node/edge rows into a new snapshot (no reparse)."""
        n = self.conn.execute(
            "INSERT INTO nodes(repo_id, snapshot_id, id, kind, name, qname,"
            " language, path, start_line, start_col, end_line, end_col,"
            " meta_json) SELECT repo_id, ?, id, kind, name, qname, language,"
            " path, start_line, start_col, end_line, end_col, meta_json FROM"
            " nodes WHERE repo_id=? AND snapshot_id=?",
            (sid, repo_id, parent_sid)).rowcount
        e = self.conn.execute(
            "INSERT INTO edges(repo_id, snapshot_id, id, kind, src_id, dst_id,"
            " confidence, meta_json) SELECT repo_id, ?, id, kind, src_id,"
            " dst_id, confidence, meta_json FROM edges WHERE repo_id=? AND"
            " snapshot_id=?", (sid, repo_id, parent_sid)).rowcount
        return n + e

    def _require_staging_support(self, repo_id: str, sid: str) -> None:
        row = self.conn.execute(
            "SELECT publication_status FROM snapshots WHERE repo_id=? AND id=?",
            (repo_id, sid)).fetchone()
        if not row or row["publication_status"] != "staging":
            raise ValueError("canonical support may change only in staging revision")

    def add_support_receipt(self, repo_id: str, snapshot_id: str,
                            receipt: CanonicalSupportReceipt) -> None:
        if not isinstance(receipt, CanonicalSupportReceipt):
            raise TypeError("canonical support requires sealed admission receipt")
        self._require_staging_support(repo_id, snapshot_id)
        row = receipt.to_dict()
        if row["canonical_revision"] != snapshot_id:
            raise ValueError("canonical support revision mismatch")
        table = "nodes" if row["canonical_fact_type"] == "node" else "edges"
        fact = self.conn.execute(
            f"SELECT kind FROM {table} WHERE repo_id=? AND snapshot_id=? AND id=?",
            (repo_id, snapshot_id, row["canonical_fact_id"])).fetchone()
        if not fact or fact["kind"] != row["canonical_fact_kind"]:
            raise ValueError("canonical support fact not present in staging graph")
        self.conn.execute(
            "INSERT INTO canonical_support_receipts(repo_id, snapshot_id,"
            " support_receipt_id, canonical_fact_id, canonical_fact_type,"
            " owner_path, receipt_json) VALUES (?,?,?,?,?,?,?)",
            (repo_id, snapshot_id, row["support_receipt_id"],
             row["canonical_fact_id"], row["canonical_fact_type"],
             row["owner_path"], json.dumps(row, sort_keys=True)))

    def support_receipts(self, repo_id: str, snapshot_id: str,
                         canonical_fact_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT receipt_json FROM canonical_support_receipts WHERE repo_id=?"
            " AND snapshot_id=? AND canonical_fact_id=? ORDER BY support_receipt_id",
            (repo_id, snapshot_id, canonical_fact_id)).fetchall()
        return [json.loads(row["receipt_json"]) for row in rows]

    def support_receipts_owned_by_paths(self, repo_id: str, snapshot_id: str,
                                        paths: Iterable[str]) -> list[dict]:
        values = tuple(paths)
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        rows = self.conn.execute(
            "SELECT receipt_json FROM canonical_support_receipts WHERE repo_id=?"
            f" AND snapshot_id=? AND owner_path IN ({marks})",
            (repo_id, snapshot_id, *values)).fetchall()
        return [json.loads(row["receipt_json"]) for row in rows]

    def copy_support_receipts(self, repo_id: str, parent_sid: str,
                              sid: str) -> int:
        self._require_staging_support(repo_id, sid)
        parent = self.snapshot(parent_sid)
        if not parent or parent["publication_status"] != "published":
            raise ValueError("support reuse requires published parent")
        rows = self.conn.execute(
            "SELECT receipt_json FROM canonical_support_receipts WHERE repo_id=?"
            " AND snapshot_id=?", (repo_id, parent_sid)).fetchall()
        copies = [_reuse_receipt(json.loads(item["receipt_json"]), sid)
                  for item in rows]
        self.conn.executemany(
            "INSERT INTO canonical_support_receipts(repo_id, snapshot_id,"
            " support_receipt_id, canonical_fact_id, canonical_fact_type,"
            " owner_path, receipt_json) VALUES (?,?,?,?,?,?,?)",
            [(repo_id, sid, item["support_receipt_id"], item["canonical_fact_id"],
              item["canonical_fact_type"], item["owner_path"],
              json.dumps(item, sort_keys=True)) for item in copies])
        return len(copies)

    def delete_support_owned_by_paths(self, repo_id: str, snapshot_id: str,
                                      paths: Iterable[str]) -> int:
        values = tuple(paths)
        if not values:
            return 0
        self._require_staging_support(repo_id, snapshot_id)
        marks = ",".join("?" for _ in values)
        return self.conn.execute(
            "DELETE FROM canonical_support_receipts WHERE repo_id=?"
            f" AND snapshot_id=? AND owner_path IN ({marks})",
            (repo_id, snapshot_id, *values)).rowcount

    def prune_orphan_support(self, repo_id: str, snapshot_id: str) -> int:
        self._require_staging_support(repo_id, snapshot_id)
        return self.conn.execute(
            "DELETE FROM canonical_support_receipts WHERE repo_id=? AND snapshot_id=?"
            " AND ((canonical_fact_type='node' AND NOT EXISTS"
            " (SELECT 1 FROM nodes n WHERE n.repo_id=canonical_support_receipts.repo_id"
            " AND n.snapshot_id=canonical_support_receipts.snapshot_id"
            " AND n.id=canonical_support_receipts.canonical_fact_id))"
            " OR (canonical_fact_type='edge' AND NOT EXISTS"
            " (SELECT 1 FROM edges e WHERE e.repo_id=canonical_support_receipts.repo_id"
            " AND e.snapshot_id=canonical_support_receipts.snapshot_id"
            " AND e.id=canonical_support_receipts.canonical_fact_id)))",
            (repo_id, snapshot_id)).rowcount

    def unsupported_new_facts(self, repo_id: str, snapshot_id: str,
                              parent_sid: str | None) -> list[str]:
        supported = {row["canonical_fact_id"] for row in self.conn.execute(
            "SELECT canonical_fact_id FROM canonical_support_receipts"
            " WHERE repo_id=? AND snapshot_id=?", (repo_id, snapshot_id))}
        old = set()
        if parent_sid:
            old = {row["id"] for row in self.all_nodes(repo_id, parent_sid)}
            old.update(row["id"] for row in self.all_edges(repo_id, parent_sid))
        facts = {row["id"] for row in self.all_nodes(repo_id, snapshot_id)}
        facts.update(row["id"] for row in self.all_edges(repo_id, snapshot_id))
        return sorted(facts - supported - old)

    def delete_evidence_all(self, repo_id: str, snapshot_id: str) -> int:
        return self.conn.execute(
            "DELETE FROM evidence WHERE repo_id=? AND snapshot_id=?",
            (repo_id, snapshot_id)).rowcount

    def delete_node_evidence_all(self, repo_id: str, snapshot_id: str) -> int:
        return self.conn.execute(
            "DELETE FROM evidence WHERE repo_id=? AND snapshot_id=? AND"
            " entity_type='node'", (repo_id, snapshot_id)).rowcount

    def copy_callsites(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO callsites(repo_id, snapshot_id, file_path, line, col,"
            " callee, candidates_json, shape, resolved_node_id, resolve_kind,"
            " confidence, edge_id) SELECT repo_id, ?, file_path, line, col,"
            " callee, candidates_json, shape, resolved_node_id, resolve_kind,"
            " confidence, edge_id FROM callsites WHERE"
            " repo_id=? AND snapshot_id=?", (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_imports(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO imports(repo_id, snapshot_id, file_path, line,"
            " src_qname, module_qname, local, only_names_json, meta_json,"
            " external)"
            " SELECT repo_id, ?, file_path, line, src_qname, module_qname,"
            " local, only_names_json, meta_json, external FROM imports WHERE repo_id=?"
            " AND snapshot_id=?", (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_includes(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO includes(repo_id, snapshot_id, file_path, line,"
            " target, meta_json, external) SELECT repo_id, ?, file_path, line,"
            " target, meta_json, external FROM includes WHERE repo_id=?"
            " AND snapshot_id=?",
            (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_pending_edges(self, repo_id: str, src_sid: str,
                           dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO pending_edges(repo_id, snapshot_id, kind, file_path,"
            " line, src_mode, src_value, dst_mode, dst_value, confidence,"
            " meta_json) SELECT repo_id, ?, kind, file_path, line, src_mode,"
            " src_value, dst_mode, dst_value, confidence, meta_json FROM"
            " pending_edges WHERE repo_id=? AND snapshot_id=?",
            (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_evidence(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO evidence(repo_id, snapshot_id, entity_type, entity_id,"
            " source, confidence, location_json, ts, payload_json)"
            " SELECT repo_id, ?, entity_type, entity_id, source, confidence,"
            " location_json, ts, payload_json FROM evidence"
            " WHERE repo_id=? AND snapshot_id=?", (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_unresolved(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO unresolved(repo_id, snapshot_id, kind, detail_json, ts)"
            " SELECT repo_id, ?, kind, detail_json, ts FROM unresolved"
            " WHERE repo_id=? AND snapshot_id=?", (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def delete_unresolved_in_paths(self, repo_id: str, snapshot_id: str,
                                   paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        marks = ",".join("?" * len(paths))
        return self.conn.execute(
            f"DELETE FROM unresolved WHERE repo_id=? AND snapshot_id=? AND "
            f"COALESCE(json_extract(detail_json, '$.file'), "
            f"json_extract(detail_json, '$.path')) IN ({marks})",
            (repo_id, snapshot_id, *paths)).rowcount

    # -- unresolved notes -----------------------------------------------
    def add_unresolved(self, repo_id: str, snapshot_id: str, kind: str,
                       detail: dict) -> None:
        self.conn.execute(
            "INSERT INTO unresolved(repo_id, snapshot_id, kind, detail_json,"
            " ts) VALUES (?,?,?,?,?)",
            (repo_id, snapshot_id, kind, json.dumps(detail), self.now()))

    def unresolved_count(self, repo_id: str, snapshot_id: str) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS c FROM unresolved WHERE repo_id=? AND"
            " snapshot_id=?", (repo_id, snapshot_id)).fetchone()
        return r["c"]

    def unresolved_rows(self, repo_id: str,
                        snapshot_id: str) -> list[dict]:
        """All unresolved notes of a snapshot (kind + detail dict) — the
        FAC-EQ0 reason histogram input."""
        return [dict(r) for r in self.conn.execute(
            "SELECT kind, detail_json FROM unresolved WHERE repo_id=? AND"
            " snapshot_id=?", (repo_id, snapshot_id)).fetchall()]

    # -- telemetry ------------------------------------------------------
    def telemetry(self, run_id: str, repo_id: str | None, language: str | None,
                  phase: str, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO run_telemetry(run_id, repo_id, language, phase, key,"
            " value, ts) VALUES (?,?,?,?,?,?,?)",
            (run_id, repo_id, language, phase, key, json.dumps(value),
             self.now()))

    # -- search ---------------------------------------------------------
    def search(self, repo_id: str, query: str, limit: int = 50,
               snapshot_id: str | None = None) -> list[dict]:
        """Symbol search (exact → fts → substring).

        `snapshot_id` scopes the search to one snapshot (DEBT-SNAPSHOT-SEARCH
        fix): without it the search spans every snapshot of the repo and the
        latest snapshot's rows shadow older ones after dedup.
        """
        q = query.strip()
        scope = " AND snapshot_id=?" if snapshot_id else ""
        args: tuple = () if not snapshot_id else (snapshot_id,)
        out: list[dict] = []
        for col in ("qname", "name", "path"):
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE repo_id=?{scope} AND {col}=?"
                " LIMIT ?",
                (repo_id, *args, q, limit)).fetchall()
            for r in rows:
                d = dict(r)
                d["match"] = f"exact:{col}"
                out.append(d)
        if len(out) >= limit:
            return out[:limit]
        try:
            fts_q = '"' + q.replace('"', '""') + '"'
            rows = self.conn.execute(
                "SELECT n.* FROM nodes n JOIN nodes_fts f ON f.rowid=n.rowid"
                f" WHERE n.repo_id=?{scope} AND nodes_fts MATCH ?"
                " ORDER BY rank LIMIT ?",
                (repo_id, *args, fts_q, limit - len(out))).fetchall()
            for r in rows:
                d = dict(r)
                d["match"] = "fts"
                out.append(d)
        except sqlite3.OperationalError:
            rows = self.conn.execute(
                f"SELECT * FROM nodes WHERE repo_id=?{scope} AND (name LIKE ?"
                " OR qname LIKE ? OR path LIKE ?) LIMIT ?",
                (repo_id, *args, f"%{q}%", f"%{q}%", f"%{q}%",
                 limit - len(out)))
            for r in rows:
                d = dict(r)
                d["match"] = "like"
                out.append(d)
        seen = set()
        dedup = []
        for d in out:
            if d["id"] not in seen:
                seen.add(d["id"])
                dedup.append(d)
        return dedup[:limit]

    # -- projections / stats --------------------------------------------
    def neighbors(self, repo_id: str, snapshot_id: str, nid: str,
                  relation: str | None = None, depth: int = 1,
                  max_nodes: int = 300, direction: str = "both") -> dict:
        """BFS neighborhood with node budget (spec §20 hierarchical LOD)."""
        seen: dict[str, int] = {nid: 0}
        edges: list[dict] = []
        frontier = [nid]
        for d in range(1, depth + 1):
            if len(seen) >= max_nodes:
                break
            nxt: list[str] = []
            for cur in frontier:
                for e in self.edges_for_node(repo_id, snapshot_id, cur,
                                             direction, relation):
                    other = e["dst_id"] if e["src_id"] == cur else e["src_id"]
                    if other not in seen:
                        seen[other] = d
                        edges.append(e)
                        nxt.append(other)
                    if len(seen) >= max_nodes:
                        break
                if len(seen) >= max_nodes:
                    break
            frontier = nxt
        nodes = {}
        for oid, dist in seen.items():
            n = self.node_by_id(repo_id, snapshot_id, oid)
            if n:
                n = dict(n)
                n["_dist"] = dist
                nodes[oid] = n
        return {"nodes": nodes, "edges": edges}

    def stats(self, repo_id: str, snapshot_id: str | None = None) -> dict:
        sid = snapshot_id or self.current_snapshot(repo_id)
        st: dict[str, Any] = {"repo": repo_id, "snapshot": sid}
        if not sid:
            return st
        st["nodes"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM nodes WHERE repo_id=? AND snapshot_id=?",
            (repo_id, sid)).fetchone()["c"]
        st["edges"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE repo_id=? AND snapshot_id=?",
            (repo_id, sid)).fetchone()["c"]
        st["node_kinds"] = {r["kind"]: r["c"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) AS c FROM nodes WHERE repo_id=? AND"
            " snapshot_id=? GROUP BY kind ORDER BY c DESC",
            (repo_id, sid)).fetchall()}
        st["edge_kinds"] = {r["kind"]: r["c"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) AS c FROM edges WHERE repo_id=? AND"
            " snapshot_id=? GROUP BY kind ORDER BY c DESC",
            (repo_id, sid)).fetchall()}
        st["languages"] = {r["language"]: r["c"] for r in self.conn.execute(
            "SELECT language, COUNT(*) AS c FROM nodes WHERE repo_id=? AND"
            " snapshot_id=? GROUP BY language", (repo_id, sid)).fetchall()}
        st["files"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE repo_id=?",
            (repo_id,)).fetchone()["c"]
        st["callsites"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM callsites WHERE repo_id=? AND"
            " snapshot_id=?", (repo_id, sid)).fetchone()["c"]
        st["callsites_unresolved"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM callsites WHERE repo_id=? AND"
            " snapshot_id=? AND resolved_node_id IS NULL",
            (repo_id, sid)).fetchone()["c"]
        st["unresolved_notes"] = self.unresolved_count(repo_id, sid)
        return st

    def snapshot_diff(self, repo_id: str, s1: str, s2: str) -> dict:
        def ids(table: str, sid: str):
            return {r["id"] for r in self.conn.execute(
                f"SELECT id FROM {table} WHERE repo_id=? AND snapshot_id=?",
                (repo_id, sid)).fetchall()}
        n1, n2 = ids("nodes", s1), ids("nodes", s2)
        e1, e2 = ids("edges", s1), ids("edges", s2)
        added_nodes = n2 - n1
        removed_nodes = n1 - n2
        added_edges = e2 - e1
        removed_edges = e1 - e2
        changed_files = set()
        for nid in added_nodes | removed_nodes:
            for sid in (s1, s2):
                r = self.conn.execute(
                    "SELECT path FROM nodes WHERE id=? AND snapshot_id=?",
                    (nid, sid)).fetchone()
                if r:
                    changed_files.add(r["path"])
        return {
            "added_nodes": sorted(added_nodes),
            "removed_nodes": sorted(removed_nodes),
            "added_edges": sorted(added_edges),
            "removed_edges": sorted(removed_edges),
            "changed_files": sorted(changed_files),
        }

    def view_export(self, repo_id: str, snapshot_id: str,
                    view: str = "all", max_edges: int = 1000) -> dict:
        """Projection export (spec §41): Architecture / Symbol / all."""
        nodes = [dict(n) for n in self.all_nodes(repo_id, snapshot_id)]
        if view == "architecture":
            keep = {"REPOSITORY", "DIRECTORY", "FILE", "PACKAGE", "MODULE",
                    "SERVICE", "SUBMODULE"}
            nodes = [n for n in nodes if n["kind"] in keep]
            edges = [dict(r) for r in self.conn.execute(
                "SELECT * FROM edges WHERE repo_id=? AND snapshot_id=? AND"
                " kind IN ('CONTAINS','IMPORTS','INCLUDES','DEPENDS_ON')"
                " LIMIT ?", (repo_id, snapshot_id, max_edges)).fetchall()]
        elif view == "symbol":
            keep = {"CLASS", "TYPE", "INTERFACE", "FUNCTION", "METHOD",
                    "PROCEDURE", "SUBROUTINE", "PROGRAM", "VARIABLE",
                    "CONSTANT", "FIELD"}
            nodes = [n for n in nodes if n["kind"] in keep]
            edges = [dict(r) for r in self.conn.execute(
                "SELECT * FROM edges WHERE repo_id=? AND snapshot_id=? AND"
                " kind IN ('CALLS','REFERENCES','INHERITS','IMPLEMENTS',"
                "'USES','BINDS_TO','DEFINES') LIMIT ?",
                (repo_id, snapshot_id, max_edges)).fetchall()]
        else:
            edges = [dict(r) for r in self.conn.execute(
                "SELECT * FROM edges WHERE repo_id=? AND snapshot_id=? LIMIT ?",
                (repo_id, snapshot_id, max_edges)).fetchall()]
        return {
            "repo": repo_id, "snapshot": snapshot_id, "view": view,
            "nodes": nodes, "edges": edges,
        }

    # ==================================================================
    # Architecture plane (S3, SPEC-P1 §6.2 / §23).  Rows are plain dicts
    # (SQLite-shaped); composite FKs + partial unique index enforce R1/fix1,
    # RESTRICT semantics are honored per R5.  Multi-statement mutations run
    # inside one explicit transaction.
    # ==================================================================
    @contextmanager
    def _arch_tx(self) -> Iterator[None]:
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _arch_fetch(self, sql: str, params: tuple) -> Optional[dict]:
        r = self.conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    _WS_COLS = ("id, repo_id, name, description, created_at, updated_at")
    _MODEL_COLS = ("id, workspace_id, kind, name, description, status,"
                   " parent_model_id, base_evidence_snapshot_id,"
                   " baseline_schema_version, created_at, updated_at")
    _COMP_COLS = ("id, workspace_id, model_id, origin_id, kind, name,"
                  " description, parent_id, sort_order, created_at,"
                  " updated_at")
    _REL_COLS = ("id, workspace_id, model_id, kind, src_id, dst_id, label,"
                 " created_at, updated_at")
    _MAP_COLS = ("id, workspace_id, component_id, evidence_entity_type,"
                 " evidence_entity_id, evidence_snapshot_id, note, created_at,"
                 " updated_at")

    # -- workspaces / models -------------------------------------------
    def arch_workspaces(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._WS_COLS} FROM arch_workspaces"
            " ORDER BY created_at").fetchall()]

    def arch_workspace(self, workspace_id: str) -> Optional[dict]:
        return self._arch_fetch(
            f"SELECT {self._WS_COLS} FROM arch_workspaces WHERE id=?",
            (workspace_id,))

    def arch_create_workspace(self, repo_id: str, name: str,
                              description: str = "") -> dict:
        """Create a workspace + its empty AS-IS model atomically (fix1)."""
        wid, mid, now = str(uuid.uuid4()), str(uuid.uuid4()), self.now()
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO arch_workspaces(id, repo_id, name, description,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (wid, repo_id, name, description, now, now))
            self.conn.execute(
                "INSERT INTO arch_models(id, workspace_id, kind, name,"
                " description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (mid, wid, "as_is", "AS-IS", "", now, now))
        ws = self.arch_workspace(wid)
        assert ws is not None
        return ws

    def arch_models(self, workspace_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._MODEL_COLS} FROM arch_models"
            " WHERE workspace_id=? ORDER BY created_at",
            (workspace_id,)).fetchall()]

    def arch_model(self, workspace_id: str, model_id: str) -> Optional[dict]:
        return self._arch_fetch(
            f"SELECT {self._MODEL_COLS} FROM arch_models"
            " WHERE workspace_id=? AND id=?", (workspace_id, model_id))

    def arch_model_baseline(self, workspace_id: str,
                            model_id: str) -> Optional[dict]:
        """Frozen fork baseline (R2): parsed baseline doc + metadata."""
        r = self.conn.execute(
            "SELECT baseline_json, baseline_schema_version,"
            " base_evidence_snapshot_id FROM arch_models WHERE workspace_id=?"
            " AND id=?", (workspace_id, model_id)).fetchone()
        if not r or not r["baseline_json"]:
            return None
        try:
            doc = json.loads(r["baseline_json"])
        except ValueError:
            doc = {}
        return {"baseline": doc if isinstance(doc, dict) else {},
                "baseline_schema_version": r["baseline_schema_version"],
                "base_evidence_snapshot_id": r["base_evidence_snapshot_id"]}

    def arch_fork_model(self, workspace_id: str, source_model_id: str,
                        name: str, description: str = "") -> dict:
        """R2 fork (SPEC-P1 §6.1-8/13): deep-copy a model into a new proposal
        with a frozen baseline, all inside one transaction."""
        src = self.arch_model(workspace_id, source_model_id)
        if not src:
            raise ArchError("model_not_found", "source model not found")
        if src["kind"] != "as_is":
            raise ArchError(
                "fork_source_not_as_is",
                "only the AS-IS model can be forked into a proposal")
        ws = self.arch_workspace(workspace_id)
        assert ws is not None
        sid = self.current_snapshot(ws["repo_id"])
        comps = self.arch_components(workspace_id, source_model_id)
        rels = self.arch_relations(workspace_id, source_model_id)
        maps_by = {c["id"]: self.arch_mappings_for_component(
            workspace_id, c["id"]) for c in comps}
        doc = build_baseline(comps, rels, maps_by, sid, self.now())
        mid, now = str(uuid.uuid4()), self.now()
        idmap: dict[str, str] = {}
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO arch_models(id, workspace_id, kind, name,"
                " description, parent_model_id, base_evidence_snapshot_id,"
                " baseline_json, baseline_schema_version, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mid, workspace_id, "proposal", name, description,
                 source_model_id, sid, json.dumps(doc), 1, now, now))
            # two-phase: insert components without parents, then link them
            for c in comps:
                nid = str(uuid.uuid4())
                idmap[c["id"]] = nid
                self.conn.execute(
                    "INSERT INTO arch_components(id, workspace_id, model_id,"
                    " origin_id, kind, name, description, parent_id,"
                    " sort_order, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (nid, workspace_id, mid, c["id"], c["kind"], c["name"],
                     c["description"], None, c["sort_order"], now, now))
            for c in comps:
                nid = idmap[c["id"]]
                if c["parent_id"] and c["parent_id"] in idmap:
                    self.conn.execute(
                        "UPDATE arch_components SET parent_id=? WHERE"
                        " workspace_id=? AND model_id=? AND id=?",
                        (idmap[c["parent_id"]], workspace_id, mid, nid))
            for r in rels:
                self.conn.execute(
                    "INSERT INTO arch_relations(id, workspace_id, model_id,"
                    " kind, src_id, dst_id, label, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), workspace_id, mid, r["kind"],
                     idmap[r["src_id"]], idmap[r["dst_id"]], r["label"],
                     now, now))
            for c in comps:
                for m in maps_by.get(c["id"], []):
                    self.conn.execute(
                        "INSERT INTO arch_mappings(id, workspace_id,"
                        " component_id, evidence_entity_type,"
                        " evidence_entity_id, evidence_snapshot_id, note,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), workspace_id, idmap[c["id"]],
                         m["evidence_entity_type"], m["evidence_entity_id"],
                         m.get("evidence_snapshot_id"), m.get("note", ""),
                         now, now))
            # copy the AS-IS layout over (UI convenience; not baseline data)
            lay = self.arch_layout(workspace_id, source_model_id)
            if lay:
                new_layout = {idmap.get(k, k): v
                              for k, v in lay["layout"].items()}
                self.conn.execute(
                    "INSERT INTO arch_layouts(workspace_id, model_id,"
                    " layout_json, updated_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(workspace_id, model_id) DO UPDATE SET"
                    " layout_json=excluded.layout_json,"
                    " updated_at=excluded.updated_at",
                    (workspace_id, mid, json.dumps(new_layout), now))
        row = self.arch_model(workspace_id, mid)
        assert row is not None
        return row

    # -- components -----------------------------------------------------
    def arch_components(self, workspace_id: str,
                        model_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._COMP_COLS} FROM arch_components"
            " WHERE workspace_id=? AND model_id=? ORDER BY sort_order,"
            " created_at", (workspace_id, model_id)).fetchall()]

    def arch_component(self, workspace_id: str, component_id: str,
                       model_id: str | None = None) -> Optional[dict]:
        if model_id is not None:
            return self._arch_fetch(
                f"SELECT {self._COMP_COLS} FROM arch_components"
                " WHERE workspace_id=? AND model_id=? AND id=?",
                (workspace_id, model_id, component_id))
        return self._arch_fetch(
            f"SELECT {self._COMP_COLS} FROM arch_components"
            " WHERE workspace_id=? AND id=?", (workspace_id, component_id))

    def arch_component_children(self, workspace_id: str,
                                component_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._COMP_COLS} FROM arch_components"
            " WHERE workspace_id=? AND parent_id=? ORDER BY sort_order,"
            " created_at", (workspace_id, component_id)).fetchall()]

    def arch_create_component(self, workspace_id: str, model_id: str,
                              kind: str, name: str, description: str = "",
                              parent_id: str | None = None) -> dict:
        cid, now = str(uuid.uuid4()), self.now()
        try:
            self.conn.execute(
                "INSERT INTO arch_components(id, workspace_id, model_id, kind,"
                " name, description, parent_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, workspace_id, model_id, kind, name, description,
                 parent_id, now, now))
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise ArchError(
                "cross_model_reference",
                "component references an entity outside its model scope",
                {"component_id": cid, "parent_id": parent_id})
        row = self.arch_component(workspace_id, cid, model_id)
        assert row is not None
        return row

    def arch_update_component(self, workspace_id: str, model_id: str,
                              component_id: str, *,
                              kind: str | None = None,
                              name: str | None = None,
                              description: str | None = None,
                              parent_id: str | None = None,
                              clear_parent: bool = False,
                              sort_order: int | None = None) -> dict:
        sets, params = [], []
        if kind is not None:
            sets.append("kind=?"), params.append(kind)
        if name is not None:
            sets.append("name=?"), params.append(name)
        if description is not None:
            sets.append("description=?"), params.append(description)
        if clear_parent:
            sets.append("parent_id=NULL")
        elif parent_id is not None:
            sets.append("parent_id=?"), params.append(parent_id)
        if sort_order is not None:
            sets.append("sort_order=?"), params.append(sort_order)
        if not sets:
            raise ArchError("no_fields", "no update fields provided")
        sets.append("updated_at=?")
        params.append(self.now())
        params.extend([workspace_id, model_id, component_id])
        try:
            self.conn.execute(
                f"UPDATE arch_components SET {', '.join(sets)}"
                " WHERE workspace_id=? AND model_id=? AND id=?", params)
            self.conn.commit()
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise ArchError(
                "cross_model_reference",
                "reparent targets an entity outside this model scope",
                {"component_id": component_id, "parent_id": parent_id})
        row = self.arch_component(workspace_id, component_id, model_id)
        assert row is not None
        return row

    def arch_batch_component(self, workspace_id: str, model_id: str,
                             kind: str, name: str, description: str,
                             parent_id: str | None,
                             entities: list[tuple[str, str, str]]) -> dict:
        """R4: one component + N mappings in a single transaction."""
        cid, now = str(uuid.uuid4()), self.now()
        created: list[dict] = []
        try:
            with self._arch_tx():
                self.conn.execute(
                    "INSERT INTO arch_components(id, workspace_id, model_id,"
                    " kind, name, description, parent_id, created_at,"
                    " updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (cid, workspace_id, model_id, kind, name, description,
                     parent_id, now, now))
                for etype, eid, note in entities:
                    mid = str(uuid.uuid4())
                    self.conn.execute(
                        "INSERT INTO arch_mappings(id, workspace_id,"
                        " component_id, evidence_entity_type,"
                        " evidence_entity_id, note, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (mid, workspace_id, cid, etype, eid, note, now, now))
                    row = self.conn.execute(
                        f"SELECT {self._MAP_COLS} FROM arch_mappings"
                        " WHERE id=?", (mid,)).fetchone()
                    assert row is not None
                    created.append(dict(row))
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise ArchError(
                "cross_model_reference",
                "batch create references an entity outside model scope",
                {"component_id": cid, "parent_id": parent_id})
        comp = self.arch_component(workspace_id, cid, model_id)
        assert comp is not None
        return {"component": comp, "mappings": created}

    def _subtree_rows(self, workspace_id: str, model_id: str,
                      component_id: str) -> list[tuple[str, int]]:
        """(id, depth) of the component and all descendants, deepest first."""
        rows = self.conn.execute(
            "WITH RECURSIVE sub(id, depth) AS ("
            " SELECT id, 0 FROM arch_components"
            "  WHERE workspace_id=? AND model_id=? AND id=?"
            " UNION ALL"
            " SELECT c.id, s.depth + 1 FROM arch_components c"
            "  JOIN sub s ON c.parent_id = s.id"
            "  WHERE c.workspace_id=? AND c.model_id=?)"
            " SELECT id, depth FROM sub",
            (workspace_id, model_id, component_id, workspace_id,
             model_id)).fetchall()
        return sorted(((r["id"], r["depth"]) for r in rows),
                      key=lambda t: -t[1])

    def arch_delete_component(self, workspace_id: str, model_id: str,
                              component_id: str,
                              subtree: bool = False) -> dict:
        """R5: RESTRICT by default; explicit `subtree` deletes the whole
        subtree (relations touching members → mappings → components)."""
        if not subtree:
            kids = self.arch_component_children(workspace_id, component_id)
            rels = self.arch_relations_touching(workspace_id, component_id)
            if kids or rels:
                raise ArchError(
                    "delete_restricted",
                    "component still has children or relations",
                    {"children": kids, "relations": rels})
            n_maps = self.conn.execute(
                "SELECT COUNT(*) AS n FROM arch_mappings"
                " WHERE component_id=?", (component_id,)).fetchone()["n"]
            try:
                with self._arch_tx():
                    self.conn.execute(
                        "DELETE FROM arch_components WHERE workspace_id=? AND"
                        " id=?", (workspace_id, component_id))
            except sqlite3.IntegrityError:
                raise ArchError(
                    "delete_restricted",
                    "component deletion blocked by a database constraint")
            return {"components": 1, "relations": 0, "mappings": n_maps}
        members = [i for i, _ in self._subtree_rows(
            workspace_id, model_id, component_id)]
        marks = ",".join("?" * len(members)) if members else "NULL"
        n_maps = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM arch_mappings"
            f" WHERE component_id IN ({marks})",
            (*members,)).fetchone()["n"]
        n_rels = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM arch_relations WHERE workspace_id=?"
            f" AND (src_id IN ({marks}) OR dst_id IN ({marks}))",
            (workspace_id, *members, *members)).fetchone()["n"]
        try:
            with self._arch_tx():
                self.conn.execute(
                    f"DELETE FROM arch_relations WHERE workspace_id=? AND"
                    f" (src_id IN ({marks}) OR dst_id IN ({marks}))",
                    (workspace_id, *members, *members))
                self.conn.execute(
                    f"DELETE FROM arch_mappings WHERE component_id IN ({marks})",
                    (*members,))
                for cid in members:  # deepest first (parent RESTRICT)
                    self.conn.execute(
                        "DELETE FROM arch_components WHERE id=? AND"
                        " workspace_id=?", (cid, workspace_id))
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise ArchError("delete_restricted",
                            "subtree deletion blocked by a constraint")
        return {"components": len(members), "relations": n_rels,
                "mappings": n_maps}

    # -- relations ------------------------------------------------------
    def arch_relations(self, workspace_id: str,
                       model_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._REL_COLS} FROM arch_relations"
            " WHERE workspace_id=? AND model_id=? ORDER BY created_at",
            (workspace_id, model_id)).fetchall()]

    def arch_relation(self, workspace_id: str, relation_id: str,
                      model_id: str | None = None) -> Optional[dict]:
        if model_id is not None:
            return self._arch_fetch(
                f"SELECT {self._REL_COLS} FROM arch_relations"
                " WHERE workspace_id=? AND model_id=? AND id=?",
                (workspace_id, model_id, relation_id))
        return self._arch_fetch(
            f"SELECT {self._REL_COLS} FROM arch_relations"
            " WHERE workspace_id=? AND id=?", (workspace_id, relation_id))

    def arch_relations_touching(self, workspace_id: str,
                                component_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._REL_COLS} FROM arch_relations"
            " WHERE workspace_id=? AND (src_id=? OR dst_id=?)"
            " ORDER BY created_at", (workspace_id, component_id,
                                     component_id)).fetchall()]

    def arch_create_relation(self, workspace_id: str, model_id: str,
                             kind: str, src_id: str, dst_id: str,
                             label: str | None = None) -> dict:
        rid, now = str(uuid.uuid4()), self.now()
        try:
            with self._arch_tx():
                self.conn.execute(
                    "INSERT INTO arch_relations(id, workspace_id, model_id,"
                    " kind, src_id, dst_id, label, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (rid, workspace_id, model_id, kind, src_id, dst_id,
                     label, now, now))
        except sqlite3.IntegrityError:
            raise ArchError(
                "cross_model_reference",
                "relation endpoint references an entity outside this model",
                {"relation_id": rid, "src_id": src_id, "dst_id": dst_id})
        row = self.arch_relation(workspace_id, rid, model_id)
        assert row is not None
        return row

    def arch_update_relation(self, workspace_id: str, model_id: str,
                             relation_id: str, *,
                             kind: str | None = None,
                             label: str | None = None,
                             clear_label: bool = False) -> dict:
        sets, params = [], []
        if kind is not None:
            sets.append("kind=?"), params.append(kind)
        if clear_label:
            sets.append("label=NULL")
        elif label is not None:
            sets.append("label=?"), params.append(label)
        if not sets:
            raise ArchError("no_fields", "no update fields provided")
        sets.append("updated_at=?")
        params.append(self.now())
        params.extend([workspace_id, model_id, relation_id])
        self.conn.execute(f"UPDATE arch_relations SET {', '.join(sets)}"
                          " WHERE workspace_id=? AND model_id=? AND id=?",
                          params)
        self.conn.commit()
        row = self.arch_relation(workspace_id, relation_id, model_id)
        assert row is not None
        return row

    def arch_delete_relation(self, workspace_id: str,
                             relation_id: str) -> None:
        with self._arch_tx():
            self.conn.execute(
                "DELETE FROM arch_relations WHERE workspace_id=? AND id=?",
                (workspace_id, relation_id))

    # -- mappings -------------------------------------------------------
    def arch_mappings(self, workspace_id: str, model_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT m.{', m.'.join(self._MAP_COLS.split(', '))}"
            " FROM arch_mappings m JOIN arch_components c ON"
            " c.id = m.component_id WHERE c.workspace_id=? AND c.model_id=?"
            " ORDER BY m.created_at", (workspace_id, model_id)).fetchall()]

    def arch_mappings_for_component(self, workspace_id: str,
                                    component_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._MAP_COLS} FROM arch_mappings"
            " WHERE workspace_id=? AND component_id=? ORDER BY created_at",
            (workspace_id, component_id)).fetchall()]

    def arch_batch_mappings(self, workspace_id: str, component_id: str,
                            entities: list[tuple[str, str, str]]) -> list[dict]:
        """Add mappings to an existing component in one transaction."""
        now = self.now()
        ids: list[str] = []
        try:
            with self._arch_tx():
                for etype, eid, note in entities:
                    mid = str(uuid.uuid4())
                    ids.append(mid)
                    self.conn.execute(
                        "INSERT INTO arch_mappings(id, workspace_id,"
                        " component_id, evidence_entity_type,"
                        " evidence_entity_id, note, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (mid, workspace_id, component_id, etype, eid, note,
                         now, now))
        except sqlite3.IntegrityError:
            self.conn.rollback()
            raise ArchError(
                "mapping_exists",
                "one of the mappings already exists for this component")
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self._MAP_COLS} FROM arch_mappings WHERE id IN"
            f" ({','.join('?' * len(ids))})", tuple(ids)).fetchall()]

    def arch_delete_mapping(self, workspace_id: str,
                            mapping_id: str) -> None:
        with self._arch_tx():
            self.conn.execute(
                "DELETE FROM arch_mappings WHERE workspace_id=? AND id=?",
                (workspace_id, mapping_id))

    def arch_mapping_exists(self, workspace_id: str, component_id: str,
                            entity_type: str, entity_id: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM arch_mappings WHERE component_id=?"
            " AND evidence_entity_type=? AND evidence_entity_id=?",
            (component_id, entity_type, entity_id)).fetchone()
        return r is not None

    # -- layout ---------------------------------------------------------
    def arch_layout(self, workspace_id: str,
                    model_id: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT layout_json, updated_at FROM arch_layouts"
            " WHERE workspace_id=? AND model_id=?",
            (workspace_id, model_id)).fetchone()
        if not r:
            return None
        try:
            layout = json.loads(r["layout_json"])
        except ValueError:
            layout = {}
        return {"layout": layout if isinstance(layout, dict) else {},
                "updated_at": r["updated_at"]}

    def arch_put_layout(self, workspace_id: str, model_id: str,
                        layout: dict,
                        expected_updated_at: int | None = None) -> int:
        now = self.now()
        cur = self.conn.execute(
            "SELECT updated_at FROM arch_layouts WHERE workspace_id=?"
            " AND model_id=?", (workspace_id, model_id)).fetchone()
        if cur is not None and expected_updated_at is not None and \
                cur["updated_at"] != expected_updated_at:
            raise ArchError(
                "layout_conflict", "layout was updated elsewhere",
                {"current_updated_at": cur["updated_at"]})
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO arch_layouts(workspace_id, model_id, layout_json,"
                " updated_at) VALUES (?,?,?,?) ON CONFLICT(workspace_id,"
                " model_id) DO UPDATE SET layout_json=excluded.layout_json,"
                " updated_at=excluded.updated_at",
                (workspace_id, model_id, json.dumps(layout), now))
        return now

    # ==================================================================
    # Software circuit plane (P2-FLOW0, SPEC-P2 §3/§19): renderer-neutral
    # netlist rows.  Same conventions as the arch plane — uuid ids, JSON
    # payloads as *_json TEXT, composite FKs, RESTRICT parent semantics,
    # multi-statement mutations inside one explicit transaction.
    # ==================================================================
    @contextmanager
    def _flow_tx(self) -> Iterator[None]:
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    MODEL_COLS = ("id, workspace_id, architecture_model_id, repo_id, name,"
                  " scope_symbol_id, root_block_id, snapshot_id, status,"
                  " version, created_at, updated_at")
    BLOCK_COLS = ("id, flow_model_id, parent_block_id, kind, name, state,"
                  " code")
    PORT_COLS = ("id, flow_model_id, block_id, name, direction, semantic_kind,"
                 " code_type, position_order")
    NET_COLS = ("id, flow_model_id, source_port_id, target_port_id, kind,"
                " label")
    BIND_COLS = ("id, flow_model_id, block_id, snapshot_id,"
                 " canonical_symbol_id, binding_kind, created_at, updated_at")

    # -- flow models -----------------------------------------------------
    def flow_models(self, repo_id: str | None = None) -> list[dict]:
        if repo_id:
            return [dict(r) for r in self.conn.execute(
                f"SELECT {self.MODEL_COLS}, meta_json FROM flow_models"
                " WHERE repo_id=? ORDER BY created_at",
                (repo_id,)).fetchall()]
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self.MODEL_COLS}, meta_json FROM flow_models"
            " ORDER BY created_at").fetchall()]

    def flow_model(self, flow_id: str) -> Optional[dict]:
        return self._arch_fetch(
            f"SELECT {self.MODEL_COLS}, meta_json FROM flow_models"
            " WHERE id=?", (flow_id,))

    def flow_create_model(self, repo_id: str, name: str, snapshot_id: str, *,
                          workspace_id: str | None = None,
                          architecture_model_id: str | None = None,
                          scope_symbol_id: str | None = None,
                          meta: dict | None = None) -> dict:
        fid, now = str(uuid.uuid4()), self.now()
        with self._flow_tx():
            self.conn.execute(
                "INSERT INTO flow_models(id, workspace_id,"
                " architecture_model_id, repo_id, name, scope_symbol_id,"
                " snapshot_id, status, version, created_at, updated_at,"
                " meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, workspace_id, architecture_model_id, repo_id, name,
                 scope_symbol_id, snapshot_id, "active", 1, now, now,
                 json.dumps(meta or {})))
        m = self.flow_model(fid)
        assert m is not None
        return m

    def flow_update_model(self, flow_id: str, *, name: str | None = None,
                          status: str | None = None,
                          root_block_id: str | None = None,
                          meta: dict | None = None) -> dict:
        m = self.flow_model(flow_id)
        if not m:
            raise FlowError("flow_not_found", "flow model not found",
                            {"flow_id": flow_id})
        with self._flow_tx():
            self.conn.execute(
                "UPDATE flow_models SET name=?, status=?, root_block_id=?,"
                " version=version+1, updated_at=?, meta_json=? WHERE id=?",
                (name if name is not None else m["name"],
                 status if status is not None else m["status"],
                 root_block_id if root_block_id is not None
                 else m["root_block_id"],
                 self.now(),
                 json.dumps(meta if meta is not None
                            else json.loads(m.get("meta_json") or "{}")),
                 flow_id))
        m2 = self.flow_model(flow_id)
        assert m2 is not None
        return m2

    # -- blocks ----------------------------------------------------------
    def flow_blocks(self, flow_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self.BLOCK_COLS}, meta_json FROM flow_blocks"
            " WHERE flow_model_id=? ORDER BY created_at",
            (flow_id,)).fetchall()]

    def flow_block(self, flow_id: str, block_id: str) -> Optional[dict]:
        return self._arch_fetch(
            f"SELECT {self.BLOCK_COLS}, meta_json FROM flow_blocks"
            " WHERE flow_model_id=? AND id=?", (flow_id, block_id))

    def flow_create_block(self, flow_id: str, *, kind: str, name: str,
                          state: str = "proposed",
                          parent_block_id: str | None = None,
                          code: str | None = None,
                          meta: dict | None = None) -> dict:
        bid, now = str(uuid.uuid4()), self.now()
        with self._flow_tx():
            if parent_block_id is not None:
                parent = self.flow_block(flow_id, parent_block_id)
                if not parent:
                    raise FlowError(
                        "parent_not_in_flow", "parent block not in flow",
                        {"parent_block_id": parent_block_id})
                if parent["kind"] != "composite":
                    raise FlowError(
                        "parent_not_composite",
                        "only composite blocks can contain children",
                        {"parent_block_id": parent_block_id})
            self.conn.execute(
                "INSERT INTO flow_blocks(id, flow_model_id, parent_block_id,"
                " kind, name, state, code, meta_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (bid, flow_id, parent_block_id, kind, name, state, code,
                 json.dumps(meta or {}), now, now))
        b = self.flow_block(flow_id, bid)
        assert b is not None
        return b

    def flow_update_block(self, flow_id: str, block_id: str, *,
                          name: str | None = None,
                          kind: str | None = None,
                          state: str | None = None,
                          parent_block_id: str | None = None,
                          clear_parent: bool = False,
                          code: str | None = None,
                          meta: dict | None = None) -> dict:
        b = self.flow_block(flow_id, block_id)
        if not b:
            raise FlowError("block_not_found", "block not found",
                            {"block_id": block_id})
        new_parent = None if clear_parent else (
            parent_block_id if parent_block_id is not None
            else b["parent_block_id"])
        if new_parent is not None:
            parent = self.flow_block(flow_id, new_parent)
            if not parent:
                raise FlowError("parent_not_in_flow",
                                "parent block not in flow",
                                {"parent_block_id": new_parent})
            if parent["kind"] != "composite":
                raise FlowError("parent_not_composite",
                                "only composite blocks can contain children",
                                {"parent_block_id": new_parent})
        with self._flow_tx():
            self.conn.execute(
                "UPDATE flow_blocks SET name=?, kind=?, state=?,"
                " parent_block_id=?, code=?, meta_json=?, updated_at=? WHERE"
                " flow_model_id=? AND id=?",
                (name if name is not None else b["name"],
                 kind if kind is not None else b["kind"],
                 state if state is not None else b["state"],
                 new_parent,
                 code if code is not None else b["code"],
                 json.dumps(meta if meta is not None
                            else json.loads(b.get("meta_json") or "{}")),
                 self.now(), flow_id, block_id))
        b2 = self.flow_block(flow_id, block_id)
        assert b2 is not None
        return b2

    def flow_delete_block(self, flow_id: str, block_id: str) -> None:
        with self._flow_tx():
            # composite deletion: children move up to its parent (same flow,
            # RESTRICT-compatible; no silent subtree destruction)
            row = self.conn.execute(
                "SELECT parent_block_id FROM flow_blocks WHERE"
                " flow_model_id=? AND id=?", (flow_id, block_id)).fetchone()
            parent = row["parent_block_id"] if row else None
            self.conn.execute(
                "UPDATE flow_blocks SET parent_block_id=?, updated_at=?"
                " WHERE flow_model_id=? AND parent_block_id=?",
                (parent, self.now(), flow_id, block_id))
            self.conn.execute(
                "DELETE FROM flow_blocks WHERE flow_model_id=? AND id=?",
                (flow_id, block_id))

    # -- ports -----------------------------------------------------------
    def flow_ports(self, flow_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self.PORT_COLS}, meta_json FROM flow_ports"
            " WHERE flow_model_id=? ORDER BY block_id, position_order, id",
            (flow_id,)).fetchall()]

    def flow_port(self, flow_id: str, port_id: str) -> Optional[dict]:
        return self._arch_fetch(
            f"SELECT {self.PORT_COLS}, meta_json FROM flow_ports"
            " WHERE flow_model_id=? AND id=?", (flow_id, port_id))

    def flow_create_port(self, flow_id: str, *, block_id: str, name: str,
                         direction: str, semantic_kind: str,
                         code_type: str | None = None,
                         position_order: int | None = None,
                         meta: dict | None = None) -> dict:
        bid, now = str(uuid.uuid4()), self.now()
        with self._flow_tx():
            block = self.flow_block(flow_id, block_id)
            if not block:
                raise FlowError("block_not_in_flow", "block not in flow",
                                {"block_id": block_id})
            existing = self.flow_ports(flow_id)
            for p in existing:
                if p["block_id"] == block_id and p["name"] == name:
                    raise FlowError(
                        "port_name_exists", "port name already exists in block",
                        {"block_id": block_id, "port": name})
            order = (position_order if position_order is not None else
                     max((p["position_order"] for p in existing
                          if p["block_id"] == block_id and
                          p["direction"] == direction), default=-1) + 1)
            self.conn.execute(
                "INSERT INTO flow_ports(id, flow_model_id, block_id, name,"
                " direction, semantic_kind, code_type, position_order,"
                " meta_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (bid, flow_id, block_id, name, direction, semantic_kind,
                 code_type, order, json.dumps(meta or {}), now))
        p = self.flow_port(flow_id, bid)
        assert p is not None
        return p

    def flow_update_port(self, flow_id: str, port_id: str, *,
                         name: str | None = None,
                         semantic_kind: str | None = None,
                         code_type: str | None = None,
                         clear_type: bool = False,
                         position_order: int | None = None,
                         meta: dict | None = None) -> dict:
        p = self.flow_port(flow_id, port_id)
        if not p:
            raise FlowError("port_not_found", "port not found",
                            {"port_id": port_id})
        if name is not None and name != p["name"]:
            for other in self.flow_ports(flow_id):
                if other["block_id"] == p["block_id"] and \
                        other["name"] == name and other["id"] != port_id:
                    raise FlowError(
                        "port_name_exists", "port name already exists in block",
                        {"block_id": p["block_id"], "port": name})
        with self._flow_tx():
            self.conn.execute(
                "UPDATE flow_ports SET name=?, semantic_kind=?, code_type=?,"
                " position_order=?, meta_json=? WHERE flow_model_id=? AND id=?",
                (name if name is not None else p["name"],
                 semantic_kind if semantic_kind is not None
                 else p["semantic_kind"],
                 None if clear_type else (
                     code_type if code_type is not None else p["code_type"]),
                 position_order if position_order is not None
                 else p["position_order"],
                 json.dumps(meta if meta is not None
                            else json.loads(p.get("meta_json") or "{}")),
                 flow_id, port_id))
        p2 = self.flow_port(flow_id, port_id)
        assert p2 is not None
        return p2

    def flow_delete_port(self, flow_id: str, port_id: str) -> None:
        with self._flow_tx():
            self.conn.execute(
                "DELETE FROM flow_ports WHERE flow_model_id=? AND id=?",
                (flow_id, port_id))

    # -- nets ------------------------------------------------------------
    def flow_nets(self, flow_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self.NET_COLS}, meta_json FROM flow_nets"
            " WHERE flow_model_id=? ORDER BY created_at",
            (flow_id,)).fetchall()]

    def flow_net(self, flow_id: str, net_id: str) -> Optional[dict]:
        return self._arch_fetch(
            f"SELECT {self.NET_COLS}, meta_json FROM flow_nets"
            " WHERE flow_model_id=? AND id=?", (flow_id, net_id))

    def flow_create_net(self, flow_id: str, *, source_port_id: str,
                        target_port_id: str, kind: str = "control",
                        label: str | None = None,
                        meta: dict | None = None) -> dict:
        nid, now = str(uuid.uuid4()), self.now()
        with self._flow_tx():
            src = self.flow_port(flow_id, source_port_id)
            dst = self.flow_port(flow_id, target_port_id)
            if not src or not dst:
                raise FlowError("port_not_in_flow", "net endpoint port not in flow",
                                {"source_port_id": source_port_id,
                                 "target_port_id": target_port_id})
            for other in self.flow_nets(flow_id):
                if other["source_port_id"] == source_port_id and \
                        other["target_port_id"] == target_port_id:
                    raise FlowError("net_exists", "duplicate net",
                                    {"source_port_id": source_port_id,
                                     "target_port_id": target_port_id})
            self.conn.execute(
                "INSERT INTO flow_nets(id, flow_model_id, source_port_id,"
                " target_port_id, kind, label, meta_json, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (nid, flow_id, source_port_id, target_port_id, kind, label,
                 json.dumps(meta or {}), now))
        n = self.conn.execute(
            f"SELECT {self.NET_COLS}, meta_json FROM flow_nets WHERE id=?",
            (nid,)).fetchone()
        assert n is not None
        return dict(n)

    def flow_delete_net(self, flow_id: str, net_id: str) -> None:
        with self._flow_tx():
            self.conn.execute(
                "DELETE FROM flow_nets WHERE flow_model_id=? AND id=?",
                (flow_id, net_id))

    def flow_update_net(self, flow_id: str, net_id: str, *,
                        kind: str | None = None,
                        label: str | None = None,
                        clear_label: bool = False,
                        meta: dict | None = None) -> dict:
        n = self.flow_net(flow_id, net_id)
        if not n:
            raise FlowError("net_not_found", "net not found",
                            {"net_id": net_id})
        with self._flow_tx():
            self.conn.execute(
                "UPDATE flow_nets SET kind=?, label=?, meta_json=?"
                " WHERE flow_model_id=? AND id=?",
                (kind if kind is not None else n["kind"],
                 None if clear_label else (
                     label if label is not None else n["label"]),
                 json.dumps(meta if meta is not None
                            else json.loads(n.get("meta_json") or "{}")),
                 flow_id, net_id))
        n2 = self.flow_net(flow_id, net_id)
        assert n2 is not None
        return n2

    # -- bindings --------------------------------------------------------
    def flow_bindings(self, flow_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            f"SELECT {self.BIND_COLS} FROM flow_bindings"
            " WHERE flow_model_id=? ORDER BY created_at",
            (flow_id,)).fetchall()]

    def flow_put_binding(self, flow_id: str, *, block_id: str,
                         snapshot_id: str, canonical_symbol_id: str,
                         binding_kind: str = "implementation") -> dict:
        now = self.now()
        with self._flow_tx():
            block = self.flow_block(flow_id, block_id)
            if not block:
                raise FlowError("block_not_in_flow", "block not in flow",
                                {"block_id": block_id})
            self.conn.execute(
                "INSERT INTO flow_bindings(id, flow_model_id, block_id,"
                " snapshot_id, canonical_symbol_id, binding_kind, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(flow_model_id, block_id) DO UPDATE SET"
                " snapshot_id=excluded.snapshot_id,"
                " canonical_symbol_id=excluded.canonical_symbol_id,"
                " binding_kind=excluded.binding_kind,"
                " updated_at=excluded.updated_at",
                (str(uuid.uuid4()), flow_id, block_id, snapshot_id,
                 canonical_symbol_id, binding_kind, now, now))
        for b in self.flow_bindings(flow_id):
            if b["block_id"] == block_id:
                return b
        raise FlowError("binding_failed", "binding write failed")

    def flow_clear_binding(self, flow_id: str, block_id: str) -> None:
        with self._flow_tx():
            self.conn.execute(
                "DELETE FROM flow_bindings WHERE flow_model_id=? AND"
                " block_id=?", (flow_id, block_id))

    # -- layout ----------------------------------------------------------
    def flow_layout(self, flow_id: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT layout_json, updated_at FROM flow_layouts"
            " WHERE flow_model_id=?", (flow_id,)).fetchone()
        if not r:
            return None
        try:
            layout = json.loads(r["layout_json"])
        except ValueError:
            layout = {}
        return {"layout": layout if isinstance(layout, dict) else {},
                "updated_at": r["updated_at"]}

    def flow_put_layout(self, flow_id: str, layout: dict) -> int:
        now = self.now()
        with self._flow_tx():
            self.conn.execute(
                "INSERT INTO flow_layouts(flow_model_id, layout_json,"
                " updated_at) VALUES (?,?,?) ON CONFLICT(flow_model_id) DO"
                " UPDATE SET layout_json=excluded.layout_json,"
                " updated_at=excluded.updated_at",
                (flow_id, json.dumps(layout), now))
        return now

    # -- evidence lookups (mapping validation / resolution) -------------
    def entity_exists(self, repo_id: str, snapshot_id: str,
                      entity_type: str, entity_id: str) -> bool:
        if entity_type == "node":
            r = self.conn.execute(
                "SELECT 1 FROM nodes WHERE repo_id=? AND snapshot_id=? AND id=?",
                (repo_id, snapshot_id, entity_id)).fetchone()
        elif entity_type == "edge":
            r = self.conn.execute(
                "SELECT 1 FROM edges WHERE repo_id=? AND snapshot_id=? AND id=?",
                (repo_id, snapshot_id, entity_id)).fetchone()
        else:
            return False
        return r is not None

    def entity_row(self, repo_id: str, snapshot_id: str,
                   entity_type: str, entity_id: str) -> Optional[dict]:
        if entity_type == "node":
            r = self.conn.execute(
                "SELECT id, kind, name, qname, language, path, start_line,"
                " start_col, end_line, end_col FROM nodes"
                " WHERE repo_id=? AND snapshot_id=? AND id=?",
                (repo_id, snapshot_id, entity_id)).fetchone()
        elif entity_type == "edge":
            r = self.conn.execute(
                "SELECT id, kind, src_id, dst_id, confidence FROM edges"
                " WHERE repo_id=? AND snapshot_id=? AND id=?",
                (repo_id, snapshot_id, entity_id)).fetchone()
        else:
            return None
        return dict(r) if r else None
