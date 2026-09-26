"""SOFTWARE-LVS1 runner over a real stored FlowModel (product path).

Design side comes from the stored flow (FlowService.get_flow DTO); the
code side comes from the canonical index + the REAL source tree at the
repo root (never from AnalysisFact mutation).  Used by the API endpoint
POST /flows/{id}/lvs and the UI Validate/LVS action (§22/§24).
"""
from __future__ import annotations

from typing import Any

from ..evidence_authority.positive_relation import direct_static_call_presence
from .engine import run_lvs
from .signatures import build_code_side_from_rows


def design_from_flow_dto(dto: dict) -> dict:
    flow = dto["flow"]
    bindings = {b["block_id"]: b for b in dto.get("bindings", [])}
    blocks = []
    for b in dto.get("blocks", []):
        binding = bindings.get(b["id"])
        blocks.append({
            "id": b["id"],
            "name": b.get("name", ""),
            "kind": b.get("kind", "function"),
            "state": b.get("state", "existing"),
            "binding": binding.get("canonical_symbol_id")
            if binding else None,
            "resources": b.get("resources", []) or [],
        })
    composites = [
        {"id": b["id"], "name": b.get("name", ""),
         "block_ids": [c["id"] for c in dto.get("blocks", [])
                       if c.get("parent_block_id") == b["id"]],
         "data_in": b.get("data_in", []) or [],
         "data_out": b.get("data_out", []) or [],
         "resources": b.get("resources", []) or [],
         "encapsulation": True}
        for b in dto.get("blocks", [])
        if b.get("kind") == "composite"]
    ports = [
        {"id": p["id"], "block_id": p["block_id"], "name": p["name"],
         "direction": p.get("direction", "input"),
         "semantic_kind": p.get("semantic_kind", "data"),
         "code_type": p.get("code_type")}
        for p in dto.get("ports", [])]
    nets = [
        {"id": n["id"],
         "kind": "control" if n.get("kind", "data") == "control" else "data",
         "source_block_id": n.get("source_block_id"),
         "target_block_id": n.get("target_block_id"),
         "source_port_id": n.get("source_port_id"),
         "target_port_id": n.get("target_port_id"),
         "label": n.get("label")}
        for n in dto.get("nets", [])]
    return {
        "snapshot_id": flow.get("snapshot_id"),
        "blocks": blocks,
        "composites": composites,
        "ports": ports,
        "nets": nets,
        "claims": [],
    }


def lvs_flow(store, flow_id: str) -> dict:
    from ..flow.service import FlowService

    dto = FlowService(store).get_flow(flow_id)
    flow = dto["flow"]
    repo = store.repo(flow["repo_id"])
    if not repo:
        return {
            "error": "repo_not_found",
            "message": f"repo {flow['repo_id']} is not registered",
        }
    design = design_from_flow_dto(dto)
    design_sid = flow["snapshot_id"]
    code_sid = store.current_snapshot(flow["repo_id"]) or design_sid

    rows = store.all_nodes(flow["repo_id"], code_sid)
    edges = _edges_for(store, flow["repo_id"], code_sid,
                       source_root=repo["root_path"])
    code = build_code_side_from_rows(rows, edges, repo["root_path"], code_sid)

    # snapshot-aware baseline (§17): the flow's design snapshot rows
    baseline_rows = []
    if design_sid and design_sid != code_sid:
        try:
            baseline_rows = store.all_nodes(flow["repo_id"], design_sid)
            baseline = build_code_side_from_rows(
                baseline_rows, [], repo["root_path"], design_sid)
            code.baseline_id = design_sid
            code.baseline_functions = baseline.functions
        except Exception:
            baseline_rows = []

    # No unresolved candidates is not a bounded absence certificate. Flow
    # control nets do not carry DIRECT_STATIC_CALL scope, so their missing
    # edges remain UNKNOWN rather than manufacturing a negative fact.
    call_cap = "PARTIAL"
    data_cap = "PARTIAL"           # no resolved DATA wires in index lanes
    result = run_lvs(design, {
        "snapshot_id": code.snapshot_id,
        "baseline_id": code.baseline_id,
        "baseline_functions": code.baseline_functions,
        "baseline_edges": [],
        "functions": code.functions,
        "edges": code.edges,
        "resources": code.resources,
        "call_capability": call_cap,
        "data_capability": data_cap,
    })
    out = result.to_dict()
    out["_meta"] = {
        "flow_id": flow_id,
        "design_snapshot": design_sid,
        "code_snapshot": code_sid,
        "call_capability": call_cap,
        "data_capability": data_cap,
        "source_root": repo["root_path"],
    }
    return out


def _edges_for(store, repo_id: str, sid: str, *,
               source_root: str | None = None) -> list[dict]:
    edges = []
    for e in store.all_edges(repo_id, sid):
        kind = (e.get("kind") or "").upper()
        kind = "CALL" if kind == "CALLS" else kind
        if kind not in ("CALL", "DATA", "STATE", "CONTROL", "TIME",
                        "RESOURCE"):
            continue
        support = store.support_receipts(repo_id, sid, e["id"])
        # A graph edge is not itself proof of EXACT/COMPLETE.  The previous
        # adapter fabricated hard-call dimensions for every legacy candidate.
        # Only unanimous sealed support at the strongest lattice point can
        # satisfy the LVS deterministic-call MATCH predicate.
        hard = bool(support) and all(
            row.get("truth_class") in {"OBSERVED", "RESOLVED"}
            and row.get("execution_modality") == "MUST"
            and row.get("target_resolution") == "EXACT"
            and row.get("coverage") == "COMPLETE"
            for row in support)
        static = direct_static_call_presence(
            store, repo_id, sid, e, support, source_root=source_root)
        edges.append({
            "kind": kind,
            "source": e.get("src_id") or e.get("source"),
            "target": e.get("dst_id") or e.get("target"),
            "truth_class": "OBSERVED" if hard else "INFERRED",
            "execution_modality": "MUST" if hard else "MAY",
            "target_resolution": "EXACT" if hard else "UNKNOWN",
            "coverage": "COMPLETE" if hard else "PARTIAL",
            "static_presence": static["state"],
            "static_presence_rule_version": static["rule_version"],
            "static_presence_support_receipt_ids": static[
                "support_receipt_ids"],
            "static_presence_support_dimensions": static.get(
                "support_dimensions", []),
            "static_presence_evidence_authority": static.get(
                "evidence_authority", "UNKNOWN"),
            "static_presence_provider_status": static.get(
                "provider_status", "UNKNOWN"),
            "runtime_state": static["runtime_state"],
            "representative_witnesses": [
                {"support_receipt_id": row["support_receipt_id"]}
                for row in support],
            "source_span": support[0].get("source_span") if support else None,
        })
    return edges
