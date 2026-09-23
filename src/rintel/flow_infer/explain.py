"""Witness chain explainer (FLOW-INFER0 §4, §22).

Every derived flow object can be explained back to:
    Flow → FlowRegion → CALL/CONTROL/DATA facts → Function/Subroutine → Source
"""
from __future__ import annotations

from .facts import FacFacts


def explain_edge(facts: FacFacts, edge: dict) -> dict:
    """Human-readable witness chain for one flow edge."""
    witnesses = edge.get("witnesses") or []
    chain = []
    for w in witnesses:
        fid = w.get("fact_id", "")
        line = w.get("line")
        chain.append({
            "fact_id": fid,
            "expr": w.get("expr"),
            "line": line,
            "provider": "joern",
            "kind": "CALL",
            "truth_class": "OBSERVED",
        })
    return {
        "edge_id": edge.get("edge_id"),
        "source": edge.get("source"),
        "target": edge.get("target"),
        "truth_class": edge.get("truth_class"),
        "target_resolution": edge.get("target_resolution"),
        "witness_chain": chain,
        "source_line": (chain[0]["line"] if chain else None),
    }


def explain_region(facts: FacFacts, region: dict) -> dict:
    return {
        "region_id": region["region_id"],
        "name": region["name"],
        "evidence": {
            "dispatch_table": True,
            "call_facts": region["witnesses"],
            "why": region["why"],
        },
        "derived": region["derived"],
        "member_markup": {
            "methods": region["methods"],
            "handlers": region["handler_symbols"],
            "callees": region["callees"],
        },
    }


def source_of_witness(facts: FacFacts, w: dict) -> dict:
    """Resolve the witness to a repo-relative source location (Monaco)."""
    line = w.get("line")
    file = None
    fid = w.get("fact_id") or ""
    # fact ids look like joern:call:<fn>:<callee>@<line>; the lane node file
    # is the best-known location hint.
    for rel in {"sfac/sfac.c", "sfac/scrm.c", "sfac/spol.c", "sfac/stoken.c"}:
        if facts.read_file(rel):
            file = rel
            break
    return {"file": file, "line": line, "fact_id": fid}
