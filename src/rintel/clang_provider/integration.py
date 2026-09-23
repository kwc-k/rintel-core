"""Rintel-owned reconciliation adapter for staged Clang Provider results."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable

from rintel.provider_arch import (
    CanonicalPublicationStore, EvidenceCandidate, ProviderResult,
)
from rintel.provider_arch.canonical import CanonicalizationResult
from rintel.evidence_authority import CanonicalStateRef, DEFAULT_ENGINE


PROJECTIONS = ("Architecture", "Module", "Flow", "Data", "Human Semantic")


def _semantic_signature(result: CanonicalizationResult) -> str:
    value = {
        "evidence": sorted(item.evidence_id for item in result.evidence),
        "conflicts": sorted(
            (item.subject, item.relation,
             tuple(sorted(json.dumps(claim.object, sort_keys=True, default=str)
                          for claim in item.provider_claims)))
            for item in result.conflicts
        ),
        "unresolved": sorted(
            (item.subject, item.predicate,
             json.dumps(item.object, sort_keys=True, default=str))
            for item in result.unresolved
        ),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _projection_plan(facts: Iterable[EvidenceCandidate]) -> tuple[str, ...]:
    kinds = {
        str(fact.witness.get("provider_local", {}).get("kind", ""))
        for fact in facts
    }
    dirty: set[str] = set()
    if kinds & {"CALL", "IndirectCall", "Binding", "Reference"}:
        dirty.update(("Flow", "Human Semantic"))
    if kinds & {"Function", "Declaration", "Include"}:
        dirty.update(("Architecture", "Module", "Human Semantic"))
    return tuple(item for item in PROJECTIONS if item in dirty)


@dataclass(frozen=True)
class IntegrationResult:
    current_revision: str | None
    canonical_total: int
    canonical_changed: int
    canonical_reconsidered: int
    projection_dirty: tuple[str, ...]
    projection_reused: tuple[str, ...]
    reconciliation: CanonicalizationResult


class ClangProviderIntegration:
    """Owns canonical comparison and delegates atomic publication to Rintel."""

    def __init__(self, root: str | Path):
        self.store = CanonicalPublicationStore(root)
        self.root = Path(root)

    def _state_ref(self) -> CanonicalStateRef:
        current = self.store.current_revision() or "UNPUBLISHED"
        return CanonicalStateRef(
            current, "sha256:" + hashlib.sha256(current.encode()).hexdigest())

    def _current_reconciliation(self) -> tuple[str | None, str | None]:
        revision = self.store.current_revision()
        if revision is None:
            return None, None
        path = self.store.revisions / revision / "canonical_evidence.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        signature = json.dumps({
            "evidence": sorted(item["evidence_id"] for item in value["evidence"]),
            "conflicts": sorted(
                (item["subject"], item["relation"], tuple(sorted(
                    json.dumps(claim["object"], sort_keys=True, default=str)
                    for claim in item["provider_claims"])))
                for item in value["conflicts"]),
            "unresolved": sorted(
                (item["subject"], item["predicate"],
                 json.dumps(item["object"], sort_keys=True, default=str))
                for item in value["unresolved"]),
        }, sort_keys=True, separators=(",", ":"))
        return revision, signature

    def apply(self, result: ProviderResult, *, revision: str,
              fail_before_publish: bool = False) -> IntegrationResult:
        result.require_publishable()
        batch = DEFAULT_ENGINE.prepare_publication(
            result, lane_id="clang_provider", state=self._state_ref())
        reconciliation = batch.reconciliation
        current, previous_signature = self._current_reconciliation()
        signature = _semantic_signature(reconciliation)
        total = (len(reconciliation.evidence) + len(reconciliation.conflicts)
                 + len(reconciliation.unresolved))
        if signature == previous_signature:
            return IntegrationResult(
                current_revision=current,
                canonical_total=total,
                canonical_changed=0,
                canonical_reconsidered=0,
                projection_dirty=(),
                projection_reused=PROJECTIONS,
                reconciliation=reconciliation,
            )
        dirty = _projection_plan(result.facts)
        if fail_before_publish:
            raise RuntimeError("injected failure before atomic publication")
        self.store.publish(revision, batch)
        changed_slots = {
            (fact.subject, fact.predicate,
             (fact.source_span or {}).get("file"),
             (fact.source_span or {}).get("start_line"))
            for fact in result.facts
        }
        return IntegrationResult(
            current_revision=revision,
            canonical_total=total,
            canonical_changed=total,
            canonical_reconsidered=len(changed_slots),
            projection_dirty=dirty,
            projection_reused=tuple(item for item in PROJECTIONS
                                    if item not in dirty),
            reconciliation=reconciliation,
        )


__all__ = ["ClangProviderIntegration", "IntegrationResult"]
