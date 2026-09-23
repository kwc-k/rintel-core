"""LFortranProvider — LFortran ASR → AnalysisFact (FLOW1-ANALYZER0 §9 /
ANALYZER-MAP0 Lane B).

ASR-derived facts are Flow *implementation evidence* — an intent(out) port
here is NOT a user Architecture port; it feeds the SoftwareNetlist via the
normal AnalysisFact → Evidence/Flow chain.

Honesty rules (frozen):
- CALL statements are OBSERVED (statement-level evidence); the callee
  target is EXACT only when ASR resolved the symbol inside the same
  compilation unit (the `(SymbolTableN)` ref resolves to a unit symbol
  table); cross-file callees stay target_resolution=UNKNOWN and
  coverage=UNKNOWN — no guessing.
- Legacy F77 without INTENT → ASR reports intent Unspecified; we do NOT
  invent IN/OUT — ports are only filled from real intents.
- ASR failure (unsupported legacy file) → provider raises/returns no
  facts for the unit; callers fall back to the conservative parser.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .contract import (AnalysisFact, Coverage, ExecutionModality, FactKind,
                       ProviderCapabilities, SemanticKind, SourceLocation,
                       TargetResolution, TruthClass)
from .provider import AnalysisProvider, Scope

_DEFAULT_BIN = "/Users/wu/Documents/dh/a3/fac/analysis-tools/lfortran/lf-env/bin/lfortran"


@dataclass
class _NodeCtx:
    node: dict
    parent: Optional["_NodeCtx"]
    index: str  # stable path id, e.g. "3.1"

    @property
    def kind(self) -> str:
        return self.node.get("node", "")

    @property
    def fields(self) -> dict:
        return self.node.get("fields", {})

    def ancestor_kind(self, kind: str) -> Optional["_NodeCtx"]:
        cur = self.parent
        while cur is not None:
            if cur.kind == kind:
                return cur
            cur = cur.parent
        return None


class LFortranProvider(AnalysisProvider):
    provider_id = "lfortran"

    def __init__(self, binary: str = _DEFAULT_BIN,
                 runtime_env: Optional[dict] = None,
                 extra_flags: Optional[list] = None,
                 repo_id: str = "", snapshot_id: Optional[str] = None):
        self._bin = binary or shutil.which("lfortran") or _DEFAULT_BIN
        self._env = dict(runtime_env or {})
        self._extra_flags = list(extra_flags or [])
        self._repo_id = repo_id
        self._snapshot_id = snapshot_id
        self.provider_version = self._version() or "?"

    # -- subprocess -----------------------------------------------------------
    def _version(self) -> Optional[str]:
        try:
            proc = subprocess.run([self._bin, "--version"], capture_output=True,
                                  text=True, timeout=20, env=self._env)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            if "LFortran version" in line:
                return line.split(":", 1)[1].strip()
        return proc.stdout.strip().splitlines()[0] if proc.stdout else None

    def asr(self, path: str, fixed_form: bool = False,
            implicit_interface: bool = False) -> Optional[dict]:
        """ASR JSON or None (unsupported/missing binary — caller falls back)."""
        if not Path(self._bin).exists() or not Path(path).exists():
            return None
        cmd = [self._bin, "--show-asr", "--json", "--no-color"]
        if fixed_form:
            cmd.append("--fixed-form")
        if implicit_interface:
            cmd.append("--implicit-interface")
        cmd.extend(self._extra_flags)
        cmd.append(str(Path(path).resolve()))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=120, env=self._env)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return None

    # -- capabilities ---------------------------------------------------------
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(symbols=True, calls=True, cfg=True,
                                    signatures=True, types=True,
                                    diagnostics=True, cross_language=False)

    # -- ASR walking ----------------------------------------------------------
    @staticmethod
    def _iter_ctx(node: Any, index: str = "0",
                  parent: Optional[_NodeCtx] = None) -> Iterable[_NodeCtx]:
        if isinstance(node, dict) and "node" in node and isinstance(
                node["node"], str):
            ctx = _NodeCtx(node, parent, index)
            yield ctx
            fields = node.get("fields", {})
            for key, value in fields.items():
                if isinstance(value, dict):
                    yield from LFortranProvider._iter_ctx(value, f"{index}.{key}", ctx)
                elif isinstance(value, list):
                    for i, v in enumerate(value):
                        if isinstance(v, dict):
                            yield from LFortranProvider._iter_ctx(
                                v, f"{index}.{key}[{i}]", ctx)
        elif isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, dict):
                    yield from LFortranProvider._iter_ctx(value, f"{index}.{key}", parent)
                elif isinstance(value, list):
                    for i, v in enumerate(value):
                        if isinstance(v, dict):
                            yield from LFortranProvider._iter_ctx(
                                v, f"{index}.{key}[{i}]", parent)

    @staticmethod
    def _text(v: Any) -> Optional[str]:
        """Symbol refs are rendered 'name (SymbolTableN)' in --json mode.

        ASR idioms handled: fields.v (Var refs), fields.name (strings),
        nested {node,fields} wrappers.  Never falls back to a node-type
        token like "Var" — that would fabricate a value.
        """
        for _ in range(16):
            if v is None:
                return None
            if isinstance(v, str):
                return v
            if isinstance(v, list):
                v = v[0] if v else None
                continue
            if not isinstance(v, dict):
                return str(v)
            f = v.get("fields", {}) or {}
            if not isinstance(f, dict):
                return None
            nxt = None
            for key in ("v", "name", "value", "body", "test", "expr", "var"):
                cand = f.get(key)
                if isinstance(cand, (str, dict, list)) and cand:
                    nxt = cand
                    break
            if nxt is None:
                return None
            v = nxt
        return None

    @staticmethod
    def _ref_symbol_table(v: Any) -> Optional[int]:
        """Extract the SymbolTableN suffix from a rendered symbol ref."""
        text = LFortranProvider._text(v)
        if not text:
            return None
        return LFortranProvider._st_from_text(text)

    @staticmethod
    def _st_from_text(text: str) -> Optional[int]:
        i = text.rfind("(SymbolTable")
        if i < 0:
            return None
        j = text.find(")", i)
        if j < 0:
            return None
        try:
            return int(text[i + len("(SymbolTable"):j])
        except ValueError:
            return None

    def _collect(self, asr: dict, path: str) -> tuple[list, dict]:
        """(facts, diagnostics) from one compilation unit."""
        facts: list[AnalysisFact] = []
        unit_symbols: dict[str, dict] = {}   # "ST{n}.{name}" → symbol fields
        name_to_loc: dict[str, SourceLocation] = {}

        ctxs = list(self._iter_ctx(asr))

        # pass 1: symbol tables (name → var/function metadata)
        for ctx in ctxs:
            if ctx.kind.startswith("SymbolTable"):
                st_id = self._symtab_id(ctx.node)
                if st_id is None:
                    continue
                for key, sym in (ctx.fields or {}).items():
                    if isinstance(sym, dict):
                        unit_symbols[f"ST{st_id}.{key}"] = sym

        def resolve(ref) -> tuple[Optional[str], Optional[dict]]:
            """(plain name, resolved symbol fields or None).

            EXACT only when the callee is defined in the SAME compilation
            unit (its symbol table entry exists with a real definition);
            a bare name without a unit symbol table reference is a
            cross-file / unknown target (never guessed).
            """
            text = self._text(ref)
            if not text:
                return None, None
            st = self._ref_symbol_table(ref)
            plain = text.split(" (SymbolTable", 1)[0].strip()
            if st is not None:
                sym = unit_symbols.get(f"ST{st}.{plain}")
                if sym is not None:
                    return plain, sym
            return plain, None

        # pass 2: facts
        for ctx in ctxs:
            kind = ctx.kind
            loc = self._loc(ctx, path)
            if kind in ("Program", "Module"):
                name = self._text(ctx.fields.get("name")) or "?"
                facts.append(self._mk(
                    FactKind.SYMBOL, SemanticKind.DECLARATION, ctx,
                    subject=name, scope=None, loc=loc,
                    truth=TruthClass.OBSERVED, modality=ExecutionModality.MUST,
                    res=TargetResolution.EXACT, coverage=Coverage.COMPLETE,
                    metadata={"asr": "program"}))
            elif kind in ("Function", "Subroutine"):
                name, sym = resolve(ctx.fields.get("name")) or (None, None)
                facts.append(self._mk(
                    FactKind.SYMBOL, SemanticKind.DECLARATION, ctx,
                    subject=name, scope=name, loc=loc,
                    truth=TruthClass.OBSERVED, modality=ExecutionModality.MUST,
                    res=TargetResolution.EXACT, coverage=Coverage.COMPLETE,
                    metadata={"asr": kind.lower()}))
                # signature facts: args/return type from the function symtab
                self._signature_facts(ctx, name, path, facts, unit_symbols)
            elif kind in ("SubroutineCall", "FunctionCall"):
                self._call_fact(ctx, path, facts, resolve, loc)
            elif kind == "If":
                facts.append(self._mk_control(
                    SemanticKind.BRANCH, ctx, path, facts,
                    guard=self._text(ctx.fields.get("test"))))
            elif kind == "Select":
                facts.append(self._mk_control(
                    SemanticKind.BRANCH, ctx, path, facts,
                    guard=self._text(ctx.fields.get("expr"))))
            elif kind in ("DoLoop", "WhileLoop", "DoConcurrentLoop"):
                facts.append(self._mk_control(
                    SemanticKind.LOOP, ctx, path, facts,
                    guard=self._loop_guard(ctx)))
            elif kind == "Return":
                facts.append(self._mk(
                    FactKind.CONTROL_FLOW, SemanticKind.RETURN, ctx,
                    subject=self._fn_name(ctx), loc=loc,
                    truth=TruthClass.OBSERVED, modality=ExecutionModality.MUST,
                    res=TargetResolution.EXACT, coverage=Coverage.COMPLETE,
                    metadata={}))
            elif kind in ("Assignment",):
                facts.append(self._mk(
                    FactKind.DATA_FLOW, SemanticKind.WRITE, ctx,
                    subject=self._text(ctx.fields.get("target")),
                    outputs=[self._text(ctx.fields.get("target"))],
                    inputs=[self._text(ctx.fields.get("value"))],
                    loc=loc,
                    truth=TruthClass.OBSERVED, modality=self._modality(ctx),
                    res=TargetResolution.EXACT, coverage=Coverage.COMPLETE,
                    metadata={}))
        return facts, []

    @staticmethod
    def _symtab_id(node: dict) -> Optional[int]:
        """SymbolTableN node name -> N; also accepts fields.symtab_id."""
        node_name = node.get("node", "")
        if isinstance(node_name, str) and node_name.startswith("SymbolTable"):
            try:
                return int(node_name[len("SymbolTable"):])
            except ValueError:
                return None
        f = node.get("fields", {}) or {}
        v = f.get("symtab_id")
        return int(v) if isinstance(v, int) else None

    def _unit_symtab_id(self, ctx: _NodeCtx) -> Optional[int]:
        symtab = ctx.fields.get("symtab") or {}
        return self._symtab_id(symtab) if isinstance(symtab, dict) else None

    def _signature_facts(self, ctx: _NodeCtx, fn_name: Optional[str],
                         path: str, facts: list, unit_symbols: dict) -> None:
        """Arguments come from the function's OWN symbol table (they carry
        intent); types come from the function_signature (FunctionType)
        arg_types in the same order.  Legacy F77 without INTENT reports
        Unspecified — we do NOT invent IN/OUT ports."""
        fsig = ctx.fields.get("function_signature") or {}
        if isinstance(fsig, dict) and "fields" in fsig:
            fsig = fsig["fields"]
        arg_types = (fsig or {}).get("arg_types") or []
        st_id = self._unit_symtab_id(ctx)
        args: list[tuple[str, dict, int]] = []  # (name, var, index)
        if st_id is not None:
            for key, sym in unit_symbols.items():
                pass
            for key, sym in (ctx.fields.get("symtab") or {}).get(
                    "fields", {}).items():
                if isinstance(sym, dict) and sym.get("node") == "Variable" \
                        and (sym.get("fields", {}).get("presence")
                             in (None, "Required")):
                    args.append((key, sym, len(args)))
        for i, (name, sym, _idx) in enumerate(args):
            fields = sym.get("fields", {}) or {}
            intent = self._text(fields.get("intent")) or ""
            ty = arg_types[i] if i < len(arg_types) else fields.get("type")
            inputs: list[str] = []
            outputs: list[str] = []
            port_sem: str = "IN"
            if intent == "In":
                inputs = [name]
            elif intent == "Out":
                outputs = [name]
                port_sem = "OUT"
            elif intent == "InOut":
                inputs = [name]
                outputs = [name]
                port_sem = "INOUT"
            elif intent == "Unspecified":
                port_sem = "UNSPECIFIED"
            else:
                port_sem = "LOCAL"
            facts.append(self._mk(
                FactKind.SIGNATURE, SemanticKind.DECLARATION, ctx,
                subject=fn_name, inputs=inputs, outputs=outputs,
                scope=fn_name, loc=self._loc(ctx, path),
                truth=TruthClass.OBSERVED, modality=ExecutionModality.MUST,
                res=TargetResolution.EXACT, coverage=Coverage.COMPLETE,
                metadata={"port": port_sem, "intent": intent,
                          "type_text": self._type_text(ty)}))
        ret = (fsig or {}).get("return_var_type")
        ret_var = (fsig or {}).get("return_var")
        facts.append(self._mk(
            FactKind.SIGNATURE, SemanticKind.RETURN, ctx, subject=fn_name,
            scope=fn_name, loc=self._loc(ctx, path),
            truth=TruthClass.OBSERVED, modality=ExecutionModality.MUST,
            res=TargetResolution.EXACT, coverage=Coverage.COMPLETE,
            metadata={"return_type": self._type_text(ret),
                      "return_var": self._text(ret_var)}))

    def _call_fact(self, ctx: _NodeCtx, path: str, facts: list,
                   resolve, loc: Optional[SourceLocation]) -> None:
        name, sym = resolve(ctx.fields.get("name"))
        # resolved-to-variable (indirect call through an argument) is a
        # dynamic dispatch candidate — never EXACT
        callee_kind = None
        indirect = False
        if isinstance(sym, dict):
            callee_kind = sym.get("node")
            if callee_kind in ("Variable", "Var", "ExternalVariable"):
                indirect = True
                sym = None
        # argument flow: call_arg nodes carry the passed value
        args = ctx.fields.get("args") or []
        in_vals = []
        for a in args:
            if isinstance(a, dict):
                av = a.get("fields", {}).get("value")
                txt = self._text(av) or self._text(a)
                if txt:
                    txt = txt.split(" (SymbolTable", 1)[0].strip()
                in_vals.append(txt or None)
        target_res = (TargetResolution.UNKNOWN if indirect
                      else (TargetResolution.EXACT if sym is not None
                            else TargetResolution.UNKNOWN))
        cov = (Coverage.PARTIAL if indirect
               else (Coverage.COMPLETE if sym is not None
                     else Coverage.UNKNOWN))
        modality = self._modality(ctx)
        facts.append(self._mk(
            FactKind.CALL, SemanticKind.CALL, ctx,
            subject=self._fn_name(ctx), target=name,
            inputs=[v for v in in_vals if v],
            guard=None if self._guard_of(ctx) is None else
            self._guard_of(ctx).split(" (SymbolTable", 1)[0].strip(),
            loc=loc if loc else self._loc(ctx, path),
            truth=TruthClass.OBSERVED, modality=modality,
            res=target_res, coverage=cov,
            metadata={"callee_symtab_ref": self._text(ctx.fields.get("name")),
                      "resolved_in_unit": sym is not None and not indirect,
                      "callee_kind": callee_kind,
                      "indirect_call": indirect}))

    # -- helpers --------------------------------------------------------------
    def _fn_name(self, ctx: _NodeCtx) -> Optional[str]:
        fn = ctx.ancestor_kind("Function") or ctx.ancestor_kind("Subroutine") \
            or ctx.ancestor_kind("Program")
        return self._text(fn.fields.get("name")) if fn else None

    def _modality(self, ctx: _NodeCtx) -> ExecutionModality:
        for k in ("If", "Select", "DoLoop", "WhileLoop", "DoConcurrentLoop",
                  "Case"):
            if ctx.ancestor_kind(k) is not None:
                return ExecutionModality.MAY
        return ExecutionModality.MUST

    def _guard_of(self, ctx: _NodeCtx) -> Optional[str]:
        for k, guard_key in (("If", "test"), ("Select", "expr"),
                             ("DoLoop", "cond"), ("WhileLoop", "cond")):
            anc = ctx.ancestor_kind(k)
            if anc is not None:
                g = self._text(anc.fields.get(guard_key))
                if g:
                    return g.split(" (SymbolTable", 1)[0].strip()
        return None

    def _loop_guard(self, ctx: _NodeCtx) -> Optional[str]:
        for key in ("cond", "var", "start", "stop", "step"):
            v = self._text(ctx.fields.get(key))
            if v:
                return v
        return None

    @staticmethod
    def _type_text(ty: Any) -> Optional[str]:
        if not isinstance(ty, dict):
            return LFortranProvider._text(ty)
        node = ty.get("node")
        kind = ty.get("fields", {}).get("kind", 0)
        if node == "Real":
            return f"Real kind={kind}"
        if node == "Integer":
            return f"Integer kind={kind}"
        if node == "Array":
            return "Array"
        if node == "Character":
            return "Character"
        return node or None

    @staticmethod
    def _loc(ctx: _NodeCtx, path: str) -> Optional[SourceLocation]:
        pos = ctx.fields.get("loc")
        if isinstance(pos, dict):
            begin = pos.get("begin") or {}
            end = pos.get("end") or {}
            return SourceLocation(
                file=path, line=begin.get("line"), column=begin.get("column"),
                end_line=end.get("line"), end_column=end.get("column"))
        if isinstance(pos, int):
            return SourceLocation(file=path, line=pos)
        return None

    def _mk(self, fact_kind: FactKind, sem: SemanticKind, ctx: _NodeCtx, *,
            subject=None, target=None, inputs=None, outputs=None, resources=None,
            scope=None, guard=None, loc=None, truth=TruthClass.OBSERVED,
            modality=ExecutionModality.MAY, res=TargetResolution.UNKNOWN,
            coverage=Coverage.UNKNOWN, metadata=None) -> AnalysisFact:
        return AnalysisFact(
            fact_id=f"lfortran:{ctx.index}", fact_kind=fact_kind,
            semantic_kind=sem, provider=self.provider_id,
            provider_version=self.provider_version, repo_id=self._repo_id,
            snapshot_id=self._snapshot_id, truth_class=truth,
            execution_modality=modality, target_resolution=res,
            coverage=coverage, subject=subject, target=target,
            inputs=[i for i in (inputs or []) if i],
            outputs=[o for o in (outputs or []) if o],
            resources=resources or [], scope=scope, guard=guard,
            source_location=loc,
            metadata=dict(metadata or {}))

    def _mk_control(self, sem: SemanticKind, ctx: _NodeCtx, path: str,
                    facts: list, guard: Optional[str]) -> AnalysisFact:
        return self._mk(
            FactKind.CONTROL_FLOW, sem, ctx, subject=self._fn_name(ctx),
            guard=guard, scope=self._fn_name(ctx), loc=self._loc(ctx, path),
            truth=TruthClass.OBSERVED, modality=ExecutionModality.MUST,
            res=TargetResolution.EXACT, coverage=Coverage.COMPLETE,
            metadata={"asr_node": ctx.kind})

    # -- AnalysisProvider API -------------------------------------------------
    def _unit_facts(self, scope: Scope) -> list[AnalysisFact]:
        path = scope.file
        if not path:
            return []
        fixed = path.endswith((".f", ".for", ".F", ".FOR"))
        asr = self.asr(path, fixed_form=fixed, implicit_interface=False)
        if asr is None:
            # legacy routine without explicit interfaces → fallback flag
            asr = self.asr(path, fixed_form=fixed, implicit_interface=True)
        if asr is None:
            return []
        facts, _diag = self._collect(asr, path)
        return facts

    def symbols(self, scope: Scope):
        self._require("symbols")
        for f in self._unit_facts(scope):
            if f.fact_kind is FactKind.SYMBOL:
                yield f

    def calls(self, scope: Scope):
        self._require("calls")
        for f in self._unit_facts(scope):
            if f.fact_kind is FactKind.CALL:
                yield f

    def control_flow(self, scope: Scope):
        self._require("cfg")
        for f in self._unit_facts(scope):
            if f.fact_kind is FactKind.CONTROL_FLOW:
                yield f

    def data_flow(self, scope: Scope):
        self._require("dataflow")
        return iter([])  # ASR provides no dataflow edge analysis (honest)

    def signature(self, scope: Scope):
        self._require("signatures")
        for f in self._unit_facts(scope):
            if f.fact_kind is FactKind.SIGNATURE:
                yield f

    def type_of(self, scope: Scope):
        self._require("types")
        for f in self._unit_facts(scope):
            if f.fact_kind is FactKind.TYPE or (
                    f.fact_kind is FactKind.SIGNATURE
                    and f.metadata.get("type_text")):
                yield f

    def diagnostics(self, scope: Scope):
        self._require("diagnostics")
        return iter([])

    def source_location(self, entity: str) -> Optional[SourceLocation]:
        return None
