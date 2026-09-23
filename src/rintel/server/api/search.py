"""Global symbol search (SPEC-P1 §7-6: exact → fts → trigram)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...store import Store
from ..deps import get_store, require_repo, resolve_snapshot

router = APIRouter(tags=["search"])


@router.get("/search")
def search(q: str, repo_id: str, snapshot: str | None = None,
           limit: int = Query(50, ge=1, le=200),
           kinds: str | None = None,
           store: Store = Depends(get_store)) -> dict:
    """§7-6: symbol search; `match` ∈ exact:{col} / fts / trigram / like.

    Search is scoped to the resolved snapshot (DEBT-SNAPSHOT-SEARCH fix):
    `?snapshot=` (default: latest) selects the snapshot whose symbols are
    searched, so historical snapshots never leak rows from newer ones.
    """
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, snapshot)
    rows = store.search(repo_id, q, limit=limit, snapshot_id=sid)
    allowed = ({k.strip().upper() for k in kinds.split(",") if k.strip()}
               if kinds else None)
    items = []
    seen: set[str] = set()
    for r in rows:
        if r["id"] in seen:
            continue
        if allowed is not None and r["kind"] not in allowed:
            continue
        seen.add(r["id"])
        items.append({"id": r["id"], "kind": r["kind"], "name": r["name"],
                      "qname": r["qname"], "language": r["language"],
                      "path": r["path"], "line": r["start_line"],
                      "start_line": r["start_line"],
                      "end_line": r["end_line"], "match": r["match"]})
    return {"items": items, "total": len(items), "limit": limit,
            "snapshot": sid}
