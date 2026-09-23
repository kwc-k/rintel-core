"""SYNTHESIS0 design diff: AS-IS vs TO-BE SoftwareNetlist (spec §9/§10).

The diff is the ONLY source of synthesis work (no incidental refactoring).
Both sides are renderer-neutral FlowModel-like dicts
(blocks/ports/nets/composites/resources).  In the product, AS-IS = the
implemented subset of the flow (state != proposed); TO-BE = the edited
flow including proposed objects.
"""
from __future__ import annotations

from typing import Any


def design_diff(as_is: dict, to_be: dict) -> list[dict]:
    """Ordered list of design change descriptors (deterministic)."""
    changes: list[dict] = []

    ab = {b.get("id"): b for b in as_is.get("blocks", [])}
    tb = {b.get("id"): b for b in to_be.get("blocks", [])}
    # added / modified blocks
    for bid in sorted(tb):
        b = tb[bid]
        if bid not in ab:
            changes.append({
                "kind": "block_added",
                "design_object": f"block:{b.get('name', bid)}",
                "detail": {"block": b},
                "as_is": None, "to_be": b,
            })
        else:
            a = ab[bid]
            diff = _block_diff(a, b)
            if diff:
                changes.append({
                    "kind": "block_modified",
                    "design_object": f"block:{b.get('name', bid)}",
                    "detail": diff,
                    "as_is": a, "to_be": b,
                })
    for bid in sorted(set(ab) - set(tb)):
        changes.append({
            "kind": "block_removed",
            "design_object": f"block:{ab[bid].get('name', bid)}",
            "detail": {"block": ab[bid]},
            "as_is": ab[bid], "to_be": None,
        })

    # ports per block
    ap = {(p.get("block_id"), p.get("name"), p.get("direction")): p
          for p in as_is.get("ports", [])}
    tp = {(p.get("block_id"), p.get("name"), p.get("direction")): p
          for p in to_be.get("ports", [])}
    for key in sorted(tp):
        if key not in ap:
            p = tp[key]
            changes.append({
                "kind": "port_added",
                "design_object": f"port:{key[0]}:{key[1]}",
                "detail": {"port": p},
                "as_is": None, "to_be": p,
            })
        elif ap[key] != tp[key]:
            changes.append({
                "kind": "port_modified",
                "design_object": f"port:{key[0]}:{key[1]}",
                "detail": {"port": tp[key], "before": ap[key]},
                "as_is": ap[key], "to_be": tp[key],
            })
    for key in sorted(set(ap) - set(tp)):
        changes.append({
            "kind": "port_removed",
            "design_object": f"port:{key[0]}:{key[1]}",
            "detail": {"port": ap[key]},
            "as_is": ap[key], "to_be": None,
        })

    # nets (added/removed) — identity = (kind, source_block, target_block)
    an = _net_key_set(as_is)
    tn = _net_key_set(to_be)
    for key in sorted(tn - an):
        n = _net_of(to_be, key)
        changes.append({
            "kind": "net_added",
            "design_object": f"net:{key}",
            "detail": {"net": n},
            "as_is": None, "to_be": n,
        })
    for key in sorted(an - tn):
        n = _net_of(as_is, key)
        changes.append({
            "kind": "net_removed",
            "design_object": f"net:{key}",
            "detail": {"net": n},
            "as_is": n, "to_be": None,
        })

    # composites (boundary / resources)
    ac = {c.get("id"): c for c in as_is.get("composites", [])}
    tc = {c.get("id"): c for c in to_be.get("composites", [])}
    for cid in sorted(tc):
        c = tc[cid]
        if cid not in ac:
            changes.append({
                "kind": "composite_added",
                "design_object": f"composite:{c.get('name', cid)}",
                "detail": {"composite": c},
                "as_is": None, "to_be": c,
            })
        else:
            d = _composite_diff(ac[cid], c)
            if d:
                changes.append({
                    "kind": "composite_modified",
                    "design_object": f"composite:{c.get('name', cid)}",
                    "detail": d,
                    "as_is": ac[cid], "to_be": c,
                })

    # resource claims on blocks
    for bid in sorted(tb):
        ares = set((ab.get(bid) or {}).get("resources", []) or [])
        tres = set((tb[bid].get("resources", []) or []))
        for r in sorted(tres - ares):
            changes.append({
                "kind": "resource_added",
                "design_object": f"resource:{tb[bid].get('name', bid)}:{r}",
                "detail": {"resource": r, "block": tb[bid]},
                "as_is": None, "to_be": {"resource": r},
            })
        for r in sorted(ares - tres):
            changes.append({
                "kind": "resource_removed",
                "design_object": f"resource:{tb[bid].get('name', bid)}:{r}",
                "detail": {"resource": r},
                "as_is": {"resource": r}, "to_be": None,
            })

    # deterministic order: keep diff-kind order stable
    return changes


def _block_diff(a: dict, b: dict) -> dict | None:
    out: dict[str, Any] = {}
    if a.get("state") != b.get("state"):
        out["state"] = {"before": a.get("state"), "after": b.get("state")}
    if a.get("binding") != b.get("binding"):
        out["binding"] = {"before": a.get("binding"), "after": b.get("binding")}
    return out or None


def _composite_diff(a: dict, b: dict) -> dict | None:
    out: dict[str, Any] = {}
    for key in ("data_in", "data_out", "resources"):
        av = set(a.get(key, []) or [])
        bv = set(b.get(key, []) or [])
        if av != bv:
            out[key] = {"before": sorted(av), "after": sorted(bv),
                        "added": sorted(bv - av), "removed": sorted(av - bv)}
    if set(a.get("block_ids", [])) != set(b.get("block_ids", [])):
        out["members"] = {"before": a.get("block_ids", []),
                          "after": b.get("block_ids", [])}
    return out or None


def _net_key_set(d: dict) -> set[tuple]:
    keys = set()
    for n in d.get("nets", []):
        s, t = n.get("source_block_id"), n.get("target_block_id")
        if not s or not t:
            continue
        keys.add((n.get("kind", "data"), s, t))
    return keys


def _net_of(d: dict, key: tuple):
    kind, s, t = key
    for n in d.get("nets", []):
        if n.get("kind", "data") == kind and \
                n.get("source_block_id") == s and n.get("target_block_id") == t:
            return n
    return {"kind": kind, "source_block_id": s, "target_block_id": t}


# ---------------------------------------------------------------------------
# change-type classification (§4 S1–S8 mapping)
# ---------------------------------------------------------------------------

def classify_change(diff: dict, as_is: dict, to_be: dict) -> str:
    kind = diff["kind"]
    if kind == "block_added":
        block = diff["to_be"] or {}
        if block.get("kind") in ("composite",):
            return "S8"
        return "S1"
    if kind == "block_modified":
        detail = diff.get("detail") or {}
        if "binding" in detail:
            return "S1"
        if "state" in detail and (detail["state"].get("after") == "modified"):
            return "S2"           # modified block: signature work follows
        return "S2"
    if kind == "port_added" or kind == "port_modified":
        return "S2"
    if kind == "port_removed":
        return "S2"
    if kind == "net_added":
        net = diff["to_be"] or {}
        if net.get("kind") in ("control", "call"):
            return "S3"
        return "S5"
    if kind == "net_removed":
        net = diff["as_is"] or {}
        if net.get("kind") in ("control", "call"):
            return "S4"
        return "S6"
    if kind == "resource_added":
        return "S7"
    if kind == "resource_removed":
        return "S7"
    if kind == "composite_added" or kind == "composite_modified":
        return "S8"
    if kind == "block_removed":
        return "S4"                # removed call relations dominate
    return "S1"
