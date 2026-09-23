"""Original-source text index: line/column mapping + identifier occurrences.

The resolver never invents text: it searches the ORIGINAL file (masked
comments/strings only, offsets preserved) and maps character indices back to
1-based line/column pairs (§13, §14).
"""
from __future__ import annotations

import re
from bisect import bisect_right
from pathlib import Path

from .mask import IDENT_CHARS, mask_for

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


class SourceText:
    """Cached original text of one file with index -> (line, column) mapping."""

    def __init__(self, path: str, text: str, lang: str | None = None):
        self.path = path
        self.text = text
        self.lang = lang
        self.masked = mask_for(lang, text)
        self._starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self._starts.append(i + 1)
        self._occ: dict[str, list[tuple[int, int]]] | None = None
        self.crlf = "\r\n" in text

    # ---------------------------------------------------------------- mapping
    @property
    def line_count(self) -> int:
        return len(self._starts)

    def line_text(self, line: int) -> str:
        if line < 1 or line > len(self._starts):
            return ""
        start = self._starts[line - 1]
        end = self._starts[line] - 1 if line < len(self._starts) else len(self.text)
        return self.text[start:end].rstrip("\r\n")   # display only

    def pos(self, index: int) -> tuple[int, int]:
        """Character index -> (1-based line, 1-based column)."""
        line = bisect_right(self._starts, index)
        return line, index - self._starts[line - 1] + 1

    def index_of(self, line: int, column: int) -> int | None:
        # legacy artifacts used the literal string "UNKNOWN" for columns
        if not isinstance(line, int) or not isinstance(column, int):
            return None
        if line < 1 or line > len(self._starts):
            return None
        i = self._starts[line - 1] + column - 1
        if i > len(self.text):
            return None
        return i

    def slice_text(self, sl: int, sc: int, el: int, ec: int) -> str:
        i0 = self.index_of(sl, sc)
        i1 = self.index_of(el, ec)
        if i0 is None or i1 is None or i1 < i0:
            return ""
        return self.text[i0:i1 + 1]

    # ------------------------------------------------------------ occurrences
    @property
    def occurrences(self) -> dict[str, list[tuple[int, int]]]:
        """name -> [(start_index, end_index_exclusive)] from MASKED text."""
        if self._occ is None:
            occ: dict[str, list[tuple[int, int]]] = {}
            for m in _IDENT_RE.finditer(self.masked):
                occ.setdefault(m.group(0), []).append((m.start(), m.end()))
            self._occ = occ
        return self._occ

    def occurrences_on_line(self, name: str, line: int) -> list[tuple[int, int]]:
        hits = self.occurrences.get(name)
        if not hits:
            return []
        lo = self._starts[line - 1] if 1 <= line <= len(self._starts) else None
        hi = (self._starts[line] if line < len(self._starts) else len(self.text))
        if lo is None:
            return []
        return [(a, b) for a, b in hits if lo <= a < hi]

    def identifier_at(self, index: int) -> tuple[str, int, int] | None:
        """Identifier token covering ``index`` (from masked text)."""
        n = len(self.masked)
        if index >= n:
            return None
        if self.masked[index] not in IDENT_CHARS:
            return None
        a = index
        while a > 0 and self.masked[a - 1] in IDENT_CHARS:
            a -= 1
        b = index
        while b < n and self.masked[b] in IDENT_CHARS:
            b += 1
        return self.masked[a:b], a, b

    # ------------------------------------------------------------------ parens
    def match_paren(self, open_index: int) -> int | None:
        """Index of the ``)`` matching the ``(`` at ``open_index`` (masked)."""
        if open_index >= len(self.masked) or self.masked[open_index] != "(":
            return None
        depth = 0
        for i in range(open_index, len(self.masked)):
            c = self.masked[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        return None

    def statement_start(self, index: int) -> int:
        """Start of the statement containing ``index`` (prev ``;{}`` or line)."""
        i = index
        while i > 0:
            c = self.masked[i - 1]
            if c in ";{}":
                break
            if c == "\n":
                break
            i -= 1
        while i < len(self.masked) and self.masked[i] in " \t":
            i += 1
        return i


def load_source(root: Path | str, rel: str, lang: str | None = None) -> SourceText | None:
    p = Path(root) / rel
    if not p.exists():
        return None
    try:
        # read_bytes + decode: NO universal-newline translation (§16) — columns
        # must be positions in the real file, so a CRLF file keeps its \r.
        text = p.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    return SourceText(rel, text, lang)


class SourceCache:
    """Lazy per-file cache (one file read + one mask + one ident scan)."""

    def __init__(self, root: Path | str, lang_of=None):
        self.root = Path(root)
        self.lang_of = lang_of or (lambda rel: None)
        self._cache: dict[str, SourceText | None] = {}

    def get(self, rel: str | None) -> SourceText | None:
        if not rel:
            return None
        if rel not in self._cache:
            self._cache[rel] = load_source(self.root, rel, self.lang_of(rel))
        return self._cache[rel]
