"""Lazy repository tree + per-file symbol list (SPEC-P1 §7-4/§7-5).

Tree mode (no `q`): children of `path` (directories first, then files) —
the browser only ever receives one directory level per request (gate:
never push the whole repo to the browser).  Filter mode (`q`): flat list of
dirs/files whose path matches (H1 virtualized-tree filtering).
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Query

from ...store import Store
from ..deps import get_store, require_repo, resolve_snapshot

router = APIRouter(prefix="/repos", tags=["tree"])

MAX_LIMIT = 1000


def _norm(path: str) -> str:
    return path.strip("/")


def _dir_prefix_counts(nodes: list[dict]) -> dict[str, int]:
    """symbol_count per ancestor directory (all levels)."""
    out: Counter = Counter()
    for n in nodes:
        p = n.get("path") or ""
        parts = p.split("/")
        for i in range(1, len(parts)):
            out["/".join(parts[:i])] += 1
    return dict(out)


def _dir_has_children(files: list[dict], prefix: str) -> bool:
    """Whether expanding `prefix` yields anything (always true for dirs
    derived from files, but kept explicit)."""
    prefix = _norm(prefix)
    for f in files:
        d = f["path"].rsplit("/", 1)[0] if "/" in f["path"] else ""
        if d == prefix or d.startswith(prefix + "/"):
            return True
    return False


@router.get("/{repo_id}/tree")
def tree(repo_id: str, path: str = "", q: str | None = None,
         snapshot: str | None = None,
         offset: int = Query(0, ge=0),
         limit: int = Query(200, ge=1, le=MAX_LIMIT),
         store: Store = Depends(get_store)) -> dict:
    """§7-4: virtualized tree (dirs+files).  `q` filters paths (flat mode)."""
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, snapshot)
    files = store.files(repo_id)
    nodes = store.all_nodes(repo_id, sid)
    per_file = Counter(n["path"] for n in nodes if n.get("path"))
    dir_counts = _dir_prefix_counts(nodes)
    base = _norm(path)
    q = q.strip().lower() if q else None

    if q:
        matched = [f for f in files if q in f["path"].lower()]
        dirs = sorted({f["path"].rsplit("/", 1)[0] for f in matched
                       if "/" in f["path"]})
        items = ([{"id": d, "kind": "dir", "name": d.rsplit("/", 1)[-1],
                   "path": d, "symbol_count": dir_counts.get(d, 0),
                   "has_children": True} for d in dirs]
                 + [{"id": f["path"], "kind": "file", "name": f["path"].rsplit("/", 1)[-1],
                     "path": f["path"], "symbol_count": per_file.get(f["path"], 0),
                     "has_children": per_file.get(f["path"], 0) > 0}
                    for f in matched])
    else:
        subdirs: set[str] = set()
        direct: list[dict] = []
        for f in files:
            p = f["path"]
            d = p.rsplit("/", 1)[0] if "/" in p else ""
            if base:
                if d == base:
                    direct.append(f)
                elif d.startswith(base + "/"):
                    subdirs.add(base + "/" + d[len(base) + 1:].split("/", 1)[0])
            else:
                if d == "":
                    direct.append(f)
                else:
                    subdirs.add(d.split("/", 1)[0])
        subdirs = sorted(subdirs)
        items = ([{"id": d, "kind": "dir", "name": d.rsplit("/", 1)[-1],
                   "path": d, "symbol_count": dir_counts.get(d, 0),
                   "has_children": True} for d in subdirs]
                 + [{"id": f["path"], "kind": "file",
                     "name": f["path"].rsplit("/", 1)[-1], "path": f["path"],
                     "symbol_count": per_file.get(f["path"], 0),
                     "has_children": per_file.get(f["path"], 0) > 0}
                    for f in sorted(direct, key=lambda f: f["path"])])
    total = len(items)
    return {"items": items[offset:offset + limit], "total": total}


@router.get("/{repo_id}/symbols")
def file_symbols(repo_id: str, path: str, snapshot: str | None = None,
                 offset: int = Query(0, ge=0),
                 limit: int = Query(500, ge=1, le=MAX_LIMIT),
                 store: Store = Depends(get_store)) -> dict:
    """§7-5: symbols defined in one file, ordered by source line."""
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, snapshot)
    rows = store.nodes_by_path(repo_id, sid, path)
    rows.sort(key=lambda r: (r["start_line"] or 0, r["name"]))
    items = [{"id": r["id"], "kind": r["kind"], "name": r["name"],
              "qname": r["qname"], "language": r["language"],
              "path": r["path"], "line": r["start_line"],
              "start_line": r["start_line"], "end_line": r["end_line"]}
             for r in rows]
    total = len(items)
    return {"items": items[offset:offset + limit], "total": total}
