"""SYNTHESIS0 product adapter: AS-IS/TO-BE from the stored flow (spec §23/§24).

AS-IS = the implemented subset of the flow (state != proposed); TO-BE =
the edited flow including proposed objects.  The same design views drive
plan/preview/apply; apply writes through the synthesis apply path and
reindexes (canonical truth only after reindex)."""
from __future__ import annotations

import copy
from typing import Any

from ..lvs.flow_runner import design_from_flow_dto


def flow_views(dto: dict) -> tuple[dict, dict]:
    to_be = design_from_flow_dto(dto)
    as_is = copy.deepcopy(to_be)
    keep = {b["id"] for b in as_is["blocks"]
            if b.get("state") != "proposed"}
    as_is["blocks"] = [b for b in as_is["blocks"]
                       if b["id"] in keep]
    as_is["ports"] = [p for p in as_is["ports"]
                      if p.get("block_id") in keep]
    as_is["nets"] = [n for n in as_is["nets"]
                     if n.get("source_block_id") in keep
                     and n.get("target_block_id") in keep]
    return as_is, to_be


def code_side_from_store(store, repo_id: str, sid: str,
                         root: str):
    from ..lvs.signatures import build_code_side_from_rows

    rows = store.all_nodes(repo_id, sid)
    edges = []
    for e in store.all_edges(repo_id, sid):
        kind = (e.get("kind") or "").upper()
        kind = "CALL" if kind == "CALLS" else kind
        if kind not in ("CALL", "DATA", "STATE", "CONTROL", "TIME",
                        "RESOURCE"):
            continue
        edges.append({"kind": kind,
                      "source": e.get("src_id") or e.get("source"),
                      "target": e.get("dst_id") or e.get("target"),
                      "truth_class": "OBSERVED",
                      "execution_modality": "MUST",
                      "target_resolution": "EXACT",
                      "coverage": "COMPLETE"})
    side = build_code_side_from_rows(rows, edges, root, sid)
    side.call_capability = ("COMPLETE" if
                            store.unresolved_count(repo_id, sid) == 0
                            else "PARTIAL")
    side.data_capability = "PARTIAL"
    return side


def synthesize_flow(store, flow_id: str, mode: str) -> dict[str, Any]:
    from ..flow.service import FlowService
    from .run import (code_side_to_dict, patch_diff_text,
                      synthesize_apply, synthesize_patches, synthesize_plan)

    dto = FlowService(store).get_flow(flow_id)
    flow = dto["flow"]
    repo = store.repo(flow["repo_id"])
    if not repo:
        return {"error": "repo_not_found"}
    as_is, to_be = flow_views(dto)
    sid = store.current_snapshot(flow["repo_id"]) or flow["snapshot_id"]
    code = code_side_from_store(store, flow["repo_id"], sid,
                                repo["root_path"])
    plan = synthesize_plan(as_is, to_be, code,
                           design_snapshot=flow.get("snapshot_id"),
                           code_snapshot=sid,
                           environment={"name": "default-lane-env",
                                        "resources": {}})
    out: dict[str, Any] = {"mode": mode, "flow_id": flow_id,
                           "plan": plan.to_dict()}
    if mode == "plan":
        return out
    gen = synthesize_patches(plan, code, repo["root_path"], to_be)
    out["preview"] = gen.preview
    out["patch_diff"] = patch_diff_text(gen.patches, repo["root_path"])
    if mode == "preview":
        return out

    def reindex():
        from ..indexer import Indexer
        res = Indexer(store, repo["root_path"],
                      repo_id=flow["repo_id"]).index()
        return res.snapshot_id

    result = synthesize_apply(plan, gen.patches, to_be, repo["root_path"],
                              reindex,
                              lambda new_sid: code_side_from_store(
                                  store, flow["repo_id"], new_sid,
                                  repo["root_path"]))
    out["result"] = result.to_dict()
    return out
