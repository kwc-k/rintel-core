"""LVS fixtures (L1–L12, spec §20) + lane runners.

Deterministic test scenarios mirror the spec fixture list.  The design
side and code side are plain dicts; the engines compares them exactly as
it would compare real FlowModels against canonical topology.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_PATH = Path(__file__).resolve().parents[2] / \
    "analysis_tournament" / "lvs" / "fixtures.json"

CANON = {
    "solver": "node:FUNCTION:solver.Solver.solve",
    "io": "node:FUNCTION:solver.io.read_data",
    "cache": "node:FUNCTION:solver.cache.get",
}


def fn(cid: str, *, params: list[dict] | None = None, returns: bool = True,
       return_type: str | None = None, name: str = "") -> dict:
    return {
        "name": name or cid.split(":")[-1],
        "file": "solver.py",
        "language": "python",
        "signature": {"params": params or [], "returns": returns,
                      "return_type": return_type, "parse_failed": False},
        "source_location": {"file": "solver.py", "line": 1},
    }


def call_edge(src: str, tgt: str, **kw) -> dict:
    base = {
        "kind": "CALL", "source": src, "target": tgt,
        "truth_class": "OBSERVED", "execution_modality": "MUST",
        "target_resolution": "EXACT", "coverage": "COMPLETE",
        "representative_witnesses": [{"fact_id": f"f:{src}->{tgt}"}],
        "source_span": {"file": "solver.py", "line": 1},
    }
    base.update(kw)
    return base


def data_edge(token: str, src: str, tgt: str, **kw) -> dict:
    base = {
        "kind": "DATA", "source": src, "target": tgt,
        "truth_class": "OBSERVED", "execution_modality": "MUST",
        "target_resolution": "EXACT", "coverage": "COMPLETE",
        "data": {"token": token, "flow": "argument"},
        "representative_witnesses": [{"fact_id": f"f:{token}"}],
        "source_span": {"file": "solver.py", "line": 1},
    }
    base.update(kw)
    return base


def res_edge(src: str, res: str) -> dict:
    return {"kind": "RESOURCE", "source": src, "target": res,
            "truth_class": "OBSERVED", "coverage": "COMPLETE",
            "representative_witnesses": [{"fact_id": f"r:{src}:{res}"}],
            "source_span": {"file": "solver.py", "line": 1}}


def base_design() -> dict:
    return {
        "snapshot_id": "s-d1",
        "blocks": [
            {"id": "b-io", "name": "read_data", "kind": "function",
             "state": "existing", "binding": CANON["io"]},
            {"id": "b-solve", "name": "Solver.solve", "kind": "function",
             "state": "existing", "binding": CANON["solver"]},
        ],
        "composites": [],
        "ports": [
            {"id": "p1", "block_id": "b-io", "name": "result",
             "direction": "output", "semantic_kind": "data", "code_type": "str"},
            {"id": "p2", "block_id": "b-solve", "name": "data",
             "direction": "input", "semantic_kind": "data", "code_type": "str"},
            {"id": "p3", "block_id": "b-solve", "name": "config",
             "direction": "input", "semantic_kind": "data", "code_type": "Config"},
        ],
        "nets": [
            {"id": "n1", "kind": "data", "source_block_id": "b-io",
             "target_block_id": "b-solve", "source_port_id": "p1",
             "target_port_id": "p2", "label": "data"},
            {"id": "n2", "kind": "control", "source_block_id": "b-io",
             "target_block_id": "b-solve", "source_port_id": None,
             "target_port_id": None, "label": None},
        ],
        "claims": [],
    }


def base_code() -> dict:
    return {
        "snapshot_id": "s-c2",
        "functions": {
            CANON["io"]: fn(CANON["io"],
                            params=[], returns=True, return_type="dict"),
            CANON["solver"]: fn(CANON["solver"],
                                params=[{"name": "data", "type": "str",
                                         "has_type": True},
                                        {"name": "config", "type": "Config",
                                         "has_type": True}],
                                returns=True, return_type="Result"),
        },
        "edges": [
            call_edge(CANON["io"], CANON["solver"]),
            data_edge("data", CANON["io"], CANON["solver"]),
        ],
        "resources": [],
    }


# ---------------------------------------------------------------------------
# L1–L12
# ---------------------------------------------------------------------------

L1 = {"id": "L1-exact-match", "label": "Exact match", "design": base_design(),
      "code": base_code(), "expect_overall": "MATCH"}

L2_DESIGN = base_design()
L2_CODE = base_code()
OLD_SIG = fn(CANON["solver"],
             params=[{"name": "data", "type": "str", "has_type": True},
                     {"name": "config", "type": "Config", "has_type": True}],
             returns=True, return_type="Result")
L2_CODE["baseline_id"] = "s-c1"
L2_CODE["baseline_functions"] = {CANON["solver"]: OLD_SIG,
                                 CANON["io"]: fn(CANON["io"], returns=True,
                                                 return_type="dict")}
L2_CODE["snapshot_id"] = "s-c2"
L2_CODE["functions"][CANON["solver"]] = fn(
    CANON["solver"],
    params=[{"name": "options", "type": "str", "has_type": True}],
    returns=True, return_type="Result")
L2 = {"id": "L2-signature-changed",
      "label": "Signature changed (config → options, baseline S1→S2)",
      "design": L2_DESIGN, "code": L2_CODE, "expect_overall": "STALE",
      "expect_has": [("port", "STALE", None), ("call", "MATCH", None)]}

L3_DESIGN = base_design()
L3_CODE = base_code()
L3_CODE["edges"] = [e for e in L3_CODE["edges"] if e["kind"] != "CALL"]
L3 = {"id": "L3-call-removed", "label": "Designed CALL removed from code",
      "design": L3_DESIGN, "code": L3_CODE, "expect_overall": "MISMATCH",
      "expect_has": [("call", None, "MISMATCH")]}

L4_DESIGN = base_design()
L4_CODE = base_code()
L4_CODE["edges"] = [e for e in L4_CODE["edges"] if e["kind"] != "DATA"]
L4 = {"id": "L4-data-removed", "label": "Designed DATA removed from code",
      "design": L4_DESIGN, "code": L4_CODE, "expect_overall": "MISMATCH",
      "expect_has": [("data", None, "MISMATCH")]}

L5_CODE = base_code()   # keeps the designed CALL; only the data wire is inferred
L5_CODE["edges"] = [e for e in L5_CODE["edges"] if not (
    e["kind"] == "DATA" and e["data"].get("token") == "data")]
L5_CODE["edges"].append(data_edge("data", CANON["io"], CANON["solver"],
                                  truth_class="INFERRED",
                                  execution_modality="MAY",
                                  coverage="PARTIAL"))
L5 = {"id": "L5-data-coverage-partial",
      "label": "Data coverage partial (inferred may-flow)",
      "design": base_design(), "code": L5_CODE,
      "expect_overall": "UNKNOWN",
      "expect_has": [("data", "UNKNOWN", None)]}

L6_DESIGN = base_design()
L6_DESIGN["blocks"].append(
    {"id": "b-cache", "name": "Cache", "kind": "function",
     "state": "proposed", "binding": None})
L6_DESIGN["nets"].append(
    {"id": "n3", "kind": "control", "source_block_id": "b-solve",
     "target_block_id": "b-cache", "source_port_id": None,
     "target_port_id": None, "label": None})
L6_CODE = base_code()
L6 = {"id": "L6-proposed-cache",
      "label": "Proposed Cache block → UNBOUND, not a code error",
      "design": L6_DESIGN, "code": L6_CODE, "expect_overall": "UNBOUND",
      "expect_has": [("block", "UNBOUND", "b-cache")]}

L7_DESIGN = base_design()
L7_DESIGN["blocks"].append(
    {"id": "b-x", "name": "notifier.call", "kind": "function",
     "state": "existing", "binding": "node:FUNCTION:solver.notifier.call"})
L7_DESIGN["nets"].append(
    {"id": "n3", "kind": "control", "source_block_id": "b-solve",
     "target_block_id": "b-x", "source_port_id": None,
     "target_port_id": None, "label": None})
L7_CODE = base_code()
L7_CODE["call_capability"] = "PARTIAL"
L7_CODE["functions"]["node:FUNCTION:solver.notifier.call"] = fn(
    "node:FUNCTION:solver.notifier.call")
L7_CODE["edges"].append(
    {"kind": "CALL", "source": CANON["solver"],
     "target": "[Unknown Dynamic Target]", "truth_class": "OBSERVED",
     "execution_modality": "MUST", "target_resolution": "UNKNOWN",
     "coverage": "UNKNOWN",
     "representative_witnesses": [{"fact_id": "f:dynamic"}],
     "source_span": {"file": "solver.py", "line": 40}})
L7 = {"id": "L7-dynamic-target-unknown",
      "label": "Design CALL with only UNKNOWN targets → UNKNOWN not MISMATCH",
      "design": L7_DESIGN, "code": L7_CODE, "expect_overall": "UNKNOWN",
      "expect_has": [("call", "UNKNOWN", None)],
      "expect_no_mismatch": True}

L8_DESIGN = base_design()
L8_DESIGN["blocks"].append(
    {"id": "b-solve2", "name": "Solver2", "kind": "function",
     "state": "existing", "binding": "node:FUNCTION:solver.Solver.solve2",
     "resources": []})
L8_DESIGN["composites"].append(
    {"id": "c1", "name": "SolverCore", "block_ids": ["b-solve", "b-solve2"],
     "resources": [], "encapsulation": True})
L8_CODE = base_code()
L8_CODE["functions"]["node:FUNCTION:solver.Solver.solve2"] = fn(
    "node:FUNCTION:solver.Solver.solve2")
L8_CODE["edges"].append(res_edge(CANON["solver"], "GPU"))
L8_CODE["resources"] = ["GPU"]
L8 = {"id": "L8-resource-hidden",
      "label": "Composite hides GPU dependency → MISMATCH",
      "design": L8_DESIGN, "code": L8_CODE, "expect_overall": "MISMATCH",
      "expect_has": [("resource", "CODE_ONLY", None),
                     ("boundary", "MISMATCH", None)]}

L9_DESIGN = base_design()
L9_DESIGN["composites"].append(
    {"id": "c1", "name": "SolverCore", "block_ids": ["b-io", "b-solve"],
     "data_in": ["input"], "data_out": ["result"],
     "resources": [], "encapsulation": True})
L9_CODE = base_code()
L9_CODE["functions"]["node:FUNCTION:solver.Solver.helper"] = fn(
    "node:FUNCTION:solver.Solver.helper")
L9_CODE["edges"].append(call_edge(CANON["solver"],
                                  "node:FUNCTION:solver.Solver.helper"))
# external observable boundary of the composite
L9_CODE["edges"].insert(0, data_edge("input", "node:FUNCTION:solver.ext_in",
                                     CANON["io"]))
L9_CODE["edges"].insert(0, data_edge("result", CANON["solver"],
                                     "node:FUNCTION:solver.ext_out"))
L9_CODE["functions"]["node:FUNCTION:solver.ext_in"] = fn(
    "node:FUNCTION:solver.ext_in")
L9_CODE["functions"]["node:FUNCTION:solver.ext_out"] = fn(
    "node:FUNCTION:solver.ext_out")
L9 = {"id": "L9-composite-internals-changed",
      "label": "Composite internals changed, boundary same → BOUNDARY_EQUIVALENT",
      "design": L9_DESIGN, "code": L9_CODE, "expect_overall": "MATCH",
      "expect_has": [("boundary", "BOUNDARY_EQUIVALENT", None)]}

L10_DESIGN = base_design()
L10_DESIGN["composites"].append(
    {"id": "c1", "name": "SolverCore", "block_ids": ["b-io", "b-solve"],
     "data_in": ["input"], "data_out": ["result"],
     "resources": [], "encapsulation": True})
L10_CODE = base_code()
# same external data boundary as L9 (contract kept)
L10_CODE["edges"].insert(0, data_edge("input", "node:FUNCTION:solver.ext_in",
                                      CANON["io"]))
L10_CODE["edges"].insert(0, data_edge("result", CANON["solver"],
                                      "node:FUNCTION:solver.ext_out"))
L10_CODE["functions"]["node:FUNCTION:solver.ext_in"] = fn(
    "node:FUNCTION:solver.ext_in")
L10_CODE["functions"]["node:FUNCTION:solver.ext_out"] = fn(
    "node:FUNCTION:solver.ext_out")
# ...but the code depends on Network inside the composite (hidden)
L10_CODE["edges"].append(res_edge(CANON["solver"], "Network"))
L10_CODE["resources"] = ["Network"]
L10 = {"id": "L10-composite-boundary-changed",
      "label": "Composite boundary (hidden resource) changed → MISMATCH",
      "design": L10_DESIGN, "code": L10_CODE, "expect_overall": "MISMATCH",
      "expect_has": [("boundary", "MISMATCH", None)]}

L11_DESIGN = base_design()
L11_DESIGN["blocks"].append(
    {"id": "b-rec", "name": "rec", "kind": "function", "state": "existing",
     "binding": "node:FUNCTION:solver.rec"})
L11_CODE = base_code()
L11_CODE["functions"]["node:FUNCTION:solver.rec"] = fn("node:FUNCTION:solver.rec")
L11_CODE["edges"].append(call_edge("node:FUNCTION:solver.rec", "node:FUNCTION:solver.rec"))
L11 = {"id": "L11-legal-recursion",
      "label": "Legal recursion does not fail LVS",
      "design": L11_DESIGN, "code": L11_CODE, "expect_overall": "MATCH",
      "expect_no_mismatch": True}

L12_DESIGN = base_design()
L12_DESIGN["claims"] = [
    {"id": "cl1", "kind": "BRANCH_CLAIM", "block_id": "b-solve",
     "guard": "if use_gpu:", "coverage": "COMPLETE"}]
L12_CODE = base_code()
L12_CODE["functions"][CANON["solver"]] = fn(
    CANON["solver"],
    params=[{"name": "data", "type": "str", "has_type": True},
            {"name": "config", "type": "Config", "has_type": True}],
    returns=True, return_type="Result")
L12_CODE["functions"][CANON["solver"]]["control_landmarks"] = [
    {"semantic_kind": "BRANCH", "guard": "if cpu_mode:"}]
L12 = {"id": "L12-branch-changed",
      "label": "Branch guard changed → MISMATCH",
      "design": L12_DESIGN, "code": L12_CODE, "expect_overall": "MISMATCH",
      "expect_has": [("control", None, "MISMATCH")]}

ALL = [L1, L2, L3, L4, L5, L6, L7, L8, L9, L10, L11, L12]


def _j(value):
    """JSON-round-trip normalizer (tuples -> lists) so the in-memory
    contract equals the on-disk fixtures file exactly."""
    if isinstance(value, dict):
        return {k: _j(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_j(v) for v in value]
    return value


def build_fixtures_doc() -> dict:
    return {
        "generator": "rintel.lvs.fixtures (SOFTWARE-LVS1 §20)",
        "note": "Design side and code side are strictly separate; expected "
                "overall statuses lock the §19 precedence.",
        "fixtures": _j(ALL),
    }


def write_fixtures(path: Path = FIXTURES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_fixtures_doc(), ensure_ascii=False, indent=1))
    return path


if __name__ == "__main__":
    p = write_fixtures()
    print(f"LVS fixtures written: {p} ({len(ALL)} scenarios)")
