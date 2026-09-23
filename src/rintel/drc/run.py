"""DRC run helpers: build DrcContext from fixture inputs or frozen lane
artifacts and execute the full rule set deterministically."""
from __future__ import annotations

import json
from pathlib import Path

from .engine import DrcContext, run_engine
from .ports import PortIndex, Port, ports_from_dataflows
from .rules import RULES

TOURNAMENT = Path(__file__).resolve().parents[2] / "analysis_tournament"


def context_from_input(lane: str, inp: dict, *, dataflows: list[dict] | None = None,
                       facts_nodes: set[str] | None = None) -> DrcContext:
    topo = inp.get("topology", {})
    ports = PortIndex()
    for p in inp.get("ports", []) or []:
        ports.add(p["cid"], Port(
            name=p["name"],
            direction=p.get("direction", "input"),
            type_=p.get("type_"),
            evidence=p.get("evidence", "DECLARED"),
        ))
    if dataflows:
        df = ports_from_dataflows(facts_nodes or set(), dataflows)
        for cid, dirs in df.by_fn.items():
            for direction, named in dirs.items():
                for name, port in named.items():
                    if ports.resolve(cid, name) is None:
                        ports.add(cid, port)
    ctx = DrcContext(
        lane=lane,
        topology=topo,
        data_capability=inp.get("data_capability", "UNKNOWN"),
        suggestions=inp.get("suggestions", []) or [],
        ports=ports,
        environment=inp.get("environment") or {"name": "default",
                                               "resources": {}},
        design_claims=inp.get("design_claims", []) or [],
        scoped_cycle_rules=inp.get("scoped_cycle_rules", []) or [],
        emit_pass=bool(inp.get("emit_pass")),
    )
    return ctx


def run_drc(lane: str, inp: dict, *, dataflows: list[dict] | None = None,
            facts_nodes: set[str] | None = None):
    return run_engine(context_from_input(lane, inp, dataflows=dataflows,
                                         facts_nodes=facts_nodes), RULES)


def lane_environment(lane: dict) -> dict:
    """Environment profile default: the lane-observed resource set is
    provided.  Deployment-specific profiles may narrow it (R001 binds to
    the profile name - D7)."""
    resources = {}
    for r in sorted(set(lane.get("meta", {}).get("resources", []) or [])):
        resources[r] = {"provided": True, "exclusive": False}
    return {"name": "default-lane-env", "resources": resources}


def lane_drc_input(lane_id: str) -> tuple[dict, list[dict] | None, set[str] | None]:
    from ..topology_view.projector import build_lane_bundle

    b = build_lane_bundle(lane_id)
    topo = {
        "nodes": b["nodes"],
        "edges": b["edges"],
    }
    suggestions = []
    for i, s in enumerate(b.get("suggestions", [])):
        suggestions.append({
            "candidate_id": s.get("candidate_id")
            or f"{lane_id}-suggestion-{i}",
            "members": s.get("members", []),
            "boundary": s.get("boundary"),
        })
    dataflows = None
    facts_nodes = None
    # frozen joern dataflow facts: derive INFERRED input-port evidence
    # (D3 gate keeps them out of hard proofs)
    facts_path = TOURNAMENT / "results" / {
        "jpl": "joern_jpl.json",
        "fac_c": "joern_fac_c.json",
        "fixture_c": "joern_fixture_c.json",
    }.get(lane_id, "")
    if facts_path.exists():
        facts = json.loads(facts_path.read_text())
        dataflows = facts
        facts_nodes = {n["canonical_symbol_id"]
                       for n in b["nodes"]}
    return {
        "lane": lane_id,
        "topology": topo,
        "data_capability": b["meta"]["data_capability"],
        "suggestions": suggestions,
        "environment": lane_environment(b),
        "design_claims": [],
        "scoped_cycle_rules": [],
        "emit_pass": False,
    }, dataflows, facts_nodes
