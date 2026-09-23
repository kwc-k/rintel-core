"""Master flow assembly + regions + scenarios + membership (FLOW-INFER0 §8-§15).

Everything produced here is a DERIVED projection:
  hard evidence:  frozen CALL facts + dispatch tables + source guard text
  soft evidence:  method-name family grouping (naming conventions in the
                  source registry) — labeled SUGGESTED/DERIVED, never
                  presented as code facts.
No graph-clustering anywhere (diagnostic-only at most; §13).
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .facts import FacFacts, APPS
from .entry import method_family, dispatch_guards, classify_guard


def build(facts: FacFacts, app: str) -> dict:
    rel = APPS[app]["file"]
    table = facts.method_tables().get(rel, [])
    callee_by_handler: dict[str, list[dict]] = {}     # handler -> call facts
    for name, handler, line in table:
        facts_for = facts.callee_edges(handler)
        callee_by_handler.setdefault(handler, []).extend(facts_for)

    # -- families from the dispatch table (naming evidence, >=2 methods) ----
    fam_of: dict[str, str | None] = {}
    for name, handler, line in table:
        fam_of[name] = method_family(name)
    fam_count = Counter(f for f in fam_of.values() if f)
    families = sorted({f for f, c in fam_count.items() if c >= 2})

    # joern records macro/constant "calls" (NUMBER, LIST, MAXNARGS, stdlib…)
    # as exprs of unresolved CALL facts.  They are NOT symbols — never nodes.
    NON_SYMBOLS = {
        "NUMBER", "STRING", "LIST", "TUPLE", "KEYWORD", "COMMENT",
        "MAXNARGS", "MAXLINELENGTH", "MAXMETHODNAME",
        "ERR_LINEUNTERMINATED", "ERR_LINETOOLONG", "ERR_NOVARIABLE",
        "ERR_ARGSTOOMANY", "ERR_SYNTAX", "ERR_EVAL",
        "free", "malloc", "calloc", "realloc", "printf", "fprintf",
        "sprintf", "snprintf", "fopen", "fclose", "fgets", "feof", "fseek",
        "fread", "fwrite", "exit", "atoi", "atof", "strcmp", "strncmp",
        "strcpy", "strncpy", "strlen", "strcat", "memcpy", "memset", "abs",
        "fabs", "sqrt", "pow", "exp", "log", "sin", "cos", "floor", "ceil",
    }

    def _is_symbol(name: str) -> bool:
        if not name or name in NON_SYMBOLS:
            return False
        if name.isupper() or name.startswith("ERR_"):
            return False
        return True

    # -- per-family region: methods + callees (resolved + call-expr names) --
    regions: list[dict] = []
    fam_methods: dict[str, list[str]] = defaultdict(list)
    for name, handler, line in table:
        f = fam_of.get(name)
        if f:
            fam_methods[f].append(name)
    for fam in families:
        names = fam_methods[fam]
        handlers = [h for n, h, l in table if fam_of.get(n) == fam]
        callees: list[str] = []
        unresolved = 0
        witnesses: list[dict] = []
        for h in handlers:
            for e in facts.callee_edges(h):
                tgt = e.get("target") or ""
                w = (e.get("representative_witnesses") or [e])[0]
                witnesses.append({
                    "fact_id": w.get("fact_id"),
                    "expr": w.get("expr"),
                    "line": (w.get("source") or {}).get("line"),
                })
                if tgt == "[Unknown Dynamic Target]":
                    unresolved += 1
                    nm = w.get("expr")
                else:
                    nm = tgt.split(":")[-1]
                if nm and nm not in callees:
                    callees.append(nm)
        callees[:] = [c for c in callees if _is_symbol(c)]
        regions.append({
            "region_id": f"region:{app}:{fam}",
            "name": fam,
            "app": app,
            "methods": names,
            "handler_symbols": [f"node:FUNCTION:{h}" for h in handlers],
            "callees": callees,
            "member_symbols": [f"cmd:{app}:{n}" for n in names],
            "shared_with": [],
            "derived": True,
            "why": [
                f"dispatch-table family: {len(names)} methods share the '{fam}' prefix "
                f"({rel} registry, naming convention evidence)",
                "call connectivity is hard evidence from frozen CALL facts",
            ],
            "witnesses": witnesses[:20],
            "unresolved_callee_count": unresolved,
        })
    region_by_name = {r["name"]: r for r in regions}

    # -- shared core: callees referenced from >=2 families ------------------
    callee_fams: dict[str, set[str]] = defaultdict(set)
    for r in regions:
        for c in r["callees"]:
            callee_fams[c].add(r["name"])
    shared = {c: sorted(fs) for c, fs in callee_fams.items() if len(fs) >= 2}

    # -- nodes / edges -------------------------------------------------------
    nodes: list[dict] = []
    edges: list[dict] = []
    entry_id = f"{app}:entry"
    init_id = f"{app}:init"
    dispatch_id = f"{app}:dispatch"
    exit_id = f"{app}:exit"

    # entry witness: main facts
    main_edges = facts.callee_edges("main")
    init_exprs = []
    for e in main_edges:
        w = (e.get("representative_witnesses") or [e])[0]
        nm = w.get("expr") or w.get("fact_id", "").split(":")[-1]
        if nm.startswith("Init"):
            init_exprs.append({"fact_id": w.get("fact_id"), "expr": nm,
                               "line": (w.get("source") or {}).get("line")})
    nodes.append({"node_id": entry_id, "kind": "entry", "label": f"{app}: main (entry)",
                  "app": app, "members": ["node:FUNCTION:main"], "truth_class": "OBSERVED" if facts.node_of("main") else "OBSERVED",
                  "why": ["frozen symbol main with call facts"],
                  "witness_count": len(main_edges),
                  "representative_witnesses": [e.get("representative_witnesses") or [{}] for e in main_edges[:1]]})
    nodes.append({"node_id": init_id, "kind": "init", "label": APPS[app]["init"],
                  "app": app, "members": [APPS[app]["init"]],
                  "truth_class": "DERIVED",
                  "why": ["init call recorded in frozen call fact (expr name; target unresolved)"],
                  "witness_count": len(init_exprs),
                  "representative_witnesses": init_exprs})
    nodes.append({"node_id": dispatch_id, "kind": "dispatch",
                  "label": "ParseArgs(command dispatcher)", "app": app,
                  "members": ["node:FUNCTION:ParseArgs"],
                  "truth_class": "OBSERVED",
                  "why": ["main→ParseArgs is a resolved frozen CALL fact"],
                  "witness_count": 1,
                  "representative_witnesses": [e.get("representative_witnesses") or [{}] for e in main_edges if (e.get("target") or "").endswith(":ParseArgs")][:1]})
    nodes.append({"node_id": exit_id, "kind": "exit", "label": "main → return 0 (process exit)", "app": app,
                  "members": ["exit"], "truth_class": "DERIVED",
                  "why": ["driver skeleton ends with return 0; 'Exit' is the command-table exit method"],
                  "witness_count": 0, "representative_witnesses": []})

    edges.append({"edge_id": f"e:{app}:entry-init", "source": entry_id, "target": init_id,
                  "kind": "flow",
                  "truth_class": "OBSERVED" if init_exprs else "DERIVED",
                  "witnesses": init_exprs[:1],
                  "why": ["frozen main→Init call fact (expr name)" if init_exprs
                          else "driver skeleton: main calls Init (call fact unresolved in this evidence set)"],
                  "resolution": "ACTIVE"})
    parse_args_facts = [e for e in main_edges if (e.get("target") or "").endswith(":ParseArgs")]
    edges.append({"edge_id": f"e:{app}:init-dispatch", "source": init_id, "target": dispatch_id,
                  "kind": "flow",
                  "truth_class": "OBSERVED" if parse_args_facts else "DERIVED",
                  "witnesses": [e.get("representative_witnesses") or [{}] for e in parse_args_facts][:1],
                  "why": ["frozen main→ParseArgs call fact" if parse_args_facts
                          else "driver skeleton: ParseArgs is the dispatcher (fact unresolved in this evidence set)"],
                  "resolution": "ACTIVE"})
    edges.append({"edge_id": f"e:{app}:dispatch-exit", "source": dispatch_id, "target": exit_id,
                  "kind": "flow", "truth_class": "DERIVED",
                  "witnesses": [],
                  "why": ["driver skeleton: ParseArgs returns and main returns 0"], "resolution": "ACTIVE"})

    for fam in families:
        rid = f"region:{app}:{fam}"
        nodes.append({"node_id": rid, "kind": "region", "label": f"{fam} calculation chain",
                      "app": app, "members": fam_methods[fam],
                      "count": len(fam_methods[fam]),
                      "truth_class": "DERIVED",
                      "why": region_by_name[fam]["why"],
                      "witness_count": len(region_by_name[fam]["witnesses"]),
                      "representative_witnesses": region_by_name[fam]["witnesses"][:5],
                      "capability": "PARTIAL" if region_by_name[fam]["unresolved_callee_count"] else "COMPLETE"})
        edges.append({"edge_id": f"e:{app}:dispatch-{fam}", "source": dispatch_id, "target": rid,
                      "kind": "guard", "truth_class": "DERIVED",
                      "guard_id": f"g-dispatch:{app}:{fam_methods[fam][0]}",
                      "witnesses": [],
                      "why": ["command-name dispatch guard: command ∈ {{family}}"],
                      "resolution": "UNKNOWN"})
        for name in fam_methods[fam]:
            cid = f"cmd:{app}:{name}"
            nodes.append({"node_id": cid, "kind": "command", "label": name, "app": app,
                          "members": [f"node:FUNCTION:{handler_of(table, name)}"],
                          "truth_class": "OBSERVED",
                          "why": ["registry entry (name, handler) in dispatch table"],
                          "witness_count": 0, "representative_witnesses": []})
            edges.append({"edge_id": f"e:{app}:{fam}-{name}", "source": rid, "target": cid,
                          "kind": "flow", "truth_class": "DERIVED",
                          "why": ["region membership from dispatch table"], "resolution": "ACTIVE"})

    # commands outside a kept family (no family, or singleton family) are
    # attached to the dispatch directly — no evidence-based grouping.
    for name, handler, line in table:
        f = fam_of.get(name)
        if f is None or f not in families:
            cid = f"cmd:{app}:{name}"
            nodes.append({"node_id": cid, "kind": "command", "label": name, "app": app,
                          "members": [f"node:FUNCTION:{handler}"],
                          "truth_class": "OBSERVED",
                          "why": ["registry entry (name, handler) in dispatch table"],
                          "witness_count": 0, "representative_witnesses": []})
            edges.append({"edge_id": f"e:{app}:dispatch-{name}", "source": dispatch_id, "target": cid,
                          "kind": "flow", "truth_class": "DERIVED",
                          "why": ["command without evidence-based family; left ungrouped (UNKNOWN)"],
                          "resolution": "UNKNOWN"})

    # shared core nodes (identity kept: callee names, not copies)
    for cal in sorted(shared):
        sid = f"shared:{app}:{cal}"
        nodes.append({"node_id": sid, "kind": "shared", "label": cal, "app": app,
                      "members": [cal], "count": len(shared[cal]),
                      "truth_class": "OBSERVED",
                      "why": [f"called from regions {shared[cal]} (frozen CALL facts)"],
                      "witness_count": 0, "representative_witnesses": []})
    # handler → callee edges (hard: frozen call facts; name from expr when unresolved)
    for name, handler, line in table:
        src = f"cmd:{app}:{name}"
        seen = set()
        for e in facts.callee_edges(handler):
            w = (e.get("representative_witnesses") or [e])[0]
            tgt = e.get("target") or ""
            if tgt == "[Unknown Dynamic Target]":
                cal = w.get("expr")
                res = "UNKNOWN"
            else:
                cal = tgt.split(":")[-1]
                res = "EXACT"
            if not cal or cal in seen or not _is_symbol(cal):
                continue
            seen.add(cal)
            if cal in shared:
                target = f"shared:{app}:{cal}"
            elif cal in {APPS[app]["init"], "SetModName", "ParseArgs"}:
                continue                      # already in scaffolding edges
            else:
                target = f"regioncal:{app}:{cal}"
                if not any(n["node_id"] == target for n in nodes):
                    nodes.append({"node_id": target, "kind": "unknown", "label": f"{cal} (unresolved)" if res == "UNKNOWN" else cal,
                                  "app": app, "members": [cal],
                                  "truth_class": "OBSERVED",
                                  "capability": "PARTIAL" if res == "UNKNOWN" else None,
                                  "why": ["callee reached from command handler (frozen call fact)"],
                                  "witness_count": 1,
                                  "representative_witnesses": [{"fact_id": w.get("fact_id"), "expr": w.get("expr"),
                                                                "line": (w.get("source") or {}).get("line")}]})
            edges.append({"edge_id": f"e:{app}:{name}-{cal}", "source": src, "target": target,
                          "kind": "flow", "truth_class": "OBSERVED",
                          "target_resolution": res,
                          "witnesses": [{"fact_id": w.get("fact_id"), "expr": w.get("expr"),
                                         "line": (w.get("source") or {}).get("line")}],
                          "why": ["frozen CALL fact"], "resolution": "ACTIVE"})

    guards = dispatch_guards(facts, app)

    return {
        "flow_id": f"master:{app}:fac_c",
        "name": f"FAC {app} master flow",
        "app": app,
        "entry_symbols": ["node:FUNCTION:main"],
        "exit_symbols": ["exit"],
        "nodes": nodes,
        "edges": edges,
        "regions": regions,
        "scenario_ids": [f"scenario:{app}:{fam}" for fam in families],
        "witnesses": [w for e in main_edges for w in (e.get("representative_witnesses") or [])][:5],
        "truth_summary": {
            "derived_nodes": sum(1 for n in nodes if n["truth_class"] == "DERIVED"),
            "observed_nodes": sum(1 for n in nodes if n["truth_class"] == "OBSERVED"),
            "derived_edges": sum(1 for e in edges if e["truth_class"] == "DERIVED"),
            "observed_edges": sum(1 for e in edges if e["truth_class"] == "OBSERVED"),
            "unresolved_target_edges": sum(1 for e in edges if e.get("target_resolution") == "UNKNOWN"),
        },
        "capability_summary": {
            "CALL": "PARTIAL (全部来自冻结 CALL 事实; 部分 target_resolution=UNKNOWN, 名称来自调用点 expr)",
            "CONTROL": "UNKNOWN (冻结 lane 无 CONTROL 事实, UI 不补造)",
            "DATA": "PARTIAL (FAC C cross-procedural DATA = PARTIAL 逐字保留)",
            "STATE": "UNKNOWN",
            "GUARD": "PARTIAL (仅 dispatch 命令名 guard 可静态判定; 其余 RUNTIME_DEPENDENT/UNKNOWN)",
            "FORTRAN": "PARTIAL (lfortran_fac_dger 仅 dger.f; blas/lapack 调用不在冻结 C lane 内)",
        },
        "guards": guards,
        "shared_core": [{"name": c, "regions": sorted(fs)} for c, fs in sorted(shared.items())],
        "derived": True,
    }


def handler_of(table: list[tuple[str, str, int]], name: str) -> str:
    for n, h, l in table:
        if n == name:
            return h
    return name


def build_scenarios(facts: FacFacts, master: dict) -> list[dict]:
    app = master["app"]
    regions = {r["region_id"]: r for r in master["regions"]}
    fam_names = {r["name"] for r in master["regions"]}
    scenarios: list[dict] = []
    for fam in sorted(fam_names):
        rid = f"region:{app}:{fam}"
        active = [rid]
        inactive = [f"region:{app}:{f}" for f in fam_names if f != fam]
        # dispatch guard resolution under this constraint
        gres = []
        for g in master["guards"]:
            gfam = g.get("family")
            if gfam is None:
                res = "UNKNOWN"
            elif gfam == fam:
                res = "TRUE"
            else:
                res = "FALSE"
            gres.append({"guard_id": g["guard_id"], "resolution": res})
        # shared regions = callee nodes referenced by another region
        scenarios.append({
            "scenario_id": f"scenario:{app}:{fam}",
            "name": f"{fam} chain",
            "app": app,
            "constraints": [f"command_family == '{fam}'"],
            "active_regions": active,
            "inactive_regions": inactive,
            "unknown_regions": [],
            "shared_regions": sorted({rid for rid in regions if rid in active}),
            "witnesses": [{"fact_id": r.get("witnesses", [{}])[0].get("fact_id")} if r.get("witnesses") else {} for r in master["regions"]][:3],
            "guard_resolutions": gres,
            "coverage": "PARTIAL",
        })
    return scenarios
