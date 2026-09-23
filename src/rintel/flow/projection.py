"""FlowProjectionService — Evidence -> Flow projection (SPEC-P2 §5/§16/§17).

Truth boundary (frozen): an initial Flow is an *evidence projection*.  It is
derived from real CALLS edges (depth 1) + real signatures; nothing about
business semantics, layout or data-flow is guessed (§5 note; §8: nets are
``kind=control`` only, never ``foo.result -> bar.input``).
"""
from __future__ import annotations

import os
from typing import Optional

from ..store import FlowError, Store
from .domain import (PROJECTABLE_SYMBOL_KINDS, SYMBOL_TO_BLOCK_KIND,
                     block_ports, control_ports)
from .service import FlowService
from .signature import Signature, extract_signature

_MAX_SPAN_BYTES = 128 * 1024


def _node_span_text(repo_root: str, path: str, node: dict) -> str:
    """Source text of a definition span (conservative; empty on any issue)."""
    full = os.path.join(repo_root, path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except OSError:
        return ""
    start = int(node.get("start_line", 1) or 1) - 1
    end = int(node.get("end_line", start + 1) or start + 1)
    lines = data.splitlines(keepends=True)
    if start >= len(lines):
        return ""
    return "".join(lines[start:end])


class FlowProjectionService:
    """Builds initial SoftwareNetlists from evidence symbols/components."""

    def __init__(self, store: Store, *, _lifecycle_write: bool = False):
        self.db = store
        self._lifecycle_write = _lifecycle_write

    @classmethod
    def _for_design_lifecycle(cls, store: Store) -> "FlowProjectionService":
        return cls(store, _lifecycle_write=True)

    def _require_design_lifecycle(self) -> None:
        if not self._lifecycle_write:
            raise FlowError(
                "design_lifecycle_required",
                "public design mutations must use DesignLifecycleService",
                {"replacement": "DesignLifecycleService.apply_command",
                 "required_change_context": "change_id"})

    # ------------------------------------------------------------------
    def from_symbol(self, repo_id: str, snapshot_id: str,
                    symbol: str, name: str | None = None,
                    workspace_id: str | None = None,
                    architecture_model_id: str | None = None,
                    include_callees: bool = True) -> dict:
        """`symbol` is a canonical node id (`node:KIND:qname`) or a bare qname.
        Blocks: the symbol + its direct CALLS callees (depth 1, §5).
        """
        self._require_design_lifecycle()
        root = self._resolve_node(repo_id, snapshot_id, symbol)
        if root["kind"] not in PROJECTABLE_SYMBOL_KINDS:
            raise FlowError(
                "symbol_not_projectable",
                f"symbol kind {root['kind']} cannot open as a flow",
                {"kind": root["kind"]})
        nodes: dict[str, dict] = {root["id"]: root}
        if include_callees:
            for e in self.db.edges_for_node(repo_id, snapshot_id, root["id"],
                                            direction="out",
                                            relation="CALLS"):
                dst = self.db.node_by_id(repo_id, snapshot_id, e["dst_id"])
                if dst and dst["kind"] in PROJECTABLE_SYMBOL_KINDS:
                    nodes.setdefault(dst["id"], dst)
        flow_name = name or f"{root['name']} Flow"
        return self._build_flow(
            repo_id, snapshot_id, nodes, root["id"], flow_name,
            workspace_id=workspace_id,
            architecture_model_id=architecture_model_id,
            scope_symbol_id=root["id"])

    def from_component(self, workspace_id: str, model_id: str,
                       component_id: str,
                       name: str | None = None) -> dict:
        """Component-scoped flow (§17): mapped functions (+CALLS depth 1)."""
        self._require_design_lifecycle()
        ws = self.db.arch_workspace(workspace_id)
        if not ws:
            raise FlowError("workspace_not_found", "workspace not found",
                            {"workspace_id": workspace_id})
        comp = self.db.arch_component(workspace_id, component_id, model_id)
        if not comp:
            raise FlowError("component_not_found", "component not found",
                            {"component_id": component_id})
        sid = self.db.current_snapshot(ws["repo_id"])
        if not sid:
            raise FlowError("repo_not_indexed", "repo has no snapshot",
                            {"repo_id": ws["repo_id"]})
        nodes: dict[str, dict] = {}
        notes: list[str] = []
        for m in self.db.arch_mappings_for_component(workspace_id,
                                                     component_id):
            if m["evidence_entity_type"] != "node":
                continue
            row = self.db.node_by_id(ws["repo_id"], sid,
                                     m["evidence_entity_id"])
            if row and row["kind"] in PROJECTABLE_SYMBOL_KINDS:
                nodes.setdefault(row["id"], row)
            elif row:
                notes.append(f"mapped symbol {row['name']} (kind "
                             f"{row['kind']}) not projectable")
        if not nodes:
            raise FlowError("component_no_mapped_functions",
                            "component maps no projectable functions")
        # CALLS depth 1 neighbourhood (§17: draw mapped + direct callees)
        extra: dict[str, dict] = {}
        for nid, node in list(nodes.items()):
            for e in self.db.edges_for_node(ws["repo_id"], sid, nid,
                                            direction="out",
                                            relation="CALLS"):
                dst = self.db.node_by_id(ws["repo_id"], sid, e["dst_id"])
                if dst and dst["kind"] in PROJECTABLE_SYMBOL_KINDS:
                    extra.setdefault(dst["id"], dst)
        nodes.update(extra)
        flow_name = name or f"{comp['name']} Flow"
        return self._build_flow(
            ws["repo_id"], sid, nodes, None, flow_name,
            workspace_id=workspace_id,
            architecture_model_id=model_id,
            scope_symbol_id=comp["id"], notes=notes)

    # ------------------------------------------------------------------
    def expand(self, flow_id: str) -> dict:
        """Expand neighbours: add depth-1 CALLS callees of existing blocks
        that are not yet blocks (SPEC-P2 §17 "Expand neighbors")."""
        self._require_design_lifecycle()
        svc = FlowService._for_design_lifecycle(self.db)
        dto = svc.get_flow(flow_id)
        flow = dto["flow"]
        repo = self.db.repo(flow["repo_id"])
        if not repo:
            raise FlowError("repo_not_found", "repo not found",
                            {"repo_id": flow["repo_id"]})
        bindings = {b["block_id"]: b for b in dto["bindings"]}
        have = {b["symbol"]["id"] for b in dto["blocks"]
                if b.get("symbol")}
        new_blocks: dict[str, dict] = {}
        added_nets = 0
        for b in dto["blocks"]:
            binding = bindings.get(b["id"])
            if not binding:
                continue
            for e in self.db.edges_for_node(flow["repo_id"],
                                            binding["snapshot_id"],
                                            binding["canonical_symbol_id"],
                                            direction="out",
                                            relation="CALLS"):
                if e["dst_id"] in have or e["dst_id"] in new_blocks:
                    continue
                dst = self.db.node_by_id(flow["repo_id"],
                                         binding["snapshot_id"], e["dst_id"])
                if not dst or dst["kind"] not in PROJECTABLE_SYMBOL_KINDS:
                    continue
                new_blocks[dst["id"]] = dst
        new_ids = list(new_blocks)
        if new_ids:
            self._add_nodes_to_flow(flow, dto, new_blocks, notes=[])
            dto = svc.get_flow(flow_id)
            added_nets = self._link_control_nets(flow, dto)
        return svc.get_flow(flow_id)

    # ------------------------------------------------------------------
    # build helpers
    # ------------------------------------------------------------------
    def _resolve_node(self, repo_id: str, snapshot_id: str,
                      symbol: str) -> dict:
        row = None
        if symbol.startswith("node:"):
            row = self.db.node_by_id(repo_id, snapshot_id, symbol)
        if row is None:
            row = self.db.node_by_qname(repo_id, snapshot_id, symbol)
        if not row:
            raise FlowError("symbol_not_found", "symbol not found",
                            {"symbol": symbol})
        return row

    def _build_flow(self, repo_id: str, snapshot_id: str,
                    nodes: dict[str, dict], root_id: str | None,
                    flow_name: str, *, workspace_id: str | None,
                    architecture_model_id: str | None,
                    scope_symbol_id: str | None,
                    notes: Optional[list[str]] = None) -> dict:
        repo = self.db.repo(repo_id)
        if not repo:
            raise FlowError("repo_not_found", "repo not found",
                            {"repo_id": repo_id})
        svc = FlowService._for_design_lifecycle(self.db)
        flow = svc.create_flow(
            repo_id, flow_name, snapshot_id, workspace_id=workspace_id,
            architecture_model_id=architecture_model_id,
            scope_symbol_id=scope_symbol_id)
        dto = self._add_nodes_to_flow(flow, self._empty_dto(flow), nodes,
                                      notes=notes or [])
        nets = self._link_control_nets(flow, dto)
        if root_id:
            root_block = next((b for b in dto["blocks"]
                               if b.get("symbol")
                               and b["symbol"]["id"] == root_id), None)
            if root_block:
                self.db.flow_update_model(
                    flow["id"], root_block_id=root_block["id"],
                    meta={"root_block_id": root_block["id"]})
        result = svc.get_flow(flow["id"])
        result["notes"] = notes or []
        return result

    def _empty_dto(self, flow: dict) -> dict:
        return {"flow": flow, "blocks": [], "ports": [], "nets": [],
                "bindings": []}

    def _add_nodes_to_flow(self, flow: dict, dto: dict,
                           nodes: dict[str, dict], *,
                           notes: list[str]) -> dict:
        svc = FlowService._for_design_lifecycle(self.db)
        repo = self.db.repo(flow["repo_id"])
        assert repo is not None
        block_by_symbol: dict[str, str] = {}
        ordered = sorted(nodes.values(), key=lambda n: (n["path"], n["qname"]))
        for node in ordered:
            kind = SYMBOL_TO_BLOCK_KIND.get(node["kind"], "function")
            block = svc.add_block(flow["id"], kind=kind, name=node["name"],
                                  state="existing")
            self.db.flow_put_binding(
                flow["id"], block_id=block["id"],
                snapshot_id=flow["snapshot_id"],
                canonical_symbol_id=node["id"])
            block_by_symbol[node["id"]] = block["id"]
            sig: Signature = Signature()
            if repo:
                text = _node_span_text(repo["root_path"], node["path"], node)
                if text:
                    sig = extract_signature(node["language"], text,
                                            node["kind"])
                else:
                    notes.append(
                        f"{node['name']}: source span unavailable — ports "
                        f"unknown")
            for p in block_ports(sig.params, sig.return_info):
                order = 0
                if p["semantic_kind"] == "control":
                    order = 300 if p["direction"] == "output" else 100
                self.db.flow_create_port(
                    flow["id"], block_id=block["id"], name=p["name"],
                    direction=p["direction"], semantic_kind=p["semantic_kind"],
                    code_type=p.get("code_type"), position_order=order)
        dto["blocks"] = self.db.flow_blocks(flow["id"])
        dto["ports"] = self.db.flow_ports(flow["id"])
        dto["bindings"] = self.db.flow_bindings(flow["id"])
        return dto

    def _link_control_nets(self, flow: dict, dto: dict) -> int:
        """Control nets caller-block -> callee-block for every real CALLS
        edge whose both endpoints are blocks (SV-derived connectivity, §8)."""
        svc = FlowService._for_design_lifecycle(self.db)
        repo = self.db.repo(flow["repo_id"])
        assert repo is not None
        binding_by_block = {b["block_id"]: b for b in dto["bindings"]}
        symbol_block = {}
        for b in dto["blocks"]:
            binding = binding_by_block.get(b["id"])
            if binding:
                symbol_block[binding["canonical_symbol_id"]] = b
        ports_by_block: dict[str, dict[str, str]] = {}
        for p in dto["ports"]:
            ports_by_block.setdefault(p["block_id"], {})[p["name"]] = p["id"]
        count = 0
        for b in dto["blocks"]:
            binding = binding_by_block.get(b["id"])
            if not binding:
                continue
            for e in self.db.edges_for_node(repo["id"],
                                            binding["snapshot_id"],
                                            binding["canonical_symbol_id"],
                                            direction="out",
                                            relation="CALLS"):
                dst_block = symbol_block.get(e["dst_id"])
                if not dst_block:
                    continue
                src_ports = ports_by_block.get(b["id"], {})
                dst_ports = ports_by_block.get(dst_block["id"], {})
                out_id = src_ports.get("control_out")
                in_id = dst_ports.get("control_in")
                if not out_id or not in_id:
                    continue
                try:
                    svc.add_net(flow["id"], source_port_id=out_id,
                                target_port_id=in_id, kind="control",
                                derived=True)
                    count += 1
                except FlowError:
                    continue  # duplicate net (same callee called twice)
        return count
