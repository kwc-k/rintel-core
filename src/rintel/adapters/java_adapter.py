"""Java adapter (tree-sitter-java).  Nodes: PACKAGE / CLASS / INTERFACE /
ENUM / METHOD / FIELD / VARIABLE.  Edges: CONTAINS, IMPORTS, INHERITS,
IMPLEMENTS, CALLS, REFERENCES (constructor use)."""
from __future__ import annotations

from ..model import (Callsite, EdgeSpec, ImportBinding, Node, ParsedFile, Ref,
                     REF_NAME, REF_QNAME)
from .base import LanguageAdapter


class JavaAdapter(LanguageAdapter):
    language = "java"
    extensions = (".java",)

    def extract(self, relpath: str, src: str, root) -> ParsedFile:
        pf = ParsedFile(language="java", path=relpath)
        pkg = ""
        for c in root.named_children:
            if c.type == "package_declaration":
                pkg = ".".join(self._all_identifiers(c, src))
        imports: list[dict] = []
        for c in root.named_children:
            if c.type == "import_declaration":
                self._import(c, imports, pf, src)
        s = self.range(root)
        if pkg:
            pf.nodes.append(Node(kind="PACKAGE", name=pkg, qname=pkg,
                                 language="java", path=relpath,
                                 start_line=s[0], start_col=s[1],
                                 end_line=s[2], end_col=s[3]))
        scope = {"pkg": pkg, "imports": imports, "vars": {}}
        for c in root.named_children:
            if c.type == "class_declaration":
                self._class(c, scope, pf, src, "CLASS")
            elif c.type == "interface_declaration":
                self._class(c, scope, pf, src, "INTERFACE")
            elif c.type == "enum_declaration":
                self._class(c, scope, pf, src, "ENUM")
        # call scanning after defs are registered
        for c in root.named_children:
            if c.type == "class_declaration":
                self._scan_class_calls(c, scope, pf, src)
        return pf

    @staticmethod
    def _all_identifiers(node, src: str) -> list[str]:
        out: list[str] = []
        def walk(n):
            for c in n.named_children:
                if c.type == "identifier":
                    out.append(c)
                else:
                    walk(c)
        walk(node)
        return [JavaAdapter.text(c, src) for c in out]

    # ------------------------------------------------------------------
    def _import(self, node, imports, pf: ParsedFile, src: str):
        text = self.text(node, src)
        if text.startswith("import static"):
            return
        ids = self._all_identifiers(node, src)
        if not ids:
            return
        parts = list(ids)
        if parts[-1] == "*":
            module = ".".join(parts[:-1])
            imports.append({"local": None, "module": module})
            pf.imports.append(ImportBinding(path=pf.path, line=self.line(node),
                                            module_qname=module, local=None,
                                            src_qname=pkg))
        else:
            module = ".".join(parts[:-1])
            local = parts[-1]
            imports.append({"local": local, "module": module})
            pf.imports.append(ImportBinding(path=pf.path, line=self.line(node),
                                            module_qname=module, local=local,
                                            only_names=[local],
                                            src_qname=pkg))

    def _class(self, node, scope, pf: ParsedFile, src: str, kind: str):
        ids = self.children_by_type(node, "identifier")
        if not ids:
            return
        name = self.text(ids[0], src)
        qname = f"{scope['pkg']}.{name}" if scope["pkg"] else name
        s = self.range(node)
        pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                             language="java", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["pkg"] or "FILE"),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))
        for sc in self.children_by_type(node, "superclass"):
            for tid in self.children_by_type(sc, "type_identifier"):
                pf.edges.append(EdgeSpec(kind="INHERITS",
                                         src=Ref(REF_QNAME, qname),
                                         dst=Ref(REF_NAME, self.text(tid, src)),
                                         confidence=0.9, line=self.line(sc)))
        for si in self.children_by_type(node, "super_interfaces"):
            for tid in self.children_by_type(si, "type_identifier"):
                pf.edges.append(EdgeSpec(kind="IMPLEMENTS",
                                         src=Ref(REF_QNAME, qname),
                                         dst=Ref(REF_NAME, self.text(tid, src)),
                                         confidence=0.9, line=self.line(si)))
        cls_scope = {"pkg": scope["pkg"], "imports": scope["imports"],
                     "vars": {}, "class_qname": qname}
        body = self.child_by_type(node, "class_body")
        if body:
            for c in body.named_children:
                if c.type == "method_declaration":
                    self._method(c, cls_scope, pf, src)
                elif c.type == "constructor_declaration":
                    self._constructor(c, cls_scope, pf, src)
                elif c.type == "field_declaration":
                    self._fields(c, cls_scope, pf, src)
                elif c.type == "class_declaration":
                    self._class(c, {"pkg": scope["pkg"], "imports": scope["imports"],
                                    "vars": {}}, pf, src, "CLASS")

    def _method(self, node, scope, pf: ParsedFile, src: str):
        ids = self.children_by_type(node, "identifier")
        if not ids:
            return
        name = self.text(ids[0], src)
        qname = f"{scope['class_qname']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="METHOD", name=name, qname=qname,
                             language="java", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["class_qname"]),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))

    def _constructor(self, node, scope, pf: ParsedFile, src: str):
        name = "<init>"
        qname = f"{scope['class_qname']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="METHOD", name=name, qname=qname,
                             language="java", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["class_qname"]),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))

    def _fields(self, node, scope, pf: ParsedFile, src: str):
        for vd in self.children_by_type(node, "variable_declarator"):
            ids = self.children_by_type(vd, "identifier")
            if not ids:
                continue
            name = self.text(ids[0], src)
            qname = f"{scope['class_qname']}.{name}"
            pf.nodes.append(Node(kind="FIELD", name=name, qname=qname,
                                 language="java", path=pf.path,
                                 start_line=self.line(vd),
                                 start_col=self.col(vd),
                                 end_line=self.line(vd),
                                 end_col=self.col(vd) + len(name)))
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, scope["class_qname"]),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(vd)))

    # ------------------------------------------------------------------
    def _scan_class_calls(self, class_node, scope, pf: ParsedFile, src: str):
        body = self.child_by_type(class_node, "class_body")
        if body is None:
            return
        cls_qname = None
        ids = self.children_by_type(class_node, "identifier")
        if ids:
            cls_qname = f"{scope['pkg']}.{self.text(ids[0], src)}" \
                if scope["pkg"] else self.text(ids[0], src)
        for c in body.named_children:
            if c.type in ("method_declaration", "constructor_declaration"):
                fn_scope = {"pkg": scope["pkg"], "imports": scope["imports"],
                            "vars": {}, "class_qname": cls_qname}
                self._collect_vars(c, fn_scope, pf, src)
                self._scan_calls(c, fn_scope, pf, src)

    def _collect_vars(self, node, scope, pf: ParsedFile, src: str):
        """`Gateway g = new Gateway()` -> vars[g] = pkg.Gateway"""
        for vd in self.children_by_type(node, "variable_declarator"):
            ids = self.children_by_type(vd, "identifier")
            if not ids:
                continue
            vname = self.text(ids[0], src)
            for init in self.children_by_type(vd, "variable_initializer"):
                for oce in self.children_by_type(init, "object_creation_expression"):
                    for tid in self.children_by_type(oce, "type_identifier"):
                        tname = self.text(tid, src)
                        scope["vars"][vname] = \
                            f"{scope['pkg']}.{tname}" if scope["pkg"] else tname

    def _scan_calls(self, node, scope, pf: ParsedFile, src: str):
        def walk(n):
            for c in n.named_children:
                if c.type == "method_invocation":
                    self._handle_invocation(c, scope, pf, src)
                    walk(c)
                elif c.type == "object_creation_expression":
                    self._handle_creation(c, scope, pf, src)
                    walk(c)
                elif c.type in ("method_declaration", "constructor_declaration",
                                "class_declaration", "lambda_expression"):
                    continue
                else:
                    walk(c)
        walk(node)

    def _handle_invocation(self, node, scope, pf: ParsedFile, src: str):
        named = node.named_children
        arg_list = named[-1] if named and named[-1].type == "argument_list" else None
        rest = named[:-1] if arg_list else named
        if not rest or rest[-1].type != "identifier":
            return
        method = self.text(rest[-1], src)
        receiver = rest[:-1]
        cands: list[str] = []
        if receiver and receiver[0].type == "object_creation_expression":
            for tid in self.children_by_type(receiver[0], "type_identifier"):
                tname = self.text(tid, src)
                cands.append(f"{scope['pkg']}.{tname}.{method}"
                             if scope["pkg"] else f"{tname}.{method}")
        elif receiver and receiver[0].type == "identifier":
            obj_name = self.text(receiver[0], src)
            if obj_name == "this":
                cands.append(f"{scope['class_qname']}.{method}")
            elif obj_name in scope.get("vars", {}):
                cands.append(f"{scope['vars'][obj_name]}.{method}")
        else:
            cands.append(f"{scope['class_qname']}.{method}")
        pf.callsites.append(Callsite(
            path=pf.path, line=self.line(node), col=self.col(node),
            callee=method, candidates=cands, ckind="call"))

    def _handle_creation(self, node, scope, pf: ParsedFile, src: str):
        for tid in self.children_by_type(node, "type_identifier"):
            tname = self.text(tid, src)
            q = f"{scope['pkg']}.{tname}" if scope["pkg"] else tname
            pf.callsites.append(Callsite(
                path=pf.path, line=self.line(node), col=self.col(node),
                callee=tname, candidates=[f"{q}.<init>", q],
                ckind="construct"))
