"""SYNTHESIS0 model: plan / patch / result schema + statuses (spec §8/§12/§28)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SynthesisStatus(str, Enum):
    NO_CHANGES = "NO_CHANGES"
    PLAN_READY = "PLAN_READY"
    BLOCKED = "BLOCKED"
    PATCH_READY = "PATCH_READY"
    APPLIED = "APPLIED"
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    PLAN_ONLY = "PLAN_ONLY"        # unsupported transform: no patch


CHANGE_TYPES = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")


@dataclass
class PlanChange:
    change_id: str                       # "C1"...
    design_diff: dict                    # {kind, design_object, detail, as_is, to_be}
    synthesis_type: str                  # S1..S8
    supported: bool = True
    affected_symbols: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    uncertainty: dict[str, Any] = field(default_factory=dict)
    implementation_steps: list[str] = field(default_factory=list)
    witnesses: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "change_id": self.change_id,
            "design_diff": self.design_diff,
            "synthesis_type": self.synthesis_type,
            "supported": self.supported,
            "affected_symbols": self.affected_symbols,
            "affected_files": self.affected_files,
            "dependencies": self.dependencies,
            "uncertainty": self.uncertainty,
            "implementation_steps": self.implementation_steps,
            "witnesses": self.witnesses,
            "reason": self.reason,
        }


@dataclass
class FileEdit:
    kind: str                            # insert | replace | delete
    at_line: int | None = None
    text: str = ""
    old_text: str = ""
    provenance: dict = field(default_factory=dict)   # §27

    def to_dict(self) -> dict:
        return {"kind": self.kind, "at_line": self.at_line,
                "text": self.text, "old_text": self.old_text,
                "provenance": self.provenance}


@dataclass
class FilePatch:
    path: str
    operation: str                       # CREATE | MODIFY
    before_hash: str | None = None
    edits: list[FileEdit] = field(default_factory=list)
    reason: str = ""
    design_change_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"path": self.path, "operation": self.operation,
                "before_hash": self.before_hash,
                "edits": [e.to_dict() for e in self.edits],
                "reason": self.reason,
                "design_change_ids": self.design_change_ids}


@dataclass
class SynthesisPlan:
    design_snapshot: str | None
    code_snapshot: str | None
    target_snapshot: str | None = None
    changes: list[PlanChange] = field(default_factory=list)
    file_plan: list[dict] = field(default_factory=list)
    risk_summary: list[str] = field(default_factory=list)
    expected_lvs_delta: list[str] = field(default_factory=list)
    pre_drc: dict = field(default_factory=dict)
    pre_lvs: dict = field(default_factory=dict)
    status: SynthesisStatus = SynthesisStatus.PLAN_READY

    @property
    def has_unsupported(self) -> bool:
        return any(not c.supported for c in self.changes)

    @property
    def has_uncertainty(self) -> bool:
        return any(c.uncertainty and c.uncertainty.get("coverage") == "PARTIAL"
                   for c in self.changes)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "design_snapshot": self.design_snapshot,
            "code_snapshot": self.code_snapshot,
            "target_snapshot": self.target_snapshot,
            "changes": [c.to_dict() for c in self.changes],
            "file_plan": self.file_plan,
            "risk_summary": self.risk_summary,
            "expected_lvs_delta": self.expected_lvs_delta,
            "pre_drc": self.pre_drc,
            "pre_lvs": self.pre_lvs,
            "has_unsupported": self.has_unsupported,
            "has_uncertainty": self.has_uncertainty,
        }


@dataclass
class SynthesisResult:
    status: SynthesisStatus
    plan: SynthesisPlan | None = None
    patches: list[FilePatch] = field(default_factory=list)
    preview: dict = field(default_factory=dict)
    apply_result: dict = field(default_factory=dict)
    post_drc: dict = field(default_factory=dict)
    post_lvs: dict = field(default_factory=dict)
    failure: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "patches": [p.to_dict() for p in self.patches],
            "preview": self.preview,
            "apply_result": self.apply_result,
            "post_drc": self.post_drc,
            "post_lvs": self.post_lvs,
            "failure": self.failure,
        }
