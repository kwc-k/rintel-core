"""Canonical source anchors for the RUNTIME-DATA0 probe sites (SC6/§13).

WHY THIS EXISTS: the RUNTIME-DATA0 probes recorded ``__LINE__`` from a
*patched worktree* copy of faclib/crm.c, and ``rintel.runtime_data.SRC_LINES``
keeps those numbers. Those numbers are NOT canonical-tree line numbers (the
inserted probe lines shift everything below them), so they must never be used
as source anchors in the canonical source.

Every entry below was derived by re-applying the deterministic probe patch
(``scripts/runtime_data0_patch.py``) to a copy of the canonical file and mapping
each probe call back to the canonical line it observes; ``expect`` is the text
that must appear on that canonical line, so a wrong table entry fails loudly
instead of silently pointing at unrelated code.

``scripts/source_span_col0_probe_map.py`` re-derives the mapping and asserts it
against this table (artifact: analysis_tournament/source_span_col0/
probe_anchor_map.json).
"""
from __future__ import annotations

from .model import (KIND_DATA_DECLARATION, KIND_OPERATION, line_only)
from .resolve import resolve_call, resolve_identifier, resolve_statement
from .text import SourceCache

# tag -> canonical anchor (file, line, token, span kind, must-contain text)
PROBE_ANCHORS: dict[str, dict] = {
    "bmatrix.alloc": {"file": "faclib/crm.c", "line": 1538,
                      "token": "bmatrix", "kind": KIND_DATA_DECLARATION,
                      "expect": "malloc"},
    "bmatrix.free": {"file": "faclib/crm.c", "line": 467,
                     "token": "bmatrix", "kind": KIND_OPERATION,
                     "expect": "free(bmatrix)"},
    "BlockMatrix.fill": {"file": "faclib/crm.c", "line": 3650,
                         "token": None, "kind": KIND_OPERATION,
                         "expect": "for (i = 0; i < n; i++)"},
    "BlockMatrix.diag": {"file": "faclib/crm.c", "line": 3658,
                         "token": "bmatrix", "kind": KIND_OPERATION,
                         "expect": "bmatrix[q] = - bmatrix[q];"},
    "FixNorm.out": {"file": "faclib/crm.c", "line": 3233,
                    "token": "bmatrix", "kind": KIND_OPERATION,
                    "expect": "bmatrix[p] = 0.0"},
    "BlockPopulation.in": {"file": "faclib/crm.c", "line": 3678,
                           "token": "bmatrix", "kind": KIND_OPERATION,
                           "expect": "a = bmatrix + n*n;"},
    "BlockPopulation.a_pre": {"file": "faclib/crm.c", "line": 3733,
                              "token": "DGESV", "kind": KIND_OPERATION,
                              "expect": "DGESV(m, nrhs"},
    "BlockPopulation.b_pre": {"file": "faclib/crm.c", "line": 3733,
                              "token": "DGESV", "kind": KIND_OPERATION,
                              "expect": "DGESV(m, nrhs"},
    "BlockPopulation.a_post": {"file": "faclib/crm.c", "line": 3733,
                               "token": "DGESV", "kind": KIND_OPERATION,
                               "expect": "DGESV(m, nrhs"},
    "BlockPopulation.b_post": {"file": "faclib/crm.c", "line": 3733,
                               "token": "DGESV", "kind": KIND_OPERATION,
                               "expect": "DGESV(m, nrhs"},
    "BlockPopulation.ipiv": {"file": "faclib/crm.c", "line": 3733,
                             "token": "DGESV", "kind": KIND_OPERATION,
                             "expect": "DGESV(m, nrhs"},
    "BlockPopulation.info": {"file": "faclib/crm.c", "line": 3733,
                             "token": "DGESV", "kind": KIND_OPERATION,
                             "expect": "DGESV(m, nrhs"},
    "BlockPopulation.nb": {"file": "faclib/crm.c", "line": 3750,
                           "token": "nb", "kind": KIND_OPERATION,
                           "expect": "blk->nb = b[p++]"},
    "BlockMatrix.dim": {"file": "faclib/crm.c", "line": 3250,
                        "token": None, "kind": KIND_OPERATION,
                        "expect": "for (i = 0; i < 2*n*(n+2); i++)"},
    "FixNorm.dim": {"file": "faclib/crm.c", "line": 3151,
                    "token": None, "kind": KIND_OPERATION,
                    "expect": "x = bmatrix + n*n;"},
    "BlockPopulation.dim": {"file": "faclib/crm.c", "line": 3678,
                            "token": None, "kind": KIND_OPERATION,
                            "expect": "a = bmatrix + n*n;"},
}

_cache: SourceCache | None = None
_recorded_to_tag: dict[str, str] | None = None

# §8: the DGESV callsite is a macro invocation whose recorded line (SRC_LINES
# "dgesv.call" = 3734) is one off the canonical line; the span is resolved by
# scanning the file for real ``DGESV(`` call expressions instead of trusting it.
DGESV_FILE = "faclib/crm.c"
DGESV_RECORDED_LINE = 3734


def dgesv_callsite_span(root, file: str = DGESV_FILE,
                        recorded_line: int = DGESV_RECORDED_LINE) -> dict:
    """Canonical OPERATION span of the solver DGESV(...) call expression."""
    global _cache
    if _cache is None or str(_cache.root) != str(root):
        _cache = SourceCache(root, lang_of=lambda rel: "c")
    src = _cache.get(file)
    if src is None:
        return line_only(file, recorded_line, KIND_OPERATION,
                         {"reason": "canonical_source_missing"})
    best = None
    for m in __import__("re").finditer(r"DGESV\s*\(", src.masked):
        line, _col = src.pos(m.start())
        span = resolve_call(src, line, "DGESV", KIND_OPERATION)
        if not span:
            continue
        span["evidence"] = dict(span.get("evidence") or {}, method="macro_callsite_scan",
                                recorded_line=recorded_line)
        if line == recorded_line:
            return span
        if best is None or abs(line - recorded_line) < abs(best[0] - recorded_line):
            best = (line, span)
    if best:
        return best[1]
    return line_only(file, recorded_line, KIND_OPERATION,
                     {"reason": "dgesv_call_not_found"})


def _recorded_map() -> dict[str, str]:
    """Frozen probe_site string ("faclib/crm.c:3357") -> probe tag.

    The frozen RUNTIME-DATA0/PERF-TOPO0 artifacts carry SRC_LINES values, which
    live in PATCHED line space. This reverse map lets a consumer turn such a
    string into a verified canonical span instead of trusting the number.
    """
    global _recorded_to_tag
    if _recorded_to_tag is None:
        try:
            from ..runtime_data import SRC_LINES
        except Exception:                                  # pragma: no cover
            SRC_LINES = {}
        m: dict[str, str] = {}
        for tag, rec in SRC_LINES.items():
            if tag in PROBE_ANCHORS and rec:
                m.setdefault(rec, tag)
        _recorded_to_tag = m
    return _recorded_to_tag


def recorded_site_span(recorded_site: str, root) -> dict:
    """Canonical span for a frozen (patched-space) probe_site string."""
    tag = _recorded_map().get(recorded_site)
    if not tag:
        return line_only(None, None, KIND_OPERATION,
                         {"reason": "unknown_recorded_probe_site",
                          "recorded_site": recorded_site})
    span = anchor_span(tag, root)
    ev = dict(span.get("evidence") or {})
    ev.update({"recorded_probe_site": recorded_site,
               "recorded_probe_site_space": "patched_instrumented_worktree",
               "canonical_anchor_tag": tag})
    span["evidence"] = ev
    return span


def anchor_span(tag: str, root) -> dict:
    """Canonical SourceSpan for a RUNTIME-DATA0 probe tag (never guessed)."""
    global _cache
    e = PROBE_ANCHORS.get(tag)
    if not e:
        return line_only(None, None, KIND_OPERATION,
                         {"reason": "no_canonical_anchor_for_tag", "tag": tag})
    if _cache is None or str(_cache.root) != str(root):
        _cache = SourceCache(root, lang_of=lambda rel: "c")
    src = _cache.get(e["file"])
    if src is None:
        return line_only(e["file"], e["line"], e["kind"],
                         {"reason": "canonical_source_missing", "tag": tag})
    line_text = src.line_text(e["line"])
    if e["expect"] not in line_text:
        return line_only(e["file"], e["line"], e["kind"],
                         {"reason": "anchor_text_mismatch", "tag": tag,
                          "expected": e["expect"], "found": line_text.strip()})
    if e["token"]:
        span = resolve_call(src, e["line"], e["token"], e["kind"])
        if span is None:
            span = resolve_identifier(src, e["line"], e["token"], e["kind"])
    else:
        span = resolve_statement(src, e["line"], e["kind"])
    ev = dict(span.get("evidence") or {})
    ev.update({"probe_tag": tag, "anchor_space": "canonical",
               "derived_from": "runtime_data0_patch.py re-applied to canonical "
                               "crm.c (scripts/source_span_col0_probe_map.py)"})
    span["evidence"] = ev
    return span
