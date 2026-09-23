"""Read-only, relation-scoped Design/Static/Runtime alignment.

The three states are derived independently.  In particular, neither a graph
miss nor a runtime miss is negative canonical evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from rintel.coverage_authority import applicable_complete
from rintel.design_lifecycle.models import DesignChange, DesignRevision
from rintel.runtime_alignment_evidence import (
    PROVIDER as BOUNDED_RUNTIME_PROVIDER, validate_bounded_artifact,
)


RULE_VERSION = "cross-layer-alignment/1"
RUNS_DIR = (Path(__file__).resolve().parents[2] /
            "analysis_tournament/runtime_trace0/runs")
RUNTIME_EVIDENCE_RUNS_DIR = (Path(__file__).resolve().parents[2] /
                             "analysis_tournament/runtime_alignment_evidence0/runs")


@dataclass(frozen=True)
class RelationIdentity:
    relation_id: str
    relation_kind: str
    source: str
    target: str
    semantic_scope: dict[str, Any]
    design_revision: str
    canonical_revision: str
    runtime_run_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["runtime_run_scopes"] = list(self.runtime_run_scopes)
        return value


@dataclass(frozen=True)
class RelationAlignment:
    relation_identity: RelationIdentity
    design: dict[str, Any]
    static: dict[str, Any]
    runtime: tuple[dict[str, Any], ...]
    alignment: dict[str, Any]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_identity": self.relation_identity.to_dict(),
            "design": deepcopy(self.design), "static": deepcopy(self.static),
            "runtime": deepcopy(list(self.runtime)),
            "alignment": deepcopy(self.alignment),
            "evidence_refs": list(self.evidence_refs),
        }


def _claim_relation(claim: Mapping[str, Any]) -> tuple[str, str, str] | None:
    evidence = claim.get("expected_evidence")
    if not isinstance(evidence, Mapping):
        return None
    kind = evidence.get("kind")
    source = evidence.get("source")
    target = evidence.get("target")
    if not all(isinstance(x, str) and x for x in (kind, source, target)):
        return None
    if (claim.get("relation_kind") != kind or claim.get("source") != source
            or claim.get("target") != target):
        return None
    return kind, source, target


def identity_for_claim(change: DesignChange, claim: Mapping[str, Any],
                       canonical_revision: str) -> RelationIdentity | None:
    """Only exact, explicit relation claims receive an alignment identity."""
    triple = _claim_relation(claim)
    if triple is None or claim.get("kind") not in {
            "planned_relation", "forbidden_relation", "removed_relation"}:
        return None
    evidence = claim["expected_evidence"]
    scope = {key: evidence[key] for key in (
        "call_semantics", "translation_unit", "build_context",
        "source_revision", "binary_sha256", "build_identity",
        "runtime_relation_semantics", "bridge_receipt_id",
        "instrumentation_identity", "runtime_provider") if key in evidence}
    if scope.get("call_semantics") == "DIRECT_STATIC_CALL":
        scope["static_scope_type"] = "EXACT_TU_FUNCTION_BODY"
    run_ids = claim.get("runtime_run_ids", ())
    if not isinstance(run_ids, (tuple, list)) or any(
            not isinstance(x, str) or not x for x in run_ids):
        run_ids = ()
    payload = [change.id, change.design_revision.id, canonical_revision,
               triple, scope, sorted(set(run_ids))]
    relation_id = "alignment:" + hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RelationIdentity(relation_id, *triple, scope,
                            change.design_revision.id, canonical_revision,
                            tuple(dict.fromkeys(run_ids)))


def identity_for_selector(change: DesignChange, selector: Mapping[str, Any],
                          canonical_revision: str) -> RelationIdentity:
    """Read-only exact relation selector, including relations absent from design."""
    triple = tuple(selector.get(key) for key in (
        "relation_kind", "source", "target"))
    if not all(isinstance(x, str) and x for x in triple):
        raise ValueError("relation selector needs exact kind, source and target")
    allowed = {"call_semantics", "translation_unit", "build_context",
               "source_revision", "binary_sha256", "build_identity",
               "runtime_relation_semantics", "bridge_receipt_id",
               "instrumentation_identity", "runtime_provider"}
    scope = {key: selector[key] for key in allowed if key in selector}
    if any(not isinstance(x, str) or not x for x in scope.values()):
        raise ValueError("relation selector scope must use nonempty exact strings")
    if scope.get("call_semantics") == "DIRECT_STATIC_CALL":
        scope["static_scope_type"] = "EXACT_TU_FUNCTION_BODY"
    run_ids = selector.get("runtime_run_ids", ())
    if not isinstance(run_ids, (tuple, list)) or any(
            not isinstance(x, str) or not x for x in run_ids):
        raise ValueError("runtime_run_ids must be an array of nonempty IDs")
    payload = [change.id, change.design_revision.id, canonical_revision,
               triple, scope, sorted(set(run_ids))]
    relation_id = "alignment:" + hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RelationIdentity(relation_id, *triple, scope,
                            change.design_revision.id, canonical_revision,
                            tuple(dict.fromkeys(run_ids)))


def derive_design(revision: DesignRevision,
                  identity: RelationIdentity) -> dict[str, Any]:
    """A different DesignRevision can never reinterpret a pinned identity."""
    support: list[dict[str, Any]] = []
    if revision.id == identity.design_revision:
        for claim in revision.expected_changes:
            if _claim_relation(claim) != (
                    identity.relation_kind, identity.source, identity.target):
                continue
            evidence = claim["expected_evidence"]
            if any(evidence.get(key) != identity.semantic_scope.get(key)
                   for key in ("call_semantics", "translation_unit",
                               "build_context")):
                continue
            if claim.get("kind") in {
                    "planned_relation", "forbidden_relation", "removed_relation"}:
                support.append(dict(claim))
    kinds = {x["kind"] for x in support}
    if kinds == {"planned_relation"}:
        state = "EXPECTED"
        reason = "explicit_required_relation"
    elif kinds and kinds <= {"forbidden_relation", "removed_relation"}:
        state = "NOT_EXPECTED"
        reason = "explicit_forbidden_or_removed_relation"
    else:
        state = "UNSPECIFIED"
        reason = ("conflicting_design_statements" if kinds else
                  "no_authoritative_exact_relation_statement")
    return {"state": state, "design_revision": identity.design_revision,
            "support": support, "reason": reason}


def derive_static(identity: RelationIdentity, *, store: Any,
                  certificate_repository: Any) -> dict[str, Any]:
    """Positive proof needs a published fact and admission support.

    Negative proof delegates to the frozen, live-input CoverageAuthority
    validator.  A certificate's mere presence is not enough.
    """
    out: dict[str, Any] = {
        "state": "UNKNOWN", "canonical_revision": identity.canonical_revision,
        "support_receipts": [], "coverage_certificate": None,
        "absence_domain": None, "reason": "canonical_revision_unavailable",
        "canonical_edge_id": None,
    }
    snap = store.snapshot(identity.canonical_revision)
    if not snap or snap.get("publication_status") != "published":
        return out
    repo_id = snap.get("repo_id")
    if not repo_id:
        return out
    out["reason"] = "no_positive_support_or_complete_absence_authority"
    graph_has_relation = False
    for row in store.all_edges(repo_id, identity.canonical_revision):
        if (row.get("kind"), row.get("src_id"), row.get("dst_id")) != (
                identity.relation_kind, identity.source, identity.target):
            continue
        graph_has_relation = True
        supports = [dict(x) for x in store.support_receipts(
            repo_id, identity.canonical_revision, row["id"])
            if x.get("canonical_revision") == identity.canonical_revision
            and x.get("canonical_fact_id") == row["id"]
            and x.get("canonical_fact_type") == "edge"
            and x.get("canonical_fact_kind") == identity.relation_kind]
        if identity.semantic_scope.get("call_semantics") == "DIRECT_STATIC_CALL":
            supports = [x for x in supports if x.get("lane_id") == "clang_provider"]
        if identity.semantic_scope.get("build_context") is not None:
            supports = [x for x in supports if x.get("build_context_id") ==
                        identity.semantic_scope["build_context"]]
        if supports:
            out.update(state="PRESENT", support_receipts=supports,
                       reason="positive_admitted_canonical_relation",
                       canonical_edge_id=row["id"])
            return out
        out["reason"] = "edge_without_applicable_admitted_support"
    if graph_has_relation:
        return out
    if identity.semantic_scope.get("call_semantics") != "DIRECT_STATIC_CALL":
        return out
    certificates = certificate_repository.coverage_certificates(
        repo_id, identity.canonical_revision, identity.source,
        "DIRECT_STATIC_CALL")
    expected = {"kind": identity.relation_kind, "source": identity.source,
                "target": identity.target, **identity.semantic_scope}
    reasons: list[str] = []
    for certificate in certificates:
        applicable, reason = applicable_complete(
            certificate, repo_id=repo_id,
            revision=identity.canonical_revision, expected=expected,
            store=store)
        if applicable:
            out.update(state="MISSING", coverage_certificate=certificate,
                       absence_domain={
                           "scope_type": "EXACT_TU_FUNCTION_BODY",
                           "translation_unit": certificate.get("translation_unit"),
                           "build_context": certificate.get("build_context"),
                           "subject": certificate.get("subject"),
                           "canonical_revision": certificate.get("canonical_revision"),
                           "certificate_id": certificate.get("id")},
                       reason=reason)
            return out
        reasons.append(reason)
    out["reason"] = (";".join(sorted(set(reasons))) if reasons else
                     "no_exact_direct_call_certificate")
    return out


def _runtime_scope_reason(identity: RelationIdentity,
                          meta: Mapping[str, Any]) -> str | None:
    scope = identity.semantic_scope
    required = {"evidence_revision": identity.canonical_revision,
                "repo_revision": scope.get("source_revision"),
                "binary_sha256": scope.get("binary_sha256"),
                "build_identity": scope.get("build_identity"),
                "relation_semantics": scope.get("runtime_relation_semantics")}
    for key, expected in required.items():
        if not expected or meta.get(key) != expected:
            return f"{key}_missing_or_mismatch"
    for key, expected in (("bridge_receipt_id", scope.get("bridge_receipt_id")),
                          ("instrumentation_identity", scope.get(
                              "instrumentation_identity")),
                          ("trace_backend", scope.get("runtime_provider"))):
        if expected is not None and meta.get(key) != expected:
            return f"{key}_missing_or_mismatch"
    if meta.get("scenario_binding") != "EXACT":
        return "scenario_binding_not_exact"
    if not isinstance(meta.get("trace_window"), Mapping):
        return "trace_window_unavailable"
    if not meta.get("trace_backend") or not meta.get("workload_id"):
        return "runtime_provider_or_workload_unavailable"
    return None


def derive_runtime(identity: RelationIdentity, run_id: str,
                   run_artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    """An inapplicable or provenance-limited run is UNMEASURED, not a miss."""
    meta = (run_artifact or {}).get("run")
    if not isinstance(meta, Mapping):
        meta = {}
    result: dict[str, Any] = {
        "state": "UNMEASURED", "run_id": run_id,
        "workload_id": meta.get("workload_id"),
        "trace_window": meta.get("trace_window"),
        "provider": meta.get("trace_backend"),
        "binary_identity": {"sha256": meta.get("binary_sha256"),
                            "build_identity": meta.get("build_identity"),
                            "compiler": meta.get("compiler_identity"),
                            "instrumentation_identity": meta.get(
                                "instrumentation_identity")},
        "runtime_coverage_receipt_id": (meta.get("relation_coverage") or {}).get(
            "receipt_id") if isinstance(meta.get("relation_coverage"), Mapping) else None,
        "evidence_refs": [], "limitations": [],
        "identity_binding": {key: meta.get(key) for key in (
            "evidence_revision", "repo_revision", "binary_sha256",
            "build_identity", "relation_semantics", "scenario_binding",
            "bridge_receipt_id", "instrumentation_identity", "trace_backend")},
    }
    reason = _runtime_scope_reason(identity, meta)
    if reason:
        result["limitations"].append(reason)
        return result
    if meta.get("run_id") != run_id:
        result["limitations"].append("run_identity_mismatch")
        return result
    if meta.get("trace_backend") == BOUNDED_RUNTIME_PROVIDER:
        valid, validation_reason = validate_bounded_artifact(run_artifact or {})
        if not valid:
            result["limitations"].append(validation_reason)
            return result
        result["evidence_refs"].append(str(meta["relation_coverage"][
            "receipt_id"]))
    edges = (run_artifact or {}).get("edges")
    if not isinstance(edges, list):
        result["limitations"].append("runtime_edges_unavailable")
        return result
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            continue
        if edge.get("parent_binding") != "EXACT" or edge.get(
                "child_binding") != "EXACT":
            continue
        if (edge.get("parent_canonical"), edge.get("child_canonical")) == (
                identity.source, identity.target) and (
                identity.semantic_scope.get("bridge_receipt_id") is None or
                edge.get("relation_bridge_receipt_id") ==
                identity.semantic_scope["bridge_receipt_id"]):
            result["state"] = "OBSERVED"
            result["evidence_refs"].append(f"{run_id}:edge:{index}")
    integrity = meta.get("integrity")
    integrity_status = (integrity.get("status")
                        if isinstance(integrity, Mapping) else None)
    if result["state"] == "OBSERVED":
        if integrity_status != "COMPLETE":
            result["limitations"].append("trace_integrity_partial")
        return result
    coverage = meta.get("relation_coverage")
    if (integrity_status == "COMPLETE"
            and isinstance(coverage, Mapping)
            and coverage.get("status") == "COMPLETE"
            and coverage.get("relation_kind") == identity.relation_kind
            and coverage.get("source") == identity.source
            and coverage.get("target", identity.target) == identity.target):
        result["state"] = "NOT_OBSERVED"
        result["limitations"].extend([
            "bounded_workload_and_trace_window_only",
            "not_a_static_or_never_executed_claim"])
    else:
        result["limitations"].append("relation_trace_coverage_not_complete")
    return result


def _load_run(run_id: str, directory: Path) -> dict[str, Any] | None:
    # Match the run's declared identity, never a user-controlled path.
    for root in (RUNTIME_EVIDENCE_RUNS_DIR, directory):
        for path in root.glob("run-*.json"):
            if path.stem != run_id and path.stem.lower() != run_id.lower():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return value if isinstance(value, dict) else None
    return None


def derive_alignment(design: Mapping[str, Any], static: Mapping[str, Any],
                     runtime: tuple[Mapping[str, Any], ...],
                     identity: RelationIdentity) -> dict[str, Any]:
    d, s = design["state"], static["state"]
    states = {x["state"] for x in runtime} or {"UNMEASURED"}
    results: list[str] = []
    if d == "EXPECTED":
        results.append({"PRESENT": "IMPLEMENTED", "MISSING": "IMPLEMENTATION_GAP",
                        "UNKNOWN": "STATIC_EVIDENCE_GAP"}[s])
        if s == "PRESENT":
            for state, label in (("OBSERVED", "IMPLEMENTED_AND_OBSERVED"),
                                 ("NOT_OBSERVED", "IMPLEMENTED_RUNTIME_NOT_OBSERVED"),
                                 ("UNMEASURED", "IMPLEMENTED_RUNTIME_UNMEASURED")):
                if state in states:
                    results.append(label)
        if s == "UNKNOWN" and "OBSERVED" in states:
            results.append("STATIC_ANALYSIS_GAP_WITH_RUNTIME_OBSERVATION")
    elif d == "NOT_EXPECTED" and s == "PRESENT":
        results.append("UNEXPECTED_IMPLEMENTATION")
    else:
        results.append("NO_DEFECT_INFERRED")
    contradiction = None
    if s == "MISSING" and "OBSERVED" in states:
        results.append("CROSS_LAYER_CONTRADICTION")
        cert = static.get("coverage_certificate") or {}
        run_bindings = [x.get("identity_binding") or {} for x in runtime
                        if x["state"] == "OBSERVED"]
        scope = identity.semantic_scope
        def check(expected: Any, observed: Any) -> dict[str, Any]:
            def incomplete(value: Any) -> bool:
                if value is None:
                    return True
                if isinstance(value, dict):
                    return any(incomplete(x) for x in value.values())
                if isinstance(value, list):
                    return not value or any(incomplete(x) for x in value)
                return False
            if incomplete(expected) or incomplete(observed):
                status = "UNKNOWN"
            else:
                status = "MATCH" if expected == observed else "MISMATCH"
            return {"expected": expected, "observed": observed,
                    "status": status}
        domain = cert.get("included_domain") or {}
        contradiction = {
            "status": "REQUIRES_AUDIT", "rule_version": RULE_VERSION,
            "checks": {
                "relation_semantics": check(
                    scope.get("call_semantics"), cert.get("relation_kind")),
                "coverage_certificate_domain": check(
                    {"subject": identity.source,
                     "relation": scope.get("call_semantics"),
                     "translation_unit": scope.get("translation_unit")},
                    {"subject": domain.get("subject"),
                     "relation": domain.get("relation"),
                     "translation_unit": domain.get("translation_unit")}),
                "build_context": check(scope.get("build_context"),
                                       cert.get("build_context")),
                "canonical_revision": check(
                    {"certificate": identity.canonical_revision,
                     "runtime": [identity.canonical_revision] * len(run_bindings)},
                    {"certificate": cert.get("canonical_revision"),
                     "runtime": [x.get("evidence_revision")
                                 for x in run_bindings]}),
                "runtime_binary_source_identity": check(
                    [{"source_revision": scope.get("source_revision"),
                      "binary_sha256": scope.get("binary_sha256")}
                     for _ in run_bindings],
                    [{"source_revision": x.get("repo_revision"),
                      "binary_sha256": x.get("binary_sha256")}
                     for x in run_bindings]),
                "runtime_relation_semantics": check(
                    [scope.get("runtime_relation_semantics")]
                    * len(run_bindings),
                    [x.get("relation_semantics") for x in run_bindings]),
                "cross_language_bridge_identity": check(
                    [scope.get("bridge_receipt_id")] * len(run_bindings),
                    [x.get("bridge_receipt_id") for x in run_bindings]),
            },
            "certificate_id": cert.get("id"),
            "runtime_run_ids": [x["run_id"] for x in runtime
                                if x["state"] == "OBSERVED"],
        }
    expected_actual = ("MATCHED" if d == "EXPECTED" and s == "PRESENT"
                       else "MISSING_IMPLEMENTATION" if d == "EXPECTED" and s == "MISSING"
                       else "UNEXPECTED_IMPLEMENTATION" if d == "NOT_EXPECTED" and s == "PRESENT"
                       else "UNKNOWN")
    return {"results": results, "expected_actual": expected_actual,
            "contradiction_audit": contradiction, "rule_version": RULE_VERSION}


def project_relation(change: DesignChange, claim: Mapping[str, Any], *,
                     canonical_revision: str, store: Any,
                     certificate_repository: Any,
                     run_artifacts: Mapping[str, Mapping[str, Any]] | None = None,
                     runs_dir: Path = RUNS_DIR) -> RelationAlignment | None:
    identity = identity_for_claim(change, claim, canonical_revision)
    if identity is None:
        return None
    return project_identity(change, identity, store=store,
                            certificate_repository=certificate_repository,
                            run_artifacts=run_artifacts, runs_dir=runs_dir)


def project_identity(change: DesignChange, identity: RelationIdentity, *,
                     store: Any, certificate_repository: Any,
                     run_artifacts: Mapping[str, Mapping[str, Any]] | None = None,
                     runs_dir: Path = RUNS_DIR) -> RelationAlignment:
    design = derive_design(change.design_revision, identity)
    static = derive_static(identity, store=store,
                           certificate_repository=certificate_repository)
    runs = tuple(derive_runtime(identity, run_id,
                 (run_artifacts.get(run_id) if run_artifacts is not None else
                  _load_run(run_id, runs_dir)))
                 for run_id in identity.runtime_run_scopes)
    alignment = derive_alignment(design, static, runs, identity)
    refs = [x.get("id") for x in design["support"] if x.get("id")]
    refs.extend(x.get("support_receipt_id") for x in static["support_receipts"])
    if static["coverage_certificate"]:
        refs.append(static["coverage_certificate"]["id"])
    refs.extend(ref for run in runs for ref in run["evidence_refs"])
    return RelationAlignment(identity, design, static, runs, alignment,
                             tuple(str(x) for x in refs if x))


def project_change_alignments(change: DesignChange, *, store: Any,
                              certificate_repository: Any,
                              relation_selector: Mapping[str, Any] | None = None
                              ) -> list[dict[str, Any]]:
    revision = ((change.implementation or {}).get("current_evidence_revision")
                or change.base_canonical_revision)
    rows = []
    for claim in change.design_revision.expected_changes:
        row = project_relation(change, claim, canonical_revision=revision,
                               store=store,
                               certificate_repository=certificate_repository)
        if row is not None:
            rows.append(row.to_dict())
    if relation_selector is not None:
        identity = identity_for_selector(change, relation_selector, revision)
        if not any(x["relation_identity"]["relation_id"] == identity.relation_id
                   for x in rows):
            rows.append(project_identity(change, identity, store=store,
                                         certificate_repository=certificate_repository
                                         ).to_dict())
    return rows


__all__ = ["RelationIdentity", "RelationAlignment", "derive_design",
           "derive_static", "derive_runtime", "derive_alignment",
           "project_relation", "project_identity", "project_change_alignments",
           "identity_for_selector"]
