"""Versioned, bounded proof of static C direct-call presence for LVS.

This rule does not issue a new canonical fact or change any claim dimension.
In particular, static presence is independent of execution modality, runtime
observation, and the certificate used to prove bounded absence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from rintel.clang_provider.process import (
    PROVIDER_CONFIG_DIGEST, PROVIDER_ID, PROVIDER_VERSION,
)

from .engine import DEFAULT_ENGINE
from .model import AuthorityClass, _stable_digest
from .registry import UnknownLaneError


RULE_VERSION = "static-relation-presence/clang-direct-call/1"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _exact_source_in_compdb(compdb: Path, source: Path) -> bool:
    rows = json.loads(compdb.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        return False
    matches = 0
    for row in rows:
        if not isinstance(row, dict):
            return False
        path = Path(str(row.get("file", "")))
        if not path.is_absolute():
            path = Path(str(row.get("directory", ""))) / path
        matches += path.resolve() == source
    return matches == 1


def direct_static_call_presence(
        store: Any, repo_id: str, revision: str, edge: Mapping[str, Any],
        supports: list[Mapping[str, Any]], *, source_root: str | Path | None
        ) -> dict[str, Any]:
    """Read-only decision; only exact-bound admitted Clang CALLS can qualify."""
    unknown = {"state": "UNKNOWN", "rule_version": RULE_VERSION,
               "support_receipt_ids": [], "runtime_state": "UNMEASURED"}
    try:
        lane = DEFAULT_ENGINE.registry.require("clang_provider")
    except UnknownLaneError:
        return {**unknown, "reason": "clang_lane_unregistered"}
    if (lane.authority_class is not AuthorityClass.CANONICAL_CANDIDATE
            or not lane.canonical_ingestion or lane.producer != PROVIDER_ID):
        return {**unknown, "reason": "clang_lane_not_admissible"}
    if (source_root is None or edge.get("kind") != "CALLS"
            or not any(row.get("lane_id") == "clang_provider"
                       for row in supports)):
        return {**unknown, "reason": "unsupported_or_unbound_scope"}
    source, target = edge.get("src_id"), edge.get("dst_id")
    if not isinstance(source, str) or not isinstance(target, str) or (
            edge.get("id") != f"edge:CALLS:{source}:{target}"):
        return {**unknown, "reason": "canonical_edge_identity_mismatch"}
    snapshot = store.snapshot(revision)
    if not snapshot or snapshot.get("repo_id") != repo_id or snapshot.get(
            "publication_status") != "published":
        return {**unknown, "reason": "canonical_revision_unavailable"}
    source_node = store.node_by_id(repo_id, revision, source)
    target_node = store.node_by_id(repo_id, revision, target)
    if any(not node or node.get("kind") != "FUNCTION" or node.get(
            "language") != "c" for node in (source_node, target_node)):
        return {**unknown, "reason": "c_function_identity_unavailable"}
    root = Path(source_root).resolve()
    qualifying: list[tuple[str, dict[str, str]]] = []
    for receipt in supports:
        if (receipt.get("schema_version") != "canonical-support-receipt/1"
                or receipt.get("canonical_revision") != revision
                or receipt.get("canonical_fact_id") != edge["id"]
                or receipt.get("canonical_fact_type") != "edge"
                or receipt.get("canonical_fact_kind") != "CALLS"
                or receipt.get("source_ids") != [source, target]
                or receipt.get("lane_id") != "clang_provider"
                or receipt.get("producer") != PROVIDER_ID
                or receipt.get("producer_version") != PROVIDER_VERSION
                or receipt.get("registry_version") != DEFAULT_ENGINE.registry.version
                or receipt.get("rule_version") != DEFAULT_ENGINE.rule_version
                or receipt.get("truth_class") not in {"OBSERVED", "RESOLVED"}
                or receipt.get("target_resolution") != "EXACT"):
            continue
        envelope = receipt.get("admitted_envelope") or {}
        claim = envelope.get("claim") or {}
        provenance = receipt.get("provenance") or {}
        owner = receipt.get("owner_path")
        span = receipt.get("source_span") or {}
        admission_id = _stable_digest(envelope)
        support_id = "support:" + hashlib.sha256(json.dumps(
            [revision, edge["id"], admission_id], sort_keys=True,
            separators=(",", ":"), default=str).encode()).hexdigest()
        if (envelope.get("lane_id") != "clang_provider"
                or envelope.get("producer") != PROVIDER_ID
                or envelope.get("producer_version") != PROVIDER_VERSION
                or receipt.get("admission_id") != admission_id
                or receipt.get("support_receipt_id") != support_id
                or receipt.get("evidence_id") != claim.get("claim_id")
                or claim.get("subject") != source
                or claim.get("predicate") != "CALLS"
                or claim.get("object") != target
                or claim.get("resolution") != "EXACT"
                or claim.get("truth_class") not in {"OBSERVED", "RESOLVED"}
                or claim.get("truth_class") != receipt.get("truth_class")
                or claim.get("coverage") != receipt.get("coverage")
                or claim.get("execution_modality") != receipt.get(
                    "execution_modality")
                or claim.get("source_span") != span
                or not isinstance(owner, str)
                or receipt.get("scope") != envelope.get("scope")
                or owner not in (envelope.get("scope") or [])
                or source_node.get("path") != owner
                or span.get("file") != owner
                or not isinstance(span.get("start_line"), int)
                or span["start_line"] < 1
                or not receipt.get("build_context")
                or not provenance.get("analysis_id")
                or not provenance.get("tu_identity")
                or not provenance.get("raw_command_digest")
                or not provenance.get("clang_binary_sha256")
                or provenance.get("provider_config_digest") != PROVIDER_CONFIG_DIGEST
                or provenance.get("build_context_id") != receipt.get(
                    "build_context")
                or receipt.get("source_digest") != provenance.get("source_digest")):
            continue
        try:
            path = (root / owner).resolve()
            path.relative_to(root)
            compdb = Path(str(provenance["compile_commands_path"])).resolve()
            clang = Path(str(provenance["clang_executable"])).resolve()
            dependencies = provenance["dependency_digests"]
            if not isinstance(dependencies, dict) or not _exact_source_in_compdb(
                    compdb, path):
                continue
            fresh = (
                _sha(path) == receipt.get("source_digest")
                and _sha(compdb) == provenance.get("compile_commands_sha256")
                and _sha(clang) == provenance.get("clang_binary_sha256")
                and all(_sha(Path(dep)) == digest
                        for dep, digest in dependencies.items()))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if fresh:
            qualifying.append((str(receipt["support_receipt_id"]), {
                "truth_class": str(receipt["truth_class"]),
                "target_resolution": str(receipt["target_resolution"]),
                "coverage": str(receipt["coverage"]),
                "execution_modality": str(receipt["execution_modality"]),
            }))
    if not qualifying:
        return {**unknown, "reason": "no_fresh_exact_clang_admission"}
    return {"state": "PRESENT", "rule_version": RULE_VERSION,
            "support_receipt_ids": sorted({item[0] for item in qualifying}),
            "evidence_authority": lane.authority_class.value,
            "provider_status": lane.status,
            "support_dimensions": [
                {"support_receipt_id": receipt_id, **dimensions}
                for receipt_id, dimensions in sorted(qualifying)],
            "runtime_state": "UNMEASURED",
            "reason": "exact_bound_clang_direct_call_admission"}


__all__ = ["RULE_VERSION", "direct_static_call_presence"]
