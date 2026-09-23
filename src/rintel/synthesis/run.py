"""SYNTHESIS0 orchestrator (spec §1 flow, §28 statuses, §14 writeback reuse).

    build_plan(...)  -> SynthesisPlan (pre DRC + pre LVS attached)
    generate(...)    -> patches + preview (no working-tree change)
    apply(...)       -> write via writeback boundary + reindex
    verify(...)      -> post DRC + LVS -> VERIFIED / PARTIAL / FAILED
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from ..lvs.code_side import LvsCodeSide
from .diff import design_diff
from .model import (FilePatch, SynthesisPlan, SynthesisResult,
                    SynthesisStatus)
from .patcher import generate_patches
from .planner import build_plan
from .verifier import apply_patches, design_drc_gate, pre_lvs, verify_after


def code_side_to_dict(code: LvsCodeSide) -> dict[str, Any]:
    return {
        "snapshot_id": code.snapshot_id,
        "baseline_id": code.baseline_id,
        "functions": code.functions,
        "edges": code.edges,
        "resources": code.resources,
        "call_capability": getattr(code, "call_capability", "COMPLETE"),
        "data_capability": getattr(code, "data_capability", "COMPLETE"),
    }


def synthesize_plan(as_is: dict, to_be: dict, code: LvsCodeSide, *,
                    design_snapshot: str | None = None,
                    code_snapshot: str | None = None,
                    environment: dict | None = None) -> SynthesisPlan:
    diffs = design_diff(as_is, to_be)
    code_d = code_side_to_dict(code)
    if environment:
        code_d["environment"] = environment
    drc = design_drc_gate(to_be, code_d)
    lvs = pre_lvs(to_be, code_d)
    plan = build_plan(diffs, as_is, to_be, code,
                      design_snapshot=design_snapshot,
                      code_snapshot=code_snapshot,
                      pre_drc=drc, pre_lvs=lvs)
    return plan


def synthesize_patches(plan: SynthesisPlan, code: LvsCodeSide,
                       root: str, to_be: dict):
    gen = generate_patches(plan, code, root, to_be)
    return gen


def _partial_only(post: dict) -> bool:
    """Honest PARTIAL: the only unsatisfied gate is a resource claim whose
    wiring was injected (parameter/import) but the analyzer cannot prove
    the resource edge — truth preserved (PARTIAL, not PASS)."""
    if post.get("acceptable"):
        return False
    if post.get("lvs_overall") != "MISMATCH":
        return False
    kinds = post.get("mismatch_kinds", [])
    return bool(kinds) and set(kinds) <= {"resource", "boundary"}


def synthesize_apply(plan: SynthesisPlan, patches: list[FilePatch],
                     to_be: dict, root: str, reindex_cb: Callable[[], str],
                     new_code_cb: Callable[[str], LvsCodeSide]) -> SynthesisResult:
    """Apply + reindex + re-verify (§17/§18).  On failure: FAILED with the
    working-tree diff and reason (rollback = discard in disposable
    worktree; recorded here)."""
    apply_res = apply_patches(root, patches, reindex_cb)
    if not apply_res.get("ok"):
        return SynthesisResult(
            status=SynthesisStatus.FAILED,
            plan=plan, patches=patches,
            failure={"reason": apply_res.get("reason"),
                     "working_tree_diff": None})
    new_sid = apply_res["new_snapshot"]
    try:
        new_code = new_code_cb(new_sid)
    except Exception as exc:      # noqa: BLE001
        return SynthesisResult(
            status=SynthesisStatus.FAILED,
            plan=plan, patches=patches,
            apply_result=apply_res,
            failure={"reason": f"post-apply code side failed: {exc}",
                     "new_snapshot": new_sid})
    verified_design = resolve_design_bindings(to_be, new_code, patches)
    post = verify_after(code_side_to_dict(new_code), verified_design,
                        plan.pre_drc, plan.pre_lvs)
    status = (SynthesisStatus.VERIFIED if post["acceptable"]
              else SynthesisStatus.PARTIAL
              if _partial_only(post) else SynthesisStatus.FAILED)
    return SynthesisResult(
        status=status,
        plan=plan, patches=patches,
        apply_result=apply_res,
        post_drc={"errors_total": post["drc_errors_total"],
                  "new_errors": post["new_drc_errors"]},
        post_lvs={"overall": post["lvs_overall"],
                  "by_status": post["lvs_by_status"]},
        failure={} if status is SynthesisStatus.VERIFIED else {
            "reason": "post gates not acceptable",
            "post": post})


def resolve_design_bindings(to_be: dict, new_code: LvsCodeSide,
                            patches: list[FilePatch]) -> dict:
    """The post-synthesis design contract: proposed blocks that now have a
    real canonical symbol become existing+bound (canonical identity from
    the reindexed code side — never fabricated)."""
    import copy
    design = copy.deepcopy(to_be)
    created = {}
    for p in patches:
        if p.operation != "CREATE":
            continue
        for e in p.edits:
            name = e.provenance.get("target_symbol")
            if name:
                created.setdefault(name, p.path)
    for b in design.get("blocks", []):
        if b.get("state") != "proposed" or b.get("binding"):
            continue
        name = b.get("name", "")
        path = created.get(name)
        cid = _find_binding(new_code, name, path)
        if cid:
            b["state"] = "existing"
            b["binding"] = cid
    return design


def _find_binding(new_code: LvsCodeSide, name: str,
                  file: str | None) -> str | None:
    for cid, fn in new_code.functions.items():
        if fn.get("name") == name and (not file or fn.get("file") == file):
            return cid
    return None


def patch_diff_text(patches: list[FilePatch], root: str) -> str:
    """Unified-ish diff text for preview/evidence (never writes)."""
    from pathlib import Path
    rootp = Path(root)
    out = []
    for p in patches:
        lines = []
        try:
            before = (rootp / p.path).read_text() \
                if (rootp / p.path).is_file() else ""
        except OSError:
            before = ""
        after = "".join(e.text for e in p.edits) if p.operation == "CREATE" \
            else _apply_on_text(before, p)
        out.append(f"--- {p.path} ({p.operation})\n")
        out.append(f"+++ {p.path}\n")
        out.extend(_unified(before, after))
        out.append("\n")
    return "".join(out)


def _apply_on_text(before: str, p: FilePatch) -> str:
    lines = before.splitlines(keepends=True)
    for e in sorted(p.edits, key=lambda x: (x.at_line or 0)):
        idx = (e.at_line or 1) - 1
        if e.kind == "insert":
            lines.insert(min(idx, len(lines)), e.text)
        elif e.kind == "replace" and 0 <= idx < len(lines):
            lines[idx] = e.text
        elif e.kind == "delete" and 0 <= idx < len(lines):
            lines.pop(idx)
    return "".join(lines)


def _unified(before: str, after: str):
    """Cheap line diff for preview: emits hunks as +/-> lines (deltas)."""
    bl = before.splitlines(keepends=True)
    al = after.splitlines(keepends=True)
    import difflib
    return list(difflib.unified_diff(bl, al, fromfile="before", tofile="after",
                                     lineterm=""))
