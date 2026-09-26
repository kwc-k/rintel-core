"""Canonical evidence-graph data model (MVP round 1).

One canonical graph per repository snapshot.  UI Views (Architecture / Symbol /
Data / Runtime / Change / Verification) are projections of this single graph,
never separate data stores.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .identity import canonical_node_id, identity_schema_version

# ---------------------------------------------------------------------------
# Canonical node kinds (spec §7).  Language-native extras are allowed; the
# canonical set is the *minimum* every projection must understand.
# ---------------------------------------------------------------------------
NODE_KINDS: frozenset[str] = frozenset({
    "REPOSITORY", "DIRECTORY", "FILE",
    "PACKAGE", "MODULE", "SUBMODULE",
    "CLASS", "TYPE", "INTERFACE", "ENUM",
    "FUNCTION", "METHOD", "PROCEDURE", "SUBROUTINE", "PROGRAM",
    "VARIABLE", "CONSTANT", "FIELD", "PARAMETER",
    "API_ENDPOINT", "SERVICE", "TEST", "COMMIT", "DATA_CONTRACT",
})

# ---------------------------------------------------------------------------
# Canonical edge kinds (spec §8).
# ---------------------------------------------------------------------------
EDGE_KINDS: frozenset[str] = frozenset({
    "CONTAINS", "DEFINES", "REFERENCES", "IMPORTS", "USES", "INCLUDES",
    "CALLS", "INHERITS", "IMPLEMENTS", "BINDS_TO",
    "TESTS", "COVERS", "OBSERVED_CALL", "CO_CHANGED",
    # future (schema is open; these are declared now so projections can rely
    # on the vocabulary): FLOWS_TO, DEPENDS_ON, GENERATES, CONFIGURES,
    # SERIALIZES_TO
    "FLOWS_TO", "DEPENDS_ON", "GENERATES", "CONFIGURES", "SERIALIZES_TO",
})

# Evidence sources (spec §9).  Heuristic and runtime-observed edges must never
# be presented as the same fact; `source` is mandatory on every non-trivial
# relation.
EVIDENCE_SOURCES: frozenset[str] = frozenset({
    "tree_sitter", "scip", "runtime", "git", "coverage",
    "manual", "inferred",   # inferred = LLM or heuristic beyond parser facts
})

# Reference modes used by adapters before the indexer resolves identity.
REF_QNAME = "qname"     # exact qualified-name lookup
REF_NAME = "name"       # name-based resolution (same-file → imported → global-unique)
REF_MODULE = "module"   # module/package node lookup by qname (import targets)


@dataclass
class Node:
    """A canonical graph node (definition site)."""
    kind: str
    name: str                 # simple name
    qname: str                # canonical qualified name (identity space)
    language: str
    path: str                 # repo-relative file path ('' for synthetic)
    start_line: Optional[int] = None
    start_col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_id(self) -> str:
        return canonical_node_id(kind=self.kind, qname=self.qname,
                                 language=self.language, path=self.path,
                                 meta=self.meta)

    @property
    def identity_schema_version(self) -> str:
        return identity_schema_version(self.kind)


@dataclass
class Ref:
    """Symbolic reference emitted by an adapter; resolved by the indexer."""
    mode: str                 # REF_QNAME | REF_NAME | REF_MODULE
    value: str                # qname or simple name
    hint_path: Optional[str] = None   # file path context for name resolution
    language: Optional[str] = None    # restrict resolution to a language (BINDS_TO)


@dataclass
class EdgeSpec:
    kind: str
    src: Ref
    dst: Ref
    confidence: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)
    line: Optional[int] = None


@dataclass
class Callsite:
    """A call site recorded for resolution + telemetry (spec Q1/Q3)."""
    path: str
    line: int
    col: Optional[int]
    callee: str                       # as written in source
    candidates: list[str] = field(default_factory=list)   # candidate qnames
    ckind: str = "call"               # call | construct | attribute
    # FAC-EQ0 call-shape metadata used to separate `call_vs_array_ambiguous`
    # from genuine no-target references: {"form": "stmt"|"expr", "args": int}
    shape: Optional[dict] = None


@dataclass
class ImportBinding:
    path: str
    line: Optional[int]
    module_qname: str
    local: Optional[str] = None       # local binding name (alias / symbol)
    only_names: Optional[list[str]] = None  # Fortran `USE m, ONLY: a, b`
    external: bool = False            # target not part of this repository
    src_qname: Optional[str] = None   # qname of the importing unit (edge src)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncludeBinding:
    path: str
    line: Optional[int]
    target: str                       # as written (e.g. "math.h")
    external: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedFile:
    """Everything an adapter extracted from one file (facts, not graph)."""
    language: str
    path: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    callsites: list[Callsite] = field(default_factory=list)
    imports: list[ImportBinding] = field(default_factory=list)
    includes: list[IncludeBinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # recorded, never fabricated
