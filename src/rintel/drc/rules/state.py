"""DRC rules: state (S001–S003)."""
from __future__ import annotations

from ..engine import DrcContext, Rule, data_sufficient, unknown_finding
from ..model import Severity, Status


def _state_edges(ctx: DrcContext, token: str | None = None) -> list[dict]:
    out = [e for e in ctx.edges if e.get("kind") == "STATE"]
    if token:
        out = [e for e in out if (e.get("data") or {}).get("token") == token]
    return out


def _ordering_between(ctx: DrcContext, a: str, b: str) -> str | None:
    """'MUST' | 'MAY' | None based on TIME edges between a and b."""
    out = None
    for e in ctx.edges:
        if e.get("kind") != "TIME":
            continue
        s, t = e.get("source"), e.get("target")
        if s == a and t == b:
            m = e.get("execution_modality")
            if m == "MUST":
                return "MUST"
            out = out or ("MAY" if m == "MAY" else None)
        elif s == b and t == a:
            m = e.get("execution_modality")
            if m == "MUST":
                return "MUST"      # b -> a proven => a -> b impossible-ordered
    return out


class ConflictingWrites(Rule):
    """DRC-S001 — two operations write the same mutable state without
    proven ordering/synchronization -> POTENTIAL_STATE_CONFLICT (never a
    proven race; D6)."""

    rule_id = "DRC-S001"

    def run(self, ctx: DrcContext) -> list:
        edges = _state_edges(ctx)
        if not edges:
            if data_sufficient(ctx):
                return []
            return [unknown_finding(
                ctx, self.rule_id,
                "Cannot verify state conflicts: no STATE edges and DATA "
                f"capability is {ctx.data_capability}.",
                "S001 needs resolved STATE write evidence; frozen lanes "
                "carry none.",
                coverage=ctx.data_capability)]
        writes: dict[str, list[dict]] = {}
        for e in edges:
            token = (e.get("data") or {}).get("token") or e.get("target", "")
            if e.get("semantic_kind") in ("WRITE", "WRITE_AFTER_WRITE") or \
                    e.get("data", {}).get("access") == "write":
                writes.setdefault(token, []).append(e)
        findings = []
        for token, ws in sorted(writes.items()):
            writers = sorted({e.get("source") for e in ws})
            if len(writers) < 2:
                continue
            for i, a in enumerate(writers):
                for b in writers[i + 1:]:
                    order = _ordering_between(ctx, a, b)
                    if order == "MUST":
                        continue                    # proven ordering: safe
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.WARNING,
                        subject_ids=[a, b],
                        message=f'Potential state conflict on "{token}": '
                                f"{a} and {b} both write it and no proven "
                                "ordering or synchronization exists.",
                        why="Two writers on the same mutable state with no "
                            "TIME MUST_PRECEDE or sync guard. Potential is "
                            "reported; race certainty is NOT proven (D6).",
                        truth_requirements=["resolved STATE write edges",
                                            "ordering evidence"],
                        truth_observed={"token": token,
                                        "ordering": order or "NONE"},
                        witnesses=[w for e in ws
                                   for w in e.get("representative_witnesses", [])][:5],
                        source_locations=[e.get("source_span")
                                          for e in ws if e.get("source_span")][:4],
                        coverage="COMPLETE", confidence=0.6,
                        suggested_fix="add explicit ordering (MUST_PRECEDE) "
                                      "or synchronization"))
        return findings


class ReadWriteOrdering(Rule):
    """DRC-S002 — read depends on write; topology cannot prove required
    precedence -> POTENTIAL_HAZARD; proven -> PROVEN_SAFE (no finding)."""

    rule_id = "DRC-S002"

    def run(self, ctx: DrcContext) -> list:
        edges = _state_edges(ctx)
        if not edges:
            if data_sufficient(ctx):
                return []
            return [unknown_finding(
                ctx, self.rule_id,
                "Cannot verify read/write ordering: no STATE edges and DATA "
                f"capability is {ctx.data_capability}.",
                "S002 needs resolved STATE read/write evidence.",
                coverage=ctx.data_capability)]
        findings = []
        rw: list[tuple[dict, dict]] = []
        for w in [e for e in edges if (e.get("data") or {}).get("access") == "write"]:
            for r in [e for e in edges if (e.get("data") or {}).get("access") == "read"]:
                token_w = (w.get("data") or {}).get("token")
                token_r = (r.get("data") or {}).get("token")
                if token_w != token_r:
                    continue
                rw.append((w, r))
        seen: set[tuple] = set()
        for w, r in rw:
            key = (w.get("source"), r.get("source"), w.get("target"))
            if key in seen:
                continue
            seen.add(key)
            order = _ordering_between(ctx, w.get("source"), r.get("source"))
            if order == "MUST":
                continue                          # PROVEN_SAFE
            findings.append(ctx.make(
                self.rule_id, severity=Severity.WARNING,
                subject_ids=[w.get("source"), r.get("source")],
                related_ids=[str(w.get("target"))],
                message=f'Read/write ordering hazard on "{w.get("target")}": '
                        "a write precedes a read but no MUST_PRECEDE can be "
                        "proven.",
                why="A read depends on a write to the same state token; "
                    "the topology only supports "
                    f"{order or 'MAY/UNKNOWN'} ordering. Potential hazard is "
                    "reported; proven hazard is not claimed.",
                truth_requirements=["resolved STATE read+write on same token",
                                    "ordering evidence"],
                truth_observed={"token": w.get("target"),
                                "ordering": order or "NONE"},
                witnesses=(w.get("representative_witnesses", []) +
                           r.get("representative_witnesses", []))[:5],
                source_locations=[w.get("source_span"), r.get("source_span")],
                coverage="COMPLETE", confidence=0.6,
                suggested_fix="prove ordering (MUST_PRECEDE) or synchronize"))
        return findings


class HiddenSharedState(Rule):
    """DRC-S003 — a Suggested Composite/Module looks encapsulated but
    relies on mutable external state not exposed on its boundary."""

    rule_id = "DRC-S003"

    def run(self, ctx: DrcContext) -> list:
        state_edges = _state_edges(ctx)
        if not state_edges:
            if data_sufficient(ctx) or not ctx.suggestions:
                return []
            return [unknown_finding(
                ctx, self.rule_id,
                "Cannot verify hidden shared state: no STATE edges and DATA "
                f"capability is {ctx.data_capability}.",
                "S003 needs resolved STATE evidence to see hidden "
                "cross-boundary state.",
                coverage=ctx.data_capability)]
        findings = []
        for sugg in ctx.suggestions:
            members = set(sugg.get("members", []))
            boundary = sugg.get("boundary") or {}
            exposed = {_tok(x) for x in boundary.get("state_inout", [])}
            hidden: set[str] = set()
            for e in state_edges:
                if e.get("kind") != "STATE":
                    continue
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
                    message=f'Suggested boundary hides external mutable '
                            f'state: {", ".join(sorted(hidden)[:5])} not '
                            "exposed on state_inout.",
                    why="Members reference state outside the candidate while "
                        "the boundary does not expose it (encapsulation "
                        "claim would be false).",
                    truth_requirements=["STATE edges crossing boundary",
                                        "boundary state_inout"],
                    truth_observed={"hidden": sorted(hidden),
                                    "exposed": sorted(exposed)},
                    witnesses=[w for e in state_edges
                               for w in e.get("representative_witnesses", [])][:5],
                    source_locations=[e.get("source_span")
                                      for e in state_edges if e.get("source_span")][:4],
                    coverage="COMPLETE", confidence=0.7,
                    suggested_fix="expose the state on the boundary or "
                                  "encapsulate it inside the composite"))
        return findings


def _tok(item) -> str:
    if isinstance(item, dict):
        return str(item.get("token") or item.get("name") or item.get("resource") or "")
    return str(item)


ALL = [ConflictingWrites, ReadWriteOrdering, HiddenSharedState]
