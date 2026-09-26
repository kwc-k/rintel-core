"""LOD neighborhood queries (SPEC-P1 §7-7, hard budgets §20)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from ...store import Store
from ..deps import get_store, require_repo, resolve_snapshot
from ..errors import ApiError

router = APIRouter(prefix="/graph", tags=["graph"])


def _node_out(r: dict) -> dict:
    out = {k: r[k] for k in ("id", "kind", "name", "qname", "language",
                             "identity_schema_version",
                             "path", "start_line", "start_col", "end_line",
                             "end_col") if k in r}
    out["dist"] = r.get("_dist", 0)
    try:
        out["meta"] = json.loads(r.get("meta_json") or "{}")
    except (ValueError, TypeError):
        out["meta"] = {}
    return out


def _edge_out(r: dict) -> dict:
    return {k: r[k] for k in ("id", "kind", "src_id", "dst_id",
                              "confidence") if k in r}


@router.get("/neighborhood")
def neighborhood(
        repo_id: str,
        node: str,
        snapshot: str | None = None,
        depth: int = Query(1, ge=0, le=4),
        node_budget: int = Query(300, ge=1, le=2000),
        edge_budget: int = Query(2000, ge=1, le=8000),
        relations: str | None = None,
        direction: str = Query("both", pattern="^(both|in|out)$"),
        store: Store = Depends(get_store)) -> dict:
    """§7-7: budgeted BFS neighborhood (LOD; no full-graph push)."""
    require_repo(store, repo_id)
    sid = resolve_snapshot(store, repo_id, snapshot)
    resolved = store.node_by_id(repo_id, sid, node)
    if resolved is None:
        candidates = store.nodes_by_legacy_id(repo_id, sid, node)
        if len(candidates) > 1:
            raise ApiError(409, "ambiguous_legacy_id",
                           "legacy symbol ID maps to multiple objects",
                           {"node": node, "snapshot": sid,
                            "candidates": [row["id"] for row in candidates]})
        raise ApiError(404, "node_not_found",
                       f"symbol '{node}' not found in snapshot",
                       {"node": node, "snapshot": sid})
    resolved_id = resolved["id"]
    relation = relations.split(",")[0].strip() if relations else None
    res = store.neighbors(repo_id, sid, resolved_id, relation=relation,
                          depth=depth, max_nodes=node_budget,
                          direction=direction)
    nodes = [_node_out(n) for n in res["nodes"].values()]
    edges = [_edge_out(e) for e in res["edges"]]
    edges_truncated = len(edges) > edge_budget
    if edges_truncated:
        edges = edges[:edge_budget]
    nodes_returned, edges_returned = len(nodes), len(edges)
    truncated = (nodes_returned >= node_budget or edges_truncated
                 or nodes_returned == 0 and depth > 0)
    return {
        "repo_id": repo_id, "snapshot": sid, "nodes": nodes, "edges": edges,
        "budgets": {"node_budget": node_budget, "edge_budget": edge_budget,
                    "nodes_returned": nodes_returned,
                    "edges_returned": edges_returned},
        "truncated": truncated,
    }
