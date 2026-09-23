"""Read-only, receipt-bound Change Workspace projection.

This module never admits evidence or mutates design/canonical state.  Missing
dimensions stay UNKNOWN; a producer's payload is provenance, not authority.
"""
from __future__ import annotations

import json
from typing import Any

from rintel.cross_layer_alignment import project_change_alignments

from .service import DesignLifecycleService
from .source_projection import project_source_span


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _canonical_evidence(store: Any, repo_id: str, revision: str,
                        entity_type: str, entity: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(entity.get("id") or "")
    rows = [row for row in store.evidence_for(repo_id, revision, entity_id)
            if row.get("entity_type") == entity_type]
    provenance = [{
        "source": row.get("source"),
        "location": _json_object(row.get("location_json")),
        "payload": _json_object(row.get("payload_json")),
        "confidence": row.get("confidence"),
    } for row in rows]
    supports = [receipt for receipt in store.support_receipts(
        repo_id, revision, entity_id)
        if receipt.get("canonical_fact_type") == entity_type
        and receipt.get("canonical_revision") == revision]
    dimensions = ("truth_class", "target_resolution", "coverage",
                  "execution_modality")
    def consensus(key: str) -> str:
        found = {str(receipt.get(key, "UNKNOWN")) for receipt in supports}
        return next(iter(found)) if len(found) == 1 else "UNKNOWN"

    values = {key: consensus(key) for key in dimensions}
    limitations = sorted({str(item) for receipt in supports
                          for item in receipt.get("limitations", [])})
    if supports and any(len({str(item.get(key, "UNKNOWN"))
                             for item in supports}) > 1 for key in dimensions):
        limitations.append("admitted supports disagree; no dimension was promoted by vote")
    if not supports:
        limitations.append("LEGACY_UNKNOWN: no durable admission support for this fact")
    refs = [f"{revision}:{entity_type}:{entity_id}:{i}"
            for i in range(len(rows))]
    if entity_type == "node":
        span = project_source_span(
            entity, revision=revision, subject=entity_id,
            entity_type=entity_type, evidence_ref=refs[0] if refs else None)
    else:
        locations = [x["location"] for x in provenance if x["location"]]
        span = (project_source_span(
            entity, revision=revision, subject=entity_id,
            entity_type=entity_type, evidence_ref=refs[0], location=locations[0])
            if len(locations) == 1 and len(refs) == 1 else None)
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "canonical_revision": revision,
        "authority": "CANONICAL_CANDIDATE",  # Rintel published graph, not producer claim
        "support_status": "ADMITTED" if supports else "LEGACY_UNKNOWN",
        **values,
        "provider": sorted({f"{item['producer']}@{item['producer_version']}"
                            for item in supports}),
        "support_receipts": supports,
        "provenance": provenance,
        "supporting_evidence_refs": refs,
        "source_span": span,
        "limitations": limitations,
    }


def _freshness(binding: dict[str, Any] | None, current: dict[str, Any],
               *, actual_sensitive: bool) -> str:
    if not binding:
        return "UNKNOWN"
    keys = ("baseline_revision", "design_revision", "scope_digest")
    if actual_sensitive:
        keys += ("actual_revision",)
    if any(binding.get(key) is None for key in keys):
        return "UNKNOWN"
    return ("CURRENT" if all(binding[key] == current.get(key) for key in keys)
            else "STALE")


def _comparison_result(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"outcome": "UNKNOWN", "rows": [], "negative_receipt": None}
    rows = []
    for field, status in (
        ("matched", "MATCHED"),
        ("missing", "MISSING_IMPLEMENTATION"),
        ("unproven_absence", "UNKNOWN"),
        ("unexpected", "UNEXPECTED_IMPLEMENTATION"),
    ):
        for item in raw.get(field, []):
            rows.append({
                "status": status,
                "claim": {key: item.get(key) for key in
                          ("kind", "source", "target")},
                "canonical_evidence_ref": item.get("id"),
            })
    return {
        "comparison_identity": raw.get("comparison_identity"),
        "outcome": raw.get("outcome", "UNKNOWN"),
        "rows": rows,
        "negative_receipt": {
            "searched_domain": raw.get("searched_domain"),
            "coverage": raw.get("negative_coverage", "UNKNOWN"),
            "certificate_refs": raw.get("coverage_certificate_refs", []),
            "certificates": raw.get("coverage_certificates", []),
            "limitations": raw.get("coverage_limitations", []),
            "observed_positive_evidence_refs": raw.get(
                "observed_positive_evidence_refs", []),
            "rule_version": raw.get("rule_version"),
        },
    }


def workspace_projection(service: DesignLifecycleService,
                         change_id: str,
                         relation_selector: dict[str, Any] | None = None,
                         authorization_identity: str | None = None,
                         ) -> dict[str, Any]:
    change = service.get_change(change_id)
    current = service._result_binding(change)
    observation_status = (change.implementation or {}).get(
        "observation_status", "previous")
    receipts = service.receipts(change_id)
    revision = (change.implementation or {}).get("current_evidence_revision")
    result_commands = {
        "plan": ("DRC", False),
        "evaluate_expected_actual": ("COMPARISON", True),
        "verify": ("VERIFICATION", True),
        "close": ("CLOSE", True),
    }
    artifacts: list[dict[str, Any]] = []
    independent = {kind: service.repo.independent_receipts(kind, change.id)
                   for kind in ("test", "approval")}
    for item in independent["test"]:
        binding = {**current, "design_revision": item["design_revision"],
                   "actual_revision": item["canonical_revision"]}
        fresh = ("CURRENT" if item["design_revision"] ==
                 change.design_revision.id and item["canonical_revision"] ==
                 revision and revision == service._active_revision(change.repo_id)
                 else "STALE")
        artifacts.append({"id": item["id"], "kind": "TESTS",
                          "binding": binding, "freshness": fresh,
                          "result": {"status": item["result"],
                                     "test_identity": item["test_identity"],
                                     "run_identity": item["run_identity"],
                                     "evidence_refs": item["evidence_refs"]},
                          "raw_diagnostic": None})
    for item in independent["approval"]:
        fresh = ("CURRENT" if item["design_revision"] ==
                 change.design_revision.id and item["change_version"] == change.version
                 else "STALE")
        artifacts.append({"id": item["id"], "kind": "APPROVAL",
                          "binding": {**current,
                                      "design_revision": item["design_revision"],
                                      "change_version": item["change_version"]},
                          "freshness": fresh,
                          "result": {"status": item["decision"],
                                     "approver_identity": item["approver_identity"],
                                     "policy_version": item["policy_version"]},
                          "raw_diagnostic": None})
    for receipt in receipts:
        if receipt.command not in result_commands:
            continue
        kind, actual_sensitive = result_commands[receipt.command]
        binding = receipt.payload.get("result_binding")
        freshness = _freshness(binding, current,
                               actual_sensitive=actual_sensitive)
        if actual_sensitive and binding and (
            binding.get("actual_revision") !=
            service._active_revision(change.repo_id)
        ):
            freshness = "STALE"
        if actual_sensitive and observation_status != "recorded":
            freshness = "STALE"
        if kind == "COMPARISON":
            artifacts.append({
                "id": receipt.id, "kind": "COMPARISON",
                "binding": binding, "freshness": freshness,
                "result": _comparison_result(receipt.payload.get("expected_actual")),
                "raw_diagnostic": None,
            })
            lvs = receipt.payload.get("lvs") or {}
            artifacts.append({
                "id": f"{receipt.id}:lvs", "kind": "LVS",
                "binding": ({**binding, "rule_version": lvs.get("rule_version")
                             or "UNKNOWN"} if binding else None),
                "freshness": freshness,
                "result": {"status": lvs.get("overall_status", "UNKNOWN"),
                           "reason": lvs.get("reason"),
                           "diff_count": len(lvs.get("diffs", []))},
                "raw_diagnostic": None,
            })
        else:
            payload_key = {"DRC": "drc", "VERIFICATION": "results",
                           "CLOSE": "verification_evidence"}[kind]
            raw = receipt.payload.get(payload_key)
            if kind == "DRC":
                raw = raw or {}
                result = {"status": ("PASS" if raw.get("acceptable") else "FAIL"),
                          "rule_version": raw.get("rule_version"),
                          "finding_count": len(raw.get("findings", []))}
            elif kind == "VERIFICATION":
                result = {"criteria": [{
                    "criterion_id": x.get("criterion_id"),
                    "status": x.get("status", "UNKNOWN"),
                    "evidence_refs": x.get("evidence_refs", []),
                } for x in (raw or [])]}
            else:
                result = {"status": "CLOSED",
                          "verification_evidence_count": len(raw or [])}
            artifacts.append({
                "id": receipt.id, "kind": kind,
                "binding": binding, "freshness": freshness,
                "result": result,
                "raw_diagnostic": None,
            })
    alignments = project_change_alignments(
        change, store=service.store,
        certificate_repository=service.repo,
        relation_selector=relation_selector)
    entities: list[tuple[str, str, dict[str, Any]]] = []
    for item in change.evidence_brief.symbols:
        entities.append(("node", change.base_canonical_revision, item))
    edge_revisions = {revision} if revision else set()
    edge_revisions.update(item["relation_identity"]["canonical_revision"]
                          for item in alignments if item["static"].get("canonical_edge_id"))
    for receipt in receipts:
        if receipt.command == "evaluate_expected_actual":
            sid = (receipt.payload.get("result_binding") or {}).get(
                "actual_revision")
            if sid:
                edge_revisions.add(sid)
    for sid in edge_revisions:
        edge_by_id = {x["id"]: x for x in service.store.all_edges(
            change.repo_id, sid)}
        selected_ids = (
             [x.get("id") for x in (change.implementation or {}).get(
                 "actual_evidence", [])] if sid == revision else [])
        selected_ids.extend(item["static"]["canonical_edge_id"]
                            for item in alignments
                            if item["relation_identity"]["canonical_revision"] == sid
                            and item["static"].get("canonical_edge_id"))
        for receipt in receipts:
            if receipt.command != "evaluate_expected_actual" or (
                (receipt.payload.get("result_binding") or {}).get("actual_revision") != sid
            ):
                continue
            selected_ids.extend(
                x.get("id") for x in
                (receipt.payload.get("expected_actual") or {}).get(
                    "actual_canonical_evidence", []))
        for edge_id in sorted(set(selected_ids) - {None}):
            row = edge_by_id.get(edge_id)
            if row:
                entities.append(("edge", sid, row))
    evidence = [_canonical_evidence(service.store, change.repo_id, sid, typ, row)
                for typ, sid, row in entities]
    run_receipts = [r for r in receipts if r.command in {
        "run_reindex", "adopt_index_job"}]
    latest_run = run_receipts[-1] if run_receipts else None
    run_binding = latest_run.payload.get("result_binding") if latest_run else None
    linked = (change.implementation or {}).get("index_job") or {}
    linked_job = (service.job_lookup(linked["job_id"])
                  if linked.get("job_id") and service.job_lookup else None)
    if linked.get("job_id") and not linked.get("adopted_at"):
        job_state = linked_job.get("status") if linked_job else None
        run_status = {"pending": "running", "running": "running",
                      "failed": "failed", "done": "completed"}.get(
                          job_state, "failed")
        if job_state == "done" and (
            (linked_job.get("result") or {}).get("snapshot_id") !=
            service._active_revision(change.repo_id)
        ):
            run_status = "stale_completed"
    else:
        run_status = ("not_requested" if latest_run is None else
                      "completed" if _freshness(run_binding, current,
                                                 actual_sensitive=False) == "CURRENT"
                      else "stale_completed")
        if latest_run and revision != service._active_revision(change.repo_id):
            run_status = "stale_completed"
    return {
        "schema_version": "change-workspace/1",
        "change": {
            "id": change.id, "repo_id": change.repo_id,
            "intent": change.intent, "state": change.state.value,
            "version": change.version, "scope": change.scope,
            "base_canonical_revision": change.base_canonical_revision,
            "acceptance_criteria": [{
                "id": x.id, "kind": x.kind, "required": x.required,
                "description": x.description,
            } for x in change.acceptance_criteria],
        },
        "design": {
            "revision_id": change.design_revision.id,
            "authority": change.design_revision.authority,
            "expected_claims": [{
                "id": x.get("id"),
                "kind": (x.get("expected_evidence") or {}).get("kind"),
                "source": (x.get("expected_evidence") or {}).get("source"),
                "target": (x.get("expected_evidence") or {}).get("target"),
                "justification": x.get("justification"),
            } for x in change.design_revision.expected_changes],
            "architecture_proposal_ref": (
                vars(change.design_revision.architecture_proposal_ref)
                if change.design_revision.architecture_proposal_ref else None),
            "flow_model_ref": (
                vars(change.design_revision.flow_model_ref)
                if change.design_revision.flow_model_ref else None),
        },
        "current_binding": current,
        "alignments": alignments,
        "artifacts": artifacts,
        "evidence": evidence,
        "activity": {
            "reindex": {"status": run_status,
                        "observation_status": observation_status,
                        "job_id": linked.get("job_id"),
                        "run_receipt_id": latest_run.id if latest_run else None,
                        "run_identity": ((change.implementation or {})
                                         .get("incremental_run") or {}).get("run_id"),
                        "produced_canonical_revision": (
                            revision if latest_run else None),
                        "adoption_status": (
                            "ADOPTED" if linked.get("adopted_at") else
                            "PENDING" if linked.get("job_id") else "UNAVAILABLE")},
            "receipts": [{"id": r.id, "command": r.command,
                          "actor": r.actor, "state_after": r.state_after,
                          "digest": r.digest, "created_at": r.created_at,
                          "binding": r.payload.get("result_binding")}
                         for r in receipts],
        },
        "eligibility": service.preflight(
            change_id, authorization_identity=authorization_identity),
        "source": {"resolver": "strict_identity_required",
                   "fallback": "FORBIDDEN"},
        "freshness": {
            kind: next((x["freshness"] for x in reversed(artifacts)
                        if x["kind"] == kind), "UNAVAILABLE")
            for kind in ("DRC", "LVS", "COMPARISON", "VERIFICATION", "CLOSE")
        } | {kind: next((x["freshness"] for x in reversed(artifacts)
                         if x["kind"] == kind), "UNAVAILABLE")
             for kind in ("TESTS", "APPROVAL")} | {
             "ACTUAL": ("UNAVAILABLE" if observation_status != "recorded" else
                        "CURRENT" if revision and
                        service._active_revision(change.repo_id) == revision
                        else "UNAVAILABLE" if not revision else "STALE")},
    }
