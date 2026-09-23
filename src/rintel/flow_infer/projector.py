"""Artifact projector + CLI (FLOW-INFER0 §26-§27, FI9).

Deterministic: same snapshot + same constraints ⇒ byte-identical JSON
(fixed key order, sorted lists).  Outputs:

    analysis_tournament/flow_infer/
      fac_entrypoints.json   fac_dispatch.json   fac_master_flow.json
      fac_scenarios.json     fac_regions.json    fac_manual_audit.json
      fac_unit_summary.json  fixtures/

The artifacts are DERIVED projections and carry their own truth/capability
summary plus witness chains back to frozen call facts (explain.py).
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from .facts import FacFacts, APPS, load_fac_bundle, fac_source_root
from .entry import method_family, dispatch_guards
from .master_flow import build, build_scenarios, handler_of
from .explain import explain_edge

OUT = Path(__file__).resolve().parents[3] / "analysis_tournament" / "flow_infer"


def _sortable(o):
    if isinstance(o, dict):
        return {k: _sortable(v) for k, v in sorted(o.items())}
    if isinstance(o, list):
        items = [_sortable(v) for v in o]
        return sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
    return o


def dump(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(_sortable(obj), ensure_ascii=False, indent=2) + "\n")
    return p


def build_memberships(facts: FacFacts, masters: list[dict]) -> list[dict]:
    """symbol → scenario memberships; shared = used by >=2 scenarios (FI4)."""
    name_scenarios: dict[str, set[str]] = defaultdict(set)
    for m in masters:
        app = m["app"]
        scens = m["scenario_ids"]
        for name, handler, line in facts.method_tables().get(APPS[app]["file"], []):
            sid = f"scenario:{app}:{method_family(name)}" if method_family(name) else None
            if sid and sid in scens:
                name_scenarios[handler].add(sid)
        # region member callees participate too (shared core)
        for r in m["regions"]:
            sid = f"scenario:{app}:{r['name']}"
            for c in r["callees"]:
                name_scenarios[c].add(sid)
    out = []
    for name, sids in sorted(name_scenarios.items()):
        node = facts.node_of(name) or {}
        out.append({
            "symbol_id": f"node:FUNCTION:{name}",
            "symbol_name": name,
            "file": node.get("file"),
            "scenario_ids": sorted(sids),
            "region_ids": [f"region:{s.split(':')[1]}:{s.split(':')[-1].replace(' chain', '')}" for s in sorted(sids)],
            "kind": "shared" if len(sids) >= 2 else "single",
            "witnesses": [w for e in facts.callee_edges(name) for w in (e.get("representative_witnesses") or [])][:3],
        })
    return out


def run(lane: str = "fac_c", out_dir: Path | None = None,
        extra_topology: dict | None = None) -> dict[str, Path]:
    global OUT
    if out_dir:
        OUT = out_dir
    bundle = load_fac_bundle()
    facts = FacFacts(bundle, fac_source_root(), extra=extra_topology)
    masters = [build(facts, app) for app in APPS if app_of_has_table(facts, app)]
    scenarios = [s for m in masters for s in build_scenarios(facts, m)]
    memberships = build_memberships(facts, masters)

    entries = []
    for app in APPS:
        rel = APPS[app]["file"]
        if not facts.method_tables().get(rel):
            continue
        entries.append({
            "app": app,
            "entry_symbol": "node:FUNCTION:main",
            "file": rel,
            "dispatcher": "ParseArgs",
            "init": APPS[app]["init"],
            "methods_total": len(facts.method_tables()[rel]),
            "witness": [e.get("representative_witnesses") or [{}] for e in facts.callee_edges("main")][:1],
        })
    dispatch = []
    for app in APPS:
        rel = APPS[app]["file"]
        table = facts.method_tables().get(rel)
        if not table:
            continue
        dispatch.append({
            "app": app,
            "file": rel,
            "dispatcher_symbol": "node:FUNCTION:ParseArgs",
            "methods": [{"name": n, "handler": h, "line": i + 1,
                         "family": method_family(n)} for n, h, i in table],
            "guard_model": {
                "dispatch_guards": [g["guard_id"] for g in dispatch_guards(facts, app)],
                "runtime_dependent": "所有 argc/argt/输入类型类 guard 均为 RUNTIME_DEPENDENT (§7)",
                "static_evaluable": "仅命令名 → 方法族映射可随 scenario 约束静态判定",
            },
        })

    paths = {
        "entrypoints": dump("fac_entrypoints.json", {"lane": lane, "source_root": str(fac_source_root()),
                                                     "entries": entries}),
        "dispatch": dump("fac_dispatch.json", {"lane": lane, "apps": dispatch}),
        "master_flow": dump("fac_master_flow.json", {"lane": lane, "flows": masters}),
        "scenarios": dump("fac_scenarios.json", {"lane": lane, "scenarios": scenarios}),
        "regions": dump("fac_regions.json", {"lane": lane,
                                             "regions": [r for m in masters for r in m["regions"]]}),
        "membership": dump("fac_membership.json", {"lane": lane, "memberships": memberships}),
        "audit": dump("fac_manual_audit.json", manual_audit_stub(masters)),
        "unit": dump("fac_unit_summary.json", unit_summary(masters, scenarios, memberships)),
    }
    return paths


def app_of_has_table(facts: FacFacts, app: str) -> bool:
    return bool(facts.method_tables().get(APPS[app]["file"]))


def manual_audit_stub(masters: list[dict]) -> dict:
    return {
        "lane": "fac_c",
        "method": "人工核查: flow edge / guard / membership / shared-core 逐条对照 frozen CALL fact 与源码行; 记录 correct/incorrect/unknown",
        "verdict_counts": {"flow_edges": {"checked": 0, "correct": 0, "incorrect": 0, "unknown": 0},
                           "guards": {"checked": 0, "correct": 0, "incorrect": 0, "unknown": 0},
                           "memberships": {"checked": 0, "correct": 0, "incorrect": 0, "unknown": 0},
                           "shared_core": {"checked": 0, "correct": 0, "incorrect": 0, "unknown": 0}},
        "items": [],
    }


def unit_summary(masters: list[dict], scenarios: list[dict], memberships: list[dict]) -> dict:
    apps = {m["app"]: m for m in masters}
    return {
        "per_app": {
            app: {
                "nodes": len(m["nodes"]),
                "edges": len(m["edges"]),
                "regions": len(m["regions"]),
                "guards": len(m["guards"]),
                "truth": m["truth_summary"],
                "capability": m["capability_summary"],
            }
            for app, m in apps.items()
        },
        "scenarios": len(scenarios),
        "memberships": len(memberships),
        "shared_memberships": sum(1 for x in memberships if x["kind"] == "shared"),
        "fixture_notes": [
            "F8 loop/recursion: C 循环与递归不产生 guard 结论; 递归调用在冻结 CALL 事实中按边呈现",
            "F9 Fortran internal subroutine: lfortran_fac_dger.json 覆盖 dger.f; Fortran 语义富集 = PARTIAL",
        ],
    }


if __name__ == "__main__":
    paths = run()
    for k, p in paths.items():
        print(f"{k}: {p} ({os.path.getsize(p)} bytes)")
