"""Deterministic local-binding dataflow — tree-sitter C (P0.4 fallback).

Why: Joern 4.0.617 `reachableByFlows` yields 0 flows on trivial C chains
(x = produce(); consume(x);) even with the ossdataflow overlay (verified
minimal repro, classification D per P0.4 matrix).  This module provides an
honest, STATEMENT-LEVEL deterministic fallback for intra-procedural C
data: producer return → assignment/binding → local identity → call
argument → consumer parameter.  Facts are OBSERVED at statement level with
coverage=PARTIAL (no pointer/alias analysis, intra-proc only) and MUST/MAY
from enclosing control scope (P0.1 semantics).
"""
from __future__ import annotations

import json
from typing import Any, Iterator, Optional

from .contract import (AnalysisFact, Coverage, ExecutionModality, FactKind,
                       SemanticKind, SourceLocation, TargetResolution,
                       TruthClass)
from .netlist_bridge import DataSegment

import tree_sitter
import tree_sitter_c


def _q(lang: str = "c"):
    return tree_sitter.Language(tree_sitter_c.language())


def _walk(node, out: list) -> None:
    out.append(node)
    for child in node.children:
        _walk(child, out)


def _text(node) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _field(parent, name, out):
    child = parent.child_by_field_name(name)
    return child if child is not None else out


def _children_by_type(node, typ):
    return [c for c in node.children if c.type == typ]


class _Ctx:
    """scope stack: (kind: 'if'|'loop'|'fn', guard text)"""

    def __init__(self):
        self.stack: list[tuple[str, Optional[str]]] = []

    def push(self, kind: str, guard: Optional[str]):
        self.stack.append((kind, guard))

    def pop(self):
        self.stack.pop()

    def modality(self) -> ExecutionModality:
        return ExecutionModality.MAY if self.stack else \
            ExecutionModality.MUST

    def guard(self) -> Optional[str]:
        for kind, guard in reversed(self.stack):
            if guard:
                return guard
        return None


def _identifier_name(node) -> Optional[str]:
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node)
    if node.type in ("field_expression", "pointer_expression",
                     "parenthesized_expression"):
        return _identifier_name(node.child_by_field_name("field")
                                or node.child_by_field_name("argument"))
    return None


class LocalCBinder:
    """Deterministic per-function C dataflow extractor (intra-proc)."""

    def __init__(self, provider="local_c_binding", version="0.1",
                 repo_id="", snapshot_id=None):
        self._provider = provider
        self._version = version
        self._repo_id = repo_id
        self._snapshot_id = snapshot_id

    def _mk(self, kind: SemanticKind, ctx: _Ctx, file: str,
            subject: str, data: str, node, *, inputs=None,
            outputs=None, truth=TruthClass.OBSERVED,
            res=TargetResolution.EXACT, cov=Coverage.PARTIAL,
            metadata=None) -> AnalysisFact:
        loc = SourceLocation(file=file,
                             line=node.start_point[0] + 1,
                             column=node.start_point[1] + 1)
        return AnalysisFact(
            fact_id=f"{self._provider}:{file}:{node.start_point[0]}"
                    f":{node.start_point[1]}:{kind.value}:{data}",
            fact_kind=FactKind.DATA_FLOW, semantic_kind=kind,
            provider=self._provider, provider_version=self._version,
            repo_id=self._repo_id, snapshot_id=self._snapshot_id,
            truth_class=truth,
            execution_modality=ctx.modality(),
            target_resolution=res, coverage=cov,
            subject=subject, inputs=inputs or [], outputs=outputs or [],
            scope=subject, guard=ctx.guard(), source_location=loc,
            metadata=dict(metadata or {}).update(
                {"deterministic_local_binding": True,
                 "data": data, "file": file}) or {
                "deterministic_local_binding": True,
                "data": data, "file": file, **(metadata or {})})

    def extract_function(self, fn_node, file: str) -> list[AnalysisFact]:
        facts: list[AnalysisFact] = []
        declarator = fn_node.child_by_field_name("declarator")
        fn_name: Optional[str] = declarator
        # walk declarator to find the identifier
        def _name_of(n):
            if n is None:
                return None
            if n.type == "function_declarator":
                return _name_of(n.child_by_field_name("declarator"))
            if n.type == "pointer_declarator":
                return _name_of(n.child_by_field_name("declarator"))
            return _identifier_name(n)
        fn_name = _name_of(declarator) or "?"
        body = fn_node.child_by_field_name("body")
        if body is None:
            return facts
        ctx = _Ctx()
        self._walk_stmt(body, ctx, file, fn_name, facts)
        return facts

    def _walk_stmt(self, node, ctx: _Ctx, file: str, fn: str,
                   facts: list) -> None:
        t = node.type
        if t == "if_statement":
            cond = node.child_by_field_name("condition")
            ctx.push("if", _text(cond) if cond else None)
            for b in _children_by_type(node, "compound_statement"):
                self._walk_stmt(b, ctx, file, fn, facts)
            ctx.pop()
        elif t in ("for_statement", "while_statement"):
            cond = node.child_by_field_name("condition")
            ctx.push("loop", _text(cond) if cond else None)
            for b in _children_by_type(node, "compound_statement"):
                self._walk_stmt(b, ctx, file, fn, facts)
            ctx.pop()
        elif t == "compound_statement":
            for c in node.children:
                self._walk_stmt(c, ctx, file, fn, facts)
        elif t == "declaration":
            for c in node.children:
                if c.type == "init_declarator":
                    d1 = c.child_by_field_name("declarator")
                    v1 = c.child_by_field_name("value")
                    if d1 is not None and v1 is not None:
                        self._binding(d1, v1, ctx, file, fn, facts)
                elif c.type in ("identifier", "array_declarator"):
                    continue  # plain declarations without initializer
        elif t == "expression_statement":
            expr = node.child_by_field_name("expression")
            if expr is None:
                for c in node.children:
                    if c.type == "call_expression":
                        expr = c
                        break
            if expr is not None:
                self._expr(expr, ctx, file, fn, facts)
            elif t == "expression_statement":
                for c in node.children:
                    if c.type == "assignment_expression":
                        self._expr(c, ctx, file, fn, facts)
        elif t == "return_statement":
            val = node.child_by_field_name("value")
            if val is None:
                for c in node.children:
                    if c.type not in ("return", ";", "return_statement"):
                        val = c
                        break
            if val is not None:
                self._return(val, ctx, file, fn, facts)
        else:
            for c in node.children:
                if c.type in ("if_statement", "for_statement",
                              "while_statement", "compound_statement"):
                    self._walk_stmt(c, ctx, file, fn, facts)

    def _binding(self, decl, init, ctx, file, fn, facts) -> None:
        name = _identifier_name(decl)
        if name is None:
            return
        # direct call:  x = f(args)
        if init.type == "call_expression":
            callee = _text(init.child_by_field_name("function"))
            args = init.child_by_field_name("arguments")
            arg_list = _text(args) if args else ""
            facts.append(self._mk(SemanticKind.READ, ctx, file, fn, name,
                                  init, outputs=[name],
                                  metadata={"binding": "call_result",
                                            "callee": callee,
                                            "args": arg_list}))
        elif init.type == "identifier":
            facts.append(self._mk(SemanticKind.READ, ctx, file, fn, name,
                                  init, inputs=[_text(init)],
                                  outputs=[name],
                                  metadata={"binding": "copy"}))
        elif init.type == "binary_expression":
            facts.append(self._mk(SemanticKind.COMPUTE, ctx, file, fn, name,
                                  init, inputs=[_text(init)],
                                  outputs=[name],
                                  metadata={"binding": "computed",
                                            "expr": _text(init)}))

    def _expr(self, expr, ctx, file, fn, facts) -> None:
        t = expr.type
        if t == "call_expression":
            callee = _text(expr.child_by_field_name("function"))
            args_node = expr.child_by_field_name("arguments")
            facts.append(self._mk(
                SemanticKind.CALL, ctx, file, fn, f"call:{callee}", expr,
                inputs=[self._arg_values(args_node)],
                metadata={"binding": "call", "callee": callee,
                          "deterministic_target": True}))
        elif t == "assignment_expression":
            left = expr.child_by_field_name("left")
            right = expr.child_by_field_name("right")
            lname = _identifier_name(left)
            if lname and right is not None:
                if right.type == "call_expression":
                    self._binding(left, right, ctx, file, fn, facts)
                elif right.type == "identifier":
                    facts.append(self._mk(
                        SemanticKind.READ, ctx, file, fn, lname, expr,
                        inputs=[_text(right)], outputs=[lname],
                        metadata={"binding": "assignment-copy"}))
                else:
                    facts.append(self._mk(
                        SemanticKind.COMPUTE, ctx, file, fn, lname, expr,
                        inputs=[_text(right)], outputs=[lname],
                        metadata={"binding": "assignment"}))

    def _return(self, val, ctx, file, fn, facts) -> None:
        facts.append(self._mk(SemanticKind.RETURN, ctx, file, fn,
                              f"{fn}.return", val, inputs=[_text(val)],
                              metadata={"binding": "return"}))

    def _arg_values(self, args_node) -> Optional[str]:
        if args_node is None:
            return None
        return _text(args_node)

    # public API -------------------------------------------------------------
    def facts_for_file(self, source: str, file: str) -> list[AnalysisFact]:
        parser = tree_sitter.Parser(_q())
        tree = parser.parse(source.encode("utf-8"))
        facts: list[AnalysisFact] = []
        all_nodes: list[Any] = []
        _walk(tree.root_node, all_nodes)
        for fn in all_nodes:
            if fn.type == "function_definition":
                facts.extend(self.extract_function(fn, file))
        return facts


def local_c_dataflow(source: str, file: str, repo_id="", snapshot=None,
                     provider="local_c_binding") -> list[AnalysisFact]:
    return LocalCBinder(provider=provider, repo_id=repo_id,
                        snapshot_id=snapshot).facts_for_file(source, file)


# ---------------------------------------------------------------------------
# Python variant (deterministic intra-proc local binding)
# ---------------------------------------------------------------------------
import tree_sitter_python  # noqa: E402  (grammar bundled in venv)

_PY_SKIP = {"self", "cls", "None", "True", "False"}


def _py_name(n):
    if n is None:
        return None
    if n.type == "identifier":
        return _text(n)
    if n.type in ("attribute", "call", "subscript", "arguments"):
        for f in ("object", "function"):
            c = n.child_by_field_name(f)
            if c is not None:
                r = _py_name(c)
                if r:
                    return r + "." + (_text(n.child_by_field_name("attribute"))
                                      if n.child_by_field_name("attribute")
                                      else "")
        return _text(n)
    return None


class LocalPythonBinder(LocalCBinder):
    """Same contract as the C binder; python grammar walk (intra-proc)."""

    def facts_for_file(self, source: str, file: str) -> list[AnalysisFact]:
        import tree_sitter
        parser = tree_sitter.Parser(tree_sitter.Language(
            tree_sitter_python.language()))
        tree = parser.parse(source.encode("utf-8"))
        facts: list[AnalysisFact] = []
        ctx = _Ctx()
        self._walk_py(tree.root_node, ctx, file, "<module>", facts)
        return facts

    def _walk_py(self, node, ctx, file, fn, facts) -> None:
        t = node.type
        if t == "function_definition":
            name = _text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            sub = _Ctx()
            if body is not None:
                self._walk_py(body, sub, file, name or fn, facts)
        elif t in ("class_definition",):
            for c in node.children:
                self._walk_py(c, ctx, file, fn, facts)
        elif t == "if_statement":
            cond = node.child_by_field_name("condition")
            ctx.push("if", _text(cond) if cond else None)
            for c in node.children:
                if c.type in ("block", "elif_clause", "else_clause"):
                    self._walk_py(c, ctx, file, fn, facts)
            ctx.pop()
        elif t in ("for_statement", "while_statement"):
            cond = node.child_by_field_name("condition") or \
                node.child_by_field_name("right")
            ctx.push("loop", _text(cond) if cond else None)
            for c in node.children:
                if c.type == "block":
                    self._walk_py(c, ctx, file, fn, facts)
            ctx.pop()
        elif t == "block":
            for c in node.children:
                self._walk_py(c, ctx, file, fn, facts)
        elif t == "expression_statement":
            expr = node.child_by_field_name("expression")
            if expr is None:
                for c in node.children:
                    if c.type in ("assignment", "call", "await",
                                  "augmented_assignment", "yield"):
                        expr = c
                        break
            if expr is not None:
                self._py_expr(expr, ctx, file, fn, facts)
        elif t == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            lname = _py_name(left)
            if lname and right is not None:
                if right.type == "call":
                    self._py_binding(lname, right, ctx, file, fn, facts)
                elif right.type == "identifier":
                    facts.append(self._mk(
                        SemanticKind.READ, ctx, file, fn, lname, node,
                        inputs=[_text(right)], outputs=[lname],
                        metadata={"binding": "copy"}))
                elif right.type in ("await", "yield"):
                    inner = right.child_by_field_name("value")
                    if inner is not None and inner.type == "call":
                        self._py_binding(lname, inner, ctx, file, fn, facts)
                else:
                    facts.append(self._mk(
                        SemanticKind.COMPUTE, ctx, file, fn, lname, node,
                        inputs=[_text(right)], outputs=[lname],
                        metadata={"binding": "computed", "expr": _text(right)}))
        elif t == "return_statement":
            val = node.child_by_field_name("value")
            if val is not None:
                self._return_direct(val, ctx, file, fn, facts)
        else:
            for c in node.children:
                if c.type in ("if_statement", "for_statement",
                              "while_statement", "block", "assignment",
                              "expression_statement", "function_definition",
                              "class_definition"):
                    self._walk_py(c, ctx, file, fn, facts)

    def _py_binding(self, lname, call, ctx, file, fn, facts) -> None:
        callee = _py_name(call.child_by_field_name("function"))
        facts.append(self._mk(
            SemanticKind.READ, ctx, file, fn, lname, call, outputs=[lname],
            metadata={"binding": "call_result", "callee": callee or "?",
                      "args": _text(call.child_by_field_name("arguments")
                                    or call)}))

    def _py_expr(self, expr, ctx, file, fn, facts) -> None:
        if expr.type == "call":
            callee = _py_name(expr.child_by_field_name("function"))
            facts.append(self._mk(
                SemanticKind.CALL, ctx, file, fn, f"call:{callee}", expr,
                inputs=[_text(expr.child_by_field_name("arguments"))],
                metadata={"binding": "call", "callee": callee or "?"}))
        elif expr.type == "assignment":
            self._walk_py(expr, ctx, file, fn, facts)

    def _return_direct(self, val, ctx, file, fn, facts) -> None:
        facts.append(self._mk(SemanticKind.RETURN, ctx, file, fn,
                              f"{fn}.return", val, inputs=[_text(val)],
                              metadata={"binding": "return"}))


def local_python_dataflow(source: str, file: str, repo_id="", snapshot=None,
                          provider="local_py_binding") -> list[AnalysisFact]:
    return LocalPythonBinder(provider=provider, repo_id=repo_id,
                             snapshot_id=snapshot).facts_for_file(source, file)
