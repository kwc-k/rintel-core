"""Adapters from existing Rintel producers into the provider import contract."""
from __future__ import annotations

from typing import Iterable

from rintel.analysis.contract import AnalysisFact, FactKind
from rintel.source_span import make_span

from .contract import EvidenceCandidate


def _span_of(fact: AnalysisFact) -> dict | None:
    loc = fact.source_location
    if loc is None:
        return None
    precision = "EXACT" if loc.column is not None and loc.end_column is not None \
        else "LINE_ONLY"
    kind = "CALLSITE" if fact.fact_kind is FactKind.CALL else "REFERENCE"
    return make_span(loc.file, loc.line, loc.column, loc.end_line,
                     loc.end_column, kind, precision,
                     {"provider": fact.provider,
                      "provider_fact_id": fact.fact_id})


def analysis_fact_to_candidate(fact: AnalysisFact,
                               revision_input: str) -> EvidenceCandidate:
    """Joern/LFortran and future analyzers use the same import contract."""
    if fact.fact_kind is FactKind.SYMBOL:
        predicate = "DECLARES"
        obj = {"kind": fact.metadata.get("kind"),
               "name": fact.metadata.get("name") or fact.subject}
        object_is_identity = False
    else:
        predicate = fact.semantic_kind.value
        if fact.target_resolution.value == "CANDIDATE_SET":
            obj = list(fact.candidate_targets)
        else:
            obj = fact.target
        object_is_identity = fact.fact_kind in {
            FactKind.CALL, FactKind.DATA_FLOW, FactKind.CONTROL_FLOW}
    return EvidenceCandidate(
        provider_id=fact.provider, provider_fact_id=fact.fact_id,
        subject=(fact.subject or fact.scope
                 or f"{fact.provider}:unknown-subject:{fact.fact_id}"),
        predicate=predicate, object=obj,
        source_span=_span_of(fact), truth_class=fact.truth_class,
        coverage=fact.coverage, resolution=fact.target_resolution,
        revision_input=revision_input,
        execution_modality=fact.execution_modality,
        confidence_if_applicable=fact.confidence,
        witness={"analysis_fact": fact.to_dict()},
        object_is_identity=object_is_identity)


def analysis_facts_to_candidates(facts: Iterable[AnalysisFact],
                                 revision_input: str) \
        -> tuple[EvidenceCandidate, ...]:
    return tuple(analysis_fact_to_candidate(f, revision_input) for f in facts)
