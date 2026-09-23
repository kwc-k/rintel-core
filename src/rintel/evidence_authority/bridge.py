"""Explicit cross-language bridge receipts; never identity equivalence."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class BridgeKind(str, Enum):
    BIND_C = "BIND_C"
    EXPLICIT_WRAPPER = "EXPLICIT_WRAPPER"
    COMPILER_EXTERNAL_SYMBOL = "COMPILER_EXTERNAL_SYMBOL"
    OBJECT_LINK_SYMBOL = "OBJECT_LINK_SYMBOL"
    RUNTIME_OBSERVED_CALL = "RUNTIME_OBSERVED_CALL"


class InsufficientBridgeEvidence(ValueError):
    """The proposed relationship lacks an approved auditable basis."""


_REQUIRED_PROVENANCE = {
    BridgeKind.BIND_C: frozenset({
        "bind_c_name", "fortran_symbol", "c_symbol", "source_artifact"}),
    BridgeKind.EXPLICIT_WRAPPER: frozenset({
        "wrapper_ref", "wrapped_ref", "source_artifact"}),
    BridgeKind.COMPILER_EXTERNAL_SYMBOL: frozenset({
        "compiler", "emitted_symbol", "source_artifact"}),
    BridgeKind.OBJECT_LINK_SYMBOL: frozenset({
        "object_hash", "symbol", "source_artifact"}),
    BridgeKind.RUNTIME_OBSERVED_CALL: frozenset({
        "run_id", "call_event", "source_artifact"}),
}


@dataclass(frozen=True)
class CrossLanguageBridge:
    source_ref: str
    target_ref: str
    basis: BridgeKind
    provenance: Mapping[str, Any]
    relationship: str = "ABI_BRIDGE"
    identity_merged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationship": self.relationship,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "basis": self.basis.value,
            "provenance": dict(self.provenance),
            "identity_merged": self.identity_merged,
        }


def validate_bridge(*, source_ref: str, target_ref: str,
                    basis: BridgeKind | str,
                    provenance: Mapping[str, Any]) -> CrossLanguageBridge:
    try:
        kind = basis if isinstance(basis, BridgeKind) else BridgeKind(basis)
    except ValueError as exc:
        raise InsufficientBridgeEvidence(
            f"unapproved bridge basis: {basis!r}") from exc
    if not source_ref or not target_ref or source_ref == target_ref:
        raise InsufficientBridgeEvidence(
            "bridge endpoints must be non-empty and distinct")
    missing = sorted(_REQUIRED_PROVENANCE[kind] - set(provenance))
    if missing:
        raise InsufficientBridgeEvidence(
            f"missing {kind.value} provenance: {', '.join(missing)}")
    if any(not provenance[key] for key in _REQUIRED_PROVENANCE[kind]):
        raise InsufficientBridgeEvidence(
            f"empty {kind.value} provenance values are forbidden")
    return CrossLanguageBridge(
        source_ref=source_ref,
        target_ref=target_ref,
        basis=kind,
        provenance=dict(provenance),
    )


__all__ = [
    "BridgeKind", "CrossLanguageBridge", "InsufficientBridgeEvidence",
    "validate_bridge",
]
