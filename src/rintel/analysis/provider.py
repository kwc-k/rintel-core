"""Frozen AnalysisProvider interface (FLOW1-ANALYZER0 §2 / ANALYZER-MAP0).

Capability-first: callers check `capabilities().require(...)` (or `has`)
before invoking an operation; providers whose capability is missing raise
CapabilityUnavailable instead of guessing (frozen rule).  All results are
AnalysisFact — never provider-native node shapes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable, Optional

from .contract import (AnalysisFact, CapabilityUnavailable,
                       ProviderCapabilities, SourceLocation)


class Scope:
    """Analysis scope: a repo+snapshot slice, optionally narrowed to a
    symbol path / function / file."""

    def __init__(self, repo_id: str, snapshot_id: Optional[str],
                 symbol: Optional[str] = None,
                 file: Optional[str] = None,
                 language: Optional[str] = None,
                 **extra: Any):
        self.repo_id = repo_id
        self.snapshot_id = snapshot_id
        self.symbol = symbol
        self.file = file
        self.language = language
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        return {"repo_id": self.repo_id, "snapshot_id": self.snapshot_id,
                "symbol": self.symbol, "file": self.file,
                "language": self.language, **self.extra}


class AnalysisProvider(ABC):
    """Uniform provider contract (frozen).

    Implementations: JoernProvider, FraunhoferCPGProvider, LFortranProvider,
    NativeSemanticProvider.  All results are AnalysisFact.
    """

    provider_id: str = "?"
    provider_version: str = "?"

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Declared capabilities; callers MUST check before invoking."""

    @abstractmethod
    def symbols(self, scope: Scope) -> Iterable[AnalysisFact]:
        """SYMBOL facts for the scope."""

    @abstractmethod
    def calls(self, scope: Scope) -> Iterable[AnalysisFact]:
        """CALL facts (subject=caller, target=callee when resolved)."""

    @abstractmethod
    def control_flow(self, scope: Scope) -> Iterable[AnalysisFact]:
        """CONTROL_FLOW facts (semantic_kind: BRANCH/LOOP/RETURN)."""

    @abstractmethod
    def data_flow(self, scope: Scope) -> Iterable[AnalysisFact]:
        """DATA_FLOW facts. Conservative may-flow MUST be marked
        truth_class=INFERRED (frozen rule); verified direct flows may be
        OBSERVED/RESOLVED."""

    @abstractmethod
    def signature(self, scope: Scope) -> Iterable[AnalysisFact]:
        """SIGNATURE facts (params/returns/ports semantics)."""

    @abstractmethod
    def type_of(self, scope: Scope) -> Iterable[AnalysisFact]:
        """TYPE facts."""

    @abstractmethod
    def diagnostics(self, scope: Scope) -> Iterable[AnalysisFact]:
        """DIAGNOSTIC facts."""

    @abstractmethod
    def source_location(self, entity: str) -> Optional[SourceLocation]:
        """Best-effort source location for a canonical entity id."""

    def _require(self, cap: str) -> None:
        self.capabilities().require(cap, self.provider_id)

    def __init_subclass__(cls, **kwargs):
        """Auto-guard every analyzer operation by its declared capability.

        A subclass that implements `calls` without declaring the `calls`
        capability will raise CapabilityUnavailable on invocation — the
        'never guess a result' rule is enforced structurally, not by
        convention.
        """
        super().__init_subclass__(**kwargs)
        cap_of = {"symbols": "symbols", "calls": "calls",
                  "control_flow": "cfg", "data_flow": "dataflow",
                  "signature": "signatures", "type_of": "types",
                  "diagnostics": "diagnostics"}
        for meth, cap in cap_of.items():
            fn = cls.__dict__.get(meth)
            if fn is None or getattr(fn, "_cap_guarded", False):
                continue

            def _guard(original, capability):
                def _guarded(self, *args, **kwargs):
                    self.capabilities().require(capability, self.provider_id)
                    return original(self, *args, **kwargs)
                _guarded._cap_guarded = True
                return _guarded
            setattr(cls, meth, _guard(fn, cap))
