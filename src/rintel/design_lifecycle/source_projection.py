"""One pinned SourceSpan shape for Change brief, REST workspace and MCP workspace."""
from __future__ import annotations

from typing import Any, Mapping, TypedDict


class SourceSpanProjection(TypedDict):
    file: str
    path: str
    start_line: int
    start_column: int | None
    end_line: int
    end_column: int | None
    span_kind: str
    precision: str
    revision: str
    subject: str
    entity_type: str
    evidence_ref: str | None


def project_source_span(
    entity: Mapping[str, Any], *, revision: str, subject: str,
    entity_type: str, evidence_ref: str | None = None,
    location: Mapping[str, Any] | None = None,
) -> SourceSpanProjection | None:
    """Normalize stored flat fields and historical nested brief spans.

    Missing or ambiguous line ownership remains unresolved; this function never
    picks a nearby source location or a different revision.
    """
    nested = entity.get("source_span")
    nested = nested if isinstance(nested, Mapping) else {}
    location = location or {}

    def field(*names: str) -> Any:
        for row in (entity, nested, location):
            for name in names:
                value = row.get(name)
                if value is not None:
                    return value
        return None

    path = field("path", "file")
    start = field("start_line", "line")
    end = field("end_line") or start
    if (not isinstance(path, str) or not path or type(start) is not int
            or type(end) is not int or start < 1 or end < start):
        return None
    start_column = field("start_column", "start_col", "column")
    end_column = field("end_column", "end_col")
    if type(start_column) is not int or start_column < 1:
        start_column = None
    if type(end_column) is not int or end_column < 1:
        end_column = None
    span_kind = field("span_kind")
    precision = field("precision")
    return {
        "file": path, "path": path, "start_line": start,
        "start_column": start_column, "end_line": end,
        "end_column": end_column,
        "span_kind": span_kind if isinstance(span_kind, str) and span_kind else (
            "ENTITY" if entity_type == "node" else "EVIDENCE_LOCATION"),
        "precision": precision if isinstance(precision, str) and precision else (
            "COLUMN" if start_column is not None else "LINE"),
        "revision": revision, "subject": subject,
        "entity_type": entity_type, "evidence_ref": evidence_ref,
    }
