"""SEMANTIC-SUBSTRATE1 kernel model (formal substrate, frozen as
SEMANTIC-SUBSTRATE-FORMAL0 — NOT redesigned here, only implemented).

Kernel objects:
    Context / Symbol / Reference / Operation / Value / Location / Resource
    + ArgumentBinding (call actual -> formal) + TypeTerm (explicit type ref).

Invariants (each is a gate):
    - identity = declaration context + name  (never name alone)      (KS2)
    - Symbol != Value != Location                                     (KS5)
    - target resolution ∈ {EXACT, CANDIDATE_SET, UNKNOWN} ONLY;
      UNRESOLVED/uncovered is coverage/analyzer state, not resolution (KS4/KS8)
    - every kernel object carries evidence + revision (single truth chain)
    - projection objects reference kernel objects via support_refs (KS7)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

KContextKind = Literal["FILE", "MODULE", "FUNCTION", "BLOCK", "TYPE",
                       "NAMESPACE", "REGION"]
KSymbolKind = Literal["CALLABLE", "DATA", "TYPE", "MODULE", "NAMESPACE"]
Resolution = Literal["EXACT", "CANDIDATE_SET", "UNKNOWN"]
OKind = Literal["READ", "WRITE", "CALL", "RETURN", "COMPARE", "BRANCH",
                "ALLOCATE", "FREE", "TRANSFORM", "INDEX", "RESOURCE_ACCESS"]
VKind = Literal["literal", "scalar", "function_address", "symbolic",
                "array_tensor", "unknown"]
LKind = Literal["variable", "array_storage", "field", "global_state",
                "parameter", "pointer_target"]
Truth = Literal["OBSERVED", "INFERRED", "DERIVED", "HEURISTIC", "UNKNOWN"]
Coverage = Literal["COMPLETE", "PARTIAL"]

KERNEL_RESOURCES = {
    "resource:file": "File IO (stdio / file descriptors)",
    "resource:gpu": "GPU / accelerators",
    "resource:network": "Network communication",
    "resource:db": "Database / persistent stores",
    "resource:external_lib": "External library calls",
    "resource:memory": "Heap / system memory",
}


def span(file: str | None, start_line: int | None, end_line: int | None,
         start_column: int | None = None, end_column: int | None = None,
         span_kind: str = "REFERENCE", precision: str | None = None,
         evidence: dict | None = None) -> dict:
    """Canonical SourceSpan (SOURCE-SPAN-COL0): delegates to the ONE contract
    in ``rintel.source_span`` — never a second position system."""
    from ..source_span import make_span
    if precision is None:
        precision = ("EXACT" if start_column is not None and end_column is not None
                     else "LINE_ONLY")
    return make_span(file, start_line, start_column, end_line or start_line,
                     end_column, span_kind, precision, evidence)


@dataclass
class Evidence:
    kind: str                      # DECL | REF | OP | RESOLUTION | CALL | ...
    expr: str
    line: int | None = None
    source: str = "code"

    def to_dict(self):
        return asdict(self)


@dataclass
class Context:
    context_id: str
    kind: KContextKind
    parent_context_id: str | None = None
    source_span: dict | None = None
    language: str | None = None
    revision: str = "frozen-0"
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class Symbol:
    symbol_id: str
    name: str
    kind: KSymbolKind
    declaration_context: str                          # context id
    owner: str | None = None                          # canonical owner (function/file)
    source_span: dict | None = None
    language: str | None = None
    revision: str = "frozen-0"
    evidence: list = field(default_factory=list)
    # semantic attributes (NOT separate primitives — §15/§16)
    role: str = "REGULAR"              # REGULAR | NAMED_CONSTANT | PARAMETER | GLOBAL
    mutability: Literal["MUTABLE", "IMMUTABLE", "UNKNOWN"] = "UNKNOWN"
    dtype: str | None = None
    storage_class: Literal["GLOBAL", "LOCAL", "STATIC", "PARAMETER",
                           "EXTERNAL", "UNKNOWN"] = "UNKNOWN"
    type_ref: str | None = None        # TypeTerm id (explicit type reference)
    is_derived_alias: bool = False
    projection_ids: list = field(default_factory=list)   # existing objects (e.g. port ids)
    # DECLARES/DEFINES/SAME_CALLABLE unification (FAC-LIBRARY-COVERAGE0, LC4):
    # a header prototype and the implementation are TWO Symbol objects (their
    # declaration contexts differ) linked via canonical_callable_id — never
    # two unrelated Functions, never a merged identity.
    is_definition: bool = True         # has a body in the covered universe
    is_declaration: bool = False       # prototype/declaration only
    canonical_callable_id: str | None = None   # SAME_CALLABLE representative
    # SOURCE-SPAN-COL0 §5: source_span is the IDENTIFIER token; the declaration
    # header / data statement lives in its own canonical span (same contract)
    declaration_span: dict | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class Value:
    value_id: str
    kind: VKind
    dtype: str | None = None
    literal: str | None = None
    representing: list = field(default_factory=list)    # data symbol ids
    points_to: list = field(default_factory=list)       # location ids
    # K4 alias state: MUST (p=&x), MAY (q=p via pointer copy), UNKNOWN
    alias_rel: Literal["MUST", "MAY", "UNKNOWN"] = "UNKNOWN"
    source_span: dict | None = None
    language: str | None = None
    revision: str = "frozen-0"
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class Location:
    location_id: str
    kind: LKind
    name: str | None = None
    container: str | None = None                       # context/symbol id
    storage_class: Literal["GLOBAL", "LOCAL", "STATIC", "PARAMETER",
                           "UNKNOWN"] = "UNKNOWN"
    pointed_from: list = field(default_factory=list)    # value ids (reverse alias)
    source_span: dict | None = None
    language: str | None = None
    revision: str = "frozen-0"
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class Reference:
    reference_id: str
    text: str
    context_id: str                                    # context the reference occurs in
    source_span: dict | None = None
    language: str | None = None
    revision: str = "frozen-0"
    resolution: Resolution = "UNKNOWN"                 # §18: NEVER EXACT+missing target
    candidate_symbol_ids: list = field(default_factory=list)
    symbol_id: str | None = None                       # set iff resolution == EXACT
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class Operation:
    operation_id: str
    kind: OKind
    context_id: str                                    # the context that performs it
    actor: str | None = None                           # canonical owner symbol (CALLABLE)
    inputs: list = field(default_factory=list)         # value/reference ids
    outputs: list = field(default_factory=list)
    target_reference: str | None = None                # Reference id (CALL)
    guard: str | None = None
    resource_refs: list = field(default_factory=list)
    source_span: dict | None = None
    language: str | None = None
    revision: str = "frozen-0"
    truth: Truth = "INFERRED"
    coverage: Coverage = "PARTIAL"
    evidence: list = field(default_factory=list)
    # realizable CALL path data (§22): balanced CALL/RETURN machinery slots
    call_site_id: str | None = None
    callee_entry: str | None = None                    # callee CALLABLE symbol id (EXACT)
    return_site: str | None = None
    # K9: state access is an explicit flag — never silently an input port
    state_use: Literal["READ", "WRITE", "READWRITE", "NONE"] = "NONE"
    # SOURCE-SPAN-COL0 §12/§30: related canonical spans (e.g. the resolved
    # callee definition) — NEVER used to fake the caller callsite span
    related_spans: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class ArgumentBinding:
    binding_id: str
    call_operation_id: str
    actual: str                                        # actual expr / Value id
    formal_symbol_id: str
    position: int | None = None
    keyword: str | None = None
    truth: Truth = "OBSERVED"
    evidence: list = field(default_factory=list)
    # SOURCE-SPAN-COL0 §17: the actual-argument range inside the call expression
    source_span: dict | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class TypeTerm:
    """Explicit type reference (§16): a builtin name or a TYPE symbol."""
    type_term_id: str
    name: str
    context_id: str
    symbol_id: str | None = None       # set iff refers to user-defined TYPE symbol
    language: str | None = None
    source_span: dict | None = None
    revision: str = "frozen-0"
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class Resource:
    resource_id: str
    kind: str
    description: str = ""
    evidence: list = field(default_factory=list)
    revision: str = "frozen-0"

    def to_dict(self):
        return asdict(self)


RESOURCES = [Resource(rid, "KERNEL", desc) for rid, desc in KERNEL_RESOURCES.items()]
