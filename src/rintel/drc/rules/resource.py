"""DRC rules: resources (R001–R003)."""
from __future__ import annotations

from ..engine import DrcContext, Rule, unknown_finding
from ..model import Severity


def _resources_of(ctx: DrcContext, cid: str) -> list[str]:
    out = list(ctx.nodes.get(cid, {}).get("resources", []) or [])
    for e in ctx.by_source.get(cid, []):
        if e.get("kind") == "RESOURCE" and e.get("target"):
            out.append(str(e.get("target")))
    return sorted(set(out))


class MissingCapability(Rule):
    """DRC-R001 — function requires a capability the current
    Environment/Profile does not provide (bound to the profile, D7)."""

    rule_id = "DRC-R001"

    def run(self, ctx: DrcContext) -> list:
        env_resources = set()
        env = ctx.environment or {}
        for r, spec in (env.get("resources") or {}).items():
            if not isinstance(spec, dict) or spec.get("provided", True):
                env_resources.add(r)
        profile = env.get("name", "default")
        findings = []
        for cid in sorted(ctx.nodes):
            for r in _resources_of(ctx, cid):
                if r not in env_resources:
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.WARNING,
                        subject_ids=[cid],
                        related_ids=[f"env:{profile}", r],
                        message=f'Function requires "{r}" but the current '
                                f"environment '{profile}' does not provide "
                                "it (missing capability).",
                        why="Resource requirement is evidenced by the "
                            "topology; the declared profile lacks the "
                            "capability. Finding is bound to the "
                            "Environment/Profile (D7).",
                        truth_requirements=["resolved RESOURCE edge",
                                            "environment profile"],
                        truth_observed={"resource": r,
                                        "profile": profile,
                                        "provided": False},
                        witnesses=[w for e in ctx.by_source.get(cid, [])
                                   if e.get("kind") == "RESOURCE"
                                   for w in e.get("representative_witnesses", [])][:3],
                        source_locations=[e.get("source_span")
                                          for e in ctx.by_source.get(cid, [])
                                          if e.get("kind") == "RESOURCE"
                                          and e.get("source_span")][:3],
                        coverage="COMPLETE", confidence=0.7,
                        suggested_fix="add the capability to the "
                                      "environment profile or decouple "
                                      "the function"))
        return findings


class HiddenResourceDependency(Rule):
    """DRC-R002 — a Suggested Composite/Module requires an external
    Resource not exposed on its boundary -> RESOURCE_PORT_MISSING."""

    rule_id = "DRC-R002"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        if not ctx.suggestions:
            return []
        for sugg in ctx.suggestions:
            members = set(sugg.get("members", []))
            boundary = sugg.get("boundary") or {}
            exposed = {str(r.get("resource")) for r in boundary.get("resource_ports", [])
                       if isinstance(r, dict) and r.get("resource")}
            exposed |= {str(r) for r in boundary.get("resource_ports", [])
                        if not isinstance(r, dict)}
            required: set[str] = set()
            for m in members:
                required |= set(_resources_of(ctx, m))
            hidden = required - exposed
            if hidden:
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.WARNING,
                    subject_ids=[sugg.get("candidate_id", "")],
                    related_ids=sorted(hidden)[:5],
                    message=f'Hidden resource dependency: {", ".join(sorted(
                        hidden)[:5])} required by members but absent from '
                            "the boundary resource ports "
                            "(RESOURCE_PORT_MISSING).",
                    why="Members have resolved RESOURCE edges; the "
                        "candidate boundary does not expose them, so the "
                        "suggested unit would hide an external dependency.",
                    truth_requirements=["resolved RESOURCE edges on members",
                                        "boundary resource_ports"],
                    truth_observed={"hidden": sorted(hidden),
                                    "exposed": sorted(exposed)},
                    witnesses=[w for m in sorted(members)
                               for e in ctx.by_source.get(m, [])
                               if e.get("kind") == "RESOURCE"
                               for w in e.get("representative_witnesses", [])][:5],
                    source_locations=[e.get("source_span")
                                      for m in sorted(members)
                                      for e in ctx.by_source.get(m, [])
                                      if e.get("kind") == "RESOURCE"
                                      and e.get("source_span")][:4],
                    coverage="COMPLETE", confidence=0.7,
                    suggested_fix="expose the resource ports on the "
                                  "suggested boundary"))
        return findings


class ExclusiveResourceConflict(Rule):
    """DRC-R003 — two operations may overlap and both require the same
    EXCLUSIVE resource.  Reported only when exclusivity is known and no
    ordering is proven (D6 conservatism)."""

    rule_id = "DRC-R003"

    def run(self, ctx: DrcContext) -> list:
        env_res = (ctx.environment or {}).get("resources", {}) or {}
        exclusive = {r for r, spec in env_res.items()
                     if isinstance(spec, dict) and spec.get("exclusive")}
        if not exclusive:
            return []
        users: dict[str, list[str]] = {}
        for r in sorted(exclusive):
            for cid in sorted(ctx.nodes):
                if r in _resources_of(ctx, cid):
                    users.setdefault(r, []).append(cid)
        findings = []
        for r, cids in sorted(users.items()):
            for i, a in enumerate(cids):
                for b in cids[i + 1:]:
                    order = _may_be_ordered(ctx, a, b)
                    if order == "MUST":
                        continue
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.WARNING,
                        subject_ids=[a, b],
                        related_ids=[f"resource:{r}"],
                        message=f'Both {a} and {b} may use exclusive '
                                f'resource "{r}" with no proven ordering '
                                "(potential overlap).",
                        why="The resource is marked exclusive in the "
                            "environment profile and both operations use it; "
                            "without proven ordering the overlap is "
                            "potential, not proven (D6).",
                        truth_requirements=["exclusive flag in profile",
                                            "RESOURCE edges for both ops"],
                        truth_observed={"resource": r,
                                        "exclusive": True,
                                        "ordering": order or "NONE"},
                        witnesses=[w for e in ctx.by_source.get(a, [])
                                   if e.get("kind") == "RESOURCE"
                                   for w in e.get("representative_witnesses", [])][:3],
                        source_locations=[e.get("source_span")
                                          for e in ctx.by_source.get(a, [])
                                          if e.get("kind") == "RESOURCE"
                                          and e.get("source_span")][:2],
                        coverage="COMPLETE", confidence=0.55,
                        suggested_fix="add acquisition ordering or a "
                                      "mutex/serialization"))
        return findings


def _may_be_ordered(ctx: DrcContext, a: str, b: str) -> str | None:
    for e in ctx.edges:
        if e.get("kind") != "TIME":
            continue
        if e.get("source") == a and e.get("target") == b:
            return e.get("execution_modality") or "MAY"
        if e.get("source") == b and e.get("target") == a:
            return e.get("execution_modality") or "MAY"
    return None


ALL = [MissingCapability, HiddenResourceDependency, ExclusiveResourceConflict]
