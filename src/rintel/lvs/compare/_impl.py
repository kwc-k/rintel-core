"""SOFTWARE-LVS1 comparator implementations.

Strict separation (§2): design side = FlowModel-derived dict; code side =
canonical topology + signatures.  The engine never mutates code truth.

The per-relation public modules (block.py, ports.py, calls.py, ...) re-export
from here so the spec §27 file layout is honoured without duplicating logic.
"""
from __future__ import annotations

from ..code_side import LvsCodeSide, LvsDesign
from ..model import LvsDiff, LvsStatus

HARD = ("OBSERVED", "RESOLVED")


def _sig_ok(sig: dict | None) -> bool:
    return bool(sig) and not sig.get("parse_failed")


# ---------------------------------------------------------------------------
# block LVS (§5)
# ---------------------------------------------------------------------------

def compare_blocks(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for b in sorted(design.blocks, key=lambda x: str(x.get("id", ""))):
        block_id = str(b.get("id", ""))
        state = b.get("state", "existing")
        binding = b.get("binding")
        name = b.get("name", block_id)
        if state == "proposed" or not binding:
            if state == "proposed":
                diffs.append(LvsDiff(
                    kind="block", status=LvsStatus.UNBOUND,
                    design_object=f"block:{name}",
                    message=f'Proposed block "{name}" has no canonical '
                            "target in code.",
                    why="Proposed design objects are UNBOUND — never a code "
                        "error (L4).",
                    coverage="UNKNOWN"))
            continue
        exists = binding in code.functions
        if exists:
            diffs.append(LvsDiff(
                kind="block", status=LvsStatus.MATCH,
                design_object=f"block:{name}",
                code_object=binding,
                message=f'Block "{name}" binds to canonical {binding}.',
                why="Canonical identity found in code (L1).",
                truth_class="OBSERVED", coverage="COMPLETE"))
            continue
        baseline_exists = binding in code.baseline_functions
        if baseline_exists:
            diffs.append(LvsDiff(
                kind="block", status=LvsStatus.STALE,
                design_object=f"block:{name}",
                code_object=binding,
                message=f'Bound block "{name}" ({binding}) existed at '
                        f"baseline {code.baseline_id} but is missing from "
                        f"current {code.snapshot_id}.",
                why="Design was bound to real code; the code has changed "
                    "since (snapshot-aware, §17).",
                truth_class="OBSERVED", coverage="COMPLETE",
                source_location=code.baseline_functions[binding]
                .get("source_location")))
        else:
            diffs.append(LvsDiff(
                kind="block", status=LvsStatus.MISMATCH,
                design_object=f"block:{name}",
                code_object=binding,
                message=f'Block "{name}" claims binding {binding} but no '
                        "such canonical function exists in code.",
                why="Deterministic contradiction: bound design object "
                    "references an identity code does not have.",
                truth_class="OBSERVED", coverage="COMPLETE"))
    return diffs


# ---------------------------------------------------------------------------
# port / signature LVS (§6/§7) — fine-grained per-port diff
# ---------------------------------------------------------------------------

def compare_ports(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for b in sorted(design.blocks, key=lambda x: str(x.get("id", ""))):
        if b.get("state") == "proposed":
            continue
        binding = b.get("binding")
        if not binding:
            continue
        fn = code.functions.get(binding)
        if fn is None:
            continue            # block diff already reports this
        sig = fn.get("signature") or {}
        params = {p["name"]: p for p in sig.get("params", [])}
        baseline = code.baseline_functions.get(binding, {})
        base_params = {p["name"]: p for p in
                       (baseline.get("signature") or {}).get("params", [])}
        for p in sorted(design.ports_of(b.get("id", "")),
                        key=lambda x: (x.get("direction", ""), x.get("name", ""))):
            # only data-direction ports are signature-backed
            if p.get("semantic_kind") not in (None, "data", "input", "output"):
                continue
            name = p.get("name", "")
            direction = p.get("direction", "input")
            if direction in ("input",):
                code_port = params.get(name)
                baseline_port = base_params.get(name)
                if code_port is not None:
                    diffs.append(_port_type_diff(p, code_port, binding))
                elif baseline_port is not None:
                    diffs.append(LvsDiff(
                        kind="port", status=LvsStatus.STALE,
                        design_object=f"port:{binding}:{name}",
                        code_object=binding,
                        message=f'Parameter "{name}" (was at baseline) is '
                                "missing from the current signature.",
                        why="Snapshot-aware: the port existed at the design "
                            "baseline; the current signature lost it (§17).",
                        truth_class="OBSERVED", coverage="COMPLETE"))
                elif not _sig_ok(sig):
                    diffs.append(LvsDiff(
                        kind="port", status=LvsStatus.UNKNOWN,
                        design_object=f"port:{binding}:{name}",
                        code_object=binding,
                        message=f'Cannot compare parameter "{name}": the '
                                "code signature could not be extracted.",
                        why="Signature extraction failed; insufficient "
                            "evidence for a verdict (L3).",
                        coverage="UNKNOWN"))
                else:
                    diffs.append(LvsDiff(
                        kind="port", status=LvsStatus.MISMATCH,
                        design_object=f"port:{binding}:{name}",
                        code_object=binding,
                        message=f'Parameter "{name}" exists in the design '
                                "but not in the code signature.",
                        why="Conflicting evidence: design declares an input "
                            "the code does not have.",
                        truth_class="OBSERVED", coverage="COMPLETE"))
        # code-only input ports (spec §10): CODE_ONLY diff items
        if _sig_ok(sig):
            design_names = {p["name"] for p in design.ports_of(b.get("id", ""))}
            for pname in sorted(params):
                if pname not in design_names:
                    diffs.append(LvsDiff(
                        kind="port", status=LvsStatus.CODE_ONLY,
                        design_object=f"function:{binding}",
                        code_object=f"port:{binding}:{pname}",
                        message=f'Code-only parameter "{pname}" not present '
                                "in the design.",
                        why="Code has an input the design does not declare "
                            "(spec §10).",
                        truth_class="OBSERVED", coverage="COMPLETE"))
    return diffs


def _port_type_diff(design_port: dict, code_port: dict,
                    binding: str) -> LvsDiff:
    name = design_port.get("name", "")
    dt = design_port.get("code_type")
    ct = code_port.get("type")
    if dt is None or ct is None or not _types_known(dt, ct):
        return LvsDiff(
            kind="port", status=LvsStatus.UNKNOWN,
            design_object=f"port:{binding}:{name}",
            code_object=f"port:{binding}:{name}",
            message=f'Cannot compare type of "{name}": design={dt!r} '
                    f"code={ct!r} — at least one side unknown.",
            why="Type LVS compares only with sufficient evidence on both "
                "sides (D4-style honesty).",
            truth_class="INFERRED", coverage="PARTIAL")
    if _incompatible(dt, ct):
        return LvsDiff(
            kind="port", status=LvsStatus.MISMATCH,
            design_object=f"port:{binding}:{name}",
            code_object=f"port:{binding}:{name}",
            message=f'Type mismatch on "{name}": design {dt} vs code {ct}.',
            why="Both sides have provider/native type evidence and disagree.",
            truth_class="OBSERVED", coverage="COMPLETE")
    return LvsDiff(
        kind="port", status=LvsStatus.MATCH,
        design_object=f"port:{binding}:{name}",
        code_object=f"port:{binding}:{name}",
        message=f'Parameter "{name}" matches (type {dt}).',
        why="Names and known types agree.",
        truth_class="OBSERVED", coverage="COMPLETE")


def _types_known(dt: str, ct: str) -> bool:
    return bool(dt) and bool(ct)


def _incompatible(dt: str, ct: str) -> bool:
    a, b = str(dt).strip().lower(), str(ct).strip().lower()
    if a == b:
        return False
    # conservative, provable only
    if a in ("str", "string") and b in ("int", "float", "list", "dict"):
        return True
    if a in ("int", "float", "bool") and b in ("str",):
        return True
    if a in ("dataclass", "matrix") and b in ("socket", "connection"):
        return True
    return False


# ---------------------------------------------------------------------------
# call LVS (§8)
# ---------------------------------------------------------------------------

def compare_calls(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for n in sorted(design.nets, key=lambda x: str(x.get("id", ""))):
        if n.get("kind") not in ("control", "call"):
            continue
        src = design.block(n.get("source_block_id", ""))
        tgt = design.block(n.get("target_block_id", ""))
        if not src or not tgt or not src.get("binding") or not tgt.get("binding"):
            continue
        a, b = src["binding"], tgt["binding"]
        hits = [e for e in code.by_source.get(a, [])
                if e.get("target") == b and e.get("kind") == "CALL"]
        if hits:
            best = hits[0]
            if (best.get("truth_class") in HARD and
                    best.get("coverage") == "COMPLETE" and
                    best.get("target_resolution") != "UNKNOWN"):
                diffs.append(LvsDiff(
                    kind="call", status=LvsStatus.MATCH,
                    design_object=f"call:{a}->{b}",
                    code_object=f"call:{a}->{b}",
                    message=f"Designed CALL {a} → {b} exists in code.",
                    why="Deterministic CALL with COMPLETE coverage and "
                        "resolved target (L2).",
                    truth_class=best.get("truth_class"),
                    coverage=best.get("coverage"),
                    code_witness=best.get("representative_witnesses",
                                          [{}])[0] if best.get(
                        "representative_witnesses") else None,
                    source_location=best.get("source_span")))
            else:
                mod = best.get("execution_modality", "MAY")
                diffs.append(LvsDiff(
                    kind="call", status=LvsStatus.UNKNOWN,
                    design_object=f"call:{a}->{b}",
                    code_object=f"call:{a}->{b}",
                    message=f"Designed CALL {a} → {b} is only supported "
                            f"with {mod}/partial evidence "
                            "(target_resolution="
                            f"{best.get('target_resolution')}).",
                    why="Design asserts a deterministic CALL; code evidence "
                        "is may-flow/partial — not a contradiction (§8).",
                    truth_class=best.get("truth_class"),
                    coverage=best.get("coverage")))
            continue
        # no code CALL: baseline-aware, then lane coverage decides (§8/§17)
        had_baseline = any(e.get("kind") == "CALL"
                           and e.get("source") == a and e.get("target") == b
                           for e in code.baseline_edges)
        if had_baseline:
            diffs.append(LvsDiff(
                kind="call", status=LvsStatus.STALE,
                design_object=f"call:{a}->{b}",
                code_object=f"call:{a}->{b}",
                message=f"Designed CALL {a} → {b} existed at baseline "
                        f"{code.baseline_id} but is missing from current "
                        f"{code.snapshot_id}.",
                why="Snapshot-aware: the designed relation was real code "
                    "behaviour at the design baseline; the code changed "
                    "(§17).",
                truth_class="OBSERVED", coverage="COMPLETE"))
        elif code.call_capability == "COMPLETE":
            diffs.append(LvsDiff(
                kind="call", status=LvsStatus.MISMATCH,
                design_object=f"call:{a}->{b}",
                message=f"Designed CALL {a} → {b} does not exist although "
                        "call coverage is COMPLETE.",
                why="Deterministic contradiction: design claims a relation "
                    "the code provably lacks."))
        else:
            diffs.append(LvsDiff(
                kind="call", status=LvsStatus.UNKNOWN,
                design_object=f"call:{a}->{b}",
                message=f"Cannot verify designed CALL {a} → {b}: call "
                        f"capability is {code.call_capability}.",
                why="Insufficient coverage (§8: may/partial never proves "
                    "design wrong).",
                coverage=code.call_capability))
    # code-only CALL relations (spec §10)
    for a in sorted(code.functions):
        for e in sorted(code.by_source.get(a, []),
                        key=lambda x: str(x.get("target", ""))):
            if e.get("kind") != "CALL":
                continue
            b = e.get("target")
            if b == "[Unknown Dynamic Target]" or b not in code.functions:
                continue
            db = design.block_by_binding(a)
            dt = design.block_by_binding(b)
            if db is None or dt is None:
                continue
            claim = any(
                n.get("kind") in ("control", "call")
                and design.block(n.get("source_block_id")) is db
                and design.block(n.get("target_block_id")) is dt
                for n in design.nets)
            if not claim:
                diffs.append(LvsDiff(
                    kind="call", status=LvsStatus.CODE_ONLY,
                    design_object=f"function:{a}",
                    code_object=f"call:{a}->{b}",
                    message=f"Code-only CALL {a} → {b} not present in the "
                            "design.",
                    why="Code has a relation the design does not declare "
                        "(spec §10).",
                    truth_class=e.get("truth_class"),
                    coverage=e.get("coverage"),
                    source_location=e.get("source_span")))
    return diffs


# ---------------------------------------------------------------------------
# data LVS (§9 — the focus; INFERRED may-flow -> UNKNOWN with explanation)
# ---------------------------------------------------------------------------

def compare_data(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for n in sorted(design.nets, key=lambda x: str(x.get("id", ""))):
        if n.get("kind") != "data":
            continue
        src = design.block(n.get("source_block_id", ""))
        tgt = design.block(n.get("target_block_id", ""))
        if not src or not tgt or not src.get("binding") or not tgt.get("binding"):
            continue
        a, b = src["binding"], tgt["binding"]
        token = (n.get("label") or (n.get("source_port_id") or "")).strip()
        edges = [e for e in code.by_source.get(a, [])
                 if e.get("kind") == "DATA" and e.get("target") == b]
        token_edges = [e for e in edges
                       if not token or (e.get("data") or {}).get("token") == token
                       or (e.get("data") or {}).get("token") is None]
        if token_edges:
            best = token_edges[0]
            if best.get("truth_class") in HARD and \
                    best.get("coverage") == "COMPLETE":
                diffs.append(LvsDiff(
                    kind="data", status=LvsStatus.MATCH,
                    design_object=f"data:{a}->{b}",
                    code_object=f"data:{a}->{b}",
                    message=f"Designed DATA connection {a} → {b} exists "
                            "(resolved).",
                    why="RESOLVED data binding with COMPLETE coverage (§9).",
                    truth_class=best.get("truth_class"),
                    coverage=best.get("coverage"),
                    code_witness=best.get("representative_witnesses",
                                          [{}])[0] if best.get(
                        "representative_witnesses") else None,
                    source_location=best.get("source_span")))
            else:
                diffs.append(LvsDiff(
                    kind="data", status=LvsStatus.UNKNOWN,
                    design_object=f"data:{a}->{b}",
                    code_object=f"data:{a}->{b}",
                    message=f"Cannot conclude {a} → {b}: compatible "
                            f"inferred flow exists (truth_class="
                            f"{best.get('truth_class')}, coverage="
                            f"{best.get('coverage')}), but resolved data "
                            "binding is unavailable.",
                    why="INFERRED may-flow supports only POSSIBLE/UNKNOWN — "
                        "it cannot prove MATCH (§9, L2).",
                    truth_class=best.get("truth_class"),
                    coverage=best.get("coverage")))
            continue
        if code.data_capability == "COMPLETE":
            diffs.append(LvsDiff(
                kind="data", status=LvsStatus.MISMATCH,
                design_object=f"data:{a}->{b}",
                message=f"Designed DATA connection {a} → {b} does not "
                        "exist although data coverage is COMPLETE.",
                why="Deterministic contradiction on a resolved data net.",
                coverage="COMPLETE"))
        else:
            had_baseline = any(
                e.get("kind") == "DATA" and e.get("source") == a and
                e.get("target") == b for e in code.baseline_edges)
            diffs.append(LvsDiff(
                kind="data", status=LvsStatus.UNKNOWN,
                design_object=f"data:{a}->{b}",
                message=f"Cannot verify designed DATA connection {a} → "
                        f"{b}: data capability is "
                        f"{code.data_capability}."
                        + (" The wire existed at the design baseline and "
                           "is now absent." if had_baseline else ""),
                why="§23: partial data capability answers UNKNOWN, never "
                    "MISMATCH (L3)." + (" Snapshot-aware: wire was present "
                                        "at baseline." if had_baseline else ""),
                coverage=code.data_capability))
    return diffs


# ---------------------------------------------------------------------------
# resource LVS (§16)
# ---------------------------------------------------------------------------

def compare_resources(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for b in sorted(design.blocks, key=lambda x: str(x.get("id", ""))):
        if b.get("state") == "proposed" or not b.get("binding"):
            continue
        binding = b["binding"]
        claimed = set(b.get("resources", []) or [])
        used = {e.get("target") for e in code.by_source.get(binding, [])
                if e.get("kind") == "RESOURCE"}
        for r in sorted(claimed):
            if r in used:
                diffs.append(LvsDiff(
                    kind="resource", status=LvsStatus.MATCH,
                    design_object=f"resource:{binding}:{r}",
                    code_object=f"resource:{binding}:{r}",
                    message=f'Resource "{r}" required by design exists in '
                            "code.",
                    why="Design resource requirement matches code evidence.",
                    truth_class="OBSERVED", coverage="COMPLETE"))
            else:
                diffs.append(LvsDiff(
                    kind="resource", status=LvsStatus.MISMATCH,
                    design_object=f"resource:{binding}:{r}",
                    message=f'Design requires "{r}" but code has no '
                            "resource dependency on it.",
                    why="Deterministic contradiction on a claimed resource."))
        for r in sorted(used - claimed):
            diffs.append(LvsDiff(
                kind="resource", status=LvsStatus.CODE_ONLY,
                design_object=f"function:{binding}",
                code_object=f"resource:{binding}:{r}",
                message=f"Code-only resource dependency: {binding} uses "
                        f'"{r}" but the design does not declare it.',
                why="Code depends on a resource the design hides (§16).",
                truth_class="OBSERVED", coverage="COMPLETE"))
    return diffs


# ---------------------------------------------------------------------------
# boundary LVS (§12/§13) — BOUNDARY_EQUIVALENT, not PROGRAM_EQUIVALENT
# ---------------------------------------------------------------------------

def compare_boundaries(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for comp in sorted(design.composites,
                       key=lambda x: str(x.get("id", ""))):
        comp_id = str(comp.get("id", ""))
        name = comp.get("name", comp_id)
        member_bindings = []
        for bid in comp.get("block_ids", []):
            blk = design.block(bid)
            if blk and blk.get("binding"):
                member_bindings.append(blk["binding"])
        if not member_bindings:
            diffs.append(LvsDiff(
                kind="boundary", status=LvsStatus.UNBOUND,
                design_object=f"composite:{name}",
                message=f'Composite "{name}" has no bound members.',
                why="Unbound design composite — cannot compare (L4)."))
            continue
        member_set = set(member_bindings)
        data_edges = [e for e in code.edges if e.get("kind") == "DATA"]
        data_complete = bool(data_edges) and all(
            e.get("coverage") == "COMPLETE" for e in data_edges)
        code_in: set[str] = set()
        code_out: set[str] = set()
        code_res: set[str] = set()
        for e in code.edges:
            if e.get("kind") == "DATA":
                s, t = e.get("source"), e.get("target")
                tok = str((e.get("data") or {}).get("token") or "")
                if s in member_set and t not in member_set and \
                        t in code.functions and tok:
                    code_out.add(tok)
                elif t in member_set and s not in member_set and \
                        s in code.functions and tok:
                    code_in.add(tok)
            elif e.get("kind") == "RESOURCE":
                if e.get("source") in member_set:
                    code_res.add(str(e.get("target")))
        # the external data contract is declared ON THE COMPOSITE (§12)
        design_in = set(comp.get("data_in", []) or [])
        design_out = set(comp.get("data_out", []) or [])
        design_res = set(comp.get("resources", []) or [])

        # -- resource part (observable contract) ----------------------------
        for r in sorted(code_res - design_res):
            diffs.append(LvsDiff(
                kind="boundary", status=LvsStatus.MISMATCH,
                design_object=f"composite:{name}",
                code_object=f"resource:{r}",
                message=f'Composite "{name}" hides resource dependency: '
                        f'"{r}" not exposed on the boundary.',
                why="Resource requirements are part of the observable "
                    "contract (§12/§16).",
                truth_class="OBSERVED", coverage="COMPLETE"))

        # -- data part -------------------------------------------------------
        data_verified = False
        if data_complete:
            data_verified = True
            if code_in != design_in:
                diffs.append(LvsDiff(
                    kind="boundary", status=LvsStatus.MISMATCH,
                    design_object=f"composite:{name}",
                    message=f'Composite "{name}" boundary inputs differ: '
                            f"design {sorted(design_in)} vs code "
                            f"{sorted(code_in)}.",
                    why="Observable data boundary differs under COMPLETE "
                        "coverage (§12).",
                    coverage="COMPLETE"))
            if code_out != design_out:
                diffs.append(LvsDiff(
                    kind="boundary", status=LvsStatus.MISMATCH,
                    design_object=f"composite:{name}",
                    message=f'Composite "{name}" boundary outputs differ: '
                            f"design {sorted(design_out)} vs code "
                            f"{sorted(code_out)}.",
                    why="Observable data boundary differs under COMPLETE "
                        "coverage (§12).",
                    coverage="COMPLETE"))
        elif (code_in or code_out or design_in or design_out) and data_edges:
            diffs.append(LvsDiff(
                kind="boundary", status=LvsStatus.UNKNOWN,
                design_object=f"composite:{name}",
                message=f'Composite "{name}" data boundary cannot be '
                        "verified: data coverage is partial "
                        f"(design in/out {sorted(design_in)}/{sorted(design_out)}, "
                        f"code in/out {sorted(code_in)}/{sorted(code_out)}).",
                why="§23: partial data capability answers UNKNOWN; the "
                    "boundary is not claimed equivalent without resolved "
                    "edges.",
                coverage="PARTIAL"))
        elif design_in or design_out:
            diffs.append(LvsDiff(
                kind="boundary", status=LvsStatus.UNKNOWN,
                design_object=f"composite:{name}",
                message=f'Composite "{name}" data boundary cannot be '
                        "verified: no resolved DATA edges in code.",
                why="No data evidence → UNKNOWN boundary, never "
                    "BOUNDARY_EQUIVALENT without evidence (L3).",
                coverage="PARTIAL"))

        # -- verdict ---------------------------------------------------------
        has_mismatch = any(d.kind == "boundary" and
                           d.status is LvsStatus.MISMATCH and
                           d.design_object == f"composite:{name}"
                           for d in diffs)
        has_unknown = any(d.kind == "boundary" and
                          d.status is LvsStatus.UNKNOWN and
                          d.design_object == f"composite:{name}"
                          for d in diffs)
        verified = data_verified and not has_mismatch and not has_unknown
        if verified or (not data_verified and not has_mismatch
                        and not has_unknown and not design_in
                        and not design_out and not code_in and not code_out):
            diffs.append(LvsDiff(
                kind="boundary", status=LvsStatus.BOUNDARY_EQUIVALENT,
                design_object=f"composite:{name}",
                message=f'Composite "{name}" external contract is '
                        "equivalent to code (BOUNDARY_EQUIVALENT).",
                why="Observable boundary (ports/resources) matches; interior "
                    "implementation differences are not compared "
                    "(§12/§13).",
                coverage="COMPLETE"))
    return diffs


def _outer_ports(design: LvsDesign, block_id: str,
                 direction: str) -> list:
    return [p for p in design.ports
            if p.get("block_id") == block_id
            and p.get("direction") == direction
            and p.get("semantic_kind") in ("data", None)]


# ---------------------------------------------------------------------------
# control LVS (§14) + timing LVS (§15) — explicit design claims only
# ---------------------------------------------------------------------------

def compare_control(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for c in sorted(design.claims, key=lambda x: str(x.get("id", ""))):
        if c.get("kind") != "BRANCH_CLAIM":
            continue
        block = design.block(c.get("block_id", ""))
        if not block or not block.get("binding"):
            continue
        binding = block["binding"]
        fn = code.functions.get(binding)
        if fn is None:
            continue
        guards = [(lm.get("semantic_kind"), lm.get("guard"))
                  for lm in fn.get("control_landmarks", [])]
        claimed_guard = c.get("guard", "")
        if any(g == claimed_guard for _, g in guards):
            diffs.append(LvsDiff(
                kind="control", status=LvsStatus.MATCH,
                design_object=f"branch:{binding}:{claimed_guard}",
                code_object=f"branch:{binding}",
                message=f'Designed branch guard "{claimed_guard}" exists '
                        "in code control landmarks.",
                why="Explicit design control claim matches code landmarks."))
        elif not guards:
            diffs.append(LvsDiff(
                kind="control", status=LvsStatus.UNKNOWN,
                design_object=f"branch:{binding}:{claimed_guard}",
                message=f"Cannot verify branch claim on {binding}: no "
                        "control landmarks in canonical evidence.",
                why="Insufficient control evidence (§14 coverage rule).",
                coverage="PARTIAL"))
        else:
            diffs.append(LvsDiff(
                kind="control", status=LvsStatus.MISMATCH,
                design_object=f"branch:{binding}:{claimed_guard}",
                code_object=f"branch:{binding}:{guards[0][0]}",
                message=f'Designed branch "{claimed_guard}" does not match '
                        f"code landmarks ({guards[:4]}).",
                why="Guard structure clearly differs while evidence is "
                    "complete."))
    return diffs


def compare_timing(design: LvsDesign, code: LvsCodeSide) -> list:
    diffs = []
    for c in sorted(design.claims, key=lambda x: str(x.get("id", ""))):
        if c.get("kind") not in ("MUST_PRECEDE", "ORDERING_CLAIM"):
            continue
        a_blk, b_blk = design.block(c.get("source", "")), \
            design.block(c.get("target", ""))
        if not a_blk or not b_blk or not a_blk.get("binding") or \
                not b_blk.get("binding"):
            continue
        a, b = a_blk["binding"], b_blk["binding"]
        order = None
        for e in code.edges:
            if e.get("kind") != "TIME":
                continue
            if e.get("source") == a and e.get("target") == b:
                order = e.get("execution_modality")
            elif e.get("source") == b and e.get("target") == a:
                order = "CONTRADICTED"
        if order == "MUST":
            diffs.append(LvsDiff(
                kind="timing", status=LvsStatus.MATCH,
                design_object=f"timing:{a}->{b}",
                code_object=f"timing:{a}->{b}",
                message=f"Designed MUST_PRECEDE {a} → {b} is supported by "
                        "code.",
                why="Explicit timing claim (never inferred from canvas "
                    "position) verified against MUST evidence (§15).",
                truth_class="OBSERVED", coverage="COMPLETE"))
        elif order == "MAY":
            diffs.append(LvsDiff(
                kind="timing", status=LvsStatus.UNKNOWN,
                design_object=f"timing:{a}->{b}",
                code_object=f"timing:{a}->{b}",
                message=f"Designed MUST_PRECEDE {a} → {b} is only "
                        "supported as MAY.",
                why="Claim semantics: a MAY-order cannot prove the design "
                    "timing; reported UNKNOWN, not contradiction (§15).",
                truth_class="OBSERVED", coverage="PARTIAL"))
        elif order == "CONTRADICTED":
            diffs.append(LvsDiff(
                kind="timing", status=LvsStatus.MISMATCH,
                design_object=f"timing:{a}->{b}",
                code_object=f"timing:{b}->{a}",
                message=f"Designed MUST_PRECEDE {a} → {b} is contradicted "
                        "by code ordering.",
                why="Deterministic contradiction in ordering evidence."))
        else:
            diffs.append(LvsDiff(
                kind="timing", status=LvsStatus.UNKNOWN,
                design_object=f"timing:{a}->{b}",
                message=f"Cannot verify MUST_PRECEDE {a} → {b}: no ordering "
                        "evidence.",
                why="No runtime/topology ordering proof; partial coverage "
                    "answers UNKNOWN (§15).",
                coverage="PARTIAL"))
    return diffs
