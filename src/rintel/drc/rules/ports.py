"""DRC rules: interface / ports (P001–P004)."""
from __future__ import annotations

from ..engine import DrcContext, Rule, data_sufficient, evidence_ok, unknown_finding
from ..model import Severity


class MissingRequiredInput(Rule):
    """DRC-P001 — required input with no valid producer/binding.

    ERROR only when the requirement is deterministic (port evidence
    DECLARED/OBSERVED) and coverage is sufficient.  With DATA capability
    PARTIAL/UNKNOWN -> UNKNOWN (D2); INFERRED port evidence -> UNKNOWN (D3).
    """

    rule_id = "DRC-P001"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        ports = ctx.ports
        if ports is None:
            return []
        if not data_sufficient(ctx):
            return [unknown_finding(
                ctx, self.rule_id,
                "Cannot verify required inputs: DATA capability is "
                f"{ctx.data_capability}.",
                "P001 requires deterministic production evidence; frozen "
                "topology does not carry resolved DATA wires for this lane.",
                coverage=ctx.data_capability)]
        for cid in sorted(ctx.nodes):
            for port in ports.inputs(cid):
                if port.evidence == "INFERRED":
                    findings.append(unknown_finding(
                        ctx, self.rule_id,
                        f'Required input "{port.name}" evidence is INFERRED; '
                        "no RESOLVED producer proof.",
                        "INFERRED evidence cannot satisfy a hard P001 proof "
                        "obligation (U003).",
                        subject_ids=[cid],
                        truth_observed={
                            "port": port.name, "evidence": port.evidence,
                            "data_capability": ctx.data_capability}))
                    continue
                producers = [e for e in ctx.by_target.get(cid, [])
                             if e.get("kind") == "DATA"
                             and e.get("data", {}).get("token") == port.name]
                if producers:
                    continue
                # a producer edge may exist without a token; check any DATA in
                any_in = [e for e in ctx.by_target.get(cid, []) if e.get("kind") == "DATA"]
                if any_in and any_in[0].get("data", {}).get("token") == port.name:
                    continue
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.ERROR,
                    subject_ids=[cid],
                    message=f'Required input "{port.name}" has no resolved '
                            "producer.",
                    why="Static port evidence declares the input; no incoming "
                        "DATA edge produces it in the frozen topology.",
                    truth_requirements=["port evidence DECLARED/OBSERVED",
                                        "incoming DATA edge with matching token"],
                    truth_observed={"port": port.name,
                                    "data_inedges": len(any_in)},
                    witnesses=[], coverage="COMPLETE", confidence=0.9,
                    suggested_fix="provide a producer for the input or make "
                                  "the port optional"))
        return findings


class DanglingOutput(Rule):
    """DRC-P002 — known output with no consumer (INFO by default)."""

    rule_id = "DRC-P002"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        ports = ctx.ports
        if ports is None:
            return []
        if not data_sufficient(ctx):
            return [unknown_finding(
                ctx, self.rule_id,
                "Cannot verify output consumers: DATA capability is "
                f"{ctx.data_capability}.",
                "P002 requires resolved DATA wires to prove consumption.",
                coverage=ctx.data_capability)]
        for cid in sorted(ctx.nodes):
            for port in ports.outputs(cid):
                if port.evidence == "INFERRED":
                    continue
                consumers = [e for e in ctx.by_source.get(cid, [])
                             if e.get("kind") == "DATA"
                             and e.get("data", {}).get("token") == port.name]
                if not consumers and not any(
                        e.get("kind") == "DATA" for e in ctx.by_source.get(cid, [])):
                    findings.append(ctx.make(
                        self.rule_id, severity=Severity.INFO,
                        subject_ids=[cid],
                        message=f'Produced output "{port.name}" has no '
                                "consumer.",
                        why="Static evidence declares the output; no outgoing "
                            "DATA edge consumes it.",
                        truth_requirements=["output port declared",
                                            "consumer evidence"],
                        truth_observed={"port": port.name, "consumers": 0},
                        coverage="COMPLETE", confidence=0.6,
                        suggested_fix="confirm the output is intentional "
                                      "(library exports may have no in-repo "
                                      "consumer)"))
        return findings


class TypeMismatch(Rule):
    """DRC-P003 — producer/consumer port type incompatibility.

    Uses provider/native semantic type evidence ONLY (D4): unknown type ->
    UNKNOWN, never a fabricated mismatch.
    """

    rule_id = "DRC-P003"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        ports = ctx.ports
        if ports is None:
            return []
        seen: set[tuple] = set()
        for e in ctx.edges:
            if e.get("kind") != "DATA" or not evidence_ok(e):
                continue
            token = (e.get("data") or {}).get("token")
            src, tgt = e.get("source"), e.get("target")
            if not token or (src, tgt, token) in seen:
                continue
            seen.add((src, tgt, token))
            out_port = ports.resolve(src, token, "output") if src in ctx.nodes else None
            in_port = ports.resolve(tgt, token, "input") if tgt in ctx.nodes else None
            ta = out_port.type_ if out_port else None
            tb = in_port.type_ if in_port else None
            if ta is None or tb is None:
                if (ta is None or tb is None) and (out_port or in_port):
                    findings.append(unknown_finding(
                        ctx, self.rule_id,
                        "Cannot verify type compatibility: "
                        f"producer type={ta!r} consumer type={tb!r} "
                        "(unknown side).",
                        "P003 uses provider/native semantic type evidence "
                        "only; unknown type is reported UNKNOWN, never a "
                        "mismatch (D4).",
                        subject_ids=[src, tgt],
                        related_ids=[token]))
                continue
            if _incompatible(ta, tb):
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.ERROR,
                    subject_ids=[src, tgt],
                    message=f'Type mismatch on "{token}": producer {ta} '
                            f"vs consumer {tb}.",
                    why="Provider semantic types are known and incompatible.",
                    truth_requirements=["both end types known from provider "
                                        "semantic evidence"],
                    truth_observed={"producer_type": ta, "consumer_type": tb},
                    witnesses=e.get("representative_witnesses", []),
                    source_locations=[e.get("source_span")],
                    coverage=e.get("coverage", "UNKNOWN"), confidence=0.9,
                    suggested_fix="align the port types or insert an "
                                  "explicit conversion"))
        return findings


def _incompatible(a: str, b: str) -> bool:
    """Conservative, provable incompatibility only."""
    if a == b:
        return False
    if a == "str" and b in ("int", "float", "list"):
        return True
    if a == "int" and b == "str" and False:   # Python allows str(int)
        return True
    if a.endswith("*") or b.endswith("*"):    # C pointer arithmetic is loose
        return False
    return False


class DirectionViolation(Rule):
    """DRC-P004 — direction violation proven by language semantics
    (e.g. Fortran intent(in) written / OUTPUT->OUTPUT wiring)."""

    rule_id = "DRC-P004"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        ports = ctx.ports
        if ports is None:
            return []
        for e in ctx.edges:
            if e.get("kind") != "DATA":
                continue
            src, tgt = e.get("source"), e.get("target")
            if src not in ctx.nodes or tgt not in ctx.nodes:
                continue
            token = (e.get("data") or {}).get("token")
            if not token:
                continue
            out_port = ports.resolve(src, token, "output")
            in_port = ports.resolve(tgt, token, "input")
            out_dir = out_port.direction if out_port else None
            in_dir = in_port.direction if in_port else None
            # intent(in) written: the producer side is an input port
            if in_dir == "input" and out_dir == "input":
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.ERROR,
                    subject_ids=[src, tgt],
                    message=f'Direction violation on "{token}": an input '
                            "port is used as a producer "
                            "(e.g. intent(in) written).",
                    why="Port directions are known from language semantics "
                        "(Fortran intent / declared direction).",
                    truth_requirements=["port direction evidence"],
                    truth_observed={"producer_direction": "input",
                                    "consumer_direction": "input"},
                    witnesses=e.get("representative_witnesses", []),
                    source_locations=[e.get("source_span")],
                    coverage=e.get("coverage", "UNKNOWN"), confidence=0.85,
                    suggested_fix="do not write inputs; pass a copy or "
                                  "declare a separate inout port"))
            elif out_dir == "output" and in_dir == "output":
                findings.append(ctx.make(
                    self.rule_id, severity=Severity.ERROR,
                    subject_ids=[src, tgt],
                    message=f'Direction violation on "{token}": OUTPUT -> '
                            "OUTPUT wiring.",
                    why="Both endpoints are outputs; a data edge must "
                        "connect output -> input.",
                    truth_requirements=["port direction evidence"],
                    truth_observed={"producer_direction": "output",
                                    "consumer_direction": "output"},
                    witnesses=e.get("representative_witnesses", []),
                    source_locations=[e.get("source_span")],
                    coverage=e.get("coverage", "UNKNOWN"), confidence=0.85))
        return findings


ALL = [MissingRequiredInput, DanglingOutput, TypeMismatch, DirectionViolation]
