"""Immutable evidence-authority records independent of analyzer wire formats."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from rintel.analysis.contract import (
    Coverage,
    ExecutionModality,
    TargetResolution,
    TruthClass,
)


class AuthorityClass(str, Enum):
    CANONICAL_CANDIDATE = "CANONICAL_CANDIDATE"
    REFERENCE_EVIDENCE = "REFERENCE_EVIDENCE"
    DESIGN_ANNOTATION = "DESIGN_ANNOTATION"


class AnnotationKind(str, Enum):
    AS_IS_NOTE = "AS_IS_NOTE"
    INTERPRETATION = "INTERPRETATION"
    TO_BE_INTENT = "TO_BE_INTENT"
    RISK_NOTE = "RISK_NOTE"
    REVIEW_DECISION = "REVIEW_DECISION"


class EnvelopeValidationError(ValueError):
    """An envelope is malformed or attempts to claim Rintel-owned authority."""


@dataclass(frozen=True)
class CanonicalStateRef:
    """Immutable pointer to canonical state, never a copied mutable payload."""

    revision_id: str
    state_hash: str
    fact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.revision_id.strip() or not self.state_hash.strip():
            raise ValueError("canonical revision_id and state_hash are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "state_hash": self.state_hash,
            "fact_refs": list(self.fact_refs),
        }


def _stable_digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ClaimPayload:
    claim_id: str
    subject: str
    predicate: str
    object: Any
    source_span: Mapping[str, Any] | None
    truth_class: TruthClass
    resolution: TargetResolution
    coverage: Coverage
    execution_modality: ExecutionModality
    revision_input: str
    object_is_identity: bool = True
    confidence_if_applicable: float | None = None
    witness: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.claim_id.strip() or not self.subject.strip() \
                or not self.predicate.strip() or not self.revision_input.strip():
            raise EnvelopeValidationError(
                "claim_id, subject, predicate, and revision_input are required")

    def to_wire(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "source_span": dict(self.source_span) if self.source_span else None,
            "truth_class": self.truth_class.value,
            "resolution": self.resolution.value,
            "coverage": self.coverage.value,
            "execution_modality": self.execution_modality.value,
            "revision_input": self.revision_input,
            "object_is_identity": self.object_is_identity,
            "confidence_if_applicable": self.confidence_if_applicable,
            "witness": dict(self.witness or {}),
        }


@dataclass(frozen=True)
class AnnotationPayload:
    kind: AnnotationKind
    design_state: str
    text: str
    source: str
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.design_state.strip() or not self.text.strip() \
                or not self.source.strip():
            raise EnvelopeValidationError(
                "annotation design_state, text, and source are required")

    def to_wire(self) -> dict[str, Any]:
        return {
            "annotation_kind": self.kind.value,
            "design_state": self.design_state,
            "text": self.text,
            "source": self.source,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class EvidenceEnvelope:
    lane_id: str
    producer: str
    producer_version: str
    scope: tuple[str, ...]
    provenance: Mapping[str, Any]
    limitations: tuple[str, ...]
    claim: ClaimPayload | None = None
    annotation: AnnotationPayload | None = None

    def __post_init__(self) -> None:
        if not self.lane_id.strip() or not self.producer.strip() \
                or not self.producer_version.strip():
            raise EnvelopeValidationError(
                "lane_id, producer, and producer_version are required")
        if (self.claim is None) == (self.annotation is None):
            raise EnvelopeValidationError(
                "EvidenceEnvelope requires exactly one claim or annotation")

    @classmethod
    def for_claim(cls, *, lane_id: str, producer: str,
                  producer_version: str, scope: tuple[str, ...],
                  provenance: Mapping[str, Any], limitations: tuple[str, ...],
                  claim: ClaimPayload) -> "EvidenceEnvelope":
        return cls(lane_id, producer, producer_version, tuple(scope),
                   dict(provenance), tuple(limitations), claim=claim)

    @classmethod
    def for_annotation(cls, *, lane_id: str, producer: str,
                       producer_version: str, scope: tuple[str, ...],
                       provenance: Mapping[str, Any], limitations: tuple[str, ...],
                       annotation: AnnotationPayload) -> "EvidenceEnvelope":
        return cls(lane_id, producer, producer_version, tuple(scope),
                   dict(provenance), tuple(limitations), annotation=annotation)

    def to_wire(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "scope": list(self.scope),
            "provenance": dict(self.provenance),
            "limitations": list(self.limitations),
            "claim": self.claim.to_wire() if self.claim else None,
            "annotation": self.annotation.to_wire() if self.annotation else None,
        }

    @property
    def digest(self) -> str:
        return _stable_digest(self.to_wire())


__all__ = [
    "AnnotationKind", "AnnotationPayload", "AuthorityClass", "ClaimPayload",
    "CanonicalStateRef", "EnvelopeValidationError", "EvidenceEnvelope",
]
