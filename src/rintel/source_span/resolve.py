"""Resolvers: original source -> canonical SourceSpan (§3, §5-§13).

All searches run on the MASKED ORIGINAL text (comments/strings blanked, every
offset preserved), so a hit index is a character position in the real file.
Nothing is ever guessed: when the range cannot be recovered the result is
LINE_ONLY and the caller is told why in ``evidence``.
"""
from __future__ import annotations

from .model import (KIND_CALLSITE, KIND_DECLARATION, KIND_DEFINITION,
                    KIND_OPERATION, KIND_REFERENCE, PRECISION_EXACT,
                    PRECISION_LINE_ONLY, PRECISION_PARTIAL, make_span)
from .text import SourceText

_FORTRAN = ("fortran", "f77", "f90", "f95", "for", "f")


def _is_fortran(lang: str | None) -> bool:
    return bool(lang) and lang.lower() in _FORTRAN


def _occ_on_line(src: SourceText, name: str, line: int) -> list[tuple[int, int]]:
    hits = src.occurrences_on_line(name, line)
    if hits or not _is_fortran(src.lang):
        return hits
    low = name.lower()
    out = []
    for nm, lst in src.occurrences.items():
        if nm.lower() == low:
            out.extend(src.occurrences_on_line(nm, line))
    out.sort()
    return out


def _call_like(src: SourceText, hit: tuple[int, int]) -> bool:
    i = hit[1]
    n = len(src.masked)
    while i < n and src.masked[i] in " \t\n\r":
        i += 1
    return i < n and src.masked[i] == "("


def _filter_hits(src: SourceText, hits: list[tuple[int, int]],
                 prefer: str | None,
                 exclude: list[tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    """Semantic narrowing (§5/§12): a CALLABLE definition identifier is the one
    followed by ``(``; a DATA identifier is one that is not. ``exclude`` drops
    occurrences that are another object's declaration token (shadowing)."""
    if exclude and hits:
        sub = [h for h in hits
               if not any(h[0] >= a and h[1] <= b for a, b in exclude)]
        if sub:
            hits = sub
    if not prefer or len(hits) <= 1:
        return hits
    if prefer == "call_like":
        sub = [h for h in hits if _call_like(src, h)]
    elif prefer == "non_call_like":
        sub = [h for h in hits if not _call_like(src, h)]
    else:
        sub = hits
    return sub or hits


def _pick(hits: list[tuple[int, int]], occurrence: int | None,
          group_size: int | None) -> tuple[tuple[int, int] | None, str, str, int | None]:
    """Return (hit, precision, why, picked_occurrence_index)."""
    m = len(hits)
    n = group_size if group_size else 1
    if m == 0:
        return None, "none", "identifier_not_found_on_line", None
    if occurrence is not None and 0 <= occurrence < m:
        prec = PRECISION_EXACT if n == m else PRECISION_PARTIAL
        return (hits[occurrence], prec,
                "positional" if n == m else "positional_subset", occurrence)
    if m == 1:
        return hits[0], PRECISION_EXACT, "unique", 0
    return hits[0], PRECISION_PARTIAL, "ambiguous_first", 0


def _line_only(src: SourceText, line: int, kind: str, why: str,
               extra: dict | None = None) -> dict:
    ev = {"method": "identifier_scan", "reason": why,
          "source": "original", "lang": src.lang}
    ev.update(extra or {})
    return make_span(src.path, line, None, line, None, kind,
                     PRECISION_LINE_ONLY, ev)


def _statement_start_before(src: SourceText, index: int,
                            max_back: int = 4000) -> int:
    """Start of the statement containing ``index``, crossing line breaks.

    A recorded line can sit inside a multi-line statement (e.g. an ``if``
    condition spanning lines); backing up to the previous ``; { }`` at paren
    depth 0 makes the span cover the WHOLE statement instead of a fragment.
    """
    i = index
    depth = 0
    floor = max(0, index - max_back)
    fortran = _is_fortran(src.lang)
    while i > floor:
        c = src.masked[i - 1]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth > 0:
                depth -= 1
        elif depth == 0 and c in ";{}":
            break
        elif depth == 0 and fortran and c == "\n":
            break
        i -= 1
    skip = " \t" if fortran else " \t\n\r"
    while i < len(src.masked) and src.masked[i] in skip:
        i += 1
    return i


def _statement_window(src: SourceText, line: int) -> tuple[int, int] | None:
    """[start, end) of the declaration/statement starting on ``line``."""
    i0 = src.index_of(line, 1)
    if i0 is None:
        return None
    n = len(src.masked)
    while i0 < n and src.masked[i0] in " \t\n\r":
        i0 += 1
    term = _statement_terminator(src, i0)
    end = (term + 1) if term is not None else min(n, i0 + 4000)
    return i0, end


def _window_hits(src: SourceText, name: str, line: int) -> list[tuple[int, int]]:
    win = _statement_window(src, line)
    if win is None:
        return []
    a, b = win
    return [(x, y) for x, y in src.occurrences.get(name, [])
            if x >= a and y <= b]


def resolve_identifier(src: SourceText, line: int, name: str,
                       span_kind: str = KIND_REFERENCE,
                       occurrence: int | None = None,
                       group_size: int | None = None,
                       prefer: str | None = None,
                       exclude: list[tuple[int, int]] | None = None,
                       forward_scan: bool = False) -> dict:
    """Span of one identifier token occurrence on ``line``.

    ``forward_scan`` (§25): when the recorded line carries no occurrence, the
    search widens to the declaration/statement that starts there — that is how
    a parameter of a multi-line signature (recorded on the head line) is still
    located exactly. The span line is then the identifier's real line and the
    evidence records the difference.
    """
    hits = _filter_hits(src, _occ_on_line(src, name, line), prefer, exclude)
    if not hits and forward_scan:
        hits = _filter_hits(src, _window_hits(src, name, line), prefer, exclude)
    hit, prec, why, picked = _pick(hits, occurrence, group_size)
    if hit is None:
        return _line_only(src, line, span_kind, why,
                          {"name": name, "occurrences_on_line": 0})
    a, b = hit
    sl, sc = src.pos(a)
    el, ec = src.pos(b - 1)
    method = "identifier_scan" if sl == line else "declaration_forward_scan"
    return make_span(src.path, sl, sc, el, ec, span_kind, prec,
                     {"method": method, "source": "original",
                      "lang": src.lang, "name": name,
                      "recorded_line": line,
                      "occurrences_on_line": len(hits),
                      "group_size": group_size or 1,
                      "pick": why, "prefer": prefer,
                      "occurrence": picked})


def resolve_call(src: SourceText, line: int, name: str,
                 span_kind: str = KIND_CALLSITE,
                 occurrence: int | None = None,
                 group_size: int | None = None,
                 prefer: str | None = None,
                 exclude: list[tuple[int, int]] | None = None) -> dict | None:
    """Span of the whole call expression ``callee(args...)`` (may be multiline)."""
    hits = _filter_hits(src, _occ_on_line(src, name, line), prefer, exclude)
    hit, prec, why, picked = _pick(hits, occurrence, group_size)
    if hit is None:
        return None
    a, b = hit
    i = b
    n = len(src.masked)
    while i < n and src.masked[i] in " \t\n\r":
        i += 1
    if i >= n or src.masked[i] != "(":
        return None
    close = src.match_paren(i)
    if close is None:
        return None
    sl, sc = src.pos(a)
    el, ec = src.pos(close)
    return make_span(src.path, sl, sc, el, ec, span_kind, prec,
                     {"method": "paren_match", "source": "original",
                      "lang": src.lang, "name": name,
                      "occurrences_on_line": len(hits),
                      "group_size": group_size or 1, "pick": why,
                      "prefer": prefer, "occurrence": picked,
                      "multiline": el != sl})


def _statement_terminator(src: SourceText, index: int) -> int | None:
    """Index of the character that ends the statement, paren-aware.

    C: ``;`` or the body ``{``.  Fortran: end of line (a statement is
    line-terminated; ``&`` continuations simply keep the same logical statement
    but stay separate source lines, so the newline is the boundary).
    """
    fortran = _is_fortran(src.lang)
    depth = 0
    i = index
    n = len(src.masked)
    while i < n:
        c = src.masked[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and c == ";":
            return i
        elif depth == 0 and c == "\n":
            if fortran:
                return i
        elif depth == 0 and c == "{":
            return i
        elif depth == 0 and c == "}" and i > index:
            return None
        i += 1
    return None


def resolve_declaration(src: SourceText, line: int, name: str,
                        span_kind: str = KIND_DECLARATION,
                        occurrence: int | None = None,
                        group_size: int | None = None,
                        include_terminator: bool | None = None,
                        prefer: str | None = None,
                        exclude: list[tuple[int, int]] | None = None,
                        forward_scan: bool = False) -> dict:
    """Declaration/header span: first token of the statement .. header end.

    ``DEFINITION``/``DECLARATION`` -> ends at the last non-blank character
    before the body ``{`` / prototype ``;`` (terminator excluded).
    ``DATA_DECLARATION`` -> ends at the terminating ``;`` (inclusive).
    """
    hits = _filter_hits(src, _occ_on_line(src, name, line), prefer, exclude)
    if not hits and forward_scan:
        hits = _filter_hits(src, _window_hits(src, name, line), prefer, exclude)
    hit, prec, why, picked = _pick(hits, occurrence, group_size)
    if hit is None:
        return _line_only(src, line, span_kind, why, {"name": name})
    a, b = hit
    start = _statement_start_before(src, a)
    term = _statement_terminator(src, b)
    if term is None:
        end = len(src.masked) - 1
        while end > start and src.masked[end] in " \t\n\r":
            end -= 1
        inc = False
    else:
        inc = include_terminator
        if inc is None:
            inc = span_kind == "DATA_DECLARATION"
        end = term if inc else term - 1
        while end > start and src.masked[end] in " \t\n\r":
            end -= 1
    sl, sc = src.pos(start)
    el, ec = src.pos(max(end, start))
    return make_span(src.path, sl, sc, el, ec, span_kind, prec,
                     {"method": "declaration_scan", "source": "original",
                      "lang": src.lang, "name": name,
                      "terminator_included": bool(inc),
                      "occurrences_on_line": len(hits),
                      "group_size": group_size or 1, "pick": why,
                      "prefer": prefer, "occurrence": picked,
                      "multiline": el != sl})


def resolve_statement(src: SourceText, line: int,
                      span_kind: str = KIND_OPERATION) -> dict:
    """Span of the statement occupying ``line`` (probe/runtime line records).

    The record only knows a line, so this is reported as PARTIAL with the
    method named — never as an EXACT recovered token range.
    """
    if line < 1 or line > src.line_count:
        return _line_only(src, line, span_kind, "line_out_of_range")
    text = src.line_text(line)
    if not text.strip():
        return _line_only(src, line, span_kind, "blank_line")
    starts = [i for i, ch in enumerate(text) if ch not in " \t"]
    first = starts[0] if starts else 0
    idx = src.index_of(line, first + 1)
    if idx is None:
        return _line_only(src, line, span_kind, "line_out_of_range")
    # a multi-line statement is covered from its real start (paren aware)
    idx = _statement_start_before(src, idx)
    _le = src.masked.find("\n", idx)
    if _le < 0:
        _le = len(src.masked)
    if not src.masked[idx:_le].strip():
        return _line_only(src, line, span_kind, "blank_or_comment_line")
    term = _statement_terminator(src, idx)
    if term is None:
        end = len(src.masked) - 1
        while end > (idx or 0) and src.masked[end] in " \t\n\r":
            end -= 1
        el, ec = src.pos(end)
    else:
        el, ec = src.pos(term)
    sl, sc = src.pos(idx)
    extra = {"method": "probe_line_statement", "source": "original",
             "lang": src.lang, "recorded_line": line,
             "multiline": el != sl,
             "note": "record carries line only; range is the whole statement "
                     "containing that line (multi-line aware)"}
    return make_span(src.path, sl, sc, el, ec, span_kind, PRECISION_PARTIAL,
                     extra)
