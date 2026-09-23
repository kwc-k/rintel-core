"""Deterministic DRC fixtures (spec §13).

Each scenario carries positive / negative / uncertain variants.  The
fixtures JSON is consumed by tests/test_drc_rules.py and serves as the
frozen DRC behavior contract (analysis_tournament/drc/fixtures.json).
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_PATH = Path(__file__).resolve().parents[2] / "analysis_tournament" / "drc" / "fixtures.json"


def node(cid: str, name: str = "", file: str = "", resources: list[str] | None = None) -> dict:
    return {
        "canonical_symbol_id": cid,
        "name": name or cid.split(":")[-1],
        "file": file,
        "resources": resources or [],
    }


def edge(kind: str, source: str, target: str, **kw) -> dict:
    base = {
        "kind": kind,
        "source": source,
        "target": target,
        "truth_layer": "T0",
        "truth_class": kw.pop("truth_class", "OBSERVED"),
        "execution_modality": kw.pop("execution_modality", "MUST"),
        "target_resolution": kw.pop("target_resolution", "EXACT"),
        "coverage": kw.pop("coverage", "COMPLETE"),
        "witness_count": 1,
        "representative_witnesses": kw.pop("representative_witnesses", False) and [
            {"fact_id": f"fixture:{kind.lower()}:{source}->{target}",
             "provider": "fixture", "kind": kind,
             "source": {"file": "fixture.py", "line": 1}, "expr": "fixture"}
        ] or [],
        "guard": kw.pop("guard", None),
        "data": kw.pop("data", None),
        "source_span": kw.pop("source_span", {"file": "fixture.py", "line": 1}),
    }
    base.update(kw)
    return base


def data_edge(token: str, src: str, tgt: str, *, truth: str = "OBSERVED",
              coverage: str = "COMPLETE", flow: str | None = None,
              access: str | None = None, modality: str = "MUST",
              resolution: str = "EXACT") -> dict:
    d: dict = {"token": token}
    if flow:
        d["flow"] = flow
    if access:
        d["access"] = access
    return edge("DATA", src, tgt, truth_class=truth, coverage=coverage,
                execution_modality=modality, target_resolution=resolution,
                data=d, representative_witnesses=True)


def state_edge(token: str, src: str, tgt: str, *, access: str = "write",
               truth: str = "OBSERVED") -> dict:
    return edge("STATE", src, tgt, data={"token": token, "access": access},
                truth_class=truth, representative_witnesses=True)


def time_edge(src: str, tgt: str, modality: str = "MUST") -> dict:
    return edge("TIME", src, tgt, execution_modality=modality,
                representative_witnesses=True)


def ports_spec(items: list[dict]) -> list[dict]:
    return items


def as_fixture(fid: str, desc: str, *, topology: dict, data_capability: str = "COMPLETE",
               ports: list[dict] | None = None, suggestions: list[dict] | None = None,
               environment: dict | None = None, design_claims: list[dict] | None = None,
               scoped_cycle_rules: list[dict] | None = None,
               expect: list[dict]) -> dict:
    return {
        "id": fid,
        "description": desc,
        "input": {
            "topology": topology,
            "data_capability": data_capability,
            "ports": ports or [],
            "suggestions": suggestions or [],
            "environment": environment or {"name": "default", "resources": {}},
            "design_claims": design_claims or [],
            "scoped_cycle_rules": scoped_cycle_rules or [],
        },
        "expect": expect,
    }


# ---------------------------------------------------------------------------
# P001
# ---------------------------------------------------------------------------
P001_POS = as_fixture(
    "p001-missing-input", "Required input without any producer -> ERROR",
    topology={"nodes": [node("f:main", file="app.py"), node("f:producer", file="app.py")],
              "edges": []},
    data_capability="COMPLETE",
    ports=ports_spec([{"cid": "f:main", "name": "config", "direction": "input",
                       "type_": "str", "evidence": "DECLARED"}]),
    expect=[{"rule_id": "DRC-P001", "status": "VIOLATION",
             "subject_contains": ["f:main"]}])

P001_NEG = as_fixture(
    "p001-valid-input", "Requirement satisfied by a producer -> no finding",
    topology={"nodes": [node("f:main", file="app.py"), node("f:producer", file="app.py")],
              "edges": [data_edge("config", "f:producer", "f:main")]},
    data_capability="COMPLETE",
    ports=ports_spec([{"cid": "f:main", "name": "config", "direction": "input",
                       "type_": "str", "evidence": "DECLARED"}]),
    expect=[])

P001_UNC = as_fixture(
    "p001-partial-capability", "Deterministic requirement but DATA capability "
                               "PARTIAL -> UNKNOWN, never ERROR",
    topology={"nodes": [node("f:main", file="app.py")], "edges": []},
    data_capability="PARTIAL",
    ports=ports_spec([{"cid": "f:main", "name": "config", "direction": "input",
                       "type_": "str", "evidence": "DECLARED"}]),
    expect=[{"rule_id": "DRC-P001", "status": "UNKNOWN", "severity": "UNKNOWN"}])

P001_INFERRED = as_fixture(
    "p001-inferred-evidence", "Inferred port evidence cannot satisfy the hard "
                              "proof (U003) -> UNKNOWN",
    topology={"nodes": [node("f:main", file="app.py")], "edges": []},
    data_capability="COMPLETE",
    ports=ports_spec([{"cid": "f:main", "name": "config", "direction": "input",
                       "type_": "str", "evidence": "INFERRED"}]),
    expect=[{"rule_id": "DRC-P001", "status": "UNKNOWN", "severity": "UNKNOWN"}])

# ---------------------------------------------------------------------------
# P002 / P003 / P004
# ---------------------------------------------------------------------------
P002_NEG = as_fixture(
    "p002-consumed-output", "Produced output with a consumer -> no finding",
    topology={"nodes": [node("f:gen", file="a.py"), node("f:use", file="b.py")],
              "edges": [data_edge("result", "f:gen", "f:use")]},
    ports=ports_spec([{"cid": "f:gen", "name": "result", "direction": "output",
                       "type_": "str", "evidence": "DECLARED"}]),
    expect=[])

P002_POS = as_fixture(
    "p002-dangling-output", "Known output with no consumer -> INFO/WARNING",
    topology={"nodes": [node("f:gen", file="a.py")], "edges": []},
    ports=ports_spec([{"cid": "f:gen", "name": "result", "direction": "output",
                       "type_": "str", "evidence": "DECLARED"}]),
    expect=[{"rule_id": "DRC-P002", "status": "WARNING", "severity": "INFO"}])

P003_POS = as_fixture(
    "p003-type-mismatch", "Known incompatible provider types -> ERROR",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": [data_edge("x", "f:a", "f:b")]},
    ports=ports_spec([
        {"cid": "f:a", "name": "x", "direction": "output", "type_": "str",
         "evidence": "DECLARED"},
        {"cid": "f:b", "name": "x", "direction": "input", "type_": "list",
         "evidence": "DECLARED"}]),
    expect=[{"rule_id": "DRC-P003", "status": "VIOLATION", "severity": "ERROR"}])

P003_NEG = as_fixture(
    "p003-matching-types", "Same semantic type both ends -> no finding",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": [data_edge("x", "f:a", "f:b")]},
    ports=ports_spec([
        {"cid": "f:a", "name": "x", "direction": "output", "type_": "str",
         "evidence": "DECLARED"},
        {"cid": "f:b", "name": "x", "direction": "input", "type_": "str",
         "evidence": "DECLARED"}]),
    expect=[])

P003_UNC = as_fixture(
    "p003-unknown-type", "Unknown type on either side -> UNKNOWN, never a "
                         "fabricated mismatch (D4)",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": [data_edge("x", "f:a", "f:b")]},
    ports=ports_spec([
        {"cid": "f:a", "name": "x", "direction": "output", "type_": None,
         "evidence": "DECLARED"},
        {"cid": "f:b", "name": "x", "direction": "input", "type_": "str",
         "evidence": "DECLARED"}]),
    expect=[{"rule_id": "DRC-P003", "status": "UNKNOWN", "severity": "UNKNOWN"}])

P004_POS = as_fixture(
    "p004-direction-violation", "intent(in) port used as producer -> ERROR",
    topology={"nodes": [node("f:mod", file="m.f90"), node("f:cal", file="m.f90")],
              "edges": [data_edge("config", "f:mod", "f:cal")]},
    ports=ports_spec([
        {"cid": "f:mod", "name": "config", "direction": "input", "type_": "real",
         "evidence": "DECLARED"},
        {"cid": "f:cal", "name": "config", "direction": "input", "type_": "real",
         "evidence": "DECLARED"}]),
    expect=[{"rule_id": "DRC-P004", "status": "VIOLATION", "severity": "ERROR"}])

# ---------------------------------------------------------------------------
# D001
# ---------------------------------------------------------------------------
D001_POS = as_fixture(
    "d001-multiple-producers", "Two producers on one token without merge "
                               "proof -> AMBIGUOUS_MULTI_PRODUCER",
    topology={"nodes": [node("f:p1", file="a.py"), node("f:p2", file="a.py"),
                        node("f:use", file="b.py")],
              "edges": [data_edge("state", "f:p1", "f:use"),
                        data_edge("state", "f:p2", "f:use")]},
    expect=[{"rule_id": "DRC-D001", "status": "WARNING"}])

D001_MERGE = as_fixture(
    "d001-legal-merge", "Proven merge/phi construct -> LEGAL, no finding",
    topology={"nodes": [node("f:p1", file="a.py"), node("f:p2", file="a.py"),
                        node("f:use", file="b.py")],
              "edges": [data_edge("state", "f:p1", "f:use", flow="merge"),
                        data_edge("state", "f:p2", "f:use", flow="merge")]},
    expect=[])

D001_UNC = as_fixture(
    "d001-partial", "No resolved DATA edges under PARTIAL capability -> UNKNOWN",
    topology={"nodes": [node("f:p1", file="a.py"), node("f:use", file="b.py")],
              "edges": []},
    data_capability="PARTIAL",
    expect=[{"rule_id": "DRC-D001", "status": "UNKNOWN"}])

# ---------------------------------------------------------------------------
# S001 / S002 / S003
# ---------------------------------------------------------------------------
S001_POS = as_fixture(
    "s001-conflict", "Unordered writers on the same mutable state -> "
                     "POTENTIAL_STATE_CONFLICT (not proven race)",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py"),
                        node("st:session", file="store.py")],
              "edges": [state_edge("session", "f:a", "st:session"),
                        state_edge("session", "f:b", "st:session")]},
    expect=[{"rule_id": "DRC-S001", "status": "WARNING", "severity": "WARNING"}])

S001_NEG = as_fixture(
    "s001-ordered", "Writers with MUST_PRECEDE ordering -> no finding",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py"),
                        node("st:session", file="store.py")],
              "edges": [state_edge("session", "f:a", "st:session"),
                        state_edge("session", "f:b", "st:session"),
                        time_edge("f:a", "f:b")]},
    expect=[])

S002_POS = as_fixture(
    "s002-hazard", "Read depends on write without proven ordering -> "
                   "POTENTIAL_HAZARD",
    topology={"nodes": [node("f:w", file="a.py"), node("f:r", file="b.py"),
                        node("st:cache", file="store.py")],
              "edges": [state_edge("cache", "f:w", "st:cache", access="write"),
                        state_edge("cache", "f:r", "st:cache", access="read")]},
    expect=[{"rule_id": "DRC-S002", "status": "WARNING"}])

S002_NEG = as_fixture(
    "s002-proven-safe", "MUST_PRECEDE between writer and reader -> PROVEN_SAFE",
    topology={"nodes": [node("f:w", file="a.py"), node("f:r", file="b.py"),
                        node("st:cache", file="store.py")],
              "edges": [state_edge("cache", "f:w", "st:cache", access="write"),
                        state_edge("cache", "f:r", "st:cache", access="read"),
                        time_edge("f:w", "f:r")]},
    expect=[])

S003_POS = as_fixture(
    "s003-hidden-state", "Suggested boundary hides external mutable state "
                         "not in state_inout -> WARNING",
    topology={"nodes": [node("f:m1", file="m.py"), node("f:m2", file="m.py"),
                        node("st:session", file="store.py")],
              "edges": [state_edge("session", "f:m1", "st:session")]},
    suggestions=[{"candidate_id": "c1", "members": ["f:m1", "f:m2"],
                  "boundary": {"data_in": [], "data_out": [],
                               "state_inout": [], "resource_ports": [],
                               "calls_in": [], "calls_out": []}}],
    expect=[{"rule_id": "DRC-S003", "status": "WARNING", "subject_contains": ["c1"]}])

# ---------------------------------------------------------------------------
# R001 / R002 / R003
# ---------------------------------------------------------------------------
R001_POS = as_fixture(
    "r001-missing-gpu", "Function requires GPU but profile lacks it -> WARNING",
    topology={"nodes": [node("f:solve", file="solver.py", resources=["GPU"])],
              "edges": [edge("RESOURCE", "f:solve", "GPU",
                             representative_witnesses=True)]},
    environment={"name": "cpu-only", "resources": {"Network": {"provided": True}}},
    expect=[{"rule_id": "DRC-R001", "status": "WARNING",
             "related_contains": ["env:cpu-only", "GPU"]}])

R001_NEG = as_fixture(
    "r001-gpu-provided", "Profile provides GPU -> no finding",
    topology={"nodes": [node("f:solve", file="solver.py", resources=["GPU"])],
              "edges": [edge("RESOURCE", "f:solve", "GPU")]},
    environment={"name": "gpu", "resources": {"GPU": {"provided": True,
                                                      "exclusive": False}}},
    expect=[])

R002_POS = as_fixture(
    "r002-hidden-resource", "Suggested module requires Network but boundary "
                            "exposes no resource port",
    topology={"nodes": [node("f:n", file="n.py", resources=["Network"])],
              "edges": [edge("RESOURCE", "f:n", "Network")]},
    suggestions=[{"candidate_id": "c1", "members": ["f:n"],
                  "boundary": {"data_in": [], "data_out": [],
                               "state_inout": [], "resource_ports": [],
                               "calls_in": [], "calls_out": []}}],
    expect=[{"rule_id": "DRC-R002", "status": "WARNING", "subject_contains": ["c1"]}])

R002_NEG = as_fixture(
    "r002-exposed-resource", "Boundary exposes the resource -> no finding",
    topology={"nodes": [node("f:n", file="n.py", resources=["Network"])],
              "edges": [edge("RESOURCE", "f:n", "Network")]},
    suggestions=[{"candidate_id": "c1", "members": ["f:n"],
                  "boundary": {"data_in": [], "data_out": [],
                               "state_inout": [],
                               "resource_ports": [{"resource": "Network",
                                                   "users": ["f:n"]}],
                               "calls_in": [], "calls_out": []}}],
    expect=[])

R003_POS = as_fixture(
    "r003-exclusive", "Two users of an exclusive resource without ordering "
                      "-> potential overlap (exclusivity known)",
    topology={"nodes": [node("f:a", file="a.py", resources=["GPU"]),
                        node("f:b", file="b.py", resources=["GPU"])],
              "edges": [edge("RESOURCE", "f:a", "GPU"),
                        edge("RESOURCE", "f:b", "GPU")]},
    environment={"name": "gpu-host", "resources": {"GPU": {"provided": True,
                                                           "exclusive": True}}},
    expect=[{"rule_id": "DRC-R003", "status": "WARNING"}])

R003_NEG = as_fixture(
    "r003-nonexclusive", "Resource not marked exclusive -> no finding",
    topology={"nodes": [node("f:a", file="a.py", resources=["GPU"]),
                        node("f:b", file="b.py", resources=["GPU"])],
              "edges": [edge("RESOURCE", "f:a", "GPU"),
                        edge("RESOURCE", "f:b", "GPU")]},
    environment={"name": "gpu-host", "resources": {"GPU": {"provided": True,
                                                           "exclusive": False}}},
    expect=[])

# ---------------------------------------------------------------------------
# U001 / U002 / C001 / C002 / C003
# ---------------------------------------------------------------------------
U001_POS = as_fixture(
    "u001-unknown-dynamic", "Unresolved call target -> INFO (honest noise "
                            "floor; never ERROR, D9 refinement)",
    topology={"nodes": [node("f:runner", file="work.py")],
              "edges": [edge("CALL", "f:runner", "[Unknown Dynamic Target]",
                             target_resolution="UNKNOWN", coverage="UNKNOWN",
                             representative_witnesses=True)]},
    expect=[{"rule_id": "DRC-U001", "status": "WARNING", "severity": "INFO",
             "subject_contains": ["f:runner"]}])

U002_POS = as_fixture(
    "u002-candidate-set", "CANDIDATE_SET + PARTIAL -> open set, UNKNOWN, "
                          "no target closure",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": [edge("CALL", "f:a", "f:b", target_resolution="CANDIDATE_SET",
                             coverage="PARTIAL", candidate_targets=["f:b", "f:c"])]},
    expect=[{"rule_id": "DRC-U002", "status": "UNKNOWN"}])

D002_POS = as_fixture(
    "d002-partial-coverage", "Frozen lane with DATA=PARTIAL must answer "
                             "UNKNOWN, not PASS",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": []},
    data_capability="PARTIAL",
    expect=[{"rule_id": "DRC-D002", "status": "UNKNOWN"}])

C001_POS = as_fixture(
    "c001-impossible-must", "Design asserts MUST_PRECEDE; topology only MAY",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": [time_edge("f:a", "f:b", modality="MAY")]},
    design_claims=[{"id": "d1", "kind": "MUST_PRECEDE", "source": "f:a",
                    "target": "f:b", "coverage": "COMPLETE"}],
    expect=[{"rule_id": "DRC-C001", "status": "WARNING"}])

C001_NEG = as_fixture(
    "c001-verified-claim", "MUST_PRECEDE claim backed by MUST evidence",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": [time_edge("f:a", "f:b", modality="MUST")]},
    design_claims=[{"id": "d1", "kind": "MUST_PRECEDE", "source": "f:a",
                    "target": "f:b", "coverage": "COMPLETE"}],
    expect=[])

C002_LEGAL = as_fixture(
    "c002-legal-recursion", "Plain CALL recursion is NOT a violation (D5)",
    topology={"nodes": [node("f:rec", file="r.py")],
              "edges": [edge("CALL", "f:rec", "f:rec")]},
    scoped_cycle_rules=[],
    expect=[])

C002_POS = as_fixture(
    "c002-illegal-scoped-cycle", "Cycle inside the acyclic-init scoped rule",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py"),
                        node("f:rec", file="r.py")],
              "edges": [edge("CALL", "f:rec", "f:rec"),
                        edge("CALL", "f:a", "f:b"), edge("CALL", "f:b", "f:a")]},
    scoped_cycle_rules=[{
        "scope": "acyclic_init_dependency",
        "edges": [{"source": "f:a", "target": "f:b"},
                  {"source": "f:b", "target": "f:a"}],
    }],
    expect=[{"rule_id": "DRC-C002", "status": "VIOLATION", "severity": "ERROR",
             "related_contains": ["acyclic_init_dependency"]}])

C003_POS = as_fixture(
    "c003-stale-path", "Designed branch unsupported under COMPLETE coverage "
                       "-> STALE/INVALID PATH",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": []},
    design_claims=[{"id": "b1", "kind": "BRANCH_CLAIM", "source": "f:a",
                    "target": "f:b", "coverage": "COMPLETE"}],
    expect=[{"rule_id": "DRC-C003", "status": "WARNING"}])

C003_UNC = as_fixture(
    "c003-partial-branch", "Designed branch unsupported under PARTIAL "
                           "coverage -> UNKNOWN",
    topology={"nodes": [node("f:a", file="a.py"), node("f:b", file="b.py")],
              "edges": []},
    design_claims=[{"id": "b1", "kind": "BRANCH_CLAIM", "source": "f:a",
                    "target": "f:b", "coverage": "PARTIAL"}],
    expect=[{"rule_id": "DRC-C003", "status": "UNKNOWN"}])

# ---------------------------------------------------------------------------
ALL = [
    P001_POS, P001_NEG, P001_UNC, P001_INFERRED,
    P002_POS, P002_NEG, P003_POS, P003_NEG, P003_UNC, P004_POS,
    D001_POS, D001_MERGE, D001_UNC,
    S001_POS, S001_NEG, S002_POS, S002_NEG, S003_POS,
    R001_POS, R001_NEG, R002_POS, R002_NEG, R003_POS, R003_NEG,
    U001_POS, U002_POS, D002_POS,
    C001_POS, C001_NEG, C002_LEGAL, C002_POS, C003_POS, C003_UNC,
]


def build_fixtures_doc() -> dict:
    return {
        "generator": "rintel.drc.fixtures (SOFTWARE-DRC0 §13)",
        "note": "Deterministic DRC behavior contract. Every rule has "
                "positive/negative/uncertain fixtures where relevant.",
        "fixtures": ALL,
    }


def write_fixtures(path: Path = FIXTURES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_fixtures_doc(), ensure_ascii=False, indent=1))
    return path


if __name__ == "__main__":
    p = write_fixtures()
    print(f"fixtures written: {p} ({len(ALL)} scenarios)")
