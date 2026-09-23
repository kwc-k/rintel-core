"""Repository endpoints (SPEC-P1 §7-1..3; S2 extension: POST /repos)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ...store import Store
from ..deps import get_store, require_repo, resolve_snapshot
from ..errors import ApiError
from ..jobs import JobManager, make_index_runner

router = APIRouter(prefix="/repos", tags=["repos"])


class RepoCreate(BaseModel):
    root_path: str
    label: str | None = None


def _parse_meta(raw) -> dict:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _manager(request: Request) -> JobManager:
    return request.app.state.jobs


def _snapshot_brief(store: Store, repo_id: str) -> tuple[str | None, int, int]:
    sid = store.current_snapshot(repo_id)
    if not sid:
        return None, 0, 0
    st = store.stats(repo_id, sid)
    return sid, st.get("nodes", 0), st.get("edge_count", st.get("edges", 0))


def _repo_row(store: Store, r: dict) -> dict:
    sid, nodes, edges = _snapshot_brief(store, r["id"])
    return {"id": r["id"], "root_path": r["root_path"],
            "created_at": r["created_at"], "latest_snapshot": sid,
            "node_count": nodes, "edge_count": edges}


@router.get("")
def list_repos(store: Store = Depends(get_store)) -> dict:
    """§7-1: repository list with latest-snapshot counts."""
    items = [_repo_row(store, r) for r in store.repos()]
    return {"items": items, "total": len(items)}


@router.post("", status_code=202)
def create_repo(body: RepoCreate, request: Request,
                store: Store = Depends(get_store)) -> dict:
    """S2 extension: register a repo on disk and start a first index job.

    The repo becomes visible immediately; its graph appears when the job
    finishes (job failure leaves the repo listed without a snapshot).
    """
    p = Path(body.root_path).expanduser().resolve()
    if not p.is_dir():
        raise ApiError(422, "invalid_repo_path",
                       f"'{body.root_path}' is not a directory",
                       {"root_path": body.root_path})
    repo_id = (body.label or p.name).strip()
    if not repo_id:
        raise ApiError(422, "invalid_repo_path",
                       "label resolves to an empty repo id")
    store.upsert_repo(repo_id, str(p))
    job = _manager(request).create("index", repo_id)
    _manager(request).enqueue(
        job, make_index_runner(request.app.state.store_factory, repo_id))
    return {"repo_id": repo_id, "job_id": job["id"]}


def _run_picker(cmd: list[str], timeout: float = 600.0) -> str | None:
    """Run a native picker command; returns trimmed stdout or None."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def pick_folder_native() -> str | None:
    """Native directory picker on the server host (local workbench).

    The browser cannot expose an absolute local path (html webkitdirectory
    only yields upload stubs), so the picker runs on the server side:
      - macOS:   osascript 'choose folder'
      - Linux:   zenity --file-selection --directory
      - Windows: PowerShell FolderBrowserDialog
    Returns the chosen absolute path, or None when the user cancels or no
    picker is available (manual path entry remains the fallback).
    """
    if sys.platform == "darwin":
        # `choose folder` returns an alias by default; `POSIX path of …`
        # converts it.  NOTE: `as alias` after `with prompt` coerces the
        # PROMPT string (error -1700) — do not add it.
        return _run_picker([
            "osascript", "-e",
            'POSIX path of (choose folder with prompt "选择仓库目录")'])
    if sys.platform.startswith("linux"):
        return _run_picker([
            "zenity", "--file-selection", "--directory",
            "--title=选择仓库目录"])
    if sys.platform.startswith("win"):
        return _run_picker([
            "powershell", "-NoProfile", "-Command",
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "if($f.ShowDialog() -eq 'OK'){Write-Output $f.SelectedPath}"])
    return None


@router.post("/pick-folder")
def pick_repo_folder() -> dict:
    """Native folder picker on the server host (local workbench helper).

    Response: {"path": "/abs/path"} when a folder is chosen, or
    {"path": null} when the user cancels / no picker is available
    (manual path entry stays the fallback).
    """
    return {"path": pick_folder_native()}


@router.get("/{repo_id}")
def get_repo(repo_id: str, store: Store = Depends(get_store)) -> dict:
    r = require_repo(store, repo_id)
    return _repo_row(store, r)


@router.get("/{repo_id}/snapshots")
def list_snapshots(repo_id: str, store: Store = Depends(get_store)) -> dict:
    """§7-3: snapshot history (oldest first)."""
    require_repo(store, repo_id)
    items = []
    for s in store.snapshots(repo_id):
        items.append({"id": s["id"], "parent_id": s["parent_id"],
                      "commit_sha": s["commit_sha"],
                      "created_at": s["created_at"],
                      "meta": _parse_meta(s.get("meta_json"))})
    return {"items": items, "total": len(items)}


@router.post("/{repo_id}/index", status_code=202)
def trigger_index(repo_id: str, force: bool = False, request: Request = None,
                  store: Store = Depends(get_store)) -> dict:
    """§7-2: trigger (incremental) index; 202 + job_id."""
    require_repo(store, repo_id)
    job = _manager(request).create("index", repo_id)
    _manager(request).enqueue(
        job, make_index_runner(request.app.state.store_factory, repo_id,
                               force=force))
    return {"job_id": job["id"]}
