"""Sealed, bounded absence authority; unrelated to positive-fact support."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


RULE_VERSION = "coverage-authority/direct-static-call/1"
ISSUER_ID = "rintel-clang-direct-call-issuer/1"
_SEAL = object()


def digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, init=False)
class CoverageCertificate:
    """Only the bounded production issuer can create this store input."""

    _payload: str

    def __init__(self, *, _seal: object, payload: Mapping[str, Any]) -> None:
        if _seal is not _SEAL:
            raise TypeError("CoverageCertificate requires production issuer")
        object.__setattr__(self, "_payload", json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._payload)


def _issue(payload: Mapping[str, Any]) -> CoverageCertificate:
    value = dict(payload)
    value["receipt_identity"] = digest(value)
    value["id"] = "coverage-" + value["receipt_identity"].split(":", 1)[1][:32]
    return CoverageCertificate(_seal=_SEAL, payload=value)


CAPABILITY_REGISTRY = (
    {"analyzer": "legacy/tree-sitter", "relation": "CALLS",
     "scope": "file or repository", "maximum": "PARTIAL",
     "reason": "Shallow extraction is not a completeness proof."},
    {"analyzer": "rintel-clang", "relation": "DIRECT_STATIC_CALL",
     "scope": "EXACT_TU_FUNCTION_BODY", "language": "c", "maximum": "COMPLETE",
     "reason": "Only a validated exact Clang TU and fully audited direct-call body may qualify."},
    {"analyzer": "rintel-clang", "relation": "DIRECT_STATIC_CALL",
     "scope": "EXACT_TU_FUNCTION_BODY", "language": "c++", "maximum": "PARTIAL",
     "reason": "C++ member, virtual and overload dispatch are outside this issuer."},
    {"analyzer": "runtime_trace", "relation": "OBSERVED_CALL",
     "scope": "observed run", "maximum": "PARTIAL",
     "reason": "Unobserved execution is not a negative static fact."},
    {"analyzer": "flang_reference", "relation": "CALL",
     "scope": "bounded reference", "maximum": "UNKNOWN",
     "reason": "REFERENCE_ONLY; no production issuer."},
    {"analyzer": "ripwire_reference", "relation": "CALL",
     "scope": "broad reference", "maximum": "UNKNOWN",
     "reason": "REFERENCE_ONLY heuristic call graph; no production issuer."},
)

CAPABILITY_REGISTRY_VERSION = digest(CAPABILITY_REGISTRY)


__all__ = ["CAPABILITY_REGISTRY", "CAPABILITY_REGISTRY_VERSION",
           "CoverageCertificate", "ISSUER_ID",
           "RULE_VERSION"]
