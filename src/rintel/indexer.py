"""Indexer: repository → canonical evidence graph (spec §1, §26, §31, §32).

Pipeline (full index):
  scan files → hash → parse (adapters) → store facts →
  resolution pass (identity resolution for every symbolic ref) →
  synthetic containment nodes → snapshot commit → telemetry.

Incremental (spec §26):
  only files whose hash changed are reparsed; unchanged facts are copied into
  the new snapshot; dependency manifests widen invalidation only to real
  consumers; resolution-derived edges and projections are reconciled only for
  the invalidated closure.  Provider semantic digests stop comment/metadata
  changes before canonical propagation.  Nothing is silently fabricated:
  every unresolvable reference is retained in `unresolved` + telemetry.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .adapters import adapter_for_path
from .adapters.base import ParseError
from .db import Database
from .analysis.contract import Coverage, ExecutionModality, TargetResolution, TruthClass
from .evidence_authority import (
    CanonicalStateRef, ClaimPayload, DEFAULT_ENGINE, EvidenceEnvelope,
    envelope_from_wire,
)
from .eq0 import classify_unresolved
from .identity import (SYMBOL_IDENTITY_SCHEMA_VERSION, py_module_qname,
                       ts_module_qname)
from .model import (Callsite, EdgeSpec, ImportBinding, IncludeBinding, Node,
                    ParsedFile, Ref, REF_MODULE, REF_NAME, REF_QNAME)
from .store import Store
from .tsutil import grammar_version

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
             "venv", "dist", "build", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", ".tox", ".idea", ".vscode", "target", ".next",
             ".uv-cache", ".uv-python", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
            ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz",
            ".woff", ".woff2", ".ttf", ".lock", ".min.js", ".map"}
MAX_FILE_BYTES = 4 * 1024 * 1024

CALL_SRC_KINDS = ("MODULE", "PACKAGE", "PROGRAM", "SUBMODULE", "CLASS",
                  "FUNCTION", "METHOD", "SUBROUTINE", "INTERFACE")
CTOR_SUFFIX = {"python": ".__init__", "typescript": ".constructor",
               "javascript": ".constructor", "java": ".<init>"}


@dataclass
class IndexResult:
    run_id: str
    repo_id: str
    snapshot_id: str
    incremental: bool
    files_total: int = 0
    files_parsed: int = 0
    files_unchanged: int = 0
    files_failed: int = 0
    nodes_added: int = 0
    nodes_merged: int = 0
    edges_added: int = 0
    callsites: int = 0
    callsites_resolved: int = 0
    callsites_unresolved: int = 0
    unresolved_notes: int = 0
    duration_ms: int = 0
    languages: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    provider_reanalyzed_files: int = 0
    canonical_changed_facts: int = 0
    canonical_reconsidered_facts: int = 0
    canonical_reused_facts: int = 0
    projections_recomputed: int = 0
    projections_reused: int = 0
    cutoff_reason: str | None = None
    changed_files: list[str] = field(default_factory=list)
    invalidated_files: list[str] = field(default_factory=list)
    dependency_reasons: dict[str, list[str]] = field(default_factory=dict)
    projection_statuses: dict[str, dict] = field(default_factory=dict)


class Indexer:
    def __init__(self, db: Store, repo_root: str, repo_id: str | None = None,
                 force: bool = False, commit: str | None = None,
                 build_context_id: str = "build-context:default",
                 provider_id: str = "rintel.builtin",
                 provider_version: str | None = None,
                 provider_config_digest: str = "sha256:default"):
        self.db = db
        self.repo_root = str(Path(repo_root).resolve())
        self.repo_id = repo_id or Path(self.repo_root).name
        self.force = force
        self.commit = commit
        self.run_id = uuid.uuid4().hex[:12]
        self.parser_version = f"tree_sitter:{grammar_version('python')}"
        self.indexer_version = f"rintel:{__version__}"
        self.build_context_id = build_context_id
        self.provider_id = provider_id
        self.provider_version = provider_version or self.parser_version
        self.provider_config_digest = provider_config_digest
        self._support_sequence = 0
        self._support_state: CanonicalStateRef | None = None

    def _analysis_identity(self) -> dict[str, str]:
        return {
            "repo_snapshot": self.commit or "working-tree",
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "provider_config_digest": self.provider_config_digest,
            "build_context_id": self.build_context_id,
            "semantic_identity_schema_version": SYMBOL_IDENTITY_SCHEMA_VERSION,
            "indexer_version": self.indexer_version,
            "authority_registry_version": DEFAULT_ENGINE.registry.version,
            "authority_rule_version": DEFAULT_ENGINE.rule_version,
        }

    def _admitted_support(self, *, sid: str, fact_id: str, fact_kind: str,
                          fact_type: str, owner_path: str, subject: str,
                          predicate: str, target: object,
                          source_ids: tuple[str, str] | None = None,
                          span: dict | None = None,
                          truth: TruthClass = TruthClass.INFERRED,
                          resolution: TargetResolution = TargetResolution.CANDIDATE_SET,
                          modality: ExecutionModality = ExecutionModality.UNKNOWN,
                          origin: str) -> tuple[object, object]:
        """Admit one actual parser/resolver input before its graph write."""
        if self._support_state is None:
            raise RuntimeError("canonical support state not initialized")
        self._support_sequence += 1
        source_state = self.db.file_state(self.repo_id, owner_path) if owner_path else None
        source_digest = source_state.get("hash") if source_state else None
        claim = ClaimPayload(
            claim_id=f"{self.run_id}:fact:{self._support_sequence}",
            subject=subject, predicate=predicate, object=target,
            source_span=span, truth_class=truth, resolution=resolution,
            coverage=Coverage.UNKNOWN, execution_modality=modality,
            revision_input=self.commit or f"index-run:{self.run_id}",
            object_is_identity=fact_type == "edge",
            witness={"origin": origin, "fact_kind": fact_kind})
        envelope = EvidenceEnvelope.for_claim(
            lane_id="legacy_builtin", producer="legacy-builtin-rintel-indexer",
            producer_version=self.provider_version,
            scope=(owner_path,) if owner_path else (self.repo_id,),
            provenance={"run_id": self.run_id, "origin": origin,
                        "provider_id": self.provider_id,
                        "provider_config_digest": self.provider_config_digest,
                        "source_digest": source_digest},
            limitations=("legacy analyzer coverage is not COMPLETE",),
            claim=claim)
        admission = DEFAULT_ENGINE.admit(envelope, self._support_state)
        receipt = DEFAULT_ENGINE.issue_fact_support(
            admission, envelope, canonical_revision=sid,
            canonical_fact_id=fact_id, canonical_fact_kind=fact_kind,
            canonical_fact_type=fact_type, owner_path=owner_path,
            build_context_id=self.build_context_id, source_digest=source_digest,
            source_ids=source_ids)
        return admission, receipt

    def _upsert_supported_node(self, node: Node, sid: str, *,
                               owner_path: str | None = None,
                               origin: str = "parser_node") -> tuple[str, bool]:
        path = node.path if owner_path is None else owner_path
        _admission, receipt = self._admitted_support(
            sid=sid, fact_id=node.canonical_id, fact_kind=node.kind,
            fact_type="node", owner_path=path, subject=node.canonical_id,
            predicate="NODE", target={"kind": node.kind, "qname": node.qname},
            span={"file": path, "start_line": node.start_line} if path else None,
            truth=TruthClass.OBSERVED if origin == "parser_node" else TruthClass.INFERRED,
            resolution=TargetResolution.EXACT, origin=origin)
        identity, created = self.db.upsert_node(node, self.repo_id, sid)
        if identity != node.canonical_id:
            raise RuntimeError("reconciled node identity differs from admitted claim")
        self.db.add_support_receipt(self.repo_id, sid, receipt)
        return identity, created

    def _add_supported_edge(self, kind: str, src_id: str, dst_id: str,
                            sid: str, *, owner_path: str, origin: str,
                            line: int | None = None, confidence: float = 1.0,
                            meta: dict | None = None,
                            evidence: dict | None = None,
                            resolution: TargetResolution = TargetResolution.CANDIDATE_SET
                            ) -> bool:
        fact_id = f"edge:{kind}:{src_id}:{dst_id}"
        _admission, receipt = self._admitted_support(
            sid=sid, fact_id=fact_id, fact_kind=kind, fact_type="edge",
            owner_path=owner_path, subject=src_id, predicate=kind,
            target=dst_id, source_ids=(src_id, dst_id),
            span={"file": owner_path, "start_line": line} if owner_path else None,
            truth=TruthClass.INFERRED, resolution=resolution,
            modality=ExecutionModality.MAY if kind == "CALLS" else
            ExecutionModality.UNKNOWN, origin=origin)
        created = self.db.add_edge_raw(
            kind, src_id, dst_id, self.repo_id, sid, confidence=confidence,
            meta=meta, evidence=evidence)
        self.db.add_support_receipt(self.repo_id, sid, receipt)
        return created

    def _reattest_support_after_identity_delta(
            self, parent: str, sid: str, paths: list[str]) -> None:
        """Fresh provider identity + unchanged semantic digest reattests exact claims.

        This is not name-based reconstruction: the previous receipt stores
        the admitted envelope and exact fact binding, while this run freshly
        parsed the same raw source and compared its semantic digest.
        """
        prior = self.db.support_receipts_owned_by_paths(self.repo_id, parent, paths)
        self.db.delete_support_owned_by_paths(self.repo_id, sid, paths)
        for old in prior:
            if (old.get("lane_id") != "legacy_builtin" or
                    old.get("producer") != "legacy-builtin-rintel-indexer" or
                    not old.get("admitted_envelope")):
                raise RuntimeError("identity delta cannot reattest foreign support")
            owner_path = old["owner_path"]
            state = self.db.file_state(self.repo_id, owner_path)
            if not state or state.get("hash") != old.get("source_digest"):
                raise RuntimeError("identity delta support source drift")
            wire = dict(old["admitted_envelope"])
            wire["producer_version"] = self.provider_version
            wire["provenance"] = {
                **wire["provenance"], "run_id": self.run_id,
                "provider_id": self.provider_id,
                "provider_config_digest": self.provider_config_digest,
                "reattest_source_receipt_id": old["support_receipt_id"],
            }
            wire["claim"] = {
                **wire["claim"],
                "claim_id": f"{self.run_id}:reattest:{old['evidence_id']}",
                "revision_input": self.commit or f"index-run:{self.run_id}",
            }
            envelope = envelope_from_wire(wire)
            admission = DEFAULT_ENGINE.admit(envelope, self._support_state)
            receipt = DEFAULT_ENGINE.issue_fact_support(
                admission, envelope, canonical_revision=sid,
                canonical_fact_id=old["canonical_fact_id"],
                canonical_fact_kind=old["canonical_fact_kind"],
                canonical_fact_type=old["canonical_fact_type"],
                owner_path=owner_path, build_context_id=self.build_context_id,
                source_digest=state["hash"],
                source_ids=tuple(old["source_ids"]) if old["source_ids"] else None,
                reattest_source_receipt_id=old["support_receipt_id"])
            self.db.add_support_receipt(self.repo_id, sid, receipt)

    # ------------------------------------------------------------------
    def index(self, on_progress: Callable[[int, str], None] | None = None) -> IndexResult:
        """Run one atomic index transaction.

        A failed provider, resolver, projection, database operation, or cancel
        cannot make a staging snapshot CURRENT.
        """
        try:
            return self._index_transaction(on_progress)
        except BaseException:
            self.db.rollback()
            raise

    def _index_transaction(
            self, on_progress: Callable[[int, str], None] | None = None
    ) -> IndexResult:
        """Full/incremental index of `repo_root`.

        `on_progress(percent, phase)` is an optional progress hook for the
        job runner (SPEC-P1 §5 `server/jobs.py`); behaviour-neutral when
        omitted.
        """
        def tick(pct: int, phase: str) -> None:
            if on_progress is not None:
                on_progress(pct, phase)

        t0 = time.time()
        self.db.upsert_repo(self.repo_id, self.repo_root)
        self.db.begin(self.repo_id)   # PG: single tx + advisory xact lock (SPEC-P1 §6.1-5)
        res = IndexResult(run_id=self.run_id, repo_id=self.repo_id,
                          snapshot_id="", incremental=False)
        files = self._scan()
        tick(5, "scan")
        res.files_total = len(files)
        parent = self.db.current_snapshot(self.repo_id)
        self._support_state = CanonicalStateRef(
            parent or "EMPTY", f"snapshot-ref:{parent or 'EMPTY'}")
        analysis_identity = self._analysis_identity()
        identity_changes: set[str] = set()
        if parent and not self.force:
            res.incremental = True
            changed, unchanged = self._diff_files(files)
            res.changed_files = sorted(f[0] for f in changed)
            parent_row = self.db.snapshot(parent) or {}
            try:
                parent_meta = json.loads(parent_row.get("meta_json") or "{}")
            except (TypeError, ValueError):
                parent_meta = {}
            previous_identity = parent_meta.get("analysis_identity", {})
            identity_changes = {
                key for key, value in analysis_identity.items()
                if previous_identity.get(key) != value
            }
            # A key-schema transition cannot copy v1 rows into a v2 snapshot.
            # Rebuild from source while keeping the published v1 snapshot and
            # its evidence immutable until the new revision is atomic.
            if "semantic_identity_schema_version" in identity_changes:
                res.incremental = False
                changed = files
                res.changed_files = sorted(f[0] for f in files)
                unchanged = []
            tick(15, "diff")
            res.files_unchanged = len(unchanged)
            if not changed and not identity_changes:
                res.snapshot_id = parent
                res.cutoff_reason = "input_identity_unchanged"
                self._telemetry(res, "index", "noop", {"reason": "no file changed"})
                res.duration_ms = int((time.time() - t0) * 1000)
                tick(100, "noop")
                self.db.commit()
                return res
        else:
            changed = files
            res.changed_files = sorted(f[0] for f in changed)

        # Analyze changed provider inputs before allocating a canonical
        # revision.  A content hash change is not necessarily a semantic
        # change (comments, whitespace, or syntax the provider intentionally
        # does not model).  This is the earliest safe cutoff boundary.
        prepared: dict[str, tuple[ParsedFile, str]] = {}
        all_semantically_unchanged = bool(
            parent and res.incremental and changed and not identity_changes)
        for relpath, source, adapter in changed:
            try:
                parsed = adapter.parse(relpath, source)
            except ParseError:
                all_semantically_unchanged = False
                continue
            digest = self._semantic_digest(parsed)
            prepared[relpath] = (parsed, digest)
            previous = self.db.file_state(self.repo_id, relpath)
            if previous is None or previous.get("semantic_digest") != digest \
                    or previous.get("parser_version") != self.parser_version \
                    or previous.get("indexer_version") != self.indexer_version:
                all_semantically_unchanged = False

        if all_semantically_unchanged:
            for relpath, source, _adapter in changed:
                _parsed, digest = prepared[relpath]
                raw = source.encode("utf-8")
                self.db.upsert_file(
                    self.repo_id, relpath, hashlib.sha256(raw).hexdigest(),
                    len(raw), self.parser_version, self.indexer_version,
                    status="ok", semantic_digest=digest)
            res.snapshot_id = parent
            res.files_parsed = len(changed)
            res.provider_reanalyzed_files = len(changed)
            res.cutoff_reason = "semantic_digest_unchanged"
            res.duration_ms = int((time.time() - t0) * 1000)
            self._telemetry(res, "index", "semantic_cutoff", {
                "changed_inputs": len(changed), "snapshot": parent})
            self.db.commit()
            tick(100, "semantic_cutoff")
            return res

        if parent and res.incremental:
            file_by_path = {item[0]: item for item in files}
            seeds = set(res.changed_files)
            if identity_changes:
                if identity_changes == {"build_context_id"}:
                    compiled = {"c", "cpp", "fortran"}
                    seeds.update(rel for rel, _source, adapter in files
                                 if adapter.language in compiled)
                else:
                    seeds.update(file_by_path)
            # Dependency expansion must compare old definitions with the
            # actual provider output for every seed.  Treating an unparsed
            # context-invalidated file as "all definitions removed" would
            # spuriously invalidate symbolic consumers in other languages.
            for relpath in sorted(seeds):
                if relpath in prepared or relpath not in file_by_path:
                    continue
                _path, source, adapter = file_by_path[relpath]
                try:
                    parsed = adapter.parse(relpath, source)
                except ParseError:
                    continue
                prepared[relpath] = (parsed, self._semantic_digest(parsed))
            invalidated, reasons = self._dependency_closure(
                parent, seeds, prepared)
            changed = [file_by_path[p] for p in sorted(invalidated)
                       if p in file_by_path]
            res.invalidated_files = [item[0] for item in changed]
            res.dependency_reasons = reasons
            for relpath, source, adapter in changed:
                if relpath in prepared:
                    continue
                try:
                    parsed = adapter.parse(relpath, source)
                except ParseError:
                    continue
                prepared[relpath] = (parsed, self._semantic_digest(parsed))
        else:
            res.invalidated_files = sorted(f[0] for f in changed)
        identity_semantic_reuse = False
        if parent and res.incremental:
            res.projection_statuses = self._projection_plan(
                parent, res.invalidated_files, prepared)
            res.projections_recomputed = sum(
                len(item["dirty_scope"])
                for item in res.projection_statuses.values())
            res.projections_reused = max(
                len(res.projection_statuses) * res.files_total
                - res.projections_recomputed, 0)
            if identity_changes:
                semantic_delta = []
                for relpath, _source, _adapter in changed:
                    parsed_and_digest = prepared.get(relpath)
                    previous = self.db.file_state(self.repo_id, relpath)
                    if parsed_and_digest is None or previous is None or \
                            previous.get("semantic_digest") != parsed_and_digest[1]:
                        semantic_delta.append(relpath)
                identity_semantic_reuse = not semantic_delta
                if identity_semantic_reuse:
                    for status in res.projection_statuses.values():
                        status["status"] = "REUSED"
                        status["dirty_scope"] = []
                    res.projections_recomputed = 0
                    res.projections_reused = len(
                        res.projection_statuses) * res.files_total
        sid = self.db.new_snapshot(self.repo_id, parent,
                                   commit=self.commit,
                                   meta={"run_id": self.run_id,
                                         "mode": "incremental" if res.incremental else "full",
                                         "analysis_identity": analysis_identity,
                                         "changed_scope": res.changed_files,
                                         "invalidated_scope": res.invalidated_files,
                                         "projection_statuses": res.projection_statuses})
        res.snapshot_id = sid

        if res.incremental and identity_semantic_reuse and not res.changed_files:
            self._copy_previous(sid, parent)
            for relpath, source, _adapter in changed:
                _parsed, digest = prepared[relpath]
                raw = source.encode("utf-8")
                self.db.upsert_file(
                    self.repo_id, relpath, hashlib.sha256(raw).hexdigest(),
                    len(raw), self.parser_version, self.indexer_version,
                    status="ok", semantic_digest=digest)
            res.files_parsed = len(changed)
            res.provider_reanalyzed_files = len(changed)
            res.cutoff_reason = "semantic_digest_unchanged_after_identity_delta"
            self._reattest_support_after_identity_delta(
                parent, sid, res.invalidated_files)
            if {"authority_registry_version", "authority_rule_version"} & identity_changes:
                self.db.delete_support_owned_by_paths(self.repo_id, sid, [""])
                repo_node = Node(kind="REPOSITORY", name=self.repo_id,
                                 qname=self.repo_id, language="", path="",
                                 start_line=1, start_col=1, end_line=1, end_col=1,
                                 meta={"root": self.repo_root})
                self._upsert_supported_node(
                    repo_node, sid, origin="derived_repository")
            self.db.prune_orphan_support(self.repo_id, sid)
            unsupported = self.db.unsupported_new_facts(self.repo_id, sid, parent)
            if unsupported:
                raise RuntimeError("canonical support reattestation incomplete")
            self.db.publish_snapshot(self.repo_id, sid)
            self.db.commit()
            res.duration_ms = int((time.time() - t0) * 1000)
            tick(100, "identity_revision_semantic_reuse")
            return res

        if res.incremental:
            self._copy_previous(sid, parent)
            if {"authority_registry_version", "authority_rule_version"} & identity_changes:
                self.db.delete_support_owned_by_paths(self.repo_id, sid, [""])
            paths = res.invalidated_files
            self.db.delete_support_owned_by_paths(self.repo_id, sid, paths)
            # FK-safe order (PG enforces constraints; SQLite unaffected):
            # evidence → edges → nodes → facts
            self.db.delete_evidence_owned_by_paths(self.repo_id, sid, paths)
            self.db.delete_edges_from_paths(self.repo_id, sid, paths)
            self.db.delete_callsites_in_paths(self.repo_id, sid, paths)
            self.db.delete_imports_in_paths(self.repo_id, sid, paths)
            self.db.delete_includes_in_paths(self.repo_id, sid, paths)
            self.db.delete_pending_in_paths(self.repo_id, sid, paths)
            self.db.delete_unresolved_in_paths(self.repo_id, sid, paths)

        lang_counts: dict[str, dict] = {}
        n = max(len(changed), 1)
        for i, (relpath, source, adapter) in enumerate(changed):
            parsed_and_digest = prepared.get(relpath)
            self._index_file(relpath, source, adapter, sid, res, lang_counts,
                             parsed_and_digest=parsed_and_digest)
            tick(30 + 60 * (i + 1) // n, f"parse:{relpath}")
        res.languages = lang_counts

        if res.incremental:
            keep_ids: set[str] = set()
            for path in res.invalidated_files:
                parsed_and_digest = prepared.get(path)
                if parsed_and_digest is None:
                    continue
                keep_ids.add(f"node:FILE:{path}")
                keep_ids.update(node.canonical_id
                                for node in parsed_and_digest[0].nodes)
            self.db.delete_nodes_in_paths_except(
                self.repo_id, sid, res.invalidated_files, keep_ids)

        if res.incremental:
            self._resolve_delta(sid, res.invalidated_files, res)
        else:
            self._resolve_all(sid)
        tick(90, "resolve")
        if res.incremental:
            self._synthetic_delta(sid, res, res.invalidated_files)
            self._regenerate_node_evidence_delta(sid, res.invalidated_files)
        else:
            self._synthetic(sid, res)
            self._regenerate_node_evidence(sid)
        self.db.prune_orphan_support(self.repo_id, sid)
        unsupported = self.db.unsupported_new_facts(
            self.repo_id, sid, parent if res.incremental else None)
        if unsupported:
            raise RuntimeError(
                "canonical publication lacks admitted support: "
                + ", ".join(unsupported[:5]))
        self.db.publish_snapshot(self.repo_id, sid)
        self.db.commit()
        res.duration_ms = int((time.time() - t0) * 1000)
        tick(100, "done")
        self._telemetry(res, "index", "summary", {
            "mode": "incremental" if res.incremental else "full",
            "snapshot": sid, "duration_ms": res.duration_ms})
        # telemetry opens a fresh write tx (sqlite legacy isolation); commit
        # so no connection is left holding the write lock for later callers
        # (e.g. FLOW0 writeback rebinds on another connection).
        self.db.commit()
        return res

    # ------------------------------------------------------------------
    def _scan(self) -> list[tuple[str, str, object]]:
        out: list[tuple[str, str, object]] = []
        root = Path(self.repo_root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                if ext in SKIP_EXT:
                    continue
                full = Path(dirpath) / fn
                rel = full.relative_to(root).as_posix()
                adapter = adapter_for_path(rel)
                if adapter is None:
                    continue
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                if size > MAX_FILE_BYTES:
                    continue
                try:
                    source = full.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                out.append((rel, source, adapter))
        out.sort(key=lambda x: x[0])
        return out

    def _diff_files(self, files) -> tuple[list, list]:
        changed, unchanged = [], []
        for rel, source, adapter in files:
            st = self.db.file_state(self.repo_id, rel)
            h = hashlib.sha256(source.encode("utf-8")).hexdigest()
            if st is None or st["hash"] != h:
                changed.append((rel, source, adapter))
            else:
                unchanged.append((rel, source, adapter))
        return changed, unchanged

    def _copy_previous(self, sid: str, parent: str) -> None:
        """Copy unchanged facts into the new snapshot (no reparse)."""
        self.db.copy_nodes_edges(self.repo_id, parent, sid)
        self.db.copy_support_receipts(self.repo_id, parent, sid)
        self.db.copy_callsites(self.repo_id, parent, sid)
        self.db.copy_imports(self.repo_id, parent, sid)
        self.db.copy_includes(self.repo_id, parent, sid)
        self.db.copy_pending_edges(self.repo_id, parent, sid)
        self.db.copy_evidence(self.repo_id, parent, sid)
        self.db.copy_unresolved(self.repo_id, parent, sid)

    def _dependency_closure(
            self, parent: str, seeds: set[str],
            prepared: dict[str, tuple[ParsedFile, str]],
    ) -> tuple[set[str], dict[str, list[str]]]:
        """Expand semantic changes through the persisted dependency manifest."""
        affected = set(seeds)
        reasons: dict[str, set[str]] = {
            path: {"source_or_context_delta"} for path in seeds
        }

        # An input-identity change requires provider reanalysis, but it is not
        # itself a semantic change.  Propagate only seeds whose fresh provider
        # digest differs from the persisted digest.  This prevents an opaque
        # build-context change from dragging language-external symbolic users
        # into the closure when the provider output is identical.
        semantic_seeds: set[str] = set()
        for path in seeds:
            parsed_and_digest = prepared.get(path)
            previous = self.db.file_state(self.repo_id, path)
            if parsed_and_digest is None or previous is None or \
                    previous.get("semantic_digest") != parsed_and_digest[1]:
                semantic_seeds.add(path)

        includes = self.db.all_includes(self.repo_id, parent)
        # Header dependencies are transitive and path-based.  Match the exact
        # relative target first and basename only as the legacy resolver does.
        dependency_affected = set(semantic_seeds)
        changed = True
        while changed:
            changed = False
            for inc in includes:
                target = os.path.normpath(os.path.join(
                    os.path.dirname(inc["file_path"]), inc["target"]))
                matches = target in dependency_affected or any(
                    os.path.basename(path) == os.path.basename(inc["target"])
                    for path in dependency_affected)
                if matches and inc["file_path"] not in dependency_affected:
                    dependency_affected.add(inc["file_path"])
                    affected.add(inc["file_path"])
                    reasons.setdefault(inc["file_path"], set()).add(
                        f"includes:{inc['target']}")
                    changed = True

        # Only definition identities that actually changed may invalidate
        # symbolic users; a function-body call change does not dirty unrelated
        # callers of other definitions in the same file.
        old_defs: set[tuple] = set()
        for path in semantic_seeds:
            for node in self.db.nodes_by_path(self.repo_id, parent, path):
                if node["kind"] not in ("FILE", "DIRECTORY", "REPOSITORY"):
                    old_meta = json.loads(node.get("meta_json") or "{}")
                    old_meta.pop("locations", None)
                    old_defs.add((node["kind"], node["name"], node["qname"],
                                  node["language"],
                                  json.dumps(old_meta, sort_keys=True)))
        new_defs: set[tuple] = set()
        for path in semantic_seeds:
            parsed_and_digest = prepared.get(path)
            if parsed_and_digest is None:
                continue
            for node in parsed_and_digest[0].nodes:
                new_defs.add((node.kind, node.name, node.qname, node.language,
                              json.dumps(node.meta, sort_keys=True)))
        changed_defs = old_defs.symmetric_difference(new_defs)
        names = {row[1] for row in changed_defs}
        qnames = {row[2] for row in changed_defs}
        if names or qnames:
            for callsite in self.db.all_callsites(self.repo_id, parent):
                candidates = set(json.loads(callsite["candidates_json"]))
                if callsite["callee"] in names or candidates.intersection(qnames):
                    affected.add(callsite["file_path"])
                    reasons.setdefault(callsite["file_path"], set()).add(
                        f"symbol:{callsite['callee']}")
            for pending in self.db.all_pending_edges(self.repo_id, parent):
                if pending["src_value"] in names | qnames or \
                        pending["dst_value"] in names | qnames:
                    affected.add(pending["file_path"])
                    reasons.setdefault(pending["file_path"], set()).add(
                        "symbolic_edge")
            for imp in self.db.all_imports(self.repo_id, parent):
                if imp["module_qname"] in qnames:
                    affected.add(imp["file_path"])
                    reasons.setdefault(imp["file_path"], set()).add(
                        f"imports:{imp['module_qname']}")
        return affected, {path: sorted(values)
                          for path, values in sorted(reasons.items())}

    @staticmethod
    def _component_hash(value) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _projection_components_from_parsed(self, parsed: ParsedFile) -> dict:
        nodes = [(n.kind, n.name, n.qname, n.language, n.meta)
                 for n in parsed.nodes]
        nodes.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        edges = []
        for edge in parsed.edges:
            meta = dict(edge.meta)
            if edge.src.language:
                meta["ref_lang_src"] = edge.src.language
            if edge.dst.language:
                meta["ref_lang_dst"] = edge.dst.language
            edges.append((edge.kind, edge.src.mode, edge.src.value,
                          edge.dst.mode, edge.dst.value, edge.confidence, meta))
        edges.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        calls = [(call.callee, tuple(call.candidates), call.shape)
                 for call in parsed.callsites]
        calls.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        imports = [(imp.src_qname or self._default_import_src(
            parsed.language, parsed.path), imp.module_qname, imp.local,
                    tuple(imp.only_names or ()), imp.meta)
                   for imp in parsed.imports]
        imports.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        includes = [(inc.target, inc.meta) for inc in parsed.includes]
        includes.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        callable_nodes = [row for row in nodes if row[0] in (
            "FUNCTION", "METHOD", "PROCEDURE", "SUBROUTINE", "PROGRAM")]
        module_nodes = [row for row in nodes if row[0] in (
            "MODULE", "PACKAGE", "SUBMODULE", "FILE")]
        architecture = (nodes, edges, imports, includes)
        flow = (calls, [row for row in edges if row[0] not in ("CONTAINS",)])
        data = callable_nodes + [row for row in nodes if row[0] in (
            "VARIABLE", "CONSTANT", "FIELD", "PARAMETER", "DATA_CONTRACT")]
        module = (module_nodes, imports, includes)
        return {
            "Architecture": self._component_hash(architecture),
            "Module": self._component_hash(module),
            "Flow": self._component_hash(flow),
            "Data": self._component_hash(data),
            "Human Semantic": self._component_hash(
                (architecture, flow, data, module, sorted(parsed.notes))),
        }

    def _projection_components_from_store(self, sid: str, path: str,
                                          language: str) -> dict:
        nodes = []
        for node in self.db.nodes_by_path(self.repo_id, sid, path):
            if node["kind"] == "FILE":
                continue
            meta = json.loads(node.get("meta_json") or "{}")
            meta.pop("locations", None)
            nodes.append((node["kind"], node["name"], node["qname"],
                          node["language"], meta))
        nodes.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        edges = []
        for edge in self.db.pending_edges_in_paths(self.repo_id, sid, [path]):
            edges.append((edge["kind"], edge["src_mode"], edge["src_value"],
                          edge["dst_mode"], edge["dst_value"],
                          edge["confidence"], json.loads(edge["meta_json"])))
        edges.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        calls = [(row["callee"], tuple(json.loads(row["candidates_json"])),
                  json.loads(row["shape"]) if row.get("shape")
                  else None)
                 for row in self.db.callsites_in_paths(
                     self.repo_id, sid, [path])]
        calls.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        imports = [(row["src_qname"], row["module_qname"], row["local"],
                    tuple(json.loads(row["only_names_json"])),
                    json.loads(row["meta_json"]))
                   for row in self.db.imports_in_paths(
                       self.repo_id, sid, [path])]
        imports.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        includes = [(row["target"], json.loads(row["meta_json"]))
                    for row in self.db.includes_in_paths(
                        self.repo_id, sid, [path])]
        includes.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
        callable_nodes = [row for row in nodes if row[0] in (
            "FUNCTION", "METHOD", "PROCEDURE", "SUBROUTINE", "PROGRAM")]
        module_nodes = [row for row in nodes if row[0] in (
            "MODULE", "PACKAGE", "SUBMODULE", "FILE")]
        architecture = (nodes, edges, imports, includes)
        flow = (calls, [row for row in edges if row[0] not in ("CONTAINS",)])
        data = callable_nodes + [row for row in nodes if row[0] in (
            "VARIABLE", "CONSTANT", "FIELD", "PARAMETER", "DATA_CONTRACT")]
        module = (module_nodes, imports, includes)
        return {
            "Architecture": self._component_hash(architecture),
            "Module": self._component_hash(module),
            "Flow": self._component_hash(flow),
            "Data": self._component_hash(data),
            "Human Semantic": self._component_hash(
                (architecture, flow, data, module, [])),
        }

    def _projection_plan(self, parent: str, paths: list[str],
                         prepared: dict[str, tuple[ParsedFile, str]]) -> dict:
        dirty = {name: [] for name in (
            "Architecture", "Module", "Flow", "Data", "Human Semantic")}
        for path in paths:
            parsed_and_digest = prepared.get(path)
            if parsed_and_digest is None:
                for values in dirty.values():
                    values.append(path)
                continue
            parsed = parsed_and_digest[0]
            previous = self._projection_components_from_store(
                parent, path, parsed.language)
            current = self._projection_components_from_parsed(parsed)
            for name in dirty:
                if previous[name] != current[name]:
                    dirty[name].append(path)
        return {
            name: {
                "status": "RECOMPUTED" if values else "REUSED",
                "dirty_scope": sorted(values),
                "reuse_source": parent,
            }
            for name, values in dirty.items()
        }

    # ------------------------------------------------------------------
    def _index_file(self, relpath: str, source: str, adapter, sid: str,
                    res: IndexResult, lang_counts: dict,
                    parsed_and_digest: tuple[ParsedFile, str] | None = None) -> None:
        lang = adapter.language
        lc = lang_counts.setdefault(lang, {"files": 0, "nodes": 0, "edges": 0,
                                           "callsites": 0, "failed": 0})
        try:
            if parsed_and_digest is None:
                pf = adapter.parse(relpath, source)
                semantic_digest = self._semantic_digest(pf)
            else:
                pf, semantic_digest = parsed_and_digest
        except ParseError as exc:
            lc["failed"] += 1
            res.files_failed += 1
            self.db.upsert_file(self.repo_id, relpath, "", 0,
                                self.parser_version, self.indexer_version,
                                status="parse_error")
            self.db.add_unresolved(self.repo_id, sid, "parse_error",
                                   {"path": relpath, "detail": str(exc)})
            res.notes.append(f"parse_error:{relpath}")
            return
        h = hashlib.sha256(source.encode("utf-8")).hexdigest()
        self.db.upsert_file(self.repo_id, relpath, h, len(source.encode()),
                            self.parser_version, self.indexer_version,
                            status="ok", semantic_digest=semantic_digest)
        lc["files"] += 1
        res.files_parsed += 1
        res.provider_reanalyzed_files += 1
        # FILE node (canonical, all languages)
        first = pf.nodes[0] if pf.nodes else None
        fnode = Node(kind="FILE", name=Path(relpath).name, qname=relpath,
                     language=lang, path=relpath,
                     start_line=first.start_line if first else 1,
                     start_col=1, end_line=1, end_col=1)
        nid, created = self._upsert_supported_node(
            fnode, sid, origin="parser_file")
        if created:
            res.nodes_added += 1
        for n in pf.nodes:
            nid2, created2 = self._upsert_supported_node(n, sid)
            lc["nodes"] += 1
            if created2:
                res.nodes_added += 1
            else:
                res.nodes_merged += 1
        for e in pf.edges:
            meta = dict(e.meta)
            if e.src.language:
                meta["ref_lang_src"] = e.src.language
            if e.dst.language:
                meta["ref_lang_dst"] = e.dst.language
            self.db.add_pending_edge(e, relpath, self.repo_id, sid, meta)
            lc["edges"] += 1
        for cs in pf.callsites:
            self.db.add_callsite(cs, self.repo_id, sid)
            lc["callsites"] += 1
        for imp in pf.imports:
            src_q = imp.src_qname or self._default_import_src(lang, relpath)
            self.db.add_import_row(imp, src_q, self.repo_id, sid)
        for inc in pf.includes:
            self.db.add_include_row(inc, self.repo_id, sid)

    @staticmethod
    def _semantic_digest(parsed: ParsedFile) -> str:
        """Digest provider facts while excluding presentation-only spans.

        Source line/column movement may refresh metadata, but it is not a
        canonical semantic delta.  Identity, relation, call/import/include
        targets, confidence, and provider metadata remain digest inputs.
        """
        def ordered(values):
            return sorted(values, key=lambda value: json.dumps(
                value, sort_keys=True, separators=(",", ":"), default=str))

        payload = {
            "schema": "rintel-provider-semantics-v1",
            "language": parsed.language,
            "path": parsed.path,
            "nodes": ordered((n.kind, n.name, n.qname, n.language, n.path,
                              n.meta) for n in parsed.nodes),
            "edges": ordered((e.kind, e.src.mode, e.src.value, e.src.language,
                              e.dst.mode, e.dst.value, e.dst.language,
                              e.confidence, e.meta) for e in parsed.edges),
            "callsites": ordered((c.path, c.callee, tuple(c.candidates),
                                  c.ckind, c.shape) for c in parsed.callsites),
            "imports": ordered((i.path, i.module_qname, i.local,
                                tuple(i.only_names or ()), i.external,
                                i.src_qname, i.meta) for i in parsed.imports),
            "includes": ordered((i.path, i.target, i.external, i.meta)
                                for i in parsed.includes),
            "notes": sorted(parsed.notes),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             default=str).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _default_import_src(lang: str, relpath: str) -> str:
        if lang == "python":
            return py_module_qname(relpath)
        if lang in ("typescript", "javascript"):
            return ts_module_qname(relpath)
        return relpath  # FILE node qname (C includes etc.)

    # ------------------------------------------------------------------
    # Resolution pass
    # ------------------------------------------------------------------
    def _resolve_all(self, sid: str) -> None:
        # drop every edge of this snapshot; rebuild from stored facts.
        # (evidence first: PG FKs edge_evidence → snapshot_edges; node
        # evidence is regenerated afterwards by _regenerate_node_evidence)
        self.db.delete_evidence_all(self.repo_id, sid)
        self.db.delete_edges_all(self.repo_id, sid)
        nodes = self.db.all_nodes(self.repo_id, sid)
        defs: dict[str, list[dict]] = {}
        by_name: dict[str, dict[str, list[str]]] = {}
        file_nodes: dict[str, list[dict]] = {}
        langs_by_name: dict[str, set[str]] = {}
        for n in nodes:
            defs.setdefault(n["qname"], []).append(n)
            by_name.setdefault(n["path"], {}).setdefault(n["name"], []).append(n)
            file_nodes.setdefault(n["path"], []).append(n)
            langs_by_name.setdefault(n["name"], set()).add(n["language"])
        imports_by_file: dict[str, list[dict]] = {}
        for imp in self.db.all_imports(self.repo_id, sid):
            imports_by_file.setdefault(imp["file_path"], []).append(imp)
        includes_by_file: dict[str, list[str]] = {}
        for inc in self.db.all_includes(self.repo_id, sid):
            includes_by_file.setdefault(inc["file_path"], []).append(inc["target"])
        include_closure = self._include_closure(includes_by_file,
                                                set(f for f in by_name))

        # 1) pending (adapter-emitted) edges
        for pe in self.db.all_pending_edges(self.repo_id, sid):
            self._resolve_pending(pe, defs, by_name, imports_by_file, sid)
        # 2) call sites
        for cs in self.db.all_callsites(self.repo_id, sid):
            self._resolve_callsite(cs, defs, by_name, imports_by_file,
                                   include_closure, file_nodes, sid,
                                   langs_by_name)
        # 3) imports -> IMPORTS edges + external flags
        for imp in self.db.all_imports(self.repo_id, sid):
            self._resolve_import(imp, defs, imports_by_file, sid)
        # 4) includes -> INCLUDES edges + external flags
        for inc in self.db.all_includes(self.repo_id, sid):
            self._resolve_include(inc, defs, sid)

    def _resolve_delta(self, sid: str, paths: list[str],
                       res: IndexResult) -> None:
        """Reconcile only resolution inputs owned by invalidated paths."""
        nodes = self.db.all_nodes(self.repo_id, sid)
        defs: dict[str, list[dict]] = {}
        by_name: dict[str, dict[str, list[dict]]] = {}
        file_nodes: dict[str, list[dict]] = {}
        langs_by_name: dict[str, set[str]] = {}
        for node in nodes:
            defs.setdefault(node["qname"], []).append(node)
            by_name.setdefault(node["path"], {}).setdefault(
                node["name"], []).append(node)
            file_nodes.setdefault(node["path"], []).append(node)
            langs_by_name.setdefault(node["name"], set()).add(node["language"])

        imports = self.db.imports_in_paths(self.repo_id, sid, paths)
        imports_by_file: dict[str, list[dict]] = {}
        for imp in imports:
            imports_by_file.setdefault(imp["file_path"], []).append(imp)
        # Include closure is dependency metadata, not a canonical
        # reconciliation input.  It is read once and only path-owned include
        # rows below are resolved/written.
        all_includes = self.db.all_includes(self.repo_id, sid)
        includes_by_file: dict[str, list[str]] = {}
        for inc in all_includes:
            includes_by_file.setdefault(inc["file_path"], []).append(inc["target"])
        include_closure = self._include_closure(includes_by_file, set(by_name))

        pending = self.db.pending_edges_in_paths(self.repo_id, sid, paths)
        callsites = self.db.callsites_in_paths(self.repo_id, sid, paths)
        includes = self.db.includes_in_paths(self.repo_id, sid, paths)
        res.canonical_reconsidered_facts = (
            len(pending) + len(callsites) + len(imports) + len(includes))
        # Count reused inputs in the database.  Do not fetch every resolution
        # row merely to populate telemetry: reconciliation remains scoped to
        # ``paths`` even on large repositories.
        total_inputs = self.db.resolution_input_count(self.repo_id, sid)
        res.canonical_reused_facts = max(
            total_inputs - res.canonical_reconsidered_facts, 0)

        for edge in pending:
            self._resolve_pending(edge, defs, by_name, imports_by_file, sid)
        for callsite in callsites:
            self._resolve_callsite(callsite, defs, by_name, imports_by_file,
                                   include_closure, file_nodes, sid,
                                   langs_by_name)
        for imp in imports:
            self._resolve_import(imp, defs, imports_by_file, sid)
        for inc in includes:
            self._resolve_include(inc, defs, sid)
        res.canonical_changed_facts = res.canonical_reconsidered_facts

    def _include_closure(self, includes_by_file: dict[str, list[str]],
                         files: set[str]) -> dict[str, set[str]]:
        """C: transitive include targets that exist in the repo."""
        file_paths = {p for p in files}
        closure: dict[str, set[str]] = {}
        def resolve_target(imp_path: str, target: str) -> str | None:
            cand = os.path.normpath(os.path.join(os.path.dirname(imp_path),
                                                 target))
            if cand in file_paths:
                return cand
            base = os.path.basename(target)
            for p in file_paths:
                if os.path.basename(p) == base:
                    return p
            return None
        for f, targets in includes_by_file.items():
            seen: set[str] = set()
            stack = list(targets)
            while stack:
                t = stack.pop()
                r = resolve_target(f, t)
                if r and r not in seen:
                    seen.add(r)
                    stack.extend(includes_by_file.get(r, []))
            closure[f] = seen
        return closure

    def _resolve_pending(self, pe: dict, defs: dict, by_name: dict,
                         imports_by_file: dict, sid: str) -> None:
        meta = json.loads(pe["meta_json"])
        src = self._resolve_ref(pe["src_mode"], pe["src_value"], pe["file_path"],
                                defs, by_name, imports_by_file,
                                language=meta.get("ref_lang_src"))
        dst = self._resolve_ref(pe["dst_mode"], pe["dst_value"], pe["file_path"],
                                defs, by_name, imports_by_file,
                                language=meta.get("ref_lang_dst"))
        if src is None or dst is None:
            self.db.add_unresolved(self.repo_id, sid, "edge_unresolved", {
                "kind": pe["kind"], "file": pe["file_path"], "line": pe["line"],
                "src": f"{pe['src_mode']}:{pe['src_value']}",
                "dst": f"{pe['dst_mode']}:{pe['dst_value']}",
                "missing": "src" if src is None else "dst"})
            return
        self._add_supported_edge(
            pe["kind"], src, dst, sid, owner_path=pe["file_path"],
            origin="resolved_pending_edge", line=pe["line"],
            confidence=pe["confidence"], meta=json.loads(pe["meta_json"]),
            evidence=self._edge_evidence(pe, "tree_sitter", pe["confidence"]))

    @staticmethod
    def _unique_def(defs: dict[str, list[dict]], qname: str,
                    language: str | None = None,
                    kind: str | None = None) -> dict | None:
        candidates = [node for node in defs.get(qname, [])
                      if (language is None or node["language"] == language)
                      and (kind is None or node["kind"] == kind)]
        return candidates[0] if len(candidates) == 1 else None

    def _resolve_ref(self, mode: str, value: str, file_path: str, defs: dict,
                     by_name: dict, imports_by_file: dict,
                     language: str | None = None) -> str | None:
        """Resolve a symbolic ref to a canonical node id, or None."""
        if language is None:
            adapter = adapter_for_path(file_path)
            language = adapter.language if adapter is not None else None
        def in_language(node: dict) -> bool:
            return language is not None and node["language"] == language
        if mode == REF_QNAME:
            n = self._unique_def(defs, value, language)
            return n["id"] if n and in_language(n) else None
        if mode == REF_MODULE:
            n = self._unique_def(defs, value, language)
            if n and in_language(n) and n["kind"] in ("MODULE", "PACKAGE"):
                return n["id"]
            return None
        # REF_NAME: same-file → imported → global-unique
        cands: list[dict] = []
        for n in by_name.get(file_path, {}).get(value, []):
            if in_language(n):
                cands.append(n)
        if len(cands) == 1:
            return cands[0]["id"]
        if len(cands) > 1:
            return None  # ambiguous in-file
        for imp in imports_by_file.get(file_path, []):
            names = json.loads(imp["only_names_json"]) or []
            if imp["local"] == value:
                targets = names or [value]
                for nm in targets:
                    n = self._unique_def(defs, f"{imp['module_qname']}.{nm}",
                                         language)
                    if n and in_language(n):
                        return n["id"]
            elif value in names:
                n = self._unique_def(defs, f"{imp['module_qname']}.{value}",
                                     language)
                if n and in_language(n):
                    return n["id"]
        unique: list[dict] = []
        for path, names in by_name.items():
            for n in names.get(value, []):
                if in_language(n):
                    unique.append(n)
        if len(unique) == 1:
            return unique[0]["id"]
        return None

    def _resolve_callsite(self, cs: dict, defs: dict, by_name: dict,
                          imports_by_file: dict, include_closure: dict,
                          file_nodes: dict, sid: str,
                          langs_by_name: dict | None = None) -> None:
        callee = cs["callee"]
        cid = cs["id"]
        cs["_file_nodes"] = file_nodes.get(cs["file_path"], [])
        adapter = adapter_for_path(cs["file_path"])
        source_language = adapter.language if adapter is not None else None
        def in_source_language(node: dict) -> bool:
            # Name/qname agreement across languages is not ABI evidence.
            return (source_language is not None
                    and node["language"] == source_language)
        # 0) TS/JS relative-specifier candidates ("./payments".Foo -> payments.Foo)
        cands = json.loads(cs["candidates_json"])
        if cs["file_path"].endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
            extra: list[str] = []
            for cand in cands:
                head, dot, _rest = cand.partition(".")
                if head.startswith(("./", "../")) and dot:
                    for mq in self._ts_module_candidates(cs["file_path"], head):
                        extra.append(mq + dot + _rest)
            cands = extra + cands
        # 1) adapter-proposed candidate qnames
        for cand in cands:
            n = self._unique_def(defs, cand, source_language)
            if n and in_source_language(n):
                self._emit_call(cs, n, "candidate", 0.9, sid)
                return
        # 2) name fallback: same-file
        same = [n for n in by_name.get(cs["file_path"], {}).get(callee, [])
                if in_source_language(n)]
        if len(same) == 1:
            self._emit_call(cs, same[0], "same_file", 0.95, sid)
            return
        if len(same) > 1:
            self._unresolved_callsite(cid, "ambiguous_same_file", sid,
                                      n_defs=len(same),
                                      langs_by_name=langs_by_name)
            return
        # 3) include closure (C)
        for inc_file in include_closure.get(cs["file_path"], set()):
            inc_defs = [n for n in by_name.get(inc_file, {}).get(callee, [])
                        if in_source_language(n)]
            if len(inc_defs) == 1:
                self._emit_call(cs, inc_defs[0], "included", 0.85, sid)
                return
        # 4) imported binding
        for imp in imports_by_file.get(cs["file_path"], []):
            names = json.loads(imp["only_names_json"]) or []
            if imp["local"] == callee or callee in names:
                targets = names or [callee]
                if imp["local"] == callee and callee in names:
                    targets = [callee]
                for nm in targets:
                    n = self._unique_def(defs, f"{imp['module_qname']}.{nm}",
                                         source_language)
                    if n and in_source_language(n):
                        self._emit_call(cs, n, "imported", 0.9, sid)
                        return
        # 5) global unique name
        unique = [n for path, names in by_name.items()
                  for n in names.get(callee, []) if in_source_language(n)]
        if len(unique) == 1:
            self._emit_call(cs, unique[0], "global_unique", 0.7, sid)
            return
        self._unresolved_callsite(cid, "unresolved", sid, n_defs=len(unique),
                                  langs_by_name=langs_by_name)

    def _unresolved_callsite(self, cid: int, kind: str, sid: str,
                             n_defs: int = 0,
                             langs_by_name: dict | None = None) -> None:
        self.db.update_callsite(cid, None, kind, None, None)
        cs = self.db.callsite_row(cid)
        shape = None
        if cs and cs.get("shape"):
            try:
                shape = json.loads(cs["shape"])
            except ValueError:
                shape = None
        detail = {
            "callsite_id": cid,
            "callee": cs["callee"] if cs else None,
            "file": cs["file_path"] if cs else None,
            "line": cs["line"] if cs else None,
        }
        if cs:
            # FAC-EQ0: label the *reason* (evidence-uncertainty bucket) so
            # reports/UI never present "unresolved" as proof-of-absence.
            detail["classification"] = classify_unresolved(
                cs["callee"], cs["file_path"], shape, kind, n_defs,
                langs_by_name)
            if shape:
                detail["shape"] = shape
        self.db.add_unresolved(self.repo_id, sid, f"callsite_{kind}", detail)

    def _emit_call(self, cs: dict, target: dict, rule: str, conf: float,
                   sid: str) -> None:
        src_id = self._src_for_callsite(cs, sid)
        if src_id is None:
            self.db.update_callsite(cs["id"], None, "no_src", None, None)
            self.db.add_unresolved(self.repo_id, sid, "callsite_no_src",
                                   {"callsite_id": cs["id"],
                                    "file": cs["file_path"],
                                    "line": cs["line"],
                                    "callee": cs["callee"]})
            return
        if target["kind"] in ("CLASS", "TYPE"):
            # construction: prefer the constructor symbol
            lang = target["language"]
            if lang == "cpp":
                ctor_q = f"{target['qname']}.{target['name']}"
            else:
                ctor_q = target["qname"] + CTOR_SUFFIX.get(lang, ".__init__")
            ctor = self.db.node_by_qname(self.repo_id, sid, ctor_q)
            if ctor and ctor["language"] == lang:
                eid = self._add_call_edge(src_id, ctor["id"], cs, rule, conf, sid)
                self.db.update_callsite(cs["id"], ctor["id"], "constructor",
                                        conf, eid)
                return
            eid = self._add_ref_edge(src_id, target["id"], cs, rule, conf, sid)
            self.db.update_callsite(cs["id"], target["id"], f"{rule}_construct",
                                    conf, eid)
            return
        eid = self._add_call_edge(src_id, target["id"], cs, rule, conf, sid)
        self.db.update_callsite(cs["id"], target["id"], rule, conf, eid)

    def _src_for_callsite(self, cs: dict, sid: str) -> str | None:
        """Deepest enclosing procedure/module node containing the call line."""
        best: dict | None = None
        for n in cs.get("_file_nodes", []):
            if n["kind"] not in CALL_SRC_KINDS:
                continue
            sl, el = n["start_line"] or 0, n["end_line"] or 0
            if sl <= cs["line"] <= el:
                if best is None or (n["start_line"] or 0) >= (best["start_line"] or 0):
                    best = n
        if best:
            return best["id"]
        # fallback: module/package node of the file
        for n in cs.get("_file_nodes", []):
            if n["kind"] in ("MODULE", "PACKAGE"):
                return n["id"]
        return None

    def _add_call_edge(self, src_id: str, dst_id: str, cs: dict, rule: str,
                       conf: float, sid: str) -> str | None:
        ok = self._add_supported_edge(
            "CALLS", src_id, dst_id, sid,
            owner_path=cs["file_path"], origin=f"resolved_call:{rule}",
            line=cs["line"], confidence=conf,
            meta={"rule": rule, "callee": cs["callee"]},
            evidence={"source": "tree_sitter", "confidence": conf,
                      "location": {"path": cs["file_path"], "line": cs["line"]},
                      "payload": {"rule": rule, "parser_version": self.parser_version}})
        return f"edge:CALLS:{src_id}:{dst_id}" if ok else None

    def _add_ref_edge(self, src_id: str, dst_id: str, cs: dict, rule: str,
                      conf: float, sid: str) -> str | None:
        ok = self._add_supported_edge(
            "REFERENCES", src_id, dst_id, sid,
            owner_path=cs["file_path"], origin=f"resolved_reference:{rule}",
            line=cs["line"], confidence=conf,
            meta={"rule": rule, "callee": cs["callee"]},
            evidence={"source": "tree_sitter", "confidence": conf,
                      "location": {"path": cs["file_path"], "line": cs["line"]},
                      "payload": {"rule": rule, "parser_version": self.parser_version}})
        return f"edge:REFERENCES:{src_id}:{dst_id}" if ok else None

    def _resolve_import(self, imp: dict, defs: dict, imports_by_file: dict,
                        sid: str) -> None:
        target = self._module_node_for(imp, defs, imports_by_file)
        adapter = adapter_for_path(imp["file_path"])
        src = self._unique_def(defs, imp["src_qname"],
                               adapter.language if adapter else None)
        if target is None:
            self.db.set_import_external(imp["id"])
            return
        if src is None:
            self.db.add_unresolved(self.repo_id, sid, "import_src_unresolved",
                                   {"file": imp["file_path"],
                                    "src_qname": imp["src_qname"]})
            return
        self._add_supported_edge("IMPORTS", src["id"], target["id"], sid,
                             owner_path=imp["file_path"], origin="resolved_import",
                             line=imp["line"], confidence=1.0,
                             meta={"module": imp["module_qname"]},
                             evidence={"source": "tree_sitter",
                                       "confidence": 1.0,
                                       "location": {"path": imp["file_path"],
                                                    "line": imp["line"]},
                                       "payload": {"parser_version": self.parser_version}})

    def _module_node_for(self, imp: dict, defs: dict,
                         imports_by_file: dict) -> dict | None:
        mq = imp["module_qname"]
        adapter = adapter_for_path(imp["file_path"])
        meta = json.loads(imp["meta_json"])
        candidates: list[str] = [mq]
        if meta.get("resolve") == "relative":
            candidates = self._ts_module_candidates(imp["file_path"], mq)
        elif imp["file_path"].endswith(".go") or mq.count("/") > 0:
            candidates.append(mq.rsplit("/", 1)[-1])
        for c in candidates:
            n = self._unique_def(defs, c, adapter.language if adapter else None)
            if (n and adapter is not None and n["language"] == adapter.language
                    and n["kind"] in ("MODULE", "PACKAGE")):
                return n
        return None

    def _ts_module_candidates(self, importing_path: str,
                              spec: str) -> list[str]:
        base = os.path.dirname(importing_path)
        cands: list[str] = []
        if spec.startswith("./") or spec.startswith("../"):
            p = os.path.normpath(os.path.join(base, spec))
            for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
                cands.append(ts_module_qname(p + ext))
            cands.append(ts_module_qname(os.path.join(p, "index")))
        return cands

    def _resolve_include(self, inc: dict, defs: dict, sid: str) -> None:
        target = self._resolve_include_target(inc["file_path"], inc["target"],
                                              defs)
        src = self._unique_def(defs, inc["file_path"], kind="FILE")
        if target is None:
            self.db.set_include_external(inc["id"])
            return
        if src is None or src["kind"] != "FILE":
            self.db.add_unresolved(self.repo_id, sid, "include_src_unresolved",
                                   {"file": inc["file_path"]})
            return
        self._add_supported_edge("INCLUDES", src["id"], target["id"], sid,
                             owner_path=inc["file_path"], origin="resolved_include",
                             line=inc["line"], confidence=1.0,
                             meta={"target": inc["target"]},
                             evidence={"source": "tree_sitter",
                                       "confidence": 1.0,
                                       "location": {"path": inc["file_path"],
                                                    "line": inc["line"]},
                                       "payload": {"parser_version": self.parser_version}})

    def _resolve_include_target(self, imp_path: str, target: str,
                                defs: dict) -> dict | None:
        cand = os.path.normpath(os.path.join(os.path.dirname(imp_path), target))
        n = self._unique_def(defs, cand, kind="FILE")
        if n and n["kind"] == "FILE":
            return n
        base = os.path.basename(target)
        matches = [node for q, rows in defs.items()
                   if os.path.basename(q) == base
                   for node in rows if node["kind"] == "FILE"]
        if len(matches) == 1:
            return matches[0]
        return None

    # ------------------------------------------------------------------
    # Synthetic containment (REPOSITORY / DIRECTORY) + FILE containment
    # ------------------------------------------------------------------
    def _synthetic(self, sid: str, res: IndexResult) -> None:
        nodes = self.db.all_nodes(self.repo_id, sid)
        files = [n for n in nodes if n["kind"] == "FILE"]
        repo_node = Node(kind="REPOSITORY", name=self.repo_id,
                         qname=self.repo_id, language="", path="",
                         start_line=1, start_col=1, end_line=1, end_col=1,
                         meta={"root": self.repo_root})
        rid, _ = self._upsert_supported_node(
            repo_node, sid, origin="derived_repository")
        dirs: dict[str, str] = {}
        for f in files:
            parts = f["path"].split("/")
            parent = rid
            acc = ""
            for i, part in enumerate(parts[:-1]):
                acc = f"{acc}/{part}" if acc else part
                if acc not in dirs:
                    dnode = Node(kind="DIRECTORY", name=part, qname=acc,
                                 language="", path=acc,
                                 start_line=1, start_col=1, end_line=1,
                                 end_col=1)
                    did, _ = self._upsert_supported_node(
                        dnode, sid, owner_path=f["path"],
                        origin="derived_directory")
                    dirs[acc] = did
                    self._add_supported_edge(
                        "CONTAINS", parent, did, sid, owner_path=f["path"],
                        origin="derived_directory_containment",
                        confidence=1.0, resolution=TargetResolution.EXACT)
                parent = dirs[acc]
            self._add_supported_edge(
                "CONTAINS", parent, f["id"], sid, owner_path=f["path"],
                origin="derived_file_containment", confidence=1.0,
                resolution=TargetResolution.EXACT)
        # FILE contains its top-level units
        for f in files:
            lang = f["language"]
            for n in nodes:
                if n["path"] != f["path"]:
                    continue
                if lang in ("python", "typescript", "javascript") and \
                        n["kind"] == "MODULE":
                    self._add_supported_edge(
                        "CONTAINS", f["id"], n["id"], sid,
                        owner_path=f["path"], origin="derived_top_level_containment",
                        confidence=1.0, resolution=TargetResolution.EXACT)
                elif lang == "fortran" and n["kind"] in ("MODULE", "SUBMODULE",
                                                         "PROGRAM"):
                    self._add_supported_edge(
                        "CONTAINS", f["id"], n["id"], sid,
                        owner_path=f["path"], origin="derived_top_level_containment",
                        confidence=1.0, resolution=TargetResolution.EXACT)
                elif lang == "go" and n["kind"] == "PACKAGE":
                    self._add_supported_edge(
                        "CONTAINS", f["id"], n["id"], sid,
                        owner_path=f["path"], origin="derived_top_level_containment",
                        confidence=1.0, resolution=TargetResolution.EXACT)
                elif lang == "java" and n["kind"] in ("CLASS", "INTERFACE",
                                                      "ENUM"):
                    self._add_supported_edge(
                        "CONTAINS", f["id"], n["id"], sid,
                        owner_path=f["path"], origin="derived_top_level_containment",
                        confidence=1.0, resolution=TargetResolution.EXACT)
                elif lang in ("c", "cpp") and n["kind"] in ("FUNCTION", "TYPE",
                                                            "ENUM") and \
                        n["qname"] == n["name"]:
                    self._add_supported_edge(
                        "CONTAINS", f["id"], n["id"], sid,
                        owner_path=f["path"], origin="derived_top_level_containment",
                        confidence=1.0, resolution=TargetResolution.EXACT)

    def _synthetic_delta(self, sid: str, res: IndexResult,
                         paths: list[str]) -> None:
        """Repair containment only for invalidated file projections."""
        repo_node = Node(kind="REPOSITORY", name=self.repo_id,
                         qname=self.repo_id, language="", path="",
                         start_line=1, start_col=1, end_line=1, end_col=1,
                         meta={"root": self.repo_root})
        existing_repo = self.db.node_by_id(
            self.repo_id, sid, repo_node.canonical_id)
        if existing_repo is None or not self.db.support_receipts(
                self.repo_id, sid, repo_node.canonical_id):
            repo_id, _ = self._upsert_supported_node(
                repo_node, sid, origin="derived_repository")
        else:
            repo_id = existing_repo["id"]
        for path in paths:
            file_nodes = self.db.nodes_by_path(self.repo_id, sid, path)
            file_node = next((n for n in file_nodes if n["kind"] == "FILE"), None)
            if file_node is None:
                continue
            parent = repo_id
            acc = ""
            for part in path.split("/")[:-1]:
                acc = f"{acc}/{part}" if acc else part
                directory = self.db.node_by_id(
                    self.repo_id, sid, f"node:DIRECTORY:{acc}")
                if directory is None:
                    dnode = Node(kind="DIRECTORY", name=part, qname=acc,
                                 language="", path=acc, start_line=1,
                                 start_col=1, end_line=1, end_col=1)
                    directory_id, _ = self._upsert_supported_node(
                        dnode, sid, owner_path=path,
                        origin="derived_directory")
                else:
                    directory_id = directory["id"]
                self._add_supported_edge(
                    "CONTAINS", parent, directory_id, sid, owner_path=path,
                    origin="derived_directory_containment", confidence=1.0,
                    resolution=TargetResolution.EXACT)
                parent = directory_id
            self._add_supported_edge(
                "CONTAINS", parent, file_node["id"], sid, owner_path=path,
                origin="derived_file_containment", confidence=1.0,
                resolution=TargetResolution.EXACT)
            language = file_node["language"]
            for node in file_nodes:
                top_level = (
                    language in ("python", "typescript", "javascript")
                    and node["kind"] == "MODULE"
                ) or (
                    language == "fortran"
                    and node["kind"] in ("MODULE", "SUBMODULE", "PROGRAM")
                ) or (
                    language == "go" and node["kind"] == "PACKAGE"
                ) or (
                    language == "java"
                    and node["kind"] in ("CLASS", "INTERFACE", "ENUM")
                ) or (
                    language in ("c", "cpp")
                    and node["kind"] in ("FUNCTION", "TYPE", "ENUM")
                    and node["qname"] == node["name"]
                )
                if top_level:
                    self._add_supported_edge(
                        "CONTAINS", file_node["id"], node["id"], sid,
                        owner_path=path, origin="derived_top_level_containment",
                        confidence=1.0, resolution=TargetResolution.EXACT)

    def _regenerate_node_evidence_delta(self, sid: str,
                                        paths: list[str]) -> None:
        for path in paths:
            for node in self.db.nodes_by_path(self.repo_id, sid, path):
                self.db.add_evidence(
                    self.repo_id, sid, "node", node["id"],
                    source="tree_sitter", confidence=1.0,
                    location={"path": node["path"],
                              "start_line": node["start_line"],
                              "end_line": node["end_line"]},
                    payload={"parser_version": self.parser_version,
                             "indexer_version": self.indexer_version})

    def _regenerate_node_evidence(self, sid: str) -> None:
        self.db.delete_node_evidence_all(self.repo_id, sid)
        for n in self.db.all_nodes(self.repo_id, sid):
            self.db.add_evidence(
                self.repo_id, sid, "node", n["id"], source="tree_sitter",
                confidence=1.0,
                location={"path": n["path"], "start_line": n["start_line"],
                          "end_line": n["end_line"]},
                payload={"parser_version": self.parser_version,
                         "indexer_version": self.indexer_version})

    def _edge_evidence(self, pe: dict, source: str, conf: float) -> dict:
        return {"source": source, "confidence": conf,
                "location": {"path": pe["file_path"], "line": pe["line"]},
                "payload": {"parser_version": self.parser_version}}

    # ------------------------------------------------------------------
    def _telemetry(self, res: IndexResult, phase: str, key: str,
                   value) -> None:
        self.db.telemetry(res.run_id, res.repo_id, None, phase, key, value)
