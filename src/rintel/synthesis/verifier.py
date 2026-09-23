"""SYNTHESIS0 verifier (spec §15–§17, §18, §22): pre/post DRC + LVS gates,
apply via the existing writeback boundary, honest statuses and rollback
reporting.  Canonical truth only changes via Reindex (§14/§21)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..drc.engine import DrcContext, run_engine
from ..drc.rules import RULES
from ..lvs.engine import run_lvs
from ..lvs.model import LvsStatus
from ..lvs.signatures import build_code_side_from_rows
from .model import FilePatch, SynthesisPlan, SynthesisResult, SynthesisStatus
from .patcher import GeneratedPatches


def design_drc_gate(to_be: dict, code: dict) -> dict:
    """DRC over the TO-BE design (design-side view + CandidateBoundary
    suggestions).  Blocking = deterministic DRC ERROR or a boundary-hiding
    finding (P001/P003/P004/C002/R002/B003/S003/B002) — §15 blocks
    synthesis of a known-illegal design; WARNING/UNKNOWN pass with risk.

    `code` supplies lanes capability; the design view comes from `to_be`.
    """
    from ..drc.engine import DrcContext, run_engine as _run
    from ..drc.model import Severity

    nodes = []
    blocks = {b["id"]: b for b in (to_be.get("blocks") or [])}
    for b in (to_be.get("blocks") or []):
        if not b.get("binding"):
            continue
        nodes.append({
            "canonical_symbol_id": b["binding"],
            "name": b.get("name", b["binding"]),
            "file": _file_of(b["binding"], code),
            "language": _lang_of(b["binding"], code),
            "resources": list(b.get("resources", []) or []),
        })
    edges = []
    for n in (to_be.get("nets") or []):
        kind = n.get("kind", "data")
        kind = "CALL" if kind in ("control", "call") else \
            ("RESOURCE" if kind == "resource" else
             ("DATA" if kind == "data" else kind))
        src_b = blocks.get(n.get("source_block_id"))
        tgt = n.get("target_block_id")
        tgt_b = blocks.get(tgt)
        edges.append({
            "kind": kind,
            "source": src_b.get("binding") if src_b else None,
            "target": tgt_b.get("binding") if tgt_b else str(tgt),
            "truth_class": "OBSERVED",
            "execution_modality": "MUST",
            "target_resolution": "EXACT",
            "coverage": "COMPLETE",
        })
    edges = [e for e in edges if e["source"]]

    suggestions = []
    for comp in (to_be.get("composites") or []):
        member_bindings = []
        for bid in comp.get("block_ids", []):
            b = blocks.get(bid)
            if b and b.get("binding"):
                member_bindings.append(b["binding"])
        suggestions.append({
            "candidate_id": f"tobe-{comp.get('id', 'c')}",
            "members": member_bindings,
            "boundary": {
                "data_in": comp.get("data_in", []) or [],
                "data_out": comp.get("data_out", []) or [],
                "state_inout": comp.get("state_inout", []) or [],
                "resource_ports": [{"resource": r} for r in
                                   (comp.get("resources", []) or [])],
                "calls_in": [], "calls_out": [],
            },
        })
    ctx = DrcContext(
        lane="tobe",
        topology={"nodes": nodes, "edges": edges},
        data_capability=code.get("data_capability", "PARTIAL"),
        suggestions=suggestions,
        environment=code.get("environment", {"name": "tobe",
                                             "resources": {}}),
    )
    run = _run(ctx, RULES)
    blocking_rules = {"DRC-P001", "DRC-P003", "DRC-P004", "DRC-C002",
                      "DRC-R002", "DRC-B003", "DRC-S003", "DRC-B002"}
    errors = [f.to_dict() for f in run.findings
              if f.severity is Severity.ERROR or
              (f.severity is Severity.WARNING and
               f.rule_id in blocking_rules)]
    return {
        "blocked": bool(errors),
        "errors": errors,
        "warnings": [f.to_dict() for f in run.findings
                     if f.severity is Severity.WARNING][:10],
        "message": ("TO-BE design has a blocking DRC finding: " +
                    "; ".join(f["message"] for f in errors[:3]))
        if errors else "TO-BE DRC gate clean",
    }


def _file_of(binding: str, code: dict) -> str:
    f = (code.get("functions") or {}).get(binding)
    return (f or {}).get("file", "")


def _lang_of(binding: str, code: dict) -> str:
    f = (code.get("functions") or {}).get(binding)
    return (f or {}).get("language", "python")


def within(code: dict) -> list[dict]:
    """code-side functions -> node-shaped rows for DrcContext."""
    nodes = []
    for cid, f in (code.get("functions") or {}).items():
        nodes.append({
            "canonical_symbol_id": cid,
            "name": f.get("name", cid),
            "file": f.get("file", ""),
            "language": f.get("language", "python"),
            "resources": [],
        })
    return nodes


def pre_lvs(to_be: dict, code: dict) -> dict:
    result = run_lvs(to_be, code)
    return result.to_dict()


def apply_patches(root: str, patches: list[FilePatch],
                  reindex_cb) -> dict:
    """Apply FilePatch[] through the writeback boundary: writes go to the
    working tree; reindex via the callback; canonical truth only after.

    `reindex_cb` returns the new snapshot id.
    """
    rootp = Path(root)
    touched = []
    for p in patches:
        full = rootp / p.path
        if p.operation == "CREATE":
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("".join(e.text for e in p.edits))
        elif p.operation == "MODIFY":
            before = full.read_text() if full.is_file() else ""
            if p.before_hash and hashlib.sha1(
                    before.encode()).hexdigest() != p.before_hash:
                return {"ok": False,
                        "reason": f"before_hash mismatch on {p.path} — "
                                  "working tree changed between plan and "
                                  "apply (no write performed)"}
            lines = before.splitlines(keepends=True)
            if not lines and before:
                lines = [before]
            # apply edits sorted; edits are line-based replacements/inserts
            for e in sorted(p.edits, key=lambda x: (x.at_line or 0)):
                idx = (e.at_line or 1) - 1
                if e.kind == "insert":
                    lines.insert(min(idx, len(lines)), e.text)
                elif e.kind == "replace":
                    if 0 <= idx < len(lines):
                        lines[idx] = e.text
                elif e.kind == "delete":
                    if 0 <= idx < len(lines):
                        lines.pop(idx)
            full.write_text("".join(lines))
        touched.append(p.path)
    new_sid = reindex_cb()
    return {"ok": True,
            "files_touched": touched,
            "new_snapshot": new_sid}


def verify_after(new_code: dict, to_be: dict, before_drc: dict,
                 before_lvs: dict) -> dict:
    """Post gates: no new deterministic DRC ERROR; LVS of the target
    design reaches MATCH (or honest UNKNOWN)."""
    gate = design_drc_gate(to_be, new_code)
    new_errors = [e for e in gate.get("errors", [])]
    prev_error_keys = {(e.get("rule_id"), tuple(e.get("subject_ids", [])))
                       for e in before_drc.get("errors", [])}
    added = [e for e in new_errors
             if (e.get("rule_id"), tuple(e.get("subject_ids", [])))
             not in prev_error_keys]
    lvs = run_lvs(to_be, new_code)
    status = lvs.overall_status
    mismatch_kinds = sorted({d.kind for d in lvs.diffs
                             if d.status is LvsStatus.MISMATCH})
    ok = (not added and
          status in (LvsStatus.MATCH, LvsStatus.UNKNOWN))
    return {
        "new_drc_errors": [e["rule_id"] + ": " + e["message"][:80]
                           for e in added],
        "drc_errors_total": len(new_errors),
        "lvs_overall": status.value,
        "lvs_by_status": lvs.by_status(),
        "mismatch_kinds": mismatch_kinds,
        "acceptable": ok,
    }


def make_result(status: SynthesisStatus, *, plan=None, gen: GeneratedPatches | None = None,
                apply_result=None, post=None, failure=None,
                patches=None) -> SynthesisResult:
    return SynthesisResult(
        status=status,
        plan=plan,
        patches=patches or (gen.patches if gen else []),
        preview=gen.preview if gen else {},
        apply_result=apply_result or {},
        post_drc=post.get("drc", {}) if post else {},
        post_lvs=post.get("lvs", {}) if post else {},
        failure=failure or {},
    )
