"""SEMANTIC-SUBSTRATE1 kernel extractors (C + Fortran, real FAC parse set).

Evidence-strict regex walkers in the style of DATA-INTERFACE0: every object
carries the source line it came from; extraction is PARTIAL (`coverage`) by
default and NEVER promotes UNKNOWN -> EXACT without a scoped match (§19).

Identity rule (KS2): symbol_id = symbol:{kind}:{declaration_context}::{name}
— the declaration context (FILE -> FUNCTION -> BLOCK chain) makes A::veff and
B::veff different Symbols; shadowed names are different Symbols (K1/K2).
"""
from __future__ import annotations

import re
from pathlib import Path

from rintel.kernel.model import (ArgumentBinding, Context, Evidence, Location,
                                Operation, Reference, Symbol, TypeTerm, Value)

# ---------------------------------------------------------------------------
# shared registers
# ---------------------------------------------------------------------------

C_KEYWORDS = {
    "if", "while", "for", "switch", "return", "sizeof", "do", "else", "case",
    "break", "continue", "goto", "typedef", "struct", "union", "enum",
    "static", "const", "volatile", "register", "extern", "inline", "void",
    "char", "short", "int", "long", "float", "double", "unsigned", "signed",
    "default", "define", "include",
}
C_STDIO = {"printf", "fprintf", "sprintf", "snprintf", "scanf", "fscanf",
           "fopen", "fclose", "fread", "fwrite", "fputs", "fgets", "puts",
           "getc", "putc", "fseek", "fflush", "perror", "sscanf", "popen"}
F_KEYWORDS = {"INTEGER", "REAL", "DOUBLE", "COMPLEX", "CHARACTER", "LOGICAL",
              "PARAMETER", "CALL", "IF", "THEN", "ELSE", "ENDIF", "DO", "ENDDO",
              "RETURN", "STOP", "CONTINUE", "DATA", "SAVE", "COMMON",
              "IMPLICIT", "EXTERNAL", "INTRINSIC", "DIMENSION", "GO", "TO",
              "GOTO", "CYCLE", "EXIT", "WRITE", "READ", "OPEN", "CLOSE",
              "FORMAT", "CONTAINS", "USE", "IMPLICIT", "END", "GOTO",
              "ASSIGN", "BACKSPACE", "REWIND", "REWIND", "FLUSH"}


class Extractor:
    """One extraction run = one revision's kernel objects."""

    def __init__(self, revision: str = "frozen-0"):
        self.revision = revision
        self.contexts: list[Context] = []
        self.symbols: list[Symbol] = []
        self.references: list[Reference] = []
        self.operations: list[Operation] = []
        self.values: list[Value] = []
        self.locations: list[Location] = []
        self.bindings: list[ArgumentBinding] = []
        self.type_terms: list[TypeTerm] = []
        self._sym_by_key: dict[tuple, Symbol] = {}
        self._sym_by_id: dict[str, Symbol] = {}
        self._idx_by_ctx: dict[str, dict[str, list[str]]] = {}   # ctx -> name -> [ids]
        self._by_name: dict[str, list[str]] = {}                 # name -> [ids] (universe)
        self.macro_bindings: list[dict] = []                     # cfortran/f2c linkage evidence
        self.macro_targets: dict[str, list[dict]] = {}           # macro name -> bindings
        self.unified: dict[str, str] = {}                        # member id -> canonical id
        self.canonical_reps: dict[str, list[str]] = {}           # canonical -> member ids
        self.fn_ctx: dict[str, str] = {}      # CALLABLE symbol id -> FUNCTION context id
        self.file_ctx_by_rel: dict[str, str] = {}   # rel path -> FILE context id
        self._counter = 0

    # ------------------------------------------------------------------ ids
    def _nid(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}:{self._counter}"

    def add_context(self, kind, span_, parent=None, lang=None,
                    evidence=None) -> Context:
        ctx = Context(context_id=self._nid(f"ctx:{kind}"), kind=kind,
                      parent_context_id=parent, source_span=span_,
                      language=lang, revision=self.revision,
                      evidence=evidence or [])
        self.contexts.append(ctx)
        return ctx

    def add_symbol(self, name, kind, decl_ctx: Context, span_, lang,
                   owner=None, role="REGULAR", mutability="UNKNOWN",
                   dtype=None, storage="UNKNOWN", evidence=None,
                   is_definition=True, is_declaration=False) -> Symbol:
        sid = f"symbol:{kind}:{decl_ctx.context_id}::{name}"
        if sid in self._sym_by_id:
            sym = self._sym_by_id[sid]
            # same declaration context: a later DEFINITION upgrades the
            # existing prototype symbol (decl+def in one file = ONE Symbol)
            if is_definition and not sym.is_definition:
                sym.is_definition = True
                sym.is_declaration = False
                if storage != "UNKNOWN":
                    sym.storage_class = storage
                sym.owner = owner or sym.owner
                sym.evidence = list(sym.evidence) + list(evidence or [])
            return sym
        sym = Symbol(symbol_id=sid, name=name, kind=kind,
                     declaration_context=decl_ctx.context_id, owner=owner,
                     source_span=span_, language=lang, revision=self.revision,
                     evidence=evidence or [], role=role, mutability=mutability,
                     dtype=dtype, storage_class=storage,
                     is_definition=is_definition, is_declaration=is_declaration)
        self.symbols.append(sym)
        self._sym_by_id[sid] = sym
        self._idx_by_ctx.setdefault(decl_ctx.context_id, {}).setdefault(name, []).append(sid)
        self._by_name.setdefault(name, []).append(sid)
        return sym

    def add_value(self, kind, dtype=None, literal=None, representing=None,
                  points_to=None, span_=None, lang=None, evidence=None) -> Value:
        v = Value(value_id=self._nid("value"), kind=kind, dtype=dtype,
                  literal=literal, representing=representing or [],
                  points_to=points_to or [], source_span=span_, language=lang,
                  revision=self.revision, evidence=evidence or [])
        self.values.append(v)
        return v

    def add_location(self, kind, name=None, container=None, storage="UNKNOWN",
                     span_=None, lang=None, evidence=None) -> Location:
        loc = Location(location_id=self._nid("loc"), kind=kind, name=name,
                       container=container, storage_class=storage,
                       source_span=span_, language=lang, revision=self.revision,
                       evidence=evidence or [])
        self.locations.append(loc)
        return loc

    def add_reference(self, text, ctx: Context, span_, lang, resolution,
                      candidates=None, symbol_id=None, evidence=None) -> Reference:
        r = Reference(reference_id=self._nid("ref"), text=text,
                      context_id=ctx.context_id, source_span=span_,
                      language=lang, revision=self.revision,
                      resolution=resolution,
                      candidate_symbol_ids=candidates or [],
                      symbol_id=symbol_id if resolution == "EXACT" else None,
                      evidence=evidence or [])
        self.references.append(r)
        return r

    def add_operation(self, kind, ctx: Context, actor=None, inputs=None,
                      outputs=None, target_reference=None, guard=None,
                      resource_refs=None, span_=None, lang=None,
                      truth="INFERRED", coverage="PARTIAL", evidence=None,
                      callee_entry=None, return_site=None, state_use="NONE",
                      bindings=None) -> Operation:
        op = Operation(operation_id=self._nid("op"), kind=kind,
                       context_id=ctx.context_id, actor=actor,
                       inputs=inputs or [], outputs=outputs or [],
                       target_reference=target_reference, guard=guard,
                       resource_refs=resource_refs or [], source_span=span_,
                       language=lang, revision=self.revision, truth=truth,
                       coverage=coverage, evidence=evidence or [],
                       call_site_id=self._nid("call_site"),
                       callee_entry=callee_entry, return_site=return_site,
                       state_use=state_use)
        self.operations.append(op)
        self.bindings.extend(bindings or [])
        return op

    def add_type_term(self, name: str, ctx: Context, span_, lang,
                      evidence=None) -> TypeTerm:
        from rintel.kernel.model import TypeTerm
        for t in self.type_terms:
            if t.name == name and t.context_id == ctx.context_id:
                return t
        t = TypeTerm(type_term_id=self._nid("type"), name=name,
                     context_id=ctx.context_id, language=lang,
                     source_span=span_, revision=self.revision,
                     evidence=evidence or [])
        self.type_terms.append(t)
        return t

    # ------------------------------------------------------------- lookups
    def symbols_in(self, ctx: Context) -> list[Symbol]:
        return [self._sym_by_id[i]
                for names in self._idx_by_ctx.get(ctx.context_id, {}).values()
                for i in names]

    def symbol_of(self, sid: str) -> Symbol | None:
        return self._sym_by_id.get(sid)

    def canonical_of(self, sid: str) -> str:
        """SAME_CALLABLE representative (LC4): decl+def unify to one identity."""
        return self.unified.get(sid, sid)

    def _canonicalize(self, ids: list[str]) -> tuple[str, list[str]]:
        canon = sorted({self.canonical_of(i) for i in ids})
        if len(canon) == 1:
            return "EXACT", canon
        return "CANDIDATE_SET", canon

    def unify_callables(self) -> int:
        """DECLARES/DEFINES/SAME_CALLABLE (LC4, §10).

        Group CALLABLE symbols by name; if the universe contains exactly ONE
        definition with that name, every declaration (header prototype) of the
        name unifies onto it (canonical = definition).  Multiple definitions
        (e.g. two file-local statics with the same name) NEVER merge — they
        are different Functions (KS2).  Returns the number of unified groups.
        """
        groups: dict[str, list[Symbol]] = {}
        for s in self.symbols:
            if s.kind == "CALLABLE":
                groups.setdefault(s.name, []).append(s)
        for name, syms in groups.items():
            defs = [s for s in syms if s.is_definition]
            if len(defs) == 1:
                canon = defs[0].symbol_id
            elif len(defs) == 0:
                decls = sorted((s for s in syms if s.is_declaration),
                               key=lambda s: s.source_span.get("file") or "")
                canon = decls[0].symbol_id if decls else syms[0].symbol_id
            else:
                continue                       # ambiguous → no merge
            for s in syms:
                self.unified[s.symbol_id] = canon
            self.canonical_reps[canon] = sorted(s.symbol_id for s in syms)
            canon_sym = self._sym_by_id.get(canon)
            if canon_sym:
                for s in syms:
                    s.canonical_callable_id = canon
        return len(self.canonical_reps)

    # --------------------------------------------------- macro linkage (§12)
    def register_macro_binding(self, macro_name: str, fortran_name: str | None,
                               wrapper: str | None, kind: str, file_: str,
                               line: int, evidence_expr: str) -> None:
        rec = {"macro": macro_name, "fortran": fortran_name, "wrapper": wrapper,
               "kind": kind, "file": file_, "line": line,
               "evidence": evidence_expr[:120].strip()}
        self.macro_bindings.append(rec)
        self.macro_targets.setdefault(macro_name, []).append(rec)

    def macro_resolve(self, name: str) -> tuple[str, list[str], list[Evidence]]:
        """cfortran/f2c macro → Fortran symbol (evidence-backed linkage, §12).

        The binding text is parsed from the real header (cf77.h/f2c.h); a
        resolution is EXACT only when the Fortran target symbol is present in
        the covered universe.  The binding record itself is the evidence —
        never string-name guessing.
        """
        recs = self.macro_targets.get(name) or []
        if not recs:
            return "UNKNOWN", [], []
        fortran_names = sorted({r["fortran"] for r in recs if r.get("fortran")})
        evidence: list[Evidence] = []
        for rec in recs:
            evidence.append(Evidence("RESOLUTION",
                                     f"macro {name} -> {rec['fortran'] or rec['wrapper']} "
                                     f"({rec['file']}:{rec['line']}, {rec['kind']})",
                                     rec["line"]))
        cands: list[str] = []
        for fn in fortran_names:
            for cand_name in {fn.upper(), fn.lower(), fn}:
                ids = [i for i in self._by_name.get(cand_name, [])
                       if self._sym_by_id[i].kind == "CALLABLE"]
                if ids:
                    cands.extend(ids)
        if not cands:
            return "UNKNOWN", [], evidence       # binding exists, target outside universe
        canon = sorted({self.canonical_of(i) for i in cands})
        if len(canon) == 1:
            return "EXACT", canon, evidence
        return "CANDIDATE_SET", canon, evidence

    def resolve(self, name: str, ctx: Context, lang: str) -> tuple[str, list[str]]:
        """lexical-scope resolution -> (resolution, [candidate ids]).

        Walks the context chain (innermost -> FILE) — the strict scope graph
        from §3.  The INNERMOST scope that contains any declaration of the
        name decides: 1 canonical callable -> EXACT; >1 in that same scope ->
        CANDIDATE_SET (shadowing must NOT merge — the inner declaration wins,
        KS2/K1); outer scopes are only consulted when inner scopes have no
        match.  No scope at all -> UNKNOWN.
        """
        chain: list[Context] = [ctx]
        while chain[-1].parent_context_id:
            parent = next((c for c in self.contexts
                           if c.context_id == chain[-1].parent_context_id), None)
            if parent is None:
                break
            chain.append(parent)
        for c in chain:
            found = [self._sym_by_id[i] for i in
                     self._idx_by_ctx.get(c.context_id, {}).get(name, [])]
            if not found:
                continue
            return self._canonicalize([s.symbol_id for s in found])
        # macro linkage first: an explicit cfortran/f2c binding record (header
        # evidence) outranks the generic same-name universe scan — a callsite
        # macro such as DGESV is NOT ambiguous even though a NAMED_CONSTANT
        # macro of the same name exists (macro constants never call).  When
        # the binding exists but its Fortran target is OUTSIDE the universe,
        # the honest answer is UNKNOWN-with-binding-evidence — the macro
        # constant itself is NEVER the call target (§12).
        mres, mcands, _mev = self.macro_resolve(name)
        if mres != "UNKNOWN":
            return mres, mcands
        if self.macro_targets.get(name):
            return "UNKNOWN", []
        # kernel-universe fallback: cross-file references (CALL DSTEQR from
        # dsbev.f, extern globals from another file).  Only file-scope
        # CALLABLEs with EXTERNAL linkage and GLOBAL/STATIC/EXTERNAL DATA
        # participate — function locals NEVER leak across files, and a
        # file-local (static) function is invisible outside its own file
        # (the scope walk above already found same-file statics).  Ambiguous
        # -> CANDIDATE_SET, never merged (KS2).
        universe = [self._sym_by_id[i] for i in self._by_name.get(name, [])
                    if (self._sym_by_id[i].kind == "CALLABLE"
                        and self._sym_by_id[i].storage_class
                        in ("GLOBAL", "EXTERNAL"))
                    or (self._sym_by_id[i].kind == "DATA"
                        and self._sym_by_id[i].storage_class
                        in ("GLOBAL", "STATIC", "EXTERNAL"))]
        if universe:
            return self._canonicalize([s.symbol_id for s in universe])
        # macro linkage fallback (call-site macros like cfortran DGESV)
        mres, mcands, _mev = self.macro_resolve(name)
        return mres, mcands
