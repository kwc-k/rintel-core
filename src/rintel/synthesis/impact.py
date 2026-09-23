"""SYNTHESIS0 impact analysis (spec §7): canonical topology-derived
affected symbols/files with honest coverage (PARTIAL never upgraded)."""
from __future__ import annotations

from typing import Any

from ..lvs.code_side import LvsCodeSide


def impact_of(binding: str | None, code: LvsCodeSide) -> dict[str, Any]:
    """Caller/callee impact of a change on `binding` from canonical CALLs."""
    out: dict[str, Any] = {
        "target": binding,
        "affected_symbols": [],
        "affected_files": [],
        "coverage": "COMPLETE",
        "uncertainty": [],
    }
    if not binding:
        out["coverage"] = "UNKNOWN"
        out["uncertainty"].append("unbound design object — no canonical impact")
        return out
    fn = code.functions.get(binding)
    if fn is None:
        out["coverage"] = "PARTIAL" if code.call_capability != "COMPLETE" else "COMPLETE"
        if code.call_capability != "COMPLETE":
            out["uncertainty"].append(
                "target is not in the current canonical topology (analyzer "
                "coverage PARTIAL)")
        return out

    callers: list[str] = []
    caller_files: set[str] = set()
    unresolved_callers = False
    for e in code.by_target.get(binding, []):
        if e.get("kind") != "CALL":
            continue
        src = e.get("source")
        if src and src in code.functions:
            callers.append(src)
            cf = code.functions[src].get("file")
            if cf:
                caller_files.add(cf)
        elif e.get("target_resolution") == "UNKNOWN" or \
                e.get("coverage") != "COMPLETE":
            unresolved_callers = True
    out["affected_symbols"] = sorted(callers)
    out["affected_files"] = sorted(caller_files)
    if code.call_capability != "COMPLETE" or unresolved_callers:
        out["coverage"] = "PARTIAL"
        out["uncertainty"].append(
            f"Known affected callers: {len(callers)}; dynamic unresolved "
            "callers may exist — coverage = PARTIAL. Impact is NOT claimed "
            "complete.")
    return out


def files_of(bindings: list[str], code: LvsCodeSide) -> list[str]:
    files = sorted({code.functions[b].get("file", "")
                    for b in bindings if b in code.functions
                    and code.functions[b].get("file")})
    return [f for f in files if f]
