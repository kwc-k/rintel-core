"""Read-only query projection over supplied evidence-authority envelopes."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from rintel.analysis.contract import (
    Coverage,
    ExecutionModality,
    TargetResolution,
    TruthClass,
)

from .bridge import CrossLanguageBridge
from .engine import DEFAULT_ENGINE
from .model import AuthorityClass, CanonicalStateRef, EvidenceEnvelope


class AnswerStatus(str, Enum):
    KNOWN_CANONICALLY = "KNOWN_CANONICALLY"
    SUPPORTED_BY_REFERENCE = "SUPPORTED_BY_REFERENCE"
    SUGGESTED_INFERRED = "SUGGESTED_INFERRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class QueryAuthorityResult:
    answer_status: AnswerStatus
    canonical_resolution: TargetResolution | None
    registry_version: str
    rule_version: str
    segments: tuple[dict[str, Any], ...]
    alignment_results: tuple[dict[str, Any], ...]
    supporting_reference: tuple[dict[str, Any], ...]
    bridges: tuple[CrossLanguageBridge, ...]
    full_chain_canonical: bool
    semantic_meaning: str | None = None
    authoritative_change_impact: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_status": self.answer_status.value,
            "canonical_resolution": (
                self.canonical_resolution.value
                if self.canonical_resolution is not None else None),
            "registry_version": self.registry_version,
            "rule_version": self.rule_version,
            "segments": [dict(item) for item in self.segments],
            "alignment_results": [dict(item) for item in self.alignment_results],
            "supporting_reference": [dict(item) for item in self.supporting_reference],
            "bridges": [item.to_dict() for item in self.bridges],
            "full_chain_canonical": self.full_chain_canonical,
            "semantic_meaning": self.semantic_meaning,
            "authoritative_change_impact": self.authoritative_change_impact,
        }


def _segment(envelope: EvidenceEnvelope) -> dict[str, Any]:
    lane = DEFAULT_ENGINE.registry.require(envelope.lane_id)
    claim = envelope.claim
    return {
        "lane_id": envelope.lane_id,
        "producer": envelope.producer,
        "producer_version": envelope.producer_version,
        "evidence_authority": lane.authority_class.value,
        "claim_id": claim.claim_id if claim else None,
        "subject": claim.subject if claim else None,
        "predicate": claim.predicate if claim else None,
        "object": claim.object if claim else None,
        "truth_class": claim.truth_class.value if claim else None,
        "resolution": claim.resolution.value if claim else None,
        "coverage": claim.coverage.value if claim else None,
        "execution_modality": claim.execution_modality.value if claim else None,
        "annotation_kind": (
            envelope.annotation.kind.value if envelope.annotation else None),
    }


def classify_evidence(
        envelopes: Iterable[EvidenceEnvelope], state: CanonicalStateRef, *,
        bridges: Iterable[CrossLanguageBridge] = ()) -> QueryAuthorityResult:
    """Classify supplied evidence only; never retrieves, admits, or publishes."""
    items = tuple(envelopes)
    bridge_items = tuple(bridges)
    segments = tuple(_segment(item) for item in items)
    canonical = [item for item in items
                 if DEFAULT_ENGINE.registry.require(item.lane_id).authority_class
                 is AuthorityClass.CANONICAL_CANDIDATE and item.claim is not None]
    references = [item for item in items
                  if DEFAULT_ENGINE.registry.require(item.lane_id).authority_class
                  is AuthorityClass.REFERENCE_EVIDENCE and item.claim is not None]
    annotations = [item for item in items if item.annotation is not None]
    canonical_resolution = canonical[0].claim.resolution if canonical else None

    if any(item.claim.resolution is TargetResolution.UNKNOWN for item in canonical):
        status = AnswerStatus.UNKNOWN
    elif canonical:
        if any(item.claim.truth_class in {TruthClass.INFERRED, TruthClass.HEURISTIC}
               for item in canonical):
            status = AnswerStatus.SUGGESTED_INFERRED
        else:
            status = AnswerStatus.KNOWN_CANONICALLY
    elif references:
        status = AnswerStatus.SUPPORTED_BY_REFERENCE
    elif annotations:
        status = AnswerStatus.SUGGESTED_INFERRED
    else:
        status = AnswerStatus.UNKNOWN

    alignment = tuple(item.to_dict() for item in DEFAULT_ENGINE.align(items, state))
    supporting = tuple(
        segment for segment in segments
        if segment["evidence_authority"] == AuthorityClass.REFERENCE_EVIDENCE.value)
    impact = any(
        item.claim is not None and item.lane_id == "ripwire_reference"
        and item.claim.predicate == "IMPACT_REACHABILITY"
        for item in items)
    full_chain_canonical = bool(canonical) and not references \
        and not annotations and not bridge_items
    return QueryAuthorityResult(
        answer_status=status,
        canonical_resolution=canonical_resolution,
        registry_version=DEFAULT_ENGINE.registry.version,
        rule_version=DEFAULT_ENGINE.rule_version,
        segments=segments,
        alignment_results=alignment,
        supporting_reference=supporting,
        bridges=bridge_items,
        full_chain_canonical=full_chain_canonical,
        semantic_meaning="TRANSITIVE_REACHABILITY_FLOOR" if impact else None,
        authoritative_change_impact=False if impact else None,
    )


__all__ = [
    "AnswerStatus", "QueryAuthorityResult", "classify_evidence",
]
