"""SOFTWARE-DRC0 rule engine.

Rules consume a renderer-neutral context (topology + candidate boundaries +
ports + environment + design claims).  No X6 / Vue dependencies (spec §12).

Truth gates (frozen, enforced in rules + tests):
  * D2: data-dependent checks return UNKNOWN when DATA capability is
        PARTIAL/UNKNOWN — UNKNOWN is reported, never reported as PASS.
  * D3: INFERRED evidence never satisfies a RESOLVED proof requirement.
  * D4: unknown types never produce a type-mismatch finding.
  * D5: plain CALL cycles (recursion/loops) never produce C002 findings;
        C002 only applies to explicitly scoped subgraphs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import DrcRun, Finding, Severity, Status


@dataclass
class DrcContext:
    lane: str
    topology: dict[str, Any]              # {"nodes": [...], "edges": [...]}
    data_capability: str = "UNKNOWN"
    suggestions: list[dict] = field(default_factory=list)  # CandidateBoundary view
    ports: Any = None                     # PortIndex
    environment: dict[str, Any] = field(default_factory=dict)
    design_claims: list[dict] = field(default_factory=list)
    scoped_cycle_rules: list[dict] = field(default_factory=list)
    emit_pass: bool = False

    # convenience indexes (built by engine)
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    by_source: dict[str, list[dict]] = field(default_factory=dict)
    by_target: dict[str, list[dict]] = field(default_factory=dict)
    resource_names: set[str] = field(default_factory=set)

    def node_edges(self, cid: str, kinds: set[str] | None = None) -> list[dict]:
        out = list(self.by_source.get(cid, [])) + list(self.by_target.get(cid, []))
        if kinds:
            out = [e for e in out if e.get("kind") in kinds]
        return out

    def make(
        self,
        rule_id: str,
        *,
        severity: Severity,
        subject_ids: list[str] | None = None,
        related_ids: list[str] | None = None,
        message: str,
        why: str,
        truth_requirements: list[str] | None = None,
        truth_observed: dict[str, Any] | None = None,
        witnesses: list[dict] | None = None,
        source_locations: list[dict] | None = None,
        coverage: str = "UNKNOWN",
        confidence: float = 0.0,
        suggested_fix: str | None = None,
        status: Status | None = None,
    ) -> Finding:
        st = status or (Status.UNKNOWN if severity is Severity.UNKNOWN
                        else Status.VIOLATION if severity is Severity.ERROR
                        else Status.WARNING)
        return Finding(
            rule_id=rule_id, severity=severity, status=st,
            subject_ids=subject_ids or [], related_ids=related_ids or [],
            message=message, why=why,
            truth_requirements=truth_requirements or [],
            truth_observed=truth_observed, witnesses=witnesses or [],
            source_locations=source_locations or [], coverage=coverage,
            confidence=confidence, suggested_fix=suggested_fix,
        )


class Rule:
    """Base: every rule declares its id; run() returns findings only for
    status != PASS unless ctx.emit_pass is True."""

    rule_id = "DRC-XXXX"

    def run(self, ctx: DrcContext) -> list[Finding]:
        raise NotImplementedError


def evidence_ok(edge: dict, required: set[str] | None = None) -> bool:
    """D3 gate: only OBSERVED/RESOLVED truth satisfies hard requirements."""
    required = required or {"OBSERVED", "RESOLVED"}
    return (edge.get("truth_class") or "UNKNOWN") in required


def data_sufficient(ctx: DrcContext) -> bool:
    return ctx.data_capability in ("COMPLETE", "MIXED")


def unknown_finding(ctx: DrcContext, rule_id: str, message: str, why: str,
                    subject_ids: list[str] | None = None,
                    related_ids: list[str] | None = None,
                    coverage: str = "PARTIAL",
                    truth_observed: dict | None = None,
                    suggested_fix: str | None = None) -> Finding:
    return ctx.make(
        rule_id, severity=Severity.UNKNOWN,
        subject_ids=subject_ids, related_ids=related_ids,
        message=message, why=why,
        truth_requirements=["sufficient data/state evidence for this check"],
        truth_observed=truth_observed or {
            "data_capability": ctx.data_capability,
            "covers": coverage,
        },
        coverage=coverage, confidence=0.5,
        suggested_fix=suggested_fix,
    )


def sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (f.rule_id, tuple(f.subject_ids), f.message),
    )


def run_engine(ctx: DrcContext, rules: list[Rule]) -> DrcRun:
    # deterministic indexes
    nodes = {n["canonical_symbol_id"]: n for n in ctx.topology.get("nodes", [])}
    edges = sorted(ctx.topology.get("edges", []),
                   key=lambda e: (str(e.get("source") or ""),
                                  str(e.get("target") or ""),
                                  str(e.get("kind") or ""),
                                  str(e.get("truth_class") or "")))
    by_source: dict[str, list[dict]] = {}
    by_target: dict[str, list[dict]] = {}
    resource_names: set[str] = set()
    for e in edges:
        by_source.setdefault(e.get("source", ""), []).append(e)
        by_target.setdefault(e.get("target", ""), []).append(e)
        if e.get("kind") == "RESOURCE":
            resource_names.add(e.get("target", ""))
    ctx.nodes = nodes
    ctx.edges = edges
    ctx.by_source = by_source
    ctx.by_target = by_target
    ctx.resource_names = resource_names

    run = DrcRun(
        lane=ctx.lane,
        data_capability=ctx.data_capability,
        environment=ctx.environment,
    )
    for rule in rules:
        run.rules_run.append(rule.rule_id)
        try:
            found = rule.run(ctx)
        except Exception as exc:  # pragma: no cover - defensive
            found = [ctx.make(rule.rule_id, severity=Severity.UNKNOWN,
                              message=f"rule crashed: {exc}", why="internal error",
                              coverage="UNKNOWN")]
        if not ctx.emit_pass:
            found = [f for f in found if f.status is not Status.PASS]
        run.findings.extend(found)
    # determinism: sort every run
    run.findings = sorted_findings(run.findings)
    return run
