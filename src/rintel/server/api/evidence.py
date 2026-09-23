"""Evidence provenance rows (SPEC-P1 §7-9; read-only plane)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from ...store import Store
from ..deps import get_store, require_repo, resolve_snapshot
from ..errors import ApiError

router = APIRouter(tags=["evidence"])


def _loads(raw) -> dict | None:
    if raw is None:
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        return None


@router.get("/evidence")
def evidence(entity_type: str = Query(..., pattern="^(node|edge)$"),
             entity_id: str = Query(...),
             repo_id: str = Query(...),
             snapshot: str | None = None,
             store: Store = Depends(get_store)) -> dict:
    """§7-9: evidence rows for one canonical entity ("why" a relation)."""
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, snapshot)
    if not entity_id.startswith(f"{entity_type}:"):
        raise ApiError(422, "entity_type_mismatch",
                       f"entity_id must start with '{entity_type}:'",
                       {"entity_type": entity_type, "entity_id": entity_id})
    items = []
    for r in store.evidence_for(repo_id, sid, entity_id):
        if r.get("entity_type") != entity_type:
            continue
        items.append({"source": r["source"], "confidence": r["confidence"],
                      "location": _loads(r.get("location_json")),
                      "payload": _loads(r.get("payload_json")),
                      "ts": r["ts"]})
    return {"items": items, "total": len(items)}
