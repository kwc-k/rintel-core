"""FlowService — store-level orchestration of the software circuit plane.

Owns the service-level invariants that are NOT DB-enforced (SPEC-P2 §19):
- composite hierarchy is a DAG (no ancestor cycles);
- net endpoints: output port -> input port, same semantic_kind;
- exposed ports of a composite are derived from children ports not consumed
  by internal nets (deterministic, no guessing);
- block/port/net CRUD never goes through FastAPI routes directly (§20).
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from ..store import FlowError, Store
from .domain import (BLOCK_KINDS, BLOCK_STATES, NET_KINDS, PORT_DIRECTIONS,
                     SEMANTIC_KINDS)

_DESIGN_LIFECYCLE_WRITE = object()


def _camelize(name: str) -> str:
    return "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", name)
                   if p) or "Block"


def snake_case(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    return re.sub(r"[^A-Za-z0-9_]", "_", s).lower()


class FlowService:
    """Domain operations over one flow model (one Store, no HTTP)."""

    def __init__(self, store: Store, *, _write_capability: object | None = None):
        self.db = store
        self._write_capability = _write_capability

    @classmethod
    def _for_design_lifecycle(cls, store: Store) -> "FlowService":
        """Internal construction path used by lifecycle-owned adapters."""
        return cls(store, _write_capability=_DESIGN_LIFECYCLE_WRITE)

    def _require_design_lifecycle(self) -> None:
        if self._write_capability is not _DESIGN_LIFECYCLE_WRITE:
            raise FlowError(
                "design_lifecycle_required",
                "public design mutations must use DesignLifecycleService",
                {"replacement": "DesignLifecycleService.apply_command",
                 "required_change_context": "change_id"})

    # ------------------------------------------------------------------
    # flow model
    # ------------------------------------------------------------------
    @staticmethod
    def _span_text(repo_root: str, path: str, sym: dict) -> str | None:
        import os
        full = os.path.join(repo_root, path)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines(keepends=True)
        except OSError:
            return None
        start = int(sym.get("start_line", 1) or 1) - 1
        end = int(sym.get("end_line", start + 1) or start + 1)
        if start < 0 or end > len(lines) or start >= end:
            return None
        return "".join(lines[start:end])

    def require_flow(self, flow_id: str) -> dict:
        m = self.db.flow_model(flow_id)
        if not m:
            raise FlowError("flow_not_found", "flow model not found",
                            {"flow_id": flow_id})
        return m

    def get_flow(self, flow_id: str) -> dict:
        """Full netlist DTO (model + blocks + ports + nets + bindings +
        layout) with evidence-side enrichment for bound blocks."""
        flow = self.require_flow(flow_id)
        blocks = self.db.flow_blocks(flow_id)
        ports = self.db.flow_ports(flow_id)
        nets = self.db.flow_nets(flow_id)
        bindings = self.db.flow_bindings(flow_id)
        port_block = {p["id"]: p["block_id"] for p in ports}
        block_port_list: dict[str, list[dict]] = {}
        for p in ports:
            block_port_list.setdefault(p["block_id"], []).append(p)

        repo = self.db.repo(flow["repo_id"])
        symbol_by_block: dict[str, Optional[dict]] = {}
        binding_by_block: dict[str, Optional[dict]] = {}
        for b in blocks:
            binding = next((x for x in bindings
                            if x["block_id"] == b["id"]), None)
            binding_by_block[b["id"]] = binding
            sym = None
            if binding and repo:
                row = None
                try:
                    row = self.db.node_by_id(repo["id"],
                                             binding["snapshot_id"],
                                             binding["canonical_symbol_id"])
                except Exception:  # noqa: BLE001 — enrichment must not break
                    row = None
                if row:
                    sym = {k: row[k] for k in (
                        "id", "kind", "name", "qname", "language", "path",
                        "start_line", "start_col", "end_line", "end_col")
                        if k in row}
            symbol_by_block[b["id"]] = sym

        block_rows = []
        for b in blocks:
            row = dict(b)
            row["binding"] = binding_by_block.get(b["id"])
            sym = symbol_by_block.get(b["id"])
            row["symbol"] = sym
            row["ports"] = block_port_list.get(b["id"], [])
            # §15: existing bound blocks expose their definition span as
            # `code` so the inspector Monaco can edit them (modified state).
            if row.get("code") is None and sym and repo:
                span = self._span_text(repo["root_path"], sym["path"], sym)
                if span:
                    row["code"] = span
            block_rows.append(row)

        net_rows = []
        for n in nets:
            row = dict(n)
            row["source_block_id"] = port_block.get(n["source_port_id"])
            row["target_block_id"] = port_block.get(n["target_port_id"])
            try:
                row["derived"] = bool(
                    json.loads(n.get("meta_json") or "{}").get("derived"))
            except ValueError:
                row["derived"] = False
            net_rows.append(row)

        layout = self.db.flow_layout(flow_id)
        return {
            "flow": flow,
            "blocks": block_rows,
            "ports": ports,
            "nets": net_rows,
            "bindings": bindings,
            "layout": (layout or {}).get("layout", {}),
            "repo": {"id": repo["id"], "root_path": repo["root_path"]}
            if repo else None,
        }

    def create_flow(self, repo_id: str, name: str, snapshot_id: str, *,
                    workspace_id: str | None = None,
                    architecture_model_id: str | None = None,
                    scope_symbol_id: str | None = None) -> dict:
        self._require_design_lifecycle()
        return self.db.flow_create_model(
            repo_id, name, snapshot_id, workspace_id=workspace_id,
            architecture_model_id=architecture_model_id,
            scope_symbol_id=scope_symbol_id)

    def update_flow(self, flow_id: str, *, name: str | None = None,
                    layout: dict | None = None,
                    meta: dict | None = None) -> dict:
        self._require_design_lifecycle()
        flow = self.require_flow(flow_id)
        if name is not None or meta is not None:
            self.db.flow_update_model(flow_id, name=name, meta=meta)
        if layout is not None:
            self.db.flow_put_layout(flow_id, layout)
        return self.get_flow(flow_id)

    def record_agent_action(self, flow_id: str, action: dict) -> dict:
        """TOPO-EDITOR-UX0 §22 — append an agent provenance record to the
        design model's meta (log.agent_actions).  Design-plane only."""
        self._require_design_lifecycle()
        flow = self.require_flow(flow_id)
        meta = json.loads(flow.get("meta_json") or "{}")
        log = meta.get("log") or {}
        actions = log.get("agent_actions") or []
        actions.append(action)
        log["agent_actions"] = actions
        meta["log"] = log
        self.db.flow_update_model(flow_id, meta=meta)
        return {"recorded": action["agent_action_id"],
                "total": len(actions)}

    # ------------------------------------------------------------------
    # blocks
    # ------------------------------------------------------------------
    def add_block(self, flow_id: str, kind: str, name: str,
                  state: str = "proposed", *,
                  parent_block_id: str | None = None,
                  code: str | None = None,
                  meta: dict | None = None) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        if kind not in BLOCK_KINDS:
            raise FlowError("invalid_block_kind", f"unknown block kind {kind!r}",
                            {"kind": kind})
        if state not in BLOCK_STATES:
            raise FlowError("invalid_block_state",
                            f"unknown block state {state!r}", {"state": state})
        if not name.strip():
            raise FlowError("block_name_required", "block name required")
        block = self.db.flow_create_block(
            flow_id, kind=kind, name=name.strip(), state=state,
            parent_block_id=parent_block_id, code=code, meta=meta)
        if kind in ("function", "object") and state == "proposed":
            # proposed function/object: no control ports until real usage —
            # user adds ports explicitly (§11)
            pass
        return block

    def update_block(self, flow_id: str, block_id: str, *,
                     name: str | None = None,
                     code: str | None = None,
                     state: str | None = None,
                     kind: str | None = None,
                     parent_block_id: str | None = None,
                     clear_parent: bool = False,
                     meta: dict | None = None) -> dict:
        self._require_design_lifecycle()
        b = self.db.flow_block(flow_id, block_id)
        if not b:
            raise FlowError("block_not_found", "block not found",
                            {"block_id": block_id})
        if state is not None and state not in BLOCK_STATES:
            raise FlowError("invalid_block_state",
                            f"unknown block state {state!r}", {"state": state})
        if kind is not None and kind not in BLOCK_KINDS:
            raise FlowError("invalid_block_kind", f"unknown block kind {kind!r}",
                            {"kind": kind})
        new_parent = None if clear_parent else (
            parent_block_id if parent_block_id is not None
            else b["parent_block_id"])
        if new_parent is not None:
            self._assert_no_parent_cycle(flow_id, block_id, new_parent)
        return self.db.flow_update_block(
            flow_id, block_id, name=name, kind=kind, code=code, state=state,
            parent_block_id=new_parent if (parent_block_id is not None
                                           or clear_parent) else None,
            clear_parent=clear_parent, meta=meta)

    def _assert_no_parent_cycle(self, flow_id: str, block_id: str,
                                new_parent: str) -> None:
        """Hierarchy is a DAG (§19): an ancestor may not be one's descendant."""
        if block_id == new_parent:
            raise FlowError("parent_cycle",
                            "a block cannot be its own parent",
                            {"block_id": block_id})
        children = {b["id"]: b for b in self.db.flow_blocks(flow_id)}
        seen: set[str] = set()
        stack = [block_id]
        while stack:
            bid = stack.pop()
            if bid in seen:
                continue
            seen.add(bid)
            for ch in children.values():
                if ch.get("parent_block_id") == bid:
                    stack.append(ch["id"])
        if new_parent in seen:
            raise FlowError("parent_cycle",
                            "hierarchy cycle detected",
                            {"block_id": block_id,
                             "parent_block_id": new_parent,
                             "contains": sorted(seen)[:20]})

    def delete_block(self, flow_id: str, block_id: str) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        self.db.flow_delete_block(flow_id, block_id)
        return {"deleted": block_id}

    # ------------------------------------------------------------------
    # ports
    # ------------------------------------------------------------------
    def add_port(self, flow_id: str, *, block_id: str, name: str,
                 direction: str, semantic_kind: str,
                 code_type: str | None = None,
                 position_order: int = 0,
                 meta: dict | None = None) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        if direction not in PORT_DIRECTIONS:
            raise FlowError("invalid_port_direction",
                            f"unknown direction {direction!r}",
                            {"direction": direction})
        if semantic_kind not in SEMANTIC_KINDS:
            raise FlowError("invalid_port_kind",
                            f"unknown semantic kind {semantic_kind!r}",
                            {"semantic_kind": semantic_kind})
        return self.db.flow_create_port(
            flow_id, block_id=block_id, name=name.strip(), direction=direction,
            semantic_kind=semantic_kind, code_type=code_type,
            position_order=position_order, meta=meta)

    def update_port(self, flow_id: str, port_id: str, *,
                    name: str | None = None,
                    semantic_kind: str | None = None,
                    code_type: str | None = None,
                    clear_type: bool = False,
                    position_order: int | None = None,
                    meta: dict | None = None) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        if semantic_kind is not None and semantic_kind not in SEMANTIC_KINDS:
            raise FlowError("invalid_port_kind",
                            f"unknown semantic kind {semantic_kind!r}",
                            {"semantic_kind": semantic_kind})
        p = self.db.flow_port(flow_id, port_id)
        if not p:
            raise FlowError("port_not_found", "port not found",
                            {"port_id": port_id})
        return self.db.flow_update_port(
            flow_id, port_id, name=name, semantic_kind=semantic_kind,
            code_type=code_type, clear_type=clear_type,
            position_order=position_order, meta=meta)

    def delete_port(self, flow_id: str, port_id: str) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        self.db.flow_delete_port(flow_id, port_id)
        return {"deleted": port_id}

    # ------------------------------------------------------------------
    # nets (1 source -> 1 target, §3.4)
    # ------------------------------------------------------------------
    def add_net(self, flow_id: str, *, source_port_id: str,
                target_port_id: str, kind: str = "control",
                label: str | None = None,
                derived: bool = False,
                meta: dict | None = None) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        if kind not in NET_KINDS:
            raise FlowError("invalid_net_kind", f"unknown net kind {kind!r}",
                            {"kind": kind})
        src = self.db.flow_port(flow_id, source_port_id)
        dst = self.db.flow_port(flow_id, target_port_id)
        if not src or not dst:
            raise FlowError("port_not_in_flow", "net endpoint not in flow",
                            {"source_port_id": source_port_id,
                             "target_port_id": target_port_id})
        if source_port_id == target_port_id:
            raise FlowError("self_net", "a port cannot be connected to itself",
                            {"port_id": source_port_id})
        if src["direction"] != "output" or dst["direction"] != "input":
            raise FlowError("net_direction",
                            "net must connect output port -> input port",
                            {"source_direction": src["direction"],
                             "target_direction": dst["direction"]})
        if src["semantic_kind"] != dst["semantic_kind"]:
            raise FlowError("net_kind_mismatch",
                            "net endpoints must share semantic_kind",
                            {"source_kind": src["semantic_kind"],
                             "target_kind": dst["semantic_kind"]})
        nmeta = {"derived": True} if derived else (meta or {})
        return self.db.flow_create_net(
            flow_id, source_port_id=source_port_id,
            target_port_id=target_port_id, kind=kind, label=label,
            meta=nmeta or None)

    def update_net(self, flow_id: str, net_id: str, *,
                   kind: str | None = None,
                   label: str | None = None,
                   meta: dict | None = None,
                   clear_label: bool = False) -> dict:
        """TOPO-EDITOR-UX0 §4: design nets are editable; nets projected
        from evidence (`derived`) are read-only here."""
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        if kind is not None and kind not in NET_KINDS:
            raise FlowError("invalid_net_kind", f"unknown net kind {kind!r}",
                            {"kind": kind})
        n = self.db.flow_net(flow_id, net_id)
        if not n:
            raise FlowError("net_not_found", "net not found",
                            {"net_id": net_id})
        existing = json.loads(n.get("meta_json") or "{}")
        if existing.get("derived"):
            raise FlowError("net_derived_readonly",
                            "evidence-projected nets are read-only",
                            {"net_id": net_id})
        return self.db.flow_update_net(
            flow_id, net_id, kind=kind, label=label, meta=meta,
            clear_label=clear_label)

    def delete_net(self, flow_id: str, net_id: str) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        self.db.flow_delete_net(flow_id, net_id)
        return {"deleted": net_id}

    # ------------------------------------------------------------------
    # composite (§10): manual encapsulation + exposed ports.  External
    # nets are redirected through the composite port (deterministic):
    # the net crossing the boundary is rewired and an internal net
    # continues it inside the composite, so hierarchy navigation and the
    # LVS-like check stay truthful at every level.
    # ------------------------------------------------------------------
    def create_composite(self, flow_id: str, name: str,
                         block_ids: Iterable[str]) -> dict:
        self._require_design_lifecycle()
        self.require_flow(flow_id)
        ids = list(block_ids)
        if not ids:
            raise FlowError("composite_empty",
                            "select at least one block to encapsulate")
        if len(ids) < 2:
            raise FlowError("composite_small",
                            "a composite needs at least 2 child blocks")
        if not name.strip():
            name = "Group"
        name = _camelize(name)
        blocks = {b["id"]: b for b in self.db.flow_blocks(flow_id)}
        for bid in ids:
            b = blocks.get(bid)
            if not b:
                raise FlowError("block_not_in_flow", "block not in flow",
                                {"block_id": bid})
            if b["kind"] == "composite":
                raise FlowError("composite_in_composite",
                                "cannot nest composites in one step",
                                {"block_id": bid})
            if b["parent_block_id"] is not None:
                raise FlowError("block_has_parent",
                                "selected blocks must be at the same level",
                                {"block_id": bid})
        children = set(ids)
        ports = [p for p in self.db.flow_ports(flow_id)
                 if p["block_id"] in children]
        port_by_id = {p["id"]: p for p in ports}
        nets = [n for n in self.db.flow_nets(flow_id)]
        internal = [n for n in nets
                    if n["source_port_id"] in port_by_id
                    and n["target_port_id"] in port_by_id]
        external = [n for n in nets
                    if n not in internal
                    and (n["source_port_id"] in port_by_id
                         or n["target_port_id"] in port_by_id)]
        internal_in = {n["target_port_id"] for n in internal}
        internal_out = {n["source_port_id"] for n in internal}
        ext_in = {n["target_port_id"] for n in external
                  if n["target_port_id"] in port_by_id}
        ext_out = {n["source_port_id"] for n in external
                   if n["source_port_id"] in port_by_id}

        composite = self.db.flow_create_block(
            flow_id, kind="composite", name=name, state="existing")

        # exposed ports: unconsumed internally OR touched by external nets
        exposed_children: dict[str, list[dict]] = {}
        # (name, direction, semantic_kind) -> composite port
        needs: list[dict] = []
        seen: set[tuple] = set()
        pools = (sorted(ports, key=lambda p: (
            p["block_id"], p["position_order"])))
        for p in pools:
            if p["direction"] == "input":
                expose = p["id"] in ext_in or p["id"] not in internal_in
            else:
                expose = p["id"] in ext_out or p["id"] not in internal_out
            if not expose:
                continue
            key = (p["name"], p["direction"], p["semantic_kind"])
            if key not in seen:
                seen.add(key)
                needs.append({"name": p["name"], "direction": p["direction"],
                              "semantic_kind": p["semantic_kind"],
                              "code_type": p.get("code_type"),
                              "position_order": len(needs),
                              "children": []})
            for n in needs:
                if (n["name"], n["direction"], n["semantic_kind"]) == key:
                    n["children"].append(
                        {"child_port_id": p["id"],
                         "child_block_id": p["block_id"]})
                    break
        composite_port_by_key: dict[tuple, str] = {}
        created: list[dict] = []
        for n in needs:
            port = self.add_port(
                flow_id, block_id=composite["id"], name=n["name"],
                direction=n["direction"], semantic_kind=n["semantic_kind"],
                code_type=n["code_type"], position_order=n["position_order"])
            composite_port_by_key[
                (n["name"], n["direction"], n["semantic_kind"])] = port["id"]
            created.append({"port": port, "children": n["children"]})

        # redirect external nets through the composite boundary
        for n in external:
            src = port_by_id.get(n["source_port_id"])
            dst = port_by_id.get(n["target_port_id"])
            if dst is not None:
                cport = composite_port_by_key.get(
                    (dst["name"], "input", dst["semantic_kind"]))
                if cport:
                    self.db.flow_delete_net(flow_id, n["id"])
                    self._create_net_missing(
                        flow_id, n["source_port_id"], cport, n["kind"],
                        n.get("label"), derived=True, redirected=True)
                    self._ensure_internal_net(
                        flow_id, cport, dst["id"], n["kind"], port_by_id)
            elif src is not None:
                cport = composite_port_by_key.get(
                    (src["name"], "output", src["semantic_kind"]))
                if cport:
                    self.db.flow_delete_net(flow_id, n["id"])
                    self._create_net_missing(
                        flow_id, cport, n["target_port_id"], n["kind"],
                        n.get("label"), derived=True, redirected=True)
                    self._ensure_internal_net(
                        flow_id, src["id"], cport, n["kind"], port_by_id)

        meta = {"children": ids,
                "exposed_ports": {
                    c["port"]["id"]: c["children"] for c in created}}
        self.db.flow_update_block(flow_id, composite["id"], meta=meta)
        for bid in ids:
            self.db.flow_update_block(flow_id, bid,
                                      parent_block_id=composite["id"])
        return self.get_flow(flow_id)

    def _create_net_missing(self, flow_id: str, src_port: str,
                            dst_port: str, kind: str, label: str | None,
                            *, derived: bool, redirected: bool) -> None:
        for n in self.db.flow_nets(flow_id):
            if n["source_port_id"] == src_port and \
                    n["target_port_id"] == dst_port:
                return
        meta = {"derived": True} if derived else {}
        if redirected:
            meta["redirected"] = True
        try:
            self.db.flow_create_net(flow_id, source_port_id=src_port,
                                    target_port_id=dst_port, kind=kind,
                                    label=label, meta=meta or None)
        except FlowError:
            pass

    def _ensure_internal_net(self, flow_id: str, src_port: str,
                             dst_port: str, kind: str,
                             port_by_id: dict) -> None:
        """Create the internal net continuing a redirected external net,
        unless an equivalent one already exists."""
        self._create_net_missing(flow_id, src_port, dst_port, kind, None,
                                 derived=True, redirected=True)
