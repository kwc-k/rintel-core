"""RUNTIME-TRACE0: API endpoints for runtime runs, invocations, stage stats,
static↔runtime alignment. All data comes from the pre-computed run artifacts
(analysis_tournament/runtime_trace0/); no decoding happens per-request.

Endpoints:
  GET /api/v1/runtime/runs                       list runs (metadata only)
  GET /api/v1/runtime/runs/{run_id}              full run (rows+hot+stats+edges+stage_stats)
  GET /api/v1/runtime/runs/{run_id}/invocations  filtered: ?symbol=&parent=&limit=500
  GET /api/v1/runtime/stages?run_id=             stage observed stats
  GET /api/v1/runtime/alignment?run_id=          static-runtime alignment (run-agnostic)
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["runtime"])

RT_OUT = Path(__file__).resolve().parents[4] / "analysis_tournament" / "runtime_trace0"
RUNS = RT_OUT / "runs"
RTD_OUT = Path(__file__).resolve().parents[4] / "analysis_tournament" / "runtime_data0"
PC0_OUT = Path(__file__).resolve().parents[4] / "analysis_tournament" / "perf_cause0"


def _run_path(run_id: str) -> Path:
    # run_id forms: "W1" / "run-W1" / "run-w1-v1" (canonical artifacts) — resolve by stem
    key = run_id.lower().removeprefix("run-")
    for f in RUNS.glob("run-*.json"):
        stem = f.stem.removeprefix("run-").lower()
        if stem == key or run_id.lower().startswith(f"run-{stem}"):
            return f
    raise HTTPException(404, f"runtime run {run_id!r} not found")


def _load(run_id: str) -> dict:
    return json.loads(_run_path(run_id).read_text())


def _instrumentation(wid: str) -> dict | None:
    """§13/§16: the trace comes from an instrumented build.  RUNTIME-TRACE0
    verified that its outputs equal the canonical run; the measurement
    equivalence artifact is the authority for that claim — never a guess."""
    p = RT_OUT / "instrumentation_equivalence.json"
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
    except Exception:
        return None
    eq = blob.get(wid) or {}
    if not eq:
        return None
    return {
        "build": "instrumented (-finstrument-functions)",
        "equivalence_failed": eq.get("equivalence_failed"),
        "output_hashes_equal": eq.get("output_hashes_equal") or [],
        "nondeterministic_outputs": eq.get("nondeterministic_outputs") or [],
        "binary_sha256": eq.get("binary_sha256"),
        "evidence": "analysis_tournament/runtime_trace0/instrumentation_equivalence.json",
        "truth_class": "OBSERVED",
        "note": "observations are valid (outputs equivalent); absolute timings are inflated",
    }


def _meta(d: dict) -> dict:
    r = d["run"]
    return {
        "run_id": r["run_id"],
        "workload_id": r["workload_id"],
        "label": r.get("scenario_label", ""),
        "trace_bytes": r.get("trace_bytes"),
        "records": r.get("records"),
        "wall_seconds": (r.get("stats") or {}).get("wall_seconds"),
        "exit_code": r.get("exit_code"),
        "evidence_revision": r.get("evidence_revision"),
        "integrity": {k: v for k, v in r.get("integrity", {}).items()
                      if k not in ("notes",)},
        "integrity_notes": r.get("integrity", {}).get("notes", []),
        "invocations": len(d.get("invocations", [])),
        "distinct_symbols": len(d.get("stats", {})),
        "observed_edges": len(d.get("edges", [])),
        "instrumentation": _instrumentation(r.get("workload_id", "")),
    }


@router.get("/runtime/runs")
def runs():
    out = []
    for p in sorted(RUNS.glob("run-*.json")):
        try:
            out.append(_meta(json.loads(p.read_text())))
        except Exception:
            continue
    return {"runs": out}


@router.get("/runtime/runs/{run_id}")
def run_detail(run_id: str):
    d = _load(run_id)
    return {
        "run": d["run"],
        "invocations": d["invocations"],
        "hot": d["hot"],
        "stats": d["stats"],
        "edges": d["edges"],
        "stage_stats": d.get("stage_stats", {}),
        "meta": _meta(d),
    }


@router.get("/runtime/runs/{run_id}/invocations")
def invocations(run_id: str,
                symbol: str = Query("", description="filter by symbol name"),
                parent: str = Query("", description="filter by parent invocation id"),
                limit: int = Query(500, ge=1, le=2000)):
    d = _load(run_id)
    rows = d["invocations"]
    if symbol:
        rows = [r for r in rows if r["symbol_name"].lstrip("_") == symbol.lstrip("_")]
    if parent:
        rows = [r for r in rows if r.get("parent_invocation_id") == parent]
    return {"invocations": rows[:limit], "total": len(rows), "limit": limit}


@router.get("/runtime/stages")
def stages(run_id: str = Query("W1")):
    d = _load(run_id)
    return {"run_id": d["run"]["run_id"], "stage_stats": d.get("stage_stats", {})}


@router.get("/runtime/alignment")
def alignment():
    p = RT_OUT / "static_runtime_alignment.json"
    if not p.exists():
        raise HTTPException(404, "alignment artifact not generated yet")
    return json.loads(p.read_text())


def rtd_path(run_id: str) -> Path:
    key = run_id.lower().replace("_", "-")
    if key.startswith("run-"):
        key = key[4:]
    key = re.sub(r"-v\d+$", "", key)  # run-w1-v1 -> w1
    for f in RTD_OUT.glob("run-*-data.json"):
        wid = f.name.removeprefix("run-").removesuffix("-data.json").lower()
        if key in (wid, f"run-{wid}"):
            return f
    raise HTTPException(404, f"runtime data for {run_id!r} not found — "
                             f"run analysis_tournament runtime_data0 collect first")


def rtd_load(run_id: str) -> dict:
    return json.loads(rtd_path(run_id).read_text())


@router.get("/runtime/runs/{run_id}/data")
def runtime_data(run_id: str):
    d = rtd_load(run_id)
    objs = []
    for o in d.get("objects", {}).values():
        oc = dict(o)
        oc["region"] = oc.get("region") or oc["canonical_location_id"].split(":")[-2]
        objs.append(oc)
    return {"run_id": d["run_id"], "revision": d.get("revision"),
            "observed_dim": d.get("observed_dim"),
            "observed_m": d.get("observed_m"),
            "dgesv_calls": d.get("dgesv_calls"),
            "objects": objs,
            "locations": list(d.get("locations", {}).values()),
            "bindings": d.get("bindings", []),
            "run_meta": d.get("run_meta", {})}


@router.get("/runtime/runs/{run_id}/data_access")
def runtime_data_access(run_id: str,
                        kind: str = Query("", description="filter operation_kind"),
                        limit: int = Query(200, ge=1, le=1000)):
    d = rtd_load(run_id)
    evs = d.get("events", [])
    if kind:
        evs = [e for e in evs if e["operation_kind"] == kind]
    kinds = {}
    for e in d.get("events", []):
        kinds[e["operation_kind"]] = kinds.get(e["operation_kind"], 0) + 1
    return {"run_id": d["run_id"], "events": evs[:limit],
            "total": len(evs), "kinds": kinds, "limit": limit}


@router.get("/runtime/runs/{run_id}/data_lineage")
def runtime_data_lineage(run_id: str):
    d = rtd_load(run_id)
    return {"run_id": d["run_id"], "lineage": d.get("lineage", {})}


@router.get("/runtime/runs/{run_id}/dgesv")
def runtime_dgesv(run_id: str):
    d = rtd_load(run_id)
    binds = d.get("bindings", [])
    return {"run_id": d["run_id"],
            "observed_dim": d.get("observed_dim"),
            "observed_m": d.get("observed_m"),
            "info_values": d.get("info_values"),
            "source_spans": _dgesv_spans(),
            "bindings": binds}


def _dgesv_spans() -> list:
    """SOURCE-SPAN-COL0 §8: canonical callsite/binding spans (never guessed)."""
    try:
        from rintel.source_span import dgesv_callsite_span
        sp = dgesv_callsite_span("/Users/wu/Documents/dh/a3/fac/fac")
        return [sp] if sp else []
    except Exception:
        return []


# --------------------------------------------------------------------------
# PERF-TOPO0: performance topology (pre-computed perf_topo0 artifacts)
# --------------------------------------------------------------------------

PERF_DIR = Path(__file__).resolve().parents[4] / "analysis_tournament" / "perf_topo0"


def _perf_wid(run_id: str) -> str:
    key = run_id.lower().replace("_", "-")
    if key.startswith("run-"):
        key = key[4:]
    key = re.sub(r"-v\d+$", "", key)
    for cand in (key.upper(), key):
        if (PERF_DIR / f"run-{cand}-perf.json").exists():
            return cand.upper()
        if (PERF_DIR / f"{cand.upper()}_findings.json").exists():
            return cand.upper()
    raise HTTPException(404, f"perf artifacts for {run_id!r} not found — "
                             f"run analysis_tournament perf_topo0 collect first")


def _perf_run(run_id: str) -> dict:
    wid = _perf_wid(run_id)
    return json.loads((PERF_DIR / f"run-{wid}-perf.json").read_text())


def _perf_findings(run_id: str) -> dict:
    wid = _perf_wid(run_id)
    return json.loads((PERF_DIR / f"{wid}_findings.json").read_text())


@router.get("/runtime/runs/{run_id}/performance")
def runtime_performance(run_id: str):
    p = _perf_run(run_id)
    return {
        "run_id": p["run_id"],
        "revision": p.get("revision"),
        "integrity": p.get("integrity"),
        "unclosed_frames": p.get("unclosed_frames", []),
        "top_level": p.get("top_level", []),
        "functions": p.get("functions", []),
        "stages": p.get("stages", []),
        "edges": p.get("edges", []),
        "chain": p.get("chain"),
    }


@router.get("/runtime/runs/{run_id}/hotspots")
def runtime_hotspots(run_id: str):
    wid = _perf_wid(run_id)
    d = json.loads((PERF_DIR / "hotspots.json").read_text())
    return {"wid": wid, **d.get(wid, {})}


@router.get("/runtime/runs/{run_id}/critical_path")
def runtime_critical_path(run_id: str):
    wid = _perf_wid(run_id)
    d = json.loads((PERF_DIR / "critical_paths.json").read_text())
    return {"wid": wid, **d.get(wid, {})}


@router.get("/runtime/runs/{run_id}/data_hotspots")
def runtime_data_hotspots(run_id: str):
    wid = _perf_wid(run_id)
    d = json.loads((PERF_DIR / "data_hotspots.json").read_text())
    return {"wid": wid, "hotspots": d.get(wid, []),
            "note": d.get("note", "")}


@router.get("/runtime/runs/{run_id}/performance_causes")
def runtime_performance_causes(run_id: str, cause_id: str = Query(""),
                              finding_id: str = Query("")):
    """PERF-CAUSE0 cause analysis (read-only projection of cause_findings.json)."""
    cf = PC0_OUT / "cause_findings.json"
    if not cf.exists():
        raise HTTPException(404, "perf_cause0 artifacts not found — run "
                                 "scripts/perf_cause0_*.py first")
    d = json.loads(cf.read_text())
    fs = list(d.get("findings", []))
    if cause_id:
        fs = [f for f in fs if f["cause_id"] == cause_id]
    if finding_id:
        fs = [f for f in fs if finding_id in (f.get("affected_finding_id") or [])]
    return {"run_id": run_id, "stage": d.get("stage"),
            "workload": d.get("workload"),
            "purity_verdict": d.get("purity_verdict"),
            "verdict_counts": d.get("verdict_counts"),
            "summary_table": d.get("summary_table", []),
            "findings": fs}


@router.get("/runtime/runs/{run_id}/findings")
def runtime_findings(run_id: str):
    d = _perf_findings(run_id)
    return {"run_id": d.get("run_id"), "revision": d.get("revision"),
            "maxwell_audit": d.get("maxwell_audit"),
            "root_inclusive_ms": d.get("root_inclusive_ms"),
            "findings": d.get("findings", [])}

