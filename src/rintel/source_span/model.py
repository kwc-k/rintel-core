"""Canonical SourceSpan contract (SOURCE-SPAN-COL0 §2-§4, §14, §30).

ONE position system for the whole repo. Every object that points into source
(kernel Context/Symbol/Reference/Operation/ArgumentBinding, performance
findings, runtime evidence witnesses, diagnostics) uses this dict shape:

    {
      "file":         "faclib/rates.c" | None,
      "start_line":   214,                # 1-based
      "start_column": 13,                 # 1-based, None => unknown
      "end_line":     214,                # 1-based, inclusive
      "end_column":   31,                 # 1-based, INCLUSIVE last character
      "span_kind":    "CALLSITE",
      "precision":    "EXACT",
      "evidence":     {...},
      "text":         "faclib/rates.c:214:13-31",   # derived display form
    }

Column semantics (§14): the column is the character offset in the ORIGINAL
file text decoded as UTF-8 (Python ``str`` index + 1). It is NOT a byte
offset, NOT a tab-expanded visual column, NOT a UTF-16 code-unit offset.
Columns are inclusive on both ends: ``start_column=13, end_column=19`` covers
exactly 7 characters (the identifier ``Maxwell``).

Precision (§3): EXACT | LINE_ONLY | PARTIAL | UNKNOWN. A range is only ever
EXACT when it was recovered from the original source text; nothing is guessed
to manufacture a column (§33: a wrong column marked EXACT is a FAIL).
"""
from __future__ import annotations

from dataclasses import dataclass, field

COLUMN_BASE = 1

PRECISION_EXACT = "EXACT"
PRECISION_LINE_ONLY = "LINE_ONLY"
PRECISION_PARTIAL = "PARTIAL"
PRECISION_UNKNOWN = "UNKNOWN"
PRECISIONS = (PRECISION_EXACT, PRECISION_LINE_ONLY, PRECISION_PARTIAL,
              PRECISION_UNKNOWN)

KIND_DEFINITION = "DEFINITION"
KIND_REFERENCE = "REFERENCE"
KIND_CALLSITE = "CALLSITE"
KIND_DECLARATION = "DECLARATION"
KIND_OPERATION = "OPERATION"
KIND_DATA_DECLARATION = "DATA_DECLARATION"
KIND_PERFORMANCE_FINDING = "PERFORMANCE_FINDING"
SPAN_KINDS = (KIND_DEFINITION, KIND_REFERENCE, KIND_CALLSITE,
              KIND_DECLARATION, KIND_OPERATION, KIND_DATA_DECLARATION,
              KIND_PERFORMANCE_FINDING)

# precision that carries a trustworthy character range
RANGE_PRECISIONS = (PRECISION_EXACT, PRECISION_PARTIAL)


def format_span(span: dict | None) -> str:
    """Canonical display form (§22): never prints a fake ``:0``."""
    if not span:
        return "UNKNOWN"
    f = span.get("file")
    sl = span.get("start_line")
    if not f or not sl:
        return "UNKNOWN"
    p = span.get("precision")
    sc, ec = span.get("start_column"), span.get("end_column")
    el = span.get("end_line") or sl
    if p in RANGE_PRECISIONS and sc and ec:
        if el != sl:
            return f"{f}:{sl}:{sc}-{el}:{ec}"
        return f"{f}:{sl}:{sc}-{ec}"
    return f"{f}:{sl}"


@dataclass(frozen=True)
class SourceSpan:
    """The single canonical position object (§17: no second position system)."""
    file: str | None = None
    start_line: int | None = None
    start_column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    span_kind: str = KIND_REFERENCE
    precision: str = PRECISION_UNKNOWN
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "file": self.file,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "span_kind": self.span_kind,
            "precision": self.precision,
            "evidence": dict(self.evidence or {}),
        }
        d["text"] = format_span(d)
        return d

    @property
    def text(self) -> str:
        return format_span(self.to_dict())

    @property
    def has_range(self) -> bool:
        return (self.precision in RANGE_PRECISIONS
                and self.start_column is not None
                and self.end_column is not None)

    @classmethod
    def from_dict(cls, d: dict | None) -> "SourceSpan | None":
        if not d:
            return None
        if isinstance(d, SourceSpan):
            return d
        return cls(
            file=d.get("file"),
            start_line=d.get("start_line"),
            start_column=d.get("start_column"),
            end_line=d.get("end_line") or d.get("start_line"),
            end_column=d.get("end_column"),
            span_kind=d.get("span_kind") or KIND_REFERENCE,
            precision=d.get("precision") or PRECISION_UNKNOWN,
            evidence=dict(d.get("evidence") or {}),
        )


def make_span(file: str | None, start_line: int | None,
              start_column: int | None = None,
              end_line: int | None = None, end_column: int | None = None,
              span_kind: str = KIND_REFERENCE,
              precision: str = PRECISION_UNKNOWN,
              evidence: dict | None = None) -> dict:
    """Build a canonical span dict (the shape every producer must emit)."""
    if start_line is None:
        precision = PRECISION_UNKNOWN
        start_column = end_column = None
        end_line = None
    elif start_column is None or end_column is None:
        precision = PRECISION_LINE_ONLY if file else PRECISION_UNKNOWN
        start_column = end_column = None
        end_line = start_line
    return SourceSpan(file=file, start_line=start_line,
                      start_column=start_column, end_line=end_line or start_line,
                      end_column=end_column, span_kind=span_kind,
                      precision=precision, evidence=evidence or {}).to_dict()


def line_only(file: str | None, line: int | None,
              span_kind: str = KIND_REFERENCE,
              evidence: dict | None = None) -> dict:
    """Honest fallback (§6/§19): line known, columns NOT recovered."""
    return make_span(file, line, None, line, None, span_kind,
                     PRECISION_LINE_ONLY if file and line else PRECISION_UNKNOWN,
                     evidence)


def unknown(span_kind: str = KIND_REFERENCE,
            evidence: dict | None = None) -> dict:
    return make_span(None, None, None, None, None, span_kind,
                     PRECISION_UNKNOWN, evidence)


@dataclass(frozen=True)
class DiagnosticLocation:
    """§30: the one location shape diagnostics will map onto later
    (pytest / compiler / type checker / DRC / LVS)."""
    source_span: dict = field(default_factory=dict)
    related_spans: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"source_span": self.source_span,
                "related_spans": list(self.related_spans)}
