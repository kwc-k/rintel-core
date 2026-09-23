"""Source / snapshot alignment (SPEC-P1 §7-10, R6).

- latest snapshot (no `commit_sha`): read the working tree; hash mismatch
  vs `files.hash` → `drift: true` warning;
- historical snapshot with `commit_sha` (git repos): `git show <sha>:<path>`;
- unrecoverable history → 404 `source_unavailable`, **never** fall back to
  current source; path traversal is rejected (422).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ...store import Store
from ..deps import get_store, require_repo
from ..errors import ApiError

router = APIRouter(tags=["source"])

MAX_BYTES = 8 * 1024 * 1024


def _resolve_path(root: Path, path: str) -> Path:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        raise ApiError(422, "invalid_source_path",
                       "path must be repository-relative", {"path": path})
    full = (root / p).resolve()
    root_s = str(root)
    if str(full) != root_s and not str(full).startswith(root_s + os.sep):
        raise ApiError(422, "invalid_source_path",
                       "path escapes the repository root", {"path": path})
    return full


def _git_show(root: Path, commit: str, path: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{path}"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


@router.get("/source")
def source(repo_id: str, path: str, snapshot: str | None = None,
           start: int | None = Query(None, ge=1),
           end: int | None = Query(None, ge=1),
           store: Store = Depends(get_store)) -> dict:
    """§7-10: file content aligned with a snapshot (R6 rules)."""
    repo = require_repo(store, repo_id)
    sid = snapshot or store.current_snapshot(repo_id)
    if sid is None:
        raise ApiError(404, "repo_not_indexed",
                       f"repository '{repo_id}' has no snapshot")
    snap = store.snapshot(sid)
    if snap is None:
        raise ApiError(404, "snapshot_not_found", f"snapshot '{sid}' not found")

    root = Path(repo["root_path"])
    full = _resolve_path(root, path)
    frow = store.file_state(repo_id, path)
    language = frow.get("language") if frow else None
    if not language:
        rows = store.nodes_by_path(repo_id, sid, path)
        if rows:
            language = rows[0].get("language")
    if not language:
        from ...adapters import adapter_for_path
        adapter = adapter_for_path(path)
        language = adapter.language if adapter else None
    commit = snap["commit_sha"]

    if commit:
        content = _git_show(root, commit, path)
        if content is None:
            raise ApiError(
                404, "source_unavailable",
                f"source for '{path}' is not recoverable at snapshot {sid}",
                {"path": path, "snapshot": sid,
                 "reason": "git show failed (not a git repo or path missing "
                           "at that commit)"})
        drift = None
    else:
        if not full.is_file():
            raise ApiError(404, "source_unavailable",
                           f"file '{path}' not found in the working tree",
                           {"path": path, "snapshot": sid})
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ApiError(500, "source_read_failed",
                           f"cannot read '{path}': {exc}") from exc
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if frow and frow["hash"] != h:
            drift = True
        elif frow:
            drift = False
        else:
            drift = None

    if start is not None:
        lines = content.splitlines(keepends=True)
        content = "".join(lines[start - 1:end if end is not None else None])
    etag = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    out: dict = {"path": path, "language": language, "content": content,
                 "etag": etag, "snapshot": sid}
    if drift is not None:
        out["drift"] = drift
    return out
