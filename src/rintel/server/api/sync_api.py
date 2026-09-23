"""CROSS-PROJECTION-SYNC0 endpoints: revision status + manual refresh.

The watcher (scripts/sync_watch.py) auto-syncs; these endpoints let the
Workbench and tests query the current revision and trigger a sync.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from rintel import sync as S

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status")
def status() -> dict:
    return S.current_state()


@router.post("/refresh")
def refresh(body: dict | None = None) -> dict:
    """Trigger an incremental sync (optional fac_root for worktree runs)."""
    body = body or {}
    root = body.get("fac_root")
    try:
        st = S.sync(root=Path(root) if root else None,
                    force=bool(body.get("force", False)),
                    regenerate=bool(body.get("regenerate", False)))
    except Exception as exc:                        # noqa: BLE001
        raise HTTPException(status_code=500, detail={
            "code": "sync_failed", "message": f"sync failed: {exc}"}) from exc
    if st.get("failed"):
        raise HTTPException(status_code=500, detail={
            "code": "sync_failed", "message": "sync failed",
            "details": st["failed"]})
    return st
