"""Design endpoints — proposal fork / frozen diff / structural impact
(SPEC-P1 §7-24/26/27, S4 slice; user ruling §S4 frozen semantics).

- fork (R2, §6.1-8/13): deep-copies the AS-IS model into a new proposal and
  freezes ``baseline_json`` (schema_version=1) + ``base_evidence_snapshot_id``
  in one transaction.  Diff(P) = Current(P) − Baseline(P) and is immune to
  later AS-IS edits.
- diff output: added/removed/modified/moved components + added/removed
  relations + mapping_changes.  modified = kind/name/description change and
  is NOT folded into moved; moved = parent change; they may overlap.
  mapping_changes covers only components present on both sides.
- impact seeds (user ruling, frozen): added → proposal mappings; removed →
  baseline mappings; modified/moved → baseline ∪ proposal mappings.  The
  traversal is bounded (CALLS/IMPORTS/INCLUDES/REFERENCES, depth ≤ 2, node /
  edge budgets) over the explicitly requested snapshot; FILE/DIRECTORY mappings
  expand to their effective symbol set (§14, S5A: DIRECTORY by path prefix).
  UI name: "Structural Impact".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...archmodel import compute_diff
from ...store import ArchError, Store
from ..deps import get_store
from ..errors import (ApiError, invalid_change, model_not_found,
                      not_a_proposal, snapshot_not_found,
                      workspace_not_found)
from .workspaces import (_component_json, _mapping_entities, _model_json,
                         _relation_json, _require_model,
                         _require_workspace)
from .design_write import legacy_design_write_deprecated

router = APIRouter(prefix="/workspaces", tags=["design"])

# bounded-traversal budgets (SPEC §7-27 defaults: depth ≤ 2 / 2,000 nodes)
IMPACT_DEPTH = 2
IMPACT_NODE_BUDGET = 2000
IMPACT_EDGE_BUDGET = 4000
# dependency edges that count as code impact; CONTAINS only expands
# FILE/DIRECTORY seeds (effective symbol set, §14) without costing depth.
DEP_KINDS = ("CALLS", "IMPORTS", "INCLUDES", "REFERENCES")
CONTAINS = "CONTAINS"
CONTAINER_KINDS = ("FILE", "DIRECTORY")


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------
class ModelForkCreate(BaseModel):
    kind: str = "proposal"  # business-validated → 422 invalid_model_kind
    name: str = Field(min_length=1)
    description: str = ""


class ImpactRequest(BaseModel):
    snapshot_id: Optional[str] = None
    change_ids: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _resolve_snapshot(store: Store, ws: dict,
                      requested: str | None) -> Optional[str]:
    """Validate the snapshot belongs to the workspace repo (R6)."""
    if requested is None:
        return store.current_snapshot(ws["repo_id"])
    snap = store.snapshot(requested)
    if not snap or snap["repo_id"] != ws["repo_id"]:
        raise snapshot_not_found(requested)
    return requested


def _baseline_mapping_dto(store: Store, repo_id: str,
                          m: dict, sid: Optional[str]) -> dict:
    """Baseline mapping doc → API mapping dto (no db row: no id/created)."""
    out = {"entity_type": m["entity_type"],
           "entity_id": m["entity_id"],
           "note": m.get("note", ""),
           "evidence_snapshot_id": m.get("evidence_snapshot_id"),
           "source": "baseline"}
    entity = None
    stale = True
    if sid is not None and store.entity_exists(
            repo_id, sid, m["entity_type"], m["entity_id"]):
        entity = store.entity_row(repo_id, sid, m["entity_type"],
                                  m["entity_id"])
        stale = False
    out["stale"] = stale
    out["entity"] = entity
    return out


def _mappings_dto(store: Store, repo_id: str,
                  working_rows: list[dict],
                  baseline_docs: list[dict],
                  sid: Optional[str]) -> list[dict]:
    """Union of working + baseline mappings with source labels
    (impact seeds for modified/moved; caller picks the subset otherwise)."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in working_rows:
        dto = _mapping_entities(store, repo_id, m, sid)
        dto["source"] = "proposal"
        out.append(dto)
        seen.add((dto["entity_type"], dto["entity_id"]))
    for m in baseline_docs:
        key = (m["entity_type"], m["entity_id"])
        if key in seen:
            continue
        out.append(_baseline_mapping_dto(store, repo_id, m, sid))
    return out


def _names_relation(store: Store, wid: str, mid: str,
                    rel: dict, comps_by_id: dict[str, dict],
                    baseline_docs: dict[str, dict],
                    baseline_side: bool) -> dict:
    """Relation json + human-readable endpoint names for the diff row."""
    out = _relation_json(rel)
    if baseline_side:
        src = baseline_docs.get(rel["src_id"])
        dst = baseline_docs.get(rel["dst_id"])
        out["src_name"] = src["name"] if src else None
        out["dst_name"] = dst["name"] if dst else None
    else:
        src = comps_by_id.get(rel["src_id"])
        dst = comps_by_id.get(rel["dst_id"])
        out["src_name"] = src["name"] if src else None
        out["dst_name"] = dst["name"] if dst else None
    return out


def _diff_rows(store: Store, ws: dict, sid: Optional[str],
               bl_doc: dict, raw: dict,
               comps: list[dict], rels: list[dict]) -> list[dict]:
    """compute_diff output → flat API rows (change_id assigned per bucket)."""
    repo_id = ws["repo_id"]
    w_by_id = {c["id"]: c for c in comps}
    b_docs = {c["id"]: c for c in bl_doc.get("components", [])}
    maps_by = {c["id"]: store.arch_mappings_for_component(ws["id"], c["id"])
               for c in comps}
    rows: list[dict] = []

    for wc in raw["added_components"]:
        rows.append({
            "bucket": "added_components", "change": "added",
            "entity": _component_json(wc), "baseline": None,
            "mappings": _mappings_dto(
                store, repo_id, maps_by.get(wc["id"], []), [], sid),
            "detail": {}})
    for bc in raw["removed_components"]:
        rows.append({
            "bucket": "removed_components", "change": "removed",
            "entity": None, "baseline": dict(bc),
            "mappings": _mappings_dto(
                store, repo_id, [], bc.get("mappings", []), sid),
            "detail": {}})
    for x in raw["modified_components"]:
        wc, bc = x["working"], x["baseline"]
        rows.append({
            "bucket": "modified_components", "change": "modified",
            "entity": _component_json(wc), "baseline": dict(bc),
            "mappings": _mappings_dto(
                store, repo_id, maps_by.get(wc["id"], []),
                bc.get("mappings", []), sid),
            "detail": {"fields": x["fields"]}})
    for x in raw["moved_components"]:
        wc, bc = x["working"], x["baseline"]
        rows.append({
            "bucket": "moved_components", "change": "moved",
            "entity": _component_json(wc), "baseline": dict(bc),
            "mappings": _mappings_dto(
                store, repo_id, maps_by.get(wc["id"], []),
                bc.get("mappings", []), sid),
            "detail": {"from_parent": x["from_parent"],
                       "to_parent": x["to_parent"],
                       "from_parent_name": x["from_parent_name"],
                       "to_parent_name": x["to_parent_name"]}})
    for wr in raw["added_relations"]:
        rows.append({
            "bucket": "added_relations", "change": "added",
            "entity": _names_relation(store, ws["id"], wr.get("model_id"),
                                      wr, w_by_id, b_docs, False),
            "baseline": None, "mappings": [], "detail": {}})
    for br in raw["removed_relations"]:
        rows.append({
            "bucket": "removed_relations", "change": "removed",
            "entity": None, "baseline": dict(br),
            "mappings": [], "detail": {
                "src_name": (b_docs.get(br["src_id"]) or {}).get("name"),
                "dst_name": (b_docs.get(br["dst_id"]) or {}).get("name")}})
    for x in raw["mapping_changes"]:
        wc = x["working"]
        for m in x["added"]:
            rows.append({
                "bucket": "mapping_changes", "change": "added",
                "entity": None, "baseline": None,
                "mappings": [_baseline_mapping_dto(store, repo_id, m, sid)],
                "detail": {"component_id": wc["id"],
                           "component_name": wc["name"]}})
        for m in x["removed"]:
            rows.append({
                "bucket": "mapping_changes", "change": "removed",
                "entity": None, "baseline": None,
                "mappings": [_baseline_mapping_dto(store, repo_id, m, sid)],
                "detail": {"component_id": wc["id"],
                           "component_name": wc["name"]}})

    seq: dict[str, int] = {}
    for row in rows:
        bucket = row["bucket"]
        seq[bucket] = seq.get(bucket, 0) + 1
        row["change_id"] = f"{bucket}:{seq[bucket]}"
    return rows


def _diff_dto(store: Store, ws: dict, model: dict,
              sid: Optional[str]) -> dict:
    """Current(model) − Baseline(model), rows resolved against `sid`."""
    bl = store.arch_model_baseline(ws["id"], model["id"])
    if not bl:
        raise not_a_proposal(model["id"])
    comps = store.arch_components(ws["id"], model["id"])
    rels = store.arch_relations(ws["id"], model["id"])
    raw = compute_diff(bl["baseline"], comps, rels,
                       {c["id"]: store.arch_mappings_for_component(
                           ws["id"], c["id"]) for c in comps})
    rows = _diff_rows(store, ws, sid, bl["baseline"], raw, comps, rels)
    counts = {bucket: sum(1 for r in rows if r["bucket"] == bucket)
              for bucket in ("added_components", "removed_components",
                             "modified_components", "moved_components",
                             "added_relations", "removed_relations",
                             "mapping_changes")}
    return {
        "model_id": model["id"],
        "kind": model["kind"],
        "baseline_schema_version": bl["baseline_schema_version"],
        "base_evidence_snapshot_id": bl["base_evidence_snapshot_id"],
        "is_empty": not rows,
        "counts": counts,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# structural impact (S1, bounded traversal)
# ---------------------------------------------------------------------------
def _entity_name(row: dict) -> str:
    for side in ("entity", "baseline"):
        x = row.get(side)
        if x:
            return x.get("name") or f"{x.get('kind', '?')} relation"
    return row["detail"].get("component_name") or "?"


def _entity_kind(row: dict) -> str:
    for side in ("entity", "baseline"):
        if row.get(side):
            return row[side]["kind"]
    return "component"


def _impact_bounds(store: Store, repo_id: str, sid: str,
                   seeds: list[dict]) -> tuple[list[dict], list[dict], bool]:
    """Seed mappings → BFS start node ids (+ stale seed report).

    FILE/DIRECTORY seeds expand to their effective symbol set (§14): FILE
    by exact path (nodes_by_path), DIRECTORY by path prefix
    (nodes_by_path_prefix — every node under the mapped directory, S5A fix;
    the graph's CONTAINS chain alone misses symbols nested below MODULE/
    CLASS containers because containment expansion only recurses through
    FILE/DIRECTORY).  Edge seeds become their two endpoint nodes.  Prefix
    expansion is capped at IMPACT_NODE_BUDGET; overflow sets the returned
    truncated flag (the traversal never silently drops seeds).
    """
    starts: list[str] = []
    stale: list[dict] = []
    truncated = False
    for s in seeds:
        if s["entity_type"] == "node":
            r = store.entity_row(repo_id, sid, "node", s["entity_id"])
            if not r:
                stale.append({"entity_type": "node",
                              "entity_id": s["entity_id"]})
                continue
            if r["kind"] == "FILE":
                for sub in store.nodes_by_path(repo_id, sid, r["path"]):
                    if len(starts) < IMPACT_NODE_BUDGET:
                        starts.append(sub["id"])
                    else:
                        truncated = True
                        break
            elif r["kind"] == "DIRECTORY":
                for sub in store.nodes_by_path_prefix(
                        repo_id, sid, r["path"]):
                    if len(starts) < IMPACT_NODE_BUDGET:
                        starts.append(sub["id"])
                    else:
                        truncated = True
                        break
            else:
                if len(starts) < IMPACT_NODE_BUDGET:
                    starts.append(s["entity_id"])
                else:
                    truncated = True
        else:
            r = store.entity_row(repo_id, sid, "edge", s["entity_id"])
            if not r:
                stale.append({"entity_type": "edge",
                              "entity_id": s["entity_id"]})
                continue
            for nid in (r["src_id"], r["dst_id"]):
                if len(starts) < IMPACT_NODE_BUDGET:
                    starts.append(nid)
                else:
                    truncated = True
                    break
    return list(dict.fromkeys(starts)), stale, truncated


def _bfs_impact(store: Store, repo_id: str, sid: str,
                start_ids: list[str]) -> dict:
    """Bounded traversal (DEP_KINDS, depth ≤ IMPACT_DEPTH) with chains."""
    cache: dict[str, Optional[dict]] = {}

    def nrow(nid: str) -> Optional[dict]:
        if nid not in cache:
            cache[nid] = store.entity_row(repo_id, sid, "node", nid)
        return cache[nid]

    visited: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for nid in start_ids:
        if nrow(nid) and nid not in visited:
            visited[nid] = (None, None)
    frontier: list[tuple[str, int]] = [
        (nid, 0) for nid in start_ids if nrow(nid)]
    support: list[dict] = []
    edge_seen: set[tuple[str, str, str]] = set()
    scanned = 0
    truncated = False
    while frontier:
        if len(visited) > IMPACT_NODE_BUDGET:
            truncated = True
            break
        nid, depth = frontier.pop(0)
        cur = nrow(nid)
        is_container = cur is not None and cur["kind"] in CONTAINER_KINDS
        for e in store.edges_for_node(repo_id, sid, nid, direction="both"):
            scanned += 1
            if scanned > IMPACT_EDGE_BUDGET:
                truncated = True
                break
            kind = e["kind"]
            other = e["dst_id"] if e["src_id"] == nid else e["src_id"]
            if (is_container and kind == CONTAINS
                    and e["src_id"] == nid):
                if other not in visited and nrow(other):
                    visited[other] = (nid, None)  # expansion, no depth
                    frontier.append((other, depth))
                continue
            if kind not in DEP_KINDS:
                continue
            ek = (kind, e["src_id"], e["dst_id"])
            if ek not in edge_seen:
                edge_seen.add(ek)
                support.append(e)
            if other not in visited:
                visited[other] = (nid, kind)
                if depth + 1 <= IMPACT_DEPTH:
                    frontier.append((other, depth + 1))
        if truncated:
            break

    def name_of(nid: str) -> str:
        r = nrow(nid)
        if not r:
            return nid
        return r.get("qname") or r["name"] or nid

    def chain_of(nid: str) -> list[str]:
        segs: list[tuple[str, str]] = []
        cur = nid
        prev, ek = visited.get(cur, (None, None))
        while prev is not None:
            segs.append((ek or "", name_of(cur)))
            cur = prev
            prev, ek = visited.get(cur, (None, None))
        parts = [name_of(cur)]
        for edge_kind, nm in reversed(segs):
            parts.append(edge_kind)
            parts.append(nm)
        return parts

    files: dict[str, dict] = {}
    symbols: list[dict] = []
    modules: list[dict] = []
    for nid, (_, _ek) in visited.items():
        r = nrow(nid)
        if not r:
            continue
        kind = r["kind"]
        path = r.get("path")
        if kind == "FILE":
            files.setdefault(path, {"symbol_count": 0, "chain": None,
                                    "start_line": r.get("start_line")})
            if files[path]["chain"] is None:
                files[path]["chain"] = chain_of(nid)
        elif kind == "DIRECTORY":
            continue
        else:
            entry = {"id": r["id"], "kind": kind, "name": r["name"],
                     "qname": r.get("qname"), "path": path,
                     "chain": chain_of(nid),
                     "start_line": r.get("start_line")}
            if kind == "MODULE":
                modules.append(entry)
            else:
                symbols.append(entry)
            if path is not None:
                f = files.setdefault(
                    path, {"symbol_count": 0, "chain": None,
                           "start_line": r.get("start_line")})
                f["symbol_count"] += 1
                if f["chain"] is None:
                    f["chain"] = entry["chain"]
    file_list = [{"path": p, "symbol_count": v["symbol_count"],
                  "chain": v["chain"] or [],
                  "start_line": v["start_line"]}
                 for p, v in files.items()]
    file_list.sort(key=lambda f: f["path"])
    return {
        "files": file_list,
        "symbols": symbols,
        "modules": modules,
        "edges": [{"id": e["id"], "kind": e["kind"],
                   "src_id": e["src_id"], "dst_id": e["dst_id"]}
                  for e in support],
        "used_nodes": len(visited),
        "used_edges": scanned,
        "truncated": truncated,
    }


def _impact_row(store: Store, repo_id: str, sid: str,
                row: dict) -> dict:
    starts, stale, seeds_truncated = _impact_bounds(
        store, repo_id, sid, row["mappings"])
    res = _bfs_impact(store, repo_id, sid, starts)
    seeds = []
    for m in row["mappings"]:
        seeds.append({"entity_type": m["entity_type"],
                      "entity_id": m["entity_id"],
                      "source": m.get("source", "proposal"),
                      "entity": m.get("entity")})
    return {
        "change_id": row["change_id"],
        "bucket": row["bucket"],
        "entity_name": _entity_name(row),
        "entity_kind": _entity_kind(row),
        "seeds": seeds,
        "stale_seeds": stale,
        "files": res["files"],
        "symbols": res["symbols"],
        "modules": res["modules"],
        "edges": res["edges"],
        "used_nodes": res["used_nodes"],
        "used_edges": res["used_edges"],
        "truncated": res["truncated"] or seeds_truncated,
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@router.post("/{workspace_id}/models", status_code=201)
def fork_model(workspace_id: str, body: ModelForkCreate,
               store: Store = Depends(get_store)) -> dict:
    """§7-24 (R2+fix1): fork the AS-IS model → proposal with frozen baseline."""
    legacy_design_write_deprecated()
    ws = _require_workspace(store, workspace_id)
    if body.kind != "proposal":
        raise ApiError(422, "invalid_model_kind",
                       f"kind '{body.kind}' is not forkable; only 'proposal'"
                       " is accepted")
    as_is = None
    for m in store.arch_models(workspace_id):
        if m["kind"] == "as_is":
            as_is = m
            break
    if not as_is:
        raise model_not_found("as-is model of this workspace")
    try:
        model = store.arch_fork_model(workspace_id, as_is["id"],
                                      body.name, body.description)
    except ArchError as exc:
        if exc.code == "model_not_found":
            raise model_not_found(exc.message) from exc
        if exc.code == "fork_source_not_as_is":
            raise ApiError(422, "fork_source_not_as_is", exc.message,
                           exc.details) from exc
        raise
    sid = store.current_snapshot(ws["repo_id"])
    diff = _diff_dto(store, ws, model, sid)
    return {"model": _model_json(model), "diff": diff}


@router.get("/{workspace_id}/models/{model_id}")
def get_model(workspace_id: str, model_id: str,
              store: Store = Depends(get_store)) -> dict:
    """§7-25 GET: model json incl. baseline metadata (no baseline payload)."""
    _require_workspace(store, workspace_id)
    m = _require_model(store, workspace_id, model_id)
    return _model_json(m)


@router.get("/{workspace_id}/models/{model_id}/diff")
def get_model_diff(workspace_id: str, model_id: str,
                   snapshot: str | None = None,
                   store: Store = Depends(get_store)) -> dict:
    """§7-26: working vs frozen baseline (R2); not vs the current AS-IS."""
    ws = _require_workspace(store, workspace_id)
    m = _require_model(store, workspace_id, model_id)
    sid = _resolve_snapshot(store, ws, snapshot)
    return _diff_dto(store, ws, m, sid)


@router.post("/{workspace_id}/models/{model_id}/impact")
def model_impact(workspace_id: str, model_id: str,
                 body: ImpactRequest,
                 store: Store = Depends(get_store)) -> dict:
    """Structural Impact (S1): changed arch entities → mapped evidence →
    bounded traversal → potentially affected files/symbols/modules.

    Impact seeds (user ruling): added → proposal mappings; removed →
    baseline mappings; modified/moved → baseline ∪ proposal mappings.
    Only the explicitly resolved snapshot is traversed.
    """
    ws = _require_workspace(store, workspace_id)
    m = _require_model(store, workspace_id, model_id)
    sid = _resolve_snapshot(store, ws, body.snapshot_id)
    diff = _diff_dto(store, ws, m, sid)
    rows = diff["rows"]
    if body.change_ids:
        by_id = {r["change_id"]: r for r in rows}
        unknown = [cid for cid in body.change_ids if cid not in by_id]
        if unknown:
            raise invalid_change(unknown)
        rows = [by_id[cid] for cid in body.change_ids]
    changes = [_impact_row(store, ws["repo_id"], sid, row) for row in rows]
    totals = {"files": sum(len(c["files"]) for c in changes),
              "symbols": sum(len(c["symbols"]) for c in changes),
              "modules": sum(len(c["modules"]) for c in changes),
              "edges": sum(len(c["edges"]) for c in changes)}
    return {
        "model_id": model_id,
        "snapshot_id": sid,
        "budgets": {"depth": IMPACT_DEPTH,
                    "node_budget": IMPACT_NODE_BUDGET,
                    "edge_budget": IMPACT_EDGE_BUDGET,
                    "used_nodes": max((c["used_nodes"] for c in changes),
                                      default=0),
                    "used_edges": max((c["used_edges"] for c in changes),
                                      default=0)},
        "truncated": any(c["truncated"] for c in changes),
        "changes": changes,
        "totals": totals,
    }
