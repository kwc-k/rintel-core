"""FLOW1-ANALYZER0 / ANALYZER-MAP0 frozen contract: AnalysisFact.

All third-party analyzers enter the system exclusively through this contract:

    Third-party analyzer → Provider Adapter → AnalysisFact
        → Canonical Evidence / SoftwareNetlist

Frozen rules:
- truth_class  (OBSERVED / RESOLVED / INFERRED / HEURISTIC)
  OBSERVED/RESOLVED may feed high-confidence Evidence projection;
  INFERRED/HEURISTIC stay explicitly marked and are NEVER silently
  upgraded into canonical observed facts.
- execution_modality (MUST / MAY / UNKNOWN): will this Operation run?
- target_resolution (EXACT / CANDIDATE_SET / UNKNOWN): who does it hit?
- coverage (COMPLETE / PARTIAL / UNKNOWN): are all candidates found?
  These four dimensions are INDEPENDENT — they must never be collapsed
  into a single confidence number.
- Provider capabilities are declared first; undeclared operations raise
  CapabilityUnavailable instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FactKind(str, Enum):
    SYMBOL = "SYMBOL"
    CALL = "CALL"
    DATA_FLOW = "DATA_FLOW"
    CONTROL_FLOW = "CONTROL_FLOW"
    TYPE = "TYPE"
    SIGNATURE = "SIGNATURE"
    DIAGNOSTIC = "DIAGNOSTIC"


class SemanticKind(str, Enum):
    """Operation/semantic kind inside a FactKind (frozen base set).

    Provider adapters normalize analyzer node types onto this set — e.g.
    Joern's `<operator>.assignment` → WRITE, `<operator>.addition` →
    COMPUTE, `<operator>.equals` → COMPARE; true method/function calls →
    CALL.  Never confuse operator nodes with software calls.
    """
    # control
    CALL = "CALL"
    BRANCH = "BRANCH"
    LOOP = "LOOP"
    RETURN = "RETURN"
    RESOURCE_USE = "RESOURCE_USE"
    # data / computation
    READ = "READ"
    WRITE = "WRITE"
    COMPUTE = "COMPUTE"
    COMPARE = "COMPARE"
    STATE_ACCESS = "STATE_ACCESS"
    DYNAMIC_DISPATCH = "DYNAMIC_DISPATCH"
    DECLARATION = "DECLARATION"
    OTHER = "OTHER"


class TruthClass(str, Enum):
    OBSERVED = "OBSERVED"      # directly observed (statement-level/parser)
    RESOLVED = "RESOLVED"      # resolved to a canonical symbol, evidenced
    INFERRED = "INFERRED"      # conservative may-flow / heuristic propagation
    HEURISTIC = "HEURISTIC"    # naming/pattern heuristic, no resolution


HIGH_CONFIDENCE_CLASSES = frozenset({TruthClass.OBSERVED, TruthClass.RESOLVED})


class ExecutionModality(str, Enum):
    """Will this Operation definitely execute on every relevant path?

    MUST — frozen semantics (P0.1): MUST_IF_SCOPE_REACHED.
    "If the operation's control scope has been reached and the execution
    follows the normal control path, the operation MUST execute."
    It is NOT a statement that the whole program run executes it —
    e.g. in  a(); b()  a throwing an exception means b() is NOT
    guaranteed, so b() must not be labeled MUST at program level;
    within the sequential scope of the enclosing block both are MUST,
    and MAY covers if/loop/select branches.  Internal name:
    MUST_IF_SCOPE_REACHED (the wire enum stays MUST).
    """
    MUST = "MUST"
    MAY = "MAY"
    UNKNOWN = "UNKNOWN"


class TargetResolution(str, Enum):
    """Once executed, how precisely is the target known?"""
    EXACT = "EXACT"
    CANDIDATE_SET = "CANDIDATE_SET"
    UNKNOWN = "UNKNOWN"


class Coverage(str, Enum):
    """Is the candidate set confirmed complete?"""
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class CapabilityUnavailable(Exception):
    """Raised by providers that cannot service a requested operation."""

    def __init__(self, capability: str, provider: str, detail: str = ""):
        self.capability = capability
        self.provider = provider
        self.detail = detail
        super().__init__(
            f"provider '{provider}' has no capability '{capability}'"
            + (f" ({detail})" if detail else ""))


class ProviderCapabilities:
    """Declared capability set; callers must check before invoking."""

    def __init__(self, *, symbols: bool = False, calls: bool = False,
                 cfg: bool = False, dataflow: bool = False,
                 types: bool = False, signatures: bool = False,
                 diagnostics: bool = False,
                 cross_language: bool = False) -> None:
        self.symbols = symbols
        self.calls = calls
        self.cfg = cfg
        self.dataflow = dataflow
        self.types = types
        self.signatures = signatures
        self.diagnostics = diagnostics
        self.cross_language = cross_language

    def has(self, capability: str) -> bool:
        return bool(getattr(self, capability, False))

    def require(self, capability: str, provider: str,
                detail: str = "") -> None:
        if not self.has(capability):
            raise CapabilityUnavailable(capability, provider, detail)

    def as_dict(self) -> dict[str, bool]:
        return {k: bool(v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: Optional[int] = None
    column: Optional[int] = None
    end_line: Optional[int] = None
    end_column: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "column": self.column,
                "end_line": self.end_line, "end_column": self.end_column}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SourceLocation":
        return SourceLocation(
            file=d.get("file", ""), line=d.get("line"),
            column=d.get("column"), end_line=d.get("end_line"),
            end_column=d.get("end_column"))


@dataclass(frozen=True)
class AnalysisFact:
    """Frozen cross-provider normalized fact (ANALYZER-MAP0 unified output).

    Entity ids are canonical when available (`node:CLASS:qname`,
    `node:FUNCTION:qname`, …); provider-native ids live in `metadata`.
    """
    fact_id: str
    fact_kind: FactKind
    semantic_kind: SemanticKind
    provider: str
    provider_version: str
    repo_id: str
    snapshot_id: Optional[str]
    truth_class: TruthClass
    execution_modality: ExecutionModality
    target_resolution: TargetResolution
    coverage: Coverage
    subject: Optional[str] = None
    target: Optional[str] = None
    candidate_targets: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    scope: Optional[str] = None
    guard: Optional[str] = None
    source_location: Optional[SourceLocation] = None
    target_location: Optional[SourceLocation] = None
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_kind": self.fact_kind.value,
            "semantic_kind": self.semantic_kind.value,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "repo_id": self.repo_id,
            "snapshot_id": self.snapshot_id,
            "truth_class": self.truth_class.value,
            "execution_modality": self.execution_modality.value,
            "target_resolution": self.target_resolution.value,
            "coverage": self.coverage.value,
            "subject": self.subject,
            "target": self.target,
            "candidate_targets": list(self.candidate_targets),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "resources": list(self.resources),
            "scope": self.scope,
            "guard": self.guard,
            "source_location": (self.source_location.to_dict()
                                if self.source_location else None),
            "target_location": (self.target_location.to_dict()
                                if self.target_location else None),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "AnalysisFact":
        return AnalysisFact(
            fact_id=d["fact_id"],
            fact_kind=FactKind(d["fact_kind"]),
            semantic_kind=SemanticKind(d.get("semantic_kind", "OTHER")),
            provider=d["provider"],
            provider_version=d["provider_version"],
            repo_id=d["repo_id"],
            snapshot_id=d.get("snapshot_id"),
            truth_class=TruthClass(d["truth_class"]),
            execution_modality=ExecutionModality(d.get(
                "execution_modality", "UNKNOWN")),
            target_resolution=TargetResolution(d.get(
                "target_resolution", "UNKNOWN")),
            coverage=Coverage(d.get("coverage", "UNKNOWN")),
            subject=d.get("subject"),
            target=d.get("target"),
            candidate_targets=list(d.get("candidate_targets", [])),
            inputs=list(d.get("inputs", [])),
            outputs=list(d.get("outputs", [])),
            resources=list(d.get("resources", [])),
            scope=d.get("scope"),
            guard=d.get("guard"),
            source_location=(SourceLocation.from_dict(d["source_location"])
                             if d.get("source_location") else None),
            target_location=(SourceLocation.from_dict(d["target_location"])
                             if d.get("target_location") else None),
            confidence=float(d.get("confidence", 0.0)),
            metadata=d.get("metadata", {}))


def ensure_contract(fact: AnalysisFact) -> AnalysisFact:
    """Contract guard: validate every enum stays in the frozen set.

    Returns the fact unchanged; unknown values raise ValueError so adapter
    bugs cannot smuggle unfreezable classes through the boundary.
    """
    FactKind(fact.fact_kind)
    SemanticKind(fact.semantic_kind)
    TruthClass(fact.truth_class)
    ExecutionModality(fact.execution_modality)
    TargetResolution(fact.target_resolution)
    Coverage(fact.coverage)
    return fact
