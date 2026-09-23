"""Fortran kernel extractor (real FAC: lapack/dsbev.f, blas/dger.f,
blas/dgemm.f, blas/xerbla.f, lapack/dsteqr.f — kernel parse set per §26).

Reuses DATA-INTERFACE0 parsing (parse_fortran_file gives per-subroutine
start/end + ports; _fortran_decls gives locals) so direction/dtype/shape
evidence is the SAME single truth source as the data interface.

Two phases: scan_* registers symbols (Phase A, all files), walk_* records
operations/references (Phase B).  This makes cross-file resolution
(CALL DSTEQR from dsbev.f) exact without order dependence (KS2/KS5).
"""
from __future__ import annotations

import re
from pathlib import Path

from rintel.kernel.extract import F_KEYWORDS, Extractor
from rintel.kernel.model import ArgumentBinding, Evidence, span
from rintel.data_interface import (F_CALL_HEAD, _call_args, _fortran_decls,
                                   _split_args, parse_fortran_file)

_IDENT_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\b")
_INDEX_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(\s*([^)]*)\)")
_COMPARE_RE = re.compile(r"\.(LT|LE|GT|GE|EQ|NE)\.")
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")
_BRANCH_RE = re.compile(r"^\s*(IF\s*\(|DO\s+\S+|DO\s+\S+\s+\S|DO\s*$)", re.I)
_RET_RE = re.compile(r"^\s*RETURN\b", re.I)


def _strip_f_strings(ln: str) -> str:
    """Replace '...' / "..." string literals with spaces (line-local)."""
    out = []
    i, n = 0, len(ln)
    while i < n:
        c = ln[i]
        if c in "'\"":
            j = i + 1
            while j < n and ln[j] != c:
                j += 1
            out.append(" " * (j - i + 1 if j < n else j - i))
            i = (j + 1) if j < n else j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _prep_lines(text: str) -> list[str]:
    """comment + string stripped lines (fixed form: column-1 *cC! only)."""
    blines = []
    for ln in text.split("\n"):
        if re.match(r"^[*cC!]", ln):
            blines.append("")
        else:
            blines.append(_strip_f_strings(re.sub(r"!\s*.*$", "", ln)))
    return blines


def scan_fortran_file(ex: Extractor, root: Path, rel: str) -> list[tuple]:
    """Phase A: FILE context, CALLABLE symbols, FUNCTION contexts,
    parameter symbols (from data-interface ports), local symbols (decl scan).
    Returns [(name, callable_symbol_id, fn_context_id, code_lines)]."""
    lang = "F90" if rel.endswith(".f90") else "FORTRAN77"
    path = root / rel
    text = path.read_text(encoding="utf-8", errors="replace")
    blines = _prep_lines(text)
    file_ctx = ex.add_context("FILE", span(rel, 1, None), None, lang,
                              [Evidence("STRUCTURE", f"file {rel}")])
    ex.file_ctx_by_rel[rel] = file_ctx.context_id

    plan: list[tuple] = []
    for meta, ports in parse_fortran_file(path):
        name = meta["name"]
        start = meta["line"]
        end = meta.get("end_line", start + 200)
        code_lines = blines[start - 1:end]
        # skip the SUBROUTINE header line itself (index 0) and join fixed-form
        # continuation lines (`$`/`&`): the DSTEQR arg list spans two lines
        code_lines = code_lines[1:]
        joined: list[str] = []
        ln_nos: list[int] = []          # TRUE source line per joined line
        for k, ln in enumerate(code_lines):
            if re.match(r"^\s*[$\&]", ln):
                if joined:
                    joined[-1] += " " + re.sub(r"^\s*[$\&]\s*", "", ln)
            else:
                joined.append(ln)
                ln_nos.append(start + 1 + k)
        code_lines = joined

        ev0 = [Evidence("DECL", f"SUBROUTINE {name}", start)]
        csym = ex.add_symbol(name, "CALLABLE", file_ctx, span(rel, start, end),
                             lang, owner=rel, storage="EXTERNAL", evidence=ev0)
        fn_ctx = ex.add_context("FUNCTION", span(rel, start, end),
                                parent=file_ctx.context_id, lang=lang,
                                evidence=ev0)
        ex.fn_ctx[csym.symbol_id] = fn_ctx.context_id

        param_names: set[str] = {p.name for p in ports}
        for p in ports:
            if p.dtype:
                ex.add_type_term(p.dtype, fn_ctx, span(rel, start, end), lang,
                                 [Evidence("DECL", p.name, start)])
            ev = [Evidence("DECL", f"{p.name} ({p.dtype})", start)]
            symp = ex.add_symbol(p.name, "DATA", fn_ctx, span(rel, start, end),
                                 lang, owner=name, role="PARAMETER",
                                 mutability="MUTABLE", dtype=p.dtype,
                                 storage="PARAMETER", evidence=ev)
            ex.add_location("parameter", name=p.name, container=symp.symbol_id,
                            storage="PARAMETER", span_=span(rel, start, end),
                            lang=lang, evidence=ev)

        decls = _fortran_decls(code_lines)
        for dname, (dtype, _src, _bits, _shape) in decls.items():
            if dname.upper() in {p.upper() for p in param_names}:
                continue
            ev = [Evidence("DECL", dname, start)]
            if dtype:
                ex.add_type_term(dtype, fn_ctx, span(rel, start, end), lang,
                                 [Evidence("DECL", f"{dname} ({dtype})", start)])
            ls = ex.add_symbol(dname, "DATA", fn_ctx, span(rel, start, end),
                               lang, owner=name, role="REGULAR",
                               mutability="MUTABLE", dtype=dtype,
                               storage="LOCAL", evidence=ev)
            ex.add_location("variable", name=dname, container=ls.symbol_id,
                            storage="LOCAL", span_=span(rel, start, end),
                            lang=lang, evidence=ev)
        plan.append((name, csym.symbol_id, fn_ctx.context_id, code_lines, ln_nos))
    return plan


def walk_fortran_file(ex: Extractor, root: Path, rel: str, plan) -> None:
    """Phase B: CALL/assignment/BRANCH/RETURN/INDEX operations + references."""
    lang = "F90" if rel.endswith(".f90") else "FORTRAN77"
    for name, csid, fnctx_id, code_lines, ln_nos in plan:
        fn_ctx = next(c for c in ex.contexts if c.context_id == fnctx_id)
        csym = next(s for s in ex.symbols if s.symbol_id == csid)
        start = fn_ctx.source_span["start_line"]
        param_names = {s.name for s in ex.symbols_in(fn_ctx)
                       if s.role == "PARAMETER"}
        for i, ln in enumerate(code_lines):
            ln_no = ln_nos[i] if i < len(ln_nos) else start + i + 1
            st = ln.strip()
            if not st:
                continue
            ev = [Evidence("OP", st, ln_no)]

            # CALL
            cm = F_CALL_HEAD.match(ln)
            if cm:
                callee = cm.group(1)
                argclause = _call_args(ln, cm.end())
                args = _split_args(argclause)
                ref = add_reference_for(ex, callee, fn_ctx,
                                        span(rel, ln_no, ln_no), lang,
                                        [Evidence("REF", callee, ln_no)])
                callee_entry = ref.symbol_id if ref.resolution == "EXACT" else None
                bindings = []
                if callee_entry:
                    callee_fn = ex.fn_ctx.get(callee_entry)
                    if callee_fn:
                        cctx = next(c for c in ex.contexts
                                    if c.context_id == callee_fn)
                        formals = [s for s in ex.symbols_in(cctx)
                                   if s.role == "PARAMETER"]
                        for j, a in enumerate(args):
                            if j >= len(formals):
                                break
                            bindings.append(ArgumentBinding(
                                binding_id=ex._nid("bind"), call_operation_id="",
                                actual=a, formal_symbol_id=formals[j].symbol_id,
                                position=j + 1, truth="OBSERVED",
                                evidence=[Evidence("CALL", a, ln_no)]))
                op = ex.add_operation(
                    "CALL", fn_ctx, actor=csym.symbol_id,
                    target_reference=ref.reference_id,
                    span_=span(rel, ln_no, ln_no), lang=lang,
                    truth="OBSERVED" if callee_entry else "INFERRED",
                    coverage="COMPLETE" if callee_entry else "PARTIAL",
                    evidence=ev, callee_entry=callee_entry)
                for b in bindings:
                    b.call_operation_id = op.operation_id
                    ex.bindings.append(b)
                continue

            # RETURN
            if _RET_RE.match(st):
                ex.add_operation("RETURN", fn_ctx, actor=csym.symbol_id,
                                 span_=span(rel, ln_no, ln_no), lang=lang,
                                 coverage="COMPLETE", evidence=ev)
                continue

            # BRANCH (IF/DO)
            if _BRANCH_RE.match(st):
                ex.add_operation("BRANCH", fn_ctx, actor=csym.symbol_id,
                                 guard=st[:60], span_=span(rel, ln_no, ln_no),
                                 lang=lang, truth="INFERRED", coverage="PARTIAL",
                                 evidence=ev)
                if _COMPARE_RE.search(st):
                    ex.add_operation("COMPARE", fn_ctx, actor=csym.symbol_id,
                                     guard=st[:60], span_=span(rel, ln_no, ln_no),
                                     lang=lang, truth="INFERRED",
                                     coverage="PARTIAL", evidence=ev)
                continue

            # assignment WRITE (+rhs READ)
            am = _ASSIGN_RE.match(st)
            if am:
                lname = am.group(1)
                rhs = am.group(2)
                add_reference_for(ex, lname, fn_ctx, span(rel, ln_no, ln_no),
                                  lang, [Evidence("REF", lname, ln_no)])
                ex.add_operation("WRITE", fn_ctx, actor=csym.symbol_id,
                                 inputs=[], outputs=[lname],
                                 span_=span(rel, ln_no, ln_no), lang=lang,
                                 truth="INFERRED", coverage="PARTIAL", evidence=ev,
                                 state_use="WRITE" if lname in param_names else "NONE")
                for rid in _IDENT_RE.findall(rhs):
                    if rid.upper() in F_KEYWORDS or rid in param_names \
                            or re.fullmatch(r"\d+", rid):
                        continue
                    add_reference_for(ex, rid, fn_ctx, span(rel, ln_no, ln_no),
                                      lang, [Evidence("REF", rid, ln_no)])
                _add_f_value(ex, rhs, ln_no, lang, rel, fn_ctx, name)
                continue

            # INDEX ops (array element refs on otherwise non-assign lines)
            for im in _INDEX_RE.finditer(st):
                base = im.group(1)
                if base.upper() in F_KEYWORDS:
                    continue
                add_reference_for(ex, base, fn_ctx, span(rel, ln_no, ln_no),
                                  lang, [Evidence("REF", base, ln_no)])
                ex.add_operation("INDEX", fn_ctx, actor=csym.symbol_id,
                                 inputs=[im.group(2)], outputs=[base],
                                 span_=span(rel, ln_no, ln_no), lang=lang,
                                 truth="INFERRED", coverage="PARTIAL", evidence=ev)
                _add_f_value(ex, st, ln_no, lang, rel, fn_ctx, name)
    return


def _add_f_value(ex, expr, ln, lang, rel, ctx, owner) -> None:
    e = expr.strip()
    ev = [Evidence("OP", e, ln)]
    if re.fullmatch(r"[-+]?[\d.eE+]+", e):
        ex.add_value("literal", literal=e, span_=span(rel, ln, ln), lang=lang,
                     evidence=ev)
    elif re.fullmatch(r"[A-Za-z][\w]*", e):
        res, cands = ex.resolve(e, ctx, lang)
        kind = "scalar" if res != "UNKNOWN" else "unknown"
        ex.add_value(kind, representing=cands, span_=span(rel, ln, ln), lang=lang,
                     evidence=ev)
    elif re.match(r"[A-Za-z][\w]*\s*\(", e):
        name = re.match(r"[A-Za-z][\w]*", e).group(0)
        res, cands = ex.resolve(name, ctx, lang)
        if res == "EXACT" and any(s.kind == "CALLABLE" for s in ex.symbols
                                  if s.symbol_id in cands):
            ex.add_value("function_address", representing=cands,
                         span_=span(rel, ln, ln), lang=lang, evidence=ev)
        else:
            ex.add_value("array_tensor", representing=cands,
                         span_=span(rel, ln, ln), lang=lang, evidence=ev)
    else:
        ex.add_value("symbolic", span_=span(rel, ln, ln), lang=lang, evidence=ev)


def add_reference_for(ex, name, ctx, sp, lang, evidence):
    res, cands = ex.resolve(name, ctx, lang)
    return ex.add_reference(name, ctx, sp, lang, res, cands,
                            cands[0] if res == "EXACT" else None, evidence)
