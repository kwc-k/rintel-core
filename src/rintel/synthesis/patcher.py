"""SYNTHESIS0 patcher (spec §11–§13): bounded Python code generation.

Only the target symbols/regions are edited (minimal patch, no incidental
refactoring).  Every edit carries provenance (§27) so generated lines can
be traced back to design changes.  Python-only production; anything outside
the bounded transforms is reduced to PLAN_ONLY (no patch fabricated).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..lvs.code_side import LvsCodeSide
from .model import FileEdit, FilePatch, PlanChange, SynthesisPlan


def _snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower().replace(".", "_")
    return re.sub(r"[^a-z0-9_]", "_", s)


def _module_path(root: Path, file: str) -> Path:
    return root / file


def _file_lines(root: Path, file: str) -> list[str]:
    p = _module_path(root, file)
    try:
        return p.read_text().splitlines(keepends=True)
    except OSError:
        return []


@dataclass
class GeneratedPatches:
    patches: list[FilePatch] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)
    plan_only: list[str] = field(default_factory=list)


def generate_patches(plan: SynthesisPlan, code: LvsCodeSide,
                     root: str, to_be: dict) -> GeneratedPatches:
    gen = GeneratedPatches()
    if plan.changes is None:
        return gen
    rootp = Path(root)
    patch_key = hashlib.sha1(
        ("|".join(c.change_id for c in plan.changes)).encode()).hexdigest()[:8]

    # group file-level work
    file_work: dict[str, dict[str, Any]] = {}
    for c in plan.changes:
        if not c.supported:
            gen.plan_only.append(
                f"{c.change_id}: {c.synthesis_type} PLAN_ONLY "
                f"({c.reason})")
            continue
        _apply_change(c, code, to_be, file_work, rootp, patch_key, gen)

    for path in sorted(file_work):
        info = file_work[path]
        edits = info["edits"]
        if not edits:
            continue
        if info["create"]:
            lines = []
            for e in edits:
                lines.append(e.text)
            before = None
            gen.patches.append(FilePatch(
                path=path, operation="CREATE", before_hash=before,
                edits=[FileEdit(kind="insert", at_line=1,
                                text="".join(lines),
                                provenance=e.provenance)
                       for e in edits][:1],
                reason=info["reason"],
                design_change_ids=info["design_ids"]))
            # rebuild contents: CREATE carries the full text
            gen.patches[-1].edits[0].text = "".join(
                e.text for e in edits)
        else:
            before_lines = _file_lines(rootp, path)
            before = hashlib.sha1(
                "".join(before_lines).encode()).hexdigest()
            gen.patches.append(FilePatch(
                path=path, operation="MODIFY", before_hash=before,
                edits=sorted(edits, key=lambda e: (e.at_line or 0,
                                                   e.text)),
                reason=info["reason"],
                design_change_ids=info["design_ids"]))

    gen.preview = build_preview(gen.patches, plan)
    return gen


def _ensure(path: str, file_work: dict, create: bool = False) -> dict:
    info = file_work.setdefault(path, {
        "edits": [], "create": create,
        "reason": "", "design_ids": []})
    info["create"] = info["create"] or create
    return info


def _apply_change(c: PlanChange, code: LvsCodeSide, to_be: dict,
                  file_work: dict, rootp: Path, patch_key: str,
                  gen: GeneratedPatches) -> None:
    stype = c.synthesis_type
    st = c.design_diff.get("kind")
    obj = c.design_diff.get("design_object", "")
    prov_base = {"design_change_id": c.change_id,
                 "synthesis_rule": f"SYN-S{stype[1:]}",
                 "target_symbol": c.affected_symbols[0] if
                 c.affected_symbols else None,
                 "source_snapshot": None,
                 "generated_patch_id": patch_key}

    if stype == "S1" and st in ("block_added",):
        block = c.design_diff.get("detail", {}).get("block") or {}
        name = block.get("name", "NewSymbol")
        ports = _ports_of(block.get("id"), to_be)
        inputs = [p for p in ports if p.get("direction") == "input"
                  and p.get("semantic_kind") in ("data", None)]
        outputs = [p for p in ports if p.get("direction") == "output"
                   and p.get("semantic_kind") in ("data", None)]
        params = ", ".join(f"{_safe(p['name'])}: None = None"
                           for p in inputs) or ""
        ret = _ret_for(outputs, inputs)
        body = _minimal_body(outputs)
        # bounded chaining: if the new symbol has an outgoing control net
        # in the TO-BE design, the generated body calls the callee and
        # returns its result (real call + data path).
        callee = _outgoing_callee(block.get("id"), to_be, code)
        import_line = ""
        if callee:
            cfn = code.functions.get(callee)
            body = f"    return {_safe(cfn['name'])}({', '.join(_safe(p['name']) for p in inputs)})\n" if cfn else body
            if cfn and cfn.get("file"):
                mod = _module_of(cfn["file"])
                import_line = f"from {mod} import {_safe(cfn['name'])}\n"
        text = (import_line +
                f"\ndef {_safe(name)}({params}) -> {ret}:\n"
                f"{body}\n")
        mod_path = _module_for(name, block.get("binding"), to_be, code)
        info = file_work.setdefault(
            mod_path, {"edits": [FileEdit(
                kind="insert", at_line=1, text=text,
                provenance={**prov_base, "target_symbol": name})],
                "create": True, "reason": f"{c.change_id}: add symbol "
                "{name} per TO-BE design",
                "design_ids": [c.change_id]})
        info["design_ids"] = sorted(set(info["design_ids"]) |
                                    {c.change_id})

    elif stype == "S2" and st in ("port_added", "port_modified",
                                  "port_removed", "block_modified"):
        target = c.affected_symbols[-1] if c.affected_symbols else None
        if not target or target not in code.functions:
            _plan_only(c, gen, "target symbol has no canonical file/span")
            return
        fn = code.functions[target]
        path = fn.get("file")
        span = fn.get("span") or {}
        if not path:
            _plan_only(c, gen, "no canonical file for target")
            return
        lines = _file_lines(rootp, path)
        if not lines:
            _plan_only(c, gen, "source unavailable")
            return
        start = span.get("start_line")
        end = span.get("end_line")
        if not start:
            _plan_only(c, gen, "no span for target signature")
            return
        new_sig = _new_signature(fn, to_be, c)
        old_line = lines[start - 1] if 0 < start <= len(lines) else None
        info = _ensure(path, file_work)
        info["edits"].append(FileEdit(
            kind="replace", at_line=start, text=new_sig,
            old_text=old_line or "",
            provenance=prov_base))
        info["reason"] = c.change_id + ": signature change"
        info["design_ids"] = sorted(set(info["design_ids"]) |
                                    {c.change_id})
        # callers: add argument bindings at their callsites
        for caller in c.affected_symbols:
            if caller not in code.functions or caller == target:
                continue
            cf = code.functions[caller]
            cpath = cf.get("file")
            cspan = cf.get("span") or {}
            if not cpath or not cspan.get("start_line"):
                continue
            clines = _file_lines(rootp, cpath)
            cstart, cend = cspan.get("start_line"), cspan.get("end_line")
            idx = _find_call_line(clines, cstart, cend, fn.get("name", ""),
                                  c)
            if idx is None:
                continue
            arg_text = _caller_arg(c, to_be, caller, fn.get("name", ""),
                                   clines, idx)
            if arg_text is None:
                _plan_only(c, gen,
                           f"caller {caller}: argument mapping unknown — "
                           "not modified (coverage preserved)")
                continue
            info2 = _ensure(cpath, file_work)
            info2["edits"].append(FileEdit(
                kind="replace", at_line=idx + 1,
                text=arg_text, old_text=clines[idx],
                provenance={**prov_base,
                            "target_symbol": caller}))
            info2["reason"] = c.change_id + ": update caller " + caller
            info2["design_ids"] = sorted(set(info2["design_ids"]) |
                                         {c.change_id})

    elif stype in ("S3", "S5", "S6") and st == "net_added":
        net = c.design_diff.get("detail", {}).get("net") or {}
        if stype in ("S5", "S6") and _has_control_pair(net, to_be):
            return      # the S3 callsite already carries the data args
        if not _handle_net(gen, c, net, to_be, code, file_work,
                           rootp, prov_base):
            _plan_only(c, gen,
                       "call/data wiring inside a caller body requires an "
                       "anchor context; bounded insert via the middleman "
                       "pattern is applied by S1+S3 combos — PLAN_ONLY here")

    elif stype == "S7" and st == "resource_added":
        # bounded resource wiring: inject a resource parameter + import
        res = (c.design_diff.get("detail") or {}).get("resource")
        block = (c.design_diff.get("detail") or {}).get("block") or {}
        binding = block.get("binding")
        if not binding or not res or binding not in code.functions:
            _plan_only(c, gen, "resource injection without bound target — "
                               "PLAN_ONLY")
            return
        param = f"{_snake(str(res))}_resource"
        _inject_params(c, binding, [param], code, to_be, file_work,
                       rootp, prov_base,
                       caller_arg=lambda pname: '"%s"' % res)

    elif stype == "S8" and st in ("composite_modified", "composite_added"):
        # bounded: new external data_in ports are injected on the entry
        # member (first bound member) — the external boundary wiring
        comp = c.design_diff.get("to_be") or {}
        _handle_composite(c, comp, code, to_be, file_work, rootp, prov_base)

    elif stype == "S4" and st == "net_removed":
        _remove_net_callsite(c, to_be, code, file_work, rootp, prov_base) \
            if not _plan_only(c, gen, "removal target not resolved — "
                                      "PLAN_ONLY") else None
    else:
        _plan_only(c, gen, f"{stype} not in bounded transform set")


def _handle_net(gen, c: PlanChange, net: dict, to_be: dict,
                code: LvsCodeSide, file_work: dict, rootp: Path,
                prov: dict) -> bool:
    """Bounded middleman pattern: all nets of the block that owns the
    TO-BE flow are implemented by inserting ONE callsite line with real
    data argument binding into the middleman function."""
    # find the middleman: a TO-BE block that connects to BOTH endpoints
    src_bid, tgt_bid = net.get("source_block_id"), net.get("target_block_id")
    blocks = {b["id"]: b for b in to_be.get("blocks", [])}
    for n in to_be.get("nets", []):
        if n.get("kind") not in ("control", "call"):
            continue
        a, b = n.get("source_block_id"), n.get("target_block_id")
        if a == src_bid and b != tgt_bid:
            for m in to_be.get("nets", []):
                if m.get("kind") not in ("control", "call"):
                    continue
                if m.get("source_block_id") == b and \
                        m.get("target_block_id") == tgt_bid:
                    return False     # chain shape: handled by caller loop
    # single-hop: caller = source block's binding function
    src = blocks.get(src_bid)
    callee = blocks.get(tgt_bid)
    if not src or not callee:
        return False
    caller_binding = src.get("binding")
    callee_binding = callee.get("binding")
    if not caller_binding or caller_binding not in code.functions:
        return False
    # callee may be a to-be-created symbol (S1): fall back to its design name
    callee_name = (code.functions.get(callee_binding, {})
                   .get("name") if callee_binding else None) \
        or callee.get("name", "")
    if not callee_name or not re.match(r"^[A-Za-z_]\w*$", callee_name):
        return False
    cf = code.functions[caller_binding]
    path, span = cf.get("file"), (cf.get("span") or {})
    if not path or not span.get("start_line"):
        return False
    lines = _file_lines(rootp, path)
    start, end = span.get("start_line"), span.get("end_line")
    ins = _insert_call_line(lines, start, end, callee_name, to_be, callee,
                            code)
    if ins is None:
        return False
    info = _ensure(path, file_work)
    info["edits"].append(FileEdit(kind="insert", at_line=ins[0] + 1,
                                  text=ins[1],
                                  provenance={**prov,
                                              "target_symbol":
                                              caller_binding}))
    info["reason"] = c.change_id + ": add real callsite with data binding"
    info["design_ids"] = sorted(set(info["design_ids"]) |
                                {c.change_id})
    # import for cross-module callee (bounded: new module name; the
    # module path is the same decision used for the CREATE file)
    if callee_binding is None:
        mod_path = _module_for(callee_name, None, to_be, code)
        mod = _module_of(mod_path)
        imp = f"from {mod} import {callee_name}\n"
        if not any(getattr(e, "tag", None) == "import" for e in info["edits"]):
            info["edits"].append(FileEdit(
                kind="insert", at_line=1, text=imp,
                provenance={**prov, "target_symbol": callee_name}))
            info["edits"][-1].tag = "import"
    return True


def _insert_call_line(lines: list[str], start: int, end: int,
                      callee_name: str, to_be: dict, callee: dict,
                      code: LvsCodeSide) -> tuple[int, str] | None:
    """Insert the callsite before the last non-blank line of the caller
    span (before the return statement, bounded)."""
    lo, hi = max(0, start - 1), min(len(lines), end or start)
    ports = [p for p in to_be.get("ports", [])
             if p.get("block_id") == callee.get("id")
             and p.get("direction") == "input"
             and p.get("semantic_kind") in ("data", None)]
    args = ", ".join(f"{_safe(p['name'])}" for p in ports)
    stmt = f"    {callee_name}({args})\n" if callee_name else ""
    if not stmt:
        return None
    for i in range(hi - 1, lo - 1, -1):
        if lines[i].strip():
            return i, stmt
    return (lo, stmt)


def _ports_of(block_id, to_be: dict) -> list[dict]:
    return [p for p in to_be.get("ports", [])
            if p.get("block_id") == block_id]


def _safe(name: str) -> str:
    return re.sub(r"\W", "_", name)


def _ret_for(outputs: list[dict], inputs: list[dict]) -> str:
    if not outputs:
        return "None"
    types = {o.get("code_type") for o in outputs if o.get("code_type")}
    if len(types) == 1:
        return str(types.pop())
    return "dict"


def _minimal_body(outputs: list[dict]) -> str:
    if not outputs:
        return "    return None"
    if len(outputs) == 1:
        return f"    return {_safe(outputs[0]['name'])}"
    return ("    return {" + ", ".join(
        f'"{p["name"]}": {_safe(p["name"])}' for p in outputs) + "}")


def _module_for(name: str, binding: str | None,
                to_be: dict | None = None, code: LvsCodeSide | None = None) -> str:
    if binding and "." in binding:
        q = binding.split(":", 1)[-1].split(".")[0]     # top-level package
        return f"src/{q}/{_snake(name)}.py"
    # place the new module next to the design neighbour's file
    if to_be and code:
        blocks = {b["id"]: b for b in to_be.get("blocks", [])}
        # find any net touching the new block; sibling endpoint file
        new_ids = {b["id"] for b in blocks.values()
                   if b.get("name") == name and not b.get("binding")}
        for n in (to_be.get("nets") or []):
            for bid, other in ((n.get("source_block_id"),
                                n.get("target_block_id")),
                               (n.get("target_block_id"),
                                n.get("source_block_id"))):
                if bid in new_ids and other in blocks:
                    b = blocks[other]
                    if b.get("binding") and b["binding"] in code.functions:
                        f = (code.functions[b["binding"]].get("file") or "")
                        if f:
                            d = f.rsplit("/", 1)[0]
                            return f"{d}/{_snake(name)}.py"
    return f"src/{_snake(name)}.py"


def _module_of(file: str) -> str:
    """src/ypipe/run.py -> ypipe.run (module path for imports)."""
    parts = file.split("/")
    if parts and parts[0] in ("src", "lib", "app"):
        parts = parts[1:]
    return ".".join(p for p in parts if p).replace(".py", "")


def _outgoing_callee(block_id, to_be: dict, code: LvsCodeSide) -> str | None:
    blocks = {b["id"]: b for b in to_be.get("blocks", [])}
    for n in sorted(to_be.get("nets", []),
                    key=lambda x: str(x.get("id", ""))):
        if n.get("kind") not in ("control", "call"):
            continue
        if n.get("source_block_id") != block_id:
            continue
        tgt = blocks.get(n.get("target_block_id"))
        if tgt and tgt.get("binding") and tgt["binding"] in code.functions:
            return tgt["binding"]
    return None


def _new_signature(fn: dict, to_be: dict, c: PlanChange) -> str:
    """Deterministic signature text from the design's TO-BE ports."""
    block_id = None
    for b in to_be.get("blocks", []):
        if b.get("binding") == c.affected_symbols[-1]:
            block_id = b.get("id")
            break
    ports = _ports_of(block_id, to_be) if block_id else []
    inputs = [p for p in ports if p.get("direction") == "input"
              and p.get("semantic_kind") in ("data", None)]
    name = fn.get("name", "f")
    params = ", ".join(f"{_safe(p['name'])}" for p in inputs) or ""
    out_types = {p.get("code_type") for p in ports
                 if p.get("direction") == "output" and p.get("code_type")}
    ret = str(next(iter(out_types))) if len(out_types) == 1 else ("dict"
                                                                  if out_types else "None")
    return f"def {_safe(name)}({params}) -> {ret}:\n"


def _find_call_line(lines: list[str], start: int, end: int,
                    callee_name: str, c: PlanChange) -> int | None:
    lo, hi = max(0, start - 1), min(len(lines), end or start)
    pat = re.compile(r"\b" + re.escape(callee_name) + r"\s*\(")
    for i in range(lo, hi):
        if pat.search(lines[i]):
            return i
    return None


def _caller_arg(c: PlanChange, to_be: dict, caller_binding: str,
                callee_name: str, lines: list[str], idx: int) -> str | None:
    """Replacement callsite text: pass the design port names (the
    variable names defined by the TO-BE data nets are used when their
    token matches a port name; otherwise the port name itself)."""
    # find callee block via binding; get its TO-BE input ports
    block = None
    for b in to_be.get("blocks", []):
        if b.get("binding") == c.affected_symbols[-1]:
            block = b
            break
    if block is None:
        return None
    in_ports = [p for p in _ports_of(block.get("id"), to_be)
                if p.get("direction") == "input"
                and p.get("semantic_kind") in ("data", None)]
    old = lines[idx]
    args = ", ".join(f"{_safe(p['name'])}" for p in in_ports)
    m = re.match(r"^(\s*)(.+?)(\s*.\s*)" + re.escape(callee_name) +
                 r"\((.*)\)(.*)$", old)
    if not m:
        return None
    prefix, _head, tail = m.group(1), m.group(2), m.group(5)
    newline = f"{prefix}{callee_name}({args}){tail}\n"
    return newline


def _insert_line(lines: list[str], start: int, end: int,
                 callee_binding: str, to_be: dict, callee: dict,
                 code: LvsCodeSide) -> tuple[int, str] | None:
    """Insert callsite line before the callable's last statement for a
    function-style callee (bounded middleman pattern)."""
    lo, hi = max(0, start - 1), min(len(lines), end or start)
    name = code.functions.get(callee_binding, {}).get("name", "")
    ports = [p for p in to_be.get("ports", [])
             if p.get("block_id") == callee.get("id")
             and p.get("direction") == "input"
             and p.get("semantic_kind") in ("data", None)]
    args = ", ".join(f"{_safe(p['name'])}" for p in ports)
    stmt = f"    {_safe(name)}({args})\n" if name else ""
    if not stmt:
        return None
    # insert before the last non-blank line of the span (before return)
    for i in range(hi - 1, lo - 1, -1):
        if lines[i].strip():
            return i, stmt
    return None


def _plan_only(c: PlanChange, gen: GeneratedPatches, reason: str) -> None:
    c.supported = False
    c.reason = reason
    gen.plan_only.append(f"{c.change_id}: PLAN_ONLY — {reason}")


# ---------------------------------------------------------------------------
# shared signature transform (S2 / S7 / S8)
# ---------------------------------------------------------------------------

def _inject_params(c: PlanChange, binding: str, params: list[str],
                   code: LvsCodeSide, to_be: dict, file_work: dict,
                   rootp: Path, prov: dict, *,
                   caller_arg=None) -> None:
    fn = code.functions.get(binding)
    if not fn or not fn.get("file") or not (fn.get("span") or {}).get("start_line"):
        c.supported = False
        c.reason = "target has no canonical file/span — PLAN_ONLY"
        return
    path = fn["file"]
    lines = _file_lines(rootp, path)
    start = fn["span"]["start_line"]
    old_line = lines[start - 1] if 0 < start <= len(lines) else ""
    new_sig = _sig_with_params(old_line, fn, params)
    if new_sig is None:
        c.supported = False
        c.reason = "signature text cannot be transformed — PLAN_ONLY"
        return
    info = _ensure(path, file_work)
    info["edits"].append(FileEdit(
        kind="replace", at_line=start, text=new_sig,
        old_text=old_line, provenance=prov))
    info["reason"] = c.change_id + ": signature injection"
    info["design_ids"] = sorted(set(info["design_ids"]) | {c.change_id})
    # known callers: pass the injected arguments (bounded mapping)
    for caller in c.affected_symbols:
        if caller == binding or caller not in code.functions:
            continue
        cf = code.functions[caller]
        cpath, cspan = cf.get("file"), (cf.get("span") or {})
        if not cpath or not cspan.get("start_line"):
            continue
        clines = _file_lines(rootp, cpath)
        idx = _find_call_line(clines, cspan.get("start_line"),
                              cspan.get("end_line"), fn.get("name", ""), c)
        if idx is None:
            continue
        args = ", ".join(caller_arg(p) if caller_arg else p
                         for p in params)
        new = _rewrite_call_args(clines[idx], fn.get("name", ""), args)
        if new is None:
            continue
        info2 = _ensure(cpath, file_work)
        info2["edits"].append(FileEdit(
            kind="replace", at_line=idx + 1, text=new,
            old_text=clines[idx],
            provenance={**prov, "target_symbol": caller}))
        info2["reason"] = c.change_id + ": update caller " + caller
        info2["design_ids"] = sorted(set(info2["design_ids"]) |
                                     {c.change_id})


def _sig_with_params(old_line: str, fn: dict, extra_params: list[str]) -> str | None:
    name = fn.get("name", "f")
    m = re.match(r"^(\s*def\s+)" + re.escape(name) + r"\s*\((.*)\)(.*)$", old_line)
    if not m:
        return None
    prefix, args, tail = m.group(1), m.group(2), m.group(3)
    existing = [a.strip() for a in args.split(",") if a.strip()]
    for p in extra_params:
        if p not in existing:
            existing.append(f"{p}: None = None")
    return f"{prefix}{_safe(name)}({', '.join(existing)}){tail}\n"


def _rewrite_call_args(line: str, callee: str, args: str) -> str | None:
    m = re.match(r"^(\s*)(.*?\b)" + re.escape(callee) + r"\s*\(.*\)(.*)$", line)
    if not m:
        return None
    prefix, head, tail = m.group(1), m.group(2), m.group(3)
    if head.strip().endswith(("=", ":")):
        return None
    return f"{prefix}{head}{callee}({args}){tail}\n"


def _handle_composite(c: PlanChange, comp: dict, code: LvsCodeSide,
                      to_be: dict, file_work: dict, rootp: Path,
                      prov: dict) -> None:
    blocks = {b["id"]: b for b in to_be.get("blocks", [])}
    member_ids = [b for b in comp.get("block_ids", [])
                  if b in blocks and blocks[b].get("binding")]
    if not member_ids:
        c.supported = False
        c.reason = "composite has no bound member — PLAN_ONLY"
        return
    entry = member_ids[0]          # deterministic entry member
    binding = blocks[entry].get("binding")
    inputs = comp.get("data_in", []) or []
    params = [_safe(str(i)) for i in inputs]
    c.affected_symbols = [binding]
    _inject_params(c, binding, params, code, to_be, file_work, rootp, prov,
                   caller_arg=lambda p: p)


def build_preview(patches: list[FilePatch], plan: SynthesisPlan) -> dict:
    files = len(patches)
    added = sum(len(e.text.splitlines()) for p in patches for e in p.edits
                if e.text)
    removed = sum(len(e.old_text.splitlines()) for p in patches
                  for e in p.edits if e.old_text)
    symbols = sorted({e.provenance.get("target_symbol", "")
                      for p in patches for e in p.edits
                      if e.provenance.get("target_symbol")})
    mapping = []
    for p in patches:
        for e in p.edits:
            mapping.append({
                "file": p.path,
                "design_change_id": e.provenance.get("design_change_id"),
                "synthesis_rule": e.provenance.get("synthesis_rule"),
                "target_symbol": e.provenance.get("target_symbol"),
            })
    return {
        "files_changed": files,
        "symbols_changed": sorted(set(symbols)),
        "lines_added": added,
        "lines_removed": removed,
        "design_to_code_mapping": mapping,
        "expected_lvs": plan.expected_lvs_delta,
        "plan_only": [c.change_id for c in plan.changes if not c.supported],
    }


def _remove_net_callsite(c: PlanChange, to_be: dict, code: LvsCodeSide,
                         file_work: dict, rootp: Path, prov: dict) -> None:
    """Bounded removal: delete the callee callsite line inside the caller
    function span (net_removed control)."""
    net = c.design_diff.get("detail", {}).get("net") or {}
    src = _block_by_id(to_be, net.get("source_block_id"))
    callee = _block_by_id(to_be, net.get("target_block_id"))
    if not src or not callee or not src.get("binding"):
        return
    caller_binding = src["binding"]
    if caller_binding not in code.functions:
        return
    callee_name = (code.functions.get(callee.get("binding"), {})
                   .get("name") if callee.get("binding") else
                   callee.get("name", ""))
    fn = code.functions[caller_binding]
    path, span = fn.get("file"), (fn.get("span") or {})
    if not path or not span.get("start_line"):
        return
    lines = _file_lines(rootp, path)
    lo, hi = max(0, span.get("start_line", 1) - 1), min(
        len(lines), span.get("end_line") or span.get("start_line", 1))
    import re as _re
    pat = _re.compile(r"\b" + _re.escape(callee_name) + r"\s*\(")
    for i in range(lo, hi):
        if pat.search(lines[i]):
            info = _ensure(path, file_work)
            info["edits"].append(FileEdit(
                kind="delete", at_line=i + 1, text="", old_text=lines[i],
                provenance={**prov, "target_symbol": caller_binding}))
            info["reason"] = c.change_id + ": remove callsite " + \
                callee_name
            info["design_ids"] = sorted(set(info["design_ids"]) |
                                        {c.change_id})
            return


def _block_by_id(to_be: dict, bid) -> dict | None:
    return next((b for b in (to_be.get("blocks") or [])
                 if b.get("id") == bid), None)


def _has_control_pair(net: dict, to_be: dict) -> bool:
    """Bounded dedup: a data net between the same block pair as a control
    net is realized by the callsite args — do not double-insert."""
    s_, t_ = net.get("source_block_id"), net.get("target_block_id")
    for n in (to_be.get("nets") or []):
        if n.get("kind") in ("control", "call") and \
                n.get("source_block_id") == s_ and \
                n.get("target_block_id") == t_:
            return True
    return False
