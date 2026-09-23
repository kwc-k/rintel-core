"""C adapter (tree-sitter-c) + C++ adapter (tree-sitter-cpp).

C identity rule: non-static symbols are global by name — a header
declaration and its .c definition merge into one canonical node
(cross-file identity).  Static symbols are file-scoped: `relpath::name`.

Edges: INCLUDES (#include), CONTAINS (file/type -> members), CALLS,
INHERITS (C++), IMPLEMENTS (C++).
"""
from __future__ import annotations

from ..model import (Callsite, EdgeSpec, IncludeBinding, Node, ParsedFile, Ref,
                     REF_QNAME)
from .base import LanguageAdapter

DECL_NAME_TYPES = ("identifier", "field_identifier", "type_identifier")


class CAdapter(LanguageAdapter):
    language = "c"
    extensions = (".c", ".h")

    def extract(self, relpath: str, src: str, root) -> ParsedFile:
        pf = ParsedFile(language=self.language, path=relpath)
        self._walk(root, None, pf, src, top=True)
        return pf

    def _walk(self, node, scope_qname: str | None, pf: ParsedFile, src: str,
              top: bool = False):
        for c in node.named_children:
            t = c.type
            if t == "preproc_include":
                self._include(c, pf, src)
            elif t == "function_definition":
                self._function(c, scope_qname, pf, src, defined=True)
            elif t == "declaration":
                self._declaration(c, scope_qname, pf, src)
            elif t == "type_definition":
                self._typedef(c, scope_qname, pf, src)
            elif t == "struct_specifier":
                self._struct(c, scope_qname, pf, src, "TYPE", "struct")
            elif t == "union_specifier":
                self._struct(c, scope_qname, pf, src, "TYPE", "union")
            elif t == "enum_specifier":
                self._struct(c, scope_qname, pf, src, "ENUM", "enum")
            elif t == "comment":
                continue
            elif t == "preproc_def":
                continue
            elif t in ("preproc_ifdef", "preproc_if", "linkage_specification"):
                self._walk(c, scope_qname, pf, src, top=top)

    def _include(self, node, pf: ParsedFile, src: str):
        target = ""
        angle = False
        for c in node.named_children:
            if c.type in ("string_literal", "system_lib_string"):
                target = self.text(c, src).strip('"<>')
                angle = c.type == "system_lib_string"
        if target:
            pf.includes.append(IncludeBinding(path=pf.path, line=self.line(node),
                                              target=target,
                                              meta={"angle": angle}))

    def _declaration(self, node, scope_qname, pf: ParsedFile, src: str):
        # constructor call: `Type var(args)` (C++)
        tid = self.child_by_type(node, "type_identifier")
        if tid is not None:
            for init in self.children_by_type(node, "init_declarator"):
                if self.child_by_type(init, "argument_list") is not None:
                    tname = self.text(tid, src)
                    pf.callsites.append(Callsite(
                        path=pf.path, line=self.line(node), col=self.col(node),
                        callee=tname, candidates=[tname], ckind="construct"))
        fd = None
        for c in node.named_children:
            if c.type == "function_declarator":
                fd = c
        if fd is not None:
            name = self._declarator_name(fd, src)
            if name:
                qname = name if not self._is_static(node, src) \
                    else f"{pf.path}::{name}"
                s = self.range(node)
                pf.nodes.append(Node(kind="FUNCTION", name=name, qname=qname,
                                     language=self.language, path=pf.path,
                                     start_line=s[0], start_col=s[1],
                                     end_line=s[2], end_col=s[3],
                                     meta={"defined": False}))
            return
        # plain struct/union/enum declarations
        for c in node.named_children:
            if c.type in ("struct_specifier", "union_specifier",
                          "enum_specifier"):
                self._struct(c, scope_qname, pf, src, "TYPE",
                             c.type.replace("_specifier", ""))

    def _typedef(self, node, scope_qname, pf: ParsedFile, src: str):
        tid = self.child_by_type(node, "type_identifier")
        if tid is None:
            return
        name = self.text(tid, src)
        s = self.range(node)
        pf.nodes.append(Node(kind="TYPE", name=name, qname=name,
                             language=self.language, path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3],
                             meta={"defined": True, "typedef": True}))
        for c in node.named_children:
            if c.type in ("struct_specifier", "union_specifier",
                          "enum_specifier", "primitive_type", "type_identifier",
                          "sized_type_specifier"):
                continue
            if c.type == "struct_specifier":
                self._struct(c, scope_qname, pf, src, "TYPE", "struct")

    def _struct(self, node, scope_qname, pf: ParsedFile, src: str,
                kind: str, meta_kind: str):
        tid = self.child_by_type(node, "type_identifier")
        name = self.text(tid, src) if tid else f"{meta_kind}@{self.line(node)}"
        qname = name
        s = self.range(node)
        pf.nodes.append(Node(kind=kind, name=name, qname=qname,
                             language=self.language, path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3],
                             meta={"struct_kind": meta_kind}))
        fdl = self.child_by_type(node, "field_declaration_list")
        if fdl:
            for fd in self.children_by_type(fdl, "field_declaration"):
                for ident in fd.named_children:
                    if ident.type in ("identifier", "field_identifier"):
                        fname = self.text(ident, src)
                        fq = f"{qname}.{fname}"
                        pf.nodes.append(Node(kind="FIELD", name=fname,
                                             qname=fq, language=self.language,
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

    def _function(self, node, scope_qname, pf: ParsedFile, src: str,
                  defined: bool):
        fd = self.child_by_type(node, "function_declarator")
        if fd is None:
            return
        name = self._declarator_name(fd, src)
        if not name:
            return
        static = self._is_static(node, src)
        qname = name if not static else f"{pf.path}::{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="FUNCTION", name=name, qname=qname,
                             language=self.language, path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3],
                             meta={"defined": defined, "static": static}))
        if defined:
            self._scan_calls(node, pf, src)

    def _is_static(self, node, src: str) -> bool:
        for c in node.named_children:
            if c.type == "storage_class_specifier":
                return "static" in self.text(c, src)
        return False

    def _declarator_name(self, fd, src: str) -> str | None:
        for c in fd.named_children:
            if c.type in DECL_NAME_TYPES:
                return self.text(c, src)
            if c.type == "qualified_identifier":
                parts = [x for x in c.named_children
                         if x.type in ("namespace_identifier", "identifier",
                                       "field_identifier")]
                if parts:
                    return self.text(parts[-1], src)
        # nested (e.g. pointer_declarator wrapping)
        for c in fd.named_children:
            if c.type in ("pointer_declarator", "init_declarator",
                          "attributed_declarator", "function_declarator"):
                inner = self._declarator_name(c, src)
                if inner:
                    return inner
        return None

    def _scan_calls(self, node, pf: ParsedFile, src: str):
        def walk(n):
            for c in n.named_children:
                if c.type == "call_expression":
                    fn = c.named_children[0] if c.named_children else None
                    if fn is None:
                        continue
                    if fn.type == "identifier":
                        name = self.text(fn, src)
                        pf.callsites.append(Callsite(
                            path=pf.path, line=self.line(c), col=self.col(c),
                            callee=name, candidates=[name], ckind="call"))
                    elif fn.type == "type_identifier":
                        name = self.text(fn, src)
                        pf.callsites.append(Callsite(
                            path=pf.path, line=self.line(c), col=self.col(c),
                            callee=name, candidates=[name], ckind="construct"))
                    elif fn.type in ("field_expression", "pointer_expression"):
                        ids = [i for i in fn.named_children
                               if i.type == "field_identifier"]
                        if ids:
                            name = self.text(ids[0], src)
                            pf.callsites.append(Callsite(
                                path=pf.path, line=self.line(c), col=self.col(c),
                                callee=name, candidates=[name], ckind="call"))
                    walk(c)  # nested calls in arguments
                elif c.type == "function_definition":
                    continue
                elif c.type == "declaration":
                    # `Type var(args);` constructor invocation (C++)
                    tid = self.child_by_type(c, "type_identifier")
                    if tid is not None:
                        for init in self.children_by_type(c, "init_declarator"):
                            if self.child_by_type(init, "argument_list") is not None:
                                tname = self.text(tid, src)
                                pf.callsites.append(Callsite(
                                    path=pf.path, line=self.line(c),
                                    col=self.col(c), callee=tname,
                                    candidates=[tname], ckind="construct"))
                    walk(c)
                else:
                    walk(c)
        walk(node)


class CppAdapter(CAdapter):
    language = "cpp"
    extensions = (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx")

    def _walk(self, node, scope_qname, pf: ParsedFile, src: str,
              top: bool = False):
        for c in node.named_children:
            t = c.type
            if t == "preproc_include":
                self._include(c, pf, src)
            elif t == "function_definition":
                if scope_qname:
                    self._method(c, scope_qname, pf, src, defined=True)
                else:
                    self._function(c, None, pf, src, defined=True)
            elif t == "declaration":
                self._declaration(c, scope_qname, pf, src)
            elif t == "type_definition":
                self._typedef(c, scope_qname, pf, src)
            elif t == "class_specifier":
                self._class_spec(c, pf, src)
            elif t == "struct_specifier":
                self._struct(c, scope_qname, pf, src, "TYPE", "struct")
            elif t == "enum_specifier":
                self._struct(c, scope_qname, pf, src, "ENUM", "enum")
            elif t == "template_declaration":
                for ch in c.named_children:
                    if ch.type in ("class_specifier", "function_definition",
                                   "declaration", "type_definition",
                                   "preproc_ifdef", "preproc_if"):
                        self._walk(ch, scope_qname, pf, src, top=top)
            elif t == "namespace_definition":
                self._walk(c, scope_qname, pf, src, top=top)
            elif t in ("preproc_ifdef", "preproc_if"):
                self._walk(c, scope_qname, pf, src, top=top)

    def _class_spec(self, node, pf: ParsedFile, src: str):
        tid = self.child_by_type(node, "type_identifier")
        name = self.text(tid, src) if tid else f"class@{self.line(node)}"
        s = self.range(node)
        pf.nodes.append(Node(kind="CLASS", name=name, qname=name,
                             language="cpp", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3]))
        # base classes
        for bc in self.children_by_type(node, "base_class_clause"):
            for tid2 in self.children_by_type(bc, "type_identifier"):
                pf.edges.append(EdgeSpec(kind="INHERITS",
                                         src=Ref(REF_QNAME, name),
                                         dst=Ref(REF_QNAME, self.text(tid2, src)),
                                         confidence=0.9, line=self.line(bc)))
        # members: method decls (declaration / field_declaration with a
        # function_declarator) and plain fields
        body = self.child_by_type(node, "field_declaration_list")
        for c in (body.named_children if body is not None
                  else node.named_children):
            if c.type == "function_definition":
                self._method(c, name, pf, src, defined=True)
            elif c.type == "declaration":
                for fd in self.children_by_type(c, "function_declarator"):
                    self._method_decl(fd, name, pf, src)
            elif c.type == "field_declaration":
                fd = self.child_by_type(c, "function_declarator")
                if fd is not None:
                    self._method_decl(fd, name, pf, src)
                else:
                    for fid in self.children_by_type(c, "field_identifier"):
                        fname = self.text(fid, src)
                        fq = f"{name}.{fname}"
                        pf.nodes.append(Node(kind="FIELD", name=fname,
                                             qname=fq, language="cpp",
                                             path=pf.path,
                                             start_line=self.line(fid),
                                             start_col=self.col(fid),
                                             end_line=self.line(fid),
                                             end_col=self.col(fid) + len(fname)))
                        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                                 src=Ref(REF_QNAME, name),
                                                 dst=Ref(REF_QNAME, fq),
                                                 confidence=1.0,
                                                 line=self.line(fid)))

    def _method_decl(self, fd, class_qname: str, pf: ParsedFile, src: str):
        mname = self._declarator_name(fd, src)
        if not mname:
            return
        pf.nodes.append(Node(kind="METHOD", name=mname,
                             qname=f"{class_qname}.{mname}",
                             language="cpp", path=pf.path,
                             start_line=self.line(fd),
                             start_col=self.col(fd),
                             end_line=self.line(fd),
                             end_col=self.col(fd),
                             meta={"defined": False}))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, class_qname),
                                 dst=Ref(REF_QNAME, f"{class_qname}.{mname}"),
                                 confidence=1.0,
                                 line=self.line(fd)))

    def _function(self, node, scope_qname, pf: ParsedFile, src: str,
                  defined: bool = True):
        fd = self.child_by_type(node, "function_declarator")
        if fd is None:
            return
        qi = self.child_by_type(fd, "qualified_identifier")
        if qi is not None:
            parts = [c for c in qi.named_children
                     if c.type in ("namespace_identifier", "identifier",
                                   "field_identifier")]
            if len(parts) >= 2:
                cls, name = self.text(parts[0], src), self.text(parts[-1], src)
                qname = f"{cls}.{name}"
                s = self.range(node)
                pf.nodes.append(Node(kind="METHOD", name=name, qname=qname,
                                     language="cpp", path=pf.path,
                                     start_line=s[0], start_col=s[1],
                                     end_line=s[2], end_col=s[3],
                                     meta={"defined": defined}))
                pf.edges.append(EdgeSpec(kind="CONTAINS",
                                         src=Ref(REF_QNAME, cls),
                                         dst=Ref(REF_QNAME, qname),
                                         confidence=1.0, line=self.line(node)))
                if defined:
                    self._scan_calls(node, pf, src)
                return
        super()._function(node, scope_qname, pf, src, defined)

    def _method(self, node, class_qname: str, pf: ParsedFile, src: str,
                defined: bool):
        fd = self.child_by_type(node, "function_declarator")
        if fd is None:
            return
        name = self._declarator_name(fd, src)
        if not name:
            return
        qname = f"{class_qname}.{name}"
        s = self.range(node)
        pf.nodes.append(Node(kind="METHOD", name=name, qname=qname,
                             language="cpp", path=pf.path,
                             start_line=s[0], start_col=s[1],
                             end_line=s[2], end_col=s[3],
                             meta={"defined": defined}))
        pf.edges.append(EdgeSpec(kind="CONTAINS",
                                 src=Ref(REF_QNAME, class_qname),
                                 dst=Ref(REF_QNAME, qname),
                                 confidence=1.0, line=self.line(node)))
        if defined:
            self._scan_calls(node, pf, src)
