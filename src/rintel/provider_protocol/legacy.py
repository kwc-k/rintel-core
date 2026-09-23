"""Rintel-owned Adapter for bounded legacy FAC compatibility evidence."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping


def _span(raw: Mapping[str, Any]) -> dict[str, Any]:
    span = raw["source_span"]
    return {key: span.get(key) for key in (
        "file", "start_line", "start_column", "end_line", "end_column",
        "span_kind", "precision",
    )}


def _tail(identity: str) -> str:
    return identity.rsplit("::", 1)[-1]


def _entity(local_seed: str, *, kind: str, name: str,
            qname: str, path: str, language: str) -> dict[str, str]:
    local = hashlib.sha256(local_seed.encode()).hexdigest()[:20]
    return {
        "local_id": f"legacy-local:{local}",
        "kind": kind,
        "name": name,
        "qualified_name": qname,
        "path": path,
        "language": language,
    }


class LegacyCandidateAdapter:
    """Convert selected legacy kernel records into provider-local RPP facts."""

    def __init__(self, *, provider_id: str, provider_version: str):
        self.provider_id = provider_id
        self.provider_version = provider_version

    def bounded_fac_facts(self, kernel: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        symbol = kernel["symbols"][0]
        reference = kernel["references"][0]
        call = next(item for item in kernel["operations"] if item["kind"] == "CALL")
        binding = kernel["bindings"][0]
        return (
            self._function(symbol),
            self._reference(reference),
            self._call(call),
            self._binding(binding),
        )

    def _function(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        name = raw["name"]
        return {
            "provider_fact_id": f"legacy:function:{raw['symbol_id']}",
            "kind": "Function",
            "subject": _entity(
                raw["symbol_id"], kind="FUNCTION", name=name, qname=name,
                path=raw["owner"], language=raw["language"],
            ),
            "predicate": "DECLARES",
            "object": {"literal": {"kind": "FUNCTION", "name": name}},
            "source_span": _span(raw),
            "claim": {"observation_basis": "DIRECT", "execution_modality": "UNKNOWN"},
        }

    def _reference(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        target = raw["symbol_id"]
        name = raw["text"]
        path = raw["source_span"]["file"]
        return {
            "provider_fact_id": f"legacy:reference:{raw['reference_id']}",
            "kind": "Reference",
            "subject": _entity(
                raw["context_id"], kind="CONTEXT", name=raw["context_id"],
                qname=raw["context_id"], path=path, language=raw["language"],
            ),
            "predicate": "REFERENCES",
            "object": {"entity": _entity(
                target, kind="FUNCTION", name=name, qname=name,
                path=path, language=raw["language"],
            )},
            "source_span": _span(raw),
            "claim": {"observation_basis": "DIRECT", "execution_modality": "UNKNOWN"},
        }

    def _call(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        actor = _tail(raw["actor"])
        callee = _tail(raw["callee_entry"])
        path = raw["source_span"]["file"]
        return {
            "provider_fact_id": f"legacy:call:{raw['operation_id']}",
            "kind": "CALL",
            "subject": _entity(
                raw["actor"], kind="FUNCTION", name=actor, qname=actor,
                path=path, language=raw["language"],
            ),
            "predicate": "CALL",
            "object": {"entity": _entity(
                raw["callee_entry"], kind="FUNCTION", name=callee, qname=callee,
                path=path, language=raw["language"],
            )},
            "source_span": _span(raw),
            "claim": {"observation_basis": "DIRECT", "execution_modality": "MUST"},
        }

    def _binding(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        formal = _tail(raw["formal_symbol_id"])
        path = raw["source_span"]["file"]
        return {
            "provider_fact_id": f"legacy:binding:{raw['binding_id']}",
            "kind": "Binding",
            "subject": _entity(
                raw["call_operation_id"], kind="OPERATION",
                name=raw["call_operation_id"], qname=raw["call_operation_id"],
                path=path, language="FORTRAN77",
            ),
            "predicate": "BINDS_TO",
            "object": {"literal": {
                "actual": raw["actual"], "formal": formal,
                "position": raw["position"], "keyword": raw["keyword"],
            }},
            "source_span": _span(raw),
            "claim": {"observation_basis": "DIRECT", "execution_modality": "UNKNOWN"},
        }


__all__ = ["LegacyCandidateAdapter"]
