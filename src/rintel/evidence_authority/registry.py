"""Rintel-owned, versioned evidence-lane authority registry."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from rintel.analysis.contract import Coverage, TargetResolution, TruthClass

from .model import AuthorityClass


class UnknownLaneError(LookupError):
    """The requested lane is not present in the pinned registry."""


@dataclass(frozen=True)
class LaneDefinition:
    lane_id: str
    producer: str
    status: str
    supported_languages: tuple[str, ...]
    supported_scope: tuple[str, ...]
    authority_class: AuthorityClass
    truth_classes_may_propose: tuple[TruthClass, ...]
    resolution_capabilities: tuple[TargetResolution, ...]
    coverage_semantics: str
    canonical_ingestion: bool
    allowed_uses: tuple[str, ...]
    forbidden_uses: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "producer": self.producer,
            "status": self.status,
            "supported_languages": list(self.supported_languages),
            "supported_scope": list(self.supported_scope),
            "authority_class": self.authority_class.value,
            "truth_classes_may_propose": [v.value for v in self.truth_classes_may_propose],
            "resolution_capabilities": [v.value for v in self.resolution_capabilities],
            "coverage_semantics": self.coverage_semantics,
            "canonical_ingestion": self.canonical_ingestion,
            "allowed_uses": list(self.allowed_uses),
            "forbidden_uses": list(self.forbidden_uses),
            "limitations": list(self.limitations),
        }


class LaneRegistry:
    def __init__(self, version: str, lanes: tuple[LaneDefinition, ...]):
        if not version.strip():
            raise ValueError("registry version is required")
        values = {lane.lane_id: lane for lane in lanes}
        if len(values) != len(lanes):
            raise ValueError("duplicate lane_id")
        self.version = version
        self._lanes: Mapping[str, LaneDefinition] = MappingProxyType(values)

    @property
    def lane_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._lanes))

    def require(self, lane_id: str) -> LaneDefinition:
        try:
            return self._lanes[lane_id]
        except KeyError as exc:
            raise UnknownLaneError(
                f"UNKNOWN_LANE: {lane_id!r} is not registered under {self.version}") from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "registry_version": self.version,
            "lanes": [self._lanes[key].to_dict() for key in sorted(self._lanes)],
        }


_ALL_TRUTH = tuple(TruthClass)
_ALL_RESOLUTION = tuple(TargetResolution)
_CANONICAL_FORBIDDEN = (
    "assign_canonical_id", "publish_revision", "promote_truth_without_rule",
)
_REFERENCE_FORBIDDEN = (
    "canonical_ingestion", "truth_promotion", "resolution_promotion",
    "delete_canonical_fact", "close_unknown",
)
_DESIGN_FORBIDDEN = (
    "canonical_ingestion", "truth_promotion", "claim_observed_reality",
    "delete_canonical_fact", "close_unknown",
)


DEFAULT_REGISTRY = LaneRegistry(
    "evidence-authority-registry/1",
    (
        LaneDefinition(
            "legacy_builtin", "legacy-builtin-*", "PRODUCTION",
            ("c", "c++", "fortran", "python"), ("configured legacy lanes",),
            AuthorityClass.CANONICAL_CANDIDATE, _ALL_TRUTH, _ALL_RESOLUTION,
            "provider run coverage; absence is not a negative fact", True,
            ("canonical_candidate", "compare", "search", "audit"),
            _CANONICAL_FORBIDDEN,
            ("legacy extraction depth varies by language",),
        ),
        LaneDefinition(
            "clang_provider", "rintel-clang", "PILOT_ONLY",
            ("c", "c++"), ("FAC configured C translation units",),
            AuthorityClass.CANONICAL_CANDIDATE, _ALL_TRUTH, _ALL_RESOLUTION,
            "run-specific COMPLETE/PARTIAL/UNKNOWN from RPP", True,
            ("canonical_candidate", "compiler_semantics", "compare", "audit"),
            _CANONICAL_FORBIDDEN,
            ("AppleClang AST/SARIF interface is pilot-only",),
        ),
        LaneDefinition(
            "flang_reference", "llvm-flang", "REFERENCE_ONLY",
            ("fortran",), ("bounded FAC HLFIR witnesses",),
            AuthorityClass.REFERENCE_EVIDENCE, _ALL_TRUTH, _ALL_RESOLUTION,
            "probe-scoped; no complete dependency manifest", False,
            ("show", "search", "agent_reasoning", "manual_audit", "cross_check"),
            _REFERENCE_FORBIDDEN,
            ("not an RPP Provider", "-fc1 unstable", "structured diagnostics unavailable"),
        ),
        LaneDefinition(
            "ripwire_reference", "ripwire", "REFERENCE_ONLY",
            ("multi-language",), ("measured FAC/JPL feasibility scope",),
            AuthorityClass.REFERENCE_EVIDENCE,
            (TruthClass.HEURISTIC, TruthClass.INFERRED),
            (TargetResolution.CANDIDATE_SET, TargetResolution.UNKNOWN),
            "broad heuristic coverage; impact means transitive reachability", False,
            ("show", "search", "candidate_discovery", "manual_audit", "cross_check"),
            _REFERENCE_FORBIDDEN,
            ("not canonical", "name-based calls may be ambiguous"),
        ),
        LaneDefinition(
            "runtime_trace", "rintel-runtime-trace", "PRODUCTION_EVIDENCE",
            ("runtime",), ("observed instrumented runs",),
            AuthorityClass.CANONICAL_CANDIDATE, (TruthClass.OBSERVED,),
            (TargetResolution.EXACT, TargetResolution.UNKNOWN),
            "run-scoped PARTIAL; not observed is not impossible", True,
            ("canonical_candidate", "observed_execution", "audit"),
            (*_CANONICAL_FORBIDDEN, "promote_observed_to_static_must"),
        ),
        LaneDefinition(
            "runtime_data", "rintel-runtime-data", "PRODUCTION_EVIDENCE",
            ("runtime",), ("observed runtime data probes",),
            AuthorityClass.CANONICAL_CANDIDATE, (TruthClass.OBSERVED,),
            (TargetResolution.EXACT, TargetResolution.UNKNOWN),
            "run/probe-scoped PARTIAL; absence is not a negative fact", True,
            ("canonical_candidate", "observed_data", "audit"),
            (*_CANONICAL_FORBIDDEN, "promote_observed_to_static_must"),
        ),
        LaneDefinition(
            "human_annotation", "human", "DESIGN_ONLY_DEFAULT",
            ("all",), ("review and interpretation",),
            AuthorityClass.DESIGN_ANNOTATION, (), (),
            "not applicable; annotations are not coverage claims", False,
            ("show", "search", "agent_reasoning", "review", "design"),
            _DESIGN_FORBIDDEN,
            ("promotion requires a future explicit versioned audited rule",),
        ),
        LaneDefinition(
            "design_plane", "rintel-design", "DESIGN_ONLY",
            ("all",), ("TO-BE architecture and planned relationships",),
            AuthorityClass.DESIGN_ANNOTATION, (), (),
            "not applicable; design intent is not code coverage", False,
            ("show", "search", "design", "review"),
            _DESIGN_FORBIDDEN,
            ("planned relationship is not observed reality",),
        ),
    ),
)


__all__ = [
    "DEFAULT_REGISTRY", "LaneDefinition", "LaneRegistry", "UnknownLaneError",
]
