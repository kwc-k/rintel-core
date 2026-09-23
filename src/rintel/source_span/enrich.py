"""Kernel enrichment: every kernel object gets ONE canonical SourceSpan (§17).

Objects are grouped by (file, line, identifier, semantic class) and matched to
identifier occurrences in the ORIGINAL source in creation order. A CALLABLE
symbol's identifier is the occurrence followed by ``(``; a DATA symbol's is one
that is not — that semantic narrowing is what keeps shadowing declarations
(``int foo(int foo)``) correct instead of positional.
"""
from __future__ import annotations

from collections import Counter

from .model import (KIND_CALLSITE, KIND_DATA_DECLARATION, KIND_DECLARATION,
                    KIND_DEFINITION, KIND_OPERATION, KIND_REFERENCE,
                    line_only, make_span)
from .resolve import (resolve_call, resolve_declaration, resolve_identifier,
                      resolve_statement)
from .text import SourceCache

_CALLABLE = "CALLABLE"


def _pref_for_symbol(sym) -> str | None:
    if sym.kind == _CALLABLE:
        return "call_like"
    if sym.kind in ("DATA", "TYPE", "VALUE", "LOCATION"):
        return "non_call_like"
    return None


def _ref_prefer(ref, callee: set, sym_of: dict) -> str | None:
    """A callee reference is call-like; a reference resolved to a non-callable
    symbol (e.g. a parameter shadowing a function name) is not (§25)."""
    if ref.reference_id in callee:
        return "call_like"
    if ref.symbol_id and ref.symbol_id in sym_of:
        return "call_like" if sym_of[ref.symbol_id].kind == _CALLABLE else "non_call_like"
    return None


def _sym_kind(sym) -> str:
    if sym.kind == "DATA":
        return KIND_DATA_DECLARATION
    if sym.kind == _CALLABLE:
        return KIND_DEFINITION if getattr(sym, "is_definition", True) else KIND_DECLARATION
    return KIND_DEFINITION


def _argument_ranges(src, span: dict) -> list[tuple[int, int, int, int]]:
    """(start_line, start_col, end_line, end_col) per top-level argument."""
    i0 = src.index_of(span["start_line"], span["start_column"])
    i1 = src.index_of(span.get("end_line") or span["start_line"],
                      span.get("end_column") or span["start_column"])
    if i0 is None or i1 is None:
        return []
    text = src.masked
    open_i = text.find("(", i0, i1 + 1)
    if open_i < 0:
        return []
    close_i = src.match_paren(open_i)
    if close_i is None or close_i > i1:
        return []
    out: list[tuple[int, int, int, int]] = []
    depth = 0
    start = open_i + 1
    for i in range(open_i + 1, close_i):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            out.append(_trim(text, src, start, i))
            start = i + 1
    if close_i > start:
        out.append(_trim(text, src, start, close_i))
    return [r for r in out if r is not None]


def _trim(text: str, src, a: int, b: int):
    while a < b and text[a] in " \t\n\r":
        a += 1
    while b > a and text[b - 1] in " \t\n\r":
        b -= 1
    if b <= a:
        return None
    sl, sc = src.pos(a)
    el, ec = src.pos(b - 1)
    return (sl, sc, el, ec)


def _sp_file_line(sp: dict | None) -> tuple[str | None, int | None]:
    if not sp:
        return None, None
    return sp.get("file"), sp.get("start_line")


class SpanEnricher:
    def __init__(self, root, lang_of=None):
        self.cache = SourceCache(root, lang_of)
        # per-file declaration token ranges (shadowing filter, §25)
        self._sym_ranges: dict[str, list[tuple[int, int]]] = {}

    def _record_symbol_ranges(self, ex) -> None:
        for sym in ex.symbols:
            sp = sym.source_span or {}
            if sp.get("precision") not in ("EXACT", "PARTIAL"):
                continue
            src = self.cache.get(sp.get("file"))
            if src is None or not sp.get("start_column"):
                continue
            i0 = src.index_of(sp["start_line"], sp["start_column"])
            i1 = src.index_of(sp["end_line"] or sp["start_line"],
                              sp["end_column"] or sp["start_column"])
            if i0 is not None and i1 is not None:
                self._sym_ranges.setdefault(sp["file"], []).append((i0, i1 + 1))

    def _exclude_for(self, f: str | None, keep: tuple[int, int] | None = None):
        rng = self._sym_ranges.get(f or "")
        if not rng:
            return None
        return rng

    # ------------------------------------------------------------- symbols
    def enrich_symbols(self, ex) -> Counter:
        stats = Counter()
        groups: dict[tuple, list] = {}
        for sym in ex.symbols:
            f, ln = _sp_file_line(sym.source_span)
            pref = _pref_for_symbol(sym)
            groups.setdefault((f, ln, sym.name, pref), []).append(sym)
        for (f, ln, name, pref), syms in groups.items():
            src = self.cache.get(f)
            for k, sym in enumerate(syms):
                kind = _sym_kind(sym)
                if not src or not ln:
                    sym.source_span = line_only(f, ln, kind,
                                                {"reason": "no_source_file"})
                    stats["symbol:line_only"] += 1
                    continue
                sym.source_span = resolve_identifier(
                    src, ln, name, kind, occurrence=k, group_size=len(syms),
                    prefer=pref, forward_scan=True)
                stats[f"symbol:{sym.source_span['precision']}"] += 1
                if sym.kind in (_CALLABLE, "DATA", "TYPE"):
                    sym.declaration_span = resolve_declaration(
                        src, ln, name, kind, occurrence=k,
                        group_size=len(syms), prefer=pref, forward_scan=True)
                    stats[f"symbol_decl:{sym.declaration_span['precision']}"] += 1
        return stats

    # ---------------------------------------------------------- references
    def enrich_references(self, ex) -> Counter:
        stats = Counter()
        callee = {o.target_reference for o in ex.operations
                  if o.kind == "CALL" and o.target_reference}
        sym_of = {s.symbol_id: s for s in ex.symbols}
        groups: dict[tuple, list] = {}
        for ref in ex.references:
            f, ln = _sp_file_line(ref.source_span)
            pref = _ref_prefer(ref, callee, sym_of)
            groups.setdefault((f, ln, ref.text, pref), []).append(ref)
        for (f, ln, name, pref), refs in groups.items():
            src = self.cache.get(f)
            for k, ref in enumerate(refs):
                kind = KIND_CALLSITE if ref.reference_id in callee else KIND_REFERENCE
                if not src or not ln:
                    ref.source_span = line_only(f, ln, kind,
                                                {"reason": "no_source_file"})
                    stats["reference:line_only"] += 1
                    continue
                ref.source_span = resolve_identifier(
                    src, ln, name, kind, occurrence=k, group_size=len(refs),
                    prefer=pref, exclude=self._exclude_for(f))
                stats[f"reference:{ref.source_span['precision']}"] += 1
        return stats

    # ---------------------------------------------------------- operations
    def enrich_operations(self, ex) -> Counter:
        stats = Counter()
        by_id = {r.reference_id: r for r in ex.references}
        for op in ex.operations:
            f, ln = _sp_file_line(op.source_span)
            src = self.cache.get(f)
            ref = by_id.get(op.target_reference) if op.target_reference else None
            if op.kind == "CALL" and ref is not None:
                rspan = ref.source_span or {}
                ev = rspan.get("evidence") or {}
                span = None
                if src and ln:
                    span = resolve_call(
                        src, ln, ref.text, KIND_OPERATION,
                        occurrence=ev.get("occurrence"),
                        group_size=ev.get("group_size"),
                        prefer=ev.get("prefer") or "call_like",
                        exclude=self._exclude_for(f))
                if span is not None:
                    op.source_span = span
                    stats[f"operation:{span['precision']}"] += 1
                    cal = ex.symbol_of(op.callee_entry) if op.callee_entry else None
                    if cal is not None and cal.source_span:
                        op.related_spans = [cal.source_span]
                    continue
                op.source_span = line_only(f, ln, KIND_OPERATION,
                                           {"reason": "call_expression_not_found",
                                            "name": ref.text})
                stats["operation:line_only"] += 1
                continue
            if src and ln:
                op.source_span = resolve_statement(src, ln, KIND_OPERATION)
                stats[f"operation:{op.source_span['precision']}"] += 1
            else:
                op.source_span = line_only(f, ln, KIND_OPERATION,
                                           {"reason": "no_source_file"})
                stats["operation:line_only"] += 1
        return stats

    # ------------------------------------------------------- argument ranges
    def enrich_bindings(self, ex) -> Counter:
        """§17: ArgumentBinding -> the ACTUAL-argument range of its CALL.

        The call expression span is split on top-level commas (paren/brace/
        bracket aware, comments/strings already masked); a binding whose
        position has no matching argument keeps the call span and is marked
        PARTIAL with the reason named.
        """
        stats = Counter()
        by_op = {o.operation_id: o for o in ex.operations}
        for b in ex.bindings:
            op = by_op.get(b.call_operation_id)
            span = dict((op.source_span if op else None) or {})
            if (not span or span.get("precision") not in ("EXACT", "PARTIAL")
                    or not isinstance(span.get("start_column"), int)):
                b.source_span = line_only(span.get("file"), span.get("start_line"),
                                          KIND_OPERATION,
                                          {"reason": "call_span_unavailable",
                                           "method": "argument_split"})
                stats["binding:line_only"] += 1
                continue
            src = self.cache.get(span.get("file"))
            args = _argument_ranges(src, span) if src else []
            # positions are 1-based (extract_c: position=j + 1)
            idx = (b.position - 1) if isinstance(b.position, int) else None
            if idx is not None and 0 <= idx < len(args):
                a = args[idx]
                covered = src.slice_text(a[0], a[1], a[2], a[3])
                ev = {"method": "call_argument_split", "source": "original",
                      "position": b.position, "arguments": len(args),
                      "call_span": span.get("text")}
                prec = "EXACT"
                if b.actual and covered.strip() != b.actual.strip():
                    prec = "PARTIAL"
                    ev["note"] = (f"covered {covered.strip()!r} != recorded "
                                  f"actual {b.actual.strip()!r}")
                b.source_span = make_span(span.get("file"), a[0], a[1], a[2], a[3],
                                          KIND_OPERATION, prec, ev)
                stats[f"binding:{prec}"] += 1
            else:
                b.source_span = dict(span)
                b.source_span["precision"] = "PARTIAL"
                ev = dict(span.get("evidence") or {})
                ev.update({"method": "argument_split_position_unmatched",
                           "position": b.position, "arguments": len(args)})
                b.source_span["evidence"] = ev
                stats["binding:call_span_partial"] += 1
        return stats

    # -------------------------------------------------------------- others
    def enrich_misc(self, ex) -> Counter:
        stats = Counter()
        for obj, name, kind, pref in (
                [(l, l.name, KIND_DECLARATION, "non_call_like") for l in ex.locations
                 if getattr(l, "name", None)]
                + [(t, t.name, KIND_DECLARATION, None) for t in ex.type_terms]):
            f, ln = _sp_file_line(obj.source_span)
            src = self.cache.get(f)
            if not src or not ln or not name:
                obj.source_span = line_only(f, ln, kind, {"reason": "no_source_file"})
                stats[f"misc:{kind}:line_only"] += 1
                continue
            obj.source_span = resolve_identifier(src, ln, name, kind,
                                                 prefer=pref, forward_scan=True)
            stats[f"misc:{kind}:{obj.source_span['precision']}"] += 1
        return stats

    # --------------------------------------------------------------- driver
    def enrich_kernel(self, ex) -> dict:
        stats = Counter()
        stats.update(self.enrich_symbols(ex))
        self._record_symbol_ranges(ex)
        stats.update(self.enrich_references(ex))
        stats.update(self.enrich_operations(ex))
        stats.update(self.enrich_bindings(ex))
        stats.update(self.enrich_misc(ex))
        return dict(sorted(stats.items()))
