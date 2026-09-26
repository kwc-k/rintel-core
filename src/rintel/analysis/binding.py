"""CanonicalSymbolBinding — Provider symbol → Existing Evidence identity.

Frozen principle (P0.3): **the analyzer is NEVER the identity authority.**
Existing Evidence Graph owns canonical identity (`node:KIND:qname`); this
binding module is the formal bridge and it uses the SAME deterministic
contract as FLOW0 writeback: exact (path + name) lookup, never guesses.

Statuses:
- EXACT      — exactly one canonical node matches (path + name + kind).
- AMBIGUOUS  — more than one candidate; no authority to choose.
- UNBOUND    — no candidate (unknown symbol / external / different file).

Provider-native ids stay in `provider_symbol`; the canonical id always
comes from the existing graph.  Joern/LFortran never mint product identity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class BindingStatus(str, Enum):
    EXACT = "EXACT"
    AMBIGUOUS = "AMBIGUOUS"
    UNBOUND = "UNBOUND"


#: canonical node kinds the evidence graph may bind for code symbols
BINDABLE_KINDS = ("FUNCTION", "METHOD", "CLASS", "MODULE", "SUBROUTINE")


@dataclass(frozen=True)
class CanonicalSymbolBinding:
    provider: str
    provider_symbol: str
    language: str
    file: str
    snapshot_id: Optional[str]
    binding_status: BindingStatus
    canonical_symbol_id: Optional[str] = None
    canonical_name: Optional[str] = None
    canonical_kind: Optional[str] = None
    binding_evidence: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    source_span: Optional[dict] = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_symbol": self.provider_symbol,
            "language": self.language,
            "file": self.file,
            "snapshot_id": self.snapshot_id,
            "binding_status": self.binding_status.value,
            "canonical_symbol_id": self.canonical_symbol_id,
            "canonical_name": self.canonical_name,
            "canonical_kind": self.canonical_kind,
            "binding_evidence": list(self.binding_evidence),
            "candidates": list(self.candidates),
            "source_span": self.source_span,
        }


def _short_name(qname: str) -> str:
    """Last component of a qname — canonical nodes carry short names
    (e.g. 'solver_app.main' → 'main'; 'CpuSolver.solve' → 'solve')."""
    return (qname or "").rsplit(".", 1)[-1].split("(", 1)[0].strip()


def bind_provider_symbol(
        db, repo_id: str, snapshot_id: str,
        provider: str, provider_symbol: str,
        language: str, file: str,
        kind_hint: Optional[str] = None,
        source_span: Optional[dict] = None) -> CanonicalSymbolBinding:
    """Walk the SAME deterministic contract as writeback `_find_node`:
    exact path + short name (+kind) over the existing evidence graph."""
    evidence: list[str] = []
    name = _short_name(provider_symbol)
    evidence.append(f"provider symbol '{provider_symbol}' -> short name '{name}'")
    try:
        rows = db.nodes_by_path(repo_id, snapshot_id, file)
    except Exception as exc:  # pragma: no cover - defensive
        return CanonicalSymbolBinding(
            provider, provider_symbol, language, file, snapshot_id,
            BindingStatus.UNBOUND,
            binding_evidence=[f"nodes_by_path failed: {exc}"])
    if not rows:
        evidence.append(f"no canonical nodes at path '{file}'")
        return CanonicalSymbolBinding(
            provider, provider_symbol, language, file, snapshot_id,
            BindingStatus.UNBOUND, binding_evidence=evidence)

    matches = []
    for n in rows:
        if n.get("kind") not in BINDABLE_KINDS:
            continue
        if n.get("name") != name or n.get("language") != language:
            continue
        if kind_hint and n.get("kind") != kind_hint:
            continue
        matches.append(n)
    if len(matches) == 1:
        n = matches[0]
        cid = n["id"]
        evidence.append(
            f"exact match: path '{file}' + name '{name}'"
            f" + language '{language}' + kind '{n['kind']}' -> {cid}")
        return CanonicalSymbolBinding(
            provider, provider_symbol, language, file, snapshot_id,
            BindingStatus.EXACT, canonical_symbol_id=cid,
            canonical_name=n.get("name"), canonical_kind=n.get("kind"),
            binding_evidence=evidence, source_span=source_span)
    if len(matches) > 1:
        evidence.append(
            f"{len(matches)} candidates at path '{file}' name '{name}' — "
            "no authority to choose")
        return CanonicalSymbolBinding(
            provider, provider_symbol, language, file, snapshot_id,
            BindingStatus.AMBIGUOUS, binding_evidence=evidence,
            candidates=[n["id"] for n in matches],
            source_span=source_span)
    evidence.append(f"no node with name '{name}' at path '{file}'")
    return CanonicalSymbolBinding(
        provider, provider_symbol, language, file, snapshot_id,
        BindingStatus.UNBOUND, binding_evidence=evidence,
        source_span=source_span)
