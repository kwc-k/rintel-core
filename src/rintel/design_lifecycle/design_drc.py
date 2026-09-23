"""Design lifecycle rules executed by the existing SOFTWARE-DRC engine."""
from __future__ import annotations

from typing import Any

from ..drc.engine import DrcContext, Rule, run_engine
from ..drc.model import Severity


class LifecycleDesignRule(Rule):
    rule_id = "DL-DRC-1"

    def run(self, ctx: DrcContext):
        findings = []
        claims = ctx.design_claims
        allowed_files = set(ctx.environment.get("scope", {}).get("files", []))
        constraints = ctx.environment.get("constraints", {})
        forbidden_kinds = set(constraints.get("forbidden_relation_kinds", []))
        forbidden_directions = {
            tuple(x) for x in constraints.get("forbidden_dependency_directions", [])
        }
        graph: dict[str, set[str]] = {}

        for i, claim in enumerate(claims):
            subject = [str(claim.get("id") or f"claim:{i}")]
            if claim.get("kind") == "planned_relation":
                src, dst = claim.get("source"), claim.get("target")
                if not src or not dst:
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.ERROR,
                        subject_ids=subject, message="planned relation target missing",
                        why="both source and target identities are required",
                        coverage="COMPLETE"))
                else:
                    graph.setdefault(str(src), set()).add(str(dst))
                    if (str(src), str(dst)) in forbidden_directions:
                        findings.append(ctx.make(
                            self.rule_id, severity=Severity.ERROR,
                            subject_ids=subject, message="illegal dependency direction",
                            why=f"{src} -> {dst} is forbidden by design constraints",
                            coverage="COMPLETE"))
                if claim.get("relation_kind") in forbidden_kinds:
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.ERROR,
                        subject_ids=subject,
                        message="forbidden architecture relation",
                        why=f"{claim.get('relation_kind')} is forbidden",
                        coverage="COMPLETE"))
                if not claim.get("justification") or not claim.get("support"):
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.UNKNOWN,
                        subject_ids=subject,
                        message="planned relation lacks justification or support",
                        why="unsupported design intent cannot satisfy a proof gate",
                        coverage="UNKNOWN"))
            if claim.get("kind") == "planned_interface":
                before = claim.get("base_signature")
                after = claim.get("planned_signature")
                if before is not None and after is not None and before != after \
                        and not claim.get("adapter"):
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.ERROR,
                        subject_ids=subject, message="interface mismatch",
                        why="signature changes require an explicit adapter or migration",
                        coverage="COMPLETE"))
            for path in claim.get("touches", []):
                if allowed_files and path not in allowed_files:
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.ERROR,
                        subject_ids=subject, message="scope contradiction",
                        why=f"{path} lies outside the approved change scope",
                        coverage="COMPLETE"))

        def visit(node: str, active: set[str], done: set[str]) -> bool:
            if node in active:
                return True
            if node in done:
                return False
            active.add(node)
            for target in graph.get(node, set()):
                if visit(target, active, done):
                    return True
            active.remove(node)
            done.add(node)
            return False

        done: set[str] = set()
        if any(visit(node, set(), done) for node in sorted(graph)):
            findings.append(ctx.make(
                self.rule_id, severity=Severity.ERROR,
                message="planned dependency cycle", why="planned relations form a cycle",
                coverage="COMPLETE"))
        return findings


def run_design_drc(expected_changes: tuple[dict[str, Any], ...],
                   scope: dict[str, Any]) -> dict[str, Any]:
    ctx = DrcContext(
        lane="design_lifecycle", topology={"nodes": [], "edges": []},
        design_claims=list(expected_changes),
        environment={"name": "design_lifecycle", "scope": scope,
                     "constraints": scope.get("constraints", {})},
        data_capability="PARTIAL",
    )
    result = run_engine(ctx, [LifecycleDesignRule()]).to_dict()
    result["acceptable"] = not any(
        f["status"] in {"VIOLATION", "UNKNOWN"}
        for f in result["findings"])
    result["rule_version"] = "design-lifecycle-drc/1"
    return result
