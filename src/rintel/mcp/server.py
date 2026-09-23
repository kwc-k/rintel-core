"""RINTEL-MCP0: minimal stdio MCP server over existing Rintel services.

Adapter only (spec §2): reuses the same domain/service layer as the FastAPI
routes; zero business logic copied; zero canonical mutation endpoints.
Transport: newline-delimited JSON-RPC (MCP stdio framing), tools/list +
tools/call + initialize + resources via read_resource.

Tools (v0, §4-§14):
  repo_status  search_symbols  get_symbol  query_topology  find_path
  explain_evidence  query_flow  create_design  design_patch
  validate_design  read_resource
"""
from __future__ import annotations

import difflib
import json
import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from .instructions import INSTRUCTIONS
from . import published as published_store

REPO = Path(__file__).resolve().parents[3]

LANES = ("fac_c", "jpl")
LANE_LABEL = {
    "fac_c": "FAC (C: sfac/scrm/stoken/spol)",
    "jpl": "JusticePlutus (Python)",
}
LANE_REPO = {"fac_c": "fac", "jpl": "jpl"}
LANE_ROOT = {
    "fac_c": "/Users/wu/Documents/dh/a3/fac/fac",
    "jpl": "/Users/wu/Documents/JusticePlutus",
}
RELATION_KINDS = ("CALL", "DATA", "STATE", "CONTROL", "TIME", "RESOURCE")
TOURNAMENT = REPO / "analysis_tournament"


def _evidence_revision() -> str:
    """published evidence revision of the derived projections (CROSS-SYNC0);
    'r0' = frozen baseline (no sync published yet)."""
    try:
        p = TOURNAMENT / "cross_projection_sync0" / "state.json"
        if p.exists():
            return str(json.loads(p.read_text()).get("revision_id") or "r0")
    except Exception:                        # noqa: BLE001
        pass
    return "r0"

try:
    from rintel.design_lifecycle import (
        AcceptanceCriterion, DesignLifecycleService, PlanChange,
    )
    from rintel.design_lifecycle.projection import workspace_projection
except Exception:  # pragma: no cover
    DesignLifecycleService = None
    workspace_projection = None

try:
    from rintel.topology_view.projector import build_lane_bundle, lane_list
except Exception:  # pragma: no cover
    build_lane_bundle = None
    lane_list = None


# ---------------------------------------------------------------------------
# store (the same product datastore resolution as the local UI)
# ---------------------------------------------------------------------------

def make_store():
    from rintel.server.deps import build_store
    return build_store()


_STORE = None


def store():
    global _STORE
    if _STORE is None:
        _STORE = make_store()
    return _STORE


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_bundle_cache: dict[str, dict] = {}


def _available_lanes() -> tuple[str, ...]:
    return tuple(row["lane"] for row in lane_list()) if lane_list else ()


def bundle(lane: str) -> dict:
    if lane not in _bundle_cache:
        _bundle_cache[lane] = build_lane_bundle(lane)
    return _bundle_cache[lane]


def _nodes(b: dict) -> list[dict]:
    return list(b.get("nodes") or [])


def _edges(b: dict) -> list[dict]:
    return list(b.get("edges") or [])


def _byname(b: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for n in _nodes(b):
        out[n["canonical_symbol_id"].split(":")[-1]] = n
    return out


def _capability_row(b: dict, lane: str) -> dict:
    meta = b.get("meta") or {}
    stats = meta.get("overlay_stats") or {}
    out = {"lane": lane, "capability": {}}
    for k in RELATION_KINDS:
        n = stats.get(k, 0)
        if k == "DATA":
            out["capability"][k] = f"PARTIAL (frozen: {meta.get('data_capability','UNKNOWN')})"
        elif n == 0:
            out["capability"][k] = "UNKNOWN (no facts produced)"
        else:
            out["capability"][k] = f"PARTIAL ({n} facts; unresolved targets present)"
    out["flow_coverage"] = _flow_coverage(lane)
    return out


def _flow_coverage(lane: str) -> str:
    p = TOURNAMENT / "flow_infer" / "fac_unit_summary.json"
    if lane == "fac_c" and p.exists():
        d = json.loads(p.read_text())
        return "PARTIAL for fac/crm/spol (faclib outside frozen lane)" if d else "UNKNOWN"
    if lane == "jpl":
        return "N/A (FLOW-INFER0 covers FAC only)"
    return "N/A"


def _artifact(prefix: str) -> dict | None:
    p = TOURNAMENT / "flow_infer" / f"fac_{prefix}.json"
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------------------
# DATA-INTERFACE0 derived objects (adapter-only: MCP exposes what the
# data_interface stage generated; never re-derives, never infers shapes)
# ---------------------------------------------------------------------------

DI_DIR = TOURNAMENT / "data_interface"


def _di(name: str) -> list | dict | None:
    p = DI_DIR / f"fac_{name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _di_ports() -> list[dict]:
    return _di("shapes") or []


def _di_bindings() -> list[dict]:
    return _di("bindings") or []


def _di_transfers() -> list[dict]:
    return _di("transfers") or []


def _di_capability() -> dict:
    cap = _di("capability") or {}
    return cap.get("_per_lane", {})


def _di_by_fn() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in _di_ports():
        out.setdefault(p.get("fn") or "", []).append(p)
    return out


def _di_modules() -> dict:
    return _di("module_interfaces") or {}


def _di_flow_intf() -> dict:
    return _di("flow_interfaces") or {}


# ---- FLOW-SEMANTIC0 derived annotation plane (never evidence) --------------

def _fs(name: str) -> list | dict | None:
    p = TOURNAMENT / "flow_semantic0" / name
    return json.loads(p.read_text()) if p.exists() else None


def _fs_stages() -> list[dict]:
    return _fs("fac_semantic_stages.json") or []


def _fs_memberships() -> list[dict]:
    return _fs("fac_memberships.json") or []


def _fs_edges() -> list[dict]:
    return _fs("fac_semantic_edges.json") or []


def _stage_compact(st: dict) -> dict:
    return {"semantic_stage_id": st.get("semantic_stage_id"), "display_name": st.get("display_name"),
            "stage_kind": st.get("stage_kind"), "status": st.get("status"),
            "origin": st.get("origin"), "truth_class": st.get("truth_class"),
            "coverage": st.get("coverage"),
            "member_count": len((st.get("members") or {}).get("flow_region_ids", []))
                           + len((st.get("members") or {}).get("canonical_symbol_ids", [])),
            "resource_uri": f"rintel://semantic-stage/{st.get('semantic_stage_id')}"}


def _port_record(p: dict) -> dict:
    """full port record: fields + URIs (never drops shape fields)."""
    return {
        "port_id": p.get("port_id"), "name": p.get("name"),
        "direction": p.get("direction"), "semantic_type": p.get("semantic_type"),
        "source_type": p.get("source_type"), "dtype": p.get("dtype"),
        "rank": p.get("rank"), "shape": p.get("shape"),
        "shape_status": p.get("shape_status"), "element_count": p.get("element_count"),
        "byte_size": p.get("byte_size"),
        "byte_size_expression": p.get("byte_size_expr"),
        "truth_class": p.get("truth_class"), "coverage": p.get("coverage"),
        "evidence": [{"kind": e.get("kind"), "expr": e.get("expr"), "line": e.get("line")}
                     for e in (p.get("evidence") or [])],
        "function": p.get("fn"), "file": p.get("file"),
        "resource_uri": f"rintel://port/{p.get('port_id')}",
    }


def _ports_summary(ports: list[dict]) -> dict:
    s = {"total": len(ports), "inputs": 0, "outputs": 0, "inouts": 0, "direction_unknown": 0,
         "scalars": 0, "vectors": 0, "matrices": 0, "rank_unknown": 0,
         "symbolic": 0, "partial": 0, "resolved": 0, "shape_unknown": 0,
         "known_byte_sizes": 0}
    for p in ports:
        d = p.get("direction")
        if d == "INPUT":
            s["inputs"] += 1
        elif d == "OUTPUT":
            s["outputs"] += 1
        elif d == "INOUT":
            s["inouts"] += 1
        else:
            s["direction_unknown"] += 1
        r = p.get("rank")
        if r is None:
            s["rank_unknown"] += 1
        elif r == 0:
            s["scalars"] += 1
        elif r == 1:
            s["vectors"] += 1
        elif r >= 2:
            s["matrices"] += 1
        st = p.get("shape_status")
        if st == "RESOLVED":
            s["resolved"] += 1
        elif st == "SYMBOLIC":
            s["symbolic"] += 1
        elif st == "PARTIAL":
            s["partial"] += 1
        else:
            s["shape_unknown"] += 1
        if p.get("byte_size") is not None:
            s["known_byte_sizes"] += 1
    return s


def _ports_summary_text(s: dict) -> str:
    return (f"{s['inputs']} input(s), {s['outputs']} output(s), {s['inouts']} inout(s), "
            f"{s['direction_unknown']} direction-unknown; {s['scalars']} scalar(s), "
            f"{s['vectors']} vector(s), {s['matrices']} matrix/matrices, "
            f"{s['rank_unknown']} rank-unknown; shape: {s['resolved']} resolved / "
            f"{s['symbolic']} symbolic / {s['partial']} partial / {s['shape_unknown']} unknown; "
            f"{s['known_byte_sizes']} resolved byte size(s)")


def _boundary_record(ps: list[dict]) -> dict:
    """aggregated module/file boundary — only what DATA-INTERFACE0 generated."""
    by: dict[str, list[str]] = {}
    summ: dict[str, int] = {}
    for p in ps:
        d = p.get("direction") or "UNKNOWN"
        summ[d] = summ.get(d, 0) + 1
        by.setdefault(d, []).append(p.get("name", ""))
    return {
        "port_count": len(ps), "by_direction": summ,
        "ports": [{"name": p.get("name"), "direction": p.get("direction"),
                   "dtype": p.get("dtype"), "rank": p.get("rank"),
                   "shape": p.get("shape"), "shape_status": p.get("shape_status"),
                   "byte_size": p.get("byte_size"),
                   "byte_size_expression": p.get("byte_size_expr"),
                   "origin_function": p.get("fn")} for p in ps],
        "note": ("projected boundary (DATA-INTERFACE0 aggregation); the underlying "
                 "function ports remain the witnesses"),
    }


def _bindings_between(src_name: str, tgt_name: str) -> list[dict]:
    out = []
    for b in _di_bindings():
        if b.get("caller_symbol") != src_name or b.get("callee_symbol") != tgt_name:
            continue
        ev = (b.get("evidence") or [{}])[0] if b.get("evidence") else {}
        out.append({
            "binding_id": b["binding_id"], "source_function": b["caller_symbol"],
            "source_actual": b.get("caller_expr"), "target_function": b["callee_symbol"],
            "target_port": b.get("callee_port"), "kind": b.get("kind", "POSITIONAL"),
            "callsite_line": b.get("callsite_line"), "truth_class": b.get("truth_class"),
            "coverage": b.get("coverage"),
            "witness": {"kind": ev.get("kind"), "expr": ev.get("expr"),
                        "line": ev.get("line"), "binding_id": b["binding_id"]},
            "resource_uri": f"rintel://binding/{b['binding_id']}",
        })
    return out


# ---------------------------------------------------------------------------
# tool implementations  (each: (params) -> compact dict, summary-first §20)
# ---------------------------------------------------------------------------

def t_repo_status(p: dict) -> dict:
    if p.get("repo") and not store().repo(p["repo"]):
        return {"summary": f"REPO_NOT_FOUND: '{p['repo']}' is not registered",
                "error": {"code": "REPO_NOT_FOUND", "requested": p["repo"]}}
    out = {"repos": []}
    for lane in (() if p.get("repo") else _available_lanes()):
        li = next((x for x in lane_list() if x["lane"] == lane), None)
        if li is None:
            continue
        b = bundle(lane)
        meta = b.get("meta") or {}
        out["repos"].append({
            "lane": lane,
            "repo_id": meta.get("source_repo_id") or LANE_REPO.get(lane),
            "label": li.get("label") or LANE_LABEL.get(lane),
            "languages": meta.get("languages") or li.get("languages"),
            "snapshot_id": meta.get("snapshot_id"),
            "views": ["flow" if lane == "fac_c" else None, "module", "file", "function"],
            "capability": _capability_row(b, lane),
            "data_interface_capability": _di_capability_row(lane),
            "fac_library_coverage": _faclib_coverage_row(lane),
            "known_limitations": meta.get("capability_notes") or [],
            "counts": {"symbols": len(_nodes(b)), "edges": len(_edges(b))},
        })
    out["repos"].extend(published_store.repo_status(store(), p.get("repo")))
    return {"summary": f"{len(out['repos'])} published repositories/available lanes.",
            "repos": out["repos"], "next": ["search_symbols", "query_topology", "query_flow"]}


def _kernel_light() -> dict:
    """Kernel index (light): symbols_light + calls_light — O(1) lookups for
    the coverage-expanded universe (never loads the 160MB+ bundle)."""
    try:
        from rintel import kernel as _krn
        p = _krn.KERNEL_OUT / "fac_kernel_index.json"
        return json.loads(p.read_text())
    except (FileNotFoundError, OSError):
        return {}


def _member_span(name: str) -> dict | None:
    """§20: definition SourceSpan for a topology member (None if unknown)."""
    kv = _kernel_view(name, limit=1)
    if not kv or not kv.get("candidates"):
        return None
    return kv["candidates"][0].get("span")


def _dgesv_span() -> dict | None:
    """§8: canonical DGESV callsite span (macro invocation, caller side)."""
    try:
        from rintel.source_span import dgesv_callsite_span
        return dgesv_callsite_span("/Users/wu/Documents/dh/a3/fac/fac")
    except Exception:
        return None


def _kernel_view(name: str, limit: int = 10) -> dict | None:
    """Kernel-universe symbol candidates (FAC-LIBRARY-COVERAGE0): definition
    file + linkage evidence per candidate; None when absent from the kernel."""
    idx = _kernel_light()
    cands = idx.get("symbols_light", {}).get(name)
    if not cands:
        return None
    rows = [{"file": c.get("file"), "kind": c.get("kind"), "role": c.get("role"),
             "storage": c.get("storage"), "line": c.get("line"),
             "definition": c.get("definition"), "id": c.get("id"),
             # SOURCE-SPAN-COL0 §17/§20: the canonical span travels with it
             "span": c.get("span")}
            for c in cands[:limit]]
    return {"count": len(cands), "candidates": rows}


def _kernel_callees(name: str, limit: int = 40) -> list[dict]:
    """Kernel CALL edges from `name` (resolved callees with canonical spans)."""
    idx = _kernel_light()
    out = []
    for e in idx.get("calls_light", {}).get(name, [])[:limit]:
        row = dict(e)
        # §20: callee definition span + the callsite span of this call
        if not row.get("span"):
            row["span"] = _source_span(row.get("file"), row.get("line"),
                                       name=row.get("callee"), kind="CALLSITE")
        out.append(row)
    return out


def _faclib_coverage_row(lane: str) -> dict:
    """§22: fac_c coverage = command layer + faclib (kernel universe), honest
    exclusions.  Read from the published kernel summary + coverage artifacts;
    never fabricates numbers when artifacts are missing."""
    if lane != "fac_c":
        return {"scope": "not_covered"}
    import json as _json
    from rintel import kernel as _krn
    p = _krn.KERNEL_OUT / "fac_kernel_summary.json"
    try:
        s = _json.loads(p.read_text())
        univ = s.get("universe", {})
        return {
            "scope": "command_layer + faclib + bound BLAS/LAPACK",
            "files_covered": len(s.get("files", [])),
            "files": {"command_layer": len(univ.get("command_layer", [])),
                      "faclib_c": len(univ.get("faclib_c", [])),
                      "faclib_headers": len(univ.get("faclib_headers", [])),
                      "fortran_external": len(univ.get("fortran_external", []))},
            "symbols_covered": {"functions": s.get("counts", {}).get("functions"),
                                "data_symbols": s.get("counts", {}).get("data_symbols"),
                                "references": s.get("counts", {}).get("references")},
            "macro_linkage_bindings": s.get("macro_bindings_count", 0),
            "known_exclusions": univ.get("exclusions", {}),
            "coverage_status": "PARTIAL (static direct calls EXACT; "
                               "function-pointer registry targets and f2c wrappers remain UNKNOWN)",
        }
    except (FileNotFoundError, OSError):
        return {"scope": "fac_c", "coverage_status": "artifacts not generated",
                "files_covered": 0}


def _di_capability_row(lane: str) -> dict:
    """per-language capability from the DATA-INTERFACE0 artifact (never upgrades
    PARTIAL; C pointer / frozen-lane limits stay visible — §2)."""
    cap = _di_capability().get("fac", {})
    if lane != "fac_c":
        return {"scope": "not_covered",
                "data_interface": "N/A (DATA-INTERFACE0 parse set covers FAC only)"}
    langs = ("fortran", "c")
    out = {"scope": "FAC", "parsed": {"functions": len(_di_by_fn()),
                                      "ports": len(_di_ports()),
                                      "bindings": len(_di_bindings())}}
    for lang in langs:
        row = {}
        for k in ("PORT_DIRECTION", "DTYPE", "RANK", "SHAPE", "BYTE_SIZE", "PORT_BINDING"):
            v = cap.get(k)
            if isinstance(v, dict):
                row[k] = v.get(lang)
            elif v:
                row[k] = v
        out[lang] = row or cap
    out["DATA_INTERFACE"] = cap.get("DATA_INTERFACE", "PARTIAL")
    out["note"] = ("FAC fixed-form Fortran has no intent(); directions come from LAPACK doc "
                   "comments (evidence class=COMMENT). C pointers keep rank/shape UNKNOWN; "
                   "PARTIAL/UNKNOWN are truth states, not failures.")
    return out


def _search_in_lane(lane: str, q: str, kind: str | None, file: str | None,
                    language: str | None, limit: int) -> list[dict]:
    b = bundle(lane)
    ql = q.lower()
    rows = []
    for n in _nodes(b):
        name = n["canonical_symbol_id"].split(":")[-1]
        if ql not in name.lower():
            continue
        if kind and kind.lower() not in name.lower():
            continue
        f = n.get("file") or ""
        if file and file not in f:
            continue
        if language and (n.get("language") or "").lower() != language.lower():
            continue
        rows.append({
            "canonical_id": n["canonical_symbol_id"],
            "name": name, "kind": "function/symbol", "file": f,
            "line": (n.get("source_range") or {}).get("start_line"),
            "language": n.get("language"),
            "binding": n.get("binding_status"),
        })
        if len(rows) >= limit:
            break
    return rows


def t_search_symbols(p: dict) -> dict:
    if p.get("repo_id") and not store().repo(p["repo_id"]):
        return {"summary": f"REPO_NOT_FOUND: '{p['repo_id']}' is not registered",
                "error": {"code": "REPO_NOT_FOUND", "requested": p["repo_id"]}}
    if p.get("repo_id") and store().current_snapshot(p["repo_id"]) is None:
        return {"summary": f"REPO_NOT_INDEXED: '{p['repo_id']}' has no published snapshot",
                "error": {"code": "REPO_NOT_INDEXED", "requested": p["repo_id"]}}
    q = str(p.get("query", ""))
    if not q and not p.get("file"):
        return {"summary": "MISSING_REQUIRED_ARGUMENT: one of query/file is required",
                "error": {"code": "MISSING_REQUIRED_ARGUMENT",
                          "message": "one of query/file is required",
                          "required": ["query", "file"],
                          "hint": "pass query=<name substring> (or file=<path> to list "
                                  "one file's symbols)"}}
    if not q and p.get("file"):
        # file.list_symbols semantics: list symbols of one file (long-file scout)
        rows = []
        for lane in (() if p.get("repo_id") else _available_lanes()):
            for n in _nodes(bundle(lane)):
                f = str(n.get("file") or "")
                if f.endswith(str(p["file"])) or f == str(p["file"]):
                    rows.append({"lane": lane, "canonical_id": n["canonical_symbol_id"],
                                 "name": n["canonical_symbol_id"].split(":")[-1],
                                 "kind": "function/subroutine/program",
                                 "file": f, "language": n.get("language"),
                                 "binding": n.get("binding_status")})
        rows.extend(published_store.search_symbols(store(), p))
        rows = rows[: int(p.get("limit", 120))]
        return {"status": "ok", "count": len(rows),
                "summary": f"{len(rows)} symbol(s) in {p['file']} (file→symbol index; no source body read)",
                "matches": rows, "next": ["get_symbol", "read_resource"]}
    limit = int(p.get("limit", 12))
    kind = p.get("kind")
    file = p.get("file")
    language = p.get("language")
    ql = q.lower()
    rows: list[dict] = []
    for lane in (() if p.get("repo_id") else _available_lanes()):
        rows.extend(_search_in_lane(lane, q, kind, file, language, max(2, limit - len(rows))))
        if len(rows) >= limit:
            break
    src_counts = {"frozen_lane": len(rows)}
    published = published_store.search_symbols(store(), p)
    rows.extend(published[:max(0, limit - len(rows))])
    src_counts["published_datastore"] = len(published)
    if len(rows) < limit and not p.get("repo_id"):
        # FAC-LIBRARY-COVERAGE0: frozen lanes cover only the command layer.
        # Also search the DATA-INTERFACE0 parse set and the kernel universe so
        # a 0-match result never implies "symbol does not exist" (§21/§22).
        seen_names = {r["name"] for r in rows}
        by_fn = _di_by_fn()
        for name in sorted(by_fn.keys()):
            if ql not in name.lower() or name in seen_names:
                continue
            if kind and kind.lower() not in name.lower():
                continue
            p0 = by_fn[name][0]
            f = p0.get("file") or ""
            if file and file not in f:
                continue
            lang = "fortran" if f.endswith(".f") else "c"
            if language and language.lower() != lang:
                continue
            rows.append({
                "lane": "fac_data_interface",
                "canonical_id": f"function:{name}", "name": name,
                "kind": "subroutine/function", "file": f,
                "line": p0.get("line"),
                "language": lang,
                "binding": "DATA-INTERFACE0 parse set",
            })
            seen_names.add(name)
            if len(rows) >= limit:
                break
        src_counts["fac_data_interface"] = sum(
            1 for r in rows if r.get("lane") == "fac_data_interface")
        idx = _kernel_light()
        sl = idx.get("symbols_light") or {}
        if len(rows) < limit:
            for name, entries in sl.items():
                if ql not in name.lower() or name in seen_names:
                    continue
                if kind and kind.lower() not in name.lower():
                    continue
                defs = [e for e in entries if e.get("definition")]
                e0 = defs[0] if defs else entries[0]
                defs_callable = [e for e in entries if e.get("kind") == "CALLABLE"]
                ec = defs_callable[0] if defs_callable else e0
                f = ec.get("file") or ""
                if file and file not in f:
                    continue
                lang = "fortran" if (ec.get("lang") or "").startswith("FORTRAN") else "c"
                if language and language.lower() != lang:
                    continue
                rows.append({
                    "lane": "fac_kernel",
                    "canonical_id": ("function:" + name if ec.get("kind") == "CALLABLE"
                                     else "symbol:" + name),
                    "name": name,
                    "kind": (ec.get("kind") or "symbol").lower(),
                    "file": f, "line": ec.get("line"),
                    "language": lang,
                    "binding": "kernel universe (faclib coverage)",
                    "definition": bool(ec.get("definition")),
                })
                seen_names.add(name)
                if len(rows) >= limit:
                    break
        src_counts["fac_kernel"] = sum(1 for r in rows if r.get("lane") == "fac_kernel")
    parts = "; ".join(f"{k}:{v}" for k, v in src_counts.items() if v)
    return {
        "status": "ok", "count": len(rows),
        "summary": f"{len(rows)} symbol(s) matched ({parts}); use canonical_id for "
                   f"get_symbol/query_topology.",
        "matches": rows[:limit],
        "sources": {k: v for k, v in src_counts.items() if v},
        "next": ["get_symbol", "query_topology", "explain_evidence"],
    }


def t_get_symbol(p: dict) -> dict:
    cid = str(p.get("canonical_id", ""))
    if not cid.strip():
        return {"summary": "MISSING_REQUIRED_ARGUMENT: canonical_id is required",
                "error": {"code": "MISSING_REQUIRED_ARGUMENT",
                          "message": "parameter 'canonical_id' is required",
                          "parameter": "canonical_id", "required": ["canonical_id"],
                           "hint": "discover the symbol first via search_symbols(query=...)"}}
    if p.get("repo_id") and not store().repo(p["repo_id"]):
        return {"summary": f"REPO_NOT_FOUND: '{p['repo_id']}' is not registered",
                "error": {"code": "REPO_NOT_FOUND", "requested": p["repo_id"]}}
    if p.get("repo_id") and store().current_snapshot(p["repo_id"]) is None:
        return {"summary": f"REPO_NOT_INDEXED: '{p['repo_id']}' has no published snapshot",
                "error": {"code": "REPO_NOT_INDEXED", "requested": p["repo_id"]}}
    published = published_store.get_symbol(store(), cid, p.get("repo_id"))
    if published is not None:
        return published
    if p.get("repo_id"):
        return {"summary": f"SYMBOL_NOT_FOUND: '{cid}' not in repository {p['repo_id']}",
                "error": {"code": "SYMBOL_NOT_FOUND", "requested": cid,
                          "hint": "use search_symbols(query=..., repo_id=...)"}}
    name = cid.split(":")[-1]
    with_ports = bool(p.get("include_ports"))
    hits = []
    for lane in _available_lanes():
        b = bundle(lane)
        n = _byname(b).get(name)
        if not n:
            continue
        calls_out = [e for e in _edges(b)
                     if e.get("kind") == "CALL" and e["source"] == n["canonical_symbol_id"]]
        calls_in = [e for e in _edges(b)
                    if e.get("kind") == "CALL" and e["target"] == n["canonical_symbol_id"]]
        hit = {
            "lane": lane,
            "identity": {"canonical_id": n["canonical_symbol_id"], "name": name,
                         "kind": "function/symbol", "language": n.get("language"),
                         "file": n.get("file"),
                         "span": n.get("source_range"),
                         "binding": n.get("binding_status")},
            "parent": {"module": (n.get("file") or "").split("/")[0], "file": n.get("file")},
            "children": [],
            "topology_summary": {
                "calls_out": len(calls_out), "calls_in": len(calls_in),
                "resources": n.get("resources") or [],
                "control_landmarks": (n.get("control_landmarks") or [])[:3],
            },
            "flow_memberships": _flow_members_of(name),
            "kernel_universe": _kernel_view(name),
            "kernel_calls": _kernel_callees(name, limit=40),
            "source_uri": f"rintel://symbol/{name}/source",
        }
        # DATA-INTERFACE0 ports (§3/§4: compact default, full when requested)
        ports = _di_by_fn().get(name)
        if ports:
            summ = _ports_summary(ports)
            hit["data_interface"] = {
                "ports_summary": summ,
                "ports_summary_text": _ports_summary_text(summ),
                "resource_uri": f"rintel://data-interface/symbol/{name}",
            }
            if with_ports:
                hit["data_interface"]["ports"] = [_port_record(p) for p in ports]
            pf = {p.get("file") for p in ports} - {None}
            lane_file = n.get("file") or ""
            if pf and lane_file and len(pf) == 1 and next(iter(pf)) != lane_file.split("/")[-1]:
                hit["data_interface"]["note"] = (
                    f"ports derive from the DATA-INTERFACE0 parse-set copy in '{next(iter(pf))}' — "
                    f"same bare name as this lane hit '{lane_file}' in {lane}; do NOT conflate files")
        else:
            hit["data_interface"] = {"ports_summary": None,
                                     "note": "symbol not in the DATA-INTERFACE0 parse set (no port evidence)"}
        hits.append(hit)
    if not hits:
        # DATA-INTERFACE0 parse-set fallback: DSBEV/DGER/… are real FAC
        # subroutines but sit outside the frozen fac_c topology lane
        ports = _di_by_fn().get(name)
        if ports:
            summ = _ports_summary(ports)
            f0 = ports[0]
            hit = {
                "lane": "fac_data_interface",
                "identity": {"canonical_id": f"function:{name}", "name": name,
                             "kind": "subroutine/function",
                             "language": "fortran" if (f0.get("file") or "").endswith(".f") else "c",
                             "file": f0.get("file"), "span": None,
                             "binding": "DATA-INTERFACE0 parse set"},
                "parent": {"module": (f0.get("file") or "").split("/")[0],
                           "file": f0.get("file")},
                "children": [],
                "topology_summary": {"calls_out": None, "calls_in": None,
                                     "resources": [], "control_landmarks": [],
                                     "note": "outside frozen fac_c topology; port-level interface from DATA-INTERFACE0"},
                "flow_memberships": _flow_members_of(name),
                "kernel_universe": _kernel_view(name),
                "kernel_calls": _kernel_callees(name, limit=40),
                "source_uri": f"rintel://symbol/{name}/source",
                "data_interface": {
                    "ports_summary": summ,
                    "ports_summary_text": _ports_summary_text(summ),
                    "resource_uri": f"rintel://data-interface/symbol/{name}",
                },
            }
            if with_ports:
                hit["data_interface"]["ports"] = [_port_record(p) for p in ports]
            hits.append(hit)
    if not hits:
        # kernel-universe fallback (FAC-LIBRARY-COVERAGE0): the symbol may be
        # a faclib function outside the frozen command-layer lane
        kv = _kernel_view(name)
        if kv:
            defs = [c for c in kv["candidates"] if c.get("definition")]
            f0 = defs[0] if defs else kv["candidates"][0]
            hit = {
                "lane": "fac_kernel",
                "identity": {"canonical_id": f"function:{name}", "name": name,
                             "kind": "callable", "language": "c/fortran",
                             "file": f0.get("file"), "span": None,
                             "binding": "kernel universe (faclib coverage)"},
                "parent": {"module": (f0.get("file") or "").split("/")[0],
                           "file": f0.get("file")},
                "children": [],
                "topology_summary": {"calls_out": len(_kernel_callees(name)),
                                     "calls_in": None, "resources": [],
                                     "control_landmarks": [],
                                     "note": "kernel-derived call graph (coverage expansion)"},
                "flow_memberships": _flow_members_of(name),
                "kernel_universe": kv,
                "kernel_calls": _kernel_callees(name, limit=40),
                "source_uri": f"rintel://symbol/{name}/source",
                "data_interface": {"ports_summary": None,
                                   "note": "symbol not in the DATA-INTERFACE0 parse set (no port evidence)"},
            }
            hits.append(hit)
    if not hits:
        cands = _name_candidates(name)
        kernel_hit = any(c.lower() == name.lower() for c in cands)
        hint = ("the name resolves in the kernel universe (faclib coverage) — pass "
                f"canonical_id='function:{name}' to get its definition") if kernel_hit else (
                "symbol not in frozen lanes / DATA-INTERFACE0 / kernel universe — use "
                "search_symbols(query=...) with a different spelling or file filter")
        return {"summary": f"SYMBOL_NOT_FOUND: '{name}' not found (frozen lanes / "
                           f"DATA-INTERFACE0 / kernel universe)", "hits": [],
                "error": {"code": "SYMBOL_NOT_FOUND", "requested": name,
                          "candidates": cands, "hint": hint}}
    return {"summary": f"{len(hits)} lane hit(s) for '{name}'; drill via source_uri / explain_evidence. "
                       f"{hits[0]['data_interface']['ports_summary_text'] if hits[0]['data_interface'].get('ports_summary_text') else 'no port data'}",
            "hits": hits,
            "next": ["query_topology", "explain_evidence", "read_resource", "query_data_interface"]}


def _flow_members_of(name: str) -> list[dict]:
    m = _artifact("fac_membership")
    if not m:
        return []
    return [{"scenario_ids": x["scenario_ids"], "kind": x["kind"]}
            for x in m.get("memberships", []) if x.get("symbol_name") == name][:5]


def t_query_topology(p: dict) -> dict:
    if p.get("repo_id"):
        if p.get("lane"):
            return {"summary": "AMBIGUOUS_ARGUMENTS: choose repo_id or lane",
                    "error": {"code": "AMBIGUOUS_ARGUMENTS", "required": ["repo_id", "lane"]}}
        return published_store.topology(store(), p)
    lane = str(p.get("lane", "fac_c"))
    if lane not in _available_lanes():
        return {"summary": f"lane {lane} has no published topology artifact",
                "error": {"code": "LANE_UNAVAILABLE", "requested": lane,
                          "hint": "use repo_status to discover published repositories, then pass repo_id"}}
    root = str(p.get("root", ""))
    relations = p.get("relations") or ["CALL"]
    direction = str(p.get("direction", "both"))
    depth = int(p.get("depth", 1))
    limit = int(p.get("limit", 60))
    want_di = bool(p.get("include_data_interfaces")) or "DATA" in relations
    b = bundle(lane)
    name = root.split(":")[-1]
    matched = _byname(b).get(name)
    nodes_seen: dict[str, dict] = {}
    edges: list[dict] = []

    def add_edge(e: dict, kind: str):
        src = e["source"].split(":")[-1]
        tgt = e["target"].split(":")[-1]
        unk = tgt == "[Unknown Dynamic Target]"
        edge = {
            "edge_id": f"{kind}:{src}->{tgt}",
            "source": src, "target": tgt if not unk else "[Unknown Dynamic Target]",
            "kind": kind,
            "truth_class": e.get("truth_class"),
            "execution_modality": e.get("execution_modality"),
            "target_resolution": e.get("target_resolution"),
            "coverage": e.get("coverage"),
            "witness_fact_ids": [(w.get("fact_id") if isinstance(w, dict) else None)
                                 for w in (e.get("representative_witnesses") or [])][:2],
            "unknown_target": unk,
        }
        if want_di:
            # §5-§6: edge-level port info from real PortBindings; never bind by name
            bindings = _bindings_between(src, tgt) if not unk else []
            if bindings:
                edge["port_bindings"] = bindings
                edge["port_level"] = True
            else:
                edge["port_bindings"] = []
                edge["port_level"] = False
                edge["source_port"] = "UNKNOWN"
                edge["target_port"] = "UNKNOWN"
                edge["note"] = ("no PortBinding for this DATA edge in the DATA-INTERFACE0 parse set — "
                                "ports NOT inferred by name")
        edges.append(edge)
        for cid in (src, tgt.replace("[Unknown Dynamic Target]", "__UNKNOWN__")):
            if cid not in nodes_seen and cid != "__UNKNOWN__":
                node = _byname(b).get(cid)
                if node:
                    nodes_seen[cid] = {"canonical_id": node["canonical_symbol_id"], "name": cid,
                                       "file": node.get("file")}
            elif cid == "__UNKNOWN__":
                nodes_seen["__UNKNOWN__"] = {"canonical_id": None, "name": "[Unknown Dynamic Target]",
                                             "file": None, "unknown": True}

    if not matched:
        # module / file scope aggregate boundary (works for key words too)
        scope = name.lower()
        members = [n for n in _nodes(b) if (n.get("file") or "").lower().startswith(scope) or
                   scope in (n.get("file") or "").lower()]
        if not members:
            cands = _name_candidates(name)
            kernel_hit = any(c.lower() == name.lower() for c in cands)
            hint = ("the name resolves outside the frozen lane (kernel universe / "
                    "DATA-INTERFACE0 parse set) — pass canonical_id to get_symbol "
                    f"(e.g. canonical_id='function:{name}')") if kernel_hit else (
                    "use search_symbols(query=...) to discover the exact name; unknown lane "
                    "symbols may resolve via get_symbol(canonical_id=...)")
            return {"summary": f"SYMBOL_NOT_FOUND: '{name}' not found as symbol/module/file "
                               f"scope in lane {lane}", "nodes": [], "edges": [],
                    "capability": _capability_row(b, lane),
                    "error": {"code": "SYMBOL_NOT_FOUND", "requested": name,
                              "candidates": cands, "hint": hint}}
        member_ids = {m["canonical_symbol_id"] for m in members}
        for e in _edges(b):
            if e.get("kind") not in relations:
                continue
            src = e["source"].split(":")[-1]
            tgt = e["target"].split(":")[-1]
            inside_src = e["source"] in member_ids
            inside_tgt = e["target"] in member_ids and tgt != "[Unknown Dynamic Target]"
            if (direction in ("out", "both") and inside_src) or \
               (direction in ("in", "both") and inside_tgt):
                if not (direction == "both" and inside_src and inside_tgt):
                    add_edge(e, e["kind"])
        out = {"summary": f"module/file scope '{name}': {len(member_ids)} members, "
                          f"{len(edges)} boundary edges (limit {limit})",
               "members": [{"canonical_id": m["canonical_symbol_id"], "name": m["name"],
                            "file": m.get("file"),
                            # §20: canonical span when the kernel universe knows
                            # this symbol (null = not resolvable, never guessed)
                            "source_span": _member_span(m["name"])}
                           for m in members[:limit]],
               "nodes": list(nodes_seen.values())[:limit],
               "edges": edges[:limit],
               "unknown_endpoints": sum(1 for e in edges if e.get("unknown_target")),
               "capability": _capability_row(b, lane),
               "next": ["explain_evidence", "get_symbol"]}
        if want_di:
            # §13: boundary interfaces — use the real DATA-INTERFACE0 aggregation
            # (never re-aggregate in the MCP)
            bmods = _di_modules()
            intf = {}
            for mod, ps in _di_modules().items():
                if scope in mod.lower() or scope in mod.lower() or mod.lower().endswith(scope):
                    intf[mod] = _boundary_record(ps)
            if scope in bmods:
                intf[scope] = _boundary_record(bmods[scope])
            out["boundary_interfaces"] = intf or {
                "note": "no DATA-INTERFACE0 module aggregation for this scope"}
        return out
    # scan by relation kind + direction + depth (bounded)
    for e in _edges(b):
        if e.get("kind") not in relations:
            continue
        src = e["source"].split(":")[-1]
        tgt = e["target"].split(":")[-1]
        if tgt == "[Unknown Dynamic Target]":
            if direction == "out" and src == name:
                add_edge(e, e["kind"])
            continue
        if depth <= 1:
            if (direction in ("out", "both") and src == name) or \
               (direction in ("in", "both") and tgt == name):
                add_edge(e, e["kind"])
        elif depth == 2:
            if direction in ("out", "both") and src == name:
                add_edge(e, e["kind"])
                for e2 in _edges(b):
                    if e2.get("kind") in relations and e2["source"] == e["target"]:
                        add_edge(e2, e2["kind"])
            if direction in ("in", "both") and tgt == name:
                add_edge(e, e["kind"])
                for e2 in _edges(b):
                    if e2.get("kind") in relations and e2["target"] == e["source"]:
                        add_edge(e2, e2["kind"])
        if len(edges) >= limit:
            break
    return {
        "summary": f"depth={depth} relations={','.join(relations)} → {len(nodes_seen)} nodes, {len(edges)} edges (limit {limit})",
        "root": name,
        "kernel_calls": _kernel_callees(name),
        "kernel_universe": _kernel_view(name),
        "nodes": list(nodes_seen.values())[:60],
        "edges": edges[:limit],
        "unknown_endpoints": sum(1 for e in edges if e.get("unknown_target")),
        "external_endpoints": 0,
        "capability": _capability_row(b, lane),
        "next": ["explain_evidence", "read_resource"],
    }


def t_find_path(p: dict) -> dict:
    if p.get("repo_id"):
        if p.get("lane"):
            return {"summary": "AMBIGUOUS_ARGUMENTS: choose repo_id or lane",
                    "error": {"code": "AMBIGUOUS_ARGUMENTS", "required": ["repo_id", "lane"]}}
        return published_store.find_path(store(), p)
    lane = str(p.get("lane", "fac_c"))
    if lane not in _available_lanes():
        return {"summary": f"lane {lane} has no published topology artifact",
                "error": {"code": "LANE_UNAVAILABLE", "requested": lane,
                          "hint": "use repo_status to discover available lanes"}}
    src = str(p.get("source", "")).split(":")[-1]
    tgt = str(p.get("target", "")).split(":")[-1]
    relations = p.get("relations") or ["CALL"]
    max_hops = int(p.get("max_hops", 6))
    b = bundle(lane)
    adj: dict[str, list[dict]] = defaultdict(list)
    for e in _edges(b):
        if e.get("kind") not in relations:
            continue
        s = e["source"].split(":")[-1]
        t = e["target"].split(":")[-1]
        if t == "[Unknown Dynamic Target]":
            t = "__UNKNOWN__"
        adj[s].append({"to": t, "edge": e, "unknown": t == "__UNKNOWN__"})
    # BFS with unresolved hops recorded as gaps (never treated as absence)
    q = deque([(src, [])])
    seen: dict[str, int] = {src: 0}
    found: list[dict] = []
    partial: list[dict] = []
    while q:
        cur, path = q.popleft()
        if len(path) >= max_hops:
            continue
        for step in adj.get(cur, []):
            npath = path + [step]
            if step["to"] == tgt:
                found.append(_path_dict(npath))
                continue
            if step["to"] == "__UNKNOWN__":
                continue  # gap: cannot traverse
            if step["to"] in seen and seen[step["to"]] <= len(npath):
                continue
            seen[step["to"]] = len(npath)
            if step["to"] == tgt:
                found.append(_path_dict(npath))
            else:
                q.append((step["to"], npath))
    # UNKNOWN semantics: also report whether the target is reachable in the
    # unresolved frontier (a path exists but crosses unresolved callees).
    gap_hits = _frontier_hits(adj, src, tgt, max_hops)
    if found:
        verdict = "FOUND"
    elif gap_hits:
        verdict = "PARTIAL"
    else:
        verdict = "UNKNOWN"
    return {
        "paths": found[:3],
        "unknown_frontier_hits": gap_hits,
        "verdict": verdict,
        "note": ("FOUND: resolved CALL chain exists. PARTIAL: chain exists only via unresolved "
                 "targets (never claimed as proven). UNKNOWN: no evidence either way — absence of "
                 "a path ≠ proof of absence."),
        "capability": _capability_row(b, lane),
    }


def _path_dict(steps: list[dict]) -> dict:
    return {
        "hops": [{"from": s["edge"]["source"].split(":")[-1],
                  "to": "[Unknown Dynamic Target]" if s["unknown"] else s["to"],
                  "kind": s["edge"]["kind"],
                  "truth_class": s["edge"].get("truth_class"),
                  "target_resolution": s["edge"].get("target_resolution"),
                  "coverage": s["edge"].get("coverage"),
                  "fact_id": (s["edge"].get("representative_witnesses") or [{}])[0].get("fact_id")
                  if s["edge"].get("representative_witnesses") else None}
                 for s in steps],
        "witness_refs": [(s["edge"].get("representative_witnesses") or [{}])[0].get("fact_id")
                         for s in steps],
    }


def _frontier_hits(adj: dict, src: str, tgt: str, max_hops: int) -> list[str]:
    out = []
    q = deque([(src, 0)])
    seen = {src}
    while q:
        cur, d = q.popleft()
        if d >= max_hops:
            continue
        for step in adj.get(cur, []):
            if step["unknown"]:
                continue
            if step["to"] == tgt:
                continue
            if step["to"] not in seen:
                seen.add(step["to"])
                q.append((step["to"], d + 1))
    return sorted(seen)[:8]


def t_explain_evidence(p: dict) -> dict:
    fact_id = str(p.get("fact_id", ""))
    edge_id = str(p.get("edge_id", ""))
    entity_id = str(p.get("entity_id", ""))
    port_id = str(p.get("port_id", ""))
    binding_id = str(p.get("binding_id", ""))
    transfer_id = str(p.get("transfer_id", ""))
    semantic_stage_id = str(p.get("semantic_stage_id", ""))
    if entity_id or edge_id:
        published = published_store.explain(store(), entity_id or edge_id, p.get("repo_id"))
        if published is not None:
            return published
        if p.get("repo_id"):
            return {"summary": "ENTITY_NOT_FOUND: no published fact at this repository",
                    "error": {"code": "ENTITY_NOT_FOUND", "requested": entity_id or edge_id}}
    # §7: Port → formal declaration → callsite actual → source
    if port_id:
        for port in _di_ports():
            if port.get("port_id") == port_id:
                return {
                    "summary": f"port {port_id}: {port.get('direction')} {port.get('dtype')} "
                               f"rank={port.get('rank')} shape_status={port.get('shape_status')} "
                               f"(file {port.get('file')})",
                    "object": {
                        "kind": "DataPort", "port_id": port_id,
                        "function": port.get("fn"), "file": port.get("file"),
                        "name": port.get("name"), "direction": port.get("direction"),
                        "dtype": port.get("dtype"), "source_type": port.get("source_type"),
                        "rank": port.get("rank"), "shape": port.get("shape"),
                        "shape_status": port.get("shape_status"),
                        "byte_size": port.get("byte_size"),
                        "byte_size_expression": port.get("byte_size_expr"),
                        "truth_class": port.get("truth_class"), "coverage": port.get("coverage"),
                    },
                    "source": {"kind": "formal parameter declaration",
                               "file": port.get("file"),
                               "evidence": port.get("evidence", []),
                               "source_span": _source_span(
                                   port.get("file"),
                                   (port.get("evidence") or [{}])[0].get("line"),
                                   name=port.get("name"), kind="DECLARATION"),
                               "resource_uri": f"rintel://port/{port_id}"},
                    "bindings": [b for b in _di_bindings() if b.get("callee_port") == port.get("name")
                                 and b.get("callee_symbol") == port.get("fn")][:8],
                    "next": ["read_resource"],
                }
        return {"summary": f"port_id '{port_id}' not found in DATA-INTERFACE0 parse set"}
    if binding_id:
        b = next((x for x in _di_bindings() if x.get("binding_id") == binding_id), None)
        if not b:
            return {"summary": f"binding_id '{binding_id}' not found"}
        callee_ports = _di_by_fn().get(b.get("callee_symbol"), [])
        formal = next((q for q in callee_ports if q.get("name") == b.get("callee_port")), None)
        callsite = {"callsite_line": b.get("callsite_line"),
                    "callsite_expr": (b.get("evidence") or [{}])[0].get("expr") if b.get("evidence") else None,
                    "source_span": _source_span(
                        (b.get("evidence") or [{}])[0].get("file") or b.get("file"),
                        b.get("callsite_line"),
                        expr=(b.get("evidence") or [{}])[0].get("expr") if b.get("evidence") else None,
                        kind="CALLSITE")}
        return {
            "summary": f"binding {binding_id}: {b.get('caller_symbol')}.{b.get('caller_expr')} → "
                       f"{b.get('callee_symbol')}.{b.get('callee_port')} @L{b.get('callsite_line')}",
            "object": {"kind": "PortBinding", "binding_id": binding_id,
                       "caller_symbol": b.get("caller_symbol"),
                       "callee_symbol": b.get("callee_symbol"),
                       "caller_actual": b.get("caller_expr"),
                       "callee_port": b.get("callee_port"),
                       "kind": b.get("kind"), "truth_class": b.get("truth_class"),
                       "coverage": b.get("coverage")},
            "callsite": callsite,
            "formal_decl": {"kind": "callee formal parameter",
                            "file": (formal or {}).get("file"),
                            "evidence": (formal or {}).get("evidence", []),
                            "resource_uri": f"rintel://binding/{binding_id}"},
            "note": "positional binding from a real CALL statement; port id is NOT a canonical function id",
            "next": ["read_resource"],
        }
    if semantic_stage_id:
        st = next((x for x in _fs_stages() if x.get("semantic_stage_id") == semantic_stage_id), None)
        if not st:
            return {"summary": f"semantic_stage_id '{semantic_stage_id}' not found",
                    "error": {"code": "STAGE_NOT_FOUND", "requested": semantic_stage_id,
                              "candidates": [x.get("semantic_stage_id") for x in _fs_stages()][:8],
                              "hint": "use query_flow(action=semantic, flow_id=...) or read_resource "
                                      "rintel://semantic-stage/{id}"}}
        memb = [m for m in _fs_memberships()
                if m.get("semantic_stage_id") == semantic_stage_id]
        return {"summary": f"semantic stage {st.get('display_name')} ({st.get('stage_kind')}): "
                           f"{len(memb)} membership(s); annotation plane, NOT evidence",
                "object": {"kind": "SemanticStage", "semantic_stage_id": semantic_stage_id,
                           "display_name": st.get("display_name"), "stage_kind": st.get("stage_kind"),
                           "origin": st.get("origin"), "status": st.get("status"),
                           "truth_class": st.get("truth_class"), "coverage": st.get("coverage")},
                "evidence_refs": st.get("evidence_refs", []),
                "unknowns": st.get("unknowns", []),
                "memberships": memb,
                "inputs": st.get("inputs", []), "outputs": st.get("outputs", []),
                "resource_uri": f"rintel://semantic-stage/{semantic_stage_id}"}
    if transfer_id:
        t = next((x for x in _di_transfers() if x.get("transfer_id") == transfer_id), None)
        if not t:
            return {"summary": f"transfer_id '{transfer_id}' not found"}
        return {"summary": f"transfer {transfer_id}: {t.get('source_port')} → {t.get('target_port')} "
                           f"({t.get('transfer_kind')})",
                "object": {"kind": "DataTransfer", "transfer_id": transfer_id,
                           "source_port": t.get("source_port"), "target_port": t.get("target_port"),
                           "dtype": t.get("dtype"), "rank": t.get("rank"),
                           "shape_status": t.get("shape_status"),
                           "byte_size": t.get("byte_size"),
                           "transfer_kind": t.get("transfer_kind"),
                           "truth_class": t.get("truth_class"), "coverage": t.get("coverage"),
                           "witnesses": t.get("witnesses", [])},
                "note": "PASS_BY_REFERENCE is an interface property, NOT evidence of a memory copy"}
    out = []
    direct_edge = None
    for lane in _available_lanes():
        b = bundle(lane)
        for e in _edges(b):
            for w in (e.get("representative_witnesses") or []):
                if fact_id and w.get("fact_id") == fact_id:
                    out.append(_fact_dict(lane, e, w))
            if edge_id and edge_id in _edge_ids(e):
                out.append(_fact_dict(lane, e, (e.get("representative_witnesses") or [{}])[0]))
                direct_edge = (lane, e)
    if edge_id and not direct_edge:
        return _edge_recovery(edge_id)
    if entity_id and not out:
        r = None
        try:
            r = store().evidence_for(LANE_REPO["jpl"], None, entity_id)
        except Exception:
            r = None
        out = [{"provider": x.get("source"), "confidence": x.get("confidence"),
                "payload": x.get("payload_json")} for x in (r or [])][:10]
    return {"summary": f"{len(out)} evidence record(s)", "evidence": out[:10],
            "direct_record_found": bool(fact_id) or direct_edge is not None,
            "next": ["read_resource"]}


# --- SOURCE-SPAN-COL0 §19: witnesses carry a canonical SourceSpan ----------
_SS_ROOT = Path("/Users/wu/Documents/dh/a3/fac/fac")
_ss_cache = None


def _source_span(file: str | None, line: int | None, name: str | None = None,
                 kind: str = "REFERENCE", expr: str | None = None) -> dict:
    """Canonical span for a witness location; LINE_ONLY when not recoverable."""
    global _ss_cache
    from rintel.source_span import (SourceCache, format_span, line_only,
                                    resolve_call, resolve_identifier)
    if _ss_cache is None:
        _ss_cache = SourceCache(_SS_ROOT, lang_of=lambda rel: "fortran"
                                if str(rel).endswith((".f", ".f90", ".for"))
                                else "c")
    src = _ss_cache.get(file)
    if src is None or not line:
        return line_only(file, line, kind,
                         {"reason": "source_not_in_canonical_tree",
                          "method": "witness_location"})
    token = name
    if not token and expr:
        import re as _re
        m = _re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", str(expr))
        token = m.group(1) if m else None
    if token:
        span = None
        if kind in ("CALLSITE", "OPERATION"):
            span = resolve_call(src, int(line), token, kind)
        if span is None:
            span = resolve_identifier(src, int(line), token, kind)
    else:
        from rintel.source_span import resolve_statement
        span = resolve_statement(src, int(line), kind)
    span["text"] = format_span(span)
    return span


def _fact_dict(lane: str, e: dict, w: dict) -> dict:
    return {
        "lane": lane,
        "fact_id": w.get("fact_id"),
        "provider": w.get("provider"),
        "kind": w.get("kind", e.get("kind")),
        "semantic": w.get("semantic"),
        "source_symbol": e["source"].split(":")[-1],
        "target": e["target"],       # may be [Unknown Dynamic Target]
        "file": (w.get("source") or {}).get("file"),
        "line": (w.get("source") or {}).get("line"),
        "expression": w.get("expr"),
        # §19: exact SourceSpan when recoverable, LINE_ONLY otherwise
        "source_span": _source_span((w.get("source") or {}).get("file"),
                                    (w.get("source") or {}).get("line"),
                                    expr=w.get("expr"), kind="CALLSITE"),
        "truth_class": e.get("truth_class"),
        "execution_modality": e.get("execution_modality"),
        "target_resolution": e.get("target_resolution"),
        "coverage": e.get("coverage"),
        "source_resource": f"rintel://file/{LANE_ROOT[lane]}",
    }


# ---------------------------------------------------------------------------
# FLOW discovery contract (MCP-FLOW-DISCOVERY-FIX0): strict action enum,
# registry-backed candidates, structured NOT_FOUND, bounded responses.
# ---------------------------------------------------------------------------

FLOW_ACTIONS = ("list", "get", "regions", "region", "scenarios", "project", "semantic")


def _err(code: str, requested, candidates: list[str] | None, hint: str,
         detail: str = "") -> dict:
    """structured NOT_FOUND / UNKNOWN_ACTION — machine readable + discovery hint."""
    out = {"error": {"code": code, "requested": requested,
                     "candidates": (candidates or [])[:8], "hint": hint}}
    if detail:
        out["error"]["detail"] = detail
    return {"summary": f"{code}: {requested} — {hint}",
            "error": out["error"]}


def _paged(items: list, limit: int | None, cursor: str | None, limit_default: int = 20):
    limit = max(1, int(limit or limit_default))
    start = int(cursor) if cursor and str(cursor).isdigit() else 0
    page = items[start:start + limit]
    return {"count": len(items), "returned": len(page),
            "has_more": start + limit < len(items),
            "next_cursor": str(start + limit) if start + limit < len(items) else None,
            "items": page}


def _flow_registry() -> dict:
    mf = _artifact("master_flow") or {}
    lane = mf.get("lane", "fac_c")
    return {"lane": lane, "flows": mf.get("flows", [])}


def _flow_candidates(requested: str) -> list[str]:
    """lightweight prefix/substring ranking over the REAL registry (§7)."""
    reg = _flow_registry()["flows"]
    ids = [f["flow_id"] for f in reg]
    if requested in ids:
        return ids
    ranked = sorted(ids, key=lambda fid: (
        0 if fid.startswith(requested) or requested.startswith(fid) else 1,
        abs(len(fid) - len(requested))))
    return ranked[:6]


def _semantic_summary(app: str) -> dict:
    stages = [x for x in _fs_stages() if x.get("semantic_stage_id", "").startswith(f"stage:{app}:")]
    return {"semantic_available": bool(stages), "stage_count": len(stages),
            "accepted": sum(1 for x in stages if x.get("status") == "ACCEPTED"),
            "suggested": sum(1 for x in stages if x.get("status") == "SUGGESTED"),
            "unclassified": sum(1 for x in stages if "unclassified" in x.get("semantic_stage_id", ""))}



def _guard_resolution_aggregate(app: str) -> dict:
    sc = (_artifact("scenarios") or {}).get("scenarios", [])
    acc = {"TRUE": 0, "FALSE": 0, "UNKNOWN": 0}
    for s in sc:
        if s.get("app") != app:
            continue
        for g in s.get("guard_resolutions", []):
            k = g.get("resolution", "UNKNOWN")
            acc[k] = acc.get(k, 0) + 1
    return acc


def _region_result(r: dict, artifacts: dict, p: dict, flow_id: str) -> dict:
    rid = r["region_id"]
    out = {"summary": f"{r['name']}: {len(r['methods'])} commands, {len(r['callees'])} callees "
                      f"({r.get('unresolved_callee_count', 0)} unresolved)",
           "region": {"region_id": rid, "name": r["name"], "methods": r["methods"],
                      "callees": r["callees"][:60],
                      "unresolved_callee_count": r.get("unresolved_callee_count", 0),
                      "why": r.get("why", [])[:2], "derived": True,
                      "handler_symbols": r.get("handler_symbols", [])[:40],
                      "witnesses": [w for w in r.get("witnesses", [])][:12],
                      "semantic_stage_ids": _stage_ids_for_region(rid),
                      "resource_uri": f"rintel://data-interface/flow-region/{rid}"}}
    if flow_id:
        out["flow_id"] = flow_id
    intf = _di_flow_intf().get(rid)
    if intf:
        ports: list[dict] = []
        fn = _di_by_fn()
        for c in intf.get("callees", []):
            ports.extend(fn.get(c, []))
        summ = _ports_summary(ports) if ports else None
        out["interface_summary"] = summ or {
            "inputs": 0, "outputs": 0, "note": "callees carry no DATA-INTERFACE0 ports"}
        if bool(p.get("include_interfaces")):
            out["boundary_ports"] = [_port_record(q) for q in ports]
            out["boundary_callees"] = intf.get("callees", [])
    else:
        out["interface_summary"] = None
        out["region"]["note"] = ("region not in DATA-INTERFACE0 flow interfaces — "
                                 "no callee matched the parse set")
    return out



def t_query_flow(p: dict) -> dict:
    action = str(p.get("action") or "list")        # §5: omitted -> action=list
    if action not in FLOW_ACTIONS:
        return _err("UNKNOWN_ACTION", action, list(FLOW_ACTIONS),
                    "use one of the actions above (see action enum)",
                    f"valid actions: {', '.join(FLOW_ACTIONS)}")
    artifacts = {k: _artifact(k) for k in ("master_flow", "scenarios", "regions", "membership")}
    reg = _flow_registry()
    lane = reg["lane"]
    all_sc = (artifacts["scenarios"] or {}).get("scenarios", [])

    if action == "list":
        # §4: no flow_id required; every real flow discoverable on first call
        flows = []
        for f in reg["flows"]:
            sm = _semantic_summary(f["app"])
            flows.append({
                "flow_id": f["flow_id"], "app": f["app"], "lane": lane,
                "entry": (f.get("entry_symbols") or [])[:3],
                "region_count": len(f.get("regions", [])),
                "scenario_count": len(f.get("scenario_ids", [])),
                "semantic_available": sm["semantic_available"],
                "coverage": "PARTIAL",
                "short_description": f"{f.get('name')} — {len(f.get('nodes', []))} nodes, "
                                     f"{len(f.get('edges', []))} edges, {len(f.get('regions', []))} regions",
            })
        return {"summary": f"{len(flows)} flow(s) on lane {lane}; use flow_id from this "
                           f"list for get/regions/region/scenarios/project/semantic",
                "lane": lane, "flows": flows,
                "next": ["query_flow(action=get, flow_id=...)",
                         "query_flow(action=regions, flow_id=...)",
                         "query_flow(action=semantic, flow_id=...)"]}

    flow_id = str(p.get("flow_id", ""))
    if action == "region" and not flow_id:
        # §24 backward-compat: global region registry lookup also works without
        # flow_id; candidates remain real (flow_id only scopes the suggestion)
        rid = str(p.get("region_id", ""))
        r = next((x for x in (artifacts["regions"] or {}).get("regions", [])
                  if x["region_id"] == rid), None)
        if r:
            return _region_result(r, artifacts, p, flow_id="")
        cand = [x["region_id"] for x in (artifacts["regions"] or {}).get("regions", [])]
        return _err("REGION_NOT_FOUND", rid, cand,
                    "use query_flow(action=regions, flow_id=...) to list regions; "
                    "or pass flow_id to scope candidates")


    flow = next((f for f in reg["flows"] if f["flow_id"] == flow_id), None)
    if not flow:
        # §6: structured FLOW_NOT_FOUND + real candidates, never a bare sentence
        return _err("FLOW_NOT_FOUND", flow_id, _flow_candidates(flow_id),
                    "use query_flow(action=list) to enumerate flows")
    app = flow["app"]

    if action == "get":
        # §11-§15: compact overview; guards no longer default-flattened
        guards = flow.get("guards", [])
        gres: dict[str, int] = {}
        for g in guards:
            k = g.get("resolution") or "UNKNOWN"
            gres[k] = gres.get(k, 0) + 1
        shared_names = {x for sc in flow.get("shared_core", [])
                        for x in (sc.get("regions") or [])}
        region_preview = [{"region_id": r["region_id"], "name": r["name"],
                           "member_count": len(r.get("methods", [])),
                           "shared": r.get("name") in shared_names}
                          for r in flow.get("regions", [])][:10]
        stg = [x for x in _fs_stages() if x.get("semantic_stage_id", "").startswith(f"stage:{app}:")]
        out = {
            "flow_id": flow["flow_id"], "entry": (flow.get("entry_symbols") or [])[:3],
            "coverage": "PARTIAL",
            "region_summary": {"count": len(flow.get("regions", [])),
                               "regions_preview": region_preview},
            "scenario_summary": {"count": len(flow.get("scenario_ids", [])),
                                 "scenarios": flow.get("scenario_ids", [])[:6],
                                 "has_more": len(flow.get("scenario_ids", [])) > 6},
            "guard_summary": {"guard_count": len(guards), "by_resolution": gres,
                              "resolved_per_scenario_pair": _guard_resolution_aggregate(app),
                              "note": "master guard resolutions are UNKNOWN (dispatch guards are "
                                      "resolved per scenario); full list via "
                                      "include_guards=true (paged)"},
            "shared_core_summary": [{"name": x.get("name"), "regions": x.get("regions", [])[:6]}
                                    for x in flow.get("shared_core", [])][:5],
            "semantic_summary": _semantic_summary(app),
            "stages_preview": [_stage_compact(x) for x in stg][:10],
        }
        if bool(p.get("include_semantic")):
            out["semantic"] = _paged(stg, p.get("limit"), p.get("cursor"))
        if bool(p.get("include_guards")):
            out["guards"] = _paged(guards, p.get("limit", 10), p.get("cursor"))
        out["summary"] = (f"flow {flow['flow_id']}: {len(flow.get('regions', []))} regions, "
                          f"{len(flow.get('scenario_ids', []))} scenarios, "
                          f"{len(guards)} guards (summary only); "
                          f"semantic_available={out['semantic_summary']['semantic_available']}")
        out["next"] = ["query_flow(action=regions, flow_id=...)",
                       "query_flow(action=semantic, flow_id=...)",
                       "query_flow(action=get, flow_id=..., include_guards=true)"]
        return out

    if action == "regions":
        # §8-§9: region catalog — no more name probing
        shared_names = {x for sc in flow.get("shared_core", [])
                        for x in (sc.get("regions") or [])}
        rows = []
        for r in flow.get("regions", []):
            rows.append({
                "region_id": r["region_id"], "display_name": r.get("name", r["region_id"]),
                "prefix": r.get("name", ""), "member_count": len(r.get("methods", [])),
                "shared": r.get("name") in shared_names,
                "coverage": "PARTIAL",
                "semantic_stage_ids": _stage_ids_for_region(r["region_id"]),
                "unresolved_callee_count": r.get("unresolved_callee_count", 0)})
        page = _paged(rows, p.get("limit"), p.get("cursor"))
        return {"summary": f"{flow['flow_id']}: {len(rows)} regions (paged, returned {page['returned']})",
                "flow_id": flow["flow_id"], **page,
                "next": ["query_flow(action=region, flow_id=..., region_id=...)",
                         "query_flow(action=semantic, flow_id=...)"]}

    if action == "semantic":
        # §15: semantic stage catalog (annotation plane)
        stg = [x for x in _fs_stages() if x.get("semantic_stage_id", "").startswith(f"stage:{app}:")]
        page = _paged(stg, p.get("limit"), p.get("cursor"))
        out = {"summary": f"flow {flow['flow_id']}: {len(stg)} semantic stage(s) (annotation)",
               "flow_id": flow["flow_id"], "semantic_summary": _semantic_summary(app), **page}
        out["items"] = [_stage_compact(x) for x in page["items"]]
        if bool(p.get("include_members")):
            mem = [m for m in _fs_memberships()
                   if m.get("semantic_stage_id") in _stage_ids_of(flow)]
            out["members"] = _paged(mem, p.get("limit", 10), None)
        out["next"] = ["read_resource(rintel://semantic-stage/{id})"]
        return out

    if action == "region":
        rid = str(p.get("region_id", ""))
        r = next((x for x in (artifacts["regions"] or {}).get("regions", [])
                  if x["region_id"] == rid), None)
        if not r:
            # §10: structured REGION_NOT_FOUND with real candidates of this flow
            cand = [x["region_id"] for x in flow.get("regions", [])] or \
                   [x["region_id"] for x in (artifacts["regions"] or {}).get("regions", [])]
            return _err("REGION_NOT_FOUND", rid, cand,
                        "use query_flow(action=regions, flow_id=...) to list regions")
        return _region_result(r, artifacts, p, flow_id=flow_id)


    if action == "scenarios":
        # §24 backward-compatible scenario list for the flow's app
        out = []
        for sc in all_sc:
            if sc.get("app") != app:
                continue
            st = {"TRUE": 0, "FALSE": 0, "UNKNOWN": 0}
            for g in sc.get("guard_resolutions", []):
                st[g["resolution"]] = st.get(g["resolution"], 0) + 1
            out.append({"scenario_id": sc["scenario_id"], "name": sc["name"],
                        "active_regions": len(sc.get("active_regions", [])),
                        "inactive_regions": len(sc.get("inactive_regions", [])),
                        "guard_status": st})
        page = _paged(out, p.get("limit"), p.get("cursor"))
        return {"summary": f"{len(out)} scenarios for {app} (paged, returned {page['returned']})",
                "flow_id": flow["flow_id"], **page}

    # action == "project": §24 backward-compatible scenario projection
    sid = str(p.get("scenario_id", ""))
    sc = next((x for x in all_sc if x["scenario_id"] == sid), None)
    if not sc:
        cand = [x["scenario_id"] for x in all_sc if x.get("app") == app]
        return _err("SCENARIO_NOT_FOUND", sid, cand,
                    "use query_flow(action=scenarios, flow_id=...) to list scenario ids")
    res = {"scenario_id": sid, "name": sc["name"],
           "constraints": sc.get("constraints", []),
           "active_regions": sc.get("active_regions", []),
           "inactive_regions": sc.get("inactive_regions", []),
           "unknown_regions": sc.get("unknown_regions", []),
           "shared_regions": sc.get("shared_regions", []),
           "guard_resolutions": sc.get("guard_resolutions", []),
           "coverage": "PARTIAL"}
    if isinstance(p.get("include_guards"), bool) and not p["include_guards"]:
        res.pop("guard_resolutions", None)
    return {"summary": f"{sc['name']}: {len(res['active_regions'])} active / "
                       f"{len(res['inactive_regions'])} inactive; UNKNOWN guards kept (UNKNOWN≠FALSE)",
            "projection": res}


def _stage_ids_for_region(region_id: str) -> list[str]:
    return [m["semantic_stage_id"] for m in _fs_memberships()
            if m.get("id") == region_id and m.get("member_kind") == "flow_region"]


def _stage_ids_of(flow: dict) -> list[str]:
    return [x.get("semantic_stage_id", "") for x in _fs_stages()
            if x.get("semantic_stage_id", "").startswith(f"stage:{flow.get('app')}:")]


def _latest_snapshot(repo_id: str) -> str | None:
    st = store()
    try:
        snaps = st.snapshots(repo_id) if hasattr(st, "snapshots") else []
        if snaps:
            return snaps[-1]["id"]
    except Exception:
        pass
    return None


def t_create_design(p: dict) -> dict:
    repo_id = str(p.get("repo_id", "jpl"))
    name = str(p.get("name", "AGENT design"))
    svc = DesignLifecycleService(store())
    sid = _latest_snapshot(repo_id)
    if not sid:
        return {"error": f"no snapshot indexed for repo {repo_id}"}
    change = svc.open_change(
        repo_id=repo_id, base_canonical_revision=sid, intent=name,
        scope=dict(p.get("scope") or {}),
        acceptance_criteria=[
            AcceptanceCriterion("structural", "structural", required=True),
        ], actor="agent:rintel-mcp")
    return {
        "summary": f"design change opened: {change.id}",
        "design_id": change.id, "change_id": change.id,
        "route": f"/design-changes/{change.id}",
        "state": change.state.value,
        "next": ["design_patch", "validate_design"],
    }


def t_design_patch(p: dict) -> dict:
    change_id = str(p.get("change_id") or p.get("design_id", ""))
    mode = str(p.get("mode", "apply"))
    ops = p.get("ops") or []
    svc = DesignLifecycleService(store())
    before = svc.get_change(change_id)
    normalized = []
    for op in ops:
        claim = dict(op)
        op_kind = str(claim.pop("op", claim.get("kind", "planned_change")))
        claim.setdefault("kind", {
            "add_node": "planned_module",
            "add_edge": "planned_relation",
            "add_port": "planned_interface",
            "delete_block": "deprecation",
        }.get(op_kind, "planned_flow"))
        claim.setdefault("justification", "agent-proposed design operation")
        claim.setdefault("support", [{
            "canonical_revision": before.base_canonical_revision,
            "authority": "CANONICAL_CANDIDATE",
        }])
        normalized.append(claim)
    sketch = {"change_id": change_id, "expected_changes": normalized}
    if mode == "preview" or not ops:
        return {"summary": f"preview only — {len(ops)} design claim(s) for {change_id[:16]}…",
                "proposal_id": f"prop-{abs(hash(json.dumps(ops, sort_keys=True))) % 10**8}",
                "before": {"state": before.state.value,
                           "design_revision": before.design_revision.id},
                "after": sketch, "affected_design_ids": [change_id]}
    after = svc.apply_command(change_id, PlanChange(
        actor="agent:rintel-mcp", expected_changes=tuple(normalized)))
    return {
        "summary": f"{len(normalized)} design claim(s) planned",
        "change_id": change_id, "design_id": change_id,
        "state": after.state.value,
        "design_revision": after.design_revision.id,
        "drc": after.design_drc,
        "route": f"/design-changes/{change_id}",
    }


def t_validate_design(p: dict) -> dict:
    change_id = str(p.get("change_id") or p.get("design_id", ""))
    change = DesignLifecycleService(store()).get_change(change_id)
    out = {
        "drc": change.design_drc or {"acceptable": False,
                                      "status": "NOT_EXECUTED"},
        "lvs": change.lvs_result or {"overall_status": "NOT_EXECUTED"},
        "expected_actual": change.expected_actual or {
            "outcome": "NOT_EXECUTED"},
    }
    return {
        "design_id": change_id, "change_id": change_id,
        "state": change.state.value, "checks": out,
        "summary": f"Design DRC acceptable={out['drc'].get('acceptable', False)}; "
                   f"LVS={out['lvs'].get('overall_status', 'NOT_EXECUTED')}",
    }


def t_get_change_workspace(p: dict) -> dict:
    """The same validated read substrate used by REST and the Human UI."""
    change_id = str(p.get("change_id") or p.get("design_id") or "")
    selector = p.get("relation_selector")
    if selector is not None and not isinstance(selector, dict):
        return {"error": {"code": "invalid_relation_selector",
                          "message": "relation_selector must be an object"}}
    try:
        return workspace_projection(DesignLifecycleService(store()), change_id,
                                    relation_selector=selector)
    except ValueError as exc:
        return {"error": {"code": "invalid_relation_selector",
                          "message": str(exc)}}


def t_get_git_collaboration(p: dict) -> dict:
    """Read the same Git workspace/merge projection as REST and the UI."""
    from rintel.git_workspace import GitWorkspaceError, GitWorkspaceManager
    from rintel.server.settings import get_settings

    change_id = str(p["change_id"])
    db = store()
    try:
        change = DesignLifecycleService(db).get_change(change_id)
        repo = db.repo(change.repo_id)
        if not repo:
            return {"error": {"code": "repo_not_found",
                              "message": "change repository unavailable"}}
        manager = GitWorkspaceManager(get_settings().git_workspace_state_path)
        result = manager.list_for_change(change_id)
        try:
            result["repository"] = manager.inspect_repository(repo["root_path"])
        except GitWorkspaceError as exc:
            result["repository"] = {"status": exc.code, "path": repo["root_path"]}
        return result
    except Exception as exc:
        return {"error": {"code": getattr(exc, "code", "git_workspace_error"),
                          "message": str(exc)}}


def t_get_agent_workspace(p: dict) -> dict:
    """Agent-authenticated read; cannot allocate, widen scope, approve or merge."""
    from rintel.git_workspace import GitWorkspaceError, GitWorkspaceManager
    from rintel.server.settings import get_settings

    try:
        return GitWorkspaceManager(
            get_settings().git_workspace_state_path).assigned_workspace(
                str(p["workspace_id"]), str(p["agent_session_token"]))
    except GitWorkspaceError as exc:
        return {"error": {"code": exc.code, "message": exc.message,
                          "details": exc.details}}


def t_request_agent_execution(p: dict) -> dict:
    """Request trusted BUILD/TEST for the caller's assigned exact Git head."""
    from rintel.git_workspace import GitWorkspaceError, GitWorkspaceManager
    from rintel.server.execution_config import load_execution_registry
    from rintel.server.settings import get_settings

    try:
        settings = get_settings()
        authority = load_execution_registry(settings)[1]
        manager = GitWorkspaceManager(
            settings.git_workspace_state_path, execution_authority=authority)
        assigned = manager.assigned_workspace(
            str(p["workspace_id"]), str(p["agent_session_token"]))
        change = DesignLifecycleService(store()).get_change(assigned["change_id"])
        profile = authority.profiles.get(str(p["profile_id"]))
        kind = str(p["kind"])
        if profile is None or profile.repo_id != change.repo_id or profile.kind != kind:
            raise GitWorkspaceError("EXECUTION_PROFILE_MISMATCH",
                                    "profile is unavailable for this workspace")
        raw_parameters = p.get("parameters") or []
        if not isinstance(raw_parameters, list) or any(
                not isinstance(item, list) or len(item) != 2
                for item in raw_parameters):
            raise GitWorkspaceError("INVALID_PARAMETERS",
                                    "parameters must be a list of [name, value] pairs")
        parameters = {str(item[0]): str(item[1]) for item in raw_parameters}
        return manager.request_workspace_execution(
            assigned["workspace_id"], str(p["agent_session_token"]),
            profile_id=profile.id, kind=kind,
            parameters=tuple(sorted((str(k), str(v))
                                    for k, v in parameters.items())))
    except GitWorkspaceError as exc:
        return {"error": {"code": exc.code, "message": exc.message,
                          "details": exc.details}}
    except Exception as exc:
        return {"error": {"code": "execution_denied", "message": str(exc)}}


def t_read_resource(p: dict) -> dict:
    uri = str(p.get("uri", ""))
    if not uri.strip():
        return {"summary": "INVALID_URI: uri required",
                "error": {"code": "INVALID_URI", "requested": "",
                          "hint": "uri must be rintel://... — see resources/list for supported forms"}}
    published = published_store.read_resource(store(), uri)
    if published is not None:
        return published
    if os.environ.get("RINTEL_MCP_LOCAL_RELEASE") == "1" and uri.startswith("rintel://file/"):
        candidate = Path(uri.removeprefix("rintel://file/"))
        if candidate.is_absolute():
            resolved = candidate.resolve()
            for repo in store().repos():
                root = Path(repo["root_path"]).resolve()
                if resolved.is_relative_to(root):
                    sid = store().current_snapshot(repo["id"])
                    if sid:
                        safe_uri = published_store.source_uri(
                            repo["id"], sid, resolved.relative_to(root).as_posix(), 1, 200)
                        return published_store.read_resource(store(), safe_uri)
        return {"uri": uri, "summary": "INVALID_URI: file is outside indexed repositories",
                "error": {"code": "INVALID_URI", "requested": uri}}
    if uri.startswith("rintel://execution/") or uri.startswith("rintel://test-run/"):
        from rintel.execution_authority import ExecutionError
        from rintel.server.execution_config import load_execution_registry
        from rintel.server.settings import get_settings

        try:
            authority = load_execution_registry(get_settings())[1]
            if uri.startswith("rintel://execution/"):
                identity = uri.removeprefix("rintel://execution/")
                return {"uri": uri, "execution_receipt": authority.get_receipt(identity)}
            identity = uri.removeprefix("rintel://test-run/")
            return {"uri": uri, "test_run_receipt": authority.get_test_run(identity)}
        except ExecutionError as exc:
            return {"uri": uri, "error": {"code": "EXECUTION_RECEIPT_UNAVAILABLE",
                                           "message": str(exc)}}
    if uri.startswith("rintel://build/attempt/") and "/artifact/" in uri:
        from rintel.build_artifact_projection import ArtifactReadError, read_build_artifact
        from rintel.build_recovery import BuildRecoveryError, BuildRecoveryService
        from rintel.server.execution_config import load_execution_registry
        from rintel.server.settings import get_settings

        parts = uri.removeprefix("rintel://build/attempt/").split("/")
        if len(parts) != 4 or parts[1] != "artifact":
            return {"uri": uri, "error": {"code": "INVALID_URI"}}
        attempt_id, _, kind, digest = parts
        settings = get_settings()
        profiles, authority = load_execution_registry(settings)
        service = BuildRecoveryService(settings.build_artifacts_path, profiles,
                                       execution_authority=authority)
        try:
            attempt = service.get_attempt(attempt_id)
            artifact_id = f"attempts/{attempt_id}/{kind.lower()}.bin"
            return {"uri": uri, "artifact": read_build_artifact(
                service.root, attempt, kind, artifact_id, digest)}
        except (BuildRecoveryError, ArtifactReadError) as exc:
            return {"uri": uri, "error": {"code": "BUILD_ARTIFACT_UNAVAILABLE",
                                           "message": str(exc)}}
    if uri.startswith("rintel://build/attempt/"):
        from rintel.build_recovery import BuildRecoveryError, BuildRecoveryService
        from rintel.design_lifecycle import DesignLifecycleService
        from rintel.server.deps import build_store
        from rintel.server.execution_config import load_execution_registry
        from rintel.server.settings import get_settings

        attempt_id = uri.removeprefix("rintel://build/attempt/")
        settings = get_settings()
        profiles, authority = load_execution_registry(settings)
        service = BuildRecoveryService(settings.build_artifacts_path, profiles,
                                       execution_authority=authority)
        try:
            build_store_instance = build_store()
            try:
                return {"uri": uri, "build_failure": service.failure_package(
                    attempt_id, build_store_instance,
                    lifecycle_service=DesignLifecycleService(build_store_instance))}
            finally:
                build_store_instance.close()
        except BuildRecoveryError as exc:
            return {"uri": uri, "error": {"code": "BUILD_ATTEMPT_UNAVAILABLE",
                                           "message": str(exc)}}
    n = uri.split("rintel://")[-1].split("/")
    if len(n) >= 3 and n[0] == "symbol" and n[2] == "source":
        name = n[1]
        node = None
        lane = LANES[0]
        for lane in _available_lanes():
            b = bundle(lane)
            node = _byname(b).get(name)
            if node and node.get("source_range"):
                break
        if node and node.get("source_range"):
            root = Path(LANE_ROOT[lane])
            fname = node["file"]
            f = next((root / d / fname for d in ("", "sfac", "faclib", "lapack", "blas")
                      if (root / d / fname).exists()), root / fname)
            try:
                text = f.read_text(errors="replace").splitlines()
                s = int(node["source_range"].get("start_line") or 1)
                e = int(node["source_range"].get("end_line") or (s + 10))
                return {"uri": uri, "file": str(f),
                        "start_line": s, "end_line": e,
                        "text": "\n".join(text[max(0, s - 5):e + 5])}
            except OSError as exc:
                return {"uri": uri, "error": str(exc)[:120]}
        # kernel-universe fallback (FAC-LIBRARY-COVERAGE0): exact function body.
        # The frozen lane lacks faclib symbols; derive the body span from the
        # kernel definition line + brace/END scan (not the file head).
        try:
            from rintel.kernel import service as _ksvc
            from rintel.kernel.extract_c import strip_c_text
            g = _ksvc.get_symbol(name)
            cand = next((c for c in g.get("candidates", []) if c.get("is_definition")),
                        (g.get("candidates") or [None])[0])
            if cand:
                sp = cand.get("source_span") or {}
                fname = sp.get("file")
                s = int(sp.get("start_line") or 0)
                if fname and s:
                    kroot = Path(LANE_ROOT["fac_c"])
                    path = next((kroot / d / fname for d in ("", "sfac", "faclib", "lapack", "blas")
                                 if (kroot / d / fname).exists()), kroot / fname)
                    if path.exists():
                        raw = path.read_text(errors="replace")
                        lines = raw.splitlines()
                        end = None
                        note = "kernel-universe function body"
                        if fname.endswith(".c"):
                            slines = strip_c_text(raw).splitlines()
                            depth = 0
                            started = False
                            for i in range(max(0, s - 1), min(len(slines), s + 2500)):
                                depth += slines[i].count("{") - slines[i].count("}")
                                if not started and "{" in slines[i]:
                                    started = True
                                if started and depth <= 0:
                                    end = i
                                    break
                            note += " (brace-matched)"
                        else:
                            for i in range(s - 1, min(len(lines), s + 2500)):
                                if re.match(r"^\s*END", lines[i], re.I):
                                    end = i
                                    break
                            note += " (fortran END)"
                        if end is not None:
                            return {"uri": uri, "file": str(path), "start_line": s,
                                    "end_line": end + 1,
                                    "text": "\n".join(lines[max(0, s - 3):end + 2]),
                                    "note": note}
        except Exception:
            pass
        # fallback: DATA-INTERFACE0 parse set (Fortran/C outside the frozen lane)
        for f in (_di("ports") or []):
            if f.get("name") == name:
                root = Path(LANE_ROOT["fac_c"])
                fname = f["file"]
                path = next((root / d / fname for d in ("lapack", "blas", "sfac", "faclib")
                             if (root / d / fname).exists()), root / fname)
                try:
                    text = path.read_text(errors="replace").splitlines()
                    s = int(f.get("line") or 1)
                    return {"uri": uri, "file": str(path),
                            "start_line": s, "end_line": s + 80,
                            "text": "\n".join(text[max(0, s - 3):s + 80]),
                            "note": "source from the DATA-INTERFACE0 parse set "
                                    "(declarations + doc comments)"}
                except OSError as exc:
                    return {"uri": uri, "error": str(exc)[:120]}
    if len(n) >= 4 and n[0] == "file":
        f = Path("/".join(n[1:]))
        try:
            lines = f.read_text(errors="replace").splitlines()
            return {"uri": uri, "file": str(f), "total_lines": len(lines),
                    "text": "\n".join(lines[:200]),
                    "note": "first 200 lines only (use file + line range for full read)"}
        except OSError as exc:
            return {"uri": uri, "error": str(exc)[:120]}
    if len(n) >= 2 and n[0] == "evidence" and len(n) >= 2:
        return t_explain_evidence({"fact_id": n[1]})
    # DATA-INTERFACE0 resources (§14)
    if len(n) >= 2 and n[0] == "port":
        return t_explain_evidence({"port_id": "/".join(n[1:])})
    if len(n) >= 2 and n[0] == "binding":
        return t_explain_evidence({"binding_id": "/".join(n[1:])})
    if len(n) >= 3 and n[0] == "data-interface":
        if n[1] == "symbol":
            return t_get_symbol({"canonical_id": n[2], "include_ports": True})
        if n[1] == "module":
            mods = _di_modules()
            key = n[2] if n[2] in mods else next((k for k in mods
                                                  if k.endswith(n[2]) or (n[2] in k)), None)
            if not key:
                return {"uri": uri, "error": f"module '{n[2]}' has no data interface"}
            return {"uri": uri, "module": key, **t_query_data_interface(
                {"action": "module", "module": key})}
        if n[1] == "flow-region":
            return {"uri": uri, **t_query_flow(
                {"action": "region", "region_id": n[2], "include_interfaces": True})}
    if len(n) >= 2 and n[0] == "semantic-stage":
        return _semantic_stage_doc(n[1])
    if len(n) >= 2 and n[0] == "semantic-flow":
        return _semantic_flow_doc(n[1])
    return {"uri": uri, "summary": "INVALID_URI: unsupported resource URI",
            "error": {"code": "INVALID_URI", "requested": uri,
                      "hint": ("supported: rintel://symbol/{name}/source, rintel://file/{path}, "
                               "rintel://port/{port_id}, rintel://binding/{binding_id}, "
                               "rintel://semantic-stage/{stage_id}, rintel://data-interface/"
                               "{symbol|module|flow-region}/{id}")}}


def _semantic_stage_doc(sid: str) -> dict:
    st = next((x for x in _fs_stages() if x.get("semantic_stage_id") == sid), None)
    if not st:
        return {"uri": f"rintel://semantic-stage/{sid}", "error": f"no semantic stage {sid}"}
    memb = [m for m in _fs_memberships() if m.get("semantic_stage_id") == sid]
    edges = [e for e in _fs_edges()
             if e.get("source_stage") == sid or e.get("target_stage") == sid]
    return {"uri": f"rintel://semantic-stage/{sid}",
            "note": "SemanticStage = annotation projection; HEURISTIC, NOT evidence; "
                    "memberships carry evidence_refs; drill to FlowRegion/Function via ids",
            "stage": {k: st.get(k) for k in ("semantic_stage_id", "flow_id", "display_name",
                                             "description", "stage_kind", "truth_class",
                                             "origin", "status", "coverage", "inputs",
                                             "outputs", "evidence_refs", "unknowns",
                                             "members")},
            "memberships": memb, "stage_edges": edges}


def _semantic_flow_doc(app: str) -> dict:
    fl = (_fs("fac_semantic_flows.json") or {}).get("flows") or []
    f = next((x for x in fl if x.get("app") == app), None)
    if not f:
        return {"uri": f"rintel://semantic-flow/{app}",
                "error": f"no semantic flow for app {app}"}
    stages = [_stage_compact(x) for x in _fs_stages()
              if x.get("semantic_stage_id") in f.get("stage_order", [])]
    return {"uri": f"rintel://semantic-flow/{app}",
            "note": "Human-semantic projection of the FLOW-INFER0 master flow (annotation, "
                    "not evidence; scenario guard semantics from FLOW-INFER0 unchanged)",
            "flow": f, "stages": stages}


# one new aggregate tool (spec §1: exactly one)
RT_ACTIONS = ("runs", "stages", "invocations", "alignment", "symbol",
                "data", "data_access", "data_lineage", "dgesv",
                "performance", "hotspots", "critical_path",
                "performance_causes")
PERF_DIR = Path(__file__).resolve().parents[3] / "analysis_tournament" / "perf_topo0"
RT_DIR = Path(__file__).resolve().parents[3] / "analysis_tournament" / "runtime_trace0"
RTD_DIR = Path(__file__).resolve().parents[3] / "analysis_tournament" / "runtime_data0"
PC0_DIR = Path(__file__).resolve().parents[3] / "analysis_tournament" / "perf_cause0"


def _rt_run(run_id: str) -> dict | None:
    p = RT_DIR / "runs" / f"run-{run_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _perf_wid(run_id: str) -> str | None:
    rid = str(run_id).lower().replace("run-", "").replace("_", "-")
    if rid.endswith("-v1"):
        rid = rid[:-3]
    rid = rid.rstrip("-").upper()
    for cand in (rid, rid):
        if (PERF_DIR / f"run-{cand}-perf.json").exists():
            return cand
    # fallback: any perf file whose run_id prefix matches
    if PERF_DIR.exists():
        for f in PERF_DIR.glob("run-*-perf.json"):
            data = json.loads(f.read_text())
            if str(data.get("run_id", "")).lower().startswith(
                    rid.lower().replace("run-", "")):
                return f.stem.removeprefix("run-").removesuffix("-perf")
    return None


def _rt_perf_action(action: str, run_id: str, r: dict) -> dict:
    """PERF-TOPO0 actions: performance / hotspots / critical_path."""
    wid = _perf_wid(run_id)
    if wid is None:
        return {"summary": f"PERF_NOT_AVAILABLE: no perf_topo0 artifacts for "
                           f"{run_id!r}; run scripts/perf_topo0_collect.py first"}
    perf = json.loads((PERF_DIR / f"run-{wid}-perf.json").read_text())
    if action == "performance":
        return {
            "summary": (f"PERF {wid} ({perf['run_id']}): "
                        f"{len(perf['functions'])} functions / "
                        f"{len(perf['stages'])} stages / "
                        f"{len(perf['edges'])} performance edges; "
                        f"root inclusive={perf['chain']['observed_root_inclusive_ms']}ms; "
                        f"critical-path coverage={perf['chain']['coverage']}; "
                        f"unclosed_at_end={len(perf.get('unclosed_frames', []))} "
                        f"(exit-before-unwind; exclusive marked unreliable)"),
            "run_id": perf["run_id"], "evidence_revision": r.get("evidence_revision"),
            "revision": perf.get("revision"),
            "top_functions_by_time": sorted(
                perf["functions"], key=lambda f: -f["inclusive_ms"])[:8],
            "top_functions_by_count": sorted(
                perf["functions"], key=lambda f: -f["count"])[:8],
            "stages_by_time": sorted(
                perf["stages"], key=lambda s: -s["top_inclusive_ms"]),
            "critical_chain": [c["symbol_name"] for c in
                               perf["chain"]["critical_chain_exact_invocations"]],
            "edges_top": sorted(perf["edges"], key=lambda e: -e["observed_calls"])[:6],
        }
    if action == "hotspots":
        hs = json.loads((PERF_DIR / "hotspots.json").read_text()).get(wid, {})
        return {
            "summary": (f"PERF hotspots {wid}: separate rankings by total time / "
                        f"exclusive time / call count / mean duration — never merged "
                        f"into one score (spec §26)"),
            "run_id": perf["run_id"],
            "top_by_time": hs.get("top_by_time", [])[:10],
            "top_by_call_count": hs.get("top_by_call_count", [])[:10],
            "top_by_mean_duration": hs.get("top_by_mean_duration", [])[:10],
            "top_stages_by_time": hs.get("top_stages_by_time", [])[:10],
            "ranking_notes": hs.get("ranking_notes", []),
        }
    return {
        "summary": (f"PERF critical path {wid}: coverage={perf['chain']['coverage']} "
                    f"(single-threaded trace; durations add linearly), "
                    f"root inclusive={perf['chain']['observed_root_inclusive_ms']}ms — "
                    "dominant chain = max-inclusive child at each nesting level"),
        "run_id": perf["run_id"],
        "coverage": perf["chain"]["coverage"],
        "chain": perf["chain"]["critical_chain_exact_invocations"],
        "top_level_segments": perf.get("top_level", [])[:12],
        "unclosed_frames": perf.get("unclosed_frames", []),
    }


def _rtd_run(run_id: str) -> dict | None:
    """resolve run-W{id}-data.json by workload id / run_id / stem."""
    rid = str(run_id).lower().replace("run-", "").replace("_", "-")
    best = None
    if RTD_DIR.exists():
        for f in RTD_DIR.glob("run-*-data.json"):
            data = json.loads(f.read_text())
            wid = f.name.removeprefix("run-").removesuffix("-data.json")
            if rid in (wid.lower(), f"run-{wid}".lower(), data.get("run_id", "").lower()):
                return data
            if not best:
                best = data
    return best


def _rtd_action(action: str, run_id: str, d: dict, r: dict) -> dict:
    data = _rtd_run(run_id)
    if data is None:
        return {"summary": "RUNTIME_DATA_NOT_FOUND: no run-*-data.json for "
                           f"{run_id!r}; run the probe-instrumented worktree first",
                "error": {"code": "RUNTIME_DATA_NOT_FOUND", "requested": run_id,
                          "hint": "query_runtime(action=runs) then data/dgesv"}}
    rev = data.get("revision")
    objs = data.get("objects", {})
    if action == "data":
        slim = [{"runtime_data_id": v["runtime_data_id"],
                 "canonical_symbol_id": v["canonical_symbol_id"],
                 "region": v["canonical_location_id"].split(":")[-2],
                 "dtype": v["dtype"], "shape": v["shape"],
                 "shape_status": v["shape_status"],
                 "element_count": v["element_count"],
                 "byte_size": v["byte_size"], "logical_size": v["logical_size"],
                 "scope": v["scope"], "coverage": v["coverage"],
                 "truth_class": v["truth_class"]} for v in objs.values()]
        return {"summary": (f"{run_id}: {len(slim)} observed runtime data objects; "
                            f"dim n={data.get('observed_dim')}, "
                            f"DGESV M values={data.get('observed_m')}; "
                            f"logical_size is NOT transfer volume (§17)"),
                "run_id": run_id, "evidence_revision": rev,
                "observed_dim": data.get("observed_dim"),
                "dgesv_calls": data.get("dgesv_calls"),
                "objects": slim,
                "next": ["query_runtime(action=data_access, run_id=…)",
                         "query_runtime(action=dgesv, run_id=…)"]}
    if action == "data_access":
        evs = data.get("events", [])
        kinds = {}
        for e in evs:
            kinds[e["operation_kind"]] = kinds.get(e["operation_kind"], 0) + 1
        slim = [{"event_id": e["event_id"], "operation_kind": e["operation_kind"],
                 "invocation_id": e["invocation_id"],
                 "runtime_data_id": e["runtime_data_id"],
                 "canonical_location_id": e["canonical_location_id"],
                 "ts_ns": e["ts_ns"], "source_line": e.get("source_line"),
                 "note": e.get("note")} for e in evs[:120]]
        return {"summary": (f"{run_id}: {len(evs)} data access events "
                            f"(kinds: {kinds}); assignment is OBSERVED access, "
                            f"never a transfer count"),
                "run_id": run_id, "evidence_revision": rev,
                "access_events_total": len(evs), "kinds": kinds,
                "events": slim,
                "next": ["query_runtime(action=data_lineage, run_id=…)"]}
    if action == "data_lineage":
        lin = data.get("lineage", {})
        edges, lws = [], []
        for region, blk in lin.items():
            for e in blk.get("edges", []):
                edges.append({**e, "region": region})
            for lw in blk.get("last_writer", []):
                lws.append({**lw, "region": region})
        return {"summary": (f"{run_id}: {len(edges)} OBSERVED_DATA_DEPENDENCE "
                            f"edges (writer→reader via runtime location), "
                            f"{len(lws)} observed_last_writer records"),
                "run_id": run_id, "evidence_revision": rev,
                "lineage_edges": edges[:80], "last_writer": lws[:60],
                "next": ["query_runtime(action=data, run_id=…)"]}
    # action == "dgesv"
    aud = RTD_DIR / "dgesv_audit.json"
    if not aud.exists():
        return {"summary": "DGESV_AUDIT_NOT_AVAILABLE", "run_id": run_id}
    a = json.loads(aud.read_text())
    key = "W1" if "W1" in str(run_id).upper() else "W2"
    ent = a.get(key) or a.get("W1")
    return {"summary": (f"{key}: {ent['dgesv_calls']} DGESV call(s); "
                        f"N={[c['N'] for c in ent['calls']]} NRHS=1 "
                        f"LDA=LDB={[c['LDA'] for c in ent['calls']]}; "
                        f"A shapes={[c['A_shape'] for c in ent['calls']]} "
                        f"(logical_size={[c['A_byte_size'] for c in ent['calls']]} B)"),
            "run_id": run_id, "evidence_revision": rev,
            "calls": ent["calls"],
            "source_spans": [_dgesv_span()] if _dgesv_span() else [],
            "w1_vs_w2": a.get("w1_vs_w2")}


def _rt_members_to_stage() -> dict[str, str]:
    """observed symbol name -> semantic stage id (from CRM semantic stage members)."""
    out: dict[str, str] = {}
    for st in _fs_stages():
        mem = st.get("members") or {}
        for cid in mem.get("canonical_symbol_ids", []):
            out[cid.split(":", 1)[-1]] = st["semantic_stage_id"]
        for nid in mem.get("node_ids", []):
            if nid.startswith("cmd:crm:"):
                out["P" + nid.split(":", 2)[-1]] = st["semantic_stage_id"]
    return out


def _rt_causes_action(run_id: str, r: dict, p: dict) -> dict:
    """PERF-CAUSE0: evidence-backed cause analysis of a hot path (spec §29-§32).

    Read-only projection of analysis_tournament/perf_cause0/cause_findings.json.
    A cause is a finding with hypothesis / evidence / counterevidence / verdict /
    source spans; correlation alone is never reported as a cause.
    """
    cf = PC0_DIR / "cause_findings.json"
    if not cf.exists():
        return {"summary": "PERF_CAUSE_NOT_AVAILABLE: no perf_cause0 artifacts; "
                           "run scripts/perf_cause0_*.py first",
                "run_id": run_id}
    d = json.loads(cf.read_text())
    fs = list(d.get("findings", []))
    cid = str(p.get("cause_id") or "")
    fid = str(p.get("finding_id") or "")
    if cid:
        fs = [f for f in fs if f["cause_id"] == cid]
    if fid:
        fs = [f for f in fs if fid in (f.get("affected_finding_id") or [])]
    counts = {}
    for f in fs:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
    return {
        "summary": (f"PERF-CAUSE0 {d.get('workload', {}).get('workload_id', 'W1')}: "
                    f"{len(fs)} cause findings "
                    f"({', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}); "
                    f"purity of Maxwell: {d.get('purity_verdict')}. Each finding "
                    f"carries evidence AND counterevidence; verdict is never "
                    f"derived from hotness alone."),
        "run_id": run_id, "revision": r.get("revision"),
        "workload": d.get("workload"),
        "purity_verdict": d.get("purity_verdict"),
        "verdict_counts": counts,
        "findings": fs,
        "summary_table": d.get("summary_table", []),
        "note": "cause kinds: ALGORITHMIC_MULTIPLICITY / EXACT_RECOMPUTATION / "
                "LOOP_INVARIANT_RECOMPUTATION / FUNCTION_COST / "
                "DATA_DEPENDENCE_CONSTRAINT / COMPILER_ALREADY_OPTIMIZED / "
                "MEASUREMENT_ARTIFACT / UNKNOWN",
    }


def t_query_runtime(p: dict) -> dict:
    action = str(p.get("action") or "runs")
    if action not in RT_ACTIONS:
        return _err("UNKNOWN_ACTION", action, list(RT_ACTIONS),
                    "use one of the actions above (see action enum)",
                    f"valid actions: {', '.join(RT_ACTIONS)}")
    if action == "runs":
        out = []
        rd = RT_DIR.joinpath("runs")
        if rd.exists():
            for f in sorted(rd.glob("run-*.json")):
                d = json.loads(f.read_text())
                r = d["run"]
                out.append({"run_id": r["run_id"], "workload_id": r["workload_id"],
                            "scenario_label": r.get("scenario_label"),
                            "trace_bytes": r.get("trace_bytes"),
                            "records": r.get("records"),
                            "wall_seconds": (r.get("stats") or {}).get("wall_seconds"),
                            "exit_code": r.get("exit_code"),
                            "evidence_revision": r.get("evidence_revision"),
                            "integrity_status": r.get("integrity", {}).get("status"),
                            "invocations_kept": len(d.get("invocations", [])),
                            "observed_edges": len(d.get("edges", []))})
        return {"summary": f"{len(out)} runtime runs (instrumented FAC executions)",
                "runs": out,
                "next": ["query_runtime(action=stages, run_id=…)",
                         "query_runtime(action=symbol, run_id=…, symbol=…)"]}
    run_id = str(p.get("run_id") or "W1")
    d = _rt_run(run_id)
    if d is None:
        return {"summary": f"RUN_NOT_FOUND: no runtime run {run_id!r}",
                "error": {"code": "RUN_NOT_FOUND", "requested": run_id,
                          "candidates": [f.stem.removeprefix("run-")
                                         for f in RT_DIR.joinpath("runs").glob("run-*.json")][:8],
                          "hint": "use query_runtime(action=runs) to list run ids"}}
    r = d["run"]
    if action == "stages":
        ss = d.get("stage_stats", {})
        return {"summary": (f"{run_id}: {len(ss)} stages observed; "
                            f"invocations retained={len(d.get('invocations', []))}; "
                            f"integrity={r.get('integrity', {}).get('status')} "
                            f"(unclosed_at_end={r.get('integrity', {}).get('unclosed_at_end', 0)} "
                            f"= legitimate exit() frames)"),
                "run_id": run_id, "evidence_revision": r.get("evidence_revision"),
                "stage_stats": ss,
                "next": ["query_runtime(action=invocations, run_id=…, stage=sid)",
                         "query_runtime(action=symbol, run_id=…, symbol=…)"]}
    if action == "alignment":
        pth = RT_DIR / "static_runtime_alignment.json"
        if not pth.exists():
            return {"summary": "ALIGNMENT_NOT_AVAILABLE: static_runtime_alignment.json missing"}
        al = json.loads(pth.read_text())
        return {"summary": (f"static flow edges={al['static_edges']}; "
                            f"verdicts={al['edge_verdicts']}; "
                            f"runtime edges with no static flow edge="
                            f"{al['observed_without_static_edge_count']}"),
                "alignment": {"edge_verdicts": al["edge_verdicts"],
                              "observed_without_static_edge_count":
                                  al["observed_without_static_edge_count"],
                              "sample_not_observed_this_run":
                                  [e["edge_id"] for e in al["not_observed"][:8]]}}
    if action in ("data", "data_access", "data_lineage", "dgesv"):
        return _rtd_action(action, run_id, d, r)
    if action in ("performance", "hotspots", "critical_path"):
        return _rt_perf_action(action, run_id, r)
    if action == "performance_causes":
        return _rt_causes_action(run_id, r, p)

    stage_id = str(p.get("stage") or "")
    symbol = str(p.get("symbol") or "")
    rows = d.get("invocations", [])
    if stage_id:
        m2s = _rt_members_to_stage()
        names = {n for n, s in m2s.items() if s == stage_id}
        rows = [x for x in rows if x["symbol_name"].lstrip("_") in names]
    if symbol:
        rows = [x for x in rows if x["symbol_name"].lstrip("_") == symbol.lstrip("_")]
    rows = rows[:80]
    compact = [{k: x.get(k) for k in ("invocation_id", "symbol_name", "start_ts",
                                      "duration_ms", "exclusive_ms", "binding",
                                      "canonical_function_id", "file", "line",
                                      "parent_invocation_id", "call_site_file",
                                      "call_site_line")} for x in rows]
    return {"summary": f"{run_id}: {len(compact)} invocation rows (OBSERVED; static MAY != observed)",
            "run_id": run_id, "evidence_revision": r.get("evidence_revision"),
            "invocations": compact,
            "next": ["explain_evidence(canonical_id=…)", "get_symbol(canonical_id=…)"]}


def t_query_data_interface(p: dict) -> dict:
    action = str(p.get("action") or "summary")
    if action not in DI_ACTIONS:
        return _err("UNKNOWN_ACTION", action, list(DI_ACTIONS),
                    "use one of the actions above (see action enum)",
                    f"valid actions: {', '.join(DI_ACTIONS)}")
    if action == "capability":
        cap = _di_capability().get("fac", {})
        return {"summary": f"DATA_INTERFACE={cap.get('DATA_INTERFACE', 'PARTIAL')} "
                           f"({len(_di_by_fn())} functions, {len(_di_ports())} ports, "
                           f"{len(_di_bindings())} bindings)",
                "per_lane": _di_capability_row("fac_c")}
    if action == "functions":
        rows = []
        for fn, ports in sorted(_di_by_fn().items()):
            s = _ports_summary(ports)
            rows.append({"function": fn, "file": ports[0].get("file"),
                         "ports": s["total"], "inputs": s["inputs"], "outputs": s["outputs"],
                         "inouts": s["inouts"], "vectors": s["vectors"], "matrices": s["matrices"],
                         "symbolic": s["symbolic"], "partial": s["partial"],
                         "shape_unknown": s["shape_unknown"],
                         "summary_text": _ports_summary_text(s),
                         "resource_uri": f"rintel://data-interface/symbol/{fn}"})
        return {"summary": f"{len(rows)} functions in the DATA-INTERFACE0 parse set",
                "functions": rows, "next": ["query_data_interface(action=ports)",
                                            "get_symbol(include_ports=true)"]}
    if action == "ports":
        fn = str(p.get("function", ""))
        ports = _di_by_fn().get(fn)
        if not ports:
            cand = [k for k in sorted(_di_by_fn()) if fn.lower() in k.lower()][:8]
            return {"summary": f"FUNCTION_NOT_FOUND: '{fn}' not in parse set",
                    "error": {"code": "FUNCTION_NOT_FOUND", "requested": fn,
                              "candidates": cand,
                              "hint": "use query_data_interface(action=functions) to list the "
                                      "parse set; search_symbols(query=...) also covers the "
                                      "kernel universe"}}
        summ = _ports_summary(ports)
        out = {"summary": f"{fn}: {_ports_summary_text(summ)}",
               "function": fn, "file": ports[0].get("file"),
               "ports_summary": summ}
        if bool(p.get("include_ports")):
            out["ports"] = [_port_record(q) for q in ports]
        return out
    if action == "bindings":
        rows = []
        for b in _di_bindings():
            rows.append({"binding_id": b["binding_id"],
                         "source_function": b.get("caller_symbol"),
                         "source_actual": b.get("caller_expr"),
                         "target_function": b.get("callee_symbol"),
                         "target_port": b.get("callee_port"),
                         "kind": b.get("kind"), "callsite_line": b.get("callsite_line"),
                         "truth_class": b.get("truth_class"), "coverage": b.get("coverage"),
                         "resource_uri": f"rintel://binding/{b['binding_id']}"})
        return {"summary": f"{len(rows)} PortBinding(s) from real FAC callsites (positional)",
                "bindings": rows[: int(p.get("limit", 40))],
                "next": ["explain_evidence(binding_id=…)", "read_resource(rintel://binding/…)"]}
    if action == "module":
        mod = str(p.get("module", ""))
        mods = _di_modules()
        if mod not in mods:
            cand = [k for k in mods if mod in k or k.endswith(mod)]
            if not cand:
                return {"summary": f"MODULE_NOT_FOUND: '{mod}' has no data interface",
                        "error": {"code": "MODULE_NOT_FOUND", "requested": mod,
                                  "candidates": sorted(mods)[:8],
                                  "hint": "use query_data_interface(action=functions) to find a "
                                          "module/file name first"}}
            mod = cand[0]
        return {"summary": f"module {mod}: {len(mods[mod])} boundary ports",
                "module": mod, **_boundary_record(mods[mod])}
    if action == "region":
        rid = str(p.get("region_id", ""))
        return t_query_flow({"action": "region", "region_id": rid,
                             "include_interfaces": True})
    # summary
    fn = _di_by_fn()
    total = [q for ps in fn.values() for q in ps]
    s = _ports_summary(total)
    return {"summary": f"DATA-INTERFACE0: {len(fn)} functions, {len(total)} ports, "
                       f"{len(_di_bindings())} bindings; {_ports_summary_text(s)}",
            "functions": len(fn), "ports": len(total), "bindings": len(_di_bindings()),
            "shape_status_counts": {"resolved": s["resolved"], "symbolic": s["symbolic"],
                                    "partial": s["partial"], "unknown": s["shape_unknown"]},
            "symbolic_ports": [q.get("port_id") for q in total
                               if q.get("shape_status") == "SYMBOLIC"][:20],
            "next": ["query_data_interface(action=functions)",
                     "query_data_interface(action=bindings)"]}


# ---------------------------------------------------------------------------
# MCP-ERROR-CONTRACT-FIX0 (single-source contract §16): spec['params'] in TOOLS
# is the ONLY source of truth (schema + description + validation co-located).
# Every argument is validated at the boundary in mcp_call() before the tool
# runs. Client mistakes return a structured application error — NEVER a
# JSON-RPC -32603, NEVER a silent null.
# ---------------------------------------------------------------------------

DI_ACTIONS = ("summary", "capability", "functions", "ports", "bindings", "module", "region")
SELECTOR_IDS = ("fact_id", "edge_id", "entity_id", "port_id", "binding_id",
                "transfer_id", "semantic_stage_id")

# finite, curated semantic-equivalent hints (§6): the target is ALWAYS a real
# field of the tool (difflib covers the remaining typos with 0.55 cutoff).
ALIAS_HINTS = {
    "pattern": ("query", "name search is a substring `query`"),
    "module": ("root", "module/file/function scope is expressed as a `root` value"),
    "flow": ("flow_id", "the flow identifier param is `flow_id`"),
    "region": ("region_id", "the region identifier param is `region_id`"),
    "name": ("canonical_id", "symbol lookup needs `canonical_id` — discover it via "
                             "search_symbols(query=...) first"),
    "op": ("ops", "design patch operations go in `ops` (list)"),
}


def _did_you_mean(param: str, fields: list[str]) -> str | None:
    if param in ALIAS_HINTS and ALIAS_HINTS[param][0] in fields:
        return ALIAS_HINTS[param][0]
    m = difflib.get_close_matches(param, fields, n=1, cutoff=0.55)
    return m[0] if m else None


def contract_issues(tool: str, spec: dict, args: dict) -> list[dict]:
    """validate args against the single schema source; returns [] when OK."""
    issues: list[dict] = []
    params: dict = spec["params"]
    rules: dict = spec.get("rules") or {}
    present = {k for k, v in args.items() if v is not None}
    # 1) unknown parameters -> did_you_mean from real schema fields
    for k in args:
        if k not in params:
            issue = {"code": "UNKNOWN_ARGUMENT",
                     "message": f"'{k}' is not a parameter of {tool}",
                     "parameter": k}
            dym = _did_you_mean(k, list(params))
            if dym:
                issue["did_you_mean"] = dym
                issue["hint"] = ALIAS_HINTS.get(k, (None, ""))[1] or f"did you mean `{dym}`?"
            else:
                issue["hint"] = (f"valid parameters: {', '.join(sorted(params))} — "
                                 "the tools/list schema is the contract")
            issues.append(issue)
    # 2) required (type without trailing ?) — key absent, null, or empty string
    for k, typ in params.items():
        if typ.endswith("?"):
            continue
        v = args.get(k)
        if k not in present or (isinstance(v, str) and not v.strip()):
            issues.append({"code": "MISSING_REQUIRED_ARGUMENT",
                           "message": f"parameter '{k}' is required",
                           "parameter": k, "required": [k]})
    # 3) type sanity (ints accept numeric strings; bools accept true/false/1/0)
    for k, typ in params.items():
        base = typ.rstrip("?")
        if k not in present:
            continue
        v = args[k]
        if base == "str" and not isinstance(v, str):
            issues.append({"code": "INVALID_ARGUMENT_TYPE", "parameter": k,
                           "received": type(v).__name__, "expected": "str",
                           "message": f"'{k}' expects str, got {type(v).__name__}"})
        elif base == "int" and not (isinstance(v, int) and not isinstance(v, bool)):
            if not (isinstance(v, str) and v.strip().isdigit()):
                issues.append({"code": "INVALID_ARGUMENT_TYPE", "parameter": k,
                               "received": type(v).__name__, "expected": "int",
                               "message": f"'{k}' expects int, got {type(v).__name__}"})
        elif base == "bool" and not isinstance(v, bool):
            if not (isinstance(v, str) and v.lower() in ("true", "false", "1", "0")):
                issues.append({"code": "INVALID_ARGUMENT_TYPE", "parameter": k,
                               "received": type(v).__name__, "expected": "bool",
                               "message": f"'{k}' expects bool, got {type(v).__name__}"})
        elif base == "list" and not isinstance(v, (list, tuple)):
            issues.append({"code": "INVALID_ARGUMENT_TYPE", "parameter": k,
                           "received": type(v).__name__, "expected": "list",
                           "message": f"'{k}' expects list, got {type(v).__name__}"})
    # 4) enums (declared per-param in rules["enums"])
    for param, allowed in (rules.get("enums") or {}).items():
        if param in present and isinstance(args[param], str) and args[param] not in allowed:
            issues.append({"code": "INVALID_ENUM", "parameter": param,
                           "received": args[param], "allowed": list(allowed),
                           "message": f"'{args[param]}' is not a valid {param}",
                           "hint": f"allowed values: {' | '.join(allowed)} — see tool description"})
    # 5) one-of groups (at least one present & non-empty)
    for group in rules.get("one_of") or []:
        if not [k for k in group if k in present and str(args[k]).strip()]:
            issues.append({"code": "MISSING_REQUIRED_ARGUMENT",
                           "message": f"one of {'/'.join(group)} is required",
                           "required": list(group),
                           "hint": f"provide one of: {', '.join(group)}"})
    # 6) mutex groups (at most one present)
    for group in rules.get("mutex") or []:
        chosen = [k for k in group if k in present and str(args[k]).strip()]
        if len(chosen) > 1:
            issues.append({"code": "AMBIGUOUS_ARGUMENTS",
                           "parameters": list(chosen),
                           "message": f"ambiguous selectors: {', '.join(chosen)}",
                           "hint": "provide exactly one selector; combination semantics are not "
                                   "defined (see tool description)"})
    return issues


def contract_fail(tool: str, issues: list[dict]) -> dict:
    first = issues[0]
    return {"summary": f"{first['code']}: {first['message']}",
            "error": first, "issues": issues[:4],
            "contract_note": ("arguments must match the schema (params) in tools/list; use "
                              "error.did_you_mean / error.hint to fix the call")}


def mcp_call(tool: str, args: dict | None) -> dict:
    """boundary: validate against the schema, then dispatch (§16 single source)."""
    spec = TOOLS[tool][1]
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    issues = contract_issues(tool, spec, clean)
    if issues:
        return contract_fail(tool, issues)
    return TOOLS[tool][0](clean)


def t_classify_evidence_authority(p: dict) -> dict:
    """Read-only classification of caller-supplied envelopes; no retrieval."""
    from rintel.evidence_authority import (
        CanonicalStateRef,
        classify_evidence,
        envelope_from_wire,
        validate_bridge,
    )

    raw_envelopes = p.get("envelopes") or []
    if len(raw_envelopes) > 100:
        return {
            "summary": "LIMIT_EXCEEDED: at most 100 evidence envelopes",
            "error": {
                "code": "LIMIT_EXCEEDED", "parameter": "envelopes",
                "received": len(raw_envelopes), "allowed": ["0..100"],
                "message": "at most 100 evidence envelopes may be classified",
            },
        }
    try:
        envelopes = tuple(envelope_from_wire(item) for item in raw_envelopes)
        state = CanonicalStateRef(
            revision_id=p["canonical_revision_id"],
            state_hash=p["canonical_state_hash"],
            fact_refs=tuple(p.get("canonical_fact_refs") or ()),
        )
        bridges = tuple(validate_bridge(
            source_ref=item["source_ref"],
            target_ref=item["target_ref"],
            basis=item["basis"],
            provenance=item["provenance"],
        ) for item in (p.get("bridges") or ()))
        payload = classify_evidence(envelopes, state, bridges=bridges).to_dict()
    except (KeyError, TypeError, ValueError, LookupError) as exc:
        return {
            "summary": f"INVALID_EVIDENCE_ENVELOPE: {exc}",
            "error": {
                "code": "INVALID_EVIDENCE_ENVELOPE",
                "message": str(exc),
                "hint": "supply registry-known lanes and fail-closed envelope fields",
            },
        }
    return {
        "summary": (
            f"classified {len(envelopes)} supplied evidence envelope(s); "
            "canonical state was not modified"),
        "operation": "CLASSIFY_SUPPLIED_EVIDENCE_ONLY",
        "read_only": True,
        **payload,
    }


def _name_candidates(name: str, limit: int = 8) -> list[str]:
    """fuzzy suggestions across frozen lanes + DATA-INTERFACE0 + kernel universe."""
    names: set[str] = set()
    for lane in _available_lanes():
        names.update(_byname(bundle(lane)).keys())
    names.update(_di_by_fn().keys())
    try:
        names.update((_kernel_light().get("symbols_light") or {}).keys())
    except Exception:
        pass
    names = {x for x in names if len(x) >= 2}
    ranked = sorted(names, key=lambda x: (
        0 if (x.lower().startswith(name.lower()) or name.lower().startswith(x.lower())) else 1,
        abs(len(x) - len(name))))
    if not ranked:
        return []
    exact = [x for x in ranked if x.lower() == name.lower()]
    top = (exact if exact else ranked)[:limit]
    return top


def _edge_ids(e: dict) -> list[str]:
    """accepted id forms for one topology edge (kind-prefixed + bare)."""
    src = e["source"].split(":")[-1]
    tgt = e["target"].split(":")[-1]
    return [f"{e.get('kind', '')}:{src}->{tgt}", f"{src}->{tgt}"]


def _split_edge_id(edge_id: str) -> tuple[str, str]:
    """CALL:SRC->TGT / SRC->TGT -> (src, tgt) — kind prefix via rsplit."""
    if "->" not in edge_id:
        return "", ""
    lhs, tgt = edge_id.split("->", 1)
    if ":" in lhs:
        _, src = lhs.rsplit(":", 1)
    else:
        src = lhs
    return src, tgt


def _edge_recovery(edge_id: str) -> dict:
    """§10/§11: no direct edge evidence record — recover via witness facts, or
    report the real candidates + hint. Absence of a direct record is surfaced,
    never swallowed into '0 evidence record(s)'."""
    src, tgt = _split_edge_id(edge_id)
    cand: list[tuple[str, dict]] = []
    for lane in _available_lanes():
        b = bundle(lane)
        for e in _edges(b):
            if not e.get("kind"):
                continue
            if src and f"{e['source'].split(':')[-1]}" != src:
                continue
            if tgt and tgt != "[Unknown Dynamic Target]" and \
                    f"{e['target'].split(':')[-1]}" != tgt:
                continue
            cand.append((lane, e))
    if cand:
        wids: list[str] = []
        for _, e in cand:
            for w in (e.get("representative_witnesses") or []):
                if w.get("fact_id") and w["fact_id"] not in wids:
                    wids.append(w["fact_id"])
        _, e0 = cand[0]
        rec = {"kind": e0.get("kind"),
               "source": e0["source"].split(":")[-1],
               "target": e0["target"],
               "truth_class": e0.get("truth_class"),
               "coverage": e0.get("coverage"),
               "edge_id": _edge_ids(e0)[0]}
        if wids:
            return {"summary": f"no direct edge evidence record for '{edge_id}'; edge exists as "
                               f"{rec['edge_id']} — resolved via witness facts",
                    "edge_record": {"direct_record_found": False, "edge": rec,
                                    "recovery": {"resolved_via_witness": True,
                                                 "witness_fact_ids": wids[:8],
                                                 "hint": "call explain_evidence(fact_id=...)"}},
                    "next": ["explain_evidence(fact_id=...)", "read_resource"]}
        return {"summary": f"no direct edge evidence record for '{edge_id}'; edge exists as "
                           f"{rec['edge_id']} but carries no witness facts",
                "edge_record": {"direct_record_found": False, "edge": rec,
                                "recovery": {"resolved_via_witness": False,
                                             "witness_fact_ids": [],
                                             "hint": "read the call site via "
                                                     "read_resource(rintel://file/...) for "
                                                     "source-level evidence"}},
                "next": ["read_resource", "query_topology"]}
    return {"summary": f"EDGE_NOT_FOUND: '{edge_id}' matches no edge in the topology",
            "error": {"code": "EDGE_NOT_FOUND", "requested": edge_id,
                      "candidates": [],
                      "hint": "use query_topology to list edges (edge_id field); drill via a "
                              "witness fact_id (witness_fact_ids in the topology response)"}}


# ---------------------------------------------------------------------------
# minimal MCP stdio server (newline-delimited JSON-RPC)
# ---------------------------------------------------------------------------

TOOLS: dict[str, tuple[Callable, dict]] = {
    "repo_status": (t_repo_status, {
        "name": "repo_status", "description": "What Rintel knows: published repositories and available lanes, snapshot, languages, "
                                              "per-relation capabilities, flow coverage, known limitations. "
                                              "Start here.", "params": {"repo": "str?"}}),
    "search_symbols": (t_search_symbols, {
        "name": "search_symbols", "description": "Search files/modules/functions/subroutines by name "
                                                 "across registered published repositories and available frozen lanes — "
                                                 "0 matches never means the symbol does not exist "
                                                 "(DATA-INTERFACE0 + kernel universes are also searched). "
                                                 "Param contract: query (substring) OR file (list one "
                                                 "file's symbols) — at least one required. "
                                                 "Params: query(str?), kind?, language?, file?, limit(int=12).",
        "params": {"query": "str?", "kind": "str?", "language": "str?", "file": "str?", "repo_id": "str?",
                   "limit": "int?"},
        "rules": {"one_of": [["query", "file"]]}}),
    "get_symbol": (t_get_symbol, {
        "name": "get_symbol", "description": "Full context for one canonical symbol: identity, file/span, "
                                              "parent module, call summary, flow memberships, source URI. "
                                              "Function/subroutine ports from DATA-INTERFACE0: compact "
                                              "ports_summary always; include_ports=true returns the full "
                                              "ports[] (direction/dtype/rank/shape/shape_status/"
                                              "byte_size/byte_size_expression/evidence). Also resolves the "
                                              "DATA-INTERFACE0 parse set (DSBEV/DGER/… are real FAC "
                                              "subroutines outside the frozen fac_c lane). "
                                              "Params: canonical_id(str), include_ports(bool?=false).",
        "params": {"canonical_id": "str", "include_ports": "bool?", "repo_id": "str?"}}),
    "query_topology": (t_query_topology, {
        "name": "query_topology", "description": "Neighborhood topology for an exact published repo_id or available frozen lane: relations (CALL/DATA/…), direction "
                                                 "in/out/both, depth 1-2, returns nodes+edges with truth, "
                                                 "coverage, unknown endpoints, witness fact ids, capability "
                                                 "(never upgrades PARTIAL). DATA edges (or "
                                                 "include_data_interfaces=true) carry edge-level port info: "
                                                 "port_bindings[] from real PortBindings (source_actual → "
                                                 "target_port), else source_port/target_port = UNKNOWN — "
                                                 "ports are NEVER inferred by name. Module/file scope "
                                                 "queries also get boundary_interfaces. Params: lane "
                                                 "(enum fac_c|jpl), root, relations, direction, depth, "
                                                 "limit, include_data_interfaces(bool?).",
        "params": {"lane": "str?", "repo_id": "str?", "root": "str", "relations": "list?", "direction": "str?",
                   "depth": "int?", "limit": "int?", "include_data_interfaces": "bool?"},
        "rules": {"enums": {"lane": LANES}}}),
    "query_runtime": (t_query_runtime, {
        "name": "query_runtime", "description": "RUNTIME-TRACE0/RUNTIME-DATA0 query: what a REAL "
                                                "instrumented FAC run actually executed (OBSERVED), as "
                                                "opposed to static MAY. Actions (enum): runs (default) | "
                                                "stages(run_id=…) | invocations(run_id=…, stage=… | "
                                                "symbol=…) | alignment (static flow vs observed; "
                                                "NOT_OBSERVED_THIS_RUN = static MAY, never proof of "
                                                "impossibility) | symbol(run_id=…, symbol=…) | "
                                                "data(run_id=…) observed runtime data objects "
                                                "(shapes RESOLVED, logical_size ≠ transfer volume, §17) | "
                                                "data_access(run_id=…, kind=…, limit=…) | "
                                                "data_lineage(run_id=…) | dgesv(run_id=…) | "
                                                "performance(run_id=…) PERF-TOPO0 exact per-function "
                                                "counts/durations/percentiles + stages + edges (full "
                                                "distribution, not trimmed rows) | "
                                                "hotspots(run_id=…) four SEPARATE rankings (total "
                                                "time / exclusive time / call count / mean latency — "
                                                "never a single score) | "
                                                "critical_path(run_id=…) single-thread chain + "
                                                "top-level segments | "
                                                "performance_causes(run_id=…, cause_id=?, finding_id=?) "
                                                "PERF-CAUSE0 cause analysis of a hot path: hypothesis, "
                                                "evidence, counterevidence, verdict "
                                                "(SUPPORTED/PARTIAL/DISPROVED/UNKNOWN) and exact source "
                                                "spans per cause — hotness alone is never a cause. "
                                                "logical_access_weight is a weight, "
                                                "never bytes_transferred/bandwidth (§7). "
                                                "Integrity: CALL/RETURN pairing, no fabricated frames.",
        "params": {"action": "str?", "run_id": "str?", "stage": "str?",
                   "symbol": "str?", "cause_id": "str?", "finding_id": "str?"},
        "rules": {"enums": {"action": RT_ACTIONS}}}),
    "query_data_interface": (t_query_data_interface, {
        "name": "query_data_interface", "description": "Aggregate DATA-INTERFACE0 query (one tool; the "
                                                       "per-object details stay in get_symbol/"
                                                       "explain_evidence/read_resource). Actions (enum): "
                                                       "summary (omitted default) | capability (per-language honesty) | "
                                                       "functions | ports(function=…) | bindings | "
                                                       "module(module=file) | region(region_id=…). "
                                                       "Never infers shapes/ranks/bindings — only exposes "
                                                       "generated derived objects.",
        "params": {"action": "str?", "function": "str?", "module": "str?",
                   "region_id": "str?", "limit": "int?"},
        "rules": {"enums": {"action": DI_ACTIONS}}}),
    "find_path": (t_find_path, {
        "name": "find_path", "description": "Candidate CALL path A→B in a published repo_id or frozen lane with verdict FOUND / PARTIAL / UNKNOWN; "
                                            "unresolved targets are gaps, never proof of absence.",
        "params": {"lane": "str?", "repo_id": "str?", "source": "str", "target": "str", "relations": "list?", "max_hops": "int?"},
        "rules": {"enums": {"lane": LANES}}}),
    "explain_evidence": (t_explain_evidence, {
        "name": "explain_evidence", "description": "Why does Rintel believe X. Supported single-id "
                                                    "selectors (all public): fact_id, edge_id, "
                                                    "entity_id (canonical evidence), port_id "
                                                    "(formal declaration drill), binding_id (caller "
                                                    "actual → callee formal port), transfer_id "
                                                    "(transfer_kind; pass-by-reference is NOT a memory "
                                                    "copy), semantic_stage_id (annotation plane: "
                                                    "memberships + evidence_refs; NOT evidence).",
        "params": {"fact_id": "str?", "edge_id": "str?", "entity_id": "str?", "repo_id": "str?",
                   "port_id": "str?", "binding_id": "str?", "transfer_id": "str?",
                   "semantic_stage_id": "str?"},
        "rules": {"one_of": [SELECTOR_IDS], "mutex": [SELECTOR_IDS]}}),
    "classify_evidence_authority": (t_classify_evidence_authority, {
        "name": "classify_evidence_authority",
        "description": "Read-only classification of caller-supplied evidence envelopes "
                       "under the versioned Rintel lane registry. Does not retrieve, "
                       "admit, reconcile, or publish canonical facts. Preserves truth, "
                       "resolution, coverage, modality, and authority independently. "
                       "At most 100 envelopes. Params: envelopes, immutable canonical "
                       "revision/state refs, optional fact refs and ABI bridges.",
        "params": {"envelopes": "list", "canonical_revision_id": "str",
                   "canonical_state_hash": "str", "canonical_fact_refs": "list?",
                   "bridges": "list?"}}),
    "query_flow": (t_query_flow, {
        "name": "query_flow", "description": "FLOW-INFER0 + semantic projection. Actions (enum: "
                                             f"{'|'.join(FLOW_ACTIONS)}): "
                                             "list (no flow_id; returns every real flow_id — discover first), "
                                             "get (compact overview: region/scenario/guard/shared/semantic "
                                             "summaries + previews; guards flattened ONLY with "
                                             "include_guards=true, paged), "
                                             "regions (region catalog), region (drill one), "
                                             "scenarios, project (scenario projection), "
                                             "semantic (semantic stage catalog). Unknown action/flow/region "
                                             "return structured codes (FLOW_NOT_FOUND/REGION_NOT_FOUND/"
                                             "UNKNOWN_ACTION) + candidates + hint. Omitted action/"
                                             "flow_id = list. Params: flow_id, region_id, scenario_id, "
                                             "include_interfaces(bool?), include_semantic(bool?), "
                                             "include_guards(bool?), include_members(bool?), "
                                             "limit(int?), cursor(str?).",
        "params": {"action": "str?", "flow_id": "str?", "region_id": "str?", "scenario_id": "str?",
                   "include_interfaces": "bool?", "include_semantic": "bool?",
                   "include_guards": "bool?", "include_members": "bool?",
                   "limit": "int?", "cursor": "str?"},
        "rules": {"enums": {"action": FLOW_ACTIONS}}}),
    "create_design": (t_create_design, {
        "name": "create_design", "description": "Open a DesignChange through the same lifecycle seam "
                                                "used by REST/UI; returns change_id.",
        "params": {"repo_id": "str?", "name": "str?"}}),
    "design_patch": (t_design_patch, {
        "name": "design_patch", "description": "Preview or plan TO-BE claims on a DesignChange. Apply "
                                               "creates an immutable DESIGN_ANNOTATION revision and runs "
                                               "Design DRC; it never edits canonical evidence.",
        "params": {"design_id": "str", "change_id": "str?", "mode": "str?", "ops": "list"}}),
    "validate_design": (t_validate_design, {
        "name": "validate_design", "description": "Read Design DRC, expected-vs-actual and LVS results "
                                                  "from the shared DesignChange aggregate.",
        "params": {"design_id": "str", "change_id": "str?", "checks": "list?"}}),
    "get_change_workspace": (t_get_change_workspace, {
        "name": "get_change_workspace",
        "description": "Read the receipt-bound, typed Change Workspace projection "
                       "shared with REST and the Human UI; unknown dimensions stay unknown.",
        "params": {"change_id": "str"}}),
    "get_git_collaboration": (t_get_git_collaboration, {
        "name": "get_git_collaboration",
        "description": "Read Git repository/worktree/merge queue status and the latest "
                       "server-owned merge eligibility projection. This tool cannot merge.",
        "params": {"change_id": "str"}}),
    "get_agent_workspace": (t_get_agent_workspace, {
        "name": "get_agent_workspace",
        "description": "Read an assigned Agent workspace and frozen base/scope using its "
                       "server-issued session. Cannot change scope, approve, or merge.",
        "params": {"workspace_id": "str", "agent_session_token": "str"}}),
    "request_agent_execution": (t_request_agent_execution, {
        "name": "request_agent_execution",
        "description": "Request server-owned BUILD or TEST for an assigned Agent "
                       "workspace at its exact clean Git head. Cannot supply commands, "
                       "approve, merge, or publish canonical evidence.",
        "params": {"workspace_id": "str", "agent_session_token": "str",
                   "profile_id": "str", "kind": "str", "parameters": "list?"}}),
    "read_resource": (t_read_resource, {
        "name": "read_resource", "description": "Read a rintel:// resource (symbol source snippet, "
                                                "file head, evidence or build attempt) — small by default.",
        "params": {"uri": "str"}}),
}

PROTOCOL_VERSION = "2024-11-05"

# RINTEL-DSH0 §1: a deployment may pin a read/analyse tool surface.  The env
# var is the ONLY switch; when it is absent the server exposes every tool
# exactly as before (frozen behaviour).  tools/list is the authority, and a
# call to a non-exposed tool answers with the standard error-contract shape
# instead of silently disappearing.
EXPOSURE_ENV = "RINTEL_MCP_TOOLS"


def exposed_tools() -> set[str] | None:
    raw = (os.environ.get(EXPOSURE_ENV) or "").strip()
    if not raw:
        return None
    names = {n.strip() for n in raw.split(",") if n.strip()}
    return names or None


def _tool_not_exposed(name: str) -> dict:
    allowed = sorted(exposed_tools() or ())
    near = difflib.get_close_matches(name, allowed, n=3, cutoff=0.4)
    return {
        "summary": f"tool '{name}' is not exposed in this deployment",
        "error": {
            "code": "TOOL_NOT_EXPOSED",
            "message": f"'{name}' is not part of the exposed tool surface",
            "parameter": "name",
            "received": name,
            "allowed": allowed,
            "did_you_mean": near[0] if near else None,
            "hint": "call tools/list for the authoritative surface; do not "
                    "substitute another tool for a capability that is not exposed",
        },
    }


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _rpc_error(id_, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def run() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        rid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid,
                   "result": {"protocolVersion": PROTOCOL_VERSION,
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "rintel-mcp", "version": "0.1.0"},
                              # RINTEL-DSH0 §2: the canonical truth contract is
                              # served to every client from instructions.py — the
                              # one place it is written.
                              "instructions": INSTRUCTIONS}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            out = []
            exposed = exposed_tools()
            for _, spec in TOOLS.values():
                if exposed is not None and spec["name"] not in exposed:
                    continue
                schema_props = {}
                for key, typ in spec["params"].items():
                    schema_props[key] = {"type": {"str": "string", "str?": "string",
                                                  "int": "integer", "int?": "integer",
                                                  "list": "array", "list?": "array",
                                                  "bool": "boolean", "bool?": "boolean"}[typ]}
                out.append({"name": spec["name"], "description": spec["description"],
                            "inputSchema": {"type": "object", "properties": schema_props}})
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": out}})
        elif method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            args = (msg.get("params") or {}).get("arguments") or {}
            fn = TOOLS.get(name, (None, None))[0]
            if fn is None:
                _rpc_error(rid, -32601, f"unknown tool {name}")
                continue
            exposed = exposed_tools()
            if exposed is not None and name not in exposed:
                _send({"jsonrpc": "2.0", "id": rid, "result": {"content": [
                    {"type": "text", "text": json.dumps(_tool_not_exposed(name),
                                                        ensure_ascii=False)}]}})
                continue
            try:
                result = mcp_call(name, args)
                if isinstance(result, dict) and "error" in result and "summary" not in result:
                    raise ValueError(result["error"])
                if isinstance(result, dict) and name in (
                        "query_data_interface", "query_topology", "repo_status",
                        "query_runtime"):
                    # XS9: high-level responses carry the published evidence
                    # revision (identical across projections — no mixed view)
                    result.setdefault("evidence_revision", _evidence_revision())
                _send({"jsonrpc": "2.0", "id": rid, "result": {"content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
            except Exception as exc:
                _rpc_error(rid, -32603, f"{type(exc).__name__}: {exc}")
        elif method == "resources/list":
            if exposed_tools() is not None and "read_resource" not in exposed_tools():
                _send({"jsonrpc": "2.0", "id": rid, "result": {"resources": []}})
                continue
            _send({"jsonrpc": "2.0", "id": rid,
                   "result": {"resources": [
                       {"uri": "rintel://symbol/{name}/source",
                        "name": "symbol source snippet", "mimeType": "text/plain"},
                        {"uri": "rintel://file/{path}",
                         "name": "file excerpt", "mimeType": "text/plain"},
                        {"uri": "rintel://published-source/{repo}/{snapshot}/{path}?start=1&end=80",
                         "name": "published repository source", "mimeType": "text/plain"},
                       {"uri": "rintel://port/{port_id}",
                        "name": "DataPort record + evidence", "mimeType": "application/json"},
                       {"uri": "rintel://binding/{binding_id}",
                        "name": "PortBinding record + callsite/formal evidence", "mimeType": "application/json"},
                       {"uri": "rintel://data-interface/symbol/{canonical}",
                        "name": "function ports (full)", "mimeType": "application/json"},
                       {"uri": "rintel://data-interface/module/{module_id}",
                        "name": "module boundary interface", "mimeType": "application/json"},
                       {"uri": "rintel://data-interface/flow-region/{region_id}",
                        "name": "flow region interface", "mimeType": "application/json"},
                       {"uri": "rintel://semantic-flow/{app}",
                        "name": "human-semantic flow projection", "mimeType": "application/json"},
                       {"uri": "rintel://semantic-stage/{stage_id}",
                        "name": "semantic stage + memberships + edges", "mimeType": "application/json"}]}})
        elif method == "resources/read":
            if exposed_tools() is not None and "read_resource" not in exposed_tools():
                _send({"jsonrpc": "2.0", "id": rid, "result": {"contents": [
                    {"type": "text", "text": json.dumps(_tool_not_exposed("read_resource"))}]}})
                continue
            uri = (msg.get("params") or {}).get("uri", "")
            _send({"jsonrpc": "2.0", "id": rid,
                   "result": {"contents": [{"uri": uri, "text": json.dumps(
                       t_read_resource({"uri": uri}), ensure_ascii=False)}]}})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            _rpc_error(rid, -32601, f"method not found: {method}")


if __name__ == "__main__":
    run()
