"""SEMANTIC-SUBSTRATE1 kernel runner.

Extracts the kernel object graph (Context/Symbol/Reference/Operation/Value/
Location/ArgumentBinding/TypeTerm/Resource) from the real FAC kernel parse
set (5 Fortran + 3 C files) and writes:
    analysis_tournament/semantic_substrate1/fac_kernel.json
    analysis_tournament/semantic_substrate1/fac_kernel_summary.json  (§28 counts)
    analysis_tournament/semantic_substrate1/support_refs.json        (KS7)

Faclib never enters the lane (§26): faclib/config.c is parsed as the config
module ONLY (globals/typedefs/functions in that file), never the math library.
"""
from __future__ import annotations

import json
from pathlib import Path

from rintel.data_interface import FORT_SOURCE_RELS
from rintel.kernel.extract import Extractor
from rintel.kernel.extract_c import scan_c_file, walk_c_file
from rintel.kernel.extract_fortran import scan_fortran_file, walk_fortran_file
from rintel.source_span import SpanEnricher

# NOTE: base kernel parse set = FORT_SOURCE_RELS + these C files
C_KERNEL_RELS = ["faclib/config.c", "sfac/sfac.c", "sfac/scrm.c"]
KERNEL_FILES = list(FORT_SOURCE_RELS) + list(C_KERNEL_RELS)
KERNEL_NAMES = ("fac_kernel.json", "fac_kernel_summary.json", "support_refs.json",
                "fac_kernel_index.json")

# FAC-LIBRARY-COVERAGE0: faclib/*.c + faclib/*.h enter the universe, with
# generated/third-party artifacts excluded (each exclusion is a documented
# coverage gap, never silently skipped):
FACLIB_C_EXCLUDE = set()                                   # all 30 .c files covered
FACLIB_H_EXCLUDE = {"cfortran.h",                          # third-party include (cfortran)
                    "grdcfg.h", "grdcfg1.h",              # generated data tables
                    "sysdef.h",                            # configure-generated
                    }
FACLIB_SKIP = {".o", ".f90", ".mod", ".doc", ".py"}
FORT_DIRS = ("blas/", "lapack/")
# f2c generated binder file (faclib/f2c.f90) and generated .mod are OUT of
# the universe; the f2c.h macro bindings are still parsed from f2c.h.

KERNEL_OUT = Path("/Users/wu/Documents/dh/a3/fac/repo-intel/analysis_tournament") \
    / "semantic_substrate1"
FAC_ROOT_DEFAULT = Path("/Users/wu/Documents/dh/a3/fac/fac")
# canonical frozen inputs for support refs (never derived from `out`)
FIN_KER = Path("/Users/wu/Documents/dh/a3/fac/repo-intel/analysis_tournament") / "flow_infer"
SEM_KER = Path("/Users/wu/Documents/dh/a3/fac/repo-intel/analysis_tournament") / "flow_semantic0"
LVS_KER = Path("/Users/wu/Documents/dh/a3/fac/repo-intel/analysis_tournament") / "lvs" / "fac_results.json"

BINDING_HDRS = ("faclib/cf77.h", "faclib/f2c.h")


def faclib_rels(root: Path) -> list[str]:
    """faclib/*.c + faclib/*.h (minus excluded generated/third-party)."""
    out = []
    d = root / "faclib"
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if p.suffix in FACLIB_SKIP:
            continue
        if p.name in FACLIB_H_EXCLUDE:
            continue
        if p.suffix in (".c", ".h"):
            out.append(f"faclib/{p.name}")
    return out


def harvest_bindings(root: Path) -> list[dict]:
    """Macro linkage evidence from the parsed header texts (cf77.h/f2c.h)."""
    ex = Extractor("bindings")
    for rel in BINDING_HDRS:
        p = root / rel
        if p.exists():
            _harvest_bindings_into(ex, p.read_text(errors="replace"), rel)
    return ex.macro_bindings


def _harvest_bindings_into(ex, text: str, rel: str) -> None:
    from rintel.kernel.extract_c import _CFORTRAN_BIND_RE, _F2C_BIND_RE, _line_of
    for m in _CFORTRAN_BIND_RE.finditer(text):
        ex.register_macro_binding(m.group(1), m.group(2), None, "cfortran",
                                  rel, _line_of(text, m.start()), m.group(0))
    for m in _F2C_BIND_RE.finditer(text):
        ex.register_macro_binding(m.group(1), m.group(1), m.group(2), "f2c",
                                  rel, _line_of(text, m.start()), m.group(0))


def fortran_extra_rels(root: Path, bindings: list[dict]) -> list[str]:
    """BLAS/LAPACK Fortran files actually bound by the cfortran/f2c macros
    (evidence-driven external-callable universe; only files that exist)."""
    out = []
    for b in bindings:
        fn = (b.get("fortran") or "").lower()
        if not fn:
            continue
        for base in ("lapack", "blas"):
            f = f"{base}/{fn}.f"
            if (root / f).exists() and f not in out:
                out.append(f)
    return sorted(out)


def kernel_files(root: Path) -> list[str]:
    """The full covered universe (deterministic order)."""
    bindings = harvest_bindings(root)
    return sorted(set(KERNEL_FILES) | set(faclib_rels(root))
                  | set(fortran_extra_rels(root, bindings)))


def is_fortran_rel(rel: str) -> bool:
    return rel.endswith(".f") or rel in FORT_SOURCE_RELS


def objects_hash_for(rel: str, root: Path) -> dict:
    """Per-file kernel object evidence hash (K11 / kernel-delta).

    Comment-only edits are stripped before scanning, so the object signature
    is stable under comment changes; any semantic edit shifts it.  Object ids
    (counters) are EXCLUDED — only kind/name/line/storage evidence is hashed.
    """
    import hashlib
    ex = Extractor("kern-delta")
    if is_fortran_rel(rel):
        plan = scan_fortran_file(ex, root, rel)
        walk_fortran_file(ex, root, rel, plan)
    else:
        plan = scan_c_file(ex, root, rel)
        walk_c_file(ex, root, rel, plan)
    sig = []

    def _sn(span_):
        return None      # line numbers are evidence anchors, NOT semantic
                         # content: comment insertion at the top shifts every
                         # line yet changes nothing (K11/K12)

    for c in ex.contexts:
        sig.append(("ctx", c.kind, _sn(c.source_span)))
    for s in ex.symbols:
        sig.append(("sym", s.kind, s.name, s.role, s.storage_class, _sn(s.source_span)))
    for r in ex.references:
        sig.append(("ref", r.text, r.resolution, _sn(r.source_span)))
    for o in ex.operations:
        sig.append(("op", o.kind, o.state_use, _sn(o.source_span)))
    for v in ex.values:
        sig.append(("val", v.kind, _sn(v.source_span)))
    for t in ex.type_terms:
        sig.append(("type", t.name, _sn(t.source_span)))
    h = hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest()
    return {"objects_hash": h,
            "counts": {"symbols": len(ex.symbols), "references": len(ex.references),
                       "operations": len(ex.operations)}}


def _light_span(sp: dict) -> dict:
    """Compact canonical span for the lookup index (§17: same contract)."""
    return {"file": sp.get("file"), "start_line": sp.get("start_line"),
            "start_column": sp.get("start_column"),
            "end_line": sp.get("end_line"), "end_column": sp.get("end_column"),
            "span_kind": sp.get("span_kind"), "precision": sp.get("precision"),
            "text": sp.get("text")}


def run(fac_root: Path | None = None, out: Path | None = None,
        revision: str = "frozen-0") -> dict:
    root = Path(fac_root or FAC_ROOT_DEFAULT)
    out = Path(out or KERNEL_OUT)
    out.mkdir(parents=True, exist_ok=True)

    ex = Extractor(revision)
    files = kernel_files(root)
    # Phase A: register ALL file-scope symbols first (cross-file resolution)
    plans: dict[str, list] = {}
    for rel in files:
        if (root / rel).exists():
            if is_fortran_rel(rel):
                plans[rel] = scan_fortran_file(ex, root, rel)
            else:
                plans[rel] = scan_c_file(ex, root, rel)
    # DECLARES/DEFINES/SAME_CALLABLE unification (LC4) BEFORE any resolution
    ex.unify_callables()
    # Phase B: walk bodies (references/operations) — order-independent
    for rel in files:
        if rel in plans:
            if is_fortran_rel(rel):
                walk_fortran_file(ex, root, rel, plans[rel])
            else:
                walk_c_file(ex, root, rel, plans[rel])
    # Phase C (SOURCE-SPAN-COL0 §17): every object gets the ONE canonical
    # SourceSpan — identifier token / declaration header / call expression —
    # resolved from the ORIGINAL source. Objects whose columns cannot be
    # recovered stay LINE_ONLY (never a fabricated column).
    span_stats = SpanEnricher(root, lang_of=lambda rel: "fortran"
                              if is_fortran_rel(rel) else "c").enrich_kernel(ex)

    bundle = {
        "revision": revision,
        "span_stats": span_stats,
        "contexts": [c.to_dict() for c in ex.contexts],
        "symbols": [s.to_dict() for s in ex.symbols],
        "references": [r.to_dict() for r in ex.references],
        "operations": [o.to_dict() for o in ex.operations],
        "values": [v.to_dict() for v in ex.values],
        "locations": [l.to_dict() for l in ex.locations],
        "bindings": [b.to_dict() for b in ex.bindings],
        "type_terms": [t.to_dict() for t in ex.type_terms],
        "resources": [r.to_dict() for r in RESOURCES],
        "counts": {
            "contexts": len(ex.contexts),
            "symbols": len(ex.symbols),
            "references": len(ex.references),
            "operations": len(ex.operations),
            "values": len(ex.values),
            "locations": len(ex.locations),
            "bindings": len(ex.bindings),
        },
    }
    json.dump(bundle, open(out / "fac_kernel.json", "w"), separators=(",", ":"))
    # look-up index (coverage scale: the 200MB+ bundle must not be re-scanned
    # for each symbol lookup — get_symbol/service use this index; the bundle
    # remains the full truth)
    index = {
        "revision": revision,
        "symbols_by_name": {k: v for k, v in ex._by_name.items()},
        "refs_by_text": {},
        "refs_by_id": {},
        "symbols_light": {},
        "calls_light": {},
    }
    for s in ex.symbols:
        sp = s.source_span or {}
        index["symbols_light"].setdefault(s.name, []).append({
            "id": s.symbol_id, "file": sp.get("file"), "kind": s.kind,
            "storage": s.storage_class, "role": s.role,
            "line": sp.get("start_line"), "definition": s.is_definition,
            "lang": s.language,
            # SOURCE-SPAN-COL0 §17: the canonical span travels with the lookup
            # index; `line` above is the derived convenience field.
            "span": _light_span(sp)})
    for o in ex.operations:
        if o.kind != "CALL":
            continue
        act = ex.symbol_of(o.actor)
        cal = ex.symbol_of(o.callee_entry) if o.callee_entry else None
        if not act:
            continue
        entry = {
            "callee": cal.name if cal else None,
            "callee_file": (cal.source_span or {}).get("file") if cal else None,
            "resolved": cal is not None,
            "line": (o.source_span or {}).get("start_line"),
            "file": (o.source_span or {}).get("file"),
            "span": _light_span(o.source_span or {})}
        lst = index["calls_light"].setdefault(act.name, [])
        if not any(c.get("callee") == entry["callee"] and c.get("line") == entry["line"]
                   for c in lst):
            lst.append(entry)
    for i, r in enumerate(ex.references):
        index["refs_by_text"].setdefault(r.text, []).append(r.reference_id)
        index["refs_by_id"][r.reference_id] = i
    json.dump(index, open(out / "fac_kernel_index.json", "w"),
              separators=(",", ":"))

    summary = _summary(ex, root, out, span_stats=span_stats)
    json.dump(summary, open(out / "fac_kernel_summary.json", "w"), indent=1)
    refs = support_refs(ex, root, out)
    json.dump(refs, open(out / "support_refs.json", "w"), indent=1)
    return summary


# ------------------------------------------------------------------ summary
def _summary(ex: Extractor, root: Path, out: Path,
             span_stats: dict | None = None) -> dict:
    symbols = ex.symbols
    datas = [s for s in symbols if s.kind == "DATA"]
    callables = [s for s in symbols if s.kind == "CALLABLE"]
    refs = ex.references
    ops = ex.operations
    bindings = ex.bindings

    # shared module state = mutable file-scope DATA (K9); compile-time
    # NAMED_CONSTANTs (#define / PARAMETER) are K10 and NOT state.
    shared = [s for s in datas
              if s.storage_class in ("GLOBAL", "STATIC") and s.role == "GLOBAL"]
    named_constants = [s for s in datas if s.role == "NAMED_CONSTANT"]
    # same-name shadowing: same name declared in nested contexts (strict ancestor)
    ctx_by_id = {c.context_id: c for c in ex.contexts}

    def is_ancestor(outer: str, inner: str) -> bool:
        seen = 0
        cur = ctx_by_id.get(inner)
        while cur and cur.parent_context_id:
            if cur.parent_context_id == outer:
                return True
            cur = ctx_by_id.get(cur.parent_context_id)
        return False

    shadow_pairs: list[dict] = []
    names: dict[str, list[Symbol]] = {}
    for s in datas:
        names.setdefault(s.name, []).append(s)
    for sname, group in names.items():
        for i in range(len(group)):
            for j in range(len(group)):
                if i == j:
                    continue
                a, b = group[i], group[j]
                if (a.declaration_context != b.declaration_context
                        and (is_ancestor(a.declaration_context, b.declaration_context)
                             or is_ancestor(b.declaration_context, a.declaration_context))):
                    shadow_pairs.append({
                        "name": sname,
                        "outer_symbol": a.symbol_id if is_ancestor(a.declaration_context, b.declaration_context) else b.symbol_id,
                        "inner_symbol": b.symbol_id if is_ancestor(a.declaration_context, b.declaration_context) else a.symbol_id,
                    })
    # dedupe pairs
    seen_pairs = set()
    unique = []
    for p in shadow_pairs:
        key = tuple(sorted([p["outer_symbol"], p["inner_symbol"]]))
        if key not in seen_pairs:
            seen_pairs.add(key)
            unique.append(p)

    unknowns = [r for r in refs if r.resolution == "UNKNOWN"]
    exacts = [r for r in refs if r.resolution == "EXACT"]
    candidates = [r for r in refs if r.resolution == "CANDIDATE_SET"]

    files = {c.source_span["file"] for c in ex.contexts if c.source_span}
    unified_calls = [s for s in callables
                     if s.canonical_callable_id and s.canonical_callable_id != s.symbol_id]
    decl_only = [s for s in callables if not s.is_definition and s.is_declaration]
    universe = _universe_of(root, files)
    return {
        "source_span": {
            "contract": "rintel.source_span.SourceSpan",
            "column_base": 1,
            "column_unit": "character index in the ORIGINAL UTF-8 text "
                           "(not bytes, not tab-expanded, not UTF-16)",
            "end_column_semantics": "inclusive",
            "precisions": ["EXACT", "LINE_ONLY", "PARTIAL", "UNKNOWN"],
            "stats": span_stats or {},
        },
        "revision": ex.revision,
        "files": sorted(files),
        "universe": universe,
        "macro_bindings_count": len(ex.macro_bindings),
        "unified_callable_links": len(unified_calls),
        "declared_only_functions": len(decl_only),
        "counts": {
            "functions": len([s for s in callables if s.is_definition]),
            "functions_with_decls": len(callables),
            "data_symbols": len(datas),
            "type_symbols": len([s for s in symbols if s.kind == "TYPE"]),
            "references": len(refs),
            "operations": len(ops),
            "argument_bindings": len(bindings),
            "shared_module_state": len(shared),
            "same_name_shadowing": len(unique),
            "unknown_targets": len(unknowns),
            "named_constants": len(named_constants),
            "type_terms": len(ex.type_terms),
        },
        "gate_minimums": {
            "functions_ge_20": len(callables) >= 20,
            "data_symbols_ge_30": len(datas) >= 30,
            "references_ge_30": len(refs) >= 30,
            "operations_ge_30": len(ops) >= 30,
            "bindings_ge_10": len(bindings) >= 10,
            "shared_module_state_ge_10": len(shared) >= 10,
            "same_name_shadowing_ge_5": len(unique) >= 5,
            "unknown_targets_ge_5": len(unknowns) >= 5,
        },
        "by_kind": {
            k: len([o for o in ops if o.kind == k])
            for k in ("READ", "WRITE", "CALL", "RETURN", "COMPARE", "BRANCH",
                      "ALLOCATE", "FREE", "INDEX")
        },
        "resolution": {"EXACT": len(exacts), "CANDIDATE_SET": len(candidates),
                       "UNKNOWN": len(unknowns)},
        "shared_module_state_symbols": [s.name for s in shared],
        "shadow_pairs": unique,
        "unknown_target_samples": [r.to_dict() for r in unknowns[:12]],
    }


def _universe_of(root: Path, files: set) -> dict:
    """Coverage map (§4/§22): file categories with honest exclusions."""
    rels = sorted(files)
    cl = [r for r in rels if r.startswith("sfac/")]
    fl = [r for r in rels if r.startswith("faclib/") and r.endswith(".c")]
    fh = [r for r in rels if r.startswith("faclib/") and r.endswith(".h")]
    fo = [r for r in rels if r.startswith(("blas/", "lapack/"))]
    return {
        "command_layer": cl,
        "faclib_c": fl,
        "faclib_headers": fh,
        "fortran_external": fo,
        "exclusions": {
            "generated": ["faclib/grdcfg.h", "faclib/grdcfg1.h",
                          "faclib/f2c.f90", "faclib/f2c.mod", "faclib/sysdef.h"],
            "third_party_not_parsed": ["faclib/cfortran.h", "faclib/cfortran.doc"],
            "third_party_parsed": ["faclib/fftsg.c (Ooura FFT — covered as real code)"],
            "build_artifacts": "faclib/*.o",
        },
    }


# ------------------------------------------------------------------- KS7
def support_refs(ex: Extractor, root: Path, out: Path) -> dict:
    """Projection objects -> kernel symbol ids (single-truth links)."""
    import glob as _glob

    name_to_callable = {}
    for s in ex.symbols:
        if s.kind == "CALLABLE" and s.is_definition:
            name_to_callable.setdefault(s.name, ex.canonical_of(s.symbol_id))
    result: dict = {"ports": [], "functions": [], "flow_regions": [],
                    "semantic_stages": [], "lvs": []}

    # 1) DataPort -> kernel parameter symbol (same parse evidence chain)
    for s in ex.symbols:
        if s.kind == "DATA" and s.role == "PARAMETER":
            fn_ctx_id = s.declaration_context
            fn_ctx = next((c for c in ex.contexts if c.context_id == fn_ctx_id), None)
            callable_id = None
            for cid, ctxid in ex.fn_ctx.items():
                if ctxid == fn_ctx_id:
                    callable_id = cid
                    break
            if callable_id and fn_ctx and fn_ctx.source_span \
                    and fn_ctx.source_span.get("file"):
                fname = fn_ctx.source_span["file"]
                result["ports"].append({
                    "kernel_symbol_id": ex.canonical_of(s.symbol_id),
                    "port_id": f"port:{callable_id.split('::')[-1]}:{s.name}",
                    "fn_file": fname,
                })

    # 2) Function canonical id -> CALLABLE symbol (definition preferred;
    #    header prototypes unify onto the same canonical via support)
    seen_fn = set()
    for s in ex.symbols:
        if s.kind == "CALLABLE" and s.is_definition:
            result["functions"].append({
                "canonical_id": f"function:{s.name}",
                "kernel_symbol_id": ex.canonical_of(s.symbol_id),
            })
            seen_fn.add(s.name)
    for s in ex.symbols:
        if s.kind == "CALLABLE" and s.is_declaration and s.name not in seen_fn:
            result["functions"].append({
                "canonical_id": f"function:{s.name}",
                "kernel_symbol_id": ex.canonical_of(s.symbol_id),
                "declared_only": True,
            })

    # 3) flow regions: callees from fac_regions.json (frozen flow_infer input)
    try:
        fin = FIN_KER / "fac_regions.json"
        regions = json.load(open(fin)).get("regions", [])
        for reg in regions:
            for callee in reg.get("callees", []):
                sid = name_to_callable.get(callee)
                if sid:
                    result["flow_regions"].append({
                        "region_id": reg.get("region_id"),
                        "callee": callee,
                        "kernel_symbol_id": sid,
                    })
    except (FileNotFoundError, AttributeError):
        pass

    # 4) semantic stages: members from flow_semantic0 artifacts
    try:
        sem = json.load(open(SEM_KER / "fac_semantic_stages.json"))
        for st in sem:
            members = st.get("members", {}) or {}
            for cid in members.get("canonical_symbol_ids", []):
                name = None
                if isinstance(cid, str) and cid.startswith("function:"):
                    name = cid.split(":", 1)[1]
                sid = name_to_callable.get(name or "")
                if sid:
                    result["semantic_stages"].append({
                        "stage_id": st.get("semantic_stage_id") or st.get("id"),
                        "member": cid,
                        "kernel_symbol_id": sid,
                    })
    except (FileNotFoundError, AttributeError):
        pass

    # 5) LVS block code objects (`node:FUNCTION:file::Name`) -> CALLABLE
    try:
        lvs = json.load(open(LVS_KER))
        for bd in lvs.get("block_diffs", []):
            co = (bd.get("code_object") or "").split("::")
            if len(co) >= 2 and co[0].startswith("node:FUNCTION:"):
                name = co[-1]
                sid = name_to_callable.get(name)
                if sid:
                    result["lvs"].append({"code_object": bd.get("code_object"),
                                          "kernel_symbol_id": sid,
                                          "status": bd.get("status"),
                                          "block_id": bd.get("block_id")})
    except (FileNotFoundError, KeyError):
        pass
    return result


from rintel.kernel.model import RESOURCES  # noqa: E402  (after run defs)
