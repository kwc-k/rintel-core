"""DRC rules: control / timing (C001–C003)."""
from __future__ import annotations

from ..engine import DrcContext, Rule, unknown_finding
from ..model import Severity


class ImpossibleMust(Rule):
    """DRC-C001 — design/netlist asserts MUST_PRECEDE but the frozen
    topology only supports MAY/UNKNOWN -> UNSUPPORTED_ORDERING_CLAIM."""

    rule_id = "DRC-C001"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        claims = [c for c in ctx.design_claims
                  if c.get("kind") in ("MUST_PRECEDE", "ORDERING_CLAIM")]
        if not claims:
            return []
        for c in sorted(claims, key=lambda c: str(c.get("id", ""))):
            a, b = c.get("source"), c.get("target")
            if not a or not b:
                continue
            supported: str | None = None
            for e in ctx.edges:
                if e.get("kind") != "TIME":
                    continue
                if e.get("source") == a and e.get("target") == b:
                    supported = e.get("execution_modality") or "UNKNOWN"
                elif e.get("source") == b and e.get("target") == a:
                    supported = "CONTRADICTED"
            if supported == "MUST":
                continue                              # claim verified
            findings.append(ctx.make(
                self.rule_id, severity=Severity.WARNING,
                subject_ids=[a, b],
                related_ids=[str(c.get("id", ""))],
                message=f'Design asserts MUST_PRECEDE {a} → {b} but frozen '
                        f"topology only supports {supported or 'UNKNOWN'}.",
                why="A MUST claim needs MUST evidence; MAY/absent evidence "
                    "makes the ordering claim unsupported (not proven).",
                truth_requirements=["MUST_PRECEDE evidence in topology"],
                truth_observed={"claim": c.get("kind"),
                                "supported": supported or "UNKNOWN"},
                witnesses=c.get("witnesses", []),
                source_locations=c.get("source_locations", []),
                coverage=c.get("coverage", "PARTIAL"), confidence=0.7,
                suggested_fix="downgrade the design claim to MAY or prove "
                              "the ordering with runtime evidence"))
        return findings


class IllegalCycle(Rule):
    """DRC-C002 — cycles flagged only within explicitly scoped subgraphs
    (acyclic init dependency / single-pass pipeline / resource acquisition
    ordering).  Plain CALL recursion/loops/event cycles are NOT violations
    (D5)."""

    rule_id = "DRC-C002"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        for scoped in ctx.scoped_cycle_rules:
            scope = scoped.get("scope", "generic")
            edges = scoped.get("edges", [])
            if not edges:
                continue
            cycles = _find_cycles(edges)
            if cycles:
                for cycle in sorted(cycles, key=lambda c: (len(c), c)):
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.ERROR,
                        subject_ids=list(cycle),
                        related_ids=[scope],
                        message=f"Illegal cycle in scoped rule "
                                f"'{scope}': {' → '.join(cycle)}.",
                        why=f"The scoped rule '{scope}' requires an acyclic "
                            "dependency graph (e.g. initialization, "
                            "single-pass pipeline, resource acquisition "
                            "ordering); the cycle violates it. Cycles "
                            "outside declared scopes (plain recursion/loops) "
                            "are not flagged (D5).",
                        truth_requirements=[f"scoped rule '{scope}' "
                                            "acyclicity"],
                        truth_observed={"scope": scope, "cycle": list(cycle)},
                        coverage="COMPLETE", confidence=0.85,
                        suggested_fix="break the cycle (staging, lazy "
                                      "initialization, event decoupling)"))
        return findings


def _find_cycles(edges: list[dict]) -> list[tuple[str, ...]]:
    """Deterministic elementary-ish cycle enumeration (small scoped
    graphs); returns normalized tuples (rotated to smallest element)."""
    adj: dict[str, list[str]] = {}
    for e in edges:
        s, t = str(e.get("source", "")), str(e.get("target", ""))
        if s and t:
            adj.setdefault(s, []).append(t)
    for k in adj:
        adj[k] = sorted(set(adj[k]))
    nodes = sorted(adj)
    found: set[tuple[str, ...]] = set()

    def dfs(start: str, path: list[str], visited: set[str]) -> None:
        for nxt in adj.get(path[-1], []):
            if nxt == start and len(path) >= 2:
                cyc = path[:]
                # normalize: rotate so the smallest node comes first, then
                # keep the lexicographically smallest rotation
                rots = [tuple(cyc[i:] + cyc[:i]) for i in range(len(cyc))]
                found.add(min(rots))
            elif nxt not in visited and len(path) < 12:
                dfs(start, path + [nxt], visited | {nxt})

    for start in nodes:
        dfs(start, [start], {start})
    return sorted(found, key=lambda c: (len(c), c))


class UnreachableDesignedBranch(Rule):
    """DRC-C003 — a designed branch/path the canonical topology cannot
    support: COMPLETE coverage -> STALE/INVALID PATH; PARTIAL -> UNKNOWN."""

    rule_id = "DRC-C003"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        claims = [c for c in ctx.design_claims if c.get("kind") == "BRANCH_CLAIM"]
        if not claims:
            return []
        for c in sorted(claims, key=lambda c: str(c.get("id", ""))):
            src, tgt = c.get("source"), c.get("target")
            if not src or not tgt:
                continue
            supported = any(
                e.get("kind") in ("CALL", "CONTROL")
                and e.get("source") == src and e.get("target") == tgt
                for e in ctx.edges)
            coverage = c.get("coverage", "PARTIAL")
            if supported:
                continue
            if coverage == "COMPLETE":
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.WARNING,
                    subject_ids=[src, tgt],
                    related_ids=[str(c.get("id", ""))],
                    message=f"Designed path {src} → {tgt} is not supported "
                            "by canonical topology (STALE/INVALID path).",
                    why="The design branch claims a relation the frozen "
                        "topology cannot support while coverage is "
                        "COMPLETE.",
                    truth_requirements=["branch support in topology"],
                    truth_observed={"coverage": coverage,
                                    "supported": False},
                    coverage="COMPLETE", confidence=0.7,
                    suggested_fix="update the design or drop the stale path"))
            else:
                findings.append(unknown_finding(
                    ctx, self.rule_id,
                    f"Cannot verify designed path {src} → {tgt}: coverage is "
                    f"{coverage}.",
                    "C003 yields UNKNOWN under partial coverage instead of "
                    "asserting staleness (D2).",
                    subject_ids=[src, tgt], related_ids=[str(c.get("id", ""))],
                    coverage=coverage))
        return findings


ALL = [ImpossibleMust, IllegalCycle, UnreachableDesignedBranch]
