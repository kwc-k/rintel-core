"""Go adapter (tree-sitter-go).  Nodes: PACKAGE / FUNCTION / METHOD / TYPE /
INTERFACE / FIELD / VARIABLE.  Edges: CONTAINS, IMPORTS, CALLS, INHERITS
(interface embedding, round 1 via meta only)."""
from __future__ import annotations

from ..model import (Callsite, EdgeSpec, ImportBinding, Node, ParsedFile, Ref,
                     REF_NAME, REF_QNAME)
from .base import LanguageAdapter


class GoAdapter(LanguageAdapter):
    language = "go"
    extensions = (".go",)

    def extract(self, relpath: str, src: str, root) -> ParsedFile:
        pf = ParsedFile(language="go", path=relpath)
        pkg = "main"
        for c in root.named_children:
            if c.type == "package_clause":
                for cand in self.children_by_type(c, "package_identifier"):
                    pkg = self.text(cand, src)
                    break
        s = self.range(root)
        pf.nodes.append(Node(kind="PACKAGE", name=pkg, qname=pkg,
                             language="go", path=relpath,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3],
                             meta={"dir": relpath.rsplit("/", 1)[0]}))
        scope = {"pkg": pkg, "imports": [], "vars": {}}
        for c in root.named_children:
            if c.type == "import_declaration":
                self._imports(c, scope, pf, src)
        for c in root.named_children:
            t = c.type
            if t == "function_declaration":
                self._function(c, scope, pf, src)
            elif t == "method_declaration":
                self._method(c, scope, pf, src)
            elif t == "type_declaration":
                self._type_decl(c, scope, pf, src)
        # scan bodies (after defs collected) for call sites
        for c in root.named_children:
            if c.type in ("function_declaration", "method_declaration"):
                self._collect_vars(c, scope, pf, src)
                self._scan_calls(c, scope, pf, src)
        return pf

    # ------------------------------------------------------------------
    def _imports(self, node, scope, pf: ParsedFile, src: str):
        for spec in self.children_by_type(node, "import_spec"):
            alias = None
            for c in spec.named_children:
                if c.type == "package_identifier":
                    alias = self.text(c, src)
            path = ""
            for sl in self.children_by_type(spec, "interpreted_string_literal"):
                path = self.text(sl, src).strip('"')
            if not path:
                continue
            local = alias or path.rsplit("/", 1)[-1]
            scope["imports"].append({"local": local, "path": path})
            pf.imports.append(ImportBinding(path=pf.path, line=self.line(spec),
                                            module_qname=path, local=local,
                                            src_qname=scope["pkg"]))

    def _function(self, node, scope, pf: ParsedFile, src: str):
        ids = self.children_by_type(node, "identifier")
        if not ids:
            return
        name = self.text(ids[0], src)
        qname = f"{scope['pkg']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="FUNCTION", name=name, qname=qname,
                             language="go", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["pkg"]),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))

    def _method(self, node, scope, pf: ParsedFile, src: str):
        # receiver is the FIRST parameter_list; method name is field_identifier
        pls = self.children_by_type(node, "parameter_list")
        type_name = None
        if pls:
            for c in pls[0].named_children:
                if c.type == "type_identifier":
                    type_name = self.text(c, src)
                elif c.type == "pointer_type":
                    for tc in self.children_by_type(c, "type_identifier"):
                        type_name = self.text(tc, src)
        name = None
        for c in node.named_children:
            if c.type in ("field_identifier", "identifier") and c not in pls:
                name = self.text(c, src)
                break
        if not name:
            return
        qname = f"{scope['pkg']}.{type_name}.{name}" if type_name \
            else f"{scope['pkg']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="METHOD", name=name, qname=qname,
                             language="go", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        if type_name:
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, f"{scope['pkg']}.{type_name}"),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(node)))
        else:
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, scope["pkg"]),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(node)))

    def _type_decl(self, node, scope, pf: ParsedFile, src: str):
        for spec in self.children_by_type(node, "type_spec"):
            ids = self.children_by_type(spec, "type_identifier")
            if not ids:
                continue
            name = self.text(ids[0], src)
            qname = f"{scope['pkg']}.{name}"
            kind = "TYPE"
            meta: dict = {}
            for c in spec.named_children:
                if c.type == "struct_type":
                    kind = "TYPE"
                    meta["go_kind"] = "struct"
                elif c.type == "interface_type":
                    kind = "INTERFACE"
                    meta["go_kind"] = "interface"
            s = self.range(spec)
            pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                                 language="go", path=pf.path,
                                 start_line=s[0], start_col=s[1],
                                 end_line=s[2], end_col=s[3], meta=meta))
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, scope["pkg"]),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(spec)))
            if kind == "TYPE":
                st = self.child_by_type(spec, "struct_type")
                fdl = self.child_by_type(st, "field_declaration_list") if st else None
                if fdl is not None:
                    for fd in self.children_by_type(fdl, "field_declaration"):
                        for ident in fd.named_children:
                            if ident.type == "field_identifier":
                                fname = self.text(ident, src)
                                fq = f"{qname}.{fname}"
                                pf.nodes.append(Node(kind="FIELD", name=fname,
                                                     qname=fq, language="go",
                                                     path=pf.path,
                                                     start_line=self.line(ident),
                                                     start_col=self.col(ident),
                                                     end_line=self.line(ident),
                                                     end_col=self.col(ident) + len(fname)))
                                pf.edges.append(EdgeSpec(kind="CONTAINS",
                                                         src=Ref(REF_QNAME, qname),
                                                         dst=Ref(REF_QNAME, fq),
                                                         confidence=1.0,
                                                         line=self.line(ident)))

    # ------------------------------------------------------------------
    def _scan_calls(self, node, scope, pf: ParsedFile, src: str):
        def walk(n):
            for c in n.named_children:
                if c.type == "call_expression":
                    self._handle_call(c, scope, pf, src)
                    walk(c)
                elif c.type == "composite_literal":
                    # `calc.Calculator{}` / `Calculator{}` construction
                    cands: list[str] = []
                    for qt in self.children_by_type(c, "qualified_type"):
                        pids = self.children_by_type(qt, "package_identifier")
                        tids = self.children_by_type(qt, "type_identifier")
                        if pids and tids:
                            cands.append(f"{self.text(pids[0], src)}.{self.text(tids[0], src)}")
                    for tid in self.children_by_type(c, "type_identifier"):
                        cands.append(f"{scope['pkg']}.{self.text(tid, src)}")
                    if cands:
                        pf.callsites.append(Callsite(
                            path=pf.path, line=self.line(c), col=self.col(c),
                            callee=cands[0].rsplit(".", 1)[-1],
                            candidates=cands, ckind="construct"))
                    walk(c)
                elif c.type in ("function_declaration", "method_declaration",
                                "func_literal"):
                    continue
                else:
                    walk(c)
        walk(node)

    def _handle_call(self, node, scope, pf: ParsedFile, src: str):
        fn = node.named_children[0] if node.named_children else None
        if fn is None:
            return
        line, col = self.line(node), self.col(node)
        if fn.type == "identifier":
            name = self.text(fn, src)
            pf.callsites.append(Callsite(
                path=pf.path, line=line, col=col, callee=name,
                candidates=[f"{scope['pkg']}.{name}", name], ckind="call"))
        elif fn.type == "selector_expression":
            named = fn.named_children
            obj = named[0] if named else None
            field = named[-1] if named else None
            field_name = self.text(field, src) if field else ""
            obj_name = self.text(obj, src) if obj else ""
            if not obj_name or not field_name:
                return
            cands: list[str] = []
            if obj_name in scope.get("vars", {}):
                cands.append(f"{scope['vars'][obj_name]}.{field_name}")
            for imp in scope.get("imports", []):
                if imp["local"] == obj_name:
                    base = imp["path"].rsplit("/", 1)[-1]
                    cands.append(f"{base}.{field_name}")
                    cands.append(f"{imp['path']}.{field_name}")
            cands.append(f"{obj_name}.{field_name}")
            pf.callsites.append(Callsite(
                path=pf.path, line=line, col=col, callee=field_name,
                candidates=cands, ckind="attribute"))
        else:
            pf.callsites.append(Callsite(path=pf.path, line=line, col=col,
                                         callee=self.text(fn, src)[:60],
                                         candidates=[], ckind="call"))

    def _collect_vars(self, node, scope, pf: ParsedFile, src: str):
        """`c := Calculator{}` -> vars[c] = pkg.Calculator (same-package and
        imported-package types)."""
        def walk(n):
            for c in n.named_children:
                if c.type in ("short_var_declaration", "var_declaration",
                              "assignment_statement"):
                    ids = [i for i in c.named_children if i.type == "identifier"]
                    right = c.named_children[-1] if c.named_children else None
                    if ids and right:
                        vname = self.text(ids[0], src)
                        tq = None
                        if right.type == "composite_literal":
                            for qt in self.children_by_type(right, "qualified_type"):
                                pids = self.children_by_type(qt, "package_identifier")
                                tids = self.children_by_type(qt, "type_identifier")
                                if pids and tids:
                                    tq = f"{self.text(pids[0], src)}.{self.text(tids[0], src)}"
                            if tq is None:
                                for tid in self.children_by_type(right, "type_identifier"):
                                    tq = f"{scope['pkg']}.{self.text(tid, src)}"
                            if tq is None:
                                for qt in self.children_by_type(right, "qualified_type"):
                                    tids = self.children_by_type(qt, "type_identifier")
                                    if tids:
                                        tq = self.text(tids[0], src)
                        elif right.type == "identifier":
                            rname = self.text(right, src)
                            for imp in scope.get("imports", []):
                                if imp["local"] == rname:
                                    base = imp["path"].rsplit("/", 1)[-1]
                                    tq = f"{base}.{rname}"
                        if tq:
                            scope["vars"][vname] = tq
                elif c.type == "func_literal":
                    continue
                else:
                    walk(c)
        walk(node)
