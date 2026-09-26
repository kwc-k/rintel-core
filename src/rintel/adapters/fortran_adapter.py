"""Fortran adapter (tree-sitter-fortran).

Fortran is modeled with language-native nodes — MODULE / SUBMODULE / PROGRAM /
SUBROUTINE / FUNCTION / INTERFACE / TYPE / FIELD / VARIABLE — never forced
into class->method shape (spec §2 Fortran requirements).

Edges:
  IMPORTS      from `USE module` (src = using unit, dst = module node)
  CONTAINS     module/submodule/program -> its procedures and types
  CALLS        `CALL sub(...)` and function calls in expressions
  USES         type references in declarations (e.g. TYPE(point) :: p)
  IMPLEMENTS   specific procedures of a GENERIC interface
  BINDS_TO     Fortran BIND(C) symbol <-> C symbol of the same external name
  INCLUDES     INCLUDE 'file'
"""
from __future__ import annotations

from ..model import (Callsite, EdgeSpec, ImportBinding, IncludeBinding, Node,
                     ParsedFile, Ref, REF_NAME, REF_QNAME)
from .base import LanguageAdapter
from .fortran_fixed_form import normalize_fixed_form


class FortranAdapter(LanguageAdapter):
    language = "fortran"
    extensions = (".f90", ".f95", ".f03", ".f08", ".f", ".for")

    def normalize_source(self, relpath: str, source: str):
        """S6: fixed-form .f/.for files are conservatively normalized to the
        grammar's shape (with an exact original-position map)."""
        if relpath.endswith((".f90", ".f95", ".f03", ".f08")):
            return None
        return normalize_fixed_form(relpath, source)

    def __init__(self) -> None:
        super().__init__()
        # FAC-EQ0: count of expression-position name(args) that were NOT
        # registered because a local declaration proves them array accesses
        self._subscript_skipped = 0

    def extract(self, relpath: str, src: str, root) -> ParsedFile:
        pf = ParsedFile(language="fortran", path=relpath)
        self._subscript_skipped = 0
        for unit in root.named_children:
            t = unit.type
            if t == "module":
                self._unit(unit, None, pf, src)
            elif t == "submodule":
                self._submodule(unit, pf, src)
            elif t == "program":
                self._unit(unit, None, pf, src, kind="PROGRAM")
            elif t in ("subroutine", "function"):
                # external (file-scope) procedure
                self._procedure(unit, None, pf, src)
        if self._subscript_skipped:
            pf.notes.append(
                f"fortran:subscript_access_skipped={self._subscript_skipped}")
        return pf

    # ------------------------------------------------------------------
    def _unit(self, node, parent_ref, pf: ParsedFile, src: str,
              kind: str = "MODULE"):
        """MODULE / PROGRAM unit."""
        stmt = self.child_by_type(node, "module_statement") or \
               self.child_by_type(node, "program_statement")
        name_node = self.child_by_type(stmt, "name") if stmt else None
        name = self.text(name_node, src) if name_node else "unit"
        qname = name
        s = self.range(node)
        pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                             language="fortran", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3],
                             meta={"definition_source_scoped": True}))
        unit_ref = Ref(REF_QNAME, qname)
        if parent_ref is not None:
            pf.edges.append(EdgeSpec(kind="CONTAINS", src=parent_ref,
                                     dst=unit_ref, confidence=1.0,
                                     line=self.line(node)))
        self._body(node, qname, unit_ref, pf, src)
        return qname

    def _submodule(self, node, pf: ParsedFile, src: str):
        stmt = self.child_by_type(node, "submodule_statement")
        name_node = self.child_by_type(stmt, "name") if stmt else None
        parent_mod = None
        if stmt:
            mn = self.child_by_type(stmt, "module_name")
            if mn:
                inner = self.child_by_type(mn, "name")
                parent_mod = self.text(inner, src) if inner else None
        name = self.text(name_node, src) if name_node else "submod"
        qname = f"{parent_mod}__{name}" if parent_mod else name
        s = self.range(node)
        pf.nodes.append(Node(kind="SUBMODULE", name=name, qname=qname,
                             language="fortran", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3],
                             meta={"parent_module": parent_mod,
                                   "definition_source_scoped": True}))
        if parent_mod:
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, parent_mod),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(node)))
        self._body(node, qname, Ref(REF_QNAME, qname), pf, src)

    # ------------------------------------------------------------------
    def _body(self, unit_node, scope_qname: str, unit_ref, pf: ParsedFile,
              src: str, emit_vars: bool = True,
              arrays: set[str] | None = None):
        """Walk a unit body: USE / types / interfaces / CONTAINS procedures /
        module variables / call sites at unit level.

        FAC-EQ0: `arrays` is the case-insensitive set of names declared with
        dimensions in the enclosing scoping units.  A Fortran call in
        expression position `name(args)` whose name is a locally declared
        array is an array access (source-visible fact — an F77 declared array
        cannot be invoked) and is therefore NOT registered as a call site.
        """
        if arrays is None:
            arrays = set()
        arrays = arrays | self._declared_arrays(unit_node, pf, src)
        for c in unit_node.named_children:
            t = c.type
            if t == "use_statement":
                self._use(c, scope_qname, unit_ref, pf, src)
            elif t == "derived_type_definition":
                self._derived_type(c, scope_qname, pf, src)
            elif t == "interface":
                self._interface(c, scope_qname, pf, src)
            elif t == "internal_procedures":
                for proc in c.named_children:
                    if proc.type in ("subroutine", "function"):
                        self._procedure(proc, scope_qname, pf, src)
            elif t == "variable_declaration":
                if emit_vars:
                    self._variables(c, scope_qname, pf, src)
            elif t == "include_statement":
                fn = self.child_by_type(c, "filename")
                target = self.text(fn, src).strip("'\"") if fn else ""
                pf.includes.append(IncludeBinding(path=pf.path,
                                                  line=self.line(c),
                                                  target=target))
            elif t in ("subroutine_call",):
                self._call(c, scope_qname, pf, src, arrays)
            elif t == "assignment_statement":
                self._scan_expression_calls(c, scope_qname, pf, src, arrays)
            elif t in ("if_statement", "do_statement", "do_while_statement",
                       "where_statement", "select_case_statement",
                       "associate_statement", "block_statement",
                       "critical_statement", "forall_statement"):
                for ch in c.named_children:
                    self._body(ch, scope_qname, unit_ref, pf, src,
                               emit_vars=emit_vars, arrays=arrays)

    # FAC-EQ0 -------------------------------------------------------------
    def _declared_arrays(self, node, pf: ParsedFile, src: str) -> set[str]:
        """Names declared with dimensions in this scoping unit.

        Source-visible declaration forms:
          REAL A(100), W(10,10)          (variable_declaration sized)
          DIMENSION C(30) / X(5), Y(6)   (variable_modification / dimension)
          COMMON /BLK/ D(50), E(40)      (common_statement groups)
          REAL, DIMENSION(100) :: A      (free-form dimension qualifier)
        Returns lowercase names (Fortran is case-insensitive).
        """
        out: set[str] = set()

        def _sized(dd) -> None:
            ids = [i for i in dd.named_children if i.type == "identifier"]
            if ids:
                out.add(self.text(ids[0], src).lower())

        for c in node.named_children:
            t = c.type
            if t == "variable_declaration":
                tq = self.child_by_type(c, "type_qualifier")
                if tq is not None and \
                        "dimension" in self.text(tq, src).lower():
                    for ident in [i for i in c.named_children
                                  if i.type == "identifier"]:
                        out.add(self.text(ident, src).lower())
                for dd in self.children_by_type(c, "sized_declarator"):
                    _sized(dd)
            elif t == "variable_modification":
                tq = self.child_by_type(c, "type_qualifier")
                if tq is not None and \
                        self.text(tq, src).strip().lower() == "dimension":
                    for dd in self.children_by_type(c, "sized_declarator"):
                        _sized(dd)
            elif t == "common_statement":
                for vg in self.children_by_type(c, "variable_group"):
                    for dd in self.children_by_type(vg, "sized_declarator"):
                        _sized(dd)
        return out

    def _use(self, node, scope_qname: str, unit_ref, pf: ParsedFile, src: str):
        mn = self.child_by_type(node, "module_name")
        module = self.text(mn, src).strip() if mn else ""
        line = self.line(node)
        if not module:
            return
        only: list[str] | None = None
        for inc in self.children_by_type(node, "included_items"):
            only = [self.text(i, src) for i in
                    self.children_by_type(inc, "identifier")]
        pf.imports.append(ImportBinding(path=pf.path, line=line,
                                        module_qname=module, only_names=only,
                                        src_qname=scope_qname))
        pf.edges.append(EdgeSpec(kind="IMPORTS", src=unit_ref,
                                 dst=Ref(REF_QNAME, module),
                                 confidence=1.0, line=line))

    def _derived_type(self, node, scope_qname: str, pf: ParsedFile, src: str):
        stmt = self.child_by_type(node, "derived_type_statement")
        name_node = self.child_by_type(stmt, "type_name") if stmt else None
        name = self.text(name_node, src) if name_node else "type"
        qname = f"{scope_qname}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="TYPE", name=name, qname=qname,
                             language="fortran", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope_qname),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))
        for c in node.named_children:
            if c.type == "variable_declaration":
                self._variables(c, qname, pf, src, field=True)

    def _interface(self, node, scope_qname: str, pf: ParsedFile, src: str):
        stmt = self.child_by_type(node, "interface_statement")
        name_node = self.child_by_type(stmt, "name") if stmt else None
        has_name = name_node is not None
        if has_name:
            # GENERIC interface: node + specific procedures IMPLEMENT it
            name = self.text(name_node, src)
            qname = f"{scope_qname}.{name}"
            s = self.range(node)
            pf.nodes.append(Node(kind="INTERFACE", name=name, qname=qname,
                                 language="fortran", path=pf.path,
                                 start_line=s[0], start_col=s[1],
                                 end_line=s[2], end_col=s[3],
                                 meta={"interface_kind": "generic"}))
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, scope_qname),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(node)))
            for ps in self.children_by_type(node, "procedure_statement"):
                for mn in self.children_by_type(ps, "method_name"):
                    impl_name = self.text(mn, src)
                    pf.edges.append(EdgeSpec(
                        kind="IMPLEMENTS",
                        src=Ref(REF_QNAME, f"{scope_qname}.{impl_name}"),
                        dst=Ref(REF_QNAME, qname),
                        confidence=1.0, line=self.line(ps)))
        else:
            # external interface: procedures declared (not defined here)
            for c in node.named_children:
                if c.type in ("subroutine", "function"):
                    self._procedure(c, scope_qname, pf, src, defined=False)

    def _procedure(self, node, parent_qname: str | None, pf: ParsedFile,
                   src: str, defined: bool = True):
        stmt = self.child_by_type(node, "subroutine_statement") or \
               self.child_by_type(node, "function_statement")
        name_node = self.child_by_type(stmt, "name") if stmt else None
        name = self.text(name_node, src) if name_node else "proc"
        kind = "SUBROUTINE" if node.type == "subroutine" else "FUNCTION"
        qname = f"{parent_qname}.{name}" if parent_qname else name
        s = self.range(node)
        meta: dict = {"defined": defined,
                      "definition_source_scoped": bool(defined and
                                                       parent_qname is None)}
        bind_name: str | None = None
        if stmt:
            lb = self.child_by_type(stmt, "language_binding")
            if lb is not None:
                bind_name = self._bind_name(lb, name, src)
                meta["bind_name"] = bind_name
        pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                             language="fortran", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3], meta=meta))
        proc_ref = Ref(REF_QNAME, qname)
        if parent_qname:
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, parent_qname),
                                     dst=proc_ref, confidence=1.0,
                                     line=self.line(node)))
        if bind_name:
            pf.edges.append(EdgeSpec(
                kind="BINDS_TO", src=proc_ref,
                dst=Ref(REF_NAME, bind_name, language="c"),
                confidence=1.0, line=self.line(node),
                meta={"bind_name": bind_name}))
        if defined:
            self._body(node, qname, proc_ref, pf, src, emit_vars=False)

    def _bind_name(self, lb, fallback: str, src: str) -> str:
        for ka in self.children_by_type(lb, "keyword_argument"):
            ids = [c for c in ka.named_children if c.type == "identifier"]
            if ids and self.text(ids[0], src) == "NAME":
                for sl in self.children_by_type(ka, "string_literal"):
                    return self.text(sl, src).strip('"\'')
        return fallback

    def _variables(self, node, scope_qname: str, pf: ParsedFile, src: str,
                   field: bool = False):
        kind = "FIELD" if field else "VARIABLE"
        type_name: str | None = None
        it = self.child_by_type(node, "intrinsic_type")
        if it is not None:
            k = self.child_by_type(it, "kind")
            if k is not None:
                ids = [c for c in k.named_children if c.type == "identifier"]
                if ids:
                    type_name = self.text(ids[0], src)
        dt = self.child_by_type(node, "derived_type")
        if dt is not None:
            for c in dt.named_children:
                if c.type in ("type_name", "identifier"):
                    type_name = self.text(c, src)
                    break
        for decl in node.named_children:
            if decl.type == "identifier":
                name = self.text(decl, src)
                qname = f"{scope_qname}.{name}"
                pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                                     language="fortran", path=pf.path,
                                     start_line=self.line(decl),
                                     start_col=self.col(decl),
                                     end_line=self.line(decl),
                                     end_col=self.col(decl) + len(name)))
                pf.edges.append(EdgeSpec(kind="CONTAINS",
                                         src=Ref(REF_QNAME, scope_qname),
                                         dst=Ref(REF_QNAME, qname),
                                         confidence=1.0,
                                         line=self.line(decl)))
                if type_name and type_name not in ("c_int", "c_double",
                                                   "c_float", "c_long",
                                                   "default", "selected_real_kind", "selected_int_kind"):
                    pf.edges.append(EdgeSpec(kind="USES",
                                             src=Ref(REF_QNAME, scope_qname),
                                             dst=Ref(REF_NAME, type_name),
                                             confidence=0.8,
                                             line=self.line(decl)))
            elif decl.type == "sized_declarator":
                ids = [c for c in decl.named_children
                       if c.type == "identifier"]
                if ids:
                    name = self.text(ids[0], src)
                    qname = f"{scope_qname}.{name}"
                    pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                                         language="fortran", path=pf.path,
                                         start_line=self.line(decl),
                                         start_col=self.col(decl),
                                         end_line=self.line(decl),
                                         end_col=self.col(decl) + len(name)))
                    pf.edges.append(EdgeSpec(kind="CONTAINS",
                                             src=Ref(REF_QNAME, scope_qname),
                                             dst=Ref(REF_QNAME, qname),
                                             confidence=1.0,
                                             line=self.line(decl)))

    # ------------------------------------------------------------------
    def _call(self, node, scope_qname: str, pf: ParsedFile, src: str,
              arrays: set[str] | None = None):
        ids = [c for c in node.named_children if c.type == "identifier"]
        if not ids:
            return
        callee = self.text(ids[0], src)
        pf.callsites.append(Callsite(
            path=pf.path, line=self.line(node), col=self.col(node),
            callee=callee, candidates=self._candidates(callee, scope_qname,
                                                       pf),
            ckind="call", shape=self._shape(node, "stmt")))

    def _scan_expression_calls(self, node, scope_qname: str, pf: ParsedFile,
                               src: str, arrays: set[str] | None = None):
        arrays = arrays or set()
        for c in node.named_children:
            if c.type == "call_expression":
                ids = [i for i in c.named_children if i.type == "identifier"]
                if ids:
                    callee = self.text(ids[0], src)
                    if callee.lower() in arrays:
                        # FAC-EQ0: locally declared array — `A(I)` is an
                        # array access, never a call site
                        self._subscript_skipped += 1
                        continue
                    pf.callsites.append(Callsite(
                        path=pf.path, line=self.line(c), col=self.col(c),
                        callee=callee,
                        candidates=self._candidates(callee, scope_qname, pf),
                        ckind="call", shape=self._shape(c, "expr")))
            elif c.type in ("subroutine_call", "assignment_statement",
                            "math_expression", "if_statement", "do_statement"):
                self._scan_expression_calls(c, scope_qname, pf, src, arrays)

    @staticmethod
    def _shape(node, form: str) -> dict:
        """Call shape for FAC-EQ0 classification: form + argument count."""
        al = None
        for c in node.named_children:
            if c.type == "argument_list":
                al = c
                break
        args = len(al.named_children) if al is not None else 0
        return {"form": form, "args": args}

    def _candidates(self, callee: str, scope_qname: str,
                    pf: ParsedFile) -> list[str]:
        cands: list[str] = []
        for imp in pf.imports:
            if imp.only_names:
                if callee in imp.only_names:
                    cands.append(f"{imp.module_qname}.{callee}")
            else:
                cands.append(f"{imp.module_qname}.{callee}")
        if scope_qname:
            cands.append(f"{scope_qname}.{callee}")
            # enclosing unit's scope (module) for internal-procedure calls
            if "." in scope_qname:
                outer = scope_qname.rsplit(".", 1)[0]
                if outer:
                    cands.append(f"{outer}.{callee}")
        cands.append(callee)
        return cands
