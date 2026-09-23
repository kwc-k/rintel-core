"""PostgreSQL implementation of the `Store` protocol (SPEC-P1 §4 / §6).

Design:
- one connection per instance; `begin(repo_id)` opens a single transaction and
  takes a per-repository advisory xact lock, so a snapshot build is atomic
  (SPEC-P1 §6.1-5) — `commit()` publishes it, `close()` rolls back any
  uncommitted work (immutable snapshot + atomic snapshot switch, fix1 §1A.3);
- every method returns SQLite-shaped dict rows (`meta_json`,
  `candidates_json`, `only_names_json`, ...) so Indexer / Harness run
  unchanged on either backend;
- canonical identity equivalence (S1): same adapter facts → identical
  `node:{kind}:{qname}` / `edge:{kind}:{src}:{dst}` id strings.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..archmodel import build_baseline
from ..model import Callsite, EdgeSpec, ImportBinding, IncludeBinding, Node
from ..store import ArchError, FlowError
from ..evidence_authority.support import CanonicalSupportReceipt, _reuse_receipt
from .pgschema import create_all

RESOLUTION_EDGE_KINDS = ("CALLS", "IMPORTS", "INCLUDES", "REFERENCES")


def _u(value: str | uuid.UUID) -> uuid.UUID | str | None:
    """UUID columns expect uuid.UUID values; row ids are strings."""
    if value is None:
        return None
    return uuid.UUID(value) if isinstance(value, str) else value

def _u(value: str | uuid.UUID) -> uuid.UUID | str | None:
    """UUID columns expect uuid.UUID values; row ids are strings."""
    if value is None:
        return None
    return uuid.UUID(value) if isinstance(value, str) else value


def _srow(r):
    """Flow-plane row -> SQLite-shaped dict (uuid objects -> str)."""
    if r is None:
        return None
    d = dict(r)
    return {k: (str(v) if isinstance(v, uuid.UUID) else v)
            for k, v in d.items()}
# canonical (SQLite-shaped) node row: snapshot_symbols JOIN symbols (language)
_NODE_SEL = (
    "SELECT ss.repo_id, ss.snapshot_id, ss.symbol_id AS id, ss.kind, ss.name,"
    " ss.qname, s.language AS language, ss.path, ss.start_line, ss.start_col,"
    " ss.end_line, ss.end_col, ss.meta::text AS meta_json"
    " FROM snapshot_symbols ss JOIN symbols s"
    "   ON s.repo_id = ss.repo_id AND s.id = ss.symbol_id"
)
_EDGE_SEL = (
    "SELECT e.repo_id, e.snapshot_id, e.id, e.kind, e.src_symbol_id AS src_id,"
    " e.dst_symbol_id AS dst_id, e.confidence, e.meta::text AS meta_json"
    " FROM snapshot_edges e"
)
_EVID_SEL = """
    SELECT id, repo_id, snapshot_id, 'node' AS entity_type,
           symbol_id AS entity_id, source, confidence,
           location::text AS location_json, ts, payload::text AS payload_json
    FROM symbol_evidence
    UNION ALL
    SELECT id, repo_id, snapshot_id, 'edge' AS entity_type,
           edge_id AS entity_id, source, confidence,
           location::text AS location_json, ts, payload::text AS payload_json
    FROM edge_evidence
"""


class PgStore:
    """PostgreSQL evidence-graph store (SPEC-P1 §6)."""

    def __init__(self, dsn: str, schema: str | None = None):
        self.dsn = dsn
        self.schema = schema
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        if schema:
            self.conn.execute(f'SET search_path TO "{schema}"')

    # ------------------------------------------------------------------
    # repos / snapshots (server query surface, SPEC-P1 §7-1)
    # ------------------------------------------------------------------
    def repos(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id, root_path, created_at FROM repositories"
            " ORDER BY created_at")
        return [dict(r) for r in cur.fetchall()]

    def repo(self, repo_id: str) -> Optional[dict]:
        cur = self.conn.execute(
            "SELECT id, root_path, created_at FROM repositories WHERE id=%s",
            (repo_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def begin(self, repo_id: str | None = None) -> None:
        """Open the snapshot-build transaction + per-repo advisory lock."""
        self.conn.execute("BEGIN")
        if repo_id:
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (repo_id,))

    def commit(self) -> None:
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        self.conn.execute("ROLLBACK")

    def close(self) -> None:
        try:
            if self.conn.info.transaction_status != \
                    psycopg.pq.TransactionStatus.IDLE:
                self.conn.execute("ROLLBACK")
        finally:
            self.conn.close()

    def now(self) -> int:
        return int(time.time() * 1000)

    # ------------------------------------------------------------------
    # repos / snapshots
    # ------------------------------------------------------------------
    def upsert_repo(self, repo_id: str, root_path: str) -> None:
        self.conn.execute(
            "INSERT INTO repositories(id, root_path, created_at) VALUES (%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET root_path=excluded.root_path",
            (repo_id, root_path, self.now()))

    def new_snapshot(self, repo_id: str, parent_id: str | None,
                     commit: str | None = None,
                     meta: dict | None = None) -> str:
        sid = f"s-{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO snapshots(id, repo_id, parent_id, commit_sha,"
            " publication_status, created_at, meta)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (sid, repo_id, parent_id, commit, "staging", self.now(),
             Jsonb(meta or {})))
        return sid

    def snapshots(self, repo_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, repo_id, parent_id, commit_sha, publication_status, created_at,"
            " meta::text AS meta_json FROM snapshots WHERE repo_id=%s"
            " ORDER BY created_at", (repo_id,)).fetchall()
        return [dict(r) for r in rows]

    def snapshot(self, sid: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT id, repo_id, parent_id, commit_sha, publication_status, created_at,"
            " meta::text AS meta_json FROM snapshots WHERE id=%s",
            (sid,)).fetchone()
        return dict(r) if r else None

    def current_snapshot(self, repo_id: str) -> Optional[str]:
        r = self.conn.execute(
            "SELECT id FROM snapshots WHERE repo_id=%s"
            " AND publication_status='published' ORDER BY created_at DESC"
            " LIMIT 1", (repo_id,)).fetchone()
        return r["id"] if r else None

    def publish_snapshot(self, repo_id: str, snapshot_id: str) -> None:
        cur = self.conn.execute(
            "UPDATE snapshots SET publication_status='published' "
            "WHERE repo_id=%s AND id=%s AND publication_status='staging'",
            (repo_id, snapshot_id))
        if cur.rowcount != 1:
            raise RuntimeError(f"snapshot not publishable: {snapshot_id}")

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------
    def upsert_file(self, repo_id: str, path: str, hash_: str, size: int,
                    parser_version: str, indexer_version: str,
                    status: str = "ok",
                    semantic_digest: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO files(repo_id, path, hash, size, parser_version,"
            " indexer_version, semantic_digest, status, last_indexed_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (repo_id, path) DO UPDATE SET hash=excluded.hash,"
            " size=excluded.size, parser_version=excluded.parser_version,"
            " indexer_version=excluded.indexer_version,"
            " semantic_digest=excluded.semantic_digest, status=excluded.status,"
            " last_indexed_at=excluded.last_indexed_at",
            (repo_id, path, hash_, size, parser_version, indexer_version,
             semantic_digest, status, self.now()))

    def file_state(self, repo_id: str, path: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT repo_id, path, hash, size, parser_version,"
            " indexer_version, semantic_digest, status, last_indexed_at FROM files"
            " WHERE repo_id=%s AND path=%s", (repo_id, path)).fetchone()
        return dict(r) if r else None

    def files(self, repo_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT repo_id, path, hash, size, parser_version,"
            " indexer_version, semantic_digest, status, last_indexed_at FROM files"
            " WHERE repo_id=%s ORDER BY path", (repo_id,)).fetchall()]

    # ------------------------------------------------------------------
    # nodes
    # ------------------------------------------------------------------
    def upsert_node(self, node: Node, repo_id: str,
                    snapshot_id: str) -> tuple[str, bool]:
        """Merge by (kind, qname) — same canonical identity (P0 semantics)."""
        row = self.conn.execute(
            "SELECT symbol_id, path, meta FROM snapshot_symbols WHERE repo_id=%s AND"
            " snapshot_id=%s AND kind=%s AND qname=%s",
            (repo_id, snapshot_id, node.kind, node.qname)).fetchone()
        loc = {"path": node.path, "start_line": node.start_line,
               "start_col": node.start_col, "end_line": node.end_line,
               "end_col": node.end_col}
        if row:
            meta = row["meta"] or {}
            locs = meta.get("locations", [])
            if row["path"] == node.path:
                locs = [old for old in locs if old.get("path") != node.path]
            if loc not in locs:
                locs.append(loc)
            meta["locations"] = locs
            merged = {**node.meta, **meta}
            if row["path"] == node.path:
                self.conn.execute(
                    "UPDATE snapshot_symbols SET name=%s, path=%s,"
                    " start_line=%s, start_col=%s, end_line=%s, end_col=%s,"
                    " meta=%s, search_tsv=to_tsvector('simple', %s || ' ' ||"
                    " %s || ' ' || replace(%s, '/', ' ')) WHERE repo_id=%s"
                    " AND snapshot_id=%s AND symbol_id=%s",
                    (node.name, node.path, node.start_line, node.start_col,
                     node.end_line, node.end_col, Jsonb(merged), node.name,
                     node.qname, node.path, repo_id, snapshot_id,
                     row["symbol_id"]))
            else:
                self.conn.execute(
                    "UPDATE snapshot_symbols SET meta=%s WHERE repo_id=%s AND"
                    " snapshot_id=%s AND symbol_id=%s",
                    (Jsonb(merged), repo_id, snapshot_id, row["symbol_id"]))
            return row["symbol_id"], False
        nid = node.canonical_id
        meta = dict(node.meta)
        meta.setdefault("locations", []).append(loc)
        now = self.now()
        self.conn.execute(
            "INSERT INTO symbols(repo_id, id, kind, name, qname, language,"
            " first_snapshot_id, last_snapshot_id, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (repo_id, id) DO UPDATE SET last_snapshot_id="
            "excluded.last_snapshot_id, updated_at=excluded.updated_at",
            (repo_id, nid, node.kind, node.name, node.qname, node.language,
             snapshot_id, snapshot_id, now, now))
        self.conn.execute(
            "INSERT INTO snapshot_symbols(repo_id, snapshot_id, symbol_id,"
            " kind, name, qname, path, start_line, start_col, end_line,"
            " end_col, meta, search_tsv) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            " to_tsvector('simple', %s || ' ' || %s || ' ' || replace(%s, '/', ' ')))",
            (repo_id, snapshot_id, nid, node.kind, node.name, node.qname,
             node.path, node.start_line, node.start_col, node.end_line,
             node.end_col, Jsonb(meta), node.name, node.qname, node.path))
        return nid, True

    def node_by_qname(self, repo_id: str, snapshot_id: str,
                      qname: str) -> Optional[dict]:
        r = self.conn.execute(
            _NODE_SEL + " WHERE ss.repo_id=%s AND ss.snapshot_id=%s AND"
            " ss.qname=%s", (repo_id, snapshot_id, qname)).fetchone()
        return dict(r) if r else None

    def node_by_id(self, repo_id: str, snapshot_id: str,
                   nid: str) -> Optional[dict]:
        r = self.conn.execute(
            _NODE_SEL + " WHERE ss.repo_id=%s AND ss.snapshot_id=%s AND"
            " ss.symbol_id=%s", (repo_id, snapshot_id, nid)).fetchone()
        return dict(r) if r else None

    def all_nodes(self, repo_id: str, snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            _NODE_SEL + " WHERE ss.repo_id=%s AND ss.snapshot_id=%s",
            (repo_id, snapshot_id)).fetchall()]

    def nodes_by_path(self, repo_id: str, snapshot_id: str,
                      path: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            _NODE_SEL + " WHERE ss.repo_id=%s AND ss.snapshot_id=%s AND"
            " ss.path=%s", (repo_id, snapshot_id, path)).fetchall()]

    def nodes_by_path_prefix(self, repo_id: str, snapshot_id: str,
                             path_prefix: str) -> list[dict]:
        """All nodes under `path_prefix` (the dir itself + every descendant)."""
        prefix = path_prefix.rstrip("/")
        return [dict(r) for r in self.conn.execute(
            _NODE_SEL + " WHERE ss.repo_id=%s AND ss.snapshot_id=%s AND"
            " (ss.path=%s OR ss.path LIKE %s)",
            (repo_id, snapshot_id, prefix, prefix + "/%")).fetchall()]

    def delete_nodes_in_paths(self, repo_id: str, snapshot_id: str,
                              paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM snapshot_symbols WHERE repo_id=%s AND snapshot_id=%s"
            " AND path = ANY(%s)", (repo_id, snapshot_id, paths))
        return cur.rowcount

    def delete_nodes_in_paths_except(self, repo_id: str, snapshot_id: str,
                                     paths: Iterable[str],
                                     keep_ids: Iterable[str]) -> int:
        paths, keep_ids = list(paths), list(keep_ids)
        if not paths:
            return 0
        if keep_ids:
            cur = self.conn.execute(
                "DELETE FROM snapshot_symbols WHERE repo_id=%s AND"
                " snapshot_id=%s AND path = ANY(%s) AND NOT"
                " (symbol_id = ANY(%s))",
                (repo_id, snapshot_id, paths, keep_ids))
        else:
            cur = self.conn.execute(
                "DELETE FROM snapshot_symbols WHERE repo_id=%s AND"
                " snapshot_id=%s AND path = ANY(%s)",
                (repo_id, snapshot_id, paths))
        return cur.rowcount

    # ------------------------------------------------------------------
    # edges
    # ------------------------------------------------------------------
    def add_edge_raw(self, kind: str, src_id: str, dst_id: str,
                     repo_id: str, snapshot_id: str, confidence: float = 1.0,
                     meta: dict | None = None,
                     evidence: dict | None = None) -> bool:
        eid = f"edge:{kind}:{src_id}:{dst_id}"
        cur = self.conn.execute(
            "INSERT INTO snapshot_edges(repo_id, snapshot_id, id, kind,"
            " src_symbol_id, dst_symbol_id, confidence, meta) VALUES"
            " (%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (repo_id, snapshot_id, id) DO NOTHING",
            (repo_id, snapshot_id, eid, kind, src_id, dst_id, confidence,
             Jsonb(meta or {})))
        if cur.rowcount == 0:
            return False
        if evidence:
            self.add_evidence(repo_id, snapshot_id, "edge", eid, **evidence)
        return True

    def edges_for_node(self, repo_id: str, snapshot_id: str, nid: str,
                       direction: str = "both",
                       relation: str | None = None) -> list[dict]:
        def other_sel(side: str) -> str:
            # snapshot_symbols join + LATERAL projection of the other endpoint
            return (
                "SELECT e.repo_id, e.snapshot_id, e.id, e.kind,"
                " e.src_symbol_id AS src_id, e.dst_symbol_id AS dst_id,"
                " e.confidence, e.meta::text AS meta_json, o.other_kind,"
                " o.other_name, o.other_qname, o.other_path"
                " FROM snapshot_edges e"
                " JOIN snapshot_symbols n ON n.repo_id=e.repo_id AND"
                f" n.snapshot_id=e.snapshot_id AND n.symbol_id={side},"
                " LATERAL (SELECT n.kind AS other_kind, n.name AS other_name,"
                " n.qname AS other_qname, n.path AS other_path) o"
            )

        if direction == "out":
            sql = other_sel("e.dst_symbol_id") + \
                " WHERE e.repo_id=%s AND e.snapshot_id=%s AND" \
                " e.src_symbol_id=%s"
            args: tuple = (repo_id, snapshot_id, nid)
        elif direction == "in":
            sql = other_sel("e.src_symbol_id") + \
                " WHERE e.repo_id=%s AND e.snapshot_id=%s AND" \
                " e.dst_symbol_id=%s"
            args = (repo_id, snapshot_id, nid)
        else:
            sql = (_EDGE_SEL + " WHERE e.repo_id=%s AND e.snapshot_id=%s AND"
                   " (e.src_symbol_id=%s OR e.dst_symbol_id=%s)")
            args = (repo_id, snapshot_id, nid, nid)
        if relation:
            sql += " AND e.kind=%s"
            args += (relation,)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def delete_edges_in_snapshot(self, repo_id: str, snapshot_id: str,
                                 kinds: Iterable[str]) -> int:
        kinds = list(kinds)
        if not kinds:
            return 0
        cur = self.conn.execute(
            "DELETE FROM snapshot_edges WHERE repo_id=%s AND snapshot_id=%s"
            " AND kind = ANY(%s)", (repo_id, snapshot_id, kinds))
        return cur.rowcount

    def delete_edges_touching_paths(self, repo_id: str, snapshot_id: str,
                                    paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM snapshot_edges WHERE repo_id=%s AND snapshot_id=%s"
            " AND (src_symbol_id IN (SELECT symbol_id FROM snapshot_symbols"
            " WHERE repo_id=%s AND snapshot_id=%s AND path = ANY(%s))"
            " OR dst_symbol_id IN (SELECT symbol_id FROM snapshot_symbols"
            " WHERE repo_id=%s AND snapshot_id=%s AND path = ANY(%s)))",
            (repo_id, snapshot_id, repo_id, snapshot_id, paths,
             repo_id, snapshot_id, paths))
        return cur.rowcount

    def delete_edges_from_paths(self, repo_id: str, snapshot_id: str,
                                paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM snapshot_edges WHERE repo_id=%s AND snapshot_id=%s "
            "AND src_symbol_id IN (SELECT symbol_id FROM snapshot_symbols "
            "WHERE repo_id=%s AND snapshot_id=%s AND path = ANY(%s))",
            (repo_id, snapshot_id, repo_id, snapshot_id, paths))
        return cur.rowcount

    def delete_edges_all(self, repo_id: str, snapshot_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM snapshot_edges WHERE repo_id=%s AND snapshot_id=%s",
            (repo_id, snapshot_id))
        return cur.rowcount

    def all_edges(self, repo_id: str, snapshot_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            _EDGE_SEL + " WHERE e.repo_id=%s AND e.snapshot_id=%s",
            (repo_id, snapshot_id)).fetchall()]

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------
    def add_evidence(self, repo_id: str, snapshot_id: str, entity_type: str,
                     entity_id: str, source: str, confidence: float,
                     location: dict | None = None,
                     payload: dict | None = None) -> None:
        if entity_type == "node":
            self.conn.execute(
                "INSERT INTO symbol_evidence(repo_id, snapshot_id, symbol_id,"
                " source, confidence, location, payload, ts) VALUES"
                " (%s,%s,%s,%s,%s,%s,%s,%s)",
                (repo_id, snapshot_id, entity_id, source, confidence,
                 Jsonb(location) if location else None, Jsonb(payload or {}),
                 self.now()))
        else:
            self.conn.execute(
                "INSERT INTO edge_evidence(repo_id, snapshot_id, edge_id,"
                " source, confidence, location, payload, ts) VALUES"
                " (%s,%s,%s,%s,%s,%s,%s,%s)",
                (repo_id, snapshot_id, entity_id, source, confidence,
                 Jsonb(location) if location else None, Jsonb(payload or {}),
                 self.now()))

    def evidence_for(self, repo_id: str, snapshot_id: str,
                     entity_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM (" + _EVID_SEL + ") u WHERE repo_id=%s AND"
            " snapshot_id=%s AND entity_id=%s",
            (repo_id, snapshot_id, entity_id)).fetchall()
        return [dict(r) for r in rows]

    def delete_evidence_all(self, repo_id: str, snapshot_id: str) -> int:
        n = self.conn.execute(
            "DELETE FROM symbol_evidence WHERE repo_id=%s AND snapshot_id=%s",
            (repo_id, snapshot_id)).rowcount
        e = self.conn.execute(
            "DELETE FROM edge_evidence WHERE repo_id=%s AND snapshot_id=%s",
            (repo_id, snapshot_id)).rowcount
        return n + e

    def delete_evidence_in_paths(self, repo_id: str, snapshot_id: str,
                                 paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        n = self.conn.execute(
            "DELETE FROM symbol_evidence WHERE repo_id=%s AND snapshot_id=%s"
            " AND symbol_id IN (SELECT symbol_id FROM snapshot_symbols WHERE"
            " repo_id=%s AND snapshot_id=%s AND path = ANY(%s))",
            (repo_id, snapshot_id, repo_id, snapshot_id, paths)).rowcount
        e = self.conn.execute(
            "DELETE FROM edge_evidence WHERE repo_id=%s AND snapshot_id=%s"
            " AND edge_id IN (SELECT e.id FROM snapshot_edges e WHERE"
            " e.repo_id=%s AND e.snapshot_id=%s AND (e.src_symbol_id IN"
            " (SELECT symbol_id FROM snapshot_symbols WHERE repo_id=%s AND"
            " snapshot_id=%s AND path = ANY(%s)) OR e.dst_symbol_id IN"
            " (SELECT symbol_id FROM snapshot_symbols WHERE repo_id=%s AND"
            " snapshot_id=%s AND path = ANY(%s))))",
            (repo_id, snapshot_id, repo_id, snapshot_id,
             repo_id, snapshot_id, paths,
             repo_id, snapshot_id, paths)).rowcount
        return n + e

    def delete_evidence_owned_by_paths(self, repo_id: str, snapshot_id: str,
                                       paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        nodes = self.conn.execute(
            "DELETE FROM symbol_evidence WHERE repo_id=%s AND snapshot_id=%s "
            "AND symbol_id IN (SELECT symbol_id FROM snapshot_symbols WHERE "
            "repo_id=%s AND snapshot_id=%s AND path = ANY(%s))",
            (repo_id, snapshot_id, repo_id, snapshot_id, paths)).rowcount
        edges = self.conn.execute(
            "WITH doomed AS MATERIALIZED (SELECT e.id FROM snapshot_edges e "
            "JOIN snapshot_symbols s ON s.repo_id=e.repo_id AND "
            "s.snapshot_id=e.snapshot_id AND s.symbol_id=e.src_symbol_id "
            "WHERE e.repo_id=%s AND e.snapshot_id=%s AND s.path = ANY(%s)) "
            "DELETE FROM edge_evidence ev USING doomed d WHERE ev.repo_id=%s "
            "AND ev.snapshot_id=%s AND ev.edge_id=d.id",
            (repo_id, snapshot_id, paths, repo_id, snapshot_id)).rowcount
        return nodes + edges

    def delete_node_evidence_all(self, repo_id: str,
                                 snapshot_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM symbol_evidence WHERE repo_id=%s AND snapshot_id=%s",
            (repo_id, snapshot_id))
        return cur.rowcount

    # ------------------------------------------------------------------
    # callsites / imports / includes / pending edges
    # ------------------------------------------------------------------
    def add_callsite(self, cs: Callsite, repo_id: str,
                     snapshot_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO callsites(repo_id, snapshot_id, file_path, line, col,"
            " callee, candidates, shape) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            " RETURNING id",
            (repo_id, snapshot_id, cs.path, cs.line, cs.col, cs.callee,
             cs.candidates, Jsonb(cs.shape) if cs.shape is not None else None))
        return cur.fetchone()["id"]

    def all_callsites(self, repo_id: str, snapshot_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, file_path, line, col, callee,"
            " array_to_json(candidates)::text AS candidates_json,"
            " shape::text AS shape,"
            " resolved_symbol_id AS resolved_node_id, resolve_kind,"
            " confidence, edge_id FROM callsites WHERE repo_id=%s AND"
            " snapshot_id=%s ORDER BY file_path,line,col,id",
            (repo_id, snapshot_id)).fetchall()
        return [dict(r) for r in rows]

    def callsites_in_paths(self, repo_id: str, snapshot_id: str,
                           paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, file_path, line, col, callee,"
            " array_to_json(candidates)::text AS candidates_json,"
            " shape::text AS shape, resolved_symbol_id AS resolved_node_id,"
            " resolve_kind, confidence, edge_id FROM callsites WHERE repo_id=%s"
            " AND snapshot_id=%s AND file_path = ANY(%s)"
            " ORDER BY file_path,line,col,id",
            (repo_id, snapshot_id, paths)).fetchall()
        return [dict(r) for r in rows]

    def update_callsite(self, cid: int, resolved_node_id: str | None,
                        resolve_kind: str | None, confidence: float | None,
                        edge_id: str | None) -> None:
        self.conn.execute(
            "UPDATE callsites SET resolved_symbol_id=%s, resolve_kind=%s,"
            " confidence=%s, edge_id=%s WHERE id=%s",
            (resolved_node_id, resolve_kind, confidence, edge_id, cid))

    def callsite_row(self, cid: int) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT file_path, line, callee, shape::text AS shape FROM"
            " callsites WHERE id=%s", (cid,)).fetchone()
        return dict(r) if r else None

    def delete_callsites_in_paths(self, repo_id: str, snapshot_id: str,
                                  paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM callsites WHERE repo_id=%s AND snapshot_id=%s AND"
            " file_path = ANY(%s)", (repo_id, snapshot_id, paths))
        return cur.rowcount

    def add_import_row(self, imp: ImportBinding, src_qname: str,
                       repo_id: str, snapshot_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO imports(repo_id, snapshot_id, file_path, line,"
            " src_qname, module_qname, local, only_names, meta) VALUES"
            " (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (repo_id, snapshot_id, imp.path, imp.line, src_qname,
             imp.module_qname, imp.local, imp.only_names or [],
             Jsonb(imp.meta)))
        return cur.fetchone()["id"]

    def all_imports(self, repo_id: str, snapshot_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, file_path, line, src_qname,"
            " module_qname, local,"
            " array_to_json(only_names)::text AS only_names_json,"
            " meta::text AS meta_json, external FROM imports WHERE repo_id=%s"
            " AND snapshot_id=%s ORDER BY file_path,line,id",
            (repo_id, snapshot_id)).fetchall()
        return [dict(r) for r in rows]

    def imports_in_paths(self, repo_id: str, snapshot_id: str,
                         paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, file_path, line, src_qname,"
            " module_qname, local, array_to_json(only_names)::text AS"
            " only_names_json, meta::text AS meta_json, external FROM imports"
            " WHERE repo_id=%s AND snapshot_id=%s AND file_path = ANY(%s)"
            " ORDER BY file_path,line,id",
            (repo_id, snapshot_id, paths)).fetchall()
        return [dict(r) for r in rows]

    def set_import_external(self, row_id: int) -> None:
        self.conn.execute(
            "UPDATE imports SET external=TRUE WHERE id=%s", (row_id,))

    def delete_imports_in_paths(self, repo_id: str, snapshot_id: str,
                                paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM imports WHERE repo_id=%s AND snapshot_id=%s AND"
            " file_path = ANY(%s)", (repo_id, snapshot_id, paths))
        return cur.rowcount

    def add_include_row(self, inc: IncludeBinding, repo_id: str,
                        snapshot_id: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO includes(repo_id, snapshot_id, file_path, line,"
            " target, meta) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (repo_id, snapshot_id, inc.path, inc.line, inc.target,
             Jsonb(inc.meta)))
        return cur.fetchone()["id"]

    def all_includes(self, repo_id: str, snapshot_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, file_path, line, target,"
            " meta::text AS meta_json, external FROM includes WHERE repo_id=%s"
            " AND snapshot_id=%s ORDER BY file_path,line,id",
            (repo_id, snapshot_id)).fetchall()
        return [dict(r) for r in rows]

    def includes_in_paths(self, repo_id: str, snapshot_id: str,
                          paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, file_path, line, target,"
            " meta::text AS meta_json, external FROM includes WHERE repo_id=%s"
            " AND snapshot_id=%s AND file_path = ANY(%s)"
            " ORDER BY file_path,line,id",
            (repo_id, snapshot_id, paths)).fetchall()
        return [dict(r) for r in rows]

    def set_include_external(self, row_id: int) -> None:
        self.conn.execute(
            "UPDATE includes SET external=TRUE WHERE id=%s", (row_id,))

    def delete_includes_in_paths(self, repo_id: str, snapshot_id: str,
                                 paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM includes WHERE repo_id=%s AND snapshot_id=%s AND"
            " file_path = ANY(%s)", (repo_id, snapshot_id, paths))
        return cur.rowcount

    def add_pending_edge(self, edge: EdgeSpec, file_path: str,
                         repo_id: str, snapshot_id: str,
                         meta: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO pending_edges(repo_id, snapshot_id, kind, file_path,"
            " line, src_mode, src_value, dst_mode, dst_value, confidence, meta)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (repo_id, snapshot_id, edge.kind, file_path, edge.line,
             edge.src.mode, edge.src.value, edge.dst.mode, edge.dst.value,
             edge.confidence,
             Jsonb(meta if meta is not None else edge.meta)))
        return cur.fetchone()["id"]

    def all_pending_edges(self, repo_id: str,
                          snapshot_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, kind, file_path, line, src_mode,"
            " src_value, dst_mode, dst_value, confidence, meta::text AS"
            " meta_json FROM pending_edges WHERE repo_id=%s AND snapshot_id=%s"
            " ORDER BY file_path,line,id",
            (repo_id, snapshot_id)).fetchall()
        return [dict(r) for r in rows]

    def resolution_input_count(self, repo_id: str, snapshot_id: str) -> int:
        row = self.conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM pending_edges WHERE repo_id=%s AND snapshot_id=%s) + "
            "(SELECT COUNT(*) FROM callsites WHERE repo_id=%s AND snapshot_id=%s) + "
            "(SELECT COUNT(*) FROM imports WHERE repo_id=%s AND snapshot_id=%s) + "
            "(SELECT COUNT(*) FROM includes WHERE repo_id=%s AND snapshot_id=%s) AS n",
            (repo_id, snapshot_id, repo_id, snapshot_id,
             repo_id, snapshot_id, repo_id, snapshot_id)).fetchone()
        return int(row["n"])

    def pending_edges_in_paths(self, repo_id: str, snapshot_id: str,
                               paths: Iterable[str]) -> list[dict]:
        paths = list(paths)
        if not paths:
            return []
        rows = self.conn.execute(
            "SELECT id, repo_id, snapshot_id, kind, file_path, line, src_mode,"
            " src_value, dst_mode, dst_value, confidence, meta::text AS"
            " meta_json FROM pending_edges WHERE repo_id=%s AND snapshot_id=%s"
            " AND file_path = ANY(%s) ORDER BY file_path,line,id",
            (repo_id, snapshot_id, paths)).fetchall()
        return [dict(r) for r in rows]

    def delete_pending_in_paths(self, repo_id: str, snapshot_id: str,
                                paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM pending_edges WHERE repo_id=%s AND snapshot_id=%s AND"
            " file_path = ANY(%s)", (repo_id, snapshot_id, paths))
        return cur.rowcount

    # ------------------------------------------------------------------
    # snapshot copy (incremental, spec §26)
    # ------------------------------------------------------------------
    def copy_nodes_edges(self, repo_id: str, parent_sid: str,
                         sid: str) -> int:
        n = self.conn.execute(
            "INSERT INTO snapshot_symbols(repo_id, snapshot_id, symbol_id,"
            " kind, name, qname, path, start_line, start_col, end_line,"
            " end_col, meta, search_tsv) SELECT repo_id, %s, symbol_id, kind,"
            " name, qname, path, start_line, start_col, end_line, end_col,"
            " meta, search_tsv FROM snapshot_symbols WHERE repo_id=%s AND"
            " snapshot_id=%s", (sid, repo_id, parent_sid)).rowcount
        e = self.conn.execute(
            "INSERT INTO snapshot_edges(repo_id, snapshot_id, id, kind,"
            " src_symbol_id, dst_symbol_id, confidence, meta) SELECT repo_id,"
            " %s, id, kind, src_symbol_id, dst_symbol_id, confidence, meta"
            " FROM snapshot_edges WHERE repo_id=%s AND snapshot_id=%s",
            (sid, repo_id, parent_sid)).rowcount
        return n + e

    def _require_staging_support(self, repo_id: str, sid: str) -> None:
        row = self.conn.execute(
            "SELECT publication_status FROM snapshots WHERE repo_id=%s AND id=%s",
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
        table = "snapshot_symbols" if row["canonical_fact_type"] == "node" else "snapshot_edges"
        id_field = "symbol_id" if row["canonical_fact_type"] == "node" else "id"
        fact = self.conn.execute(
            f"SELECT kind FROM {table} WHERE repo_id=%s AND snapshot_id=%s"
            f" AND {id_field}=%s",
            (repo_id, snapshot_id, row["canonical_fact_id"])).fetchone()
        if not fact or fact["kind"] != row["canonical_fact_kind"]:
            raise ValueError("canonical support fact not present in staging graph")
        self.conn.execute(
            "INSERT INTO canonical_support_receipts(repo_id, snapshot_id,"
            " support_receipt_id, canonical_fact_id, canonical_fact_type,"
            " owner_path, receipt) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (repo_id, snapshot_id, row["support_receipt_id"],
             row["canonical_fact_id"], row["canonical_fact_type"],
             row["owner_path"], Jsonb(row)))

    def support_receipts(self, repo_id: str, snapshot_id: str,
                         canonical_fact_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT receipt FROM canonical_support_receipts WHERE repo_id=%s"
            " AND snapshot_id=%s AND canonical_fact_id=%s ORDER BY support_receipt_id",
            (repo_id, snapshot_id, canonical_fact_id)).fetchall()
        return [dict(row["receipt"]) for row in rows]

    def support_receipts_owned_by_paths(self, repo_id: str, snapshot_id: str,
                                        paths: Iterable[str]) -> list[dict]:
        values = list(paths)
        if not values:
            return []
        rows = self.conn.execute(
            "SELECT receipt FROM canonical_support_receipts WHERE repo_id=%s"
            " AND snapshot_id=%s AND owner_path=ANY(%s)",
            (repo_id, snapshot_id, values)).fetchall()
        return [dict(row["receipt"]) for row in rows]

    def copy_support_receipts(self, repo_id: str, parent_sid: str,
                              sid: str) -> int:
        self._require_staging_support(repo_id, sid)
        parent = self.snapshot(parent_sid)
        if not parent or parent["publication_status"] != "published":
            raise ValueError("support reuse requires published parent")
        rows = self.conn.execute(
            "SELECT receipt FROM canonical_support_receipts WHERE repo_id=%s"
            " AND snapshot_id=%s", (repo_id, parent_sid)).fetchall()
        copies = [_reuse_receipt(item["receipt"], sid) for item in rows]
        self.conn.cursor().executemany(
            "INSERT INTO canonical_support_receipts(repo_id, snapshot_id,"
            " support_receipt_id, canonical_fact_id, canonical_fact_type,"
            " owner_path, receipt) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [(repo_id, sid, item["support_receipt_id"], item["canonical_fact_id"],
              item["canonical_fact_type"], item["owner_path"], Jsonb(item))
             for item in copies])
        return len(copies)

    def delete_support_owned_by_paths(self, repo_id: str, snapshot_id: str,
                                      paths: Iterable[str]) -> int:
        values = list(paths)
        if not values:
            return 0
        self._require_staging_support(repo_id, snapshot_id)
        return self.conn.execute(
            "DELETE FROM canonical_support_receipts WHERE repo_id=%s"
            " AND snapshot_id=%s AND owner_path=ANY(%s)",
            (repo_id, snapshot_id, values)).rowcount

    def prune_orphan_support(self, repo_id: str, snapshot_id: str) -> int:
        self._require_staging_support(repo_id, snapshot_id)
        return self.conn.execute(
            "DELETE FROM canonical_support_receipts s WHERE s.repo_id=%s"
            " AND s.snapshot_id=%s AND ((s.canonical_fact_type='node' AND NOT EXISTS"
            " (SELECT 1 FROM snapshot_symbols n WHERE n.repo_id=s.repo_id"
            " AND n.snapshot_id=s.snapshot_id AND n.symbol_id=s.canonical_fact_id))"
            " OR (s.canonical_fact_type='edge' AND NOT EXISTS"
            " (SELECT 1 FROM snapshot_edges e WHERE e.repo_id=s.repo_id"
            " AND e.snapshot_id=s.snapshot_id AND e.id=s.canonical_fact_id)))",
            (repo_id, snapshot_id)).rowcount

    def unsupported_new_facts(self, repo_id: str, snapshot_id: str,
                              parent_sid: str | None) -> list[str]:
        supported = {row["canonical_fact_id"] for row in self.conn.execute(
            "SELECT canonical_fact_id FROM canonical_support_receipts"
            " WHERE repo_id=%s AND snapshot_id=%s", (repo_id, snapshot_id))}
        old = set()
        if parent_sid:
            old = {row["id"] for row in self.all_nodes(repo_id, parent_sid)}
            old.update(row["id"] for row in self.all_edges(repo_id, parent_sid))
        facts = {row["id"] for row in self.all_nodes(repo_id, snapshot_id)}
        facts.update(row["id"] for row in self.all_edges(repo_id, snapshot_id))
        return sorted(facts - supported - old)

    def copy_callsites(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO callsites(repo_id, snapshot_id, file_path, line, col,"
            " callee, candidates, shape, resolved_symbol_id, resolve_kind,"
            " confidence, edge_id) SELECT repo_id, %s, file_path, line, col,"
            " callee, candidates, shape, resolved_symbol_id, resolve_kind,"
            " confidence, edge_id FROM callsites WHERE repo_id=%s"
            " AND snapshot_id=%s", (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_imports(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO imports(repo_id, snapshot_id, file_path, line,"
            " src_qname, module_qname, local, only_names, meta, external) SELECT"
            " repo_id, %s, file_path, line, src_qname, module_qname, local,"
            " only_names, meta, external FROM imports WHERE repo_id=%s AND snapshot_id=%s",
            (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_includes(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO includes(repo_id, snapshot_id, file_path, line,"
            " target, meta, external) SELECT repo_id, %s, file_path, line,"
            " target, meta, external"
            " FROM includes WHERE repo_id=%s AND snapshot_id=%s",
            (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_pending_edges(self, repo_id: str, src_sid: str,
                           dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO pending_edges(repo_id, snapshot_id, kind, file_path,"
            " line, src_mode, src_value, dst_mode, dst_value, confidence,"
            " meta) SELECT repo_id, %s, kind, file_path, line, src_mode,"
            " src_value, dst_mode, dst_value, confidence, meta FROM"
            " pending_edges WHERE repo_id=%s AND snapshot_id=%s",
            (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def copy_evidence(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        nodes = self.conn.execute(
            "INSERT INTO symbol_evidence(repo_id, snapshot_id, symbol_id,"
            " source, confidence, location, payload, ts) SELECT repo_id, %s,"
            " symbol_id, source, confidence, location, payload, ts FROM"
            " symbol_evidence WHERE repo_id=%s AND snapshot_id=%s",
            (dst_sid, repo_id, src_sid)).rowcount
        edges = self.conn.execute(
            "INSERT INTO edge_evidence(repo_id, snapshot_id, edge_id, source,"
            " confidence, location, payload, ts) SELECT repo_id, %s, edge_id,"
            " source, confidence, location, payload, ts FROM edge_evidence"
            " WHERE repo_id=%s AND snapshot_id=%s",
            (dst_sid, repo_id, src_sid)).rowcount
        return nodes + edges

    def copy_unresolved(self, repo_id: str, src_sid: str, dst_sid: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO unresolved(repo_id, snapshot_id, kind, detail, ts)"
            " SELECT repo_id, %s, kind, detail, ts FROM unresolved"
            " WHERE repo_id=%s AND snapshot_id=%s",
            (dst_sid, repo_id, src_sid))
        return cur.rowcount

    def delete_unresolved_in_paths(self, repo_id: str, snapshot_id: str,
                                   paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        cur = self.conn.execute(
            "DELETE FROM unresolved WHERE repo_id=%s AND snapshot_id=%s AND"
            " COALESCE(detail->>'file', detail->>'path') = ANY(%s)",
            (repo_id, snapshot_id, paths))
        return cur.rowcount

    # ------------------------------------------------------------------
    # unresolved / telemetry
    # ------------------------------------------------------------------
    def add_unresolved(self, repo_id: str, snapshot_id: str, kind: str,
                       detail: dict) -> None:
        self.conn.execute(
            "INSERT INTO unresolved(repo_id, snapshot_id, kind, detail, ts)"
            " VALUES (%s,%s,%s,%s,%s)",
            (repo_id, snapshot_id, kind, Jsonb(detail), self.now()))

    def unresolved_count(self, repo_id: str, snapshot_id: str) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS c FROM unresolved WHERE repo_id=%s AND"
            " snapshot_id=%s", (repo_id, snapshot_id)).fetchone()
        return r["c"]

    def unresolved_rows(self, repo_id: str,
                        snapshot_id: str) -> list[dict]:
        """All unresolved notes of a snapshot (kind + detail dict) — the
        FAC-EQ0 reason histogram input."""
        rows = self.conn.execute(
            "SELECT kind, detail::text AS detail_json FROM unresolved WHERE"
            " repo_id=%s AND snapshot_id=%s",
            (repo_id, snapshot_id)).fetchall()
        return [dict(r) for r in rows]

    def telemetry(self, run_id: str, repo_id: str | None,
                  language: str | None, phase: str, key: str,
                  value: Any) -> None:
        self.conn.execute(
            "INSERT INTO run_telemetry(run_id, repo_id, language, phase, key,"
            " value, ts) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (run_id, repo_id, language, phase, key, Jsonb(value), self.now()))

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def search(self, repo_id: str, query: str, limit: int = 50,
               snapshot_id: str | None = None) -> list[dict]:
        """Symbol search (exact → fts → trigram) scoped to one snapshot.

        `snapshot_id` filters `snapshot_symbols` (DEBT-SNAPSHOT-SEARCH fix):
        without it, every snapshot of the repo is searched and the dedup at
        the end lets the latest snapshot shadow historical rows.
        """
        q = query.strip()
        out: list[dict] = []
        scope = " AND ss.snapshot_id=%s" if snapshot_id else ""
        sargs = () if snapshot_id is None else (snapshot_id,)
        for col in ("qname", "name", "path"):
            rows = self.conn.execute(
                _NODE_SEL + f" WHERE ss.repo_id=%s{scope} AND ss.{col}=%s"
                " LIMIT %s", (repo_id, *sargs, q, limit)).fetchall()
            for r in rows:
                d = dict(r)
                d["match"] = f"exact:{col}"
                out.append(d)
        if len(out) >= limit:
            return out[:limit]
        rows = self.conn.execute(
            _NODE_SEL + f" WHERE ss.repo_id=%s{scope} AND ss.search_tsv @@"
            " plainto_tsquery('simple', %s) ORDER BY ts_rank(ss.search_tsv,"
            " plainto_tsquery('simple', %s)) DESC LIMIT %s",
            (repo_id, *sargs, q, q, limit - len(out))).fetchall()
        for r in rows:
            d = dict(r)
            d["match"] = "fts"
            out.append(d)
        if len(out) >= limit:
            return out[:limit]
        like = f"%{q}%"
        rows = self.conn.execute(
            _NODE_SEL + f" WHERE ss.repo_id=%s{scope} AND (ss.qname ILIKE %s"
            " OR ss.name ILIKE %s OR ss.path ILIKE %s) LIMIT %s",
            (repo_id, *sargs, like, like, like, limit - len(out))).fetchall()
        for r in rows:
            d = dict(r)
            d["match"] = "trigram"
            out.append(d)
        seen: set[str] = set()
        dedup: list[dict] = []
        for d in out:
            if d["id"] not in seen:
                seen.add(d["id"])
                dedup.append(d)
        return dedup[:limit]

    def neighbors(self, repo_id: str, snapshot_id: str, nid: str,
                  relation: str | None = None, depth: int = 1,
                  max_nodes: int = 300, direction: str = "both") -> dict:
        """BFS neighborhood with node budget (spec §20) — port of P0 logic."""
        seen: dict[str, int] = {nid: 0}
        edges: list[dict] = []
        frontier = [nid]
        for _d in range(1, depth + 1):
            if len(seen) >= max_nodes:
                break
            nxt: list[str] = []
            for cur in frontier:
                for e in self.edges_for_node(repo_id, snapshot_id, cur,
                                             direction, relation):
                    other = e["dst_id"] if e["src_id"] == cur else e["src_id"]
                    if other not in seen:
                        seen[other] = _d
                        edges.append(e)
                        nxt.append(other)
                    if len(seen) >= max_nodes:
                        break
                if len(seen) >= max_nodes:
                    break
            frontier = nxt
        nodes: dict[str, dict] = {}
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
            "SELECT COUNT(*) AS c FROM snapshot_symbols WHERE repo_id=%s AND"
            " snapshot_id=%s", (repo_id, sid)).fetchone()["c"]
        st["edges"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM snapshot_edges WHERE repo_id=%s AND"
            " snapshot_id=%s", (repo_id, sid)).fetchone()["c"]
        st["node_kinds"] = {r["kind"]: r["c"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) AS c FROM snapshot_symbols WHERE repo_id=%s"
            " AND snapshot_id=%s GROUP BY kind ORDER BY c DESC",
            (repo_id, sid)).fetchall()}
        st["edge_kinds"] = {r["kind"]: r["c"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) AS c FROM snapshot_edges WHERE repo_id=%s"
            " AND snapshot_id=%s GROUP BY kind ORDER BY c DESC",
            (repo_id, sid)).fetchall()}
        st["languages"] = {r["language"]: r["c"] for r in self.conn.execute(
            "SELECT s.language, COUNT(*) AS c FROM snapshot_symbols ss JOIN"
            " symbols s ON s.repo_id=ss.repo_id AND s.id=ss.symbol_id WHERE"
            " ss.repo_id=%s AND ss.snapshot_id=%s GROUP BY s.language",
            (repo_id, sid)).fetchall()}
        st["files"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE repo_id=%s",
            (repo_id,)).fetchone()["c"]
        st["callsites"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM callsites WHERE repo_id=%s AND"
            " snapshot_id=%s", (repo_id, sid)).fetchone()["c"]
        st["callsites_unresolved"] = self.conn.execute(
            "SELECT COUNT(*) AS c FROM callsites WHERE repo_id=%s AND"
            " snapshot_id=%s AND resolved_symbol_id IS NULL",
            (repo_id, sid)).fetchone()["c"]
        st["unresolved_notes"] = self.unresolved_count(repo_id, sid)
        return st

    def snapshot_diff(self, repo_id: str, s1: str, s2: str) -> dict:
        def ids_sym(sid: str):
            return {r["symbol_id"] for r in self.conn.execute(
                "SELECT symbol_id FROM snapshot_symbols WHERE repo_id=%s AND"
                " snapshot_id=%s", (repo_id, sid)).fetchall()}

        def ids_edge(sid: str):
            return {r["id"] for r in self.conn.execute(
                "SELECT id FROM snapshot_edges WHERE repo_id=%s AND"
                " snapshot_id=%s", (repo_id, sid)).fetchall()}

        n1, n2 = ids_sym(s1), ids_sym(s2)
        e1, e2 = ids_edge(s1), ids_edge(s2)
        added_nodes = n2 - n1
        removed_nodes = n1 - n2
        added_edges = e2 - e1
        removed_edges = e1 - e2
        changed_files: set[str] = set()
        for nid in added_nodes | removed_nodes:
            for sid in (s1, s2):
                r = self.conn.execute(
                    "SELECT path FROM snapshot_symbols WHERE symbol_id=%s AND"
                    " snapshot_id=%s", (nid, sid)).fetchone()
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
        nodes = [dict(n) for n in self.all_nodes(repo_id, snapshot_id)]
        if view == "architecture":
            keep = {"REPOSITORY", "DIRECTORY", "FILE", "PACKAGE", "MODULE",
                    "SERVICE", "SUBMODULE"}
            nodes = [n for n in nodes if n["kind"] in keep]
            edges = [dict(r) for r in self.conn.execute(
                _EDGE_SEL + " WHERE e.repo_id=%s AND e.snapshot_id=%s AND"
                " e.kind IN ('CONTAINS','IMPORTS','INCLUDES','DEPENDS_ON')"
                " LIMIT %s", (repo_id, snapshot_id, max_edges)).fetchall()]
        elif view == "symbol":
            keep = {"CLASS", "TYPE", "INTERFACE", "FUNCTION", "METHOD",
                    "PROCEDURE", "SUBROUTINE", "PROGRAM", "VARIABLE",
                    "CONSTANT", "FIELD"}
            nodes = [n for n in nodes if n["kind"] in keep]
            edges = [dict(r) for r in self.conn.execute(
                _EDGE_SEL + " WHERE e.repo_id=%s AND e.snapshot_id=%s AND"
                " e.kind IN ('CALLS','REFERENCES','INHERITS','IMPLEMENTS',"
                "'USES','BINDS_TO','DEFINES') LIMIT %s",
                (repo_id, snapshot_id, max_edges)).fetchall()]
        else:
            edges = [dict(r) for r in self.conn.execute(
                _EDGE_SEL + " WHERE e.repo_id=%s AND e.snapshot_id=%s"
                " LIMIT %s", (repo_id, snapshot_id, max_edges)).fetchall()]
        return {
            "repo": repo_id, "snapshot": snapshot_id, "view": view,
            "nodes": nodes, "edges": edges,
        }

    # ==================================================================
    # Architecture plane (S3, SPEC-P1 §6.2 / §23).  SQLite-shaped rows
    # (uuid columns are cast ::text, JSONB payloads are parsed dicts).
    # R1 model isolation / R5 RESTRICT semantics are DB-enforced here;
    # multi-statement mutations run in one explicit transaction.
    # ==================================================================
    @contextmanager
    def _arch_tx(self) -> Iterator[None]:
        try:
            self.conn.execute("BEGIN")
            yield
            self.conn.execute("COMMIT")
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001 - connection may be broken
                pass
            raise

    def _arch_fetch(self, sql: str, params: tuple) -> Optional[dict]:
        r = self.conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    # -- workspaces / models -------------------------------------------
    def arch_workspaces(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id::text, repo_id, name, description, created_at,"
            " updated_at FROM arch_workspaces ORDER BY created_at")
        return [dict(r) for r in cur.fetchall()]

    def arch_workspace(self, workspace_id: str) -> Optional[dict]:
        return self._arch_fetch(
            "SELECT id::text, repo_id, name, description, created_at,"
            " updated_at FROM arch_workspaces WHERE id=%s",
            (_u(workspace_id),))

    def arch_create_workspace(self, repo_id: str, name: str,
                              description: str = "") -> dict:
        """Create a workspace + its empty AS-IS model atomically (fix1)."""
        wid, mid, now = uuid.uuid4(), uuid.uuid4(), self.now()
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO arch_workspaces(id, repo_id, name, description,"
                " created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (wid, repo_id, name, description, now, now))
            self.conn.execute(
                "INSERT INTO arch_models(id, workspace_id, kind, name,"
                " description, created_at, updated_at) VALUES"
                " (%s,%s,%s,%s,%s,%s,%s)",
                (mid, wid, "as_is", "AS-IS", "", now, now))
        ws = self.arch_workspace(str(wid))
        assert ws is not None
        return ws

    def arch_models(self, workspace_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id::text, workspace_id::text, kind, name, description,"
            " status, parent_model_id::text, base_evidence_snapshot_id,"
            " baseline_schema_version, created_at, updated_at"
            " FROM arch_models WHERE workspace_id=%s"
            " ORDER BY created_at", (_u(workspace_id),))
        return [dict(r) for r in cur.fetchall()]

    def arch_model(self, workspace_id: str, model_id: str) -> Optional[dict]:
        return self._arch_fetch(
            "SELECT id::text, workspace_id::text, kind, name, description,"
            " status, parent_model_id::text, base_evidence_snapshot_id,"
            " baseline_schema_version, created_at, updated_at"
            " FROM arch_models WHERE workspace_id=%s"
            " AND id=%s", (_u(workspace_id), _u(model_id)))

    def arch_model_baseline(self, workspace_id: str,
                            model_id: str) -> Optional[dict]:
        """Frozen fork baseline (R2): parsed baseline doc + metadata."""
        r = self.conn.execute(
            "SELECT baseline_json::text, baseline_schema_version,"
            " base_evidence_snapshot_id FROM arch_models WHERE workspace_id=%s"
            " AND id=%s", (_u(workspace_id), _u(model_id))).fetchone()
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
        mid, now = uuid.uuid4(), self.now()
        idmap: dict[str, uuid.UUID] = {}
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO arch_models(id, workspace_id, kind, name,"
                " description, parent_model_id, base_evidence_snapshot_id,"
                " baseline_json, baseline_schema_version, created_at,"
                " updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (mid, _u(workspace_id), "proposal", name, description,
                 _u(source_model_id), sid, Jsonb(doc), 1, now, now))
            # two-phase: insert components without parents, then link them
            for c in comps:
                nid = uuid.uuid4()
                idmap[c["id"]] = nid
                self.conn.execute(
                    "INSERT INTO arch_components(id, workspace_id, model_id,"
                    " origin_id, kind, name, description, parent_id,"
                    " sort_order, created_at, updated_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (nid, _u(workspace_id), mid, _u(c["id"]), c["kind"],
                     c["name"], c["description"], None, c["sort_order"],
                     now, now))
            for c in comps:
                nid = idmap[c["id"]]
                if c["parent_id"] and c["parent_id"] in idmap:
                    self.conn.execute(
                        "UPDATE arch_components SET parent_id=%s WHERE"
                        " workspace_id=%s AND model_id=%s AND id=%s",
                        (idmap[c["parent_id"]], _u(workspace_id), mid, nid))
            for r in rels:
                self.conn.execute(
                    "INSERT INTO arch_relations(id, workspace_id, model_id,"
                    " kind, src_id, dst_id, label, created_at, updated_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (uuid.uuid4(), _u(workspace_id), mid, r["kind"],
                     idmap[r["src_id"]], idmap[r["dst_id"]], r["label"],
                     now, now))
            for c in comps:
                for m in maps_by.get(c["id"], []):
                    self.conn.execute(
                        "INSERT INTO arch_mappings(id, workspace_id,"
                        " component_id, evidence_entity_type,"
                        " evidence_entity_id, evidence_snapshot_id, note,"
                        " created_at, updated_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (uuid.uuid4(), _u(workspace_id), idmap[c["id"]],
                         m["evidence_entity_type"], m["evidence_entity_id"],
                         m.get("evidence_snapshot_id"), m.get("note", ""),
                         now, now))
            # copy the AS-IS layout over (UI convenience; not baseline data)
            lay = self.arch_layout(workspace_id, source_model_id)
            if lay:
                new_layout = {str(idmap.get(k, k)): v
                              for k, v in lay["layout"].items()}
                self.conn.execute(
                    "INSERT INTO arch_layouts(workspace_id, model_id, layout,"
                    " updated_at) VALUES (%s,%s,%s,%s)"
                    " ON CONFLICT (workspace_id, model_id) DO UPDATE SET"
                    " layout=excluded.layout, updated_at=excluded.updated_at",
                    (_u(workspace_id), mid, Jsonb(new_layout), now))
        row = self.arch_model(workspace_id, str(mid))
        assert row is not None
        return row

    # -- components -----------------------------------------------------
    def arch_components(self, workspace_id: str,
                        model_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id::text, workspace_id::text, model_id::text,"
            " origin_id::text, kind, name, description, parent_id::text,"
            " sort_order, created_at, updated_at FROM arch_components"
            " WHERE workspace_id=%s AND model_id=%s ORDER BY sort_order,"
            " created_at",
            (_u(workspace_id), _u(model_id)))
        return [dict(r) for r in cur.fetchall()]

    def arch_component(self, workspace_id: str, component_id: str,
                       model_id: str | None = None) -> Optional[dict]:
        if model_id is not None:
            return self._arch_fetch(
                "SELECT id::text, workspace_id::text, model_id::text,"
                " origin_id::text, kind, name, description, parent_id::text,"
                " sort_order, created_at, updated_at FROM arch_components"
                " WHERE workspace_id=%s AND model_id=%s AND id=%s",
                (_u(workspace_id), _u(model_id), _u(component_id)))
        return self._arch_fetch(
            "SELECT id::text, workspace_id::text, model_id::text,"
            " origin_id::text, kind, name, description, parent_id::text,"
            " sort_order, created_at, updated_at FROM arch_components"
            " WHERE workspace_id=%s AND id=%s",
            (_u(workspace_id), _u(component_id)))

    def arch_component_children(self, workspace_id: str,
                                component_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id::text, workspace_id::text, model_id::text,"
            " origin_id::text, kind, name, description, parent_id::text,"
            " sort_order, created_at, updated_at FROM arch_components"
            " WHERE workspace_id=%s AND parent_id=%s ORDER BY sort_order,"
            " created_at",
            (_u(workspace_id), _u(component_id)))
        return [dict(r) for r in cur.fetchall()]

    def arch_create_component(self, workspace_id: str, model_id: str,
                              kind: str, name: str, description: str = "",
                              parent_id: str | None = None) -> dict:
        cid, now = uuid.uuid4(), self.now()
        try:
            with self._arch_tx():
                self.conn.execute(
                    "INSERT INTO arch_components(id, workspace_id, model_id,"
                    " kind, name, description, parent_id, created_at,"
                    " updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (cid, _u(workspace_id), _u(model_id), kind, name,
                     description, _u(parent_id) if parent_id else None,
                     now, now))
        except pg_errors.IntegrityError:
            raise ArchError(
                "cross_model_reference",
                "component references an entity outside its model scope",
                {"component_id": str(cid), "parent_id": parent_id})
        row = self.arch_component(workspace_id, str(cid), model_id)
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
            sets.append("kind=%s"), params.append(kind)
        if name is not None:
            sets.append("name=%s"), params.append(name)
        if description is not None:
            sets.append("description=%s"), params.append(description)
        if clear_parent:
            sets.append("parent_id=NULL")
        elif parent_id is not None:
            sets.append("parent_id=%s"), params.append(_u(parent_id))
        if sort_order is not None:
            sets.append("sort_order=%s"), params.append(sort_order)
        if not sets:
            raise ArchError("no_fields", "no update fields provided")
        sets.append("updated_at=%s")
        params.append(self.now())
        params.extend([_u(workspace_id), _u(model_id), _u(component_id)])
        try:
            with self._arch_tx():
                self.conn.execute(
                    "UPDATE arch_components SET " + ", ".join(sets) +
                    " WHERE workspace_id=%s AND model_id=%s AND id=%s",
                    params)
        except pg_errors.IntegrityError:
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
        cid, now = uuid.uuid4(), self.now()
        created: list[dict] = []
        try:
            with self._arch_tx():
                self.conn.execute(
                    "INSERT INTO arch_components(id, workspace_id, model_id,"
                    " kind, name, description, parent_id, created_at,"
                    " updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (cid, _u(workspace_id), _u(model_id), kind, name,
                     description, _u(parent_id) if parent_id else None,
                     now, now))
                for etype, eid, note in entities:
                    mid = uuid.uuid4()
                    self.conn.execute(
                        "INSERT INTO arch_mappings(id, workspace_id,"
                        " component_id, evidence_entity_type,"
                        " evidence_entity_id, note, created_at, updated_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (mid, _u(workspace_id), cid, etype, eid, note,
                         now, now))
                    created.append(self._arch_fetch(
                        "SELECT id::text, workspace_id::text,"
                        " component_id::text, evidence_entity_type,"
                        " evidence_entity_id, evidence_snapshot_id, note,"
                        " created_at, updated_at FROM arch_mappings"
                        " WHERE id=%s", (mid,)))
        except pg_errors.IntegrityError:
            raise ArchError(
                "cross_model_reference",
                "batch create references an entity outside model scope",
                {"component_id": str(cid), "parent_id": parent_id})
        comp = self.arch_component(workspace_id, str(cid), model_id)
        assert comp is not None
        return {"component": comp, "mappings": created}

    def _subtree_rows(self, workspace_id: str, model_id: str,
                      component_id: str) -> list[tuple[str, int]]:
        """(id, depth) of the component and all descendants, deepest first."""
        cur = self.conn.execute(
            "WITH RECURSIVE sub(id, depth) AS ("
            " SELECT id, 0 FROM arch_components"
            "  WHERE workspace_id=%s AND model_id=%s AND id=%s"
            " UNION ALL"
            " SELECT c.id, s.depth + 1 FROM arch_components c"
            "  JOIN sub s ON c.parent_id = s.id"
            "  WHERE c.workspace_id=%s AND c.model_id=%s)"
            " SELECT id::text, depth FROM sub",
            (_u(workspace_id), _u(model_id), _u(component_id),
             _u(workspace_id), _u(model_id)))
        return sorted(((r["id"], r["depth"]) for r in cur.fetchall()),
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
                " WHERE component_id=%s", (_u(component_id),)).fetchone()["n"]
            try:
                with self._arch_tx():
                    self.conn.execute(
                        "DELETE FROM arch_components WHERE workspace_id=%s"
                        " AND id=%s", (_u(workspace_id), _u(component_id)))
            except pg_errors.IntegrityError:
                raise ArchError(
                    "delete_restricted",
                    "component deletion blocked by a database constraint")
            return {"components": 1, "relations": 0, "mappings": n_maps}
        members = [i for i, _ in self._subtree_rows(
            workspace_id, model_id, component_id)]
        if not members:
            return {"components": 0, "relations": 0, "mappings": 0}
        marks = ",".join(["%s"] * len(members))
        n_maps = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM arch_mappings"
            f" WHERE component_id IN ({marks})",
            tuple(_u(i) for i in members)).fetchone()["n"]
        n_rels = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM arch_relations WHERE workspace_id=%s"
            f" AND (src_id IN ({marks}) OR dst_id IN ({marks}))",
            (_u(workspace_id), *(_u(i) for i in members),
             *(_u(i) for i in members))).fetchone()["n"]
        try:
            with self._arch_tx():
                self.conn.execute(
                    f"DELETE FROM arch_relations WHERE workspace_id=%s AND"
                    f" (src_id IN ({marks}) OR dst_id IN ({marks}))",
                    (_u(workspace_id), *(_u(i) for i in members),
                     *(_u(i) for i in members)))
                self.conn.execute(
                    f"DELETE FROM arch_mappings WHERE component_id IN ({marks})",
                    tuple(_u(i) for i in members))
                for cid in members:  # deepest first (parent RESTRICT)
                    self.conn.execute(
                        "DELETE FROM arch_components WHERE id=%s AND"
                        " workspace_id=%s", (_u(cid), _u(workspace_id)))
        except pg_errors.IntegrityError:
            raise ArchError("delete_restricted",
                            "subtree deletion blocked by a constraint")
        return {"components": len(members), "relations": n_rels,
                "mappings": n_maps}

    # -- relations ------------------------------------------------------
    def arch_relations(self, workspace_id: str,
                       model_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id::text, workspace_id::text, model_id::text, kind,"
            " src_id::text, dst_id::text, label, created_at, updated_at"
            " FROM arch_relations WHERE workspace_id=%s AND model_id=%s"
            " ORDER BY created_at", (_u(workspace_id), _u(model_id)))
        return [dict(r) for r in cur.fetchall()]

    def arch_relation(self, workspace_id: str, relation_id: str,
                      model_id: str | None = None) -> Optional[dict]:
        if model_id is not None:
            return self._arch_fetch(
                "SELECT id::text, workspace_id::text, model_id::text, kind,"
                " src_id::text, dst_id::text, label, created_at, updated_at"
                " FROM arch_relations WHERE workspace_id=%s AND model_id=%s"
                " AND id=%s",
                (_u(workspace_id), _u(model_id), _u(relation_id)))
        return self._arch_fetch(
            "SELECT id::text, workspace_id::text, model_id::text, kind,"
            " src_id::text, dst_id::text, label, created_at, updated_at"
            " FROM arch_relations WHERE workspace_id=%s AND id=%s",
            (_u(workspace_id), _u(relation_id)))

    def arch_relations_touching(self, workspace_id: str,
                                component_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id::text, workspace_id::text, model_id::text, kind,"
            " src_id::text, dst_id::text, label, created_at, updated_at"
            " FROM arch_relations WHERE workspace_id=%s AND (src_id=%s OR"
            " dst_id=%s) ORDER BY created_at",
            (_u(workspace_id), _u(component_id), _u(component_id)))
        return [dict(r) for r in cur.fetchall()]

    def arch_create_relation(self, workspace_id: str, model_id: str,
                             kind: str, src_id: str, dst_id: str,
                             label: str | None = None) -> dict:
        rid, now = uuid.uuid4(), self.now()
        try:
            with self._arch_tx():
                self.conn.execute(
                    "INSERT INTO arch_relations(id, workspace_id, model_id,"
                    " kind, src_id, dst_id, label, created_at, updated_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (rid, _u(workspace_id), _u(model_id), kind,
                     _u(src_id), _u(dst_id), label, now, now))
        except pg_errors.IntegrityError:
            raise ArchError(
                "cross_model_reference",
                "relation endpoint references an entity outside this model",
                {"relation_id": str(rid), "src_id": src_id, "dst_id": dst_id})
        row = self.arch_relation(workspace_id, str(rid), model_id)
        assert row is not None
        return row

    def arch_update_relation(self, workspace_id: str, model_id: str,
                             relation_id: str, *,
                             kind: str | None = None,
                             label: str | None = None,
                             clear_label: bool = False) -> dict:
        sets, params = [], []
        if kind is not None:
            sets.append("kind=%s"), params.append(kind)
        if clear_label:
            sets.append("label=NULL")
        elif label is not None:
            sets.append("label=%s"), params.append(label)
        if not sets:
            raise ArchError("no_fields", "no update fields provided")
        sets.append("updated_at=%s")
        params.append(self.now())
        params.extend([_u(workspace_id), _u(model_id), _u(relation_id)])
        self.conn.execute(
            "UPDATE arch_relations SET " + ", ".join(sets) +
            " WHERE workspace_id=%s AND model_id=%s AND id=%s", params)
        row = self.arch_relation(workspace_id, relation_id, model_id)
        assert row is not None
        return row

    def arch_delete_relation(self, workspace_id: str,
                             relation_id: str) -> None:
        with self._arch_tx():
            self.conn.execute(
                "DELETE FROM arch_relations WHERE workspace_id=%s AND id=%s",
                (_u(workspace_id), _u(relation_id)))

    # -- mappings -------------------------------------------------------
    def arch_mappings(self, workspace_id: str, model_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT m.id::text, m.workspace_id::text, m.component_id::text,"
            " m.evidence_entity_type, m.evidence_entity_id,"
            " m.evidence_snapshot_id, m.note, m.created_at, m.updated_at"
            " FROM arch_mappings m JOIN arch_components c"
            " ON c.id = m.component_id"
            " WHERE c.workspace_id=%s AND c.model_id=%s"
            " ORDER BY m.created_at", (_u(workspace_id), _u(model_id)))
        return [dict(r) for r in cur.fetchall()]

    def arch_mappings_for_component(self, workspace_id: str,
                                    component_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id::text, workspace_id::text, component_id::text,"
            " evidence_entity_type, evidence_entity_id,"
            " evidence_snapshot_id, note, created_at, updated_at"
            " FROM arch_mappings WHERE workspace_id=%s AND component_id=%s"
            " ORDER BY created_at", (_u(workspace_id), _u(component_id)))
        return [dict(r) for r in cur.fetchall()]

    def arch_batch_mappings(self, workspace_id: str, component_id: str,
                            entities: list[tuple[str, str, str]]) -> list[dict]:
        """Add mappings to an existing component in one transaction."""
        now = self.now()
        ids: list[uuid.UUID] = []
        try:
            with self._arch_tx():
                for etype, eid, note in entities:
                    mid = uuid.uuid4()
                    ids.append(mid)
                    self.conn.execute(
                        "INSERT INTO arch_mappings(id, workspace_id,"
                        " component_id, evidence_entity_type,"
                        " evidence_entity_id, note, created_at, updated_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (mid, _u(workspace_id), _u(component_id), etype, eid,
                         note, now, now))
        except pg_errors.IntegrityError:
            raise ArchError(
                "mapping_exists",
                "one of the mappings already exists for this component")
        if not ids:
            return []
        marks = ",".join(["%s"] * len(ids))
        cur = self.conn.execute(
            "SELECT id::text, workspace_id::text, component_id::text,"
            " evidence_entity_type, evidence_entity_id,"
            " evidence_snapshot_id, note, created_at, updated_at"
            f" FROM arch_mappings WHERE id IN ({marks})", tuple(ids))
        return [dict(r) for r in cur.fetchall()]

    def arch_delete_mapping(self, workspace_id: str,
                            mapping_id: str) -> None:
        with self._arch_tx():
            self.conn.execute(
                "DELETE FROM arch_mappings WHERE workspace_id=%s AND id=%s",
                (_u(workspace_id), _u(mapping_id)))

    def arch_mapping_exists(self, workspace_id: str, component_id: str,
                            entity_type: str, entity_id: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM arch_mappings WHERE component_id=%s"
            " AND evidence_entity_type=%s AND evidence_entity_id=%s",
            (_u(component_id), entity_type, entity_id)).fetchone()
        return r is not None

    # -- layout ---------------------------------------------------------
    def arch_layout(self, workspace_id: str,
                    model_id: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT layout::text, updated_at FROM arch_layouts"
            " WHERE workspace_id=%s AND model_id=%s",
            (_u(workspace_id), _u(model_id))).fetchone()
        if not r:
            return None
        try:
            layout = json.loads(r["layout"])
        except ValueError:
            layout = {}
        return {"layout": layout if isinstance(layout, dict) else {},
                "updated_at": r["updated_at"]}

    def arch_put_layout(self, workspace_id: str, model_id: str,
                        layout: dict,
                        expected_updated_at: int | None = None) -> int:
        now = self.now()
        cur = self.conn.execute(
            "SELECT updated_at FROM arch_layouts WHERE workspace_id=%s AND"
            " model_id=%s", (_u(workspace_id), _u(model_id))).fetchone()
        if cur is not None and expected_updated_at is not None and \
                cur["updated_at"] != expected_updated_at:
            raise ArchError(
                "layout_conflict", "layout was updated elsewhere",
                {"current_updated_at": cur["updated_at"]})
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO arch_layouts(workspace_id, model_id, layout,"
                " updated_at) VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (workspace_id, model_id) DO UPDATE SET"
                " layout=excluded.layout, updated_at=excluded.updated_at",
                (_u(workspace_id), _u(model_id), Jsonb(layout), now))
        return now

    # ==================================================================
    # Software circuit plane (P2-FLOW0, SPEC-P2 §3/§19) — UUID ids,
    # JSONB payloads mapped to SQLite-shaped rows (meta_json etc).
    # ==================================================================
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
        where = " WHERE repo_id=%s" if repo_id else ""
        params = (repo_id,) if repo_id else ()
        return [_srow(r) for r in self.conn.execute(
            f"SELECT {self.MODEL_COLS}, meta::text AS meta_json FROM"
            " flow_models" + where + " ORDER BY created_at",
            params).fetchall()]

    def flow_model(self, flow_id: str) -> Optional[dict]:
        r = self.conn.execute(
            f"SELECT {self.MODEL_COLS}, meta::text AS meta_json FROM"
            " flow_models WHERE id=%s", (_u(flow_id),)).fetchone()
        return _srow(r) if r else None

    def flow_create_model(self, repo_id: str, name: str, snapshot_id: str, *,
                          workspace_id: str | None = None,
                          architecture_model_id: str | None = None,
                          scope_symbol_id: str | None = None,
                          meta: dict | None = None) -> dict:
        fid, now = str(uuid.uuid4()), self.now()
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO flow_models(id, workspace_id,"
                " architecture_model_id, repo_id, name, scope_symbol_id,"
                " snapshot_id, status, version, created_at, updated_at, meta)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (fid, workspace_id, architecture_model_id, repo_id, name,
                 scope_symbol_id, snapshot_id, "active", 1, now, now,
                 Jsonb(meta or {})))
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
        with self._arch_tx():
            self.conn.execute(
                "UPDATE flow_models SET name=%s, status=%s, root_block_id=%s,"
                " version=version+1, updated_at=%s, meta=%s WHERE id=%s",
                (name if name is not None else m["name"],
                 status if status is not None else m["status"],
                 _u(root_block_id) if root_block_id is not None
                 else m["root_block_id"],
                 self.now(),
                 Jsonb(meta if meta is not None
                       else json.loads(m.get("meta_json") or "{}")),
                 _u(flow_id)))
        m2 = self.flow_model(flow_id)
        assert m2 is not None
        return m2

    # -- blocks ----------------------------------------------------------
    def flow_blocks(self, flow_id: str) -> list[dict]:
        return [_srow(r) for r in self.conn.execute(
            f"SELECT {self.BLOCK_COLS}, meta::text AS meta_json FROM"
            " flow_blocks WHERE flow_model_id=%s ORDER BY created_at",
            (_u(flow_id),)).fetchall()]

    def flow_block(self, flow_id: str, block_id: str) -> Optional[dict]:
        r = self.conn.execute(
            f"SELECT {self.BLOCK_COLS}, meta::text AS meta_json FROM"
            " flow_blocks WHERE flow_model_id=%s AND id=%s",
            (_u(flow_id), _u(block_id))).fetchone()
        return _srow(r) if r else None

    def flow_create_block(self, flow_id: str, *, kind: str, name: str,
                          state: str = "proposed",
                          parent_block_id: str | None = None,
                          code: str | None = None,
                          meta: dict | None = None) -> dict:
        bid, now = str(uuid.uuid4()), self.now()
        with self._arch_tx():
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
                " kind, name, state, code, meta, created_at, updated_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (bid, _u(flow_id), _u(parent_block_id), kind, name, state,
                 code, Jsonb(meta or {}), now, now))
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
        with self._arch_tx():
            self.conn.execute(
                "UPDATE flow_blocks SET name=%s, kind=%s, state=%s,"
                " parent_block_id=%s, code=%s, meta=%s, updated_at=%s WHERE"
                " flow_model_id=%s AND id=%s",
                (name if name is not None else b["name"],
                 kind if kind is not None else b["kind"],
                 state if state is not None else b["state"],
                 _u(new_parent),
                 code if code is not None else b["code"],
                 Jsonb(meta if meta is not None
                       else json.loads(b.get("meta_json") or "{}")),
                 self.now(), _u(flow_id), _u(block_id)))
        b2 = self.flow_block(flow_id, block_id)
        assert b2 is not None
        return b2

    def flow_delete_block(self, flow_id: str, block_id: str) -> None:
        with self._arch_tx():
            row = self.conn.execute(
                "SELECT parent_block_id FROM flow_blocks WHERE"
                " flow_model_id=%s AND id=%s",
                (_u(flow_id), _u(block_id))).fetchone()
            parent = row["parent_block_id"] if row else None
            # children of the deleted block move up to ITS parent
            self.conn.execute(
                "UPDATE flow_blocks SET parent_block_id=%s, updated_at=%s"
                " WHERE flow_model_id=%s AND parent_block_id=%s",
                (parent, self.now(), _u(flow_id), _u(block_id)))
            self.conn.execute(
                "DELETE FROM flow_blocks WHERE flow_model_id=%s AND id=%s",
                (_u(flow_id), _u(block_id)))

    # -- ports -----------------------------------------------------------
    def flow_ports(self, flow_id: str) -> list[dict]:
        return [_srow(r) for r in self.conn.execute(
            f"SELECT {self.PORT_COLS}, meta::text AS meta_json FROM"
            " flow_ports WHERE flow_model_id=%s"
            " ORDER BY block_id, position_order",
            (_u(flow_id),)).fetchall()]

    def flow_port(self, flow_id: str, port_id: str) -> Optional[dict]:
        r = self.conn.execute(
            f"SELECT {self.PORT_COLS}, meta::text AS meta_json FROM"
            " flow_ports WHERE flow_model_id=%s AND id=%s",
            (_u(flow_id), _u(port_id))).fetchone()
        return _srow(r) if r else None

    def flow_create_port(self, flow_id: str, *, block_id: str, name: str,
                         direction: str, semantic_kind: str,
                         code_type: str | None = None,
                         position_order: int = 0,
                         meta: dict | None = None) -> dict:
        pid, now = str(uuid.uuid4()), self.now()
        with self._arch_tx():
            block = self.flow_block(flow_id, block_id)
            if not block:
                raise FlowError("block_not_in_flow", "block not in flow",
                                {"block_id": block_id})
            for p in self.flow_ports(flow_id):
                if p["block_id"] == block_id and p["name"] == name:
                    raise FlowError(
                        "port_name_exists", "port name already exists in block",
                        {"block_id": block_id, "port": name})
            self.conn.execute(
                "INSERT INTO flow_ports(id, flow_model_id, block_id, name,"
                " direction, semantic_kind, code_type, position_order, meta,"
                " created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (pid, _u(flow_id), _u(block_id), name, direction,
                 semantic_kind, code_type, position_order, Jsonb(meta or {}),
                 now))
        p = self.flow_port(flow_id, pid)
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
        with self._arch_tx():
            self.conn.execute(
                "UPDATE flow_ports SET name=%s, semantic_kind=%s,"
                " code_type=%s, position_order=%s, meta=%s WHERE"
                " flow_model_id=%s AND id=%s",
                (name if name is not None else p["name"],
                 semantic_kind if semantic_kind is not None
                 else p["semantic_kind"],
                 None if clear_type else (
                     code_type if code_type is not None else p["code_type"]),
                 position_order if position_order is not None
                 else p["position_order"],
                 Jsonb(meta if meta is not None
                       else json.loads(p.get("meta_json") or "{}")),
                 _u(flow_id), _u(port_id)))
        p2 = self.flow_port(flow_id, port_id)
        assert p2 is not None
        return p2

    def flow_delete_port(self, flow_id: str, port_id: str) -> None:
        with self._arch_tx():
            self.conn.execute(
                "DELETE FROM flow_ports WHERE flow_model_id=%s AND id=%s",
                (_u(flow_id), _u(port_id)))

    # -- nets ------------------------------------------------------------
    def flow_nets(self, flow_id: str) -> list[dict]:
        return [_srow(r) for r in self.conn.execute(
            f"SELECT {self.NET_COLS}, meta::text AS meta_json FROM"
            " flow_nets WHERE flow_model_id=%s ORDER BY created_at",
            (_u(flow_id),)).fetchall()]

    def flow_create_net(self, flow_id: str, *, source_port_id: str,
                        target_port_id: str, kind: str = "control",
                        label: str | None = None,
                        meta: dict | None = None) -> dict:
        nid, now = str(uuid.uuid4()), self.now()
        with self._arch_tx():
            src = self.flow_port(flow_id, source_port_id)
            dst = self.flow_port(flow_id, target_port_id)
            if not src or not dst:
                raise FlowError("port_not_in_flow",
                                "net endpoint port not in flow",
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
                " target_port_id, kind, label, meta, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (nid, _u(flow_id), _u(source_port_id), _u(target_port_id),
                 kind, label, Jsonb(meta or {}), now))
        n = self.conn.execute(
            f"SELECT {self.NET_COLS}, meta::text AS meta_json FROM flow_nets"
            " WHERE id=%s", (_u(nid),)).fetchone()
        assert n is not None
        return _srow(n)

    def flow_delete_net(self, flow_id: str, net_id: str) -> None:
        with self._arch_tx():
            self.conn.execute(
                "DELETE FROM flow_nets WHERE flow_model_id=%s AND id=%s",
                (_u(flow_id), _u(net_id)))

    def flow_net(self, flow_id: str, net_id: str) -> Optional[dict]:
        r = self.conn.execute(
            f"SELECT {self.NET_COLS}, meta::text AS meta_json FROM"
            " flow_nets WHERE flow_model_id=%s AND id=%s",
            (_u(flow_id), _u(net_id))).fetchone()
        return _srow(r) if r else None

    def flow_update_net(self, flow_id: str, net_id: str, *,
                        kind: str | None = None,
                        label: str | None = None,
                        clear_label: bool = False,
                        meta: dict | None = None) -> dict:
        n = self.flow_net(flow_id, net_id)
        if not n:
            raise FlowError("net_not_found", "net not found",
                            {"net_id": net_id})
        with self._arch_tx():
            self.conn.execute(
                "UPDATE flow_nets SET kind=%s, label=%s, meta=%s WHERE"
                " flow_model_id=%s AND id=%s",
                (kind if kind is not None else n["kind"],
                 None if clear_label else (
                     label if label is not None else n["label"]),
                 Jsonb(meta if meta is not None
                       else json.loads(n.get("meta_json") or "{}")),
                 _u(flow_id), _u(net_id)))
        n2 = self.flow_net(flow_id, net_id)
        assert n2 is not None
        return n2

    # -- bindings --------------------------------------------------------
    def flow_bindings(self, flow_id: str) -> list[dict]:
        return [_srow(r) for r in self.conn.execute(
            f"SELECT {self.BIND_COLS} FROM flow_bindings"
            " WHERE flow_model_id=%s ORDER BY created_at",
            (_u(flow_id),)).fetchall()]

    def flow_put_binding(self, flow_id: str, *, block_id: str,
                         snapshot_id: str, canonical_symbol_id: str,
                         binding_kind: str = "implementation") -> dict:
        now = self.now()
        with self._arch_tx():
            block = self.flow_block(flow_id, block_id)
            if not block:
                raise FlowError("block_not_in_flow", "block not in flow",
                                {"block_id": block_id})
            self.conn.execute(
                "INSERT INTO flow_bindings(id, flow_model_id, block_id,"
                " snapshot_id, canonical_symbol_id, binding_kind, created_at,"
                " updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (flow_model_id, block_id) DO UPDATE SET"
                " snapshot_id=excluded.snapshot_id,"
                " canonical_symbol_id=excluded.canonical_symbol_id,"
                " binding_kind=excluded.binding_kind,"
                " updated_at=excluded.updated_at",
                (str(uuid.uuid4()), _u(flow_id), _u(block_id), snapshot_id,
                 canonical_symbol_id, binding_kind, now, now))
        for b in self.flow_bindings(flow_id):
            if b["block_id"] == block_id:
                return b
        raise FlowError("binding_failed", "binding write failed")

    def flow_clear_binding(self, flow_id: str, block_id: str) -> None:
        with self._arch_tx():
            self.conn.execute(
                "DELETE FROM flow_bindings WHERE flow_model_id=%s AND"
                " block_id=%s", (_u(flow_id), _u(block_id)))

    # -- layout ----------------------------------------------------------
    def flow_layout(self, flow_id: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT layout::text AS layout_json, updated_at FROM"
            " flow_layouts WHERE flow_model_id=%s",
            (_u(flow_id),)).fetchone()
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
        with self._arch_tx():
            self.conn.execute(
                "INSERT INTO flow_layouts(flow_model_id, layout, updated_at)"
                " VALUES (%s,%s,%s) ON CONFLICT (flow_model_id) DO UPDATE SET"
                " layout=excluded.layout, updated_at=excluded.updated_at",
                (_u(flow_id), Jsonb(layout), now))
        return now

    # -- evidence lookups (mapping validation / resolution) -------------
    def entity_exists(self, repo_id: str, snapshot_id: str,
                      entity_type: str, entity_id: str) -> bool:
        if entity_type == "node":
            r = self.conn.execute(
                "SELECT 1 FROM snapshot_symbols WHERE repo_id=%s AND"
                " snapshot_id=%s AND symbol_id=%s",
                (repo_id, snapshot_id, entity_id)).fetchone()
        elif entity_type == "edge":
            r = self.conn.execute(
                "SELECT 1 FROM snapshot_edges WHERE repo_id=%s AND"
                " snapshot_id=%s AND id=%s",
                (repo_id, snapshot_id, entity_id)).fetchone()
        else:
            return False
        return r is not None

    def entity_row(self, repo_id: str, snapshot_id: str,
                   entity_type: str, entity_id: str) -> Optional[dict]:
        if entity_type == "node":
            r = self.conn.execute(
                _NODE_SEL + " WHERE ss.repo_id=%s AND ss.snapshot_id=%s AND"
                " ss.symbol_id=%s",
                (repo_id, snapshot_id, entity_id)).fetchone()
            if not r:
                return None
            d = dict(r)
            return {k: d[k] for k in (
                "id", "kind", "name", "qname", "language", "path",
                "start_line", "start_col", "end_line", "end_col")}
        if entity_type == "edge":
            r = self.conn.execute(
                _EDGE_SEL + " WHERE e.repo_id=%s AND e.snapshot_id=%s AND"
                " e.id=%s", (repo_id, snapshot_id, entity_id)).fetchone()
            if not r:
                return None
            d = dict(r)
            return {k: d[k] for k in ("id", "kind", "src_id", "dst_id",
                                      "confidence")}
        return None
