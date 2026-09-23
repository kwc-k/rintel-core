"""SEMANTIC-SUBSTRATE1 kernel service (§31 — INTERNAL service only).

No new public endpoints; MCP is NOT extended.  Workbench/MCP integration
happens through the existing get_symbol/query paths at the projection layer
(kernel objects referenced via support_refs).

FAC-LIBRARY-COVERAGE0: the full bundle is ~70MB compact — loads are cached
and name/ref lookups go through fac_kernel_index.json (O(1), never a scan).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

KERNEL_OUT = Path("/Users/wu/Documents/dh/a3/fac/repo-intel/analysis_tournament") \
    / "semantic_substrate1"


@lru_cache(maxsize=1)
def _load() -> dict:
    p = KERNEL_OUT / "fac_kernel.json"
    if not p.exists():
        raise FileNotFoundError(f"kernel artifacts missing: {p}")
    return json.loads(p.read_text())


@lru_cache(maxsize=1)
def _load_index() -> dict:
    p = KERNEL_OUT / "fac_kernel_index.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _bundle() -> tuple[dict, dict]:
    b = _load()
    syms = {}
    for s in b.get("symbols", []):
        syms[s["symbol_id"]] = s
    return b, syms


def get_symbol(name: str, file: str | None = None) -> dict:
    """Symbol lookup by name (file-qualified when given).  NEVER merges two
    same-name symbols — candidates are returned as-is (KS2)."""
    b, syms = _bundle()
    by_name = _load_index().get("symbols_by_name", {})
    cands = []
    for sid in by_name.get(name, []):
        s = syms.get(sid)
        if not s:
            continue
        if file and s.get("source_span", {}).get("file") != file:
            continue
        cands.append(s)
    return {"name": name, "count": len(cands),
            "candidates": cands, "single": len(cands) == 1,
            "merged": False}


def resolve_reference(reference_id: str) -> dict | None:
    b, _syms = _bundle()
    idx = _load_index().get("refs_by_id", {}).get(reference_id)
    if idx is None:
        return None
    refs = b.get("references", [])
    return refs[idx] if idx < len(refs) else None


def _ref_by_text(text: str) -> list[dict]:
    b, _syms = _bundle()
    refs = b.get("references", [])
    out = []
    for rid in _load_index().get("refs_by_text", {}).get(text, []):
        idx = _load_index().get("refs_by_id", {}).get(rid)
        if idx is not None and idx < len(refs):
            out.append(refs[idx])
    return out


def get_operations(symbol_id: str) -> list[dict]:
    """All operations whose ACTOR is the symbol (calls/reads/writes it
    performs) plus operations TARGETING it (calls whose callee_entry it is)."""
    b, _syms = _bundle()
    out = []
    for o in b.get("operations", []):
        if o.get("actor") == symbol_id or o.get("callee_entry") == symbol_id:
            out.append(o)
    return out


def trace_binding(symbol_id: str) -> list[dict]:
    """ArgumentBinding lineage (KS6): for a formal parameter symbol, every
    binding that fills it + the CALL operation + its caller context."""
    b, syms = _bundle()
    ctxs = {c["context_id"]: c for c in b.get("contexts", [])}
    ops = {o["operation_id"]: o for o in b.get("operations", [])}
    # CALLABLE -> FUNCTION context map: the callable's declaration context is
    # its FILE ctx; the FUNCTION ctx has the SAME file+start line and its
    # parent is that FILE ctx (identity linkage, no separate object needed).
    by_fn_ctx: dict[str, dict] = {}
    for s in syms.values():
        if s.get("kind") != "CALLABLE" or not s.get("is_definition", True):
            continue
        for c in ctxs.values():
            if (c.get("kind") == "FUNCTION"
                    and c.get("parent_context_id") == s.get("declaration_context")
                    and c.get("source_span", {}).get("start_line")
                    == s.get("source_span", {}).get("start_line")
                    and c.get("source_span", {}).get("file")
                    == s.get("source_span", {}).get("file")):
                by_fn_ctx[c["context_id"]] = s
                break
    out = []
    for bd in b.get("bindings", []):
        if bd.get("formal_symbol_id") != symbol_id:
            continue
        op = ops.get(bd["call_operation_id"])
        entry = {"binding": bd,
                 "call": {"operation_id": bd["call_operation_id"],
                          "kind": op and op.get("kind"),
                          "callee_entry": op and op.get("callee_entry"),
                          "coverage": op and op.get("coverage"),
                          "truth": op and op.get("truth"),
                          "source_span": op and op.get("source_span"),
                          "call_site_id": op and op.get("call_site_id")}}
        if op and op.get("context_id"):
            ctx = ctxs.get(op["context_id"])
            if ctx:
                owner = by_fn_ctx.get(ctx["context_id"]) \
                    or (syms.get(op.get("actor")) or {})
                entry["caller"] = {"context_id": ctx["context_id"],
                                   "kind": ctx.get("kind"),
                                   "function": owner.get("name"),
                                   "function_symbol_id": owner.get("symbol_id")}
        out.append(entry)
    return out


def get_support(kernel_symbol_id: str) -> dict:
    """Support refs (KS7): existing projection objects that point at the
    kernel symbol (ports / flow regions / semantic stages)."""
    p = KERNEL_OUT / "support_refs.json"
    if not p.exists():
        return {"kernel_symbol_id": kernel_symbol_id,
                "ports": [], "flow_regions": [], "semantic_stages": []}
    refs = json.loads(p.read_text())
    out = {"kernel_symbol_id": kernel_symbol_id, "ports": [],
           "flow_regions": [], "semantic_stages": []}
    for key in ("ports", "flow_regions", "semantic_stages"):
        for r in refs.get(key, []):
            if r.get("kernel_symbol_id") == kernel_symbol_id:
                out[key].append(r)
    return out
