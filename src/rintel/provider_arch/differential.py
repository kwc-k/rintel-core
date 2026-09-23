"""Provider differential comparison over canonicalized semantic evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .canonical import IdentityResolver, _reconcile_for_differential, strict_identity
from .contract import EvidenceCandidate


@dataclass(frozen=True)
class ProviderDiff:
    left_provider: str
    right_provider: str
    shared: tuple[str, ...]
    only_left: tuple[str, ...]
    only_right: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]
    unresolved_left: int
    unresolved_right: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_provider": self.left_provider,
            "right_provider": self.right_provider,
            "shared": list(self.shared),
            "only_left": list(self.only_left),
            "only_right": list(self.only_right),
            "conflicts": list(self.conflicts),
            "unresolved_left": self.unresolved_left,
            "unresolved_right": self.unresolved_right,
            "absence_semantics": "ABSENCE_OF_FACT_NOT_NEGATIVE_FACT",
        }


def provider_diff(left: Iterable[EvidenceCandidate],
                  right: Iterable[EvidenceCandidate], *,
                  resolver: IdentityResolver = strict_identity) -> ProviderDiff:
    left = tuple(left)
    right = tuple(right)
    lres, rres, combined = _reconcile_for_differential(left, right, resolver)
    lmap = {item.evidence_id: item for item in lres.evidence}
    rmap = {item.evidence_id: item for item in rres.evidence}
    shared = tuple(sorted(lmap.keys() & rmap.keys()))
    only_left = tuple(sorted(lmap.keys() - rmap.keys()))
    only_right = tuple(sorted(rmap.keys() - lmap.keys()))
    conflicts = tuple(c.to_dict() for c in combined.conflicts)
    return ProviderDiff(
        left_provider=left[0].provider_id if left else "UNKNOWN",
        right_provider=right[0].provider_id if right else "UNKNOWN",
        shared=shared, only_left=only_left, only_right=only_right,
        conflicts=conflicts, unresolved_left=len(lres.unresolved),
        unresolved_right=len(rres.unresolved))
