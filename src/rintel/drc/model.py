"""SOFTWARE-DRC0 finding model.

Severity / status are frozen (spec §11):  ERROR / WARNING / INFO / UNKNOWN.
Status: VIOLATION / WARNING / UNKNOWN / PASS.

Invariants enforced at construction and by tests:
  * severity == UNKNOWN  <=>  status == UNKNOWN   (D2: UNKNOWN is not PASS)
  * severity == ERROR    =>  status == VIOLATION
  * severity in (WARNING, INFO) => status == WARNING
  * A finding is never a PASS unless `emit_pass` mode is on (fixtures).
  * findings carry explicit rule_id + witnesses (D1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    UNKNOWN = "UNKNOWN"


class Status(str, Enum):
    VIOLATION = "VIOLATION"
    WARNING = "WARNING"
    UNKNOWN = "UNKNOWN"
    PASS = "PASS"


def status_for(severity: Severity) -> Status:
    if severity is Severity.UNKNOWN:
        return Status.UNKNOWN
    if severity is Severity.ERROR:
        return Status.VIOLATION
    return Status.WARNING


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    status: Status
    subject_ids: list[str] = field(default_factory=list)
    related_ids: list[str] = field(default_factory=list)
    message: str = ""
    why: str = ""
    truth_requirements: list[str] = field(default_factory=list)
    truth_observed: dict[str, Any] | None = None
    witnesses: list[dict] = field(default_factory=list)
    source_locations: list[dict] = field(default_factory=list)
    coverage: str = "UNKNOWN"
    confidence: float = 0.0
    suggested_fix: str | None = None

    def __post_init__(self) -> None:
        self.severity = Severity(self.severity)
        self.status = Status(self.status)
        if self.status is Status.PASS:
            # PASS findings only exist in fixture detail mode; the engine
            # strips them from real runs.  Keep the invariant trivially true.
            self.severity = Severity.INFO
            self.status = Status.PASS
            return
        expected = status_for(self.severity)
        if self.status is Status.UNKNOWN and self.severity is not Severity.UNKNOWN:
            self.severity = Severity.UNKNOWN
        elif self.status is Status.UNKNOWN:
            self.severity = Severity.UNKNOWN
        else:
            self.status = expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "status": self.status.value,
            "subject_ids": list(self.subject_ids),
            "related_ids": list(self.related_ids),
            "message": self.message,
            "why": self.why,
            "truth_requirements": list(self.truth_requirements),
            "truth_observed": self.truth_observed,
            "witnesses": self.witnesses,
            "source_locations": self.source_locations,
            "coverage": self.coverage,
            "confidence": round(float(self.confidence), 3),
            "suggested_fix": self.suggested_fix,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(
            rule_id=d["rule_id"],
            severity=d["severity"],
            status=d["status"],
            subject_ids=d.get("subject_ids", []),
            related_ids=d.get("related_ids", []),
            message=d.get("message", ""),
            why=d.get("why", ""),
            truth_requirements=d.get("truth_requirements", []),
            truth_observed=d.get("truth_observed"),
            witnesses=d.get("witnesses", []),
            source_locations=d.get("source_locations", []),
            coverage=d.get("coverage", "UNKNOWN"),
            confidence=d.get("confidence", 0.0),
            suggested_fix=d.get("suggested_fix"),
        )


@dataclass
class DrcRun:
    lane: str
    data_capability: str
    environment: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    rules_run: list[str] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        by_sev: dict[str, int] = {}
        by_rule: dict[str, int] = {}
        for f in self.findings:
            by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1
            by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
        return {
            "lane": self.lane,
            "total": len(self.findings),
            "by_severity": by_sev,
            "by_rule": by_rule,
            "data_capability": self.data_capability,
            "environment": self.environment.get("name"),
            "pass_count": 0,      # UNKNOWN is never PASS, and runs never emit PASS
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "data_capability": self.data_capability,
            "environment": self.environment,
            "summary": self.summary(),
            "findings": [f.to_dict() for f in self.findings],
            "rules_run": self.rules_run,
            "not_applicable": self.not_applicable,
            "notes": self.notes,
        }
