"""Frozen-baseline architecture model helpers (SPEC-P1 §6.1-8/13, §7-24/26; S4).

Pure functions shared by the store backends (baseline serialization) and the
API layer (frozen diff).  No I/O; unit-testable in isolation.

Data semantics (user ruling, frozen):
- Diff(P) = Current(P) - Baseline(P); the baseline is written once at fork
  time and never changes when AS-IS evolves afterwards.
- Frozen diff output: added/removed/modified/moved_components +
  added/removed_relations + mapping_changes.  modified = kind/name/description
  change (NOT folded into moved); moved = parent change; the two may overlap.
- Working components match baseline components via ``origin_id`` (set at fork
  to the source AS-IS component id).  Baseline ids are the source ids.
"""
from __future__ import annotations

from typing import Optional

BASELINE_SCHEMA_VERSION = 1

_COMPARE_FIELDS = ("kind", "name", "description")


def build_baseline(components: list[dict],
                   relations: list[dict],
                   mappings_by_component: dict[str, list[dict]],
                   snapshot_id: Optional[str],
                   now: int) -> dict:
    """Serialize a model into the immutable version-1 baseline document.

    ``components``/``relations`` are store rows; ``mappings_by_component``
    maps component id → store mapping rows (``evidence_entity_type`` /
    ``evidence_entity_id`` keys).  Ids stored inside the document are the
    SOURCE ids — proposal components reference them via ``origin_id``.
    """
    comp_docs = []
    for c in sorted(components, key=lambda c: (c.get("sort_order", 0),
                                               c.get("created_at", 0))):
        comp_docs.append({
            "id": c["id"],
            "kind": c["kind"],
            "name": c["name"],
            "description": c.get("description", ""),
            "parent_id": c.get("parent_id"),
            "sort_order": c.get("sort_order", 0),
            "mappings": [{
                "entity_type": m["evidence_entity_type"],
                "entity_id": m["evidence_entity_id"],
                "evidence_snapshot_id": m.get("evidence_snapshot_id"),
                "note": m.get("note", ""),
            } for m in mappings_by_component.get(c["id"], [])],
        })
    rel_docs = [{
        "id": r["id"],
        "kind": r["kind"],
        "src_id": r["src_id"],
        "dst_id": r["dst_id"],
        "label": r.get("label"),
    } for r in relations]
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "forked_at": now,
        "evidence_snapshot_id": snapshot_id,
        "components": comp_docs,
        "relations": rel_docs,
    }


def _norm_mapping(m: dict) -> dict:
    """Store mapping row → baseline-shaped mapping doc."""
    return {
        "entity_type": m["evidence_entity_type"],
        "entity_id": m["evidence_entity_id"],
        "evidence_snapshot_id": m.get("evidence_snapshot_id"),
        "note": m.get("note", ""),
    }


def compute_diff(baseline: dict,
                 components: list[dict],
                 relations: list[dict],
                 mappings_by_component: dict[str, list[dict]]) -> dict:
    """Current(P) − Baseline(P) (user ruling §S4-0.1).

    Returns an intermediate structure consumed by the API layer (which adds
    change ids, entity resolution and DTO shaping):

    - ``added_components``: working rows without a baseline counterpart;
    - ``removed_components``: baseline comp docs without a working counterpart;
    - ``modified_components``: [{working, baseline, fields}];
    - ``moved_components``: [{working, baseline, from_parent, from_parent_name,
      to_parent, to_parent_name}];
    - ``added_relations`` / ``removed_relations``: existence diff by
      (kind, origin(src), origin(dst)) — label-only edits are not reported;
    - ``mapping_changes``: [{working, baseline, added, removed}] only for
      components present on BOTH sides (component-level add/remove already
      covers their mappings).
    """
    b_comps = {c["id"]: c for c in baseline.get("components", [])}
    b_rels = {(r["kind"], r["src_id"], r["dst_id"]): r
              for r in baseline.get("relations", [])}
    w_by_id = {c["id"]: c for c in components}
    w_by_origin = {}
    for c in components:
        if c.get("origin_id"):
            w_by_origin.setdefault(c["origin_id"], c)

    # -- components -----------------------------------------------------
    added_components = []
    removed_components = []
    modified_components = []
    moved_components = []
    for c in components:
        oid = c.get("origin_id")
        if not oid or oid not in b_comps:
            added_components.append(c)
    for bid, bc in b_comps.items():
        if bid not in w_by_origin:
            removed_components.append(bc)
    for oid, wc in w_by_origin.items():
        bc = b_comps[oid]
        fields = [f for f in _COMPARE_FIELDS if wc[f] != bc[f]]
        if fields:
            modified_components.append({"working": wc, "baseline": bc,
                                        "fields": fields})
        w_parent = wc.get("parent_id")
        w_parent_origin = None
        if w_parent and w_parent in w_by_id:
            w_parent_origin = w_by_id[w_parent].get("origin_id")
        b_parent = bc.get("parent_id")
        # moved = the hierarchy changed vs the baseline.  Origin mapping only
        # helps when BOTH sides have a baseline counterpart: a working parent
        # without an origin (e.g. a TO-BE-added group) is by definition a
        # change against the baseline, even when the baseline parent is null.
        if w_parent is None:
            moved = b_parent is not None
        elif w_parent_origin is not None:
            moved = w_parent_origin != b_parent
        else:
            moved = True
        if moved:
            from_name = None
            if b_parent and b_parent in b_comps:
                from_name = b_comps[b_parent]["name"]
            to_name = None
            if w_parent and w_parent in w_by_id:
                to_name = w_by_id[w_parent]["name"]
            moved_components.append({
                "working": wc, "baseline": bc,
                "from_parent": b_parent, "from_parent_name": from_name,
                "to_parent": w_parent, "to_parent_name": to_name,
            })

    # -- relations ------------------------------------------------------
    def _rel_key(r: dict) -> Optional[tuple]:
        src = w_by_id.get(r["src_id"])
        dst = w_by_id.get(r["dst_id"])
        if not src or not dst:
            return None
        so, do = src.get("origin_id"), dst.get("origin_id")
        if not so or not do or so not in b_comps or do not in b_comps:
            return None
        return (r["kind"], so, do)

    w_keys = set()
    added_relations = []
    for r in relations:
        key = _rel_key(r)
        if key is None:
            # endpoints have no baseline counterpart → working-only relation
            added_relations.append(r)
        elif key in b_rels:
            w_keys.add(key)
        else:
            w_keys.add(key)
            added_relations.append(r)
    removed_relations = []
    for key, br in b_rels.items():
        if key not in w_keys:
            removed_relations.append(br)

    # -- mapping changes (matched components only) ----------------------
    mapping_changes = []
    for oid, wc in w_by_origin.items():
        bc = b_comps[oid]
        w_map = {(_m["entity_type"], _m["entity_id"])
                 for _m in (_norm_mapping(m)
                            for m in mappings_by_component.get(wc["id"], []))}
        b_map = {(m["entity_type"], m["entity_id"])
                 for m in bc.get("mappings", [])}
        if w_map == b_map:
            continue
        added = [_norm_mapping(m) for m in
                 mappings_by_component.get(wc["id"], [])
                 if (_norm_mapping(m)["entity_type"],
                     _norm_mapping(m)["entity_id"]) not in b_map]
        removed = [m for m in bc.get("mappings", [])
                   if (m["entity_type"], m["entity_id"]) not in w_map]
        if added or removed:
            mapping_changes.append({"working": wc, "baseline": bc,
                                    "added": added, "removed": removed})

    return {
        "added_components": added_components,
        "removed_components": removed_components,
        "modified_components": modified_components,
        "moved_components": moved_components,
        "added_relations": added_relations,
        "removed_relations": removed_relations,
        "mapping_changes": mapping_changes,
    }
