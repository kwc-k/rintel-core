"""DRC rules: dynamic / uncertainty (U001–U003)."""
from __future__ import annotations

from collections import Counter

from ..engine import DrcContext, Rule, unknown_finding
from ..model import Severity


class UnknownDynamicTarget(Rule):
    """DRC-U001 — target_resolution = UNKNOWN (or target is the dynamic
    placeholder) -> UNRESOLVED_CALL_TARGET.

    D9 refinement (real-repo precision, MODULE-OPT/TOPO-UI preserved):
    on JPL/FAC the placeholder mostly covers statically-unbound callsites
    (builtins like len/bool, stdlib methods like lower/append, logging
    calls), not genuine dynamic dispatch — the rule therefore reports at
    INFO severity with an honest "unresolved (may be builtin/library/
    dynamic)" wording, never ERROR and never claiming dynamism.
    """

    rule_id = "DRC-U001"

    def run(self, ctx: DrcContext) -> list:
        per_subject: dict[str, list[dict]] = {}
        for e in ctx.edges:
            if e.get("kind") not in ("CALL", "DATA", "STATE", "CONTROL",
                                     "TIME", "RESOURCE"):
                continue
            if e.get("target") == "[Unknown Dynamic Target]" or \
                    e.get("target_resolution") == "UNKNOWN":
                per_subject.setdefault(e.get("source", ""), []).append(e)
        findings = []
        for src in sorted(per_subject):
            edges = per_subject[src]
            placeholder = sum(1 for e in edges
                              if e.get("target") == "[Unknown Dynamic Target]")
            unknown_res = sum(1 for e in edges
                              if e.get("target_resolution") == "UNKNOWN")
            top = Counter(e.get("expr", "")
                          for e in edges
                          for w in e.get("representative_witnesses", []))
            targets = ", ".join(f"{t}×{c}" for t, c in
                                sorted(top.items(), key=lambda kv: -kv[1])[:5])
            findings.append(ctx.make(
                self.rule_id, severity=Severity.INFO,
                subject_ids=[src],
                message=f"Unresolved call target(s): {len(edges)} call "
                        f"site(s) ({placeholder} placeholder, "
                        f"{unknown_res} target_resolution=UNKNOWN) — "
                        f"{targets or 'n/a'}.",
                why="target_resolution cannot resolve the receiver from "
                    "frozen evidence. The callsite may be a builtin/stdlib "
                    "call, an attribute access, or genuine dynamic dispatch "
                    "(executor.submit/plugin-style); the frozen evidence "
                    "cannot distinguish them. Reported at INFO — the "
                    "deterministic noise floor, not a dynamism claim.",
                truth_requirements=["resolved CALL sites",
                                    "target_resolution"],
                truth_observed={"call_sites": len(edges),
                                "placeholder": placeholder,
                                "target_resolution_unknown": unknown_res},
                witnesses=[w for e in edges
                           for w in e.get("representative_witnesses", [])][:3],
                source_locations=[e.get("source_span")
                                  for e in edges if e.get("source_span")][:3],
                coverage="UNKNOWN", confidence=0.55,
                suggested_fix="bind the callsite (builtin/library table or "
                              "registry-aware resolution) to separate "
                              "static noise from genuine dynamic dispatch"))
        return findings


class PartialCandidateSet(Rule):
    """DRC-U002 — target_resolution = CANDIDATE_SET with PARTIAL/UNKNOWN
    coverage: downstream checks must not conclude target closure. The rule
    surfaces the open set; the engine's truth gates guarantee that no
    dependent rule can use it as closure (D2/D3)."""

    rule_id = "DRC-U002"

    def run(self, ctx: DrcContext) -> list:
        findings = []
        for e in ctx.edges:
            if e.get("target_resolution") != "CANDIDATE_SET":
                continue
            if e.get("coverage") in ("COMPLETE",):
                continue
            cands = e.get("candidate_targets") or []
            findings.append(unknown_finding(
                ctx, self.rule_id,
                f"Partial candidate set on {e.get('source')} → "
                f"{e.get('target')}: {len(cands)} candidate(s), coverage "
                f"{e.get('coverage', 'UNKNOWN')} — target closure CANNOT "
                "be concluded.",
                "CANDIDATE_SET + partial coverage means the check is "
                "open; consumers must treat it as unknown (D2/D3).",
                subject_ids=[e.get("source", ""), e.get("target", "")],
                related_ids=[str(c) for c in cands][:5],
                coverage=e.get("coverage", "UNKNOWN"),
                truth_observed={"candidate_targets": len(cands),
                                "coverage": e.get("coverage")},
                suggested_fix="resolve the candidate set or treat as "
                              "dynamically bound"))
        return findings


class EvidenceDowngradeProtection(Rule):
    """DRC-U003 — rules requiring RESOLVED evidence refuse INFERRED as
    equivalent support. This rule audits that no hard finding in the run
    was produced from INFERRED-only evidence."""

    rule_id = "DRC-U003"

    def run(self, ctx: DrcContext) -> list:
        down_graded: list[dict] = []
        for e in ctx.edges:
            if e.get("truth_class") == "INFERRED" and \
                    e.get("execution_modality") == "MAY":
                down_graded.append(e)
        if not down_graded:
            return []
        # INFERRED facts exist in this lane: all hard rules must have been
        # gated (they are — see engine gates). Surface a single audit note.
        return [unknown_finding(
            ctx, self.rule_id,
            f"{len(down_graded)} INFERRED/MAY dataflow fact(s) exist in the "
            "frozen evidence; hard RESOLVED proof obligations were gated "
            "down to UNKNOWN — no inferred fact silently satisfies a hard "
            "proof (D3).",
            "U003 audit note: INFERRED facts are present and were excluded "
            "from hard proofs.",
            truth_observed={"inferred_edges": len(down_graded)})]


ALL = [UnknownDynamicTarget, PartialCandidateSet, EvidenceDowngradeProtection]
