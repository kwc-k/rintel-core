"""Provider-neutral import contract for the Rintel evidence kernel.

Providers observe programs.  This module describes those observations without
granting a provider authority over canonical identity or publication.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping

from rintel.analysis.contract import (
    Coverage,
    ExecutionModality,
    TargetResolution,
    TruthClass,
)


class ProviderKind(str, Enum):
    SOURCE_FRONTEND = "SOURCE_FRONTEND"
    STATIC_ANALYZER = "STATIC_ANALYZER"
    COMPILER_IR = "COMPILER_IR"
    RUNTIME_TRACER = "RUNTIME_TRACER"
    RUNTIME_DATA = "RUNTIME_DATA"
    PROFILER = "PROFILER"
    DEBUG_INFO = "DEBUG_INFO"
    TEST_RUNNER = "TEST_RUNNER"
    DIAGNOSTIC = "DIAGNOSTIC"
    GRAPH_LAYOUT = "GRAPH_LAYOUT"
    AGENT_HOST = "AGENT_HOST"


class ProviderHealth(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class ProviderCapability:
    provider_id: str
    provider_kind: ProviderKind
    provider_version: str
    languages: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    identity_scope: str = "EXTERNAL_ONLY"
    truth_class: TruthClass = TruthClass.OBSERVED
    coverage_semantics: str = "UNKNOWN"
    source_span_precision: tuple[str, ...] = ("UNKNOWN",)
    deterministic: bool = False
    incremental: bool = False
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must be non-empty")
        if not self.provider_version.strip():
            raise ValueError("provider_version must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["provider_kind"] = self.provider_kind.value
        data["truth_class"] = self.truth_class.value
        for key in ("languages", "capabilities", "source_span_precision",
                    "limitations"):
            data[key] = list(data[key])
        return data


@dataclass(frozen=True)
class ProviderRequest:
    repo_id: str
    revision_input: str
    root: str
    paths: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    build_context: Mapping[str, Any] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)

    def cache_key(self, capability: ProviderCapability) -> str:
        """Cache identity includes provider, config, revision and build input."""
        payload = {
            "provider_id": capability.provider_id,
            "provider_version": capability.provider_version,
            "provider_kind": capability.provider_kind.value,
            "revision_input": self.revision_input,
            "paths": list(self.paths),
            "languages": list(self.languages),
            "build_context": dict(self.build_context),
            "config": dict(self.config),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class EvidenceCandidate:
    provider_id: str
    provider_fact_id: str
    subject: str
    predicate: str
    object: Any
    source_span: dict[str, Any] | None
    truth_class: TruthClass
    coverage: Coverage
    resolution: TargetResolution
    revision_input: str
    execution_modality: ExecutionModality = ExecutionModality.UNKNOWN
    confidence_if_applicable: float | None = None
    witness: Mapping[str, Any] = field(default_factory=dict)
    object_is_identity: bool = True

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_fact_id:
            raise ValueError("provider and provider_fact_id are required")
        if not self.subject or not self.predicate:
            raise ValueError("subject and predicate are required")
        if not self.revision_input:
            raise ValueError("revision_input is required")
        if self.confidence_if_applicable is not None and not (
                0.0 <= self.confidence_if_applicable <= 1.0):
            raise ValueError("confidence_if_applicable must be in [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_fact_id": self.provider_fact_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "source_span": self.source_span,
            "truth_class": self.truth_class.value,
            "coverage": self.coverage.value,
            "resolution": self.resolution.value,
            "revision_input": self.revision_input,
            "execution_modality": self.execution_modality.value,
            "confidence_if_applicable": self.confidence_if_applicable,
            "witness": dict(self.witness),
            "object_is_identity": self.object_is_identity,
        }


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    version: str
    input_revision: str
    artifacts: tuple[Mapping[str, Any], ...] = ()
    facts: tuple[EvidenceCandidate, ...] = ()
    coverage: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    duration: float = 0.0

    @property
    def publishable(self) -> bool:
        return not self.errors and all(
            fact.provider_id == self.provider
            and fact.revision_input == self.input_revision
            for fact in self.facts)

    def require_publishable(self) -> None:
        if self.errors:
            raise ValueError("provider result has errors: " + "; ".join(self.errors))
        bad = [f.provider_fact_id for f in self.facts
               if f.provider_id != self.provider
               or f.revision_input != self.input_revision]
        if bad:
            raise ValueError(f"provider result fact mismatch: {bad[:3]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "version": self.version,
            "input_revision": self.input_revision,
            "artifacts": [dict(a) for a in self.artifacts],
            "facts": [f.to_dict() for f in self.facts],
            "coverage": dict(self.coverage),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "duration": self.duration,
        }


class EvidenceProvider(ABC):
    """Small provider seam; implementations keep raw formats private."""

    @abstractmethod
    def capabilities(self) -> ProviderCapability:
        raise NotImplementedError

    @abstractmethod
    def analyze(self, request: ProviderRequest) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw: Any, request: ProviderRequest) \
            -> Iterable[EvidenceCandidate]:
        raise NotImplementedError
