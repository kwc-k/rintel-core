"""Isolated real-store fixture for the browser/harness EDA loop witness."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from rintel.db import Database
from rintel.design_lifecycle.models import AcceptanceCriterion, DesignMutation
from rintel.design_lifecycle.service import DesignLifecycleService
from rintel.indexer import Indexer
from rintel.mcp import server as mcp


def setup(root: Path) -> dict:
    repo = root / "source-repo"
    repo.mkdir(parents=True)
    (repo / "model.py").write_text("def source():\n    return 1\n", encoding="utf-8")
    db = Database(root / "rintel.db")
    try:
        Indexer(db, repo, repo_id="eda-browser-repo").index()
        svc = DesignLifecycleService(db)
        change = svc.open_change(
            repo_id="eda-browser-repo",
            base_canonical_revision=db.current_snapshot("eda-browser-repo"),
            intent="Browser EDA witness", scope={},
            acceptance_criteria=[AcceptanceCriterion("structure", "structural")],
            actor="human:fixture")

        def mutate(current, op, payload):
            return svc.apply_command(current.id, DesignMutation(
                actor="human:fixture", plane="flow", operation=op, payload=payload,
                expected_version=current.version,
                expected_design_revision=current.design_revision.id))

        change = mutate(change, "create_blank", {
            "repo_id": "eda-browser-repo", "name": "Live EDA"})
        flow_id = change.design_revision.flow_model_ref.identity
        change = mutate(change, "add_block", {
            "flow_id": flow_id, "kind": "proposed", "state": "proposed",
            "name": "Source"})
        source_id = db.flow_blocks(flow_id)[0]["id"]
        change = mutate(change, "add_port", {
            "flow_id": flow_id, "block_id": source_id, "name": "signal",
            "direction": "output", "semantic_kind": "data",
            "meta": {"port_contract": {"generic_type": "MATRIX", "shape": "[20,20]"}}})
        source_port_id = db.flow_ports(flow_id)[0]["id"]
        return {"change_id": change.id, "flow_id": flow_id,
                "source_id": source_id, "source_port_id": source_port_id,
                "revision": change.design_revision.id}
    finally:
        db.close()


def mutate_from_mcp(root: Path, state: dict) -> dict:
    db = Database(root / "rintel.db")
    mcp._STORE = db
    try:
        def call(operation: str, stable_id: str, payload: dict) -> dict:
            nonlocal state
            out = mcp.t_mutate_flow_design({
                "change_id": state["change_id"],
                "expected_design_revision": state["revision"],
                "stable_id": stable_id, "operation": operation,
                "ops": [payload]})
            state = {**state, "revision": out["design_revision"]}
            return out["mutation_result"]

        flow_id = state["flow_id"]
        sink = call("add_block", flow_id, {
            "flow_id": flow_id, "kind": "proposed", "state": "proposed",
            "name": "Sink"})
        port = call("add_port", sink["id"], {
            "flow_id": flow_id, "block_id": sink["id"], "name": "input_signal",
            "direction": "input", "semantic_kind": "data"})
        net = call("add_net", state["source_port_id"], {
            "flow_id": flow_id, "source_port_id": state["source_port_id"],
            "target_port_id": port["id"], "kind": "data"})
        return {**state, "sink_id": sink["id"], "sink_port_id": port["id"],
                "net_id": net["id"]}
    finally:
        mcp._STORE = None
        db.close()


def expand_multiports(root: Path, state: dict) -> dict:
    """Add real second data and control IN/OUT pairs through the same MCP seam."""
    db = Database(root / "rintel.db")
    mcp._STORE = db
    try:
        def call(operation: str, stable_id: str, payload: dict) -> dict:
            nonlocal state
            out = mcp.t_mutate_flow_design({
                "change_id": state["change_id"],
                "expected_design_revision": state["revision"],
                "stable_id": stable_id, "operation": operation,
                "ops": [payload]})
            state = {**state, "revision": out["design_revision"]}
            return out["mutation_result"]

        flow_id = state["flow_id"]
        matrix_out = call("add_port", state["source_id"], {
            "flow_id": flow_id, "block_id": state["source_id"],
            "name": "matrix", "direction": "output", "semantic_kind": "data",
            "code_type": "double[20][20]",
            "meta": {"port_contract": {"generic_type": "MATRIX",
                                       "dtype": "float64", "shape": "[20,20]"}}})
        matrix_in = call("add_port", state["sink_id"], {
            "flow_id": flow_id, "block_id": state["sink_id"],
            "name": "matrix_in", "direction": "input", "semantic_kind": "data",
            "meta": {"port_contract": {"generic_type": "MATRIX",
                                       "dtype": "float64", "shape": "[20,20]"}}})
        matrix_net = call("add_net", matrix_out["id"], {
            "flow_id": flow_id, "source_port_id": matrix_out["id"],
            "target_port_id": matrix_in["id"], "kind": "data"})
        gate_out = call("add_port", state["source_id"], {
            "flow_id": flow_id, "block_id": state["source_id"],
            "name": "gate", "direction": "output", "semantic_kind": "control"})
        gate_in = call("add_port", state["sink_id"], {
            "flow_id": flow_id, "block_id": state["sink_id"],
            "name": "enable", "direction": "input", "semantic_kind": "control"})
        gate_net = call("add_net", gate_out["id"], {
            "flow_id": flow_id, "source_port_id": gate_out["id"],
            "target_port_id": gate_in["id"], "kind": "control"})
        node_read = mcp.mcp_call("get_change_workspace", {
            "change_id": state["change_id"], "node_id": state["source_id"]})
        return {**state, "matrix_out_id": matrix_out["id"],
                "matrix_in_id": matrix_in["id"], "matrix_net_id": matrix_net["id"],
                "gate_out_id": gate_out["id"], "gate_in_id": gate_in["id"],
                "gate_net_id": gate_net["id"],
                "mcp_source_outputs": len(node_read["eda_node"]["outputs"]),
                "mcp_source_actual": node_read["eda_node"]["outputs"][0]
                    ["expected_actual"]["actual"]["status"]}
    finally:
        mcp._STORE = None
        db.close()


if __name__ == "__main__":
    root = Path(sys.argv[2]).resolve()
    result = (setup(root) if sys.argv[1] == "setup"
              else mutate_from_mcp(root, json.loads(sys.argv[3]))
              if sys.argv[1] == "mutate"
              else expand_multiports(root, json.loads(sys.argv[3])))
    print(json.dumps(result, sort_keys=True))
