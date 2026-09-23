"""FLOW-SEMANTIC0 endpoints (read-only + semantic-plane edits).

Reads the derived semantic artifacts; user edits (rename/description/status/
order) live in a SEPARATE overlay file — the semantic plane, never canonical
evidence (FS8).  POST is limited to semantic stage fields.
"""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException

TOURNAMENT = Path(__file__).resolve().parents[4] / "analysis_tournament" / "flow_semantic0"
router = APIRouter(prefix="/semantic-flow", tags=["flow-semantic"])

OVERLAY_FIELDS = ("display_name", "description", "status", "layout_order", "extra_note")


def _load(name: str) -> list | dict | None:
    p = TOURNAMENT / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _overlay() -> dict:
    return _load("fac_user_edits.json") or {}


@router.get("/bundle")
def bundle(app: str | None = None) -> dict:
    flows = (_load("fac_semantic_flows.json") or {}).get("flows") or []
    stages = _load("fac_semantic_stages.json") or []
    edges = _load("fac_semantic_edges.json") or []
    memb = _load("fac_memberships.json") or []
    overlay = _overlay()
    if app:
        flows = [f for f in flows if f["app"] == app]
        keep = {s["semantic_stage_id"] for f in flows for s in (
            [x for x in stages if x["semantic_stage_id"] in f["stage_order"]])}
        stages = [s for s in stages if s["semantic_stage_id"] in keep]
        edges = [e for e in edges
                 if e["source_stage"] in keep and e["target_stage"] in keep]
        memb = [m for m in memb if m["semantic_stage_id"] in keep]
    # apply semantic-plane overlay (user edits)
    for s in stages:
        ov = overlay.get(s["semantic_stage_id"])
        if ov:
            for k, v in ov.items():
                s[k] = v
    # apply sync staleness markers (XS6): human-accepted stages whose inputs
    # regenerated are served STALE_NEEDS_REVIEW — never auto-rewritten
    stale = _load("fac_sync_staleness.json") or {}
    for s in stages:
        m = stale.get(s["semantic_stage_id"])
        if m:
            s["sync_status"] = m.get("sync_status")
            s["sync_revision"] = m.get("since_revision")
    return {"flows": flows, "stages": stages, "edges": edges, "memberships": memb,
            "note": "Semantic Flow = annotation plane; status SUGGESTED until accepted; never evidence",
            "overlay_count": len(overlay),
            "stale_count": len(stale)}


@router.get("/stage/{stage_id}")
def stage(stage_id: str) -> dict:
    s = next((x for x in (_load("fac_semantic_stages.json") or [])
              if x["semantic_stage_id"] == stage_id), None)
    if not s:
        raise HTTPException(status_code=404, detail={"code": "stage_not_found",
                                                     "message": f"no semantic stage {stage_id}"})
    ov = _overlay().get(stage_id)
    if ov:
        for k, v in ov.items():
            s[k] = v
    return {"stage": s,
            "memberships": [m for m in (_load("fac_memberships.json") or [])
                            if m["semantic_stage_id"] == stage_id]}


@router.post("/stage/{stage_id}")
def update_stage(stage_id: str, body: dict) -> dict:
    """Semantic-plane edit only (rename/description/status/order).  Never
    touches evidence, flow, or design objects."""
    s = next((x for x in (_load("fac_semantic_stages.json") or [])
              if x["semantic_stage_id"] == stage_id), None)
    if not s:
        raise HTTPException(status_code=404, detail={"code": "stage_not_found",
                                                     "message": f"no semantic stage {stage_id}"})
    if body.get("status") not in (None, "SUGGESTED", "ACCEPTED", "REJECTED"):
        raise HTTPException(status_code=422, detail={"code": "bad_status",
                                                     "message": "status must be SUGGESTED/ACCEPTED/REJECTED"})
    overlay = _overlay()
    cur = overlay.setdefault(stage_id, {})
    for k in OVERLAY_FIELDS:
        if k in body and body[k] is not None:
            cur[k] = body[k]
    (TOURNAMENT / "fac_user_edits.json").write_text(
        json.dumps(overlay, indent=2, ensure_ascii=False))
    kept = {k: s[k] for k in OVERLAY_FIELDS if k in s}
    merged = {**kept, **cur}
    return {"stage_id": stage_id, "applied": {k: merged[k] for k in OVERLAY_FIELDS
                                              if k in merged},
            "note": "semantic plane only; canonical evidence untouched"}
