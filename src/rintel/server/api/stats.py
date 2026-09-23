"""Graph statistics (SPEC-P1 §7-12)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...store import Store
from ..deps import get_store, require_repo, resolve_snapshot

router = APIRouter(tags=["stats"])


@router.get("/stats")
def stats(repo_id: str, snapshot: str | None = None,
          store: Store = Depends(get_store)) -> dict:
    """§7-12: same shape as `Store.stats`."""
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, snapshot)
    return store.stats(repo_id, sid)
