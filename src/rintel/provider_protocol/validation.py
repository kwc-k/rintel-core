"""Strict validation for untrusted RPP JSON Lines messages."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .model import PROTOCOL_VERSION


class ProtocolError(RuntimeError):
    pass


_FORBIDDEN_AUTHORITY_FIELDS = frozenset({
    "canonical_id", "truth_class", "resolution", "delete_canonical_fact",
    "publish_revision", "canonical_revision", "canonical_truth",
})

_MESSAGE_FIELDS = {
    "hello": {"protocol_version", "type", "host", "authority"},
    "hello_ack": {"protocol_version", "type", "process_id", "advertisement"},
    "analyze": {"protocol_version", "type", "contract"},
    "analysis_started": {"protocol_version", "type", "analysis_id"},
    "fact_batch": {"protocol_version", "type", "analysis_id", "sequence", "facts"},
    "analysis_complete": {
        "protocol_version", "type", "analysis_id", "status", "batch_count",
        "fact_count", "coverage", "incremental_result",
    },
    "cancel": {"protocol_version", "type", "analysis_id"},
    "cancelled": {"protocol_version", "type", "analysis_id"},
    "shutdown": {"protocol_version", "type"},
    "shutdown_ack": {"protocol_version", "type"},
    "protocol_error": {"protocol_version", "type", "code", "message"},
}
_FACT_FIELDS = {
    "provider_fact_id", "kind", "subject", "predicate", "object",
    "source_span", "claim",
}
_ENTITY_FIELDS = {"local_id", "kind", "name", "qualified_name", "path", "language"}
_SPAN_FIELDS = {
    "file", "start_line", "start_column", "end_line", "end_column",
    "span_kind", "precision",
}
_CLAIM_FIELDS = {"observation_basis", "execution_modality"}


def load_protocol_schema() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("rpp-v1.schema.json").read_text())


def _authority_scan(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = _FORBIDDEN_AUTHORITY_FIELDS & value.keys()
        if forbidden:
            raise ProtocolError(
                "provider authority field is forbidden: " + sorted(forbidden)[0])
        for child in value.values():
            _authority_scan(child)
    elif isinstance(value, list):
        for child in value:
            _authority_scan(child)


def _exact_fields(value: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ProtocolError(f"unknown {name} field: {sorted(unknown)[0]}")


def _required(value: Mapping[str, Any], required: set[str], name: str) -> None:
    missing = required - value.keys()
    if missing:
        raise ProtocolError(f"missing {name} field: {sorted(missing)[0]}")


def _validate_entity(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ProtocolError("local entity must be an object")
    _exact_fields(value, _ENTITY_FIELDS, "local entity")
    _required(value, _ENTITY_FIELDS, "local entity")
    if not all(isinstance(value[key], str) for key in _ENTITY_FIELDS):
        raise ProtocolError("local entity fields must be strings")
    if not value["local_id"] or not value["kind"] or not value["name"] \
            or not value["qualified_name"]:
        raise ProtocolError("local entity identity fields must be non-empty")


def _validate_fact(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ProtocolError("candidate fact must be an object")
    _exact_fields(value, _FACT_FIELDS, "candidate fact")
    _required(value, _FACT_FIELDS, "candidate fact")
    _validate_entity(value["subject"])
    obj = value["object"]
    if not isinstance(obj, Mapping) or len(obj) != 1:
        raise ProtocolError("candidate object must have exactly one variant")
    variant = next(iter(obj))
    if variant == "entity":
        _validate_entity(obj[variant])
    elif variant == "candidates":
        if not isinstance(obj[variant], list):
            raise ProtocolError("candidate set must be an array")
        for entity in obj[variant]:
            _validate_entity(entity)
    elif variant == "none":
        if obj[variant] is not True:
            raise ProtocolError("none variant must be true")
    elif variant != "literal":
        raise ProtocolError(f"unknown candidate object variant: {variant}")
    span = value["source_span"]
    if not isinstance(span, Mapping):
        raise ProtocolError("source span must be an object")
    _exact_fields(span, _SPAN_FIELDS, "source span")
    _required(span, _SPAN_FIELDS, "source span")
    claim = value["claim"]
    if not isinstance(claim, Mapping):
        raise ProtocolError("claim must be an object")
    _exact_fields(claim, _CLAIM_FIELDS, "claim")
    _required(claim, _CLAIM_FIELDS, "claim")
    if claim["observation_basis"] not in {"DIRECT", "DERIVED", "HEURISTIC"}:
        raise ProtocolError("invalid observation basis")
    if claim["execution_modality"] not in {"MUST", "MAY", "UNKNOWN"}:
        raise ProtocolError("invalid execution modality")


def validate_message(message: Any, *, expected_type: str | None = None) -> None:
    if not isinstance(message, Mapping):
        raise ProtocolError("protocol message must be an object")
    _authority_scan(message)
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in _MESSAGE_FIELDS:
        raise ProtocolError("unknown protocol message type")
    if expected_type is not None and message_type != expected_type:
        raise ProtocolError(f"expected {expected_type}, received {message_type}")
    if message.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    allowed = _MESSAGE_FIELDS[message_type]
    _exact_fields(message, allowed, "message")
    _required(message, allowed, "message")
    if message_type == "fact_batch":
        if not isinstance(message["sequence"], int) or message["sequence"] < 0:
            raise ProtocolError("fact batch sequence must be a non-negative integer")
        facts = message["facts"]
        if not isinstance(facts, list) or not 1 <= len(facts) <= 1000:
            raise ProtocolError("fact batch must contain 1..1000 facts")
        for fact in facts:
            _validate_fact(fact)


__all__ = ["ProtocolError", "load_protocol_schema", "validate_message"]
