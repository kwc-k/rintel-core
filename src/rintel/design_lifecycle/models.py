"""Immutable domain values for the DESIGN-LIFECYCLE0 change aggregate."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ChangeState(str, Enum):
    OPEN = "OPEN"
    PLANNED = "PLANNED"
    IMPLEMENTING = "IMPLEMENTING"
    EVIDENCE_MATCHED = "EVIDENCE_MATCHED"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"
    ABANDONED = "ABANDONED"


class ExpectedActualOutcome(str, Enum):
    MATCHED = "MATCHED"
    MISSING_IMPLEMENTATION = "MISSING_IMPLEMENTATION"
    UNEXPECTED_IMPLEMENTATION = "UNEXPECTED_IMPLEMENTATION"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DesignRef:
    identity: str
    revision: str


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    kind: str
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        allowed = {"structural", "semantic", "test", "runtime", "manual"}
        if self.kind not in allowed:
            raise ValueError(f"unsupported acceptance criterion kind: {self.kind}")


@dataclass(frozen=True)
class DesignRevision:
    id: str
    change_id: str
    authority: str
    architecture_proposal_ref: DesignRef | None
    flow_model_ref: DesignRef | None
    expected_changes: tuple[dict[str, Any], ...]
    created_at: int
    digest: str


@dataclass(frozen=True)
class EvidenceBrief:
    canonical_revision: str
    modules: tuple[dict[str, Any], ...] = ()
    symbols: tuple[dict[str, Any], ...] = ()
    flows: tuple[dict[str, Any], ...] = ()
    data_interfaces: tuple[dict[str, Any], ...] = ()
    runtime_evidence: tuple[dict[str, Any], ...] = ()
    unknowns: tuple[dict[str, Any], ...] = ()
    reference_evidence: tuple[dict[str, Any], ...] = ()
    constraints: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class LifecycleReceipt:
    id: str
    change_id: str
    sequence: int
    command: str
    actor: str
    state_before: str | None
    state_after: str
    payload: dict[str, Any]
    digest: str
    created_at: int


@dataclass(frozen=True)
class DesignChange:
    id: str
    repo_id: str
    base_canonical_revision: str
    intent: str
    scope: dict[str, Any]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    design_revision: DesignRevision
    evidence_brief: EvidenceBrief
    state: ChangeState
    design_drc: dict[str, Any] | None = None
    implementation: dict[str, Any] | None = None
    expected_actual: dict[str, Any] | None = None
    lvs_result: dict[str, Any] | None = None
    verification: tuple[dict[str, Any], ...] = ()
    close_receipt: dict[str, Any] | None = None
    remaining_unknowns: tuple[dict[str, Any], ...] = ()
    version: int = 1
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["state"] = self.state.value
        return out

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DesignChange":
        rev = dict(value["design_revision"])
        if rev.get("architecture_proposal_ref"):
            rev["architecture_proposal_ref"] = DesignRef(
                **rev["architecture_proposal_ref"])
        if rev.get("flow_model_ref"):
            rev["flow_model_ref"] = DesignRef(**rev["flow_model_ref"])
        rev["expected_changes"] = tuple(rev.get("expected_changes", ()))
        brief = dict(value["evidence_brief"])
        for name in (
            "modules", "symbols", "flows", "data_interfaces",
            "runtime_evidence", "unknowns", "reference_evidence", "constraints",
        ):
            brief[name] = tuple(brief.get(name, ()))
        return cls(
            id=value["id"], repo_id=value["repo_id"],
            base_canonical_revision=value["base_canonical_revision"],
            intent=value["intent"], scope=dict(value.get("scope", {})),
            acceptance_criteria=tuple(
                AcceptanceCriterion(**item)
                for item in value.get("acceptance_criteria", ())),
            design_revision=DesignRevision(**rev),
            evidence_brief=EvidenceBrief(**brief),
            state=ChangeState(value["state"]),
            design_drc=value.get("design_drc"),
            implementation=value.get("implementation"),
            expected_actual=value.get("expected_actual"),
            lvs_result=value.get("lvs_result"),
            verification=tuple(value.get("verification", ())),
            close_receipt=value.get("close_receipt"),
            remaining_unknowns=tuple(value.get("remaining_unknowns", ())),
            version=int(value.get("version", 1)),
            created_at=int(value.get("created_at", 0)),
            updated_at=int(value.get("updated_at", 0)),
        )


@dataclass(frozen=True)
class BuildAttemptRef:
    identity: str
    revision: str


@dataclass(frozen=True)
class BuildDiagnosticRef:
    identity: str
    revision: str


@dataclass(frozen=True)
class RecordRepairApplication:
    """Record an actor's repair claim; never assert build or test success."""
    actor: str
    application_id: str
    previous_attempt: str
    repair_candidate_id: str
    source_revision_after_claim: str
    expected_version: int | None = None


@dataclass(frozen=True)
class PlanChange:
    actor: str
    expected_changes: tuple[dict[str, Any], ...]
    architecture_proposal_ref: DesignRef | None = None
    flow_model_ref: DesignRef | None = None
    expected_version: int | None = None


@dataclass(frozen=True)
class BeginImplementation:
    actor: str
    expected_touched_scope: dict[str, Any]
    expected_version: int | None = None


@dataclass(frozen=True)
class RecordReindex:
    actor: str
    final_canonical_revision: str
    actual_changed_files: tuple[str, ...] = ()
    actual_changed_symbols: tuple[str, ...] = ()
    actual_evidence: tuple[dict[str, Any], ...] = ()
    build_attempt_ref: BuildAttemptRef | None = None
    build_diagnostic_refs: tuple[BuildDiagnosticRef, ...] = ()
    expected_version: int | None = None


@dataclass(frozen=True)
class RunReindex:
    actor: str
    actual_evidence_selectors: tuple[dict[str, Any], ...] = ()
    expected_version: int | None = None


@dataclass(frozen=True)
class RequestReindex:
    actor: str
    job_id: str
    expected_version: int | None = None


@dataclass(frozen=True)
class AdoptIndexJob:
    actor: str
    job_id: str
    actual_evidence_selectors: tuple[dict[str, Any], ...] = ()
    expected_version: int | None = None


@dataclass(frozen=True)
class EvaluateExpectedActual:
    actor: str
    expected_version: int | None = None


@dataclass(frozen=True)
class VerifyChange:
    actor: str
    results: tuple[dict[str, Any], ...]
    expected_version: int | None = None


@dataclass(frozen=True)
class CloseChange:
    actor: str
    rule_versions: dict[str, str]
    expected_version: int | None = None


@dataclass(frozen=True)
class AbandonChange:
    actor: str
    reason: str
    expected_version: int | None = None


@dataclass(frozen=True)
class DesignMutation:
    """A design-plane write admitted by an existing DesignChange.

    The producer selects an operation, but cannot select authority or create a
    lifecycle implicitly.  The service validates the target repository and
    records the resulting exact design reference in a new DesignRevision.
    """

    actor: str
    plane: str
    operation: str
    payload: dict[str, Any]
    expected_version: int | None = None
    expected_design_revision: str | None = None
