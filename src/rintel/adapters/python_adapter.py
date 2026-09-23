"""Python adapter (tree-sitter-python).  Native nodes: MODULE / CLASS /
FUNCTION / METHOD / VARIABLE / FIELD.  Edges: CONTAINS, INHERITS, IMPORTS,
REFERENCES, CALLS.  The adapter *proposes* candidate qnames (same-module,
import bindings, self./local-var typing); the indexer confirms them against
canonical identities — nothing is fabricated (spec §32)."""
from __future__ import annotations

from ..identity import py_module_qname
from ..model import (Callsite, EdgeSpec, ImportBinding, Node, ParsedFile, Ref,
                     REF_NAME, REF_QNAME)
from .base import LanguageAdapter


class PythonAdapter(LanguageAdapter):
    language = "python"
    extensions = (".py",)

    def extract(self, relpath: str, src: str, root) -> ParsedFile:
        pf = ParsedFile(language="python", path=relpath)
        mod_qname = py_module_qname(relpath)
        s = self.range(root)
        pf.nodes.append(Node(kind="MODULE", name=mod_qname, qname=mod_qname,
                             language="python", path=relpath,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        # phase 1: import bindings anywhere in the file (imports may follow use)
        imports: list[ImportBinding] = []
        self._collect_imports(root, pf, imports, mod_qname, src)
        scope = {"qname": mod_qname, "mod_qname": mod_qname,
                 "class_qname": None, "vars": {}, "imports": imports}
        # phase 2: definitions + bodies
        for stmt in root.named_children:
            self._stmt(stmt, scope, pf, src)
        return pf

    # ------------------------------------------------------------------
    def _collect_imports(self, node, pf: ParsedFile, imports, mod_qname, src: str):
        for c in node.named_children:
            if c.type == "import_statement":
                self._import(c, pf, imports, mod_qname, src)
            elif c.type == "import_from_statement":
                self._import_from(c, pf, imports, mod_qname, src)
            elif c.type in ("function_definition", "class_definition",
                            "lambda"):
                continue  # imports inside functions are rare; skip
            else:
                self._collect_imports(c, pf, imports, mod_qname, src)

    def _import(self, node, pf: ParsedFile, imports, mod_qname, src: str):
        dotted = self.child_by_type(node, "dotted_name")
        module = self.text(dotted, src).strip() if dotted else ""
        line = self.line(node)
        if not module:
            return
        local = module.split(".")[0]
        for alias in self.children_by_type(node, "aliased_import"):
            ids = [c for c in alias.named_children if c.type == "identifier"]
            if len(ids) >= 2:
                local = self.text(ids[1], src)
        imports.append(ImportBinding(path=pf.path, line=line,
                                     module_qname=module, local=local,
                                     src_qname=mod_qname))
        pf.edges.append(EdgeSpec(kind="IMPORTS",
                                 src=Ref(REF_QNAME, mod_qname),
                                 dst=Ref(REF_QNAME, module),
                                 confidence=1.0, line=line))

    def _import_from(self, node, pf: ParsedFile, imports, mod_qname, src: str):
        dotteds = self.children_by_type(node, "dotted_name")
        if not dotteds:
            return
        module = self.text(dotteds[0], src).strip()
        line = self.line(node)
        for name_node in dotteds[1:]:
            name = self.text(name_node, src).strip()
            local = name
            for alias in self.children_by_type(name_node, "aliased_import"):
                ids = [c for c in alias.named_children if c.type == "identifier"]
                if len(ids) >= 2:
                    name = self.text(ids[0], src)
                    local = self.text(ids[1], src)
            imports.append(ImportBinding(path=pf.path, line=line,
                                         module_qname=module, local=local,
                                         only_names=[name],
                                         src_qname=mod_qname))
            pf.edges.append(EdgeSpec(
                kind="REFERENCES",
                src=Ref(REF_QNAME, mod_qname),
                dst=Ref(REF_QNAME, f"{module}.{name}"),
                confidence=0.9, line=line))
        pf.edges.append(EdgeSpec(kind="IMPORTS",
                                 src=Ref(REF_QNAME, mod_qname),
                                 dst=Ref(REF_QNAME, module),
                                 confidence=1.0, line=line))

    # ------------------------------------------------------------------
    def _stmt(self, node, scope, pf: ParsedFile, src: str):
        t = node.type
        if t == "decorated_definition":
            for c in node.named_children:
                self._stmt(c, scope, pf, src)
        elif t == "class_definition":
            self._class(node, scope, pf, src)
        elif t == "function_definition":
            self._function(node, scope, pf, src, is_method=False)
        elif t == "expression_statement":
            self._maybe_variable(node, scope, pf, src, field=False)
            self._scan_calls(node, scope, pf, src)
        elif t in ("if_statement", "for_statement", "while_statement",
                   "with_statement", "try_statement", "match_statement"):
            for c in node.named_children:
                self._stmt(c, scope, pf, src)

    def _class(self, node, scope, pf: ParsedFile, src: str):
        name_node = self.child_by_type(node, "identifier")
        name = self.text(name_node, src) if name_node else f"class@{self.line(node)}"
        qname = f"{scope['qname']}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="CLASS", name=name, qname=qname,
                             language="python", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["qname"]),
                                 dst=Ref(REF_QNAME, qname), confidence=1.0,
                                 line=self.line(node)))
        bases: list = []
        for c in node.named_children:
            if c is name_node or c.type == "block":
                continue
            bases.extend(self._base_leaves(c, src))
        for b in bases:
            ref = (Ref(REF_QNAME, self.text(b, src))
                   if b.type == "attribute" else Ref(REF_NAME, self.text(b, src)))
            pf.edges.append(EdgeSpec(kind="INHERITS",
                                     src=Ref(REF_QNAME, qname),
                                     dst=ref, confidence=0.9,
                                     line=self.line(b)))
        cls_scope = {"qname": qname, "mod_qname": scope["mod_qname"],
                     "class_qname": qname, "vars": {},
                     "imports": scope["imports"]}
        block = self.child_by_type(node, "block")
        if block:
            for stmt in block.named_children:
                self._class_stmt(stmt, cls_scope, pf, src)

    def _base_leaves(self, node, src: str) -> list:
        out = []
        def walk(n):
            for c in n.named_children:
                if c.type in ("identifier", "attribute"):
                    out.append(c)
                else:
                    walk(c)
        walk(node)
        return out

    def _class_stmt(self, node, scope, pf: ParsedFile, src: str):
        if node.type == "function_definition":
            self._function(node, scope, pf, src, is_method=True)
        elif node.type == "expression_statement":
            self._maybe_variable(node, scope, pf, src, field=True)

    def _function(self, node, scope, pf: ParsedFile, src: str,
                  is_method: bool):
        name_node = self.child_by_type(node, "identifier")
        name = self.text(name_node, src) if name_node else f"fn@{self.line(node)}"
        qname = f"{scope['qname']}.{name}"
        kind = "METHOD" if is_method else "FUNCTION"
        s = self.range(node)
        pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                             language="python", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, scope["qname"]),
                                 dst=Ref(REF_QNAME, qname), confidence=1.0,
                                 line=self.line(node)))
        fn_scope = {"qname": qname, "mod_qname": scope["mod_qname"],
                    "class_qname": scope.get("class_qname"), "vars": {},
                    "imports": scope["imports"]}
        self._assignments(node, fn_scope, pf, src)
        self._scan_calls(node, fn_scope, pf, src)

    def _maybe_variable(self, node, scope, pf: ParsedFile, src: str,
                        field: bool):
        for a in self.children_by_type(node, "assignment"):
            left = self.child_by_type(a, "identifier")
            if left is None:
                continue
            name = self.text(left, src)
            qname = f"{scope['qname']}.{name}"
            kind = "FIELD" if field else "VARIABLE"
            pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                                 language="python", path=pf.path,
                                 start_line=self.line(left),
                                 start_col=self.col(left),
                                 end_line=self.line(left),
                                 end_col=self.col(left) + len(name)))
            pf.edges.append(EdgeSpec(kind="CONTAINS",
                                     src=Ref(REF_QNAME, scope["qname"]),
                                     dst=Ref(REF_QNAME, qname),
                                     confidence=1.0, line=self.line(left)))

    # ------------------------------------------------------------------
    def _assignments(self, fn_node, scope, pf: ParsedFile, src: str):
        """Track `x = PaymentService()` so `x.method()` can be proposed."""
        def walk(n):
            for c in n.named_children:
                if c.type == "assignment":
                    left = self.child_by_type(c, "identifier")
                    right = c.named_children[-1] if c.named_children else None
                    if left and right:
                        tq = self._type_qname(right, scope, src)
                        if tq:
                            scope["vars"][self.text(left, src)] = tq
                elif c.type in ("function_definition", "class_definition",
                                "lambda"):
                    continue
                else:
                    walk(c)
        walk(fn_node)

    def _type_qname(self, node, scope, src) -> str | None:
        if node.type == "call":
            fn = node.named_children[0] if node.named_children else None
            if fn and fn.type == "identifier":
                name = self.text(fn, src)
                for c in self._name_candidates(name, scope):
                    return c
        elif node.type == "identifier":
            for c in self._name_candidates(self.text(node, src), scope):
                return c
        return None

    def _name_candidates(self, name: str, scope) -> list[str]:
        cands: list[str] = []
        for imp in scope.get("imports", []):
            if imp.local == name:
                if imp.only_names:
                    cands.append(f"{imp.module_qname}.{imp.only_names[0]}")
                else:
                    cands.append(f"{imp.module_qname}.{name}")
        cands.append(f"{scope['qname']}.{name}")
        cands.append(f"{scope['mod_qname']}.{name}")
        return cands

    def _scan_calls(self, node, scope, pf: ParsedFile, src: str):
        def walk(n):
            for c in n.named_children:
                if c.type == "call":
                    self._handle_call(c, scope, pf, src)
                    walk(c)  # nested calls in arguments
                elif c.type in ("function_definition", "class_definition",
                                "lambda"):
                    continue  # nested scopes handled by their own pass
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
                candidates=self._name_candidates(name, scope), ckind="call"))
        elif fn.type == "attribute":
            named = fn.named_children
            obj = named[0] if named else None
            attr = named[-1] if named else None
            attr_name = self.text(attr, src) if attr else ""
            if not obj or not attr_name:
                return
            cands: list[str] = []
            if obj.type == "identifier":
                oname = self.text(obj, src)
                if oname == "self" and scope.get("class_qname"):
                    cands.append(f"{scope['class_qname']}.{attr_name}")
                elif oname in scope.get("vars", {}):
                    cands.append(f"{scope['vars'][oname]}.{attr_name}")
                else:
                    for imp in scope.get("imports", []):
                        if imp.local == oname and not imp.only_names:
                            cands.append(f"{imp.module_qname}.{attr_name}")
            elif obj.type == "attribute":
                cands.append(f"{self.text(obj, src)}.{attr_name}")
            pf.callsites.append(Callsite(
                path=pf.path, line=line, col=col, callee=attr_name,
                candidates=cands, ckind="attribute"))
        else:
            pf.callsites.append(Callsite(path=pf.path, line=line, col=col,
                                         callee=self.text(fn, src)[:80],
                                         candidates=[], ckind="call"))
