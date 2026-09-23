"""Read-only MCP projection of the same published Store used by REST.

This adapter adds no analyzer or truth inference. Missing dimensions stay UNKNOWN;
the Store's exact published snapshot, provenance rows and source service remain
the authorities for every returned value.
"""
from __future__ import annotations

import json
from collections import deque
from urllib.parse import parse_qs, quote, unquote, urlsplit

from rintel.server.api.source import source as read_source
from rintel.server.errors import ApiError
from rintel.store import Store


def _error(code: str, message: str, **details: object) -> dict:
    return {"summary": f"{code}: {message}",
            "error": {"code": code, "message": message, **details}}


def _json(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _scopes(store: Store, repo_id: str | None = None) -> list[tuple[dict, str]]:
    repos = ([store.repo(repo_id)] if repo_id else store.repos())
    return [(repo, sid) for repo in repos if repo is not None
            if (sid := store.current_snapshot(repo["id"])) is not None]


def repo_status(store: Store, repo_id: str | None = None) -> list[dict]:
    rows = []
    for repo in ([store.repo(repo_id)] if repo_id else store.repos()):
        if repo is None:
            continue
        sid = store.current_snapshot(repo["id"])
        stats = store.stats(repo["id"], sid) if sid else {}
        rows.append({"lane": "published", "repo_id": repo["id"],
                     "label": repo["id"], "root_path": repo["root_path"],
                     "snapshot_id": sid, "canonical_revision": sid,
                     "status": "INDEXED" if sid else "UNINDEXED",
                     "counts": stats,
                     "capability": {"coverage": "UNKNOWN unless a scoped certificate is supplied"},
                     "known_limitations": ["No completeness inferred from a published graph."]})
    return rows


def search_symbols(store: Store, params: dict) -> list[dict]:
    query = str(params.get("query") or "")
    path_filter = str(params.get("file") or "")
    limit = max(1, min(int(params.get("limit") or 12), 200))
    out: list[dict] = []
    for repo, sid in _scopes(store, params.get("repo_id")):
        rows = (store.search(repo["id"], query, limit=limit, snapshot_id=sid)
                if query else store.all_nodes(repo["id"], sid))
        for row in rows:
            if path_filter and not str(row.get("path") or "").endswith(path_filter):
                continue
            if params.get("kind") and str(params["kind"]).lower() not in str(row["kind"]).lower():
                continue
            if params.get("language") and str(params["language"]).lower() != str(row.get("language") or "").lower():
                continue
            out.append({"lane": "published", "repo_id": repo["id"],
                        "snapshot_id": sid, "canonical_id": row["id"],
                        "name": row["name"], "kind": row["kind"],
                        "file": row.get("path"), "line": row.get("start_line"),
                        "language": row.get("language"),
                        "match": row.get("match", "file")})
            if len(out) >= limit:
                return out
    return out


def source_uri(repo_id: str, sid: str, path: str, start: int, end: int) -> str:
    return (f"rintel://published-source/{quote(repo_id, safe='')}/"
            f"{quote(sid, safe='')}/{quote(path, safe='')}?start={start}&end={end}")


def get_symbol(store: Store, canonical_id: str, repo_id: str | None = None) -> dict | None:
    hits = []
    for repo, sid in _scopes(store, repo_id):
        node = store.node_by_id(repo["id"], sid, canonical_id)
        if node is None:
            continue
        span = {key: node.get(key) for key in
                ("path", "start_line", "start_col", "end_line", "end_col")}
        edges = store.edges_for_node(repo["id"], sid, canonical_id)
        start = max(1, int(node.get("start_line") or 1))
        end = min(start + 80, max(start, int(node.get("end_line") or start)))
        uri = (source_uri(repo["id"], sid, node["path"], start, end)
               if node.get("path") else None)
        hits.append({"lane": "published", "repo_id": repo["id"],
                     "snapshot_id": sid,
                     "identity": {"canonical_id": node["id"], "name": node["name"],
                                  "qname": node["qname"], "kind": node["kind"],
                                  "language": node.get("language"), "file": node.get("path"),
                                  "span": span, "binding": "published canonical node"},
                     "parent": {"file": node.get("path")}, "children": [],
                     "topology_summary": {
                         "calls_out": sum(e["kind"] == "CALLS" and e["src_id"] == canonical_id
                                          for e in edges),
                         "calls_in": sum(e["kind"] == "CALLS" and e["dst_id"] == canonical_id
                                         for e in edges)},
                     "flow_memberships": [], "source_uri": uri,
                     "data_interface": {"ports_summary": None,
                                        "note": "No port evidence inferred by this adapter."}})
    if not hits:
        return None
    if len(hits) > 1:
        return _error("AMBIGUOUS_SYMBOL", "canonical id exists in multiple repositories",
                      requested=canonical_id, candidates=[h["repo_id"] for h in hits],
                      hint="pass repo_id from repo_status")
    return {"summary": f"published symbol {canonical_id} in {hits[0]['repo_id']}",
            "hits": hits, "next": ["query_topology", "explain_evidence", "read_resource"]}


def topology(store: Store, params: dict) -> dict:
    repo_id = str(params.get("repo_id") or "")
    if not store.repo(repo_id):
        return _error("REPO_NOT_FOUND", f"repository '{repo_id}' not registered")
    sid = store.current_snapshot(repo_id)
    if sid is None:
        return _error("REPO_NOT_INDEXED", f"repository '{repo_id}' has no published snapshot")
    root = str(params.get("root") or "")
    node = store.node_by_id(repo_id, sid, root)
    if node is None:
        return _error("SYMBOL_NOT_FOUND", f"symbol '{root}' not in current snapshot",
                      hint="use search_symbols(query=..., repo_id=...) first")
    direction = str(params.get("direction") or "both")
    if direction not in {"in", "out", "both"}:
        return _error("INVALID_ENUM", "invalid direction", parameter="direction",
                      received=direction, allowed=["in", "out", "both"])
    depth = max(0, min(int(params.get("depth") or 1), 2))
    limit = max(1, min(int(params.get("limit") or 60), 300))
    relations = params.get("relations") or ["CALL"]
    requested = {"CALLS" if r == "CALL" else r for r in relations}
    graph = store.neighbors(repo_id, sid, root, depth=depth,
                            max_nodes=limit, direction=direction)
    nodes = [{"canonical_id": n["id"], "name": n["name"],
              "file": n.get("path"), "source_span": {
                  "path": n.get("path"), "start_line": n.get("start_line"),
                  "end_line": n.get("end_line")}} for n in graph["nodes"].values()]
    edges = []
    for edge in graph["edges"]:
        if edge["kind"] not in requested:
            continue
        meta = _json(edge.get("meta_json"))
        receipts = store.support_receipts(repo_id, sid, edge["id"])
        edges.append({"edge_id": edge["id"], "source": edge["src_id"],
                      "target": edge["dst_id"], "kind": edge["kind"],
                      "truth_class": meta.get("truth_class", "UNKNOWN"),
                      "target_resolution": meta.get("resolution", "UNKNOWN"),
                      "coverage": meta.get("coverage", "UNKNOWN"),
                      "support_receipt_count": len(receipts),
                      "unknown_target": False})
        if len(edges) >= limit:
            break
    return {"summary": f"published snapshot {sid}: {len(nodes)} nodes, {len(edges)} edges",
            "repo_id": repo_id, "snapshot_id": sid, "evidence_revision": sid, "root": root,
            "nodes": nodes, "edges": edges, "unknown_endpoints": 0,
            "capability": {"coverage": "UNKNOWN unless a scoped certificate is supplied"},
            "next": ["explain_evidence", "read_resource"]}


def find_path(store: Store, params: dict) -> dict:
    repo_id = str(params.get("repo_id") or "")
    if not store.repo(repo_id):
        return _error("REPO_NOT_FOUND", f"repository '{repo_id}' not registered")
    sid = store.current_snapshot(repo_id)
    if sid is None:
        return _error("REPO_NOT_INDEXED", f"repository '{repo_id}' has no published snapshot")
    source, target = str(params["source"]), str(params["target"])
    if not store.node_by_id(repo_id, sid, source) or not store.node_by_id(repo_id, sid, target):
        return _error("SYMBOL_NOT_FOUND", "source or target is not an exact published node id",
                      hint="discover exact ids with search_symbols")
    kinds = {"CALLS" if kind == "CALL" else kind
             for kind in (params.get("relations") or ["CALL"])}
    max_hops = max(1, min(int(params.get("max_hops") or 6), 8))
    queue = deque([(source, [])])
    seen = {source}
    while queue and len(seen) <= 300:
        current, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for edge in store.edges_for_node(repo_id, sid, current, direction="out"):
            if edge["kind"] not in kinds:
                continue
            next_path = path + [{"from": edge["src_id"], "to": edge["dst_id"],
                                 "edge_id": edge["id"], "kind": edge["kind"]}]
            if edge["dst_id"] == target:
                return {"verdict": "FOUND", "repo_id": repo_id, "snapshot_id": sid,
                        "paths": [{"hops": next_path}], "unknown_frontier_hits": [],
                        "note": "Published canonical edge path; provenance remains per-edge.",
                        "next": ["explain_evidence"]}
            if edge["dst_id"] not in seen:
                seen.add(edge["dst_id"])
                queue.append((edge["dst_id"], next_path))
    return {"verdict": "UNKNOWN", "repo_id": repo_id, "snapshot_id": sid,
            "paths": [], "unknown_frontier_hits": [],
            "note": "No bounded published path found; absence is not proof of impossibility."}


def explain(store: Store, entity_id: str, repo_id: str | None = None) -> dict | None:
    hits = []
    for repo, sid in _scopes(store, repo_id):
        if entity_id.startswith("node:"):
            exists = store.node_by_id(repo["id"], sid, entity_id) is not None
        elif entity_id.startswith("edge:"):
            exists = any(edge["id"] == entity_id for edge in store.all_edges(repo["id"], sid))
        else:
            exists = False
        if not exists:
            continue
        rows = store.evidence_for(repo["id"], sid, entity_id)
        receipts = store.support_receipts(repo["id"], sid, entity_id)
        hits.append({"repo_id": repo["id"], "snapshot_id": sid,
                     "entity_id": entity_id,
                     "evidence": [{"provider": row["source"],
                                   "confidence": row["confidence"],
                                   "location": _json(row.get("location_json")),
                                   "payload": _json(row.get("payload_json"))}
                                  for row in rows],
                     "support_receipts": receipts})
    if not hits:
        return None
    if len(hits) > 1:
        return _error("AMBIGUOUS_ENTITY", "entity id exists in multiple repositories",
                      requested=entity_id, candidates=[h["repo_id"] for h in hits],
                      hint="pass repo_id from repo_status")
    hit = hits[0]
    return {"summary": f"{len(hit['evidence'])} provenance record(s) for {entity_id}",
            **hit, "direct_record_found": bool(hit["evidence"] or hit["support_receipts"]),
            "next": ["read_resource"]}


def read_resource(store: Store, uri: str) -> dict | None:
    parts = urlsplit(uri)
    if parts.scheme != "rintel" or parts.netloc != "published-source":
        return None
    segments = parts.path.lstrip("/").split("/", 2)
    if len(segments) != 3:
        return _error("INVALID_URI", "published source URI needs repo, snapshot and path")
    repo_id, sid, path = map(unquote, segments)
    if not path or not store.repo(repo_id):
        return _error("INVALID_URI", "unknown repository or missing source path")
    snap = store.snapshot(sid)
    if snap is None or snap.get("repo_id") != repo_id or snap.get("publication_status") != "published":
        return _error("SNAPSHOT_NOT_FOUND", "source snapshot is not published for this repository")
    query = parse_qs(parts.query)
    try:
        start = int(query.get("start", ["1"])[0])
        end = int(query.get("end", [str(start + 79)])[0])
    except ValueError:
        return _error("INVALID_URI", "source line range must be numeric")
    if start < 1 or end < start or end - start > 199:
        return _error("INVALID_URI", "source line range must be 1-200 lines")
    try:
        result = read_source(repo_id=repo_id, path=path, snapshot=sid,
                             start=start, end=end, store=store)
    except ApiError as exc:
        return _error(exc.code.upper(), exc.message)
    if result.get("drift") is True:
        return _error("SOURCE_STALE", "working-tree source differs from indexed snapshot",
                      repo_id=repo_id, snapshot_id=sid, path=path)
    return {"uri": uri, "repo_id": repo_id, "snapshot_id": sid,
            "file": path, "start_line": start, "end_line": end,
            "text": result["content"], "source_etag": result["etag"],
            "drift": result.get("drift")}
