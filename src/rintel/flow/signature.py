"""Signature extraction for flow port generation (SPEC-P2 §6/§7).

Uses the existing tree-sitter grammars (same language bindings as the
adapters) against the *node span text* of a definition.  Everything that
cannot be determined becomes ``unknown`` — never guessed (§6 rule 4):
- Python: annotated params + annotated return type; missing annotation ->
  ``unknown``.
- C/C++/Go/Java: parameter table as parsed; return type when the grammar
  exposes it; ``void`` -> no output port.
- Fortran: conservative parse of the SUBROUTINE/FUNCTION statement through
  the fixed-form normalizer; FUNCTION -> ``result : unknown``, SUBROUTINE ->
  no output port.
- Parse failure: empty params, ``returns=True`` w/ unknown type — the
  ``unknown`` output is the honest fallback, never a fabricated type.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..tsutil import parser_for

_TYPE_SPECS = ("type_identifier", "primitive_type", "sized_type_specifier",
               "struct_specifier", "enum_specifier", "union_specifier",
               "qualified_identifier", "integral_type",
               "floating_point_type", "boolean_type", "void_type",
               "generic_type", "scoped_type_identifier")


@dataclass
class Signature:
    params: list[dict] = field(default_factory=list)  # {name, type, has_type}
    returns: bool = True       # does the definition produce a result port?
    return_type: str | None = None
    parse_failed: bool = False

    @property
    def return_info(self) -> dict | None:
        if not self.returns:
            return None
        return {"type": self.return_type}


def extract_signature(language: str, text: str,
                      node_kind: str) -> Signature:
    """Best-effort signature of a definition span (`text`)."""
    try:
        if language == "fortran":
            return _fortran(text, node_kind)
        if language == "python":
            return _python(text)
        if language in ("typescript", "javascript"):
            return _ts_js(text)
        if language == "c":
            return _c(text, "c")
        if language == "cpp":
            return _c(text, "cpp")
        if language == "go":
            return _go(text)
        if language == "java":
            return _java(text)
    except Exception:  # noqa: BLE001 — conservative fallback on any parse issue
        return Signature(parse_failed=True)
    return Signature(parse_failed=True)


def _text(node, src: str) -> str:
    return src[node.start_byte:node.end_byte]


# ---------------------------------------------------------------------------
def _python(text: str) -> Signature:
    tree = parser_for("python").parse(text.encode())
    fn = None
    for c in tree.root_node.named_children:
        if c.type == "function_definition":
            fn = c
            break
    if fn is None:
        return Signature(parse_failed=True)
    params: list[dict] = []
    for c in fn.named_children:
        if c.type != "parameters":
            continue
        for prm in c.named_children:
            name, typ = None, None
            for pc in prm.named_children:
                if pc.type == "identifier":
                    name = _text(pc, text)
                elif pc.type == "type":
                    typ = _text(pc, text).strip()
            if name is None and prm.type == "identifier":
                name = _text(prm, text)
            if name is not None:
                if name in ("self", "cls"):
                    continue  # instance/class receiver is not a data port
                params.append({"name": name, "type": typ,
                               "has_type": typ is not None})
        break
    ret = None
    for c in fn.named_children:
        if c.type in ("type", "return_type"):
            ret = _text(c, text).strip()
    return Signature(params=params, returns=True, return_type=ret)


# ---------------------------------------------------------------------------
def _ts_js(text: str) -> Signature:
    tree = parser_for("typescript").parse(text.encode())
    decl = None
    for c in tree.root_node.named_children:
        if c.type in ("function_declaration", "method_definition"):
            decl = c
            break
    if decl is None:
        return Signature(parse_failed=True)
    params: list[dict] = []
    for c in decl.named_children:
        if c.type != "parameters":
            continue
        for prm in c.named_children:
            name, typ = None, None
            for pc in prm.named_children:
                if pc.type == "identifier":
                    name = _text(pc, text)
                elif pc.type == "type_annotation":
                    typ = _text(pc, text).strip()
            if name is None and prm.type in (
                    "required_parameter", "optional_parameter",
                    "rest_pattern"):
                for pc in prm.named_children:
                    if pc.type == "identifier":
                        name = _text(pc, text)
            if name is not None:
                params.append({"name": name, "type": typ,
                               "has_type": typ is not None})
        break
    ret = None
    for c in decl.named_children:
        if c.type == "type_annotation":
            ret = _text(c, text).strip()
            break
    return Signature(params=params, returns=True, return_type=ret)


# ---------------------------------------------------------------------------
def _c(text: str, lang: str) -> Signature:
    tree = parser_for(lang).parse(text.encode())
    fn = None
    for c in tree.root_node.named_children:
        if c.type == "function_definition":
            fn = c
            break
    if fn is None:
        return Signature(parse_failed=True)
    params: list[dict] = []
    ret_parts: list[str] = []
    for c in fn.named_children:
        if c.type == "function_declarator":
            for d in c.named_children:
                if d.type != "parameter_list":
                    continue
                for prm in d.named_children:
                    if prm.type not in ("parameter_declaration",
                                        "optional_parameter_declaration"):
                        continue
                    name, typ_parts = None, []
                    for pc in prm.named_children:
                        if pc.type in _TYPE_SPECS:
                            typ_parts.append(_text(pc, text))
                        elif pc.type == "identifier":
                            name = _text(pc, text)
                        elif pc.type in ("pointer_declarator",
                                         "array_declarator",
                                         "init_declarator"):
                            for idn in pc.named_children:
                                if idn.type == "identifier":
                                    name = _text(idn, text)
                                    break
                    raw = _text(prm, text)
                    typ = " ".join(typ_parts).strip() or None
                    if typ is None:
                        # fallback: strip the name out of the raw text
                        if name:
                            typ = raw.replace(name, "", 1).strip(" ;*&")
                            typ = re.sub(r"\s+", " ", typ).strip() or None
                    if name is None and typ == "void":
                        continue  # `(void)` = no parameters
                    if typ in (None, "") and name is None:
                        continue  # unnamed + untyped -> skip
                    params.append({
                        "name": name if name else f"arg{len(params)+1}",
                        "type": typ, "has_type": typ is not None})
        elif c.type in _TYPE_SPECS:
            ret_parts.append(_text(c, text))
    return_type = " ".join(ret_parts).strip() or None
    returns = return_type is not None and \
        re.sub(r"\s+", " ", return_type) != "void"
    return Signature(params=params, returns=returns,
                     return_type=return_type)


# ---------------------------------------------------------------------------
def _go(text: str) -> Signature:
    tree = parser_for("go").parse(text.encode())
    fn = None
    for c in tree.root_node.named_children:
        if c.type == "function_declaration":
            fn = c
            break
    if fn is None:
        return Signature(parse_failed=True)
    params: list[dict] = []
    res = None
    for c in fn.named_children:
        if c.type == "parameter_list":
            for prm in c.named_children:
                if prm.type != "parameter_declaration":
                    continue
                name, typ = None, None
                for pc in prm.named_children:
                    if pc.type == "identifier":
                        name = _text(pc, text)
                    elif pc.type not in ("comment", "__comment"):
                        typ = _text(pc, text).strip()
                params.append({
                    "name": name if name else f"arg{len(params)+1}",
                    "type": typ if typ else None,
                    "has_type": typ is not None})
        if c.type in ("result", "parameter_list"):
            res = c if c.type == "result" else res
    return_type = None
    if res is not None:
        named = [n for n in res.named_children if n.type != "comment"]
        if len(named) == 1 and named[0].type != "parameter_list":
            return_type = _text(named[0], text).strip()
        else:
            return_type = None  # multiple/structured results -> unknown
    return Signature(params=params, returns=True, return_type=return_type)


def _java(text: str) -> Signature:
    tree = parser_for("java").parse(text.encode())
    fn = None
    for c in tree.root_node.named_children:
        if c.type in ("method_declaration", "constructor_declaration"):
            fn = c
            break
    if fn is None:
        return Signature(parse_failed=True)
    params: list[dict] = []
    ret_parts: list[str] = []
    for c in fn.named_children:
        if c.type in ("formal_parameters", "spread_parameter"):
            for prm in c.named_children:
                if prm.type != "formal_parameter":
                    continue
                name, typ = None, None
                for pc in prm.named_children:
                    if pc.type == "identifier":
                        name = _text(pc, text)
                    elif pc.type in _TYPE_SPECS:
                        typ = _text(pc, text).strip()
                params.append({
                    "name": name if name else f"arg{len(params)+1}",
                    "type": typ if typ else None,
                    "has_type": typ is not None})
        elif c.type in _TYPE_SPECS:
            ret_parts.append(_text(c, text))
    return_type = " ".join(ret_parts).strip() or None
    returns = return_type is not None and return_type != "void"
    return Signature(params=params, returns=returns,
                     return_type=return_type)


# ---------------------------------------------------------------------------
# Fortran: conservative statement parse (fixed-form aware, no fake types).
# ---------------------------------------------------------------------------

_FORTRAN_STMT_RE = re.compile(
    r"^\s*(subroutine|function)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*(.*?)\s*\))?\s*$",
    re.IGNORECASE | re.DOTALL)


def _fortran(text: str, node_kind: str) -> Signature:
    if node_kind not in ("FUNCTION", "SUBROUTINE"):
        return Signature(parse_failed=True)
    lines = text.splitlines()
    stmt = lines[0] if lines else text
    if len(lines) > 1 and len(lines[0]) > 72:
        # fixed-form continuation: join continuation lines
        stmt = " ".join(ln.strip() for ln in lines)
    m = _FORTRAN_STMT_RE.match(stmt)
    if not m:
        return Signature(parse_failed=True)
    is_function = m.group(1).lower() == "function"
    args_raw = m.group(3) or ""
    params: list[dict] = []
    if args_raw.strip() and not args_raw.strip().lower().startswith("bind("):
        for part in args_raw.split(","):
            name = _fortran_arg_name(part)
            if name:
                params.append({"name": name.lower(), "type": None,
                               "has_type": False})
    return Signature(params=params, returns=is_function,
                     return_type=None)


def _fortran_arg_name(part: str) -> str | None:
    """Last identifier of a dummy-arg fragment (strip intent/value/attr)."""
    part = re.sub(r"bind\s*\([^)]*\)", "", part, flags=re.IGNORECASE)
    part = re.sub(r"\bintent\s*\([^)]*\)|\b(value|optional|inout|in|out|"
                  r"dimension)\b", " ", part, flags=re.IGNORECASE)
    ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", part)
    return ids[-1] if ids else None
