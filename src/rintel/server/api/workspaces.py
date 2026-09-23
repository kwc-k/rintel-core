"""Architecture workspace endpoints (SPEC-P1 §7-13..22, S3 slice).

Covers the S3 scope: workspaces (auto empty AS-IS model, fix1), components
(CRUD + RESTRICT delete + R4 batch create), human relations, evidence
mappings (+ batch), and layout persistence.  S4 features (proposal fork,
diff, annotations, ui-state API) are intentionally absent.

Business rules live here on top of thin store CRUD:
- R1: every arch write is model-scoped; cross-model refs → 422
  `cross_model_reference` (DB composite FKs are the backstop);
- R4: batch create / batch mappings are all-or-nothing (422 `invalid_entity`);
- R5: component delete is RESTRICT (409 with dependents) unless
  `?subtree=true` deletes the whole subtree in one transaction;
- hierarchy cycles are rejected at the service layer (422 `hierarchy_cycle`);
- mapping staleness (missing from the current snapshot) is a first-class
  flag, never an error and never auto-deleted (SPEC §14).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...store import ArchError, Store
from ..deps import get_store, require_repo
from ..errors import (ApiError, component_not_found, mapping_not_found,
                      model_not_found, relation_not_found,
                      workspace_not_found)
from .design_write import legacy_design_write_deprecated

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

ComponentKind = Literal["component", "subsystem", "layer", "service",
                        "boundary", "interface", "datastore",
                        "external_system", "group"]
RelationKind = Literal["DEPENDS_ON", "USES", "CALLS", "DATA_FLOW",
                       "PROVIDES", "CONTAINS"]

RELATION_KINDS: tuple[str, ...] = ("DEPENDS_ON", "USES", "CALLS",
                                   "DATA_FLOW", "PROVIDES", "CONTAINS")
COMPONENT_KINDS: tuple[str, ...] = ("component", "subsystem", "layer",
                                    "service", "boundary", "interface",
                                    "datastore", "external_system", "group")


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------
class WorkspaceCreate(BaseModel):
    repo_id: str
    name: str
    description: str = ""


class ComponentCreate(BaseModel):
    model_id: str
    kind: ComponentKind
    name: str = Field(min_length=1)
    parent_id: Optional[str] = None
    description: str = ""


class ComponentPatch(BaseModel):
    model_id: str
    name: Optional[str] = Field(default=None, min_length=1)
    kind: Optional[ComponentKind] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None  # null = move out of the hierarchy


class ComponentBatchCreate(BaseModel):
    model_id: str
    kind: ComponentKind
    name: str = Field(min_length=1)
    entity_ids: list[str] = Field(min_length=1)
    parent_id: Optional[str] = None
    description: str = ""
    note: str = ""


class RelationCreate(BaseModel):
    model_id: str
    kind: RelationKind
    src_id: str
    dst_id: str
    label: Optional[str] = None


class RelationPatch(BaseModel):
    model_id: str
    kind: Optional[RelationKind] = None
    label: Optional[str] = None  # null = clear the label


class MappingCreate(BaseModel):
    component_id: str
    entity_type: Literal["node", "edge"]
    entity_id: str
    note: str = ""


class MappingBatch(BaseModel):
    component_id: str
    entity_ids: list[str] = Field(min_length=1)
    note: str = ""


class LayoutPut(BaseModel):
    layout: dict
    updated_at: Optional[int] = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _require_workspace(store: Store, wid: str) -> dict:
    ws = store.arch_workspace(wid)
    if not ws:
        raise workspace_not_found(wid)
    return ws


def _require_model(store: Store, wid: str, mid: str) -> dict:
    m = store.arch_model(wid, mid)
    if not m:
        raise model_not_found(mid)
    return m


def _require_component(store: Store, wid: str, cid: str,
                       mid: str | None = None) -> dict:
    c = store.arch_component(wid, cid, mid)
    if not c:
        raise component_not_found(cid)
    return c


def _require_relation(store: Store, wid: str, rid: str,
                      mid: str | None = None) -> dict:
    r = store.arch_relation(wid, rid, mid)
    if not r:
        raise relation_not_found(rid)
    return r


def _component_json(c: dict) -> dict:
    return {k: c[k] for k in ("id", "model_id", "kind", "name",
                              "description", "parent_id", "sort_order",
                              "created_at", "updated_at")}


def _model_json(m: dict) -> dict:
    """Model row → API json (baseline metadata; no baseline_json payload)."""
    out = {k: m[k] for k in ("id", "workspace_id", "kind", "name",
                             "description", "status", "parent_model_id",
                             "base_evidence_snapshot_id",
                             "baseline_schema_version", "created_at",
                             "updated_at")}
    out["has_baseline"] = m["kind"] == "proposal"
    return out


def _relation_json(r: dict) -> dict:
    return {k: r[k] for k in ("id", "model_id", "kind", "src_id", "dst_id",
                              "label", "created_at", "updated_at")}


def _entity_format(entity_id: str) -> tuple[str | None, str | None]:
    """Canonical evidence id → (entity_type|None, reason|None)."""
    if entity_id.startswith("node:"):
        return "node", None
    if entity_id.startswith("edge:"):
        return "edge", None
    return None, ("invalid canonical id; must start with 'node:' or 'edge:'")


def _check_parent(store: Store, wid: str, mid: str,
                  parent_id: str | None) -> None:
    """Parent must exist inside the same model (R1)."""
    if parent_id is None:
        return
    if store.arch_component(wid, parent_id, mid):
        return
    if store.arch_component(wid, parent_id):
        raise ApiError(
            422, "cross_model_reference",
            "parent component belongs to a different model; arch assets are"
            " model-scoped (R1)",
            {"component_id": parent_id, "model_id": mid})
    raise component_not_found(parent_id)


def _would_create_cycle(store: Store, wid: str, mid: str, cid: str,
                        new_parent_id: str) -> bool:
    """True when `new_parent_id` is `cid` or a descendant of `cid`."""
    node = store.arch_component(wid, new_parent_id, mid)
    seen: set[str] = set()
    while node is not None:
        if node["id"] == cid:
            return True
        if node["id"] in seen:
            return False  # defensive: pre-existing corruption, don't loop
        seen.add(node["id"])
        pid = node["parent_id"]
        node = store.arch_component(wid, pid, mid) if pid else None
    return False


def _check_relation_endpoints(store: Store, wid: str, mid: str,
                              src_id: str, dst_id: str) -> None:
    for cid, role in ((src_id, "src"), (dst_id, "dst")):
        if store.arch_component(wid, cid, mid):
            continue
        if store.arch_component(wid, cid):
            raise ApiError(
                422, "cross_model_reference",
                f"relation {role} component belongs to a different model;"
                " arch assets are model-scoped (R1)",
                {"component_id": cid, "model_id": mid})
        raise component_not_found(cid)


def _validate_entity_ids(store: Store, repo_id: str,
                         entity_ids: list[str]) -> None:
    """R4: every entity must exist in the repo's current snapshot."""
    sid = store.current_snapshot(repo_id)
    errors = []
    for eid in entity_ids:
        etype, reason = _entity_format(eid)
        if reason:
            errors.append({"entity_id": eid, "reason": reason})
            continue
        if sid is None:
            errors.append({"entity_id": eid,
                           "reason": "repository has no snapshot yet"})
            continue
        if not store.entity_exists(repo_id, sid, etype, eid):
            errors.append({"entity_id": eid,
                           "reason": "not present in the current snapshot"})
    if errors:
        raise ApiError(422, "invalid_entity",
                       "some evidence entities are invalid; nothing was"
                       " created (all-or-nothing)",
                       {"errors": errors})


def _mapping_entities(store: Store, repo_id: str,
                      mapping: dict,
                      snapshot_id: str | None = None) -> dict:
    """mapping row → API json + stale flag + resolved evidence entity."""
    sid = snapshot_id or store.current_snapshot(repo_id)
    out = {
        "id": mapping["id"],
        "component_id": mapping["component_id"],
        "entity_type": mapping["evidence_entity_type"],
        "entity_id": mapping["evidence_entity_id"],
        "evidence_snapshot_id": mapping["evidence_snapshot_id"],
        "note": mapping["note"],
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }
    entity = None
    stale = True
    if sid is not None and store.entity_exists(
            repo_id, sid, mapping["evidence_entity_type"],
            mapping["evidence_entity_id"]):
        entity = store.entity_row(repo_id, sid,
                                  mapping["evidence_entity_type"],
                                  mapping["evidence_entity_id"])
        stale = False
    out["stale"] = stale
    out["entity"] = entity
    return out


def _to_api(exc: ArchError) -> ApiError:
    """Translate a store-level backstop failure into the API envelope."""
    if exc.code == "delete_restricted":
        children = exc.details.get("children") or []
        relations = exc.details.get("relations") or []
        if children:
            return ApiError(409, "component_has_children",
                            exc.message,
                            {"children": [_component_json(c)
                                          for c in children]})
        if relations:
            return ApiError(409, "component_has_relations",
                            exc.message,
                            {"relations": [_relation_json(r)
                                           for r in relations]})
        return ApiError(409, "delete_restricted", exc.message)
    if exc.code == "cross_model_reference":
        return ApiError(422, "cross_model_reference", exc.message,
                        exc.details)
    if exc.code == "mapping_exists":
        return ApiError(409, "mapping_exists", exc.message, exc.details)
    if exc.code == "layout_conflict":
        return ApiError(409, "layout_conflict", exc.message, exc.details)
    return ApiError(422, exc.code, exc.message, exc.details)


def _guard(fn):
    """Run an arch mutation; translate ArchError backstops."""
    try:
        return fn()
    except ArchError as exc:
        raise _to_api(exc) from exc


# ---------------------------------------------------------------------------
# workspaces
# ---------------------------------------------------------------------------
@router.post("", status_code=201)
def create_workspace(body: WorkspaceCreate,
                     store: Store = Depends(get_store)) -> dict:
    """§7-13: workspace + auto empty AS-IS model (R3+fix1)."""
    legacy_design_write_deprecated()
    require_repo(store, body.repo_id)
    ws = store.arch_create_workspace(body.repo_id, body.name,
                                     body.description)
    model = _model_json(store.arch_model(ws["id"],
                                         store.arch_models(ws["id"])[0]["id"]))
    return {"workspace": ws, "model": model}


@router.get("")
def list_workspaces(store: Store = Depends(get_store)) -> dict:
    items = store.arch_workspaces()
    return {"items": items, "total": len(items)}


@router.get("/{workspace_id}")
def get_workspace(workspace_id: str, snapshot: str | None = None,
                  store: Store = Depends(get_store)) -> dict:
    """§7-13 bootstrap payload (fix1: models[] incl. auto AS-IS)."""
    ws = _require_workspace(store, workspace_id)
    sid = None
    if snapshot is not None:
        snap = store.snapshot(snapshot)
        if not snap or snap["repo_id"] != ws["repo_id"]:
            from ..errors import snapshot_not_found
            raise snapshot_not_found(snapshot)
        sid = snapshot
    else:
        sid = store.current_snapshot(ws["repo_id"])
    models = [_model_json(m) for m in store.arch_models(workspace_id)]
    components: list[dict] = []
    relations: list[dict] = []
    mappings: list[dict] = []
    layout = None
    layouts: dict[str, dict] = {}
    staleness: list[dict] = []
    as_is: dict | None = None
    for m in models:
        if m["kind"] == "as_is" and as_is is None:
            as_is = m
        lay = store.arch_layout(workspace_id, m["id"])
        if lay:
            layouts[m["id"]] = lay["layout"]
        for c in store.arch_components(workspace_id, m["id"]):
            components.append(_component_json(c))
        for r in store.arch_relations(workspace_id, m["id"]):
            relations.append(_relation_json(r))
        for mp in store.arch_mappings(workspace_id, m["id"]):
            dto = _mapping_entities(store, ws["repo_id"], mp, sid)
            mappings.append(dto)
            if dto["stale"]:
                staleness.append({"mapping_id": dto["id"],
                                  "entity_type": dto["entity_type"],
                                  "entity_id": dto["entity_id"]})
    if as_is is not None:
        layout = layouts.get(as_is["id"])
    return {
        "workspace": ws,
        "models": models,
        "components": components,
        "relations": relations,
        "mappings": mappings,
        "layout": layout,
        "layouts": layouts,
        "has_components": bool(components),
        "snapshot": sid,
        "mapping_staleness": staleness,
        "annotations": [],
    }


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------
@router.post("/{workspace_id}/components", status_code=201)
def create_component(workspace_id: str, body: ComponentCreate,
                     store: Store = Depends(get_store)) -> dict:
    """§7-15: model_id required (R1); cross-model parent rejected."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_model(store, workspace_id, body.model_id)
    _check_parent(store, workspace_id, body.model_id, body.parent_id)
    c = _guard(lambda: store.arch_create_component(
        workspace_id, body.model_id, body.kind, body.name, body.description,
        body.parent_id))
    return _component_json(c)


@router.post("/{workspace_id}/components/batch", status_code=201)
def batch_create_component(workspace_id: str, body: ComponentBatchCreate,
                           store: Store = Depends(get_store)) -> dict:
    """§7-19 (R4): N evidence entities → 1 component + N mappings, atomic."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_model(store, workspace_id, body.model_id)
    ws = store.arch_workspace(workspace_id)
    _check_parent(store, workspace_id, body.model_id, body.parent_id)
    _validate_entity_ids(store, ws["repo_id"], body.entity_ids)
    entities = []
    for eid in body.entity_ids:
        etype, _ = _entity_format(eid)
        entities.append((etype, eid, body.note))
    res = _guard(lambda: store.arch_batch_component(
        workspace_id, body.model_id, body.kind, body.name, body.description,
        body.parent_id, entities))
    return {"component": _component_json(res["component"]),
            "mappings": [_mapping_entities(store, ws["repo_id"], mp)
                         for mp in res["mappings"]]}


@router.patch("/{workspace_id}/components/{component_id}")
def update_component(workspace_id: str, component_id: str,
                     body: ComponentPatch,
                     store: Store = Depends(get_store)) -> dict:
    """§7-16: rename / describe / kind / reparent (cycle-guarded)."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_component(store, workspace_id, component_id, body.model_id)
    fields = body.model_fields_set
    parent_clear = False
    parent_id = None
    if "parent_id" in fields:
        if body.parent_id is None:
            parent_clear = True
        else:
            parent_id = body.parent_id
            _check_parent(store, workspace_id, body.model_id, parent_id)
            if _would_create_cycle(store, workspace_id, body.model_id,
                                   component_id, parent_id):
                raise ApiError(422, "hierarchy_cycle",
                               "this parent change would create a cycle in"
                               " the component hierarchy")
    c = _guard(lambda: store.arch_update_component(
        workspace_id, body.model_id, component_id,
        kind=body.kind if "kind" in fields else None,
        name=body.name if "name" in fields else None,
        description=body.description if "description" in fields else None,
        parent_id=parent_id,
        clear_parent=parent_clear))
    return _component_json(c)


@router.delete("/{workspace_id}/components/{component_id}")
def delete_component(workspace_id: str, component_id: str,
                     model_id: str | None = Query(default=None),
                     subtree: bool = Query(default=False),
                     store: Store = Depends(get_store)) -> dict:
    """§7-16 (R5): RESTRICT by default; `subtree=true` deletes the subtree."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    c = _require_component(store, workspace_id, component_id, model_id)
    res = _guard(lambda: store.arch_delete_component(
        workspace_id, c["model_id"], component_id, subtree=subtree))
    return {"ok": True, "deleted": res}


# ---------------------------------------------------------------------------
# relations (human-created only; no automatic derivation)
# ---------------------------------------------------------------------------
@router.post("/{workspace_id}/relations", status_code=201)
def create_relation(workspace_id: str, body: RelationCreate,
                    store: Store = Depends(get_store)) -> dict:
    """§7-17: both endpoints in the same model (R1)."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_model(store, workspace_id, body.model_id)
    _check_relation_endpoints(store, workspace_id, body.model_id,
                              body.src_id, body.dst_id)
    if body.src_id == body.dst_id:
        raise ApiError(422, "relation_self_loop",
                       "a relation cannot connect a component to itself")
    for r in store.arch_relations(workspace_id, body.model_id):
        if (r["kind"], r["src_id"], r["dst_id"]) == \
                (body.kind, body.src_id, body.dst_id):
            raise ApiError(409, "relation_exists",
                           "an identical relation already exists")
    r = _guard(lambda: store.arch_create_relation(
        workspace_id, body.model_id, body.kind, body.src_id, body.dst_id,
        body.label))
    return _relation_json(r)


@router.patch("/{workspace_id}/relations/{relation_id}")
def update_relation(workspace_id: str, relation_id: str,
                    body: RelationPatch,
                    store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_relation(store, workspace_id, relation_id, body.model_id)
    fields = body.model_fields_set
    label = body.label if "label" in fields else None
    r = _guard(lambda: store.arch_update_relation(
        workspace_id, body.model_id, relation_id,
        kind=body.kind if "kind" in fields else None,
        label=label,
        clear_label=("label" in fields and body.label is None)))
    return _relation_json(r)


@router.delete("/{workspace_id}/relations/{relation_id}")
def delete_relation(workspace_id: str, relation_id: str,
                    model_id: str | None = Query(default=None),
                    store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_relation(store, workspace_id, relation_id, model_id)
    _guard(lambda: store.arch_delete_relation(workspace_id, relation_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# mappings (Architecture ↔ Evidence; evidence plane stays read-only)
# ---------------------------------------------------------------------------
@router.post("/{workspace_id}/mappings", status_code=201)
def create_mapping(workspace_id: str, body: MappingCreate,
                   store: Store = Depends(get_store)) -> dict:
    """§7-18: format-valid ids are mapped even when currently stale."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_component(store, workspace_id, body.component_id)
    etype, reason = _entity_format(body.entity_id)
    if reason:
        raise ApiError(422, "invalid_entity",
                       "mapping target is not a canonical evidence id",
                       {"errors": [{"entity_id": body.entity_id,
                                    "reason": reason}]})
    if etype != body.entity_type:
        raise ApiError(
            422, "invalid_entity",
            f"entity_type '{body.entity_type}' does not match the canonical"
            f" id prefix '{body.entity_id.split(':')[0]}:': use"
            f" '{etype}'",
            {"errors": [{"entity_id": body.entity_id,
                         "reason": "entity_type/entity_id mismatch"}]})
    if store.arch_mapping_exists(workspace_id, body.component_id,
                                 etype, body.entity_id):
        raise ApiError(409, "mapping_exists",
                       "this evidence entity is already mapped to the"
                       " component")
    ws = store.arch_workspace(workspace_id)
    rows = _guard(lambda: store.arch_batch_mappings(
        workspace_id, body.component_id,
        [(etype, body.entity_id, body.note)]))
    return _mapping_entities(store, ws["repo_id"], rows[0])


@router.post("/{workspace_id}/mappings/batch", status_code=201)
def batch_mappings(workspace_id: str, body: MappingBatch,
                   store: Store = Depends(get_store)) -> dict:
    """§7-20 (R4): transactional add; any invalid id rolls everything back."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_component(store, workspace_id, body.component_id)
    ws = store.arch_workspace(workspace_id)
    _validate_entity_ids(store, ws["repo_id"], body.entity_ids)
    existing = {m["evidence_entity_id"]
                for m in store.arch_mappings_for_component(
                    workspace_id, body.component_id)}
    dupes = [eid for eid in body.entity_ids if eid in existing]
    if dupes:
        raise ApiError(409, "mapping_exists",
                       "one of the entities is already mapped",
                       {"entity_ids": dupes})
    entities = []
    for eid in body.entity_ids:
        etype, _ = _entity_format(eid)
        entities.append((etype, eid, body.note))
    rows = _guard(lambda: store.arch_batch_mappings(
        workspace_id, body.component_id, entities))
    return {"mappings": [_mapping_entities(store, ws["repo_id"], mp)
                         for mp in rows]}


@router.delete("/{workspace_id}/mappings/{mapping_id}")
def delete_mapping(workspace_id: str, mapping_id: str,
                   store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    found = False
    for m in store.arch_models(workspace_id):
        for row in store.arch_mappings(workspace_id, m["id"]):
            if row["id"] == mapping_id:
                found = True
                break
    if not found:
        raise mapping_not_found(mapping_id)
    _guard(lambda: store.arch_delete_mapping(workspace_id, mapping_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------
@router.put("/{workspace_id}/models/{model_id}/layout")
def put_layout(workspace_id: str, model_id: str, body: LayoutPut,
               store: Store = Depends(get_store)) -> dict:
    """§7-22: node positions; optimistic lock via client `updated_at`."""
    legacy_design_write_deprecated()
    _require_workspace(store, workspace_id)
    _require_model(store, workspace_id, model_id)
    ts = _guard(lambda: store.arch_put_layout(
        workspace_id, model_id, body.layout, body.updated_at))
    return {"model_id": model_id, "updated_at": ts}
