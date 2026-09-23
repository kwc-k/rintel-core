"""TypeScript / JavaScript adapter (tree-sitter-typescript / -javascript).

Nodes: MODULE / CLASS / INTERFACE / ENUM / TYPE (alias) / FUNCTION / METHOD /
VARIABLE.  Edges: CONTAINS, IMPORTS, INHERITS, IMPLEMENTS, CALLS, REFERENCES.
Relative import specifiers are resolved by the indexer against the file map.
"""
from __future__ import annotations

from ..identity import ts_module_qname
from ..model import (Callsite, EdgeSpec, ImportBinding, Node, ParsedFile, Ref,
                     REF_NAME, REF_QNAME)
from .base import LanguageAdapter


class TypeScriptAdapter(LanguageAdapter):
    language = "typescript"
    extensions = (".ts", ".tsx")
    js_mode = False

    def extract(self, relpath: str, src: str, root) -> ParsedFile:
        pf = ParsedFile(language="javascript" if self.js_mode else "typescript",
                        path=relpath)
        mod_qname = ts_module_qname(relpath)
        s = self.range(root)
        pf.nodes.append(Node(kind="MODULE", name=mod_qname, qname=mod_qname,
                             language=pf.language, path=relpath,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        imports: list[dict] = []
        self._collect_imports(root, pf, imports, mod_qname, src)
        scope = {"qname": mod_qname, "mod_qname": mod_qname,
                 "class_qname": None, "vars": {}, "imports": imports}
        for c in root.named_children:
            self._stmt(c, scope, pf, src)
        return pf

    # ------------------------------------------------------------------
    def _collect_imports(self, node, pf: ParsedFile, imports, mod_qname,
                         src: str):
        for c in node.named_children:
            if c.type == "import_statement":
                self._import(c, pf, imports, mod_qname, src)
            elif c.type in ("function_declaration", "class_declaration",
                            "method_definition", "arrow_function",
                            "function_expression"):
                continue
            else:
                self._collect_imports(c, pf, imports, mod_qname, src)

    def _import(self, node, pf: ParsedFile, imports, mod_qname, src: str):
        spec = ""
        for sl in self.children_by_type(node, "string"):
            spec = self.text(sl, src).strip("'\"")
        if not spec:
            return
        relative = spec.startswith(("./", "../", "/"))
        line = self.line(node)
        bindings: list[tuple[str, str]] = []  # (local, original)
        for c in node.named_children:
            if c.type == "import_clause":
                for ic in c.named_children:
                    if ic.type == "named_imports":
                        for isp in self.children_by_type(ic, "import_specifier"):
                            ids = self.children_by_type(isp, "identifier")
                            if not ids:
                                continue
                            original = self.text(ids[0], src)
                            local = self.text(ids[-1], src)
                            bindings.append((local, original))
                    elif ic.type == "identifier":
                        bindings.append((self.text(ic, src), "default"))
                    elif ic.type == "namespace_import":
                        ids = self.children_by_type(ic, "identifier")
                        if ids:
                            bindings.append((self.text(ids[0], src), "*"))
        for local, original in bindings:
            imports.append({"local": local, "spec": spec, "original": original})
            pf.imports.append(ImportBinding(
                path=pf.path, line=line, module_qname=spec, local=local,
                only_names=None if original in ("*", "default") else [original],
                src_qname=mod_qname,
                meta={"resolve": "relative" if relative else "bare"}))
        if not bindings:
            pf.imports.append(ImportBinding(path=pf.path, line=line,
                                            module_qname=spec, local=None,
                                            src_qname=mod_qname,
                                            meta={"resolve": "relative" if relative else "bare"}))

    # ------------------------------------------------------------------
    def _stmt(self, node, scope, pf: ParsedFile, src: str):
        t = node.type
        if t == "class_declaration":
            self._class(node, scope, pf, src)
        elif t == "interface_declaration":
            self._decl(node, scope, pf, src, "INTERFACE")
        elif t == "enum_declaration":
            self._decl(node, scope, pf, src, "ENUM")
        elif t == "type_alias_declaration":
            self._decl(node, scope, pf, src, "TYPE")
        elif t == "function_declaration":
            self._function(node, scope, pf, src)
        elif t == "lexical_declaration" or t == "variable_declaration":
            self._variables(node, scope, pf, src)
            self._scan_calls(node, scope, pf, src)
        elif t == "expression_statement":
            self._scan_calls(node, scope, pf, src)
        elif t in ("export_statement",):
            for c in node.named_children:
                if c.type != "string":
                    self._stmt(c, scope, pf, src)

    def _decl(self, node, scope, pf: ParsedFile, src: str, kind: str):
        name_node = None
        for c in node.named_children:
            if c.type in ("type_identifier", "identifier"):
                name_node = c
                break
        name = self.text(name_node, src) if name_node else f"{kind.lower()}@{self.line(node)}"
        qname = f"{scope['qname']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                             language=pf.language, path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["qname"]),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))

    def _class(self, node, scope, pf: ParsedFile, src: str):
        name_node = None
        for c in node.named_children:
            if c.type == "type_identifier":
                name_node = c
                break
        name = self.text(name_node, src) if name_node else f"class@{self.line(node)}"
        qname = f"{scope['qname']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="CLASS", name=name, qname=qname,
                             language=pf.language, path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["qname"]),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))
        for ec in self.children_by_type(node, "extends_clause"):
            for c in ec.named_children:
                if c.type == "identifier":
                    pf.edges.append(EdgeSpec(kind="INHERITS",
                                             src=Ref(REF_QNAME, qname),
                                             dst=Ref(REF_NAME, self.text(c, src)),
                                             confidence=0.9, line=self.line(ec)))
        for ic in self.children_by_type(node, "implements_clause"):
            for c in ic.named_children:
                if c.type in ("identifier", "type_identifier"):
                    pf.edges.append(EdgeSpec(kind="IMPLEMENTS",
                                             src=Ref(REF_QNAME, qname),
                                             dst=Ref(REF_NAME, self.text(c, src)),
                                             confidence=0.9, line=self.line(ic)))
        cls_scope = {"qname": qname, "mod_qname": scope["mod_qname"],
                     "class_qname": qname, "vars": {},
                     "imports": scope["imports"]}
        for c in node.named_children:
            if c.type == "class_body":
                for m in c.named_children:
                    if m.type == "method_definition":
                        self._method(m, cls_scope, pf, src)

    def _method(self, node, scope, pf: ParsedFile, src: str):
        name_node = None
        for c in node.named_children:
            if c.type in ("property_identifier", "identifier"):
                name_node = c
                break
        name = self.text(name_node, src) if name_node else "method"
        qname = f"{scope['qname']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="METHOD", name=name, qname=qname,
                             language=pf.language, path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["qname"]),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))
        fn_scope = {"qname": qname, "mod_qname": scope["mod_qname"],
                    "class_qname": scope.get("class_qname"), "vars": {},
                    "imports": scope["imports"]}
        self._scan_calls(node, fn_scope, pf, src)

    def _function(self, node, scope, pf: ParsedFile, src: str):
        name_node = None
        for c in node.named_children:
            if c.type == "identifier":
                name_node = c
                break
        name = self.text(name_node, src) if name_node else "fn"
        qname = f"{scope['qname']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="FUNCTION", name=name, qname=qname,
                             language=pf.language, path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["qname"]),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))
        fn_scope = {"qname": qname, "mod_qname": scope["mod_qname"],
                    "class_qname": None, "vars": {}, "imports": scope["imports"]}
        self._scan_calls(node, fn_scope, pf, src)

    def _variables(self, node, scope, pf: ParsedFile, src: str):
        for vd in self.children_by_type(node, "variable_declarator"):
            ids = self.children_by_type(vd, "identifier")
            if not ids:
                continue
            name = self.text(ids[0], src)
            qname = f"{scope['qname']}.{name}"
            pf.nodes.append(Node(kind="VARIABLE", name=name, qname=qname,
                                 language=pf.language, path=pf.path,
                                 start_line=self.line(vd),
                                 start_col=self.col(vd),
                                 end_line=self.line(vd),
                                 end_col=self.col(vd) + len(name)))
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, scope["qname"]),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(vd)))
            # var typing: `const svc = new Service()` -> svc.Service
            value = None
            for c in vd.named_children:
                if c.type == "new_expression":
                    value = c
            if value is not None:
                for c in value.named_children:
                    if c.type == "identifier":
                        cands = self._name_candidates(self.text(c, src), scope)
                        if cands:
                            scope["vars"][name] = cands[0]

    # ------------------------------------------------------------------
    def _scan_calls(self, node, scope, pf: ParsedFile, src: str):
        def walk(n):
            for c in n.named_children:
                if c.type == "call_expression":
                    self._handle_call(c, scope, pf, src)
                    walk(c)
                elif c.type == "new_expression":
                    self._handle_new(c, scope, pf, src)
                    walk(c)
                elif c.type in ("function_declaration", "method_definition",
                                "class_declaration", "arrow_function",
                                "function_expression"):
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
            cands = self._name_candidates(name, scope)
            pf.callsites.append(Callsite(
                path=pf.path, line=line, col=col, callee=name,
                candidates=cands, ckind="call"))
        elif fn.type in ("property_access_expression", "member_expression"):
            named = fn.named_children
            obj = named[0] if named else None
            prop = named[-1] if named else None
            prop_name = self.text(prop, src) if prop else ""
            if not obj or not prop_name:
                return
            cands: list[str] = []
            if obj.type == "this" and scope.get("class_qname"):
                cands.append(f"{scope['class_qname']}.{prop_name}")
            elif obj.type == "identifier":
                oname = self.text(obj, src)
                if oname in scope.get("vars", {}):
                    cands.append(f"{scope['vars'][oname]}.{prop_name}")
                for imp in scope.get("imports", []):
                    if imp["local"] == oname and imp["original"] == "*":
                        cands.append(f"{imp['spec']}.{prop_name}")
            elif obj.type == "new_expression":
                for c in obj.named_children:
                    if c.type == "identifier":
                        cands.append(f"{scope['mod_qname']}.{self.text(c, src)}.{prop_name}")
            pf.callsites.append(Callsite(
                path=pf.path, line=line, col=col, callee=prop_name,
                candidates=cands, ckind="attribute"))
        else:
            pf.callsites.append(Callsite(path=pf.path, line=line, col=col,
                                         callee=self.text(fn, src)[:60],
                                         candidates=[], ckind="call"))

    def _handle_new(self, node, scope, pf: ParsedFile, src: str):
        for c in node.named_children:
            if c.type == "identifier":
                name = self.text(c, src)
                cands = self._name_candidates(name, scope)
                pf.callsites.append(Callsite(
                    path=pf.path, line=self.line(node), col=self.col(node),
                    callee=name,
                    candidates=[f"{q}.constructor" for q in cands] + cands,
                    ckind="construct"))

    def _name_candidates(self, name: str, scope) -> list[str]:
        cands: list[str] = []
        for imp in scope.get("imports", []):
            if imp["local"] == name:
                if imp["original"] == "*":
                    cands.append(f"{imp['spec']}.{name}")
                elif imp["original"] == "default":
                    cands.append(f"{imp['spec']}.default")
                else:
                    cands.append(f"{imp['spec']}.{imp['original']}")
        cands.append(f"{scope['qname']}.{name}")
        cands.append(f"{scope['mod_qname']}.{name}")
        return cands


class JavaScriptAdapter(TypeScriptAdapter):
    language = "javascript"
    extensions = (".js", ".jsx", ".mjs", ".cjs")
    js_mode = True
