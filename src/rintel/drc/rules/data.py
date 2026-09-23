"""DRC rules: data (D001–D003)."""
from __future__ import annotations

from ..engine import DrcContext, Rule, data_sufficient, unknown_finding
from ..model import Severity, Status


class MultipleProducers(Rule):
    """DRC-D001 — one logical mutable data/token with multiple producers.

    LEGAL_MERGE (phi/merge constructs with proof, e.g. edge `data.flow ==
    merge` or an explicit merge flag) is not flagged; without proof the
    result is AMBIGUOUS_MULTI_PRODUCER (WARNING, never a race claim).
    """

    rule_id = "DRC-D001"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        if not data_sufficient(ctx):
            return [unknown_finding(
                ctx, self.rule_id,
                "Cannot verify multi-producer data: DATA capability is "
                f"{ctx.data_capability}.",
                "D001 needs resolved DATA edges; the frozen topology "
                "carries none for this lane.",
                coverage=ctx.data_capability)]
        by_consumer_token: dict[tuple, list[dict]] = {}
        for e in ctx.edges:
            if e.get("kind") != "DATA":
                continue
            if e.get("data", {}).get("flow") == "merge":
                continue                      # proven merge construct: LEGAL
            token = (e.get("data") or {}).get("token")
            if not token:
                continue
            by_consumer_token.setdefault((e.get("target"), token), []).append(e)
        for (tgt, token), edges in sorted(by_consumer_token.items()):
            producers = sorted({e.get("source") for e in edges})
            if len(producers) <= 1:
                continue
            merge = all((e.get("data") or {}).get("flow") == "merge" for e in edges)
            if merge:
                continue
            findings.append(ctx.make(
                self.rule_id, severity=Severity.WARNING,
                subject_ids=[tgt],
                related_ids=producers,
                message=f'Data token "{token}" has multiple producers '
                        f"({len(producers)}): {', '.join(sorted(producers)[:5])}"
                        f"{'…' if len(producers) > 5 else ''}.",
                why="No LEGAL_MERGE proof (phi/merge marker) for the logical "
                    "token; ambiguity is reported, race certainty is not "
                    "invented (D6 spirit).",
                truth_requirements=["resolved DATA edges",
                                    "merge proof absent"],
                truth_observed={"token": token, "producers": len(producers),
                                "merge_proof": False},
                witnesses=[w for e in edges
                           for w in e.get("representative_witnesses", [])][:5],
                source_locations=[e.get("source_span")
                                  for e in edges if e.get("source_span")][:5],
                coverage="COMPLETE", confidence=0.7,
                suggested_fix="merge the producers under an explicit "
                              "single-writer rule or a join point"))
        return findings


class DataflowUnsupported(Rule):
    """DRC-D002 — requested dataflow checks cannot be established because
    DATA capability is PARTIAL/UNKNOWN -> UNKNOWN (mandatory truth rule).
    """

    rule_id = "DRC-D002"

    def run(self, ctx: DrcContext) -> list:
        if data_sufficient(ctx):
            return []
        data_edges = sum(1 for e in ctx.edges if e.get("kind") == "DATA")
        status_note = ("no resolved DATA wires in the frozen topology"
                       if data_edges == 0 else
                       f"{data_edges} DATA wires exist but capability is "
                       f"{ctx.data_capability}")
        return [unknown_finding(
            ctx, self.rule_id,
            "Requested dataflow checks cannot be established: "
            f"DATA capability is {ctx.data_capability} ({status_note}).",
            "D002 is a mandatory truth rule: insufficient coverage yields "
            "UNKNOWN, never PASS (D2).",
            truth_observed={"data_capability": ctx.data_capability,
                            "data_edges": data_edges},
            suggested_fix="re-run the upstream dataflow analysis for this "
                          "lane before making data statements")]


class UnresolvedDataCrossing(Rule):
    """DRC-D003 — known DATA crossing a candidate boundary without a
    matching boundary port (MODULE-INFER boundary completeness logic)."""

    rule_id = "DRC-D003"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        if not data_sufficient(ctx):
            return [unknown_finding(
                ctx, self.rule_id,
                "Cannot verify data boundary completeness: DATA capability "
                f"is {ctx.data_capability}.",
                "D003 needs resolved DATA wires to see crossings.",
                coverage=ctx.data_capability)]
        if not ctx.suggestions:
            return []
        for sugg in ctx.suggestions:
            members = set(sugg.get("members", []))
            boundary = sugg.get("boundary") or {}
            data_in = {_tok(x) for x in boundary.get("data_in", [])}
            data_out = {_tok(x) for x in boundary.get("data_out", [])}
            for e in ctx.edges:
                if e.get("kind") != "DATA":
                    continue
                token = (e.get("data") or {}).get("token")
                if not token:
                    continue
                s, t = e.get("source"), e.get("target")
                if s in members and t not in members and token not in data_out:
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.WARNING,
                        subject_ids=[sugg.get("candidate_id", "")],
                        related_ids=[s, t],
                        message=f'DATA crossing "{token}" leaves the '
                                "candidate boundary without a matching "
                                "data_out port.",
                        why="Boundary completeness: produced data must be "
                            "exposed (MODULE-INFER boundary semantics).",
                        truth_requirements=["DATA edge crossing boundary",
                                            "boundary data_out"],
                        truth_observed={"token": token, "data_out": sorted(data_out)},
                        witnesses=e.get("representative_witnesses", []),
                        source_locations=[e.get("source_span")],
                        coverage=e.get("coverage", "UNKNOWN"), confidence=0.7,
                        suggested_fix="declare the data on the boundary "
                                      "outputs"))
                elif t in members and s not in members and token not in data_in:
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.WARNING,
                        subject_ids=[sugg.get("candidate_id", "")],
                        related_ids=[s, t],
                        message=f'DATA crossing "{token}" enters the '
                                "candidate boundary without a matching "
                                "data_in port.",
                        why="Boundary completeness: required data must be "
                            "exposed (MODULE-INFER boundary semantics).",
                        truth_requirements=["DATA edge crossing boundary",
                                            "boundary data_in"],
                        truth_observed={"token": token, "data_in": sorted(data_in)},
                        witnesses=e.get("representative_witnesses", []),
                        source_locations=[e.get("source_span")],
                        coverage=e.get("coverage", "UNKNOWN"), confidence=0.7,
                        suggested_fix="declare the input on the boundary"))
        return findings


def _tok(item) -> str:
    if isinstance(item, dict):
        return str(item.get("token") or item.get("name") or item.get("to") or "")
    return str(item)


ALL = [MultipleProducers, DataflowUnsupported, UnresolvedDataCrossing]
