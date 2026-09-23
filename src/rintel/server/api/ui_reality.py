"""UI-REALITY-ALIGN0 endpoints (read-only projections for the Workbench UI).

Three additive endpoints.  **No new analysis**: every field is a re-projection
of an already frozen authority, and each element carries the authority it came
from plus an explicit truth class / coverage so the UI cannot invent either.

  GET /api/v1/software-circuit?repo_id=&node=&depth=&run_id=&app=
      node + typed-socket + typed-edge projection of a LOCAL neighborhood:
        static   : repo graph (store: tree_sitter CALLS/CONTAINS)
        sockets  : DATA-INTERFACE0 ports/shapes (per-port dtype/rank/shape)
        observed : RUNTIME-TRACE0 stats/edges + RUNTIME-DATA0 objects/events
      Sockets and observed access are joined by *evidence*, never by guessing:
      a data event is attributed to a node only when its source_line falls
      inside that node's static line range.

  GET /api/v1/agent-runs
      external-agent (MCP client) activity, read from the frozen
      `analysis_tournament/*/agent_runs/` artifacts.  Agent prose is a CLAIM;
      the tool calls and audit verdicts are the evidence.

  GET /api/v1/ui-reality
      the audited UI reality ledger (UI-REALITY-ALIGN0 deliverables).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from . import data_interface as DI
from . import runtime_api as RT
from . import semantic_flow as SF
from ..deps import get_store, require_repo, resolve_snapshot
from ...store import Store

router = APIRouter(tags=["ui-reality"])

ROOT = Path(__file__).resolve().parents[4]
TOURNAMENT = ROOT / "analysis_tournament"
UI_DIR = TOURNAMENT / "ui_reality_align0"

CALLABLE_KINDS = {"FUNCTION", "METHOD", "SUBROUTINE", "PROCEDURE", "CONSTRUCTOR"}

# A data event whose invocation id is a probe site (not a real invocation) is
# evidence of a *selective* probe, not of general memory coverage.
PROBE_PREFIX = "rt:probe"


def _basename(p: str) -> str:
    return p.rsplit("/", 1)[-1] if p else p


# SOURCE-SPAN-COL0 §12: a macro callsite, the macro declaration and the
# resolved target are three different things.  The runtime trace records the
# *expanded* C symbol (`_f_dgesv`) while the repo graph edges point at the
# Fortran routine (`DGESV`).  Joining them is explicit and the macro witness is
# carried on the edge — never silently equated.
NAME_ALIASES = {"F_DGESV": {"DGESV"}, "DGESV": {"F_DGESV"}}


def _norm(name: str) -> str:
    """Symbol-name key: the runtime trace records C symbols with a leading
    underscore and Fortran symbols with a trailing one; the repo graph does
    not.  Normalising is a *join* convenience — it never changes a payload."""
    return (name or "").strip("_").upper()


def _jsonish(raw):
    """Evidence rows store `location_json`/`payload_json` as JSON strings."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, (dict, list)) else None
    except (ValueError, TypeError):
        return None


def _span(file: str, start_line: int | None, end_line: int | None,
          kind: str = "DECLARATION", precision: str = "LINE_ONLY") -> dict:
    """SourceSpan-shaped dict.  The store carries line ranges only, so these
    spans are LINE_ONLY by construction — never a fake column."""
    if end_line and start_line and end_line < start_line:
        end_line = None          # the index can carry end before start; drop it
    if not file or not start_line:
        return {"file": file or None, "start_line": None, "end_line": None,
                "span_kind": kind, "precision": "UNKNOWN",
                "evidence": {"reason": "no location recorded by the index"},
                "text": f"{file or '?'} (unknown)"}
    text = f"{file}:{start_line}" if not end_line or end_line == start_line \
        else f"{file}:{start_line}-{end_line}"
    return {"file": file, "start_line": start_line, "end_line": end_line,
            "span_kind": kind, "precision": precision, "evidence": {
                "authority": "repo index (PG store)", "columns": "not indexed",
                "reason": "the store stores line ranges; column-exact anchors "
                          "come from the kernel SourceSpan plane when available"},
            "text": text}


def _ports_index() -> dict[str, dict]:
    """DATA-INTERFACE0 ports artifact → {(name, basename(file)): entry}."""
    try:
        ports = DI._load("ports")
    except HTTPException:
        return {}
    out: dict[str, dict] = {}
    for e in ports or []:
        name = e.get("name")
        if not name:
            continue
        out.setdefault(name, e)
        out.setdefault(f"{name}::{_basename(e.get('file', ''))}", e)
    return out


def _semantic_stage_index() -> dict[str, list[dict]]:
    """kernel/canonical symbol → Human Semantic stage membership."""
    stages = SF._load("fac_semantic_stages.json") or []
    members = SF._load("fac_memberships.json") or []
    by_id = {s["semantic_stage_id"]: s for s in stages}
    out: dict[str, list[dict]] = {}
    for m in members:
        sid = m.get("semantic_stage_id")
        st = by_id.get(sid)
        if not st:
            continue
        rec = {"stage_id": sid, "display_name": st.get("display_name"),
               "stage_kind": st.get("stage_kind"), "truth_class": st.get("truth_class"),
               "origin": st.get("origin"), "status": st.get("status"),
               "plane": "Human Semantic",
               "authority": "FLOW-SEMANTIC0 (annotation plane, not source code)"}
        v = m.get("id") or m.get("canonical_symbol_id") or m.get("canonical_id")
        if not v:
            continue
        for key in (str(v), str(v).split(":")[-1], _norm(str(v).split(":")[-1])):
            out.setdefault(key, []).append(rec)
    return out


def _runtime_for_nodes(names: set[str], run_id: str) -> dict:
    """Observed stats + edges for the requested symbol names (leading `_`
    tolerated: the runtime trace records the C symbol name)."""
    try:
        run = RT._load(run_id)
    except HTTPException:
        return {}
    stats = run.get("stats", {}) or {}
    edges = run.get("edges", []) or []
    by_name: dict[str, dict] = {}
    for sym, st in stats.items():
        by_name[_norm(sym)] = {"symbol": sym, **st}
    obs_edges: dict[str, list[dict]] = {}
    for e in edges:
        parent = _norm(e.get("parent_symbol"))
        child = _norm(e.get("child_symbol"))
        item = {"parent": parent, "parent_symbol": e.get("parent_symbol"),
                "child": child, "child_symbol": e.get("child_symbol"),
                "child_canonical": e.get("child_canonical"),
                "child_file": e.get("child_file"),
                "parent_binding": e.get("parent_binding"),
                "child_binding": e.get("child_binding"),
                "count": e.get("count"), "run_id": run.get("run", {}).get("run_id", run_id)}
        obs_edges.setdefault(parent, []).append(item)
        obs_edges.setdefault(child, []).append(item)
    return {"by_name": by_name, "edges": obs_edges, "run": run.get("run", {}),
            "names": names}


def _observed_data(run_id: str) -> dict:
    """Runtime data objects/events (RUNTIME-DATA0).  Empty dict when absent —
    the projection must degrade, not invent."""
    try:
        data = RT.runtime_data(run_id)
        # NOTE: FastAPI Query defaults are objects, not values — calling a
        # route handler directly needs every parameter passed explicitly.
        access = RT.runtime_data_access(run_id, kind="", limit=1000)
    except HTTPException:
        return {}
    return {"objects": data.get("objects", []), "locations": data.get("locations", []),
            "bindings": data.get("bindings", []), "events": access.get("events", []),
            "event_total": access.get("total"), "raw": data}


def _sockets_for(node: dict, ports_idx: dict, obs: dict) -> tuple[list[dict], dict]:
    """Static INPUT/OUTPUT sockets (DATA-INTERFACE0) + observed STATE/LOCAL
    sockets (runtime data).  Coverage is reported per node, never assumed."""
    name = node.get("name") or ""
    entry = ports_idx.get(name) or ports_idx.get(f"{name}::{_basename(node.get('path', ''))}")
    sockets: list[dict] = []
    notes: list[str] = []
    if entry:
        placeholder = [p for p in entry.get("ports", [])
                       if str(p.get("name", "")).strip() in ("", "void", "()")]
        real_ports = [p for p in entry.get("ports", []) if p not in placeholder]
        if placeholder and not real_ports:
            notes.append("DATA-INTERFACE0 recorded no parameter list for this function "
                         "(C parameter modelling is PARTIAL) → the function may still "
                         "read/write state; this is UNKNOWN, not 'no inputs'")
        for p in real_ports:
            sockets.append({
                "socket_id": p.get("port_id"),
                "direction": p.get("direction") or "INPUT",
                "name": p.get("name"),
                "semantic_type": p.get("semantic_type", "DATA"),
                "dtype": p.get("dtype"), "rank": p.get("rank"),
                "shape": p.get("shape") or [], "shape_status": p.get("shape_status"),
                "element_count": p.get("element_count"), "byte_size": p.get("byte_size"),
                "truth_class": p.get("truth_class", "DERIVED"),
                "coverage": p.get("coverage", "PARTIAL"),
                "plane": "STATIC (DATA-INTERFACE0 ports)",
                "authority": "GET /api/v1/data-interface/ports",
            })
        n_args = len([a for a in (entry.get("args") or [])
                      if str(a).strip() not in ("", "void", "()")])
        if n_args > len(real_ports):
            notes.append(
                f"DATA-INTERFACE0 models {len(real_ports)} of {n_args} declared "
                "parameters for this function → socket coverage PARTIAL")
    else:
        notes.append("no DATA-INTERFACE0 port entry for this symbol → socket coverage UNKNOWN")

    # observed data objects touched inside this node's static line range
    path = node.get("path")
    lo, hi = node.get("start_line"), node.get("end_line")
    seen: set[str] = set()
    for e in obs.get("events", []):
        src = e.get("source_line") or ""
        f, _, ln = src.rpartition(":")
        if not f or not ln.isdigit() or f != path:
            continue
        if lo and hi and not (lo <= int(ln) <= hi):
            continue
        oid = e.get("runtime_data_id")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        obj = next((o for o in obs.get("objects", []) if o["runtime_data_id"] == oid), None)
        if not obj:
            continue
        probe = str(e.get("invocation_id") or "").startswith(PROBE_PREFIX)
        sockets.append({
            "socket_id": f"{oid}:{e.get('canonical_location_id')}",
            "direction": "STATE",
            "name": obj.get("region") or _basename(str(obj.get("canonical_symbol_id"))),
            "semantic_type": "DATA", "dtype": obj.get("dtype"), "rank": obj.get("rank"),
            "shape": obj.get("shape") or [], "shape_status": obj.get("shape_status"),
            "element_count": obj.get("element_count"), "byte_size": obj.get("byte_size"),
            "truth_class": "OBSERVED",
            "coverage": obj.get("coverage", "PARTIAL"),
            "plane": "OBSERVED (runtime data)",
            "authority": f"GET /api/v1/runtime/runs/{obs.get('run_id', 'W1')}/data_access",
            "observed": {"event_id": e.get("event_id"), "kind": e.get("operation_kind"),
                         "invocation_id": e.get("invocation_id"),
                         "source_line": src, "probe": probe,
                         "byte_extent": e.get("byte_extent"),
                         "note": e.get("note"),
                         "truth_class": "OBSERVED" if not probe else "OBSERVED (probe site)"},
        })
    runtime_missing = not obs.get("objects")
    return sockets, {"notes": notes, "runtime_data_available": not runtime_missing}


def _node_out(n: dict, ports_idx: dict, obs: dict) -> dict:
    observed = obs.get("by_name", {}).get(_norm(n.get("name")))
    sockets, socket_cov = _sockets_for(n, ports_idx, obs)
    kinds = {s["direction"] for s in sockets}
    if not sockets:
        cov = "UNKNOWN"
    elif socket_cov["notes"] and "PARTIAL" in " ".join(socket_cov["notes"]):
        cov = "PARTIAL"
    else:
        cov = "COMPLETE"
    return {
        "id": n["id"], "kind": n.get("kind"), "name": n.get("name"),
        "qname": n.get("qname"), "language": n.get("language"), "path": n.get("path"),
        "start_line": n.get("start_line"), "end_line": n.get("end_line"),
        "span": _span(n.get("path"), n.get("start_line"), n.get("end_line"),
                      "DEFINITION" if n.get("kind") in CALLABLE_KINDS else "DECLARATION"),
        "truth_class": "RESOLVED",
        "authority": "GET /api/v1/graph/neighborhood (repo graph, tree_sitter)",
        "observed": ({"calls": observed.get("count"),
                      "inclusive_ms": observed.get("inclusive_ms"),
                      "exclusive_ms": observed.get("exclusive_ms"),
                      "binding": observed.get("binding"),
                      "runtime_symbol": observed.get("symbol"),
                      "truth_class": "OBSERVED",
                      "run_id": obs.get("run", {}).get("run_id")}
                     if observed else None),
        "sockets": sockets,
        "socket_directions": sorted(kinds),
        "socket_coverage": cov,
        "socket_notes": socket_cov["notes"],
        "groups": _groups_for(n),
    }


_STAGE_INDEX: dict[str, list[dict]] | None = None


def _groups_for(n: dict) -> list[dict]:
    """Real group memberships (never invented): Human Semantic stage by the
    member's canonical id, plus the file containment from the repo graph."""
    global _STAGE_INDEX
    if _STAGE_INDEX is None:
        _STAGE_INDEX = _semantic_stage_index()
    out: list[dict] = []
    for key in (n.get("name"), n.get("qname"),
                f"function:{n.get('name')}",
                _norm(n.get("name"))):
        if not key:
            continue
        for rec in _STAGE_INDEX.get(key, []) or []:
            g = {"group_id": rec["stage_id"], "label": rec["display_name"],
                 "source_type": "Human Semantic Stage", "truth_class": rec["truth_class"],
                 "origin": rec["origin"], "status": rec["status"],
                 "authority": rec["authority"]}
            if g not in out:
                out.append(g)
    return out


def _edge_span(e: dict, store: Store, repo_id: str, sid: str) -> dict:
    """Edge evidence → SourceSpan.  The store records a callsite LINE only;
    it is reported as LINE_ONLY rather than dressed up with a column."""
    try:
        rows = list(store.evidence_for(repo_id, sid, e["id"]))
    except Exception:
        rows = []
    loc = _jsonish(rows[0].get("location")) if rows else None
    if loc is None and rows:
        loc = _jsonish(rows[0].get("location_json"))
    loc = loc or {}
    span = _span(loc.get("path"), loc.get("start_line") or loc.get("line"),
                 loc.get("end_line"), "CALLSITE")
    span["evidence"] = {"authority": "GET /api/v1/evidence (repo index)",
                        "confidence": rows[0].get("confidence") if rows else None,
                        "source": rows[0].get("source") if rows else None}
    return span


@router.get("/software-circuit")
def software_circuit(repo_id: str,
                     node: str = Query(..., description="root node id, e.g. node:FUNCTION:BlockPopulation"),
                     depth: int = Query(1, ge=1, le=3),
                     run_id: str = Query("W1"),
                     app: str | None = Query(None),
                     store: Store = Depends(get_store)) -> dict:
    """Local node/socket/edge projection (Blender-style software circuit)."""
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, None)
    if not store.node_by_id(repo_id, sid, node):
        raise HTTPException(404, {"code": "node_not_found",
                                  "message": f"symbol '{node}' not found",
                                  "details": {"node": node, "snapshot": sid}})
    res = store.neighbors(repo_id, sid, node, relation=None, depth=depth,
                          max_nodes=60, direction="both")
    nodes = sorted(res["nodes"].values(), key=lambda r: (r.get("kind") != "FUNCTION", r.get("name") or ""))
    edges = [e for e in res["edges"]
             if e.get("kind") in ("CALLS", "CALL", "CONTAINS", "IMPORTS")]
    ports_idx = _ports_index()
    obs = _runtime_for_nodes({(n.get("name") or "") for n in nodes}, run_id)
    observed_data = _observed_data(run_id)
    runtime_ctx = {**obs, "run_id": run_id,
                   "events": observed_data.get("events", []),
                   "objects": observed_data.get("objects", [])}
    node_out = [_node_out(n, ports_idx, runtime_ctx) for n in nodes]
    by_id = {n["id"]: n for n in node_out}
    name_of = {n["id"]: (n.get("name") or "") for n in node_out}

    edges_out = []
    for e in edges:
        src, dst = e.get("src_id"), e.get("dst_id")
        if src not in by_id or dst not in by_id:
            continue
        src_name = _norm(name_of[src])
        dst_name = _norm(name_of[dst])
        observed = None
        via_alias = False
        for cand in obs.get("edges", {}).get(src_name, []):
            if cand["child"] == dst_name:
                observed = cand
                break
            if dst_name in NAME_ALIASES.get(cand["child"], set()):
                observed, via_alias = cand, True
                break
        if observed is None:
            for cand in obs.get("edges", {}).get(dst_name, []):
                if cand["parent"] == src_name:
                    observed = cand
                    break
        kind = "CONTAINS" if e.get("kind") == "CONTAINS" else "CALLS"
        edges_out.append({
            "id": e["id"], "kind": kind, "src": src, "dst": dst,
            "confidence": e.get("confidence"),
            "truth_class": "RESOLVED",
            "plane": "STATIC",
            "authority": "GET /api/v1/graph/neighborhood (repo graph)",
            "observed": ({"count": observed["count"], "run_id": observed["run_id"],
                          "child_binding": observed["child_binding"],
                          "parent_binding": observed["parent_binding"],
                          "child_symbol": observed["child_symbol"],
                          "via_macro": via_alias,
                          "note": ("runtime observed the macro-expanded C symbol; the "
                                   "static edge resolves through the f2c macro to the "
                                   "Fortran routine (SOURCE-SPAN-COL0 §12)")
                                  if via_alias else None,
                          "plane": "OBSERVED", "truth_class": "OBSERVED"}
                         if observed else None),
            "span": _edge_span(e, store, repo_id, sid),
        })

    # observed-only edges (runtime saw a call the static graph does not model)
    static_pairs = {(_norm(name_of[e["src"]]), _norm(name_of[e["dst"]])) for e in edges_out}
    by_norm = {_norm(n.get("name")): n for n in node_out}
    for n in node_out:
        nm = _norm(n.get("name"))
        for cand in obs.get("edges", {}).get(nm, []):
            if cand["parent"] != nm:
                continue
            if (nm, cand["child"]) in static_pairs:
                continue
            if cand["child"] not in by_norm:
                continue
            dst = by_norm[cand["child"]]["id"]
            static_pairs.add((nm, cand["child"]))
            edges_out.append({
                "id": f"observed:{n['id']}->{dst}", "kind": "CALLS", "src": n["id"], "dst": dst,
                "confidence": None, "truth_class": "OBSERVED", "plane": "OBSERVED",
                "authority": f"GET /api/v1/runtime/runs/{run_id} (observed call edges)",
                "observed": {"count": cand["count"], "run_id": cand["run_id"],
                             "child_binding": cand["child_binding"],
                             "parent_binding": cand["parent_binding"],
                             "child_symbol": cand["child_symbol"],
                             "plane": "OBSERVED", "truth_class": "OBSERVED"},
                "span": {"file": cand.get("child_file"), "start_line": None, "end_line": None,
                         "span_kind": "CALLSITE", "precision": "UNKNOWN",
                         "evidence": {"reason": "observed edge: callsite line not recorded"},
                         "text": f"{cand.get('child_file') or '?'} (callsite unknown)"},
            })

    counts = {"node": len(node_out), "edge": len(edges_out),
              "socket_static": sum(1 for n in node_out for s in n["sockets"] if s["plane"].startswith("STATIC")),
              "socket_observed": sum(1 for n in node_out for s in n["sockets"] if s["plane"].startswith("OBSERVED")),
              "observed_edge": sum(1 for e in edges_out if e["observed"]),
              # only real call relations count here: containment is a different
              # kind of fact and must not inflate the static-only number
              "static_only_edge": sum(1 for e in edges_out
                                      if e["kind"] == "CALLS" and not e["observed"]
                                      and e["plane"] == "STATIC")}
    notes = [
        "node positions/layout/frames are UI layout state, never architecture truth",
        "static CALLS from the repo graph; OBSERVED from the runtime trace of one run",
        "socket coverage is per node and may be PARTIAL (see socket_notes)",
    ]
    if not observed_data.get("objects"):
        notes.append(f"no runtime data artifacts for run {run_id} → observed sockets absent "
                     "(not zero)")
    return {
        "repo_id": repo_id, "snapshot": sid, "root": node, "depth": depth,
        "run_id": run_id, "app": app,
        "run": {"run_id": obs.get("run", {}).get("run_id"),
                "workload_id": obs.get("run", {}).get("workload_id"),
                "evidence_revision": obs.get("run", {}).get("evidence_revision"),
                "label": obs.get("run", {}).get("scenario_label")},
        "nodes": node_out, "edges": edges_out, "counts": counts, "notes": notes,
    }


@router.get("/agent-runs")
def agent_runs(stage: str | None = None) -> dict:
    """External-agent activity from the frozen agent-run artifacts.

    An agent's prose is a CLAIM; `tools_used` + the audit verdict are evidence.
    """
    items = []
    for d in sorted(TOURNAMENT.glob("*/agent_runs")):
        stage_name = d.parent.name
        if stage and stage_name != stage:
            continue
        metrics = {}
        mp = d / "metrics.json"
        if mp.exists():
            try:
                metrics = json.loads(mp.read_text())
            except Exception:
                metrics = {}
        audit = {}
        for cand in ("audit.json", "verdicts.json", "results.json"):
            p = d / cand
            if p.exists():
                try:
                    audit = json.loads(p.read_text())
                except Exception:
                    audit = {}
                break
        verdicts = {}
        if isinstance(audit, dict):
            for key in ("results", "verdicts"):
                v = audit.get(key)
                if isinstance(v, list):
                    for row in v:
                        if isinstance(row, dict) and row.get("task"):
                            verdicts[row["task"]] = row.get("verdict")
                elif isinstance(v, dict):
                    for k, row in v.items():
                        verdicts[k] = row.get("verdict") if isinstance(row, dict) else row
        for tag, m in metrics.items():
            items.append({
                "stage": stage_name, "task_id": tag,
                "provider": m.get("provider"), "model": m.get("model"),
                "tool_calls": m.get("tool_calls"), "turns": m.get("turns"),
                "tools_used": m.get("tools_used") or [],
                "grep_fallbacks": m.get("grep_fallbacks"),
                "verdict": verdicts.get(tag),
                "trace": str((d / f"{tag}_trace.json").relative_to(ROOT))
                         if (d / f"{tag}_trace.json").exists() else None,
                "truth_class": "OBSERVED",
                "authority": f"analysis_tournament/{stage_name}/agent_runs",
                "note": "agent answer = CLAIM; tool calls + audit verdict = evidence",
            })
        if not metrics:
            traces = sorted(p.name for p in d.glob("*_trace.json"))
            for t in traces:
                items.append({
                    "stage": stage_name, "task_id": t.replace("_trace.json", ""),
                    "provider": None, "model": None, "tool_calls": None, "turns": None,
                    "tools_used": [], "grep_fallbacks": None,
                    "verdict": verdicts.get(t.replace("_trace.json", "")),
                    "trace": str((d / t).relative_to(ROOT)),
                    "truth_class": "OBSERVED",
                    "authority": f"analysis_tournament/{stage_name}/agent_runs",
                    "note": "trace only (no metrics.json); agent answer = CLAIM; "
                            "tool calls + audit verdict = evidence",
                })
    return {"items": items, "total": len(items),
            "transport": "MCP (external agent host → rintel MCP server)",
            "host_support": {
                "external_agent_via_mcp": "AVAILABLE",
                "dsh_agent_host": "PLANNED", "pi_host": "PLANNED",
                "codex_host": "PLANNED",
                "note": "hosts other than a generic MCP client are not implemented",
            }}


@router.get("/ui-reality")
def ui_reality() -> dict:
    """The audited UI reality ledger + screen audit + task paths."""
    out: dict = {"dir": str(UI_DIR.relative_to(ROOT))}
    for name in ("reality_ledger", "screen_audit", "task_paths", "issue_register"):
        p = UI_DIR / f"{name}.json"
        out[name] = json.loads(p.read_text()) if p.exists() else None
    if out["reality_ledger"] is None:
        raise HTTPException(404, {"code": "ui_reality_missing",
                                  "message": "UI-REALITY-ALIGN0 artifacts not generated",
                                  "details": {"dir": str(UI_DIR)}})
    return out
