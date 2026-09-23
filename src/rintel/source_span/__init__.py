"""Canonical SourceSpan (SOURCE-SPAN-COL0).

Public API — import from here, never re-declare positions elsewhere:

    from rintel.source_span import (SourceSpan, DiagnosticLocation, make_span,
                                    line_only, format_span, SourceCache,
                                    resolve_identifier, resolve_call,
                                    resolve_declaration, resolve_statement,
                                    SpanEnricher, SPAN_KINDS, PRECISIONS)
"""
from .model import (COLUMN_BASE, KIND_CALLSITE, KIND_DATA_DECLARATION,
                    KIND_DECLARATION, KIND_DEFINITION, KIND_OPERATION,
                    KIND_PERFORMANCE_FINDING, KIND_REFERENCE, PRECISION_EXACT,
                    PRECISION_LINE_ONLY, PRECISION_PARTIAL, PRECISION_UNKNOWN,
                    PRECISIONS, RANGE_PRECISIONS, SPAN_KINDS, DiagnosticLocation,
                    SourceSpan, format_span, line_only, make_span, unknown)
from .mask import mask_c, mask_for, mask_fortran
from .resolve import (resolve_call, resolve_declaration, resolve_identifier,
                      resolve_statement)
from .text import SourceCache, SourceText, load_source
from .enrich import SpanEnricher
from .probe_anchors import (PROBE_ANCHORS, anchor_span,
                            dgesv_callsite_span, recorded_site_span)

__all__ = [
    "COLUMN_BASE", "DiagnosticLocation", "KIND_CALLSITE",
    "KIND_DATA_DECLARATION", "KIND_DECLARATION", "KIND_DEFINITION",
    "KIND_OPERATION", "KIND_PERFORMANCE_FINDING", "KIND_REFERENCE",
    "PRECISION_EXACT", "PRECISION_LINE_ONLY", "PRECISION_PARTIAL",
    "PRECISION_UNKNOWN", "PRECISIONS", "RANGE_PRECISIONS", "SPAN_KINDS",
    "SourceCache", "SourceSpan", "SourceText", "SpanEnricher", "format_span",
    "line_only", "load_source", "make_span", "mask_c", "mask_for",
    "mask_fortran", "resolve_call", "resolve_declaration",
    "resolve_identifier", "resolve_statement", "unknown",
]
