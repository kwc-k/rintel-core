"""SOFTWARE-LVS1 engine: normalize inputs, run comparators, order diffs."""
from __future__ import annotations

from typing import Any

from ..lvs.code_side import LvsCodeSide, LvsDesign, resolve_code, resolve_design
from ..lvs.compare import ALL_COMPARATORS
from ..lvs.model import DIFF_KINDS, LvsResult


def run_lvs(design_raw: dict, code_raw: dict) -> LvsResult:
    design = resolve_design(design_raw)
    code = resolve_code(code_raw)

    diffs = []
    for cmp in ALL_COMPARATORS:
        diffs.extend(cmp(design, code))

    # deterministic ordering: kind order, then status, then object
    kind_rank = {k: i for i, k in enumerate(DIFF_KINDS)}
    status_rank = {"MISMATCH": 0, "STALE": 1, "UNBOUND": 2, "UNKNOWN": 3,
                   "MATCH": 4, "CODE_ONLY": 5, "DESIGN_ONLY": 6,
                   "BOUNDARY_EQUIVALENT": 7}
    diffs.sort(key=lambda d: (kind_rank.get(d.kind, 99),
                              status_rank.get(d.status.value, 99),
                              d.design_object, str(d.code_object or "")))

    result = LvsResult(
        design_snapshot=design.snapshot_id,
        code_snapshot=code.snapshot_id,
        design_summary={
            "blocks": len(design.blocks),
            "composites": len(design.composites),
            "ports": len(design.ports),
            "nets": len(design.nets),
            "claims": len(design.claims),
        },
        code_summary={
            "functions": len(code.functions),
            "edges": len(code.edges),
            "baseline_id": code.baseline_id,
        },
        diffs=diffs,
    )
    return result


def lvs_to_json(result: LvsResult) -> dict[str, Any]:
    return result.to_dict()
