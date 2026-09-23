"""SOFTWARE-LVS1 model: diff schema + overall status precedence.

Frozen statuses (§4): MATCH / MISMATCH / STALE / UNBOUND / UNKNOWN, plus
the two diff-item-only statuses CODE_ONLY / DESIGN_ONLY (spec §10/§11;
the final object status is derived from the frozen set).

Overall precedence (§19, locked by tests):
    any deterministic contradiction     -> MISMATCH
    else any STALE                      -> STALE
    else any UNBOUND                    -> UNBOUND
    else any UNKNOWN                    -> UNKNOWN
    else                                -> MATCH
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LvsStatus(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    STALE = "STALE"
    UNBOUND = "UNBOUND"
    UNKNOWN = "UNKNOWN"
    CODE_ONLY = "CODE_ONLY"
    DESIGN_ONLY = "DESIGN_ONLY"
    BOUNDARY_EQUIVALENT = "BOUNDARY_EQUIVALENT"   # composite contract view


DIFF_KINDS = (
    "block", "port", "call", "data", "state",
    "resource", "control", "timing", "boundary",
)


@dataclass
class LvsDiff:
    kind: str                       # one of DIFF_KINDS
    status: LvsStatus
    design_object: str
    code_object: str | None = None
    message: str = ""
    why: str = ""
    truth_class: str = "UNKNOWN"
    coverage: str = "UNKNOWN"
    design_witness: dict | None = None
    code_witness: dict | None = None
    source_location: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": self.status.value,
            "design_object": self.design_object,
            "code_object": self.code_object,
            "message": self.message,
            "why": self.why,
            "truth_class": self.truth_class,
            "coverage": self.coverage,
            "design_witness": self.design_witness,
            "code_witness": self.code_witness,
            "source_location": self.source_location,
        }


def overall_status(diffs: list[LvsDiff]) -> LvsStatus:
    """Deterministic precedence (§19, spec-required)."""
    if any(d.status is LvsStatus.MISMATCH for d in diffs):
        return LvsStatus.MISMATCH
    if any(d.status is LvsStatus.STALE for d in diffs):
        return LvsStatus.STALE
    if any(d.status is LvsStatus.UNBOUND for d in diffs):
        return LvsStatus.UNBOUND
    if any(d.status is LvsStatus.UNKNOWN for d in diffs):
        return LvsStatus.UNKNOWN
    return LvsStatus.MATCH


@dataclass
class LvsResult:
    design_snapshot: str | None
    code_snapshot: str | None
    design_summary: dict[str, Any] = field(default_factory=dict)
    code_summary: dict[str, Any] = field(default_factory=dict)
    diffs: list[LvsDiff] = field(default_factory=list)

    def sections(self) -> dict[str, list[LvsDiff]]:
        out: dict[str, list[LvsDiff]] = {k: [] for k in DIFF_KINDS}
        for d in self.diffs:
            out.setdefault(d.kind, []).append(d)
        return out

    @property
    def overall_status(self) -> LvsStatus:
        return overall_status(self.diffs)

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.diffs:
            out[d.status.value] = out.get(d.status.value, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        sections = self.sections()
        return {
            "overall_status": self.overall_status.value,
            "design_snapshot": self.design_snapshot,
            "code_snapshot": self.code_snapshot,
            "design_summary": self.design_summary,
            "code_summary": self.code_summary,
            "by_status": self.by_status(),
            "block_diffs": [d.to_dict() for d in sections["block"]],
            "port_diffs": [d.to_dict() for d in sections["port"]],
            "call_diffs": [d.to_dict() for d in sections["call"]],
            "data_diffs": [d.to_dict() for d in sections["data"]],
            "state_diffs": [d.to_dict() for d in sections["state"]],
            "resource_diffs": [d.to_dict() for d in sections["resource"]],
            "control_diffs": [d.to_dict() for d in sections["control"]],
            "timing_diffs": [d.to_dict() for d in sections["timing"]],
            "boundary_diffs": [d.to_dict() for d in sections["boundary"]],
        }
