"""Revision-bound, read-only EDA addresses over the existing Flow netlist.

Ordinals are display coordinates, never mutation identities.  The stored
block/port/net IDs and the DesignLifecycle flow digest remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


ADDRESS_VERSION = "eda-address/v0"
PORT_CONTRACT_VERSION = "port-contract/v0"
_GENERIC_TYPES = frozenset({
    "SCALAR", "VECTOR", "MATRIX", "TENSOR", "ARRAY", "STRUCT", "TABLE",
    "GRAPH", "IMAGE", "STRING", "BYTES", "FILE", "STREAM", "HANDLE",
    "OPAQUE", "UNKNOWN",
})
_CONTRACT_FIELDS = ("port_family", "semantic_object", "generic_type",
                    "dtype", "rank", "shape")


def flow_design_digest(store: Any, flow_id: str) -> str:
    """The same stored-TO-BE digest used by DesignRevision.flow_model_ref.

    Do not include live source-code enrichment from FlowService.get_flow().
    """
    payload = {
        "flow": store.flow_model(flow_id),
        "blocks": store.flow_blocks(flow_id),
        "ports": store.flow_ports(flow_id),
        "nets": store.flow_nets(flow_id),
        "bindings": store.flow_bindings(flow_id),
        "layout": store.flow_layout(flow_id),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "flow-design-v1:" + hashlib.sha256(raw.encode()).hexdigest()


def _meta(row: dict) -> dict:
    try:
        value = json.loads(row.get("meta_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _contract(port: dict, ordinal: int) -> dict:
    declaration = _meta(port).get("port_contract") or {}
    if not isinstance(declaration, dict):
        declaration = {}
    fields: dict[str, Any] = {}
    unknown: list[str] = []
    for key in _CONTRACT_FIELDS:
        value = declaration.get(key)
        valid = (isinstance(value, str) and bool(value) and value != "UNKNOWN")
        if key == "generic_type":
            valid = valid and value in _GENERIC_TYPES
        elif key == "rank":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        elif key == "shape":
            valid = valid or (isinstance(value, list) and bool(value) and
                              all(isinstance(dim, (str, int)) and dim != ""
                                  for dim in value))
        if not valid:
            fields[key] = "UNKNOWN"
            unknown.append(key)
        else:
            fields[key] = value
    return {
        "version": PORT_CONTRACT_VERSION,
        "authority": "DESIGN_ANNOTATION",
        "port_id": port["id"],
        "owner_node_id": port["block_id"],
        "direction": "IN" if port["direction"] == "input" else "OUT",
        "ordinal": ordinal,
        "name": port["name"],
        "semantic_kind": port.get("semantic_kind") or "UNKNOWN",
        **fields,
        "unknown_fields": unknown,
    }


def project_eda(flow: dict, blocks: list[dict], ports: list[dict],
                nets: list[dict], revision: str) -> dict:
    """Project one immutable read context; layout/screen order is irrelevant.

    The model root is L1.  Each composite's child scope is a distinct layer;
    layers and nodes without formal order use stable-ID ordering.  Ports use
    the stored position_order, breaking ties by stable ID.
    """
    model_id = flow["id"]
    by_id = {b["id"]: b for b in blocks}
    parent_ids = {b.get("parent_block_id") for b in blocks}
    if any(parent is not None and parent not in by_id for parent in parent_ids):
        raise ValueError("flow hierarchy contains a missing parent")

    def lineage(block_id: str) -> tuple[str, ...]:
        parents: list[str] = []
        seen = {block_id}
        parent = by_id[block_id].get("parent_block_id")
        while parent is not None:
            if parent in seen:
                raise ValueError("flow hierarchy contains a cycle")
            seen.add(parent)
            parents.append(parent)
            parent = by_id[parent].get("parent_block_id")
        return tuple(reversed(parents))

    layer_ids = {None, *[b["id"] for b in blocks if b["kind"] == "composite"]}
    layer_order = sorted(layer_ids, key=lambda parent: (() if parent is None else
                        lineage(parent) + (parent,)))
    layer_ordinal = {parent: i for i, parent in enumerate(layer_order, 1)}
    layer_stable_id = lambda parent: parent if parent is not None else model_id

    def address(layer: str, path: str, *, node: str | None = None,
                port: str | None = None, net: str | None = None,
                display: dict | None = None) -> dict:
        segments = [f"design:{model_id}@{revision}", f"layer:{layer}"]
        for kind, value in (("node", node), ("port", port), ("net", net)):
            if value is not None:
                segments.append(f"{kind}:{value}")
        return {"version": ADDRESS_VERSION, "plane": "flow_design",
                "model_id": model_id, "revision": revision,
                "layer_id": layer, "node_id": node, "port_id": port,
                "net_id": net, "machine_path": "/".join(segments),
                "display": {"path": path, **(display or {})}}

    layers = [{"id": layer_stable_id(parent), "ordinal": layer_ordinal[parent],
               "parent_node_id": parent, "display_address": f"L{layer_ordinal[parent]}"}
              for parent in layer_order]
    nodes: list[dict] = []
    node_address: dict[str, dict] = {}
    for parent in layer_order:
        children = sorted((b for b in blocks if b.get("parent_block_id") == parent),
                          key=lambda b: b["id"])
        for ordinal, block in enumerate(children, 1):
            layer_no = layer_ordinal[parent]
            path = f"L{layer_no}.N{ordinal}"
            addr = address(layer_stable_id(parent), path, node=block["id"],
                           display={"layer_ordinal": layer_no,
                                    "node_ordinal": ordinal})
            node_address[block["id"]] = addr
            nodes.append({"id": block["id"], "name": block["name"],
                          "role": block["kind"], "layer_id": layer_stable_id(parent),
                          "ordinal": ordinal, "display_address": path,
                          "eda_address": addr})

    port_views: list[dict] = []
    port_address: dict[str, dict] = {}
    by_owner_direction: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for port in ports:
        by_owner_direction[(port["block_id"], port["direction"])].append(port)
    for (owner, direction), siblings in sorted(by_owner_direction.items()):
        if owner not in node_address:
            raise ValueError("flow port has no owner node")
        for ordinal, port in enumerate(sorted(
                siblings, key=lambda p: (p.get("position_order", 0), p["id"])), 1):
            node_addr = node_address[owner]
            label = "IN" if direction == "input" else "OUT"
            path = f"{node_addr['display']['path']}.{label}{ordinal}:{port['name']}"
            addr = address(node_addr["layer_id"], path, node=owner,
                           port=port["id"], display={
                               "layer_ordinal": node_addr["display"]["layer_ordinal"],
                               "node_ordinal": node_addr["display"]["node_ordinal"],
                               "direction": label, "port_ordinal": ordinal})
            port_address[port["id"]] = addr
            expected = _contract(port, ordinal)
            port_views.append({"id": port["id"], "owner_node_id": owner,
                               "display_address": path, "eda_address": addr,
                               "code_type": port.get("code_type"),
                               "code_type_authority": "DESIGN_ANNOTATION",
                               "port_contract": expected,
                               "expected_actual": {
                                   "version": "port-expected-actual/v0",
                                   "expected": expected,
                                   "actual": {"status": "UNKNOWN",
                                              "reason": "no_bound_port_evidence"},
                                   "comparison": "UNKNOWN",
                               }})

    net_views: list[dict] = []
    nets_by_layer: dict[str, list[dict]] = defaultdict(list)
    for net in nets:
        driver = port_address.get(net["source_port_id"])
        if driver is None or net["target_port_id"] not in port_address:
            raise ValueError("flow net endpoint is not a port")
        nets_by_layer[driver["layer_id"]].append(net)
    for layer in layers:
        for ordinal, net in enumerate(sorted(nets_by_layer[layer["id"]],
                                             key=lambda n: n["id"]), 1):
            path = f"L{layer['ordinal']}.NET{ordinal}"
            addr = address(layer["id"], path, net=net["id"], display={
                "layer_ordinal": layer["ordinal"], "net_ordinal": ordinal})
            net_views.append({"id": net["id"], "display_address": path,
                              "eda_address": addr,
                              "driver_port_id": net["source_port_id"],
                              "sink_port_ids": [net["target_port_id"]],
                              "semantic_kind": net.get("kind") or "UNKNOWN"})
    return {"address_contract_version": ADDRESS_VERSION,
            "port_contract_version": PORT_CONTRACT_VERSION,
            "plane": "flow_design", "model_id": model_id,
            "revision": revision, "layers": layers, "nodes": nodes,
            "ports": port_views, "nets": net_views,
            "ordinal_identity_warning": "Display ordinals are revision-scoped labels, not write IDs"}
