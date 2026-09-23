"""SYNTHESIS0 planner (spec §6/§8): design diff + canonical impact ->
implementation plan.  Renderer-neutral; the plan is the ONLY base for
patches (no free-form architecture)."""
from __future__ import annotations

from typing import Any

from ..lvs.code_side import LvsCodeSide
from .diff import classify_change
from .impact import files_of, impact_of
from .model import PlanChange, SynthesisPlan, SynthesisStatus

# design-change -> synthesis rule ids (spec §10, each mapping has a rule id)
RULE_BY_TYPE = {
    "S1": "SYN-S1-ADD_SYMBOL",
    "S2": "SYN-S2-MODIFY_SIGNATURE",
    "S3": "SYN-S3-ADD_CALLSITE",
    "S4": "SYN-S4-REMOVE_CALLSITE",
    "S5": "SYN-S5-ADD_DATA_BINDING",
    "S6": "SYN-S6-MODIFY_DATA_WIRING",
    "S7": "SYN-S7-RESOURCE_WIRING",
    "S8": "SYN-S8-COMPOSITE_BOUNDARY",
}

UNSUPPORTED_DESCRIPTION = {
    "S1": "added symbol without a design binding",
    "S4": "block removal (complex deletion)",
    "S6": "rewiring data nets beyond the bounded cache pattern",
    "S7": "resource wiring beyond constructor/parameter injection",
    "S8": "composite boundary beyond exposed-parameter wiring",
}


def build_plan(design_diff: list[dict], as_is: dict, to_be: dict,
               code: LvsCodeSide, *,
               design_snapshot: str | None,
               code_snapshot: str | None,
               pre_drc: dict | None = None,
               pre_lvs: dict | None = None) -> SynthesisPlan:
    changes: list[PlanChange] = []
    index = 0
    for diff in design_diff:
        index += 1
        cid = f"C{index}"
        stype = classify_change(diff, as_is, to_be)
        rule = RULE_BY_TYPE.get(stype, "SYN-S1-ADD_SYMBOL")
        supported, reason = _supportability(stype, diff)
        target_binding = _binding_of(diff, to_be)
        imp = impact_of(target_binding, code) if supported else {
            "target": target_binding, "affected_symbols": [],
            "affected_files": [], "coverage": "UNKNOWN",
            "uncertainty": ["unsupported transform — PLAN_ONLY"]}
        steps = _steps_for(stype, diff, imp, to_be)
        files = imp["affected_files"]
        if target_binding and code.functions.get(target_binding, {}).get("file"):
            files = sorted(set(files) | {
                code.functions[target_binding]["file"]})
        dep = _deps_for(stype, diff)
        changes.append(PlanChange(
            change_id=cid,
            design_diff={"kind": diff["kind"],
                         "design_object": diff["design_object"],
                         "detail": diff.get("detail", {}),
                         "as_is": diff.get("as_is"),
                         "to_be": diff.get("to_be")},
            synthesis_type=stype,
            supported=supported,
            affected_symbols=imp["affected_symbols"] + ([target_binding] if target_binding and supported else []),
            affected_files=files,
            dependencies=dep,
            uncertainty={"coverage": imp["coverage"],
                         "detail": imp.get("uncertainty", [])},
            implementation_steps=steps,
            witnesses=_witnesses_of(diff),
            reason=reason,
        ))

    status = SynthesisStatus.PLAN_READY
    risks: list[str] = []
    if pre_drc and pre_drc.get("blocked"):
        status = SynthesisStatus.BLOCKED
        risks.append(pre_drc.get("message", "TO-BE design has a DRC ERROR"))
    if any(not c.supported for c in changes):
        status = SynthesisStatus.PLAN_ONLY if status != SynthesisStatus.BLOCKED else status
        risks.append("one or more changes are unsupported → PLAN_ONLY, no patch")
    if any(c.uncertainty.get("coverage") == "PARTIAL" for c in changes):
        risks.append("impact coverage is PARTIAL: dynamic unresolved callers "
                     "may remain unmodified (truth preserved, §25)")
    if not changes:
        status = SynthesisStatus.NO_CHANGES

    plan = SynthesisPlan(
        design_snapshot=design_snapshot,
        code_snapshot=code_snapshot,
        changes=changes,
        file_plan=_file_plan(changes),
        risk_summary=risks,
        expected_lvs_delta=_expected_lvs(changes),
        pre_drc=pre_drc or {},
        pre_lvs=pre_lvs or {},
        status=status,
    )
    return plan


def _supportability(stype: str, diff: dict) -> tuple[bool, str]:
    kind = diff["kind"]
    block = diff.get("to_be") or {}
    if stype == "S1":
        # added symbols are synthesizable for function blocks (the
        # patcher falls back to PLAN_ONLY when it cannot generate them).
        if block.get("kind") in ("composite", "object") or \
                block.get("kind") == "composite":
            return False, UNSUPPORTED_DESCRIPTION["S1"]
        return True, "TO-BE adds a function symbol with a defined design " \
                     "contract"
    if stype == "S4" and kind in ("block_removed",):
        return False, UNSUPPORTED_DESCRIPTION["S4"]
    if stype == "S6" and kind == "net_removed":
        return False, UNSUPPORTED_DESCRIPTION["S6"]
    if stype == "S7":
        return True, "resource wiring via constructor/parameter injection"
    if stype == "S8":
        return True, "bounded composite boundary: external data_in ports " \
                     "injected on the entry member"
    return True, "bounded transform"


def _binding_of(diff: dict, to_be: dict) -> str | None:
    block = diff.get("to_be") or diff.get("as_is") or {}
    if diff["kind"].startswith("port"):
        port = diff.get("detail", {}).get("port") or block
        for b in to_be.get("blocks", []):
            if b.get("id") == (port.get("block_id") if
                               isinstance(port, dict) else None):
                return b.get("binding")
        return None
    if diff["kind"].startswith("block"):
        return block.get("binding")
    if diff["kind"] == "net_added":
        net = diff.get("to_be") or {}
        src = _block(to_be, net.get("source_block_id"))
        return (src or {}).get("binding")
    if diff["kind"] == "net_removed":
        net = diff.get("as_is") or {}
        src = _block(to_be, net.get("source_block_id"))
        return (src or {}).get("binding")
    if diff["kind"] == "resource_added":
        return (block.get("binding") if isinstance(block, dict) else None)
    return None


def _block(to_be: dict, bid) -> dict | None:
    return next((b for b in to_be.get("blocks", []) if b.get("id") == bid), None)


def _steps_for(stype: str, diff: dict, imp: dict, to_be: dict) -> list[str]:
    obj = diff["design_object"]
    steps = [f"{diff['kind']}: {obj}"]
    if stype == "S1":
        b = diff.get("to_be") or {}
        steps.append(f"create implementation symbol for {b.get('name', obj)} "
                     "with the designed ports")
    elif stype == "S2":
        steps.append("modify the target signature (add/rename/remove "
                     "parameter)")
        steps.append("update known callers: " +
                     ", ".join(imp.get("affected_symbols", [])[:5]) +
                     (f" (+{len(imp.get('affected_symbols', [])) - 5} more)"
                      if len(imp.get("affected_symbols", [])) > 5 else ""))
    elif stype == "S3":
        steps.append("insert a real callsite in the caller's body")
    elif stype == "S4":
        steps.append("remove the corresponding implementation callsite")
    elif stype == "S5":
        steps.append("create a real data binding (argument/assignment) — "
                     "not just a call")
    elif stype == "S6":
        steps.append("reroute the data flow through the new intermediate "
                     "and bind outputs to the consumer input")
    elif stype == "S7":
        steps.append("inject the resource (constructor/parameter/import)")
    elif stype == "S8":
        steps.append("wire the exposed boundary port to the entry member")
    return steps


def _deps_for(stype: str, diff: dict) -> list[str]:
    if stype in ("S3", "S5", "S6"):
        # rely on the added symbol's change id (computed by planner caller)
        return []
    return []


def _witnesses_of(diff: dict) -> list[dict]:
    w = []
    block = diff.get("to_be") or diff.get("as_is") or {}
    if isinstance(block, dict) and block.get("witnesses"):
        w = block["witnesses"]
    return w


def _file_plan(changes: list[PlanChange]) -> list[dict]:
    files: dict[str, dict[str, Any]] = {}
    for c in changes:
        for f in c.affected_files:
            files.setdefault(f, {"path": f,
                                 "operation": "MODIFY",
                                 "changes": []})
            files[f]["changes"].append(c.change_id)
    out = sorted(files.values(), key=lambda x: x["path"])
    for f in out:
        f["operation"] = "CREATE" if not f["path"] else "MODIFY"
    return out


def _expected_lvs(changes: list[PlanChange]) -> list[str]:
    out = []
    for c in changes:
        if c.synthesis_type == "S1":
            out.append(f"{c.change_id}: block UNBOUND → MATCH after reindex "
                       "(new canonical symbol)")
        elif c.synthesis_type in ("S3", "S5", "S6"):
            out.append(f"{c.change_id}: designed relation → MATCH after "
                       "reindex (real source callsite/binding)")
        elif c.synthesis_type == "S2":
            out.append(f"{c.change_id}: port diffs → MATCH; callers updated "
                       f"(coverage {c.uncertainty.get('coverage')})")
        elif c.synthesis_type == "S4":
            out.append(f"{c.change_id}: designed call removed → design no "
                       "longer claims it")
        else:
            out.append(f"{c.change_id}: {c.synthesis_type} — "
                       "PLAN_ONLY, no LVS delta")
    return out
