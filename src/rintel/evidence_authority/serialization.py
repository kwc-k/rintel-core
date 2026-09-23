"""Fail-closed serialization for producer/reference/design envelopes."""
from __future__ import annotations

from typing import Any, Mapping

from rintel.analysis.contract import (
    Coverage,
    ExecutionModality,
    TargetResolution,
    TruthClass,
)

from .model import (
    AnnotationKind,
    AnnotationPayload,
    ClaimPayload,
    EnvelopeValidationError,
    EvidenceEnvelope,
)


_FORBIDDEN_AUTHORITY_KEYS = frozenset({
    "authority", "authority_class", "canonical_ingestion",
    "effective_authority", "evidence_authority",
})
_ENVELOPE_KEYS = frozenset({
    "lane_id", "producer", "producer_version", "scope", "provenance",
    "limitations", "claim", "annotation",
})


def envelope_from_wire(value: Mapping[str, Any]) -> EvidenceEnvelope:
    forbidden = sorted(_FORBIDDEN_AUTHORITY_KEYS & set(value))
    if forbidden:
        raise EnvelopeValidationError(
            "producer authority fields are forbidden: " + ", ".join(forbidden))
    unknown = sorted(set(value) - _ENVELOPE_KEYS)
    if unknown:
        raise EnvelopeValidationError("unknown envelope fields: " + ", ".join(unknown))
    common = {
        "lane_id": str(value.get("lane_id", "")),
        "producer": str(value.get("producer", "")),
        "producer_version": str(value.get("producer_version", "")),
        "scope": tuple(str(item) for item in value.get("scope", ())),
        "provenance": dict(value.get("provenance") or {}),
        "limitations": tuple(str(item) for item in value.get("limitations", ())),
    }
    claim = value.get("claim")
    annotation = value.get("annotation")
    if claim is not None:
        raw = dict(claim)
        return EvidenceEnvelope.for_claim(
            **common,
            claim=ClaimPayload(
                claim_id=str(raw["claim_id"]),
                subject=str(raw["subject"]),
                predicate=str(raw["predicate"]),
                object=raw.get("object"),
                source_span=(dict(raw["source_span"])
                             if raw.get("source_span") else None),
                truth_class=TruthClass(raw["truth_class"]),
                resolution=TargetResolution(raw["resolution"]),
                coverage=Coverage(raw["coverage"]),
                execution_modality=ExecutionModality(raw["execution_modality"]),
                revision_input=str(raw["revision_input"]),
                object_is_identity=bool(raw.get("object_is_identity", True)),
                confidence_if_applicable=raw.get("confidence_if_applicable"),
                witness=(dict(raw["witness"]) if raw.get("witness") else None),
            ),
        )
    if annotation is not None:
        raw = dict(annotation)
        forbidden_annotation = {
            "truth_class", "resolution", "coverage", "execution_modality",
        } & set(raw)
        if forbidden_annotation:
            raise EnvelopeValidationError(
                "annotation cannot carry truth dimensions: "
                + ", ".join(sorted(forbidden_annotation)))
        return EvidenceEnvelope.for_annotation(
            **common,
            annotation=AnnotationPayload(
                kind=AnnotationKind(raw["annotation_kind"]),
                design_state=str(raw["design_state"]),
                text=str(raw["text"]),
                source=str(raw["source"]),
                metadata=dict(raw.get("metadata") or {}),
            ),
        )
    raise EnvelopeValidationError(
        "EvidenceEnvelope requires exactly one claim or annotation")


__all__ = ["envelope_from_wire"]
