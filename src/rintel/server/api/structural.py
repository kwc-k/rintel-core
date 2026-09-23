"""R3: read-only Evidence Structural Overview (SPEC-P1 §7-14).

Pure evidence projection aggregated over the latest (or requested)
snapshot: module/file tree, top-level units, top module-pair relationships
and counts.  **Never** generates architecture components — the UI labels
this as Evidence / Code Structure.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, Query

from ...store import Store
from ..deps import get_store, require_repo, resolve_snapshot

router = APIRouter(tags=["structural"])

TOP_KIND_PRIORITY = {
    "MODULE": 0, "PACKAGE": 1, "PROGRAM": 2, "NAMESPACE": 3, "CLASS": 4,
    "INTERFACE": 5, "SUBMODULE": 6, "FUNCTION": 7, "PROCEDURE": 8,
    "SUBROUTINE": 9, "METHOD": 10,
}


def _module_of(path: str) -> str:
    if not path:
        return "<root>"
    return path.split("/", 1)[0]


def _parse_meta(row: dict) -> dict:
    try:
        return json.loads(row.get("meta_json") or "{}")
    except (ValueError, TypeError):
        return {}


def _build_tree(files: list[dict], dir_counts: dict[str, int],
                per_file: dict[str, int], depth: int,
                prefix: str = "", level: int = 0, is_root: bool = True) -> list[dict]:
    """Directory/file entries down to `depth` levels (module = top level)."""
    out: list[dict] = []
    subdirs: set[str] = set()
    direct: list[dict] = []
    for f in files:
        p = f["path"]
        d = p.rsplit("/", 1)[0] if "/" in p else ""
        if prefix:
            if d == prefix:
                direct.append(f)
            elif d.startswith(prefix + "/"):
                subdirs.add(prefix + "/" + d[len(prefix) + 1:].split("/", 1)[0])
        else:
            if d == "":
                direct.append(f)
            else:
                subdirs.add(d.split("/", 1)[0])
    for sd in sorted(subdirs):
        out.append({"id": sd, "kind": "module" if is_root else "dir",
                    "name": sd.rsplit("/", 1)[-1], "path": sd,
                    "symbol_count": dir_counts.get(sd, 0),
                    "has_children": True})
        if level + 1 < depth:
            out.extend(_build_tree(files, dir_counts, per_file, depth,
                                   prefix=sd, level=level + 1, is_root=False))
    for f in sorted(direct, key=lambda x: x["path"]):
        out.append({"id": f["path"], "kind": "file",
                    "name": f["path"].rsplit("/", 1)[-1], "path": f["path"],
                    "symbol_count": per_file.get(f["path"], 0),
                    "has_children": per_file.get(f["path"], 0) > 0})
    return out


@router.get("/repos/{repo_id}/structural-overview")
def structural_overview(
        repo_id: str,
        snapshot: str | None = None,
        depth: int = Query(2, ge=1, le=4),
        node_budget: int = Query(200, ge=1, le=2000),
        edge_budget: int = Query(500, ge=1, le=8000),
        store: Store = Depends(get_store)) -> dict:
    """§7-14: evidence-projection overview (R3, read-only)."""
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, snapshot)
    files = store.files(repo_id)
    nodes = store.all_nodes(repo_id, sid)
    edges = store.all_edges(repo_id, sid)

    per_file: Counter = Counter()
    dir_counts: Counter = Counter()
    for n in nodes:
        p = n.get("path") or ""
        if p:
            per_file[p] += 1
            parts = p.split("/")
            for i in range(1, len(parts)):
                dir_counts["/".join(parts[:i])] += 1

    tree = _build_tree(files, dict(dir_counts), dict(per_file), depth)
    tree_truncated = len(tree) >= node_budget

    # top-level units: symbols not nested inside another symbol
    units = [n for n in nodes if n.get("qname") == n.get("name")]
    units.sort(key=lambda n: (TOP_KIND_PRIORITY.get(n["kind"], 99), n["name"]))
    top_units = [{"id": u["id"], "kind": u["kind"], "name": u["name"],
                  "qname": u["qname"], "language": u["language"],
                  "path": u["path"], "start_line": u["start_line"],
                  "end_line": u["end_line"], "meta": _parse_meta(u)}
                 for u in units]
    units_truncated = len(top_units) > node_budget
    top_units = top_units[:node_budget]

    # top relationships: edges aggregated per (module, module, kind)
    node_mod = {n["id"]: _module_of(n.get("path") or "") for n in nodes}
    groups: dict[tuple[str, str, str], int] = defaultdict(int)
    for e in edges:
        s, d = node_mod.get(e["src_id"]), node_mod.get(e["dst_id"])
        if s is None or d is None:
            continue
        groups[(s, d, e["kind"])] += 1
    rel_rows = [{"src": s, "dst": d, "kind": k, "count": c}
                for (s, d, k), c in groups.items()]
    rel_rows.sort(key=lambda r: (-r["count"], r["kind"], r["src"], r["dst"]))
    edges_truncated = len(rel_rows) > edge_budget
    rel_rows = rel_rows[:edge_budget]

    modules = sorted({_module_of(f["path"]) for f in files})
    counts = {"files": len(files), "symbols": len(nodes), "edges": len(edges),
              "modules": len(modules),
              "unresolved": store.unresolved_count(repo_id, sid)}
    # FAC-EQ0: unresolved-evidence reason histogram (evidence-uncertainty
    # buckets from the indexer; raw kinds for non-callsite notes).  Lets the
    # UI present "unresolved" as uncertainty with honest causes, never as
    # proof-of-absence.
    reasons: Counter = Counter()
    for r in store.unresolved_rows(repo_id, sid):
        try:
            detail = json.loads(r["detail_json"] or "{}")
        except ValueError:
            detail = {}
        cls = detail.get("classification")
        reasons[cls if cls else r["kind"]] += 1
    if reasons:
        counts["unresolved_reasons"] = dict(reasons)
    return {
        "repo_id": repo_id, "snapshot": sid,
        "tree": tree, "top_units": top_units, "edges": rel_rows,
        "counts": counts,
        "budgets": {"node_budget": node_budget, "edge_budget": edge_budget,
                    "nodes_returned": len(top_units),
                    "edges_returned": len(rel_rows)},
        "truncated": tree_truncated or units_truncated or edges_truncated,
    }
