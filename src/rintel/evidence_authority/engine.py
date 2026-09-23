"""Rintel-owned admission and non-escalating evidence alignment."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatchcase
import hashlib
import json
from typing import Any, Iterable

from rintel.analysis.contract import (
    Coverage,
    ExecutionModality,
    TargetResolution,
    TruthClass,
)
from rintel.provider_arch.canonical import (
    CanonicalizationResult,
    _reconcile_raw_candidates,
)
from rintel.provider_arch.contract import EvidenceCandidate
from rintel.provider_arch._publication_batch import (
    CanonicalPublicationBatch,
    _issue_publication_batch,
)
from rintel.provider_arch.contract import ProviderResult

from .model import AuthorityClass, CanonicalStateRef, ClaimPayload, EvidenceEnvelope
from .registry import DEFAULT_REGISTRY, LaneDefinition, LaneRegistry
from .support import CanonicalSupportReceipt, _issue_support_receipt


RULE_VERSION = "evidence-authority-rules/1"
_ADMISSION_SEAL = object()


class AdmissionDenied(PermissionError):
    """The envelope cannot cross the canonical admission boundary."""


class AlignmentKind(str, Enum):
    CANONICAL_ADMISSION = "CanonicalAdmission"
    CORROBORATION = "Corroboration"
    PROVIDER_CONFLICT = "ProviderConflict"
    REFERENCE_DISAGREEMENT = "ReferenceDisagreement"
    CANONICAL_REFERENCE_DISCREPANCY = "CanonicalReferenceDiscrepancy"
    UNKNOWN_PRESERVED = "UnknownPreserved"


@dataclass(frozen=True, init=False)
class CanonicalAdmission:
    """Sealed receipt required before raw candidate reconciliation."""

    evidence_authority: str
    registry_version: str
    rule_version: str
    envelope_digest: str
    canonical_state_before: CanonicalStateRef
    canonical_state_after: CanonicalStateRef
    _candidate: EvidenceCandidate

    def __init__(self, *, _seal: object, evidence_authority: str,
                 registry_version: str, rule_version: str,
                 envelope_digest: str,
                 canonical_state_before: CanonicalStateRef,
                 canonical_state_after: CanonicalStateRef,
                 candidate: EvidenceCandidate) -> None:
        if _seal is not _ADMISSION_SEAL:
            raise AdmissionDenied(
                "CanonicalAdmission can only be issued by EvidenceAuthorityEngine")
        object.__setattr__(self, "evidence_authority", evidence_authority)
        object.__setattr__(self, "registry_version", registry_version)
        object.__setattr__(self, "rule_version", rule_version)
        object.__setattr__(self, "envelope_digest", envelope_digest)
        object.__setattr__(self, "canonical_state_before", canonical_state_before)
        object.__setattr__(self, "canonical_state_after", canonical_state_after)
        object.__setattr__(self, "_candidate", candidate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": AlignmentKind.CANONICAL_ADMISSION.value,
            "evidence_authority": self.evidence_authority,
            "registry_version": self.registry_version,
            "rule_version": self.rule_version,
            "envelope_digest": self.envelope_digest,
            "canonical_state_before": self.canonical_state_before.to_dict(),
            "canonical_state_after": self.canonical_state_after.to_dict(),
        }


@dataclass(frozen=True)
class AlignmentResult:
    kind: AlignmentKind
    registry_version: str
    rule_version: str
    canonical_state_before: CanonicalStateRef
    canonical_state_after: CanonicalStateRef
    evidence_authority: str
    truth_class: TruthClass | None = None
    resolution: TargetResolution | None = None
    coverage: Coverage | None = None
    execution_modality: ExecutionModality | None = None
    supporting_evidence: tuple[dict[str, Any], ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "registry_version": self.registry_version,
            "rule_version": self.rule_version,
            "canonical_state_before": self.canonical_state_before.to_dict(),
            "canonical_state_after": self.canonical_state_after.to_dict(),
            "evidence_authority": self.evidence_authority,
            "truth_class": self.truth_class.value if self.truth_class else None,
            "resolution": self.resolution.value if self.resolution else None,
            "coverage": self.coverage.value if self.coverage else None,
            "execution_modality": (
                self.execution_modality.value if self.execution_modality else None),
            "supporting_evidence": [dict(item) for item in self.supporting_evidence],
            "message": self.message,
        }


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _anchor(envelope: EvidenceEnvelope) -> tuple[Any, ...]:
    claim = envelope.claim
    if claim is None:
        return ("annotation", envelope.digest)
    span = claim.source_span or {}
    return (
        claim.subject,
        claim.predicate,
        span.get("file"),
        span.get("start_line"),
        span.get("span_kind"),
        claim.revision_input,
    )


def _variant(envelope: EvidenceEnvelope) -> tuple[Any, ...]:
    claim = envelope.claim
    if claim is None:
        return ("annotation", envelope.digest)
    return (
        _stable(claim.object),
        claim.truth_class.value,
        claim.resolution.value,
        claim.coverage.value,
        claim.execution_modality.value,
    )


def _support(envelope: EvidenceEnvelope) -> dict[str, Any]:
    claim = envelope.claim
    return {
        "lane_id": envelope.lane_id,
        "producer": envelope.producer,
        "producer_version": envelope.producer_version,
        "envelope_digest": envelope.digest,
        "claim_id": claim.claim_id if claim else None,
    }


class EvidenceAuthorityEngine:
    """Resolve Rintel-owned lane authority and issue sealed admissions."""

    def __init__(self, registry: LaneRegistry = DEFAULT_REGISTRY,
                 rule_version: str = RULE_VERSION) -> None:
        self.registry = registry
        self.rule_version = rule_version

    def _lane(self, envelope: EvidenceEnvelope) -> LaneDefinition:
        lane = self.registry.require(envelope.lane_id)
        if not fnmatchcase(envelope.producer, lane.producer):
            raise AdmissionDenied(
                f"producer {envelope.producer!r} is not registered for "
                f"lane {envelope.lane_id!r}")
        return lane

    def _validate_claim(self, envelope: EvidenceEnvelope,
                        lane: LaneDefinition) -> None:
        claim = envelope.claim
        if claim is None:
            raise AdmissionDenied("canonical admission requires a claim payload")
        if claim.truth_class not in lane.truth_classes_may_propose:
            raise AdmissionDenied(
                f"truth class {claim.truth_class.value} is forbidden for {lane.lane_id}")
        if claim.resolution not in lane.resolution_capabilities:
            raise AdmissionDenied(
                f"resolution {claim.resolution.value} is forbidden for {lane.lane_id}")
        if (lane.lane_id in {"runtime_trace", "runtime_data"}
                and claim.execution_modality is ExecutionModality.MUST):
            raise AdmissionDenied(
                "runtime observation cannot promote execution modality to static MUST")

    def admit(self, envelope: EvidenceEnvelope,
              state: CanonicalStateRef) -> CanonicalAdmission:
        lane = self._lane(envelope)
        if (lane.authority_class is not AuthorityClass.CANONICAL_CANDIDATE
                or not lane.canonical_ingestion):
            raise AdmissionDenied(
                f"lane {lane.lane_id!r} has authority "
                f"{lane.authority_class.value}; canonical admission denied")
        self._validate_claim(envelope, lane)
        claim = envelope.claim
        assert claim is not None
        candidate = EvidenceCandidate(
            provider_id=envelope.producer,
            provider_fact_id=claim.claim_id,
            subject=claim.subject,
            predicate=claim.predicate,
            object=claim.object,
            source_span=dict(claim.source_span) if claim.source_span else None,
            truth_class=claim.truth_class,
            coverage=claim.coverage,
            resolution=claim.resolution,
            revision_input=claim.revision_input,
            execution_modality=claim.execution_modality,
            confidence_if_applicable=claim.confidence_if_applicable,
            witness=dict(claim.witness or {}),
            object_is_identity=claim.object_is_identity,
        )
        return CanonicalAdmission(
            _seal=_ADMISSION_SEAL,
            evidence_authority=lane.authority_class.value,
            registry_version=self.registry.version,
            rule_version=self.rule_version,
            envelope_digest=envelope.digest,
            canonical_state_before=state,
            canonical_state_after=state,
            candidate=candidate,
        )

    def reconcile(self, admissions: Iterable[CanonicalAdmission]) \
            -> CanonicalizationResult:
        checked: list[EvidenceCandidate] = []
        for admission in admissions:
            if not isinstance(admission, CanonicalAdmission):
                raise AdmissionDenied(
                    "canonical reconciliation requires CanonicalAdmission")
            checked.append(admission._candidate)
        return _reconcile_raw_candidates(checked)

    def issue_fact_support(
            self, admission: CanonicalAdmission, envelope: EvidenceEnvelope,
            *, canonical_revision: str, canonical_fact_id: str,
            canonical_fact_kind: str, canonical_fact_type: str,
            owner_path: str, build_context_id: str,
            source_digest: str | None = None,
            source_ids: tuple[str, str] | None = None,
            reattest_source_receipt_id: str | None = None) -> CanonicalSupportReceipt:
        """Bind an admitted claim to the exact fact returned by reconciliation.

        The caller supplies the reconciled fact identity, not a producer claim.
        For an edge, endpoints must equal the admission's claim; for a node,
        its Rintel-computed node identity must equal the claim subject.
        """
        if not isinstance(admission, CanonicalAdmission) or (
                admission.envelope_digest != envelope.digest or
                admission.registry_version != self.registry.version or
                admission.rule_version != self.rule_version):
            raise AdmissionDenied("support requires matching current CanonicalAdmission")
        lane = self._lane(envelope)
        if lane.authority_class is not AuthorityClass.CANONICAL_CANDIDATE:
            raise AdmissionDenied("noncanonical lane cannot support a canonical fact")
        claim = envelope.claim
        if claim is None or canonical_fact_type not in {"node", "edge"}:
            raise AdmissionDenied("support requires an admitted node or edge claim")
        if canonical_fact_type == "node":
            if (claim.subject != canonical_fact_id or claim.predicate != "NODE"
                    or not isinstance(claim.object, dict)
                    or claim.object.get("kind") != canonical_fact_kind):
                raise AdmissionDenied("node support identity mismatch")
        else:
            if (source_ids is None or claim.subject != source_ids[0]
                    or claim.predicate != canonical_fact_kind
                    or claim.object != source_ids[1]
                    or canonical_fact_id !=
                    f"edge:{canonical_fact_kind}:{source_ids[0]}:{source_ids[1]}"):
                raise AdmissionDenied("edge support identity mismatch")
        admission_id = admission.envelope_digest
        receipt_id = "support:" + hashlib.sha256(_stable([
            canonical_revision, canonical_fact_id, admission_id]).encode()).hexdigest()
        return _issue_support_receipt({
            "schema_version": "canonical-support-receipt/1",
            "support_receipt_id": receipt_id,
            "canonical_revision": canonical_revision,
            "canonical_fact_id": canonical_fact_id,
            "canonical_fact_kind": canonical_fact_kind,
            "canonical_fact_type": canonical_fact_type,
            "admission_id": admission_id,
            "evidence_id": claim.claim_id,
            "lane_id": envelope.lane_id,
            "producer": envelope.producer,
            "producer_version": envelope.producer_version,
            "truth_class": claim.truth_class.value,
            "target_resolution": claim.resolution.value,
            "coverage": claim.coverage.value,
            "execution_modality": claim.execution_modality.value,
            "scope": list(envelope.scope),
            "build_context": build_context_id,
            "provenance": dict(envelope.provenance),
            "limitations": list(envelope.limitations),
            "source_span": dict(claim.source_span or {}),
            "source_ids": list(source_ids) if source_ids else None,
            "admitted_envelope": envelope.to_wire(),
            "source_digest": source_digest,
            "owner_path": owner_path,
            "registry_version": admission.registry_version,
            "rule_version": admission.rule_version,
            "reuse_source_receipt_id": None,
            "reattest_source_receipt_id": reattest_source_receipt_id,
            "reattest_rule_version": (
                "semantic-digest-identity-reattest/1"
                if reattest_source_receipt_id else None),
        })

    def prepare_publication(
            self, result: ProviderResult, *, lane_id: str,
            state: CanonicalStateRef,
            build_context_id: str | None = None,
            provider_config_digest: str | None = None) -> CanonicalPublicationBatch:
        """Validate a complete result and seal its canonical publication batch."""
        result.require_publishable()
        lane = self.registry.require(lane_id)
        if not fnmatchcase(result.provider, lane.producer):
            raise AdmissionDenied(
                f"producer {result.provider!r} is not registered for lane {lane_id!r}")
        if (lane.authority_class is not AuthorityClass.CANONICAL_CANDIDATE
                or not lane.canonical_ingestion):
            raise AdmissionDenied(
                f"lane {lane_id!r} cannot prepare canonical publication")
        admissions: list[CanonicalAdmission] = []
        admission_sources: dict[tuple[str, str], list[tuple[
            CanonicalAdmission, EvidenceEnvelope]]] = {}
        for fact in result.facts:
            key = (fact.provider_id, fact.provider_fact_id)
            claim = ClaimPayload(
                claim_id=fact.provider_fact_id,
                subject=fact.subject,
                predicate=fact.predicate,
                object=fact.object,
                source_span=fact.source_span,
                truth_class=fact.truth_class,
                resolution=fact.resolution,
                coverage=fact.coverage,
                execution_modality=fact.execution_modality,
                revision_input=fact.revision_input,
                object_is_identity=fact.object_is_identity,
                confidence_if_applicable=fact.confidence_if_applicable,
                witness=fact.witness,
            )
            envelope = EvidenceEnvelope.for_claim(
                lane_id=lane_id,
                producer=result.provider,
                producer_version=result.version,
                scope=((fact.source_span or {}).get("file",),)
                if (fact.source_span or {}).get("file") else (),
                provenance={
                    "provider_fact_id": fact.provider_fact_id,
                    "revision_input": fact.revision_input,
                },
                limitations=lane.limitations,
                claim=claim,
            )
            admission = self.admit(envelope, state)
            admissions.append(admission)
            admission_sources.setdefault(key, []).append((admission, envelope))
        reconciliation = self.reconcile(admissions)
        support_receipts: list[dict[str, Any]] = []
        for evidence in reconciliation.evidence:
            for claim in evidence.provider_claims:
                sources = admission_sources[
                    (claim.provider_id, claim.provider_fact_id)]
                match = next((index for index, (source, _) in enumerate(sources)
                              if source._candidate.subject == evidence.subject
                              and source._candidate.predicate == evidence.predicate
                              and _stable(source._candidate.object) == _stable(claim.object)
                              and source._candidate.truth_class == claim.truth_class
                              and source._candidate.resolution == claim.resolution
                              and source._candidate.coverage == claim.coverage
                              and all((source._candidate.source_span or {}).get(key)
                                      == (claim.source_span or {}).get(key)
                                      for key in ("file", "start_line", "start_column",
                                                  "end_line", "end_column",
                                                  "span_kind", "precision"))), None)
                if match is None:
                    raise AdmissionDenied("published support lacks matching admission")
                admission, envelope = sources.pop(match)
                candidate = admission._candidate
                support_receipts.append({
                    "schema_version": "canonical-support-receipt/1",
                    "canonical_fact_identity": evidence.evidence_id,
                    "source_admission": admission.envelope_digest,
                    "provider": result.provider,
                    "provider_version": result.version,
                    "provider_fact_id": candidate.provider_fact_id,
                    "truth_class": candidate.truth_class.value,
                    "target_resolution": candidate.resolution.value,
                    "coverage": candidate.coverage.value,
                    "execution_modality": candidate.execution_modality.value,
                    "scope": list(envelope.scope),
                    "build_context_id": build_context_id or "UNKNOWN",
                    "provider_config_digest": provider_config_digest or "UNKNOWN",
                    "provenance": dict(envelope.provenance),
                    "source_span": dict(candidate.source_span or {}),
                    "registry_version": admission.registry_version,
                    "rule_version": admission.rule_version,
                })
        return _issue_publication_batch(
            provider=result.provider,
            provider_version=result.version,
            input_revision=result.input_revision,
            coverage=result.coverage,
            warnings=result.warnings,
            reconciliation=reconciliation,
            registry_version=self.registry.version,
            rule_version=self.rule_version,
            canonical_state_before=state.to_dict(),
            canonical_state_after=state.to_dict(),
            admission_digests=tuple(item.envelope_digest for item in admissions),
            support_receipts=tuple(support_receipts),
        )

    def _result(self, kind: AlignmentKind, state: CanonicalStateRef,
                evidence_authority: str, envelopes: Iterable[EvidenceEnvelope],
                *, claim_from: EvidenceEnvelope | None = None,
                message: str = "") -> AlignmentResult:
        source = claim_from.claim if claim_from else None
        return AlignmentResult(
            kind=kind,
            registry_version=self.registry.version,
            rule_version=self.rule_version,
            canonical_state_before=state,
            canonical_state_after=state,
            evidence_authority=evidence_authority,
            truth_class=source.truth_class if source else None,
            resolution=source.resolution if source else None,
            coverage=source.coverage if source else None,
            execution_modality=source.execution_modality if source else None,
            supporting_evidence=tuple(_support(item) for item in envelopes),
            message=message,
        )

    def align(self, envelopes: Iterable[EvidenceEnvelope],
              state: CanonicalStateRef) -> tuple[AlignmentResult, ...]:
        items = tuple(envelopes)
        lanes = {item.digest: self._lane(item) for item in items}
        results: list[AlignmentResult] = []
        groups: dict[tuple[Any, ...], list[EvidenceEnvelope]] = {}
        for item in items:
            if item.claim is not None:
                groups.setdefault(_anchor(item), []).append(item)

        for group in groups.values():
            canonical = [item for item in group if lanes[item.digest].authority_class
                         is AuthorityClass.CANONICAL_CANDIDATE]
            references = [item for item in group if lanes[item.digest].authority_class
                          is AuthorityClass.REFERENCE_EVIDENCE]

            for item in canonical:
                self.admit(item, state)
                results.append(self._result(
                    AlignmentKind.CANONICAL_ADMISSION, state,
                    AuthorityClass.CANONICAL_CANDIDATE.value, (item,),
                    claim_from=item, message="registry-authorized canonical candidate"))

            canonical_variants = {_variant(item) for item in canonical}
            reference_variants = {_variant(item) for item in references}
            if len(canonical) > 1:
                if len(canonical_variants) == 1:
                    results.append(self._result(
                        AlignmentKind.CORROBORATION, state,
                        AuthorityClass.CANONICAL_CANDIDATE.value, canonical,
                        claim_from=canonical[0],
                        message="canonical providers agree; provenance added only"))
                else:
                    results.append(self._result(
                        AlignmentKind.PROVIDER_CONFLICT, state,
                        AuthorityClass.CANONICAL_CANDIDATE.value, canonical,
                        message="canonical providers disagree; no winner selected"))

            if len(references) > 1 and len(reference_variants) > 1:
                results.append(self._result(
                    AlignmentKind.REFERENCE_DISAGREEMENT, state,
                    AuthorityClass.REFERENCE_EVIDENCE.value, references,
                    message="reference lanes disagree; canonical state unchanged"))

            if canonical and references:
                canonical_unknown = any(
                    item.claim is not None
                    and item.claim.resolution is TargetResolution.UNKNOWN
                    for item in canonical)
                same = bool(canonical_variants & reference_variants)
                if canonical_unknown:
                    results.append(self._result(
                        AlignmentKind.UNKNOWN_PRESERVED, state, "MIXED", references,
                        claim_from=canonical[0],
                        message="reference support cannot close canonical UNKNOWN"))
                elif same:
                    results.append(self._result(
                        AlignmentKind.CORROBORATION, state, "MIXED",
                        (*canonical, *references), claim_from=canonical[0],
                        message="reference corroborates without truth promotion"))
                else:
                    results.append(self._result(
                        AlignmentKind.CANONICAL_REFERENCE_DISCREPANCY, state,
                        "MIXED", (*canonical, *references),
                        message="canonical and reference evidence disagree"))

        return tuple(results)


DEFAULT_ENGINE = EvidenceAuthorityEngine()


__all__ = [
    "AdmissionDenied", "AlignmentKind", "AlignmentResult",
    "CanonicalAdmission", "DEFAULT_ENGINE", "EvidenceAuthorityEngine",
    "RULE_VERSION",
]
