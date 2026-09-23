"""FAC-LIBRARY-COVERAGE0: kernel-derived call-graph extension (LC6, §16).

The frozen FAC topology (TOPO-ENGINE0, joern) covers the command layer only —
faclib targets were "[Unknown Dynamic Target]".  This module derives a
frozen-SCHEMA topology extension from the SAME-SOURCE kernel CALL graph
(kernel operations carry the real source lines), so flow inference can
naturally resolve command → faclib and faclib → faclib calls.

Additive and read-only: the frozen fac_c.topology.json is never modified; the
extension is merged at FacFacts level (frozen node/edge ids win on conflict).
"""
from __future__ import annotations

import json
from pathlib import Path

KERNEL_BUNDLE = Path("/Users/wu/Documents/dh/a3/fac/repo-intel/analysis_tournament") \
    / "semantic_substrate1" / "fac_kernel.json"

# only functions whose body is really covered (definitions); faclib + blas/
# lapack Fortran + command layer become resolvable targets (LC2)
EXT_FILE_PRED = lambda rel: rel.startswith(("faclib/", "blas/", "lapack/", "sfac/"))  # noqa: E731


def build_extension(kernel_bundle: Path | None = None) -> dict:
    """frozen-schema nodes+edges derived from kernel CALL operations."""
    p = kernel_bundle or KERNEL_BUNDLE
    if not p.exists():
        return {"nodes": [], "edges": []}
    b = json.loads(p.read_text())
    syms = {s["symbol_id"]: s for s in b.get("symbols", [])}
    ctx = {c["context_id"]: c for c in b.get("contexts", [])}
    ops = [o for o in b.get("operations", []) if o.get("kind") == "CALL"]

    def file_of(sid: str) -> str | None:
        s = syms.get(sid)
        if not s:
            return None
        c = ctx.get(s.get("declaration_context"))
        return (c or {}).get("source_span", {}).get("file")

    def node_id(sid: str):
        s = syms.get(sid)
        f = file_of(sid)
        if not s or not f:
            return None
        return f"node:FUNCTION:{f}::{s['name']}"

    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    edge_count = 0
    for o in ops:
        src = node_id(o.get("actor"))
        tgt_sid = o.get("callee_entry")
        if not src or not tgt_sid:
            continue
        tgt = node_id(tgt_sid)
        if not tgt:
            continue
        sfile = file_of(o.get("actor")) or ""
        tfile = file_of(tgt_sid) or ""
        if not EXT_FILE_PRED(sfile) and not EXT_FILE_PRED(tfile):
            continue
        # nodes (keep only covered-definition symbols)
        for sid, nid in ((o.get("actor"), src), (tgt_sid, tgt)):
            s = syms.get(sid)
            if s and s.get("is_definition", True) and nid not in nodes:
                f = file_of(sid) or ""
                nodes[nid] = {
                    "canonical_symbol_id": nid,
                    "name": s["name"],
                    "language": "c" if f.endswith((".c", ".h")) else "fortran",
                    "file": f.split("/")[-1],
                    "rel_file": f,
                    "snapshot_id": "kernel",
                    "binding_status": "EXACT",
                    "provenance": {"provider": "kernel",
                                   "provider_version": "fac-lib-coverage0",
                                   "provider_symbol": s["name"]},
                }
        span_ = o.get("source_span") or {}
        w = {"fact_id": f"kernel:call:{span_.get('file', '')}:{o.get('call_site_id', '')}",
             "provider": "kernel", "kind": "CALL", "semantic": "CALL",
             "source": {"file": span_.get("file"), "line": span_.get("start_line"),
                        "column": None, "end_line": None, "end_column": None},
             "expr": syms[tgt_sid]["name"]}
        key = (src, tgt)
        if key in edges:
            continue
        edge_count += 1
        edges[key] = {
            "kind": "CALL",
            "source": src,
            "target": tgt,
            "truth_layer": "T0",
            "truth_class": "OBSERVED",
            "execution_modality": "MUST",
            "target_resolution": "EXACT",
            "coverage": "COMPLETE",
            "witness_count": 1,
            "representative_witnesses": [w],
            "guard": None,
            "data": None,
            "source_span": {"file": span_.get("file"), "line": span_.get("start_line"),
                            "column": None, "end_line": None, "end_column": None},
        }
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def merge_topologies(frozen: dict, ext: dict) -> dict:
    """Merge two topology dicts (frozen wins on id conflicts; edges dedupe by
    (source, target)).  Neither input is mutated."""
    topo = {}
    for source in (frozen, ext):
        for n in source.get("nodes", []):
            topo.setdefault(n.get("canonical_symbol_id"), n)
    seen_edges = set()
    edge_list = []
    for source in (frozen, ext):
        for e in source.get("edges", []):
            k = (e.get("source"), e.get("target"))
            if k in seen_edges:
                continue
            seen_edges.add(k)
            edge_list.append(e)
    return {"nodes": list(topo.values()), "edges": edge_list}
