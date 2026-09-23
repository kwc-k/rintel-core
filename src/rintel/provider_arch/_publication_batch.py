"""Internal sealed value handed from authority admission to publication."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from .canonical import CanonicalizationResult


_BATCH_SEAL = object()


@dataclass(frozen=True, init=False)
class CanonicalPublicationBatch:
    provider: str
    provider_version: str
    input_revision: str
    _coverage_json: str
    warnings: tuple[str, ...]
    reconciliation: CanonicalizationResult
    _reconciliation_json: str
    registry_version: str
    rule_version: str
    _canonical_state_before_json: str
    _canonical_state_after_json: str
    admission_digests: tuple[str, ...]
    _support_receipts_json: tuple[str, ...]

    def __init__(self, *, _seal: object, provider: str,
                 provider_version: str, input_revision: str,
                 coverage: Mapping[str, Any], warnings: tuple[str, ...],
                 reconciliation: CanonicalizationResult,
                 registry_version: str, rule_version: str,
                 canonical_state_before: Mapping[str, Any],
                 canonical_state_after: Mapping[str, Any],
                 admission_digests: tuple[str, ...],
                 support_receipts: tuple[Mapping[str, Any], ...]) -> None:
        if _seal is not _BATCH_SEAL:
            raise TypeError(
                "CanonicalPublicationBatch can only be issued by authority engine")
        for name, value in {
            "provider": provider,
            "provider_version": provider_version,
            "input_revision": input_revision,
            "registry_version": registry_version,
            "rule_version": rule_version,
        }.items():
            if not value:
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "provider_version", provider_version)
        object.__setattr__(self, "input_revision", input_revision)
        object.__setattr__(self, "_coverage_json", json.dumps(
            dict(coverage), sort_keys=True, separators=(",", ":")))
        object.__setattr__(self, "warnings", tuple(warnings))
        object.__setattr__(self, "reconciliation", reconciliation)
        object.__setattr__(self, "_reconciliation_json", json.dumps(
            reconciliation.to_dict(), sort_keys=True, separators=(",", ":")))
        object.__setattr__(self, "registry_version", registry_version)
        object.__setattr__(self, "rule_version", rule_version)
        object.__setattr__(self, "_canonical_state_before_json", json.dumps(
            dict(canonical_state_before), sort_keys=True, separators=(",", ":")))
        object.__setattr__(self, "_canonical_state_after_json", json.dumps(
            dict(canonical_state_after), sort_keys=True, separators=(",", ":")))
        object.__setattr__(self, "admission_digests", tuple(admission_digests))
        object.__setattr__(self, "_support_receipts_json", tuple(
            json.dumps(dict(item), sort_keys=True, separators=(",", ":"))
            for item in support_receipts))

    @property
    def support_receipts(self) -> tuple[dict[str, Any], ...]:
        return tuple(json.loads(item) for item in self._support_receipts_json)

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return json.loads(self._reconciliation_json)

    @property
    def coverage(self) -> dict[str, Any]:
        return json.loads(self._coverage_json)

    @property
    def canonical_state_before(self) -> dict[str, Any]:
        return json.loads(self._canonical_state_before_json)

    @property
    def canonical_state_after(self) -> dict[str, Any]:
        return json.loads(self._canonical_state_after_json)


def _issue_publication_batch(**values: Any) -> CanonicalPublicationBatch:
    return CanonicalPublicationBatch(_seal=_BATCH_SEAL, **values)


__all__ = ["CanonicalPublicationBatch"]
