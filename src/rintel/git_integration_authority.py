"""Trusted semantic/execution authority for one exact Git integration head.

Candidate evidence is built in a physically separate SQLite store.  The
production store is touched only by ``observe_merged_commit`` after Git merge.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .cross_layer_alignment import project_change_alignments
from .coverage_authority.issuer import CoverageAuthority, CoverageUnavailable
from .clang_provider.frontend import FrontendError
from .db import Database
from .design_lifecycle import DesignLifecycleService
from .design_lifecycle.design_drc import run_design_drc
from .execution_authority import ExecutionAuthority, ExecutionError, ExecutionRequest
from .indexer import Indexer
from .lvs.engine import run_lvs
from .lvs.flow_runner import _edges_for, design_from_flow_dto
from .lvs.signatures import build_code_side_from_rows


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class GitIntegrationAuthority:
    """One interface for candidate audit and post-merge observation."""

    RULE_VERSION = "git-integration-authority/1"

    def __init__(self, state_root: str | Path, *, production_store: Any,
                 execution_authority: ExecutionAuthority):
        self.state_root = Path(state_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.production_store = production_store
        self.execution_authority = execution_authority

    def _candidate_path(self, integration: dict) -> Path:
        identity = _digest({key: integration[key] for key in (
            "repository_identity", "integration_id", "target_head",
            "integration_head", "candidate_commits")})
        return self.state_root / "candidates" / f"{identity}.sqlite"

    @staticmethod
    def _receipt(kind: str, integration: dict, payload: dict) -> dict:
        value = {
            "schema_version": "integration-authority-receipt/1",
            "receipt_id": f"integration-{kind.lower()}-{uuid.uuid4().hex}",
            "kind": kind, "issuer": "Rintel",
            "rule_version": GitIntegrationAuthority.RULE_VERSION,
            "integration_id": integration["integration_id"],
            "repository_identity": integration["repository_identity"],
            "source_git_commit": integration["integration_head"],
            **payload,
        }
        value["receipt_digest"] = _digest(value)
        return value

    def _lvs(self, change: Any, candidate: Database, snapshot_id: str,
             source_root: str) -> dict:
        ref = change.design_revision.flow_model_ref
        if ref is None:
            rows = candidate.all_nodes(change.repo_id, snapshot_id)
            edges = _edges_for(candidate, change.repo_id, snapshot_id,
                               source_root=source_root)
            code = build_code_side_from_rows(rows, edges, source_root, snapshot_id)
            result = run_lvs(
                {"snapshot_id": change.design_revision.id, "blocks": [],
                 "composites": [], "ports": [], "nets": [], "claims": []},
                {"snapshot_id": snapshot_id, "baseline_id": None,
                 "baseline_functions": {}, "baseline_edges": [],
                 "functions": code.functions, "edges": code.edges,
                 "resources": code.resources, "call_capability": "PARTIAL",
                 "data_capability": "PARTIAL"}).to_dict()
            result.update({"acceptable": result["overall_status"] == "MATCH",
                           "reason": "empty_design_revision_flow_surface",
                           "rule_version": "software-lvs/1",
                           "candidate_snapshot": snapshot_id})
            return result
        from .flow.service import FlowService
        dto = FlowService(self.production_store).get_flow(ref.identity)
        design = design_from_flow_dto(dto)
        rows = candidate.all_nodes(change.repo_id, snapshot_id)
        edges = _edges_for(candidate, change.repo_id, snapshot_id,
                           source_root=source_root)
        code = build_code_side_from_rows(rows, edges, source_root, snapshot_id)
        # Generic Flow control nets do not bind the certificate's exact
        # DIRECT_STATIC_CALL domain. Unresolved-count zero is not absence
        # authority, even when the graph has no missing targets.
        call_capability = "PARTIAL"
        result = run_lvs(design, {
            "snapshot_id": snapshot_id, "baseline_id": None,
            "baseline_functions": {}, "baseline_edges": [],
            "functions": code.functions, "edges": code.edges,
            "resources": code.resources, "call_capability": call_capability,
            "data_capability": "PARTIAL",
        }).to_dict()
        result.update({
            "acceptable": result["overall_status"] == "MATCH",
            "rule_version": "software-lvs/1",
            "design_revision_ref": asdict(ref),
            "candidate_snapshot": snapshot_id,
        })
        return result

    def _issue_bound_clang_support(
            self, change: Any, candidate: Database, snapshot_id: str,
            source_root: Path, integration_head: str) -> tuple[str, list[dict]]:
        """Ask the existing issuer about bound C control-net sources only.

        Design selects the audit scope, never the evidence result. Missing or
        unsuitable build context leaves the legacy projection UNKNOWN.
        """
        ref = change.design_revision.flow_model_ref
        compdb = source_root / "compile_commands.json"
        if ref is None or not compdb.is_file():
            return snapshot_id, []
        from .flow.service import FlowService
        design = design_from_flow_dto(
            FlowService(self.production_store).get_flow(ref.identity))
        bindings = {block["id"]: block.get("binding")
                    for block in design["blocks"]}
        subjects = sorted({bindings.get(net.get("source_block_id"))
                           for net in design["nets"]
                           if net.get("kind") == "control"} - {None})
        attempts: list[dict] = []
        for subject_id in subjects:
            node = candidate.node_by_id(change.repo_id, snapshot_id, subject_id)
            if not node or node.get("kind") != "FUNCTION" or node.get(
                    "language") != "c":
                continue
            try:
                cert = CoverageAuthority(candidate).issue_direct_static_call(
                    repo_id=change.repo_id, base_revision=snapshot_id,
                    compile_commands=compdb, translation_unit=node["path"],
                    subject_id=subject_id)
            except (CoverageUnavailable, FrontendError, ValueError, OSError) as exc:
                attempts.append({"subject": subject_id, "status": "UNAVAILABLE",
                                 "reason": str(exc)})
                continue
            issued = cert.to_dict()
            snapshot_id = issued["canonical_revision"]
            snapshot = candidate.snapshot(snapshot_id)
            if not snapshot or snapshot.get("commit_sha") != integration_head:
                raise RuntimeError("Clang support overlay lost exact integration commit")
            for attempt in attempts:
                if attempt["status"] == "ISSUED":
                    attempt["status"] = "SUPERSEDED"
                    attempt["reason"] = "support receipt bound to earlier overlay revision"
            attempts.append({"subject": subject_id, "status": "ISSUED",
                             "certificate_id": issued["id"],
                             "canonical_revision": snapshot_id,
                             "completeness": issued["completeness"]})
        return snapshot_id, attempts

    def audit(self, integration: dict, *, build_profile_id: str,
              test_profile_id: str,
              build_parameters: tuple[tuple[str, str], ...] = (),
              test_parameters: tuple[tuple[str, str], ...] = ()) -> dict:
        """Produce a complete merge projection from trusted issuers only."""
        source_root = Path(integration["integration_worktree"]).resolve()
        change = DesignLifecycleService(self.production_store).get_change(
            self._change_id(integration))
        candidate_path = self._candidate_path(integration)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate = Database(candidate_path)
        try:
            result = Indexer(
                candidate, str(source_root), repo_id=change.repo_id, force=True,
                commit=integration["integration_head"]).index()
            candidate_revision, clang_support = self._issue_bound_clang_support(
                change, candidate, result.snapshot_id, source_root,
                integration["integration_head"])
            snapshot = candidate.snapshot(candidate_revision) or {}
            if snapshot.get("commit_sha") != integration["integration_head"]:
                raise RuntimeError("candidate snapshot is not bound to integration commit")
            candidate_receipt = self._receipt("CANDIDATE_INDEX", integration, {
                "tree_sha": integration["integration_tree"],
                "indexer_run_id": result.run_id,
                "candidate_canonical_revision": candidate_revision,
                "clang_support_attempts": clang_support,
                "candidate_store_identity": f"sha256:{_digest(str(candidate_path))}",
                "publication_policy": "CANDIDATE_ONLY",
                "production_current_before": self.production_store.current_snapshot(
                    change.repo_id),
                "snapshot_commit": snapshot.get("commit_sha"),
            })
            drc = run_design_drc(change.design_revision.expected_changes, change.scope)
            drc_receipt = self._receipt("DRC", integration, {
                "design_revision": change.design_revision.id,
                "candidate_canonical_revision": candidate_revision,
                "inputs": {
                    "design_revision": change.design_revision.id,
                    "scope_digest": _digest(change.scope),
                    "candidate_snapshot": candidate_revision,
                    "candidate_nodes": len(candidate.all_nodes(
                        change.repo_id, candidate_revision)),
                    "candidate_edges": len(candidate.all_edges(
                        change.repo_id, candidate_revision)),
                },
                "result": drc,
            })
            lvs = self._lvs(change, candidate, candidate_revision, str(source_root))
            lvs_receipt = self._receipt("LVS", integration, {
                "design_revision": change.design_revision.id,
                "candidate_canonical_revision": candidate_revision,
                "result": lvs,
            })
            candidate_change = replace(
                change, implementation={**(change.implementation or {}),
                                        "current_evidence_revision": candidate_revision})
            alignments = project_change_alignments(
                candidate_change, store=candidate,
                certificate_repository=candidate)
            alignment_receipt = self._receipt("ALIGNMENT", integration, {
                "design_revision": change.design_revision.id,
                "candidate_canonical_revision": candidate_revision,
                "runtime_binding": "EXACT_OR_UNMEASURED",
                "results": alignments,
            })
            unresolved_unknowns = [
                item["relation_identity"]["relation_id"] for item in alignments
                if item["alignment"].get("expected_actual") == "UNKNOWN"
                or item["alignment"].get("contradiction_audit") is not None]
            integration = {**integration,
                           "candidate_canonical_revision": candidate_revision}
            build = self._execute(
                integration, change, build_profile_id, "BUILD", build_parameters)
            test = self._execute(
                integration, change, test_profile_id, "TEST", test_parameters)
            unsupported = candidate.unsupported_new_facts(
                change.repo_id, candidate_revision, None)
            semantic = {
                "candidate_canonical_identity": candidate_revision,
                "unsupported_fact_ids": unsupported,
                "unresolved_count": candidate.unresolved_count(
                    change.repo_id, candidate_revision),
                "support_receipt_count": len(candidate.support_receipts_owned_by_paths(
                    change.repo_id, candidate_revision, [""])),
            }
        finally:
            candidate.close()

        diagnostics = []
        if integration["git_status"] != "CLEAN_MERGE":
            diagnostics.append(integration["git_status"])
        if integration.get("agent_scope_status") != "IN_SCOPE":
            diagnostics.append("SCOPE_VIOLATION")
        if not drc["acceptable"]:
            diagnostics.append("DRC_FAILURE")
        if not lvs.get("acceptable"):
            diagnostics.append(
                "LVS_UNKNOWN" if lvs.get("overall_status") == "UNKNOWN"
                else "LVS_MISMATCH")
        if build["result"] != "PASS":
            diagnostics.append("BUILD_FAILURE")
        if test["result"] != "PASS":
            diagnostics.append("TEST_FAILURE")
        if semantic["unsupported_fact_ids"]:
            diagnostics.append("UNSUPPORTED_CANONICAL_FACT")
        if unresolved_unknowns:
            diagnostics.append("EVIDENCE_UNKNOWN")
        production_after = self.production_store.current_snapshot(change.repo_id)
        if production_after != candidate_receipt["production_current_before"]:
            diagnostics.append("CANDIDATE_MUTATED_CURRENT")
        projection = {
            "schema_version": "merge-candidate/2",
            **{key: integration[key] for key in (
                "integration_id", "repository_identity", "target_branch",
                "target_head", "merge_bases", "candidate_commits",
                "workspace_ids", "integration_head", "integration_tree",
                "git_status", "conflicted_files")},
            "scope": {"status": integration.get("agent_scope_status")},
            "semantic": semantic,
            "design": {"drc": drc_receipt, "lvs": lvs_receipt,
                       "alignment": alignment_receipt},
            "execution": {"build": build, "test": test},
            "candidate_index": candidate_receipt,
            "evidence": {"freshness": "FRESH",
                         "unresolved_unknowns": unresolved_unknowns},
            "evidence_freshness": "FRESH",
            "diagnostics": sorted(set(diagnostics)),
            "merge_eligibility": "MERGE_ELIGIBLE" if not diagnostics
            else "NOT_MERGE_ELIGIBLE",
        }
        projection["evidence_identity"] = _digest({
            "target_head": integration["target_head"],
            "integration_head": integration["integration_head"],
            "candidate": candidate_receipt["receipt_digest"],
            "drc": drc_receipt["receipt_digest"],
            "lvs": lvs_receipt["receipt_digest"],
            "alignment": alignment_receipt["receipt_digest"],
            "build": build["execution_id"], "test": test["execution_id"],
        })
        return projection

    @staticmethod
    def _change_id(integration: dict) -> str:
        value = integration.get("change_id")
        if not value:
            raise ValueError("integration has no DesignChange identity")
        return value

    def _execute(self, integration: dict, change: Any, profile_id: str,
                 kind: str, parameters: tuple[tuple[str, str], ...]) -> dict:
        return self.execution_authority.execute_in_git_worktree(
            ExecutionRequest(
                profile_id=profile_id, kind=kind, parameters=parameters,
                change_id=change.id,
                canonical_revision=integration["candidate_canonical_revision"],
                design_revision=change.design_revision.id,
                lineage_ref=f"integration:{integration['integration_id']}"),
            workspace_root=integration["integration_worktree"],
            expected_git_commit=integration["integration_head"],
            workspace_id=integration["integration_id"],
            principal_id="rintel-integration-authority")

    def observe_merged_commit(self, integration: dict) -> dict:
        """Index one already-merged exact SHA into the production store."""
        merged = integration.get("merged_commit")
        if not merged:
            raise ValueError("integration has not been merged")
        change = DesignLifecycleService(self.production_store).get_change(
            self._change_id(integration))
        before = self.production_store.current_snapshot(change.repo_id)
        try:
            result = Indexer(
                self.production_store, integration["repository_root"],
                repo_id=change.repo_id, commit=merged).index()
            current = self.production_store.current_snapshot(change.repo_id)
            snapshot = self.production_store.snapshot(current) if current else None
            if current != result.snapshot_id or not snapshot or snapshot.get(
                    "commit_sha") != merged:
                raise RuntimeError("published snapshot is not bound to merged Git commit")
            current, clang_support = self._issue_bound_clang_support(
                change, self.production_store, current,
                Path(integration["repository_root"]).resolve(), merged)
            snapshot = self.production_store.snapshot(current) or {}
            if (self.production_store.current_snapshot(change.repo_id) != current
                    or snapshot.get("commit_sha") != merged):
                raise RuntimeError("production support lost exact merged commit")
            unsupported = self.production_store.unsupported_new_facts(
                change.repo_id, current, before)
            if unsupported:
                raise RuntimeError("production publication lacks canonical support")
            support_count = len(self.production_store.support_receipts_owned_by_paths(
                change.repo_id, current, [""]))
            return self._receipt("PRODUCTION_OBSERVATION", integration, {
                "status": "CANONICAL_CURRENT_OBSERVED",
                "merged_git_commit": merged,
                "production_current_before": before,
                "production_current_after": current,
                "published_canonical_source_git_commit": snapshot["commit_sha"],
                "indexer_run_id": result.run_id,
                "clang_support_attempts": clang_support,
                "canonical_support_receipt_count": support_count,
                "unsupported_canonical_fact_ids": [],
            })
        except Exception as exc:
            current = self.production_store.current_snapshot(change.repo_id)
            status = ("MERGED_BUT_NOT_OBSERVED" if current == before else
                      "PUBLICATION_INVARIANT_VIOLATION")
            return self._receipt("PRODUCTION_OBSERVATION", integration, {
                "status": status,
                "merged_git_commit": merged,
                "production_current_before": before,
                "production_current_after": current,
                "error": type(exc).__name__,
            })
