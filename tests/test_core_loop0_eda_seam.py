"""EDA labels are read-only, revision-bound coordinates over existing Flow IDs."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from rintel.db import Database
from rintel.design_lifecycle.models import AcceptanceCriterion, DesignMutation
from rintel.design_lifecycle.service import DesignLifecycleError, DesignLifecycleService
from rintel.flow.eda_address import project_eda
from rintel.flow.service import FlowService
from rintel.indexer import Indexer
from rintel.mcp import server as mcp_server
from rintel.server.api.design_lifecycle import CommandBody, command as rest_command


def sample_netlist():
    flow = {"id": "flow-a", "repo_id": "repo-a"}
    blocks = [
        {"id": "node-a", "name": "Source", "kind": "function", "parent_block_id": None},
        {"id": "node-c", "name": "Package", "kind": "composite", "parent_block_id": None},
        {"id": "node-b", "name": "Solver", "kind": "function", "parent_block_id": "node-c"},
    ]
    ports = [
        {"id": "port-b-in-1", "block_id": "node-b", "name": "A", "direction": "input",
         "position_order": 0, "semantic_kind": "data",
         "meta_json": '{"port_contract":{"generic_type":"MATRIX","dtype":"float64","shape":"[N,N]","semantic_object":"linear_system.A"}}'},
        {"id": "port-b-in-2", "block_id": "node-b", "name": "data", "direction": "input",
         "position_order": 1, "semantic_kind": "data", "meta_json": "{}"},
        {"id": "port-b-out-1", "block_id": "node-b", "name": "x", "direction": "output",
         "position_order": 0, "semantic_kind": "data", "meta_json": "{}"},
        {"id": "port-b-out-2", "block_id": "node-b", "name": "data", "direction": "output",
         "position_order": 1, "semantic_kind": "data", "meta_json": "{}"},
        {"id": "port-a-out", "block_id": "node-a", "name": "data", "direction": "output",
         "position_order": 0, "semantic_kind": "data", "meta_json": "{}"},
    ]
    nets = [{"id": "net-1", "source_port_id": "port-a-out",
             "target_port_id": "port-b-in-1", "kind": "data"}]
    return flow, blocks, ports, nets


def by_id(rows):
    return {row["id"]: row for row in rows}


def test_address_determinism_hierarchy_direction_local_ordinals_and_contract():
    args = sample_netlist()
    a = project_eda(*args, revision="flow-design-v1:r5")
    b = project_eda(args[0], list(reversed(args[1])), list(reversed(args[2])),
                    args[3], revision="flow-design-v1:r5")
    assert a == b  # T1: row iteration order is not an address source
    nodes = by_id(a["nodes"])
    ports = by_id(a["ports"])
    assert nodes["node-b"]["display_address"] == "L2.N1"
    assert ports["port-b-in-1"]["display_address"] == "L2.N1.IN1:A"
    assert ports["port-b-in-2"]["display_address"] == "L2.N1.IN2:data"
    assert ports["port-b-out-1"]["display_address"] == "L2.N1.OUT1:x"
    assert ports["port-b-out-2"]["display_address"] == "L2.N1.OUT2:data"
    assert ports["port-b-in-2"]["id"] != ports["port-b-out-2"]["id"]
    assert ports["port-b-in-2"]["eda_address"]["machine_path"] != ports["port-b-out-2"]["eda_address"]["machine_path"]
    contract = ports["port-b-in-1"]["port_contract"]
    assert (contract["generic_type"], contract["dtype"], contract["shape"]) == (
        "MATRIX", "float64", "[N,N]")
    assert contract["authority"] == "DESIGN_ANNOTATION"
    assert ports["port-b-in-2"]["port_contract"]["shape"] == "UNKNOWN"
    assert "shape" in ports["port-b-in-2"]["port_contract"]["unknown_fields"]
    alignment = ports["port-b-in-1"]["expected_actual"]
    assert alignment["expected"]["shape"] == "[N,N]"
    assert alignment["actual"] == {"status": "UNKNOWN",
                                    "reason": "no_bound_port_evidence"}
    assert alignment["comparison"] == "UNKNOWN"
    assert "MATCH" not in repr(a)
    assert a["nets"][0]["driver_port_id"] == "port-a-out"
    assert a["nets"][0]["sink_port_ids"] == ["port-b-in-1"]


def test_rename_reorder_insert_preserve_stable_ids_not_display_coordinates():
    flow, blocks, ports, nets = sample_netlist()
    before = project_eda(flow, blocks, ports, nets, "r5")
    changed_blocks, changed_ports = deepcopy(blocks), deepcopy(ports)
    changed_blocks[2]["name"] = "RenamedSolver"
    changed_ports[0]["name"] = "renamed_A"
    renamed = project_eda(flow, changed_blocks, changed_ports, nets, "r6")
    assert by_id(renamed["nodes"])["node-b"]["id"] == "node-b"
    assert by_id(renamed["ports"])["port-b-in-1"]["id"] == "port-b-in-1"
    assert by_id(renamed["ports"])["port-b-in-1"]["display_address"].endswith(":renamed_A")
    changed_ports[0]["position_order"] = 3
    reordered = project_eda(flow, changed_blocks, changed_ports, nets, "r7")
    assert by_id(reordered["ports"])["port-b-in-1"]["display_address"].startswith("L2.N1.IN2")
    assert by_id(reordered["ports"])["port-b-in-1"]["id"] == "port-b-in-1"
    moved_blocks = deepcopy(changed_blocks)
    moved_blocks[0]["parent_block_id"] = "node-c"
    moved = project_eda(flow, moved_blocks, changed_ports, nets, "r7b")
    assert by_id(moved["nodes"])["node-a"]["id"] == "node-a"
    assert by_id(moved["nodes"])["node-a"]["display_address"] != by_id(before["nodes"])["node-a"]["display_address"]
    changed_blocks.append({"id": "node-0", "name": "Inserted", "kind": "function",
                           "parent_block_id": "node-c"})
    inserted = project_eda(flow, changed_blocks, changed_ports, nets, "r8")
    assert by_id(inserted["nodes"])["node-b"]["id"] == "node-b"
    assert by_id(inserted["nodes"])["node-b"]["display_address"] == "L2.N2"
    assert before["revision"] != inserted["revision"]


@pytest.fixture
def indexed_store(tmp_path: Path):
    db = Database(tmp_path / "rintel.db")
    repos = []
    for repo_id in ("repo-a", "repo-b"):
        repo = tmp_path / repo_id
        repo.mkdir()
        (repo / "sample.py").write_text("def witness():\n    return 1\n")
        Indexer(db, repo, repo_id=repo_id).index()
        repos.append((repo_id, db.current_snapshot(repo_id)))
    yield db, dict(repos)
    db.close()


def open_change(svc, repo_id, snapshot):
    return svc.open_change(repo_id=repo_id, base_canonical_revision=snapshot,
                           intent="EDA seam witness", scope={},
                           acceptance_criteria=[AcceptanceCriterion("structural", "structural")],
                           actor="human:test")


def mutate(svc, change, operation, payload):
    return svc.apply_command(change.id, DesignMutation(
        actor="human:test", plane="flow", operation=operation, payload=payload,
        expected_version=change.version))


def test_lifecycle_revision_mcp_readback_stale_and_cross_repo_fail_closed(indexed_store, monkeypatch):
    db, snapshots = indexed_store
    svc = DesignLifecycleService(db)
    change = open_change(svc, "repo-a", snapshots["repo-a"])
    change = mutate(svc, change, "create_blank", {"repo_id": "repo-a", "name": "EDA"})
    flow_id = change.design_revision.flow_model_ref.identity
    change = mutate(svc, change, "add_block", {"flow_id": flow_id,
                    "kind": "function", "name": "Solver"})
    block_id = db.flow_blocks(flow_id)[0]["id"]
    change = mutate(svc, change, "add_port", {"flow_id": flow_id, "block_id": block_id,
                    "name": "A", "direction": "input", "semantic_kind": "data",
                    "meta": {"port_contract": {"dtype": "float64", "shape": "[N,N]"}}})
    port_id = db.flow_ports(flow_id)[0]["id"]
    dto = FlowService(db).get_flow(flow_id)
    repeat = FlowService(db).get_flow(flow_id)
    assert dto["eda"] == repeat["eda"]
    assert dto["eda"]["revision"] == change.design_revision.flow_model_ref.revision
    assert dto["ports"][0]["port_contract"]["shape"] == "[N,N]"
    assert dto["blocks"][0]["ports"][0]["id"] == port_id
    monkeypatch.setattr(mcp_server, "_STORE", db)
    mcp = mcp_server.t_get_change_workspace({"change_id": change.id})
    assert mcp["eda_design"]["ports"][0]["id"] == port_id
    assert mcp["eda_design"]["revision"] == dto["eda"]["revision"]
    assert mcp["eda_design"]["ports"][0]["display_address"] == dto["ports"][0]["display_address"]
    assert "get_change_workspace" in mcp_server.TOOLS

    # An older DesignChange remains bound to its old digest; never show latest
    # Flow data under the historical address context.
    historical = svc.open_change(repo_id="repo-a", base_canonical_revision=snapshots["repo-a"],
                                 intent="historical", scope={}, acceptance_criteria=[],
                                 actor="human:test", flow_model_ref=change.design_revision.flow_model_ref)
    old_version = change.version
    change = mutate(svc, change, "update_port", {"flow_id": flow_id,
                    "port_id": port_id, "name": "renamed_A"})
    assert db.flow_port(flow_id, port_id)["name"] == "renamed_A"
    assert change.design_revision.flow_model_ref.revision != dto["eda"]["revision"]
    stale = mcp_server.t_get_change_workspace({"change_id": historical.id})
    assert stale["eda_design"]["status"] == "STALE_CONTEXT"
    assert "ports" not in stale["eda_design"]
    with pytest.raises(DesignLifecycleError) as exc:
        svc.apply_command(change.id, DesignMutation(
            actor="human:test", plane="flow", operation="update_port",
            payload={"flow_id": flow_id, "port_id": port_id, "name": "bad"},
            expected_version=old_version))
    assert exc.value.code == "stale_preflight"

    other = open_change(svc, "repo-b", snapshots["repo-b"])
    with pytest.raises(DesignLifecycleError) as exc:
        mutate(svc, other, "add_port", {"flow_id": flow_id, "block_id": block_id,
                "name": "escape", "direction": "input", "semantic_kind": "data"})
    assert exc.value.code == "design_repo_mismatch"
    with pytest.raises(DesignLifecycleError) as exc:
        mutate(svc, change, "add_net", {"flow_id": flow_id,
               "source_port_id": "L1.N1.OUT1", "target_port_id": "L1.N1.IN1"})
    assert exc.value.code == "port_not_in_flow"


def test_mcp_exact_node_read_exposes_multiple_ports_and_keeps_actual_unknown(
        indexed_store, monkeypatch):
    db, snapshots = indexed_store
    svc = DesignLifecycleService(db)
    change = open_change(svc, "repo-a", snapshots["repo-a"])
    change = mutate(svc, change, "create_blank", {"repo_id": "repo-a", "name": "Multiport"})
    flow_id = change.design_revision.flow_model_ref.identity
    change = mutate(svc, change, "add_block", {
        "flow_id": flow_id, "kind": "function", "name": "Solver"})
    block_id = db.flow_blocks(flow_id)[0]["id"]
    for name, direction, code_type, contract in (
        ("A", "input", "double[20][20]", {"generic_type": "MATRIX", "dtype": "float64", "shape": "[20,20]"}),
        ("rhs", "input", None, {}),
        ("x", "output", "double[20]", {"generic_type": "VECTOR", "dtype": "float64", "shape": "[20]"}),
        ("status", "output", None, {}),
    ):
        change = mutate(svc, change, "add_port", {
            "flow_id": flow_id, "block_id": block_id, "name": name,
            "direction": direction, "semantic_kind": "data", "code_type": code_type,
            "meta": {"port_contract": contract}})

    monkeypatch.setattr(mcp_server, "_STORE", db)
    result = mcp_server.t_get_change_workspace({"change_id": change.id, "node_id": block_id})
    node = result["eda_node"]
    assert result["design_revision"] == change.design_revision.id
    assert node["id"] == block_id
    assert [p["name"] for p in node["inputs"]] == ["A", "rhs"]
    assert [p["name"] for p in node["outputs"]] == ["x", "status"]
    assert node["inputs"][0]["code_type"] == "double[20][20]"
    assert node["inputs"][0]["port_contract"]["dtype"] == "float64"
    assert node["inputs"][0]["port_contract"]["authority"] == "DESIGN_ANNOTATION"
    assert node["inputs"][0]["expected_actual"]["actual"]["status"] == "UNKNOWN"
    assert node["inputs"][1]["code_type"] is None
    assert node["inputs"][1]["port_contract"]["dtype"] == "UNKNOWN"
    assert all(p["expected_actual"]["comparison"] == "UNKNOWN"
               for p in node["inputs"] + node["outputs"])
    assert "eda_design" not in result  # exact node read is bounded

    display_address = node["display_address"]
    bad = mcp_server.t_get_change_workspace({"change_id": change.id,
                                             "node_id": display_address})
    assert bad["error"]["code"] == "NODE_NOT_FOUND"


def test_implicit_port_order_persists_across_rename_and_append(indexed_store):
    db, snapshots = indexed_store
    svc = DesignLifecycleService(db)
    change = open_change(svc, "repo-a", snapshots["repo-a"])
    change = mutate(svc, change, "create_blank", {"repo_id": "repo-a", "name": "Ordered"})
    flow_id = change.design_revision.flow_model_ref.identity
    change = mutate(svc, change, "add_block", {"flow_id": flow_id, "name": "Solver"})
    block_id = db.flow_blocks(flow_id)[0]["id"]
    ids = {}
    for name, direction in (("A", "input"), ("rhs", "input"),
                            ("x", "output"), ("status", "output")):
        change = mutate(svc, change, "add_port", {
            "flow_id": flow_id, "block_id": block_id, "name": name,
            "direction": direction, "semantic_kind": "data"})
        ids[name] = next(p["id"] for p in db.flow_ports(flow_id) if p["name"] == name)

    def ordered():
        ports = FlowService(db).get_flow(flow_id)["eda"]["ports"]
        return ([(p["id"], p["display_address"]) for p in ports
                 if p["owner_node_id"] == block_id and p["eda_address"]["display"]["direction"] == "IN"],
                [(p["id"], p["display_address"]) for p in ports
                 if p["owner_node_id"] == block_id and p["eda_address"]["display"]["direction"] == "OUT"])

    inputs, outputs = ordered()
    assert [id_ for id_, _ in inputs] == [ids["A"], ids["rhs"]]
    assert [id_ for id_, _ in outputs] == [ids["x"], ids["status"]]
    assert [p["position_order"] for p in db.flow_ports(flow_id)
            if p["direction"] == "input"] == [0, 1]
    assert [p["position_order"] for p in db.flow_ports(flow_id)
            if p["direction"] == "output"] == [0, 1]

    change = mutate(svc, change, "update_port", {
        "flow_id": flow_id, "port_id": ids["A"], "name": "matrix"})
    inputs, outputs_after = ordered()
    assert [id_ for id_, _ in inputs] == [ids["A"], ids["rhs"]]
    assert inputs[0][1].endswith("IN1:matrix")
    assert outputs_after == outputs

    change = mutate(svc, change, "add_port", {
        "flow_id": flow_id, "block_id": block_id, "name": "tolerance",
        "direction": "input", "semantic_kind": "data"})
    inputs, outputs_after = ordered()
    assert [id_ for id_, _ in inputs[:2]] == [ids["A"], ids["rhs"]]
    assert inputs[2][1].endswith("IN3:tolerance")
    assert outputs_after == outputs


def test_mcp_flow_write_requires_stable_identity_and_exact_design_revision(
        indexed_store, monkeypatch):
    db, snapshots = indexed_store
    svc = DesignLifecycleService(db)
    change = open_change(svc, "repo-a", snapshots["repo-a"])
    change = mutate(svc, change, "create_blank", {"repo_id": "repo-a", "name": "EDA"})
    flow_id = change.design_revision.flow_model_ref.identity
    change = mutate(svc, change, "add_block", {"flow_id": flow_id, "name": "Solver"})
    block_id = db.flow_blocks(flow_id)[0]["id"]
    change = mutate(svc, change, "add_port", {
        "flow_id": flow_id, "block_id": block_id, "name": "A",
        "direction": "input", "semantic_kind": "data"})
    port_id = db.flow_ports(flow_id)[0]["id"]
    monkeypatch.setattr(mcp_server, "_STORE", db)
    request = {
        "change_id": change.id,
        "expected_design_revision": change.design_revision.id,
        "stable_id": port_id,
        "operation": "update_port",
        "ops": [{"flow_id": flow_id, "port_id": port_id, "name": "B"}],
    }
    assert "mutate_flow_design" in mcp_server.TOOLS
    missing = dict(request)
    del missing["expected_design_revision"]
    error = mcp_server.mcp_call("mutate_flow_design", missing)
    assert error["error"]["code"] == "MISSING_REQUIRED_ARGUMENT"
    assert db.flow_port(flow_id, port_id)["name"] == "A"

    with pytest.raises(DesignLifecycleError) as exc:
        mcp_server.t_mutate_flow_design({**request, "stable_id": "L1.N1.IN1:A"})
    assert exc.value.code == "stable_id_mismatch"
    with pytest.raises(DesignLifecycleError) as exc:
        mcp_server.t_mutate_flow_design({**request,
            "ops": [{"flow_id": flow_id, "port_id": "L1.N1.IN1:A", "name": "B"}],
            "stable_id": "L1.N1.IN1:A"})
    assert exc.value.code == "port_not_found"
    assert db.flow_port(flow_id, port_id)["name"] == "A"

    other = open_change(svc, "repo-a", snapshots["repo-a"])
    other = mutate(svc, other, "create_blank", {
        "repo_id": "repo-a", "name": "Other Flow"})
    with pytest.raises(DesignLifecycleError) as exc:
        mcp_server.t_mutate_flow_design({
            **request, "ops": [{"flow_id": other.design_revision.flow_model_ref.identity,
                                 "port_id": port_id, "name": "B"}]})
    assert exc.value.code == "flow_binding_mismatch"

    result = mcp_server.mcp_call("mutate_flow_design", request)
    assert result["design_revision"] != change.design_revision.id
    assert result["stable_id"] == port_id
    assert db.flow_port(flow_id, port_id)["name"] == "B"
    activity = FlowService(db).get_flow(flow_id)["design_activity"]
    assert activity["status"] == "RECORDED"
    assert activity["operation"] == "update_port"
    assert activity["actor"] == "agent:rintel-mcp"
    assert activity["design_revision"] == result["design_revision"]
    with pytest.raises(DesignLifecycleError) as exc:
        mcp_server.t_mutate_flow_design(request)
    assert exc.value.code == "stale_design_revision"
    assert db.flow_port(flow_id, port_id)["name"] == "B"
    FlowService._for_design_lifecycle(db).update_port(flow_id, port_id, name="out_of_band")
    with pytest.raises(DesignLifecycleError) as exc:
        mcp_server.t_mutate_flow_design({
            **request, "expected_design_revision": result["design_revision"]})
    assert exc.value.code == "stale_flow_model"
    assert db.flow_port(flow_id, port_id)["name"] == "out_of_band"


def test_mcp_design_patch_apply_is_revision_guarded(indexed_store, monkeypatch):
    db, snapshots = indexed_store
    svc = DesignLifecycleService(db)
    change = open_change(svc, "repo-a", snapshots["repo-a"])
    monkeypatch.setattr(mcp_server, "_STORE", db)
    request = {"design_id": change.id, "mode": "apply",
               "ops": [{"op": "add_node", "name": "proposed"}]}
    with pytest.raises(DesignLifecycleError) as exc:
        mcp_server.t_design_patch(request)
    assert exc.value.code == "expected_design_revision_required"
    assert svc.get_change(change.id).version == change.version
    result = mcp_server.t_design_patch({
        **request, "expected_design_revision": change.design_revision.id})
    assert result["design_revision"] != change.design_revision.id
    with pytest.raises(DesignLifecycleError) as exc:
        mcp_server.t_design_patch({
            **request, "expected_design_revision": change.design_revision.id})
    assert exc.value.code == "stale_design_revision"


def test_rest_design_mutation_compatibility_is_explicitly_deprecated(indexed_store):
    db, snapshots = indexed_store
    svc = DesignLifecycleService(db)
    change = open_change(svc, "repo-a", snapshots["repo-a"])
    out = rest_command(change.id, CommandBody(
        command="design_mutation", actor="human:compat", plane="flow",
        operation="create_blank", payload={"repo_id": "repo-a", "name": "Legacy"}),
        db)
    assert out["write_surface"]["status"] == "DEPRECATED_COMPATIBILITY"
    assert "expected_design_revision" in out["write_surface"]["required_context"]
    assert svc.get_change(change.id).design_revision.flow_model_ref is not None
