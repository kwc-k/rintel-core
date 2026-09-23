"""Immutable admission-to-canonical-fact publication receipt."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from typing import Any, Mapping


_SEAL = object()


@dataclass(frozen=True, init=False)
class CanonicalSupportReceipt:
    """Issued only from a sealed admission; store adapters accept this type."""

    _payload: str

    def __init__(self, *, _seal: object, payload: Mapping[str, Any]) -> None:
        if _seal is not _SEAL:
            raise TypeError("CanonicalSupportReceipt requires authority admission")
        object.__setattr__(self, "_payload", json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._payload)


def _issue_support_receipt(payload: Mapping[str, Any]) -> CanonicalSupportReceipt:
    return CanonicalSupportReceipt(_seal=_SEAL, payload=payload)


def _reuse_receipt(payload: Mapping[str, Any], revision: str) -> dict[str, Any]:
    """Explicit, revision-bound reuse of an already published admission."""
    prior = dict(payload)
    original_id = str(prior["support_receipt_id"])
    next_id = "support:" + hashlib.sha256(
        json.dumps([revision, original_id], separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **prior,
        "support_receipt_id": next_id,
        "canonical_revision": revision,
        "reuse_source_receipt_id": original_id,
        "reuse_rule_version": "canonical-support-reuse/1",
    }


__all__ = ["CanonicalSupportReceipt"]
