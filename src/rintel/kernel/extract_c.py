"""C kernel extractor (real FAC C files: faclib/*.c + sfac/*.c + faclib/*.h).

Evidence-strict walk: comment/string stripping preserves line numbers;
symbols come from declarations, references/operations from executable lines.

Two passes per file:
  pass 1 — file scope: #define NAMED_CONSTANTs, typedefs/structs/enums,
           function-pointer typedefs, prototypes (DECLARES), globals,
           initialized arrays with function-pointer values (§12 registry),
           macro linkage bindings (cfortran/f2c, evidence-backed), CALLABLE
           symbols + FUNCTION contexts + parameter symbols
  pass 2 — function bodies: locals, references, operations, bindings, values
"""
from __future__ import annotations

import re
from pathlib import Path

from rintel.kernel.extract import C_KEYWORDS, C_STDIO, Extractor
from rintel.kernel.model import ArgumentBinding, Evidence, span
from rintel.data_interface import C_FN, _c_body, _c_params, _call_args, _split_args

_DECL_RE = re.compile(
    r"^[\t ]*(?:static\s+|const\s+|volatile\s+|register\s+|extern\s+|inline\s+)*"
    r"([A-Za-z_]\w*(?:\s+(?:unsigned|signed|long|short|char|int|float|double|void))?(?:\s*\*+)*)"
    r"\s*([\*\s]*)(\w+)\s*"
    r"(\[[^\]]*\])?\s*(=\s*([^;]+?))?\s*;$")
C_TYPE_SET = {"void", "char", "short", "int", "long", "float", "double",
              "unsigned", "signed"}
_GLOBAL_RE = re.compile(
    r"^(?:static\s+|extern\s+|const\s+)?([A-Za-z_][\w\s\*]*?)\s+(\*{0,2}\w+)\s*(\[[^\]]*\])?\s*"
    r"(?:=\s*([^;]+?))?\s*;$")
_TYPEDEF_RE = re.compile(r"typedef\s+(?:struct\s*\{[^}]*\}|[\w\s\*]+?)\s+(\w+)\s*;")
_STRUCT_RE = re.compile(r"\b(?:struct|union)\s+(\w+)\s*\{")
_ENUM_RE = re.compile(r"\benum\s+(\w+)\s*\{(.*?)\}", re.S)
_FPTR_RE = re.compile(r"typedef\s+[\w\s\*]+\(\s*\*\s*(\w+)\s*\)\s*\([^)]*\)\s*;")
_DEFINE_RE = re.compile(r"^#\s*define\s+(\w+)(.*)$", re.M)
_CALL_RE = re.compile(r"\b(\w+)\s*\(")
_ASSIGN_RE = re.compile(r"(\w+)\s*=\s*([^;=]+)(?=\s*;|\s*\)|\s*,)")
_INDEX_RE = re.compile(r"\b(\w+)\s*\[([^\]]*)\]")
_PTR_RE = re.compile(r"&\s*(\w+)")
_IDENT_RE = re.compile(r"\b([A-Za-z_]\w*)\b")
_RET_RE = re.compile(r"\breturn\b([^;]*;?)")
_COMPARE_RE = re.compile(r"<=|>=|==|!=|<|>|&&|\|\|")
_CTRL = {"if", "while", "for", "switch", "sizeof", "do", "else", "return",
         "case", "break", "continue", "goto", "typedef", "struct"}
_CONTROL_START = {*_CTRL, "default"}

# multi-line-tolerant function head: `[^)]*` includes newlines so signatures
# like `static int F(int a, int b,\n        ARRAY *variables) {` match;
# nested parens (function pointers) conservatively do NOT match (uncaptured).
C_FN_MULTI = re.compile(r"(?m)^[A-Za-z_][\w\s\*]*\b(\w+)\s*\(([^)]*)\)\s*\{")

# header/forward prototype (declaration): ends with `;`
_PROTO_RE = re.compile(r"(?m)^[\t ]*(?:extern\s+|static\s+)?[A-Za-z_][\w\s\*()]*\b(\w+)\s*\(([^)]*)\)\s*;")

# file-scope initialized arrays: `static METHOD methods[] = { ... }`
_ARRAY_INIT_RE = re.compile(
    r"(?m)^[\t ]*(?:static\s+|const\s+)?([A-Za-z_][\w\s\*]*?)\s+(\w+)\s*\[\s*[^\]\n]*\]\s*=\s*\{")

# cfortran binding: `#define MACRO(...) CCALLSFSUB8(MACRO, fname, ...)` and
# the PROTOCCALLSFSUB8(MACRO, fname, ...) twin — the SECOND arg is the literal
# Fortran symbol name (linkage convention, evidence from the header text).
_CFORTRAN_BIND_RE = re.compile(
    r"\b(?:CCALLSFSUB|PROTOCCALLSFSUB)\d+\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*,")
# f2c wrapper: `#define NAME(a0,...) f_name((a0),...)` — the F77 symbol is NAME.
_F2C_BIND_RE = re.compile(r"(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\s*\([^)]*\)\s+f_(\w+)\s*\(")

LITERALS = {"NULL", "true", "false", "TRUE", "FALSE"}
_DECL_TYPE_BANNED = {"return", "if", "else", "for", "while", "switch", "case",
                     "goto", "break", "continue", "do", "sizeof", "typedef"}


def strip_c_text(text: str) -> str:
    """Remove comments and string literals, preserving line count."""
    out = []
    i, n = 0, len(text)
    in_str = in_char = in_lc = in_blk = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_lc:
            out.append(c)
            if c == "\n":
                in_lc = False
            i += 1
            continue
        if in_blk:
            if c == "*" and nxt == "/":
                out.append("  ")
                i += 2
                in_blk = False
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if in_str:
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if c == "\"":
                in_str = False
                out.append(" ")
            else:
                out.append(" " if c != "\n" else "\n")
            i += 1
            continue
        if in_char:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_char = False
            i += 1
            continue
        if c == "/" and nxt == "/":
            in_lc = True
            out.append(" ")
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_blk = True
            out.append("  ")
            i += 2
            continue
        if c == "\"":
            in_str = True
            out.append(" ")
            i += 1
            continue
        if c == "'":
            in_char = True
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _dtype_c(ty: str) -> str | None:
    t = ty.strip()
    if "double" in t:
        return "Real64"
    if "float" in t:
        return "Real32"
    if "int" in t:
        return "Integer32"
    if "long" in t:
        return "Integer64"
    if "char" in t:
        return "Character"
    return None


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _fn_spans(src: str, spans: list | None = None) -> list[tuple[int, int, re.Match]]:
    """[(start, end_exclusive, match)] per function-head match; end = closing
    brace pos.  Control statements (`if (x) {`, `for (...) {`, ...) must NOT
    become "functions" — identity pollution would break KS2."""
    out = []
    for m in C_FN_MULTI.finditer(src):
        name = m.group(1)
        if name in _CTRL or name in C_KEYWORDS:
            continue
        body = _c_body(src, m.end())
        out.append((m.start(), m.start() + len(body) + 2, m))
    return out


def _balanced_close(src: str, open_pos: int) -> int:
    """index after the `}` matching the `{` at open_pos (source positions)."""
    depth = 0
    i = open_pos
    while i < len(src):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(src)


def _split_top(s: str) -> list[str]:
    """Split on depth-0 commas, balancing (), [] and {} (registry tables)."""
    s = re.sub(r"[\r\n]\s*[$\&]?\s*", " ", s)
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return [a for a in out if a]


def _harvest_macro_bindings(ex: Extractor, text: str, rel: str) -> None:
    """cfortran (cf77.h) / f2c (f2c.h) Fortran linkage macro evidence (§12)."""
    for m in _CFORTRAN_BIND_RE.finditer(text):
        ex.register_macro_binding(m.group(1), m.group(2), None, "cfortran",
                                  rel, _line_of(text, m.start()), m.group(0))
    for m in _F2C_BIND_RE.finditer(text):
        ex.register_macro_binding(m.group(1), m.group(1), m.group(2), "f2c",
                                  rel, _line_of(text, m.start()), m.group(0))


def scan_c_file(ex: Extractor, root: Path, rel: str) -> list[tuple]:
    """Phase A: file-scope symbols (defines/typedefs/structs/enums/prototypes/
    globals/arrays/CALLABLE symbols + FUNCTION contexts + parameter symbols).
    Returns the function span table for walk_c_file."""
    lang = "C"
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    src = strip_c_text(text)
    lines = src.split("\n")
    is_header = rel.endswith(".h")
    file_ctx = ex.add_context("FILE", span(rel, 1, None), None, lang,
                              [Evidence("STRUCTURE", f"file {rel}")])

    def in_function(line_start: int) -> bool:
        return any(s <= line_start < e for s, e, _ in spans)

    # ---------------- pass 0: #define NAMED_CONSTANT (K10) -------------------
    for m in _DEFINE_RE.finditer(text):
        name = m.group(1)
        if name in C_KEYWORDS or name in LITERALS:
            continue
        ln = _line_of(text, m.start())
        ev = [Evidence("DECL", m.group(0).strip(), ln)]
        sym = ex.add_symbol(name, "DATA", file_ctx, span(rel, ln, ln), lang,
                            owner=rel, role="NAMED_CONSTANT", mutability="IMMUTABLE",
                            storage="GLOBAL", evidence=ev)
        ex.add_value("literal", literal=(m.group(2).strip() or "1")[:160],
                     representing=[sym.symbol_id], span_=span(rel, ln, ln),
                     lang=lang, evidence=ev)
        ex.add_location("global_state", name=name, container=sym.symbol_id,
                        storage="GLOBAL", span_=span(rel, ln, ln), lang=lang,
                        evidence=ev)

    # macro linkage bindings (evidence regardless of .c/.h)
    _harvest_macro_bindings(ex, text, rel)

    spans = _fn_spans(src)

    # ---------------- pass 1: file-scope symbols -----------------------------
    for m in _TYPEDEF_RE.finditer(src):
        ln = _line_of(src, m.start())
        if spans and in_function(src.rfind("\n", 0, m.start()) + 1):
            continue
        ex.add_symbol(m.group(1), "TYPE", file_ctx, span(rel, ln, ln), lang,
                      owner=rel, evidence=[Evidence("DECL", m.group(0).strip(), ln)])
    for m in _FPTR_RE.finditer(src):
        ln = _line_of(src, m.start())
        if spans and in_function(src.rfind("\n", 0, m.start()) + 1):
            continue
        ex.add_symbol(m.group(1), "TYPE", file_ctx, span(rel, ln, ln), lang,
                      owner=rel, evidence=[Evidence("DECL", m.group(0).strip(), ln)])
    for m in _STRUCT_RE.finditer(src):
        ln = _line_of(src, m.start())
        if spans and in_function(src.rfind("\n", 0, m.start()) + 1):
            continue
        ex.add_symbol(m.group(1), "TYPE", file_ctx, span(rel, ln, ln), lang,
                      owner=rel, evidence=[Evidence("DECL", m.group(0).strip(), ln)])
    for m in _ENUM_RE.finditer(src):
        ln = _line_of(src, m.start())
        if spans and in_function(src.rfind("\n", 0, m.start()) + 1):
            continue
        ename = m.group(1)
        ex.add_symbol(ename, "TYPE", file_ctx, span(rel, ln, ln), lang,
                      owner=rel, evidence=[Evidence("DECL", m.group(0)[:80], ln)])
        for item in m.group(2).split(","):
            im = re.match(r"\s*([A-Za-z_]\w*)\s*(?:=\s*(\S+))?", item)
            if not im:
                continue
            ev = [Evidence("DECL", im.group(0).strip(), ln)]
            es = ex.add_symbol(im.group(1), "DATA", file_ctx, span(rel, ln, ln),
                               lang, owner=rel, role="NAMED_CONSTANT",
                               mutability="IMMUTABLE", storage="GLOBAL", evidence=ev)
            ex.add_value("literal", literal=im.group(2),
                         representing=[es.symbol_id], span_=span(rel, ln, ln),
                         lang=lang, evidence=ev)

    # prototypes (DECLARES) — header files and forward declarations in .c
    proto_syms = set()
    for m in _PROTO_RE.finditer(src):
        name = m.group(1)
        if name in _CTRL or name in C_KEYWORDS or name in C_TYPE_SET:
            continue
        line_start = src.rfind("\n", 0, m.start()) + 1
        if in_function(line_start):
            continue
        ln = _line_of(src, m.start())
        head = src[m.start():m.start(1)]
        storage = "STATIC" if re.search(r"\bstatic\b", head) else "EXTERNAL"
        sym = ex.add_symbol(name, "CALLABLE", file_ctx, span(rel, ln, ln), lang,
                            owner=rel, storage=storage, is_definition=False,
                            is_declaration=True,
                            evidence=[Evidence("DECL", m.group(0).strip(), ln)])
        proto_syms.add(sym.symbol_id)

    array_syms: dict[str, str] = {}
    # NOTE: the file-scope registry-value walk happens AFTER the CALLABLE
    # symbol pass below (the handlers must be registered before their bare
    # identifiers can resolve to function_address Values).

    # globals (lines outside any function body)
    for ln_no, ln in enumerate(lines, start=1):
        st = ln.strip()
        if not st or st.startswith("#"):
            continue
        line_start = sum(len(x) + 1 for x in lines[:ln_no - 1])
        if in_function(line_start):
            continue
        m = _GLOBAL_RE.match(ln)
        if not m:
            continue
        name = m.group(2).lstrip("*")
        if name in array_syms or name in proto_syms:
            continue                      # handled above (or prototype)
        ty = m.group(1).strip()
        if any(name == w for w in C_KEYWORDS):
            continue
        if re.search(rf"\b{re.escape(name)}\s*\(", ln):   # prototype
            continue
        ev = [Evidence("DECL", st, ln_no)]
        # note: the optional (static|extern|const) prefix is consumed by group 0,
        # NOT part of group(1) — storage/mutability must be read from the line.
        storage_ln = ln
        sym = ex.add_symbol(name, "DATA", file_ctx, span(rel, ln_no, ln_no), lang,
                            owner=rel, role="GLOBAL",
                            mutability=("IMMUTABLE" if re.search(r"\bconst\b", storage_ln)
                                         else "MUTABLE"),
                            dtype=_dtype_c(ty),
                            storage=("STATIC" if re.search(r"\bstatic\b", storage_ln)
                                     else ("EXTERNAL" if re.search(r"\bextern\b", storage_ln)
                                           else "GLOBAL")),
                            evidence=ev)
        rhs = (m.group(4) or "").strip()
        vkind = "literal"
        if rhs and not re.fullmatch(r"[\d.eE+-]+", rhs) and rhs not in LITERALS \
                and not re.fullmatch(r"[\"'].*[\"']", rhs):
            vkind = "scalar" if re.fullmatch(r"\w+", rhs) else "symbolic"
        v = ex.add_value(vkind, dtype=_dtype_c(ty), literal=(rhs or "")[:80] or None,
                         representing=[sym.symbol_id], span_=span(rel, ln_no, ln_no),
                         lang=lang, evidence=ev)
        loc = ex.add_location("global_state", name=name, container=sym.symbol_id,
                              storage="STATIC" if "static" in ty else "GLOBAL",
                              span_=span(rel, ln_no, ln_no), lang=lang, evidence=ev)
        pm = _PTR_RE.search(rhs)
        if pm:
            v.points_to.append(loc.location_id)
            v.alias_rel = "MUST"

    # (in-function static tables are handled AFTER the CALLABLE/FUNCTION ctx
    # pass below — _fn_map needs the FUNCTION contexts to exist.)

    # CALLABLE symbols + FUNCTION contexts + parameter symbols (one scope map)
    for start, end, m in spans:
        name = m.group(1)
        ln = _line_of(src, start)
        head = src[start:m.start(1)]
        storage = "STATIC" if re.search(r"\bstatic\b", head) else "GLOBAL"
        cfold = next((s for s in ex.symbols if s.name == name and s.kind == "CALLABLE"
                      and s.declaration_context == file_ctx.context_id
                      and s.is_definition), None)
        csym = cfold or ex.add_symbol(name, "CALLABLE", file_ctx,
                                      span(rel, ln, ln), lang, owner=rel,
                                      storage=storage, is_definition=True,
                                      evidence=[Evidence("DECL", f"function {name}", ln)])
        body = _c_body(src, m.end())
        fn_ctx = ex.add_context("FUNCTION", span(rel, ln, ln + body.count("\n")),
                                parent=file_ctx.context_id, lang=lang,
                                evidence=[Evidence("DECL", f"function {name}", ln)])
        ex.fn_ctx[csym.symbol_id] = fn_ctx.context_id
        for ty, pname, _dim in _c_params(m.group(2)):
            if pname in C_TYPE_SET:
                continue                    # `void f(void)` has no parameters
            ev = [Evidence("DECL", f"{ty} {pname}", ln)]
            clean_ty = re.sub(r"\s+", " ", ty.replace("*", " ")).strip() or None
            if clean_ty:
                ex.add_type_term(clean_ty, fn_ctx, span(rel, ln, ln), lang,
                                 [Evidence("DECL", ty, ln)])
            symp = ex.add_symbol(pname, "DATA", fn_ctx, span(rel, ln, ln), lang,
                                 owner=name, role="PARAMETER",
                                 mutability="IMMUTABLE" if "const" in ty else "MUTABLE",
                                 dtype=_dtype_c(ty), storage="PARAMETER", evidence=ev)
            ex.add_location("parameter", name=pname, container=symp.symbol_id,
                            storage="PARAMETER", span_=span(rel, ln, ln), lang=lang,
                            evidence=ev)
    ex.file_ctx_by_rel[rel] = file_ctx.context_id
    _scan_file_scope_arrays(ex, src, text, spans, file_ctx, lang, rel, array_syms)
    _scan_fn_static_arrays(ex, src, text, spans, file_ctx, lang, rel)
    return spans


def _scan_file_scope_arrays(ex, src, text, spans, file_ctx, lang, rel,
                            array_syms) -> None:
    """File-scope initialized arrays (registry tables §12): string command
    names + function-pointer values — BOTH are Values, never a name->target
    inference.  Runs AFTER the CALLABLE pass so handler identifiers resolve."""
    for m in _ARRAY_INIT_RE.finditer(src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        if any(s <= line_start < e for s, e, _ in spans):
            continue
        name = m.group(2)
        ty = m.group(1).strip()
        ln = _line_of(src, m.start())
        close = _balanced_close(src, m.end() - 1)
        init_text = text[m.end():close - 1]          # raw positions align 1:1
        ev = [Evidence("DECL", f"{ty} {name}[] = {{...}}", ln)]
        sym = ex.add_symbol(name, "DATA", file_ctx, span(rel, ln, ln), lang,
                            owner=rel, role="GLOBAL",
                            mutability="MUTABLE",
                            dtype=_dtype_c(ty), storage="STATIC", evidence=ev)
        array_syms[name] = sym.symbol_id
        ex.add_location("global_state", name=name, container=sym.symbol_id,
                        storage="STATIC", span_=span(rel, ln, ln), lang=lang,
                        evidence=ev)
        for table_elem in _split_top(init_text):
            inner = _strip_outer_braces(table_elem)
            for item in _split_top(inner):
                item = item.strip().rstrip(",").strip()
                if not item:
                    continue
                if re.fullmatch(r"[\"'].*[\"']", item):
                    ex.add_value("literal", literal=item[:80],
                                 representing=[sym.symbol_id], span_=span(rel, ln, ln),
                                 lang=lang, evidence=[Evidence("DECL", item[:80], ln)])
                    continue
                if re.fullmatch(r"\w+", item) and not item.isupper():
                    res, cands = ex.resolve(item, file_ctx, lang)
                    callables = [s.symbol_id for s in
                                 (ex.symbol_of(c) for c in cands if ex.symbol_of(c))
                                 if s.kind == "CALLABLE"]
                    if callables:
                        add_reference_for(ex, item, file_ctx, span(rel, ln, ln), lang,
                                          [Evidence("REF", item, ln)])
                        ex.add_value("function_address", representing=callables,
                                     span_=span(rel, ln, ln), lang=lang,
                                     evidence=[Evidence("DECL", item, ln)])


def _scan_fn_static_arrays(ex: Extractor, src: str, text: str, spans,
                           file_ctx, lang: str, rel: str) -> None:
    """In-function STATIC tables (the METHOD registry lives INSIDE ParseArgs —
    §12's {"RateCoefficients", PRateCoefficients, METH_VARARGS} case): only
    `static` tables; values = string literals + function-address targets,
    NEVER a string-name->target inference."""
    fn_of_line: dict[int, tuple] = {}
    for start, _end, m in spans:
        csym = next((s for s in ex.symbols
                     if s.name == m.group(1) and s.kind == "CALLABLE"
                     and s.declaration_context == file_ctx.context_id
                     and s.is_definition), None)
        if csym and csym.symbol_id in ex.fn_ctx:
            fnc = next(c for c in ex.contexts if c.context_id == ex.fn_ctx[csym.symbol_id])
            fn_of_line[start] = (csym, fnc)
    for m in _ARRAY_INIT_RE.finditer(src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        if line_start not in fn_of_line:
            continue
        head = src[m.start():m.start(2)]
        if not re.search(r"\bstatic\b", head):
            continue
        csym, fn_ctx = fn_of_line[line_start]
        name = m.group(2)
        ty = m.group(1).strip()
        ln = _line_of(src, m.start())
        close = _balanced_close(src, m.end() - 1)
        init_text = text[m.end():close - 1]          # raw positions align 1:1
        ev = [Evidence("DECL", f"static {ty} {name}[] = {{...}}", ln)]
        sym = ex.add_symbol(name, "DATA", fn_ctx, span(rel, ln, ln), lang,
                            owner=csym.name, role="REGULAR", mutability="MUTABLE",
                            dtype=_dtype_c(ty), storage="STATIC", evidence=ev)
        ex.add_location("variable", name=name, container=sym.symbol_id,
                        storage="STATIC", span_=span(rel, ln, ln), lang=lang,
                        evidence=ev)
        for table_elem in _split_top(init_text):
            inner = _strip_outer_braces(table_elem)
            for item in _split_top(inner):
                item = item.strip().rstrip(",").strip()
                if not item:
                    continue
                if re.fullmatch(r"[\"'].*[\"']", item):
                    ex.add_value("literal", literal=item[:80],
                                 representing=[sym.symbol_id], span_=span(rel, ln, ln),
                                 lang=lang, evidence=[Evidence("DECL", item[:80], ln)])
                    continue
                if re.fullmatch(r"\w+", item) and not item.isupper():
                    res, cands = ex.resolve(item, fn_ctx, lang)
                    callables = [s.symbol_id for s in
                                 (ex.symbol_of(c) for c in cands if ex.symbol_of(c))
                                 if s.kind == "CALLABLE"]
                    if callables:
                        add_reference_for(ex, item, fn_ctx, span(rel, ln, ln), lang,
                                          [Evidence("REF", item, ln)])
                        ex.add_value("function_address", representing=callables,
                                     span_=span(rel, ln, ln), lang=lang,
                                     evidence=[Evidence("DECL", item, ln)])


def _strip_outer_braces(s: str) -> str:
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        return s[1:-1]
    return s


def walk_c_file(ex: Extractor, root: Path, rel: str, spans) -> None:
    """Phase B: function bodies — locals, references, operations, bindings."""
    lang = "C"
    file_ctx = next(c for c in ex.contexts
                    if c.context_id == ex.file_ctx_by_rel[rel])
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    src = strip_c_text(text)
    for start, _end, m in spans:
        name = m.group(1)
        fn_line = _line_of(src, start)
        csym = next(s for s in ex.symbols if s.name == name and s.kind == "CALLABLE"
                    and s.declaration_context == file_ctx.context_id
                    and s.is_definition)
        fn_ctx = next(c for c in ex.contexts if c.context_id == ex.fn_ctx[csym.symbol_id])
        body = _c_body(src, m.end())
        # SOURCE-SPAN-COL0: the body text starts on the line where the head
        # match ENDS (usually the ``{`` line) — the old ``fn_line + i + 1``
        # assumed the body always began on the next line, which shifted every
        # body-level line number by +1 for one-line-brace functions.
        body_line0 = _line_of(src, m.end())
        val_by_name: dict[str, ValueT] = {}
        for i, ln in enumerate(body.split("\n"), start=0):
            ln_no = body_line0 + i
            st = ln.strip()
            if not st or st.startswith("#"):
                continue
            ev = [Evidence("OP", st, ln_no)]
            first = st.split(None, 1)[0] if st.split(None, 1) else ""
            if first in _CONTROL_START and first != "else":
                pass  # control lines handled below; decl regex won't match
            dm = _DECL_RE.match(ln)
            if dm and dm.group(1).strip() not in _DECL_TYPE_BANNED and "(" not in ln:
                lname = dm.group(3)
                evl = [Evidence("DECL", st, ln_no)]
                clean_ty = re.sub(r"\s+", " ",
                                  (dm.group(1) + " " + dm.group(2)).strip()) or None
                if clean_ty:
                    ex.add_type_term(clean_ty, fn_ctx, span(rel, ln_no, ln_no),
                                     lang, [Evidence("DECL", st, ln_no)])
                ls = ex.add_symbol(lname, "DATA", fn_ctx, span(rel, ln_no, ln_no),
                                   lang, owner=name, role="REGULAR", mutability="MUTABLE",
                                   dtype=_dtype_c(dm.group(1)), storage="LOCAL",
                                   evidence=evl)
                loc = ex.add_location("variable", name=lname, container=ls.symbol_id,
                                      storage="LOCAL", span_=span(rel, ln_no, ln_no),
                                      lang=lang, evidence=evl)
                init = (dm.group(6) or "").strip()
                if init:
                    val_by_name[lname] = _add_value_expr(ex, init, ln_no, lang, rel,
                                                         fn_ctx, name, representing=[ls.symbol_id])
                continue

            # BRANCH (if/for/while/switch) + COMPARE
            bm = re.match(r"\b(if|for|while|switch)\s*\((.*?)\)", st)
            if bm:
                guard = bm.group(2)
                _b = ex.add_operation("BRANCH", fn_ctx, actor=csym.symbol_id,
                                      guard=guard, span_=span(rel, ln_no, ln_no),
                                      lang=lang, truth="INFERRED", coverage="PARTIAL",
                                      evidence=ev)
                if _COMPARE_RE.search(guard):
                    ex.add_operation("COMPARE", fn_ctx, actor=csym.symbol_id,
                                     guard=guard, span_=span(rel, ln_no, ln_no),
                                     lang=lang, truth="INFERRED", coverage="PARTIAL",
                                     evidence=ev)
                for rid in _IDENT_RE.findall(guard):
                    if rid in C_KEYWORDS or rid in LITERALS or rid in _CONTROL_START:
                        continue
                    add_reference_for(ex, rid, fn_ctx, span(rel, ln_no, ln_no),
                                      lang, [Evidence("REF", rid, ln_no)])

            rm = _RET_RE.search(st)
            if rm:
                ex.add_operation("RETURN", fn_ctx, actor=csym.symbol_id,
                                 guard=rm.group(1).strip() or None,
                                 span_=span(rel, ln_no, ln_no), lang=lang,
                                 coverage="COMPLETE", evidence=ev)
                for rid in _IDENT_RE.findall(rm.group(1)):
                    if rid in C_KEYWORDS or rid in LITERALS or rid in _CONTROL_START:
                        continue
                    add_reference_for(ex, rid, fn_ctx, span(rel, ln_no, ln_no),
                                      lang, [Evidence("REF", rid, ln_no)])

            if re.search(r"\b(malloc|calloc|realloc)\s*\(", st):
                ex.add_operation("ALLOCATE", fn_ctx, actor=csym.symbol_id,
                                 resource_refs=["resource:memory"],
                                 span_=span(rel, ln_no, ln_no), lang=lang, evidence=ev)
            free_m = re.search(r"\bfree\s*\(", st)
            if free_m:
                ex.add_operation("FREE", fn_ctx, actor=csym.symbol_id,
                                 resource_refs=["resource:memory"],
                                 span_=span(rel, ln_no, ln_no), lang=lang, evidence=ev)

            # calls
            for cm in _CALL_RE.finditer(st):
                cname = cm.group(1)
                if cname in _CTRL or cname in {"malloc", "calloc", "realloc", "free"}:
                    continue
                cr = _CALL_RE.match(st)
                if cr and cr.start() != cm.start() and cm.start() > 0 and st[cm.start() - 1] == ".":
                    continue
                argclause = _call_args(st, cm.end())
                args = _split_args(argclause)
                if not args and cname in C_KEYWORDS:
                    continue
                ref = add_reference_for(ex, cname, fn_ctx, span(rel, ln_no, ln_no),
                                        lang, [Evidence("REF", cname, ln_no)])
                callee_entry = ref.symbol_id if ref.resolution == "EXACT" else None
                callee_sym = ex.symbol_of(callee_entry) if callee_entry else None
                defined = bool(callee_sym and callee_sym.is_definition)
                res = []
                if cname in C_STDIO:
                    res.append("resource:file")
                if ref.resolution == "UNKNOWN":
                    res.append("resource:external_lib")
                bindings = []
                if callee_entry:
                    callee_fn = ex.fn_ctx.get(callee_entry)
                    if callee_fn:
                        formals = sorted(
                            (s for s in ex.symbols_in(next(
                                c for c in ex.contexts if c.context_id == callee_fn))
                                if s.role == "PARAMETER"),
                            key=lambda s: _param_pos(ex, callee_fn, s.name))
                        for j, a in enumerate(args):
                            if j >= len(formals):
                                break
                            bindings.append(ArgumentBinding(
                                binding_id=ex._nid("bind"), call_operation_id="",
                                actual=a, formal_symbol_id=formals[j].symbol_id,
                                position=j + 1, truth="OBSERVED",
                                evidence=[Evidence("CALL", a, ln_no)]))
                op = ex.add_operation(
                    "CALL", fn_ctx, actor=csym.symbol_id,
                    target_reference=ref.reference_id, resource_refs=res,
                    span_=span(rel, ln_no, ln_no), lang=lang,
                    truth="OBSERVED" if defined else "INFERRED",
                    coverage="COMPLETE" if defined else "PARTIAL",
                    evidence=ev, callee_entry=callee_entry)
                for b in bindings:
                    b.call_operation_id = op.operation_id
                    ex.bindings.append(b)
                for a in args:
                    _add_value_expr(ex, a, ln_no, lang, rel, fn_ctx, name)

            # assignment
            am = _ASSIGN_RE.search(st)
            if am:
                lname = am.group(1)
                rhs = am.group(2)
                if lname not in C_KEYWORDS and not re.match(r"\b(if|while|for|switch|return)\b", st):
                    _handle_assignment(ex, fn_ctx, name, csym, ln_no, lang, rel,
                                       lname, rhs, val_by_name, ev, param_names=None,
                                       in_params=set())

            # INDEX ops
            for im in _INDEX_RE.finditer(st):
                base = im.group(1)
                if base in C_KEYWORDS or base in LITERALS:
                    continue
                add_reference_for(ex, base, fn_ctx, span(rel, ln_no, ln_no), lang,
                                  [Evidence("REF", base, ln_no)])
                ex.add_operation("INDEX", fn_ctx, actor=csym.symbol_id,
                                 inputs=[im.group(2)], outputs=[base],
                                 span_=span(rel, ln_no, ln_no), lang=lang,
                                 truth="INFERRED", coverage="PARTIAL", evidence=ev)


# small alias to avoid circular import typing
ValueT = "Value"


def _param_pos(ex: Extractor, fn_ctx_id: str, name: str) -> int:
    ctx = next(c for c in ex.contexts if c.context_id == fn_ctx_id)
    syms = [s for s in ex.symbols_in(ctx) if s.role == "PARAMETER"]
    for i, s in enumerate(syms):
        if s.name == name:
            return i
    return 10 ** 6


def _storage_of(ex: Extractor, name: str, ctx, lang: str) -> str | None:
    res, cands = ex.resolve(name, ctx, lang)
    if res != "EXACT":
        return None
    sym = ex.symbol_of(cands[0])
    return sym.storage_class if sym else None


def _handle_assignment(ex, fn_ctx, owner, csym, ln_no, lang, rel, lname, rhs,
                       val_by_name: dict, ev, param_names=None, in_params=None):
    add_reference_for(ex, lname, fn_ctx, span(rel, ln_no, ln_no), lang,
                      [Evidence("REF", lname, ln_no)])
    stor = _storage_of(ex, lname, fn_ctx, lang)
    state = "NONE"
    if stor in ("GLOBAL", "STATIC"):
        state = "WRITE"
    ex.add_operation("WRITE", fn_ctx, actor=csym.symbol_id, inputs=[], outputs=[lname],
                     span_=span(rel, ln_no, ln_no), lang=lang, truth="INFERRED",
                     coverage="PARTIAL", evidence=ev, state_use=state)
    # pointer semantics (K4): &x / &(expr[..]) / &(a.b) -> value of MUST alias
    pm = re.search(r"&\s*\(?\s*([A-Za-z_]\w*)", rhs)
    if pm:
        pn = pm.group(1)
        res, cands = ex.resolve(pn, fn_ctx, lang)
        loc = None
        if res == "EXACT":
            lsym = ex.symbol_of(cands[0])
            loc = next((l for l in ex.locations if l.container == lsym.symbol_id
                        and l.kind in ("variable", "parameter", "global_state")), None)
        v = ex.add_value("scalar", dtype=None, representing=cands if res == "EXACT" else [],
                         points_to=[loc.location_id] if loc else [],
                         span_=span(rel, ln_no, ln_no), lang=lang,
                         evidence=[Evidence("REF", rhs, ln_no)])
        v.alias_rel = "MUST"
        if loc:
            loc.pointed_from.append(v.value_id)
        val_by_name[lname] = v
    elif re.fullmatch(r"\w+", rhs):
        # q = p : copy the alias relation (derived alias, identity NOT merged)
        base = val_by_name.get(rhs) or _find_value_for(ex, rhs, fn_ctx)
        if base is not None and base.points_to:
            v = ex.add_value("scalar", dtype=base.dtype, representing=base.representing,
                             points_to=list(base.points_to), span_=span(rel, ln_no, ln_no),
                             lang=lang, evidence=[Evidence("REF", rhs, ln_no)])
            v.alias_rel = "MAY"            # q = p : derived alias
            val_by_name[lname] = v
        elif base is not None and base.kind == "literal":
            val_by_name[lname] = base
    else:
        v = _add_value_expr(ex, rhs, ln_no, lang, rel, fn_ctx, owner)
        val_by_name[lname] = v
    for rid in _IDENT_RE.findall(rhs):
        if rid in C_KEYWORDS or rid in LITERALS or rid in _CONTROL_START \
                or re.search(rf"\b{rid}\s*\(|&\s*{rid}|{rid}\s*\[", rhs):
            continue
        add_reference_for(ex, rid, fn_ctx, span(rel, ln_no, ln_no), lang,
                          [Evidence("REF", rid, ln_no)])
        stor = _storage_of(ex, rid, fn_ctx, lang)
        if stor in ("GLOBAL", "STATIC"):
            from rintel.kernel.model import Operation
            ex.add_operation("READ", fn_ctx, actor=csym.symbol_id, inputs=[rid],
                             span_=span(rel, ln_no, ln_no), lang=lang,
                             truth="INFERRED", coverage="PARTIAL",
                             evidence=[Evidence("REF", rid, ln_no)],
                             state_use="READ")


def _find_value_for(ex, name: str, ctx) -> ValueT | None:
    res, cands = ex.resolve(name, ctx, "C")
    if res != "EXACT":
        return None
    sym = ex.symbol_of(cands[0])
    return next((v for v in ex.values if sym.symbol_id in v.representing
                 and v.points_to), None)


def _add_value_expr(ex, expr, ln, lang, rel, ctx, owner, representing=None) -> ValueT | None:
    """value for an expression: literal / scalar / symbolic / array_tensor /
    function_address / unknown.  Returns the Value (or None for '&x' handled
    elsewhere)."""
    e = expr.strip().rstrip(";").strip()
    if not e or e.startswith("&"):
        return None
    ev = [Evidence("OP", e, ln)]
    if re.fullmatch(r"-?[\d.eE+-]+", e):
        return ex.add_value("literal", literal=e, span_=span(rel, ln, ln), lang=lang, evidence=ev)
    if re.fullmatch(r"[\"'].*[\"']", e):
        return ex.add_value("literal", literal=e, span_=span(rel, ln, ln), lang=lang, evidence=ev)
    if e in LITERALS:
        return ex.add_value("literal", literal=e, span_=span(rel, ln, ln), lang=lang, evidence=ev)
    if re.fullmatch(r"\w+", e):
        res, cands = ex.resolve(e, ctx, lang)
        callables = [s.symbol_id for s in
                     (ex.symbol_of(c) for c in cands if ex.symbol_of(c))
                     if s.kind == "CALLABLE"]
        if callables:
            return ex.add_value("function_address", representing=callables,
                                span_=span(rel, ln, ln), lang=lang, evidence=ev)
        kind = "scalar" if res != "UNKNOWN" else "unknown"
        return ex.add_value(kind, representing=cands, span_=span(rel, ln, ln),
                            lang=lang, evidence=ev)
    if re.fullmatch(r"\w+\s*\(.*\)", e):
        return ex.add_value("array_tensor" if "[" not in e else "array_tensor",
                            representing=representing or [], span_=span(rel, ln, ln),
                            lang=lang, evidence=ev)
    return ex.add_value("symbolic", representing=representing or [],
                        span_=span(rel, ln, ln), lang=lang, evidence=ev)


def add_reference_for(ex, name, ctx, sp, lang, evidence):
    res, cands = ex.resolve(name, ctx, lang)
    ev = list(evidence)
    if ex.macro_targets.get(name):
        # macro-linkage path (EXACT target, or UNKNOWN-with-binding-evidence):
        # attach the header binding evidence (cf77.h/f2c.h line) to the ref
        _, _c, mev = ex.macro_resolve(name)
        ev = ev + list(mev)
    return ex.add_reference(name, ctx, sp, lang, res, cands,
                            cands[0] if res == "EXACT" else None, ev)
