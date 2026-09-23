"""DATA-INTERFACE0 endpoints (read-only): serve the derived port/shape/
binding artifacts for the Workbench Function/Module/Flow views.

FAC-LIBRARY-COVERAGE0: kernel artifacts are now coverage-scale (~70MB compact
bundle + O(1) index) — loads are cached per artifact AND per file mtime so a
regenerated artifact invalidates automatically (never serves stale data)."""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

TOURNAMENT = Path(__file__).resolve().parents[4] / "analysis_tournament" / "data_interface"
KERNEL_TOURNAMENT = TOURNAMENT.parent / "semantic_substrate1"
ARTIFACTS = ("ports", "shapes", "bindings", "transfers", "module_interfaces",
             "flow_interfaces", "capability")
# kernel-substrate artifacts (SEMANTIC-SUBSTRATE1).  Served by the SAME
# artifact endpoint (no new endpoint surface): {artifact} here maps into the
# kernel dir.  "kernel" (full object bundle) is dev-mode only.
KERNEL_ARTIFACTS = {"kernel": "fac_kernel.json",
                    "kernel_summary": "fac_kernel_summary.json",
                    "support_refs": "support_refs.json",
                    "kernel_index": "fac_kernel_index.json"}
router = APIRouter(prefix="/data-interface", tags=["data-interface"])


@lru_cache(maxsize=16)
def _cached_load(path_str: str, mtime_ns: int) -> dict | list:
    return json.loads(Path(path_str).read_text())


def _load(name: str) -> dict | list:
    if name in KERNEL_ARTIFACTS:
        p = KERNEL_TOURNAMENT / KERNEL_ARTIFACTS[name]
    else:
        p = TOURNAMENT / f"fac_{name}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail={"code": "data_interface_missing",
            "message": f"data-interface artifact '{name}' not generated",
            "details": {"artifact": name}})
    # mtime in the cache key -> a regeneration re-reads automatically
    return _cached_load(str(p), p.stat().st_mtime_ns)


@router.get("")
def index() -> dict:
    return {"items": list(ARTIFACTS) + list(KERNEL_ARTIFACTS),
            "total": len(ARTIFACTS) + len(KERNEL_ARTIFACTS)}


@router.get("/revision")
def revision() -> dict:
    """evidence_revision of the currently published artifact set (sync state;
    'r0' = frozen DATA-INTERFACE0 baseline)."""
    from rintel import sync as S
    return {"evidence_revision": S.current_state().get("revision_id", "r0")}

@router.get("/{artifact}")
def artifact(artifact: str, fn: str | None = Query(
        None, description="UI-REALITY-ALIGN0: optional symbol/file filter; the "
                          "unfiltered artifact is unchanged when omitted")):
    if artifact not in ARTIFACTS and artifact not in KERNEL_ARTIFACTS:
        raise HTTPException(status_code=404, detail={"code": "unknown",
            "message": f"unknown artifact '{artifact}'",
            "details": {"known": list(ARTIFACTS) + list(KERNEL_ARTIFACTS)}})
    data = _load(artifact)
    if fn and isinstance(data, list):
        # Filter by the identity fields the artifacts actually carry; a record
        # that matches on none of them is simply not returned (no guessing).
        keys = ("name", "fn", "file", "canonical_symbol_id", "port_id",
                "caller_symbol", "callee_symbol", "callee_port")
        needle = fn.lower()
        data = [r for r in data if isinstance(r, dict)
                and any(needle in str(r.get(k, "")).lower() for k in keys)]
    elif fn and isinstance(data, dict):
        needle = fn.lower()
        data = {k: v for k, v in data.items() if needle in str(k).lower()}
    return data
