"""Canonical reconciliation for provider evidence candidates."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Callable, Iterable

from rintel.analysis.contract import Coverage, ExecutionModality, TargetResolution, TruthClass

from .contract import EvidenceCandidate
from rintel.identity import canonical_provider_entity_id

IdentityResolver = Callable[[str, EvidenceCandidate, str], str | None]


class IdentityAdmissionError(RuntimeError):
    """A candidate attempted to publish an unbound formal symbol identity."""


class ConflictDecision(str, Enum):
    EXACT = "EXACT"
    CANDIDATE_SET = "CANDIDATE_SET"
    UNKNOWN = "UNKNOWN"
    PROVIDER_CONFLICT = "PROVIDER_CONFLICT"


@dataclass(frozen=True)
class ProviderClaim:
    provider_id: str
    provider_fact_id: str
    object: Any
    truth_class: TruthClass
    coverage: Coverage
    resolution: TargetResolution
    source_span: dict[str, Any] | None = None
    witness: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_fact_id": self.provider_fact_id,
            "object": self.object,
            "truth_class": self.truth_class.value,
            "coverage": self.coverage.value,
            "resolution": self.resolution.value,
            "source_span": self.source_span,
            "witness": dict(self.witness),
        }


@dataclass(frozen=True)
class CanonicalEvidence:
    evidence_id: str
    subject: str
    predicate: str
    object: Any
    source_span: dict[str, Any] | None
    truth_class: TruthClass
    coverage: Coverage
    resolution: TargetResolution
    execution_modality: ExecutionModality
    revision_input: str
    provider_claims: tuple[ProviderClaim, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "source_span": self.source_span,
            "truth_class": self.truth_class.value,
            "coverage": self.coverage.value,
            "resolution": self.resolution.value,
            "execution_modality": self.execution_modality.value,
            "revision_input": self.revision_input,
            "provider_claims": [c.to_dict() for c in self.provider_claims],
        }


@dataclass(frozen=True)
class ProviderConflict:
    subject: str
    relation: str
    provider_claims: tuple[ProviderClaim, ...]
    canonical_decision: ConflictDecision = ConflictDecision.PROVIDER_CONFLICT
    reason: str = "providers disagree on semantic payload or truth dimensions"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "relation": self.relation,
            "provider_claims": [c.to_dict() for c in self.provider_claims],
            "canonical_decision": self.canonical_decision.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CanonicalizationResult:
    evidence: tuple[CanonicalEvidence, ...]
    conflicts: tuple[ProviderConflict, ...]
    unresolved: tuple[EvidenceCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": [e.to_dict() for e in self.evidence],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "unresolved": [u.to_dict() for u in self.unresolved],
        }


def strict_identity(value: str, candidate: EvidenceCandidate, role: str) -> str:
    """Accept only explicit Rintel canonical ids, never raw analyzer names."""
    provider_prefix = candidate.provider_id.lower().replace("_", "-") + ":"
    lowered = value.lower()
    if value == candidate.provider_fact_id or lowered.startswith(provider_prefix):
        raise ValueError("provider identity cannot become canonical identity")
    canonical_prefixes = (
        "arch:", "call_site:", "ctx:", "data:", "diagnostic:", "evidence:",
        "file:", "flow:", "loc:", "location:", "module:", "node:", "op:",
        "port:", "ref:", "repo:", "resource:", "runtime:", "stage:",
        "symbol:", "type:", "value:",
    )
    if not lowered.startswith(canonical_prefixes):
        raise ValueError("identity was not resolved by Rintel canonical authority")
    if value.startswith("node:"):
        if not (re.fullmatch(r"node:[A-Z_]+:v2:[0-9a-f]{64}", value)
                or re.fullmatch(r"node:(?:REPOSITORY|DIRECTORY|FILE|COMMIT):.+",
                                value)):
            raise IdentityAdmissionError(
                "formal node identity requires symbol-identity/v2")
        raw = candidate.witness.get("provider_local")
        if not isinstance(raw, dict):
            raise IdentityAdmissionError(
                "formal node identity lacks Rintel-resolvable provider descriptor")
        if role == "subject":
            entities = [raw.get("subject")]
        else:
            obj = raw.get("object")
            entities = ([obj["entity"]] if isinstance(obj, dict) and "entity" in obj
                        else obj.get("candidates", []) if isinstance(obj, dict)
                        else [])
        try:
            expected = {canonical_provider_entity_id(entity)
                        for entity in entities if isinstance(entity, dict)}
        except (KeyError, ValueError) as exc:
            raise IdentityAdmissionError(
                "provider descriptor cannot be bound to v2 identity") from exc
        if value not in expected:
            raise IdentityAdmissionError(
                "formal node identity does not match Rintel-resolved descriptor")
    return value


def _resolve_object(candidate: EvidenceCandidate, resolver: IdentityResolver) -> Any:
    value = candidate.object
    if not candidate.object_is_identity or value is None:
        return value
    if isinstance(value, str):
        return resolver(value, candidate, "object")
    if isinstance(value, (list, tuple)):
        resolved = [resolver(str(v), candidate, "object") for v in value]
        return [v for v in resolved if v is not None]
    raise ValueError("identity object must be a string, list, tuple, or null")


def _anchor(span: dict[str, Any] | None) -> tuple[Any, ...]:
    span = span or {}
    return (span.get("file"), span.get("start_line"), span.get("span_kind"))


def _semantic_span(span: dict[str, Any] | None) -> dict[str, Any] | None:
    if not span:
        return None
    return {key: span.get(key) for key in (
        "file", "start_line", "start_column", "end_line", "end_column",
        "span_kind", "precision")}


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _reconcile_raw_candidates(
        candidates: Iterable[EvidenceCandidate],
        resolver: IdentityResolver = strict_identity) -> CanonicalizationResult:
    """Internal raw reconciliation; callers must cross authority admission."""
    resolved: list[tuple[EvidenceCandidate, str, Any]] = []
    unresolved: list[EvidenceCandidate] = []
    for candidate in candidates:
        try:
            subject = resolver(candidate.subject, candidate, "subject")
            obj = _resolve_object(candidate, resolver)
        except (KeyError, ValueError):
            unresolved.append(candidate)
            continue
        if subject is None:
            unresolved.append(candidate)
            continue
        if candidate.object_is_identity and candidate.object is not None and obj is None:
            unresolved.append(candidate)
            continue
        if (candidate.object_is_identity
                and candidate.resolution is TargetResolution.EXACT
                and obj is None):
            unresolved.append(candidate)
            continue
        if (candidate.object_is_identity
                and candidate.resolution is TargetResolution.CANDIDATE_SET
                and not obj):
            unresolved.append(candidate)
            continue
        resolved.append((candidate, subject, obj))

    slots: dict[tuple[Any, ...], list[tuple[EvidenceCandidate, str, Any]]] = {}
    for item in resolved:
        candidate, subject, _obj = item
        slots.setdefault((subject, candidate.predicate, _anchor(candidate.source_span),
                          candidate.revision_input), []).append(item)

    evidence: list[CanonicalEvidence] = []
    conflicts: list[ProviderConflict] = []
    for (subject, predicate, _span_anchor, revision), claims in sorted(
            slots.items(), key=lambda item: _stable(item[0])):
        variants: dict[tuple[Any, ...], list[tuple[EvidenceCandidate, str, Any]]] = {}
        for item in claims:
            candidate, _subject, obj = item
            variant = (_stable(obj), candidate.truth_class.value,
                       candidate.coverage.value, candidate.resolution.value,
                       candidate.execution_modality.value,
                       _stable(_semantic_span(candidate.source_span)))
            variants.setdefault(variant, []).append(item)
        provider_claims = tuple(
            ProviderClaim(c.provider_id, c.provider_fact_id, obj,
                          c.truth_class, c.coverage, c.resolution,
                          _semantic_span(c.source_span), dict(c.witness))
            for c, _subject, obj in claims)
        if len(variants) != 1:
            conflicts.append(ProviderConflict(subject, predicate, provider_claims))
            continue
        candidate, _subject, obj = claims[0]
        semantic = {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "source_span": _semantic_span(candidate.source_span),
            "revision_input": revision,
        }
        evidence_id = "evidence:" + hashlib.sha256(
            _stable(semantic).encode()).hexdigest()
        evidence.append(CanonicalEvidence(
            evidence_id=evidence_id, subject=subject, predicate=predicate,
            object=obj, source_span=candidate.source_span,
            truth_class=candidate.truth_class, coverage=candidate.coverage,
            resolution=candidate.resolution,
            execution_modality=candidate.execution_modality,
            revision_input=revision, provider_claims=provider_claims))
    return CanonicalizationResult(tuple(evidence), tuple(conflicts),
                                  tuple(unresolved))


def _reconcile_for_differential(
        left: Iterable[EvidenceCandidate], right: Iterable[EvidenceCandidate],
        resolver: IdentityResolver = strict_identity,
) -> tuple[CanonicalizationResult, CanonicalizationResult, CanonicalizationResult]:
    """Internal audit-only comparison; it never produces a publication batch."""
    left = tuple(left)
    right = tuple(right)
    return (
        _reconcile_raw_candidates(left, resolver),
        _reconcile_raw_candidates(right, resolver),
        _reconcile_raw_candidates((*left, *right), resolver),
    )
