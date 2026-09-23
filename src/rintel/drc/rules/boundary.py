"""DRC rules: CandidateBoundaryDRC (B001–B004, spec §8).

Runs boundary completeness against every Suggested candidate:
  * required Data exposed?
  * produced external Data exposed?
  * shared mutable State exposed?
  * required Resources exposed?
  * dynamic uncertainty preserved?
Suggestions are NOT validated as implementations — findings describe how
the suggested boundary would hide dependencies (D8: no promotion here).
"""
from __future__ import annotations

from ..engine import DrcContext, Rule, data_sufficient, unknown_finding
from ..model import Severity


class BoundaryDataExposure(Rule):
    """DRC-B001 — required/produced data not exposed on the suggested
    boundary (only evaluated with resolved DATA edges; otherwise UNKNOWN)."""

    rule_id = "DRC-B001"

    def run(self, ctx: DrcContext) -> list:
        if not data_sufficient(ctx):
            if ctx.suggestions:
                return [unknown_finding(
                    ctx, self.rule_id,
                    "Cannot verify boundary data exposure: DATA capability "
                    f"is {ctx.data_capability}.",
                    "B001 needs resolved DATA wires to evaluate crossing "
                    "exposure (D2).",
                    coverage=ctx.data_capability)]
            return []
        findings = []
        data_edges = [e for e in ctx.edges if e.get("kind") == "DATA"]
        for sugg in ctx.suggestions:
            members = set(sugg.get("members", []))
            b = sugg.get("boundary") or {}
            data_in = {_tok(x) for x in b.get("data_in", [])}
            data_out = {_tok(x) for x in b.get("data_out", [])}
            crossing_out: set[str] = set()
            crossing_in: set[str] = set()
            for e in data_edges:
                tok = (e.get("data") or {}).get("token")
                if not tok:
                    continue
                s, t = e.get("source"), e.get("target")
                if s in members and t not in members:
                    crossing_out.add(tok)
                elif t in members and s not in members:
                    crossing_in.add(tok)
            for tok, exposed, direction in (
                    (crossing_out - data_out, data_out, "data_out"),
                    (crossing_in - data_in, data_in, "data_in")):
                if tok:
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.WARNING,
                        subject_ids=[sugg.get("candidate_id", "")],
                        related_ids=sorted(tok)[:5],
                        message=f"Candidate boundary exposes no "
                                f"{direction} port for data "
                                f"{', '.join(sorted(tok)[:5])} — produced/"
                                "required data is hidden.",
                        why="CandidateBoundaryDRC §8: all produced/required "
                            "data must be exposed for the boundary to be "
                            "sound. Suggested units are not validated as "
                            "implementations (D8).",
                        truth_requirements=["resolved DATA edges crossing",
                                            "boundary ports"],
                        truth_observed={"hidden": sorted(tok),
                                        "exposed": sorted(exposed),
                                        "direction": direction},
                        witnesses=[w for e in data_edges
                                   for w in e.get("representative_witnesses", [])][:4],
                        coverage="COMPLETE", confidence=0.65,
                        suggested_fix="declare the data on the suggested "
                                      "boundary or keep the unit open"))
        return findings


class BoundaryStateExposure(Rule):
    """DRC-B002 — shared mutable State not exposed (reuses S003
    semantics; distinct id for CandidateBoundaryDRC granularity)."""

    rule_id = "DRC-B002"

    def run(self, ctx: DrcContext) -> list:
        state_edges = [e for e in ctx.edges if e.get("kind") == "STATE"]
        findings = []
        if not state_edges:
            if ctx.suggestions and not data_sufficient(ctx):
                return [unknown_finding(
                    ctx, self.rule_id,
                    "Cannot verify shared-state exposure: no STATE edges "
                    f"(DATA capability {ctx.data_capability}).",
                    "B002 needs resolved STATE evidence (D2).",
                    coverage=ctx.data_capability)]
            return []
        for sugg in ctx.suggestions:
            members = set(sugg.get("members", []))
            b = sugg.get("boundary") or {}
            exposed = {_tok(x) for x in b.get("state_inout", [])}
            hidden: set[str] = set()
            for e in state_edges:
                s, t = e.get("source"), e.get("target")
                if s in members and t not in members:
                    hidden.add(str(t))
                elif t in members and s not in members:
                    hidden.add(str(s))
            hidden -= exposed
            if hidden:
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.WARNING,
                    subject_ids=[sugg.get("candidate_id", "")],
                    related_ids=sorted(hidden)[:5],
                    message=f"Suggested boundary hides shared mutable state "
                            f"{', '.join(sorted(hidden)[:5])}.",
                    why="CandidateBoundaryDRC §8: shared mutable state must "
                        "be exposed; the suggestion as formed would hide an "
                        "external dependency (D8 — suggestion is not "
                        "promoted).",
                    truth_requirements=["STATE edges crossing boundary",
                                        "boundary state_inout"],
                    truth_observed={"hidden": sorted(hidden),
                                    "exposed": sorted(exposed)},
                    witnesses=[w for e in state_edges
                               for w in e.get("representative_witnesses", [])][:4],
                    coverage="COMPLETE", confidence=0.65,
                    suggested_fix="expose state_inout on the boundary"))
        return findings


class BoundaryResourceExposure(Rule):
    """DRC-B003 — required Resources not exposed (reuses R002
    semantics; distinct id for CandidateBoundaryDRC granularity)."""

    rule_id = "DRC-B003"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        for sugg in ctx.suggestions:
            members = set(sugg.get("members", []))
            b = sugg.get("boundary") or {}
            exposed = {str(r.get("resource")) for r in b.get("resource_ports", [])
                       if isinstance(r, dict) and r.get("resource")}
            required: set[str] = set()
            for m in members:
                required |= set(_res(ctx, m))
            hidden = required - exposed
            if hidden:
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.WARNING,
                    subject_ids=[sugg.get("candidate_id", "")],
                    related_ids=sorted(hidden)[:5],
                    message=f"Suggested boundary exposes no resource port "
                            f"for {', '.join(sorted(hidden)[:5])}.",
                    why="CandidateBoundaryDRC §8: required resources must "
                        "be exposed; the suggestion would hide the "
                        "dependency (D8 — no promotion).",
                    truth_requirements=["RESOURCE edges on members",
                                        "boundary resource_ports"],
                    truth_observed={"hidden": sorted(hidden),
                                    "exposed": sorted(exposed)},
                    witnesses=[w for m in members
                               for e in ctx.by_source.get(m, [])
                               if e.get("kind") == "RESOURCE"
                               for w in e.get("representative_witnesses", [])][:4],
                    coverage="COMPLETE", confidence=0.65,
                    suggested_fix="expose resource ports on the boundary"))
        return findings


class BoundaryDynamicPreserved(Rule):
    """DRC-B004 — dynamic uncertainty preserved: a candidate whose members
    include unknown-dynamic calls must keep them visible (not closed)."""

    rule_id = "DRC-B004"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        for sugg in ctx.suggestions:
            members = set(sugg.get("members", []))
            count = 0
            for e in ctx.edges:
                if e.get("target") == "[Unknown Dynamic Target]" and \
                        e.get("source") in members:
                    count += 1
                elif e.get("target_resolution") == "UNKNOWN" and \
                        e.get("source") in members:
                    count += 1
            if count:
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.WARNING,
                    subject_ids=[sugg.get("candidate_id", "")],
                    message=f"Candidate contains {count} unresolved dynamic "
                            "call site(s); dynamic uncertainty must be "
                            "preserved (not closed) by any boundary claim.",
                    why="CandidateBoundaryDRC §8: dynamic uncertainty stays "
                        "visible; the suggestion must not pretend target "
                        "closure.",
                    truth_requirements=["unresolved callsites in members"],
                    truth_observed={"unresolved_dynamic": count},
                    witnesses=[w for e in ctx.edges
                               if e.get("target") == "[Unknown Dynamic Target]"
                               and e.get("source") in members
                               for w in e.get("representative_witnesses", [])][:3],
                    coverage="UNKNOWN", confidence=0.6))
        return findings


def _res(ctx: DrcContext, cid: str) -> list[str]:
    out = list(ctx.nodes.get(cid, {}).get("resources", []) or [])
    for e in ctx.by_source.get(cid, []):
        if e.get("kind") == "RESOURCE" and e.get("target"):
            out.append(str(e.get("target")))
    return set(out)


def _tok(item) -> str:
    if isinstance(item, dict):
        return str(item.get("token") or item.get("name") or item.get("resource") or "")
    return str(item)


ALL = [BoundaryDataExposure, BoundaryStateExposure,
       BoundaryResourceExposure, BoundaryDynamicPreserved]
