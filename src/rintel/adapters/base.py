"""Language adapter contract (spec §1 Q1, §2, §44 P0).

An adapter converts one file of source into *facts* (nodes / edge specs /
call sites / import & include bindings / notes).  Facts are language-shaped;
the indexer turns them into the single canonical evidence graph and records
anything it cannot resolve instead of fabricating edges (spec §32).
"""
from __future__ import annotations

import hashlib

from ..model import ParsedFile
from ..tsutil import parser_for


class ParseError(Exception):
    def __init__(self, relpath: str, exc: Exception):
        super().__init__(f"parse failed for {relpath}: {exc}")
        self.relpath = relpath


def _line_byte_starts(data: bytes) -> list[int]:
    """Byte offset of every line start (tree-sitter rows are lines)."""
    starts = [0]
    for i, b in enumerate(data):
        if b == 0x0A:  # \n
            starts.append(i + 1)
    return starts


class LanguageAdapter:
    language: str = ""
    extensions: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.parser = parser_for(self.language)
        # per-file parse state: tree-sitter node offsets are UTF-8 byte
        # indexes; Python strings are char-indexed, so names/columns are
        # only correct when the offsets are converted back (see text/col).
        self._data: bytes = b""
        self._line_starts: list[int] = [0]
        # optional fixed-form normalizer mapping (S6): normalized buffer
        # positions -> original file positions (sourced offsets stay exact)
        self._srcmap = None

    def can_parse(self, relpath: str) -> bool:
        return relpath.endswith(self.extensions)

    def normalize_source(self, relpath: str, source: str):
        """Optional pre-parse source transform (returns a SourceMapper or
        None).  When set, tree-sitter parses the normalized text; line/col/
        range are mapped back to the ORIGINAL file."""
        return None

    def parse(self, relpath: str, source: str) -> ParsedFile:
        """Parse + extract; raises ParseError on fatal failure."""
        try:
            norm = self.normalize_source(relpath, source)
            if norm is not None:
                data = norm.text.encode("utf-8")
                self._srcmap = norm
            else:
                data = source.encode("utf-8")
                self._srcmap = None
            self._data = data
            self._line_starts = _line_byte_starts(data)
            tree = self.parser.parse(data)
            pf = self.extract(relpath, data, tree.root_node)
            if tree.root_node.has_error:
                pf.notes.append("tree_error_nodes=1")
            if norm is not None:
                pf.notes.extend(norm.notes)
            return pf
        except ParseError:
            raise
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            raise ParseError(relpath, exc) from exc

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def text(node, src: bytes) -> str:
        """Source text of a node.  `src` is the UTF-8 bytes of the file;
        tree-sitter's start/end_byte are byte offsets, so slicing the raw
        Python str (char offsets) would garble every name in any file that
        contains multi-byte characters before the node (S5B real-repo bug:
        Chinese docstrings produced garbage symbol names like
        `CLASS f __init__(` for a plain `class StockAnalysisPipeline:`).
        """
        return src[node.start_byte:node.end_byte].decode("utf-8")

    @staticmethod
    def children_by_type(node, ntype: str):
        return [c for c in node.children if c.type == ntype]

    @staticmethod
    def child_by_type(node, ntype: str):
        for c in node.children:
            if c.type == ntype:
                return c
        return None

    @staticmethod
    def child_named(node, name: str):
        for c in node.named_children:
            if c.type == name:
                return c
        return None

    def line(self, node) -> int:
        """Original 1-based line (mapped through the S6 fixed-form mapper)."""
        return self._mapped_pt(node, node.start_point)[0]

    def _mapped_pt(self, node, point) -> tuple[int, int]:
        """Original (line, col) for a tree-sitter point in the parsed
        buffer (normalized or not)."""
        if self._srcmap is not None:
            return self._srcmap.orig_pt(point[0], point[1])
        return point[0] + 1, self._char_col(point[0], point[1]) + 1

    def _mapped_end(self, node) -> tuple[int, int]:
        if self._srcmap is not None:
            return self._srcmap.orig_end(node.end_point[0], node.end_point[1])
        return (node.end_point[0] + 1,
                self._char_col(node.end_point[0], node.end_point[1]) + 1)

    def col(self, node) -> int:
        """1-based character column (tree-sitter columns are byte offsets)."""
        return self._mapped_pt(node, node.start_point)[1]

    def range(self, node) -> tuple[int | None, int | None, int | None, int | None]:
        s = self._mapped_pt(node, node.start_point)
        e = self._mapped_end(node)
        return (s[0], s[1], e[0], e[1])

    def _char_col(self, row: int, byte_col: int) -> int:
        """Byte column of a tree-sitter point → char column on that line."""
        line_start = (self._line_starts[row]
                      if row < len(self._line_starts)
                      else len(self._data))
        prefix = self._data[line_start:line_start + byte_col]
        return len(prefix.decode("utf-8", errors="replace"))

    def extract(self, relpath: str, source: str, root) -> ParsedFile:
        raise NotImplementedError


def file_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
