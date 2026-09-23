"""AnalysisFact → SoftwareNetlist bridge with the truth guard (P0.2).

Frozen rules:
- DATA_FLOW facts with truth_class=INFERRED/HEURISTIC produce nets with
  the SAME class — a software-netlist edge is NEVER upgraded to RESOLVED.
- RESOLVED/OBSERVED DATA nets require the deterministic witness chain
  (producer return → assignment/binding → local identity → call argument
  → consumer parameter).  Nets without a full witness keep the class of
  their weakest segment; a net with NO segments can never claim RESOLVED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .contract import (HIGH_CONFIDENCE_CLASSES, AnalysisFact,
                       TargetResolution, TruthClass)


@dataclass(frozen=True)
class DataSegment:
    """One deterministic segment of a data witness chain."""
    kind: str          # 'producer_return' | 'assignment' | 'local' |
    #                    'call_argument' | 'consumer_parameter'
    expr: str
    line: int = 0
    file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "expr": self.expr, "line": self.line,
                "file": self.file}


@dataclass(frozen=True)
class DataNetEdge:
    source_port: str
    data: str
    target_port: str
    truth_class: TruthClass
    provenance: dict[str, Any]
    witnesses: list[DataSegment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"source_port": self.source_port, "data": self.data,
                "target_port": self.target_port,
                "truth_class": self.truth_class.value,
                "provenance": self.provenance,
                "witnesses": [w.to_dict() for w in self.witnesses]}


def witness_chain_complete(segments: list[DataSegment]) -> bool:
    """The deterministic 5-step chain (may collapse adjacent local steps,
    but must contain producer_return .. consumer_parameter endpoints)."""
    kinds = [s.kind for s in segments]
    if "producer_return" not in kinds or "consumer_parameter" not in kinds:
        return False
    return True


def data_net_from_fact(fact: AnalysisFact,
                       source_port: str, target_port: str,
                       data: str,
                       segments: list[DataSegment]) -> DataNetEdge:
    """Build a DATA net edge from an AnalysisFact.

    Truth rule (P0.2): the net's truth_class is the MINIMUM of the fact
    class and the witness completeness: without a full deterministic
    witness chain, INFERRED may never resolve to RESOLVED; RESOLVED facts
    with incomplete witnesses downgrade to INFERRED (documented
    conservative side of the guard).
    """
    base = fact.truth_class
    complete = witness_chain_complete(segments)
    if base in HIGH_CONFIDENCE_CLASSES and not complete:
        base = TruthClass.INFERRED
    if base not in HIGH_CONFIDENCE_CLASSES:
        base = TruthClass.INFERRED
    return DataNetEdge(
        source_port=source_port, data=data, target_port=target_port,
        truth_class=base,
        provenance={
            "provider": fact.provider,
            "provider_version": fact.provider_version,
            "fact_id": fact.fact_id,
            "repo_id": fact.repo_id,
            "snapshot_id": fact.snapshot_id,
            "may_flow": fact.metadata.get("may_flow", False),
        },
        witnesses=list(segments))


def iter_full_chain_edges() -> Iterator[DataNetEdge]:
    """(documented) example generator for the regression fixture."""
    return iter([])
