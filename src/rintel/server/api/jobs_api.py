"""Index job endpoints (SPEC-P1 §7-2: GET /jobs/{job_id})."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..errors import ApiError
from ..jobs import JobManager

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _manager(request: Request) -> JobManager:
    return request.app.state.jobs


@router.get("")
def list_jobs(repo_id: str | None = None, status: str | None = None,
              limit: int = Query(50, ge=1, le=200),
              request: Request = None) -> dict:
    all_jobs = _manager(request).list(repo_id, status)
    return {"items": all_jobs[:limit], "total": len(all_jobs)}


@router.get("/{job_id}")
def get_job(job_id: str, request: Request = None) -> dict:
    """§7-2: `{status, progress, result: IndexResult}` (+ error on failure)."""
    j = _manager(request).get(job_id)
    if not j:
        raise ApiError(404, "job_not_found", f"job '{job_id}' not found")
    return j
