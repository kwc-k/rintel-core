"""FlowValidationService — minimal software-LVS (SPEC-P2 §16).

Compares Flow Existing function/object blocks against the CURRENT evidence
snapshot of the repo:

- binding still exists          -> else UNBOUND
- expected CALLS still exists   -> else STALE
- new unexpected CALLS          -> else MISMATCH
- otherwise                     -> MATCH

Only ``control`` nets are CALLS projections; ``data``/``event``/``error``/
``resource`` nets are design intent and are excluded from the comparison
(§8 correctness boundary — never claim data flow from a call relation).
"""

from __future__ import annotations

from ..store import FlowError, Store
from .domain import compare_flow


class FlowValidationService:
    def __init__(self, store: Store):
        self.db = store

    def validate(self, flow_id: str) -> dict:
        flow = self.db.flow_model(flow_id)
        if not flow:
            raise FlowError("flow_not_found", "flow model not found",
                            {"flow_id": flow_id})
        repo = self.db.repo(flow["repo_id"])
        if not repo:
            raise FlowError("repo_not_found", "repo not found",
                            {"repo_id": flow["repo_id"]})
        current_sid = self.db.current_snapshot(repo["id"])
        if not current_sid:
            raise FlowError("repo_not_indexed", "repo has no snapshot",
                            {"repo_id": repo["id"]})

        blocks = {b["id"]: b for b in self.db.flow_blocks(flow_id)}
        bindings = {b["block_id"]: b for b in self.db.flow_bindings(flow_id)}
        ports = self.db.flow_ports(flow_id)
        nets = self.db.flow_nets(flow_id)
        port_block = {p["id"]: p["block_id"] for p in ports}

        # composite unrolling maps (internal nets crossing a composite
        # boundary): composite input port -> child ports it feeds; child
        # output ports -> composite output port they consolidate into.
        port_children: dict[str, list[str]] = {}
        port_parents: dict[str, list[str]] = {}
        for n in nets:
            src_b = port_block.get(n["source_port_id"])
            dst_b = port_block.get(n["target_port_id"])
            if src_b and blocks.get(src_b, {}).get("kind") == "composite":
                port_children.setdefault(n["source_port_id"], []).append(
                    n["target_port_id"])
            if dst_b and blocks.get(dst_b, {}).get("kind") == "composite":
                port_parents.setdefault(n["target_port_id"], []).append(
                    n["source_port_id"])

        bound: set[str] = set()
        for b in blocks.values():
            if b["kind"] not in ("function", "object"):
                continue
            if b["state"] not in ("existing", "modified"):
                continue
            bind = bindings.get(b["id"])
            if bind:
                bound.add(bind["canonical_symbol_id"])

        # canonical ids present in the current snapshot (bindings)
        node_exists: set[str] = set()
        for bid in bound:
            if self.db.node_by_id(repo["id"], current_sid, bid):
                node_exists.add(bid)

        # CALLS edges of the current snapshot restricted to flow symbols
        call_pairs: set[tuple[str, str]] = set()
        try:
            edges = self.db.all_edges(repo["id"], current_sid)
        except Exception:  # noqa: BLE001
            edges = []
        for e in edges:
            if e.get("kind") != "CALLS":
                continue
            src, dst = e.get("src_id"), e.get("dst_id")
            if src in bound and dst in bound:
                call_pairs.add((src, dst))

        result = compare_flow(blocks, bindings, port_block, nets,
                              call_pairs, node_exists,
                              port_children=port_children,
                              port_parents=port_parents)
        design_nets = [n for n in nets if n.get("kind") != "control"]
        result.update({
            "flow_id": flow_id,
            "current_snapshot_id": current_sid,
            "pinned_snapshot_id": flow["snapshot_id"],
            "design_nets": len(design_nets),
            "evidence_call_pairs": len(call_pairs),
        })
        return result
