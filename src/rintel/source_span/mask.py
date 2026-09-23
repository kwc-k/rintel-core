"""Offset-preserving comment/string maskers (SOURCE-SPAN-COL0 §13).

Every character of the input keeps its index and every newline is preserved,
so a position found in the masked text IS a position in the original source.
Comments and string literals are replaced by spaces (never deleted), which is
what lets a resolver match identifiers without hitting comment text while still
reporting columns that are valid for the original file.

Note: ``rintel.kernel.extract_c.strip_c_text`` is NOT offset preserving — it
collapses ``//`` (2 chars -> 1) and drops character literals entirely. It only
ever fed line numbers to the walker, so extraction stayed correct, but it must
never be used for columns. The maskers here are the column-safe path.
"""
from __future__ import annotations

IDENT_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$"


def mask_c(text: str) -> str:
    """Blank C/C++ comments and string/char literals, preserving offsets."""
    out = list(text)
    i, n = 0, len(text)
    in_str = in_char = in_lc = in_blk = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_lc:
            if c == "\n":
                in_lc = False
            else:
                out[i] = " "
            i += 1
            continue
        if in_blk:
            if c == "*" and nxt == "/":
                out[i] = out[i + 1] = " "
                i += 2
                in_blk = False
                continue
            if c != "\n":
                out[i] = " "
            i += 1
            continue
        if in_str:
            if c == "\\" and nxt:
                out[i] = " "
                if nxt != "\n":
                    out[i + 1] = " "
                i += 2
                continue
            if c == "\"":
                out[i] = " "
                in_str = False
                i += 1
                continue
            if c != "\n":
                out[i] = " "
            i += 1
            continue
        if in_char:
            if c == "\\" and nxt:
                out[i] = " "
                if nxt != "\n":
                    out[i + 1] = " "
                i += 2
                continue
            if c == "'":
                out[i] = " "
                in_char = False
                i += 1
                continue
            if c != "\n":
                out[i] = " "
            i += 1
            continue
        if c == "/" and nxt == "/":
            out[i] = " "
            in_lc = True
            i += 1
            continue
        if c == "/" and nxt == "*":
            out[i] = " "
            in_blk = True
            i += 1
            continue
        if c == "\"":
            out[i] = " "
            in_str = True
            i += 1
            continue
        if c == "'":
            out[i] = " "
            in_char = True
            i += 1
            continue
        i += 1
    return "".join(out)


def mask_fortran(text: str) -> str:
    """Blank Fortran comments and string literals, preserving offsets.

    Handles free-form ``!`` comments, fixed-form ``C``/``c``/``*`` comment
    lines (column 1), ``'``/``"`` strings with doubled-quote escaping, and
    Hollerith-free plain text. Continuation lines are NOT joined (§13): the
    original text is what spans are measured against.
    """
    out = list(text)
    i, n = 0, len(text)
    line_start = True
    in_s = None
    while i < n:
        c = text[i]
        if in_s:
            if c == "\n":
                in_s = None
                line_start = True
                i += 1
                continue
            if c == in_s:
                if i + 1 < n and text[i + 1] == in_s:      # doubled quote
                    out[i] = out[i + 1] = " "
                    i += 2
                    continue
                out[i] = " "
                in_s = None
                i += 1
                continue
            out[i] = " "
            i += 1
            continue
        if c == "\n":
            line_start = True
            i += 1
            continue
        if line_start:
            if c in "Cc*" and (i + 1 >= n or text[i + 1] != "\n"):
                # fixed-form full-line comment
                j = i
                while j < n and text[j] != "\n":
                    out[j] = " "
                    j += 1
                i = j
                continue
            line_start = False
        if c == "!":
            j = i
            while j < n and text[j] != "\n":
                out[j] = " "
                j += 1
            i = j
            continue
        if c in "'\"":
            out[i] = " "
            in_s = c
            i += 1
            continue
        i += 1
    return "".join(out)


def mask_for(lang: str | None, text: str) -> str:
    if (lang or "").lower().startswith("f") or (lang or "").lower() in ("f90", "fortran"):
        return mask_fortran(text)
    return mask_c(text)
