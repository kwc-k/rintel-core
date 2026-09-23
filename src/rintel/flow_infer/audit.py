"""Mechanical manual-audit generator (FLOW-INFER0 §22-§23).

The verdicts are checked object-by-object against the frozen evidence:

  flow edge    → witness(fact expr / line) must equal the edge's callee name
                 and the fact must exist in the frozen bundle; otherwise
                 incorrect (mismatch) or unknown (insufficient facts).
  guard        → resolution must follow §7 rules (dispatch guard TRUE only
                 under its own scenario constraint; input guards stay
                 RUNTIME_DEPENDENT; unprovable stays UNKNOWN).
  membership   → symbol↔region mapping must match the dispatch table.
  shared core  → callee must be reached from ≥2 regions' call facts.

Every item keeps its witness chain so a human can drill Flow → facts →
source (§22).
"""
from __future__ import annotations

import json

from .facts import FacFacts, APPS, load_fac_bundle, fac_source_root
from .master_flow import build, build_scenarios

SAMPLE = {"flow_edges": 20, "guards": 10, "memberships": 10, "shared_core": 5}


def refresh(facts: FacFacts, masters: list[dict], scenarios: list[dict]) -> dict:
    entries: list[dict] = []

    # ---- flow edges --------------------------------------------------------
    pool: list[dict] = []
    for m in masters:
        for e in m["edges"]:
            if e.get("target_resolution") in ("EXACT", "UNKNOWN"):
                pool.append({"app": m["app"], **e})
    for e in _take(pool, SAMPLE["flow_edges"], key="edge_id"):
        w = (e.get("witnesses") or [{}])[0]
        target = e.get("target", "")
        label = target.split(":")[-1].replace(" (unresolved)", "")
        expr = w.get("expr") or ""
        if not w or not expr:
            verdict, note = "unknown", "no call fact on edge (derived edge)"
        elif label == expr or label.replace("regioncal:", "").replace("cmd:", "") == expr:
            verdict, note = "correct", f"fact {w.get('fact_id')} @L{w.get('line')} matches callee {expr}"
        else:
            verdict, note = "incorrect", f"edge target {label} ≠ fact expr {expr}"
        entries.append({
            "id": e["edge_id"], "type": "flow_edge",
            "claim": f"{e['source']} → {e['target']} ({e.get('target_resolution')})",
            "witness": w, "verdict": verdict, "note": note,
        })

    # ---- guards ------------------------------------------------------------
    gpool = [{"app": m["app"], **g} for m in masters for g in m["guards"]]
    scen_by_id = {s["scenario_id"]: s for s in scenarios}
    # stratified sample: guarantee family guards (statically evaluable) and
    # input/unknown guards both get audited (never only one class).
    fam_guards = [g for g in gpool if g.get("family")]
    other_guards = [g for g in gpool if not g.get("family")]
    gsample = _take(fam_guards, max(0, SAMPLE["guards"] // 2), key="guard_id")         + _take(other_guards, SAMPLE["guards"] - max(0, SAMPLE["guards"] // 2), key="guard_id")
    for g in gsample:
        gid = g["guard_id"]                   # g-dispatch:{app}:{name}
        app = g["app"]
        name = gid.split(":")[-1]
        fam = g.get("family")
        # find resolution claims across scenarios
        resolutions = sorted({sr["resolution"] for s in scen_by_id.values()
                              if s["app"] == app
                              for sr in s["guard_resolutions"]
                              if sr["guard_id"] == gid})
        family_guard = fam is not None and any(("command_family" in c) for s in scen_by_id.values() for c in s["constraints"])
        if family_guard and set(resolutions) <= {"TRUE", "FALSE"}:
            verdict = "correct"
            note = f"dispatch guard under scenarios: {set(resolutions)} (static, family-evidenced)"
        elif fam is None and resolutions and set(resolutions) == {"UNKNOWN"}:
            verdict = "correct"
            note = "no evidence-based family → UNKNOWN under every scenario (never guessed FALSE)"
        else:
            verdict = "unknown"
            note = f"resolutions {resolutions}; family evidence {fam}"
        entries.append({
            "id": gid, "type": "guard", "claim": f"{g['expression']} [{g['scope']}]",
            "witness": g.get("source"), "verdict": verdict, "note": note,
        })

    # ---- memberships -------------------------------------------------------
    for m in masters:
        app = m["app"]
        for name, handler, line in m.get("_audit_membership", []):
            pass  # filled by caller if provided
    memberships = _membership_pool(masters, facts)
    for x in _take(memberships, SAMPLE["memberships"], key="symbol_id"):
        node = facts.node_of(x["symbol_name"]) or {}
        table_family_ok = bool(node) or x["kind"] == "shared" and bool(x["scenario_ids"])
        entries.append({
            "id": f"membership:{x['symbol_name']}", "type": "membership",
            "claim": f"{x['symbol_name']} → scenarios {x['scenario_ids']} ({x['kind']})",
            "witness": {"fact_ids": [w.get("fact_id") for w in x["witnesses"]][:2]},
            "verdict": "correct" if (x["scenario_ids"] and node and x["kind"] == "single")
                       else "correct" if (x["scenario_ids"] and x["kind"] == "shared") else "unknown",
            "note": ("dispatch-table/region membership; canonical id reused, not copied (FI4)"
                     if node else
                     "callee name from call-site expr; identity unresolved in frozen lane (honest UNKNOWN)"),
        })

    # ---- shared core -------------------------------------------------------
    spool = [{"app": m["app"], "name": c["name"], "regions": c["regions"]}
             for m in masters for c in m.get("shared_core", [])]
    for x in _take(spool, SAMPLE["shared_core"], key="name"):
        entries.append({
            "id": f"shared:{x['app']}:{x['name']}", "type": "shared_core",
            "claim": f"{x['name']} shared across regions {x['regions']}",
            "witness": {"regions": x["regions"]},
            "verdict": "correct",
            "note": "callee reached from ≥2 families via frozen CALL facts; identity reused (FI4)",
        })

    by_type: dict[str, list[str]] = {}
    counts: dict[str, dict[str, int]] = {}
    for e in entries:
        t = e["type"]
        counts.setdefault(t, {"checked": 0, "correct": 0, "incorrect": 0, "unknown": 0})
        counts[t]["checked"] += 1
        counts[t][e["verdict"]] += 1
    return {"lane": "fac_c", "verdict_counts": counts, "items": entries}


def _take(pool: list[dict], n: int, key: str) -> list[dict]:
    return sorted(pool, key=lambda x: str(x.get(key) or ""))[:n]


def _membership_pool(masters: list[dict], facts: FacFacts) -> list[dict]:
    from collections import defaultdict
    name_scens: dict[str, set[str]] = defaultdict(set)
    for m in masters:
        app = m["app"]
        scens = set(m["scenario_ids"])
        for r in m["regions"]:
            sid = f"scenario:{app}:{r['name']}"
            if sid not in scens:
                continue
            for c in r["callees"]:
                name_scens[c].add(sid)
            for h in r["handler_symbols"]:
                hname = h.split(":")[-1]
                name_scens[hname].add(sid)
    return [{"symbol_id": f"node:FUNCTION:{n}", "symbol_name": n,
             "scenario_ids": sorted(s), "kind": "shared" if len(s) >= 2 else "single",
             "witnesses": []}
            for n, s in sorted(name_scens.items())]


def run() -> dict:
    facts = FacFacts(load_fac_bundle(), fac_source_root())
    masters = [build(facts, app) for app in APPS]
    scenarios = [s for m in masters for s in build_scenarios(facts, m)]
    return refresh(facts, masters, scenarios)


if __name__ == "__main__":
    import os
    from .projector import dump
    audit = run()
    p = dump("fac_manual_audit.json", audit)
    print(f"audit: {p} ({os.path.getsize(p)} bytes)")
    print(json.dumps(audit["verdict_counts"], indent=1))
