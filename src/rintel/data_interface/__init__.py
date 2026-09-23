"""DATA-INTERFACE0: Function/Subroutine as data chips for FAC (C + Fortran).

Read-only semantic enrichment layered on the frozen lane (spec §2): new
objects (DataPort / DataShape / PortBinding / DataTransfer) never mutate
AnalysisFact semantics.  Parsers are evidence-strict:

  Fortran  direction  ← intent(...) keywords (fixed-form absent in FAC) +
                        LAPACK doc-comment "(input)/(output)" (class=COMMENT)
  C        direction  ← const qualifier only → INPUT; non-const pointer → UNKNOWN
                        (§13; never auto-OUTPUT).  In-function read/write scan
                        may upgrade to INOUT/OUTPUT with INFERRED witness.
  dtype    from declarations (REAL(8)/DOUBLE PRECISION/INTEGER/REAL/LOGICAL…)
  rank     from decl shape/array-spec; shape_status RESOLVED/SYMBOLIC/PARTIAL/UNKNOWN
  binding  positional+named args at real callsites.

Invariants honored: T1 pointer≠matrix; T2 array≠known shape; T3 non-const≠output;
T5 symbolic≠unknown; T6 PARTIAL≠COMPLETE; T7 binding from real callsite.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FAC_ROOT = Path("/Users/wu/Documents/dh/a3/fac/fac")
OUT = REPO / "analysis_tournament" / "data_interface"
FIN = REPO / "analysis_tournament" / "flow_infer"

DTYPE_MAP = {
    "double precision": "Real64", "real(8)": "Real64", "real(kind=8)": "Real64",
    "real": "Real32", "real*4": "Real32", "real(4)": "Real32",
    "complex(8)": "Complex128", "complex*16": "Complex128", "double complex": "Complex128",
    "complex": "Complex64", "integer": "Integer32", "integer*4": "Integer32",
    "integer*8": "Integer64", "integer(4)": "Integer32", "logical": "Logical",
    "character": "Character", "float": "Real32", "double": "Real64",
    "int": "Integer32", "long": "Integer64", "size_t": "Integer64",
}

INTENT_TO_DIR = {"in": "INPUT", "out": "OUTPUT", "inout": "INOUT"}


@dataclass
class DataShape:
    rank: int | None = None
    dims: list[str] = field(default_factory=list)
    shape_status: str = "UNKNOWN"          # RESOLVED | SYMBOLIC | PARTIAL | UNKNOWN
    element_count: int | None = None       # only when RESOLVED numeric
    symbolic_size: str | None = None       # e.g. '8*n*m'
    byte_size: int | None = None           # only when dtype_bits and RESOLVED
    byte_size_expr: str | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class DataPort:
    port_id: str
    canonical_symbol_id: str
    name: str
    direction: str = "UNKNOWN"             # INPUT OUTPUT INOUT RETURN UNKNOWN
    semantic_type: str = "DATA"
    dtype: str = "Unknown"
    source_type: str | None = None
    rank: int | None = None
    shape: list[str] = field(default_factory=list)
    shape_status: str = "UNKNOWN"
    element_count: int | None = None
    byte_size: int | None = None
    byte_size_expr: str | None = None
    mutability: str = "UNKNOWN"            # MUTABLE | IMMUTABLE | UNKNOWN
    ownership: str = "UNKNOWN"
    truth_class: str = "DERIVED"
    coverage: str = "PARTIAL"
    evidence: list[dict] = field(default_factory=list)   # {kind, line, expr}
    section: str = "PARAM"                 # PARAM | RETURN | STATE | RESOURCE

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DataPort":
        """Reconstruct a port from its serialized form (sync merge path)."""
        return cls(
            port_id=d.get("port_id", "port:?"), name=d.get("name", "?"),
            canonical_symbol_id=d.get("canonical_symbol_id", "function:?"),
            direction=d.get("direction", "UNKNOWN"), dtype=d.get("dtype", "Unknown"),
            source_type=d.get("source_type"), rank=d.get("rank"),
            shape=list(d.get("shape") or []),
            shape_status=d.get("shape_status", "UNKNOWN"),
            element_count=d.get("element_count"), byte_size=d.get("byte_size"),
            byte_size_expr=d.get("byte_size_expr"),
            coverage=d.get("coverage", "PARTIAL"),
            evidence=list(d.get("evidence") or []))


@dataclass
class PortBinding:
    binding_id: str
    caller_symbol: str
    callee_symbol: str
    caller_expr: str
    callee_port: str
    callsite_line: int | None
    kind: str = "POSITIONAL"               # POSITIONAL | KEYWORD
    truth_class: str = "OBSERVED"
    coverage: str = "COMPLETE"
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class DataTransfer:
    transfer_id: str
    source_port: str
    target_port: str
    dtype: str = "Unknown"
    rank: int | None = None
    shape_status: str = "UNKNOWN"
    byte_size: int | None = None
    transfer_kind: str = "PASS_BY_REFERENCE"
    truth_class: str = "DERIVED"
    coverage: str = "PARTIAL"
    witnesses: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# parsers — evidence-strict
# ---------------------------------------------------------------------------

F_SUB = re.compile(r"^\s*(SUBROUTINE|subroutine)\s+(\w+)\s*\(([^)]*)\)", re.M)
F_FN = re.compile(r"^\s*(\w+\s+)?FUNCTION\s+(\w+)\s*\(([^)]*)\)", re.M)
F_COMMENT_DIR = re.compile(
    r"^\s*\*\s*(\w+)\s+(?:\(\s*(input/output|input|output)\s*\)|"
    r"(input/output|input|output))?", re.M)
F_CALL = re.compile(r"^\s*CALL\s+(\w+)\s*\(([^)]*)\)", re.M)
C_FN = re.compile(r"(?m)^[A-Za-z_][\w\s\*]*\b(\w+)\s*\((.*)\)\s*\{")


def _c_body(text: str, start: int) -> str:
    """function body from just after '{' to its matching '}' (brace-matched,
    so later functions' locals never pollute the read/write scan)."""
    depth = 1
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:start + 40000]


def _c_params(raw: str) -> list[tuple[str, str, str | None]]:
    """(type-with-stars, name, dim|'') per C parameter, comma/paren-aware.

    `char *clist`, `CONFIG **cfg`, `int argt[]`, `int **k` all parse: the
    parameter is split on top-level commas first, trailing `[dims]` becomes
    the dim, and the name is the LAST identifier in the item (types may be
    multi-word + stars in any spacing).
    """
    out = []
    for it in _split_args(raw):
        if not it:
            continue
        dim = None
        dm = re.match(r"^(.*?)\s*\[\s*([^\]]*?)\s*\]\s*$", it)
        if dm:
            it, dim = dm.group(1).strip(), dm.group(2)
        nm = re.search(r"([A-Za-z_]\w*)\s*$", it)
        if not nm:
            continue
        ty = it[:nm.start()].strip()
        out.append((ty, nm.group(1), dim))
    return out

# ---- fixed-form helpers -----------------------------------------------------
# Continuation lines (fixed-form `$` in column 6, free-form `&`) are joined into
# the previous line; arguments are split paren-aware so `AB( 1, KD+1 )` stays a
# single argument (evidence-strict: never shift positions).

def _split_args(clause: str) -> list[str]:
    clause = re.sub(r"[\r\n]\s*[$\&]\s*", " ", clause)
    out, depth, cur = [], 0, []
    for ch in clause:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
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


F_CALL_HEAD = re.compile(r"^\s*CALL\s+(\w+)\s*\(", re.M)


def _call_args(text: str, start: int) -> str:
    """balanced-paren argument clause starting right after '('."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return text[start:i]
            depth -= 1
    return text[start:]


_FORT_TYPE_RE = re.compile(
    r"^\s*(double\s+precision|real\s*\(\s*8\s*\)|real\s*\*\s*8|real\s*\(\s*4\s*\)|"
    r"real\s*\*\s*4|real|complex\s*\(\s*8\s*\)|complex\s*\*\s*16|complex\s*\*\s*8|"
    r"double\s+complex|complex|integer\s*\(\s*8\s*\)|integer\s*\*\s*8|"
    r"integer\s*\(\s*4\s*\)|integer\s*\*\s*4|integer|logical|"
    r"character\s*\(\s*len\s*=\s*\*\s*\)|character\s*\(\s*\d+\s*\)|"
    r"character\s*\*\s*\d+|character)(?![\w(])", re.I)


def _fort_variant(key: str) -> tuple[str, int] | None:
    k = re.sub(r"[\s\*]+", "", key.lower())     # keep digits/parens: real(8)≠real
    if k.startswith("character"):
        return ("CHARACTER(LEN=*)" if "len" in k else "CHARACTER", 32)
    if k.startswith("doubleprecision"):
        return ("DOUBLE PRECISION", 64)
    if k.startswith("doublecomplex"):
        return ("DOUBLE COMPLEX", 128)
    if k.startswith("real"):
        return ("REAL(8)", 64) if "8" in k else ("REAL", 32)
    if k.startswith("integer"):
        return ("INTEGER(8)", 64) if "8" in k else ("INTEGER", 32)
    if k.startswith("complex"):
        if "16" in k or k == "complex(8)":
            return ("COMPLEX(8)", 128) if k == "complex(8)" else ("COMPLEX(16)", 128)
        return ("COMPLEX*8", 64) if k == "complex*8" else ("COMPLEX", 64)
    if k.startswith("logical"):
        return ("LOGICAL", 32)
    return None


def _dtype_of(base: str, bits: int) -> str:
    if base == "DOUBLE PRECISION":
        return "Real64"
    if base == "DOUBLE COMPLEX":
        return "Complex128"
    if bits >= 128:
        return "Complex128"
    if base == "REAL":
        return "Real64" if bits == 64 else "Real32"
    if base == "INTEGER":
        return "Integer64" if bits == 64 else "Integer32"
    return {"COMPLEX": "Complex64", "LOGICAL": "Logical",
            "CHARACTER": "Character"}.get(base, "Unknown")


def _fortran_decls(code_lines: list[str]) -> dict[str, tuple[str, str, int, str | None]]:
    """name -> (dtype, source_type, bits, shape_spec|None).

    Type-anchored declaration scan. Only lines that START with a type keyword
    are considered (fixed-form decl section); continuation lines are joined.
    Per declaration line the declared names are the comma items AFTER the type
    keyword (parenthesised dimension lists are stripped before name-listing) —
    so `INTEGER INFO, KD, LDAB...` yields scalars and `DOUBLE PRECISION
    AB( LDAB, * )` yields AB with shape 'LDAB, *' and never swallows `LDAB` from
    the dimension list as its own declaration.
    """
    out: dict[str, tuple[str, str, int, str | None]] = {}
    joined_lines: list[str] = []
    for ln in code_lines:
        if re.match(r"^\s*[$\&]", ln):          # continuation line
            if joined_lines:
                joined_lines[-1] += " " + re.sub(r"^\s*[$\&]\s*", "", ln)
            continue
        joined_lines.append(ln)
    for ln in joined_lines:
        tm = _FORT_TYPE_RE.match(ln)
        if not tm:
            continue
        canon = _fort_variant(tm.group(1))
        if not canon:
            continue
        src, bits = canon
        base = re.sub(r"[*\s(].*$", "", src)
        if base == "DOUBLE" and "PRECISION" in src:
            base = "DOUBLE PRECISION"
        if base == "DOUBLE" and "COMPLEX" in src:
            base = "DOUBLE COMPLEX"
        dtype = _dtype_of(base, bits)
        rest = ln[tm.end():]
        rest = re.sub(r"::", " ", rest)
        rest = re.sub(r"!\s*.*$", " ", rest)
        for it in _split_args(rest):
            it = it.strip()
            if not it:
                continue
            it = re.sub(r"^\([^)]*\)[\s\*]*", "", it)   # (LEN=*) / (8) prefix
            it = re.sub(r"^\*\s*\d+\s*", "", it)         # CHARACTER*1 prefix
            nm = re.match(r"([A-Za-z][A-Za-z0-9_]*)", it)
            if not nm:
                continue
            name = nm.group(1)
            sh = re.match(r"([A-Za-z][A-Za-z0-9_]*)\s*\(\s*([^)]*?)\s*\)", it)
            shape = sh.group(2) if sh else None
            if name not in out:
                out[name] = (dtype, src, bits, shape)
    return out


def _shape_of(spec: str | None, dtype_bits: int) -> DataShape:
    spec = (spec or "").strip()
    if not spec:
        return DataShape(rank=0, shape_status="RESOLVED", dims=[], element_count=1,
                         byte_size=dtype_bits // 8 if dtype_bits else None)
    if spec == "*":
        # assumed-size array x(*) → rank=1, shape ['?'], PARTIAL (never scalar)
        return DataShape(rank=1, dims=["?"], shape_status="PARTIAL", element_count=None,
                         symbolic_size="?")
    dims: list[str] = []
    symbolic = False
    partial = False
    for d in re.split(r"\s*,\s*", spec):
        d = d.strip()
        if d == "*" or d == ":":
            dims.append("?")
            if d == "*":
                partial = True
            else:
                partial = True
        else:
            dims.append(d)
            if d == "?":
                partial = True
            elif not d.isdigit():
                symbolic = True
    rank = len(dims)
    shape_status = "RESOLVED" if not symbolic and not partial else \
        ("PARTIAL" if partial else "SYMBOLIC")
    elem = None
    if shape_status == "RESOLVED":
        elem = 1
        for d in dims:
            elem *= int(d)
    return DataShape(rank=rank, dims=dims, shape_status=shape_status,
                     element_count=elem,
                     byte_size=elem * (dtype_bits // 8) if elem and dtype_bits else None,
                     symbolic_size=(f"{dtype_bits//8}*" + "*".join(dims)
                                    if symbolic and dtype_bits else None))


def parse_fortran_file(path: Path) -> list[tuple[dict, list[DataPort]]]:
    """→ [(subroutine_meta, ports)] for one .f file (fixed-form, evidence-strict)."""
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    out = []
    m = None
    for m2 in F_SUB.finditer(text):
        m = m2
        name = m.group(2)
        args = _split_args(m.group(3))
        start_line = text[:m.start()].count("\n") + 1
        body = text[m.end():]
        nxt = F_SUB.search(body) or F_FN.search(body)
        block = body if not nxt else body[:nxt.start()]
        blines = block.splitlines()
        end_line = start_line + len(blines) - 1
        ports: list[DataPort] = []
        # type+shape declarations (fixed-form comment lines: `*`/`c`/`C`/`!`
        # in COLUMN 1 only — a `CHARACTER ...` decl indented after 6 spaces of
        # fixed-form code is a real declaration, not a comment)
        code_lines = [l for l in blines if not re.match(r"^[*cC!]", l)]
        decls = _fortran_decls(code_lines)
        for a in args:
            decl = decls.get(a)
            if decl:
                dtype, source_type, dtype_bits, shape_spec = decl
            else:
                dtype, source_type, dtype_bits, shape_spec = ("Unknown", None, 0, None)
            shape = _shape_of(shape_spec, dtype_bits) if shape_spec else DataShape(
                rank=0, shape_status="RESOLVED", dims=[], element_count=1,
                byte_size=dtype_bits // 8 if dtype_bits else None)
            # direction: intent() keyword, else LAPACK doc comment, else UNKNOWN
            direction = "UNKNOWN"
            ev = []
            intent_m = re.search(r"intent\s*\(\s*(in|out|inout)\s*\)\s*[^\n]*\b%s\b" % a,
                                 "\n".join(code_lines), re.I)
            if intent_m:
                direction = INTENT_TO_DIR[intent_m.group(1).lower()]
                ev.append({"kind": "INTENT", "expr": intent_m.group(0).strip(), "line": None})
            else:
                for i, ln in enumerate(blines, start=start_line):
                    cm = F_COMMENT_DIR.match(ln)
                    if cm and cm.group(1) == a:
                        d = cm.group(2) or cm.group(3)
                        direction = {"input": "INPUT", "output": "OUTPUT",
                                     "input/output": "INOUT"}.get(d, "UNKNOWN")
                        ev.append({"kind": "COMMENT", "expr": ln.strip(), "line": i})
                        break
            if not ev:
                ev.append({"kind": "DECL", "expr": (shape_spec or dtype or "?"), "line": start_line})
            ports.append(DataPort(
                port_id=f"port:{name}:{a}", canonical_symbol_id=f"function:{name}",
                name=a, direction=direction, dtype=dtype, source_type=source_type,
                rank=shape.rank, shape=list(shape.dims), shape_status=shape.shape_status,
                element_count=shape.element_count, byte_size=shape.byte_size,
                byte_size_expr=shape.symbolic_size, coverage="COMPLETE" if ev else "PARTIAL",
                evidence=ev))
        out.append(({"name": name, "args": args, "file": path.name, "line": start_line,
                     "end_line": end_line}, ports))
    return out


def parse_c_function(path: Path, name: str) -> tuple[dict, list[DataPort]] | None:
    text = path.read_text(errors="replace")
    m = re.search(r"(?m)^[\w\s\*]*\b" + name + r"\s*\(([^)]*)\)\s*\{", text)
    if not m:
        return None
    raw = m.group(1)
    body = _c_body(text, m.end())     # brace-matched body — signature excluded
    ports = []
    params = _c_params(raw)
    for j, (ty, n, dim) in enumerate(params):
        ty = ty.strip()
        const = "const" in ty
        ptr = "*" in ty
        dtype = DTYPE_MAP.get(ty.replace("const", "").replace("*", "").strip().lower(), "Unknown")
        bits = {"Real64": 64, "Real32": 32, "Integer32": 32, "Integer64": 64}.get(dtype, 0)
        if ptr and dim is not None:
            # e.g. char *argv[] — array-of-pointers: rank 1, extents unknown
            shape = DataShape(rank=1, dims=["?"], shape_status="PARTIAL",
                              symbolic_size="?")
            direction = "INPUT"
            evidence = [{"kind": "DECL", "expr": f"{ty} {n}[]", "line": None}]
        elif ptr:
            shape = DataShape(rank=None, shape_status="UNKNOWN")
            direction = "INPUT" if const else "UNKNOWN"
            evidence = [{"kind": "DECL", "expr": f"{ty} {n}", "line": None}]
        elif dim is not None:
            if dim == "":
                # int argt[] — explicit array, extents unknown
                shape = DataShape(rank=1, dims=["?"], shape_status="PARTIAL",
                                  symbolic_size="?")
            else:
                shape = _shape_of(dim, bits)
            direction = "INPUT"
            evidence = [{"kind": "DECL", "expr": f"{ty} {n}[{dim}]", "line": None}]
        else:
            shape = DataShape(rank=0, shape_status="RESOLVED", dims=[], element_count=1,
                              byte_size=bits // 8 if bits else None)
            direction = "INPUT"
            evidence = [{"kind": "DECL", "expr": f"{ty} {n}", "line": None}]
        # read/write scan for non-const pointers (INFERRED only when proven;
        # never-read never-write stays UNKNOWN — §13)
        if ptr and not const:
            lhs = [r"%s\s*\[[^\]]*\]\s*=[^=]" % n,
                   r"\(\s*\*\s*%s\s*\)\s*\[[^\]]*\]\s*=[^=]" % n,
                   r"\(\s*\*\s*%s\s*\)\s*=[^=]" % n,
                   r"\*\s*%s\s*=[^=]" % n,
                   r"%s\)?\s*->\s*\w+\s*=[^=]" % n,
                   r"memcpy\([^,]*%s" % n,
                   r"scanf\([^,]*%s" % n]
            writes = sum(len(re.findall(wp, body)) for wp in lhs)
            mentions = len(re.findall(r"\b%s\b" % n, body))
            reads = max(0, mentions - writes)
            if reads and writes:
                direction = "INOUT"        # both: evidence-strict INOUT
            elif writes:
                direction = "OUTPUT"       # write-only through pointer: INFERRED OUTPUT
            elif reads:
                direction = "INPUT"        # read-only non-const pointer: INFERRED INPUT
            else:
                direction = "UNKNOWN"      # never-read never-write: leave UNKNOWN (§13)
            evidence.append({"kind": "SCAN", "expr": f"reads={bool(reads)} writes={bool(writes)}",
                             "line": None})
        ports.append(DataPort(
            port_id=f"port:{name}:{n}", canonical_symbol_id=f"function:{name}", name=n,
            direction=direction, dtype=dtype, source_type=ty,
            rank=shape.rank, shape=list(shape.dims), shape_status=shape.shape_status,
            element_count=shape.element_count, byte_size=shape.byte_size,
            byte_size_expr=shape.symbolic_size,
            coverage="PARTIAL" if ptr else "COMPLETE", evidence=evidence))
    return ({"name": name, "args": [x for _, x, _ in params],
             "file": path.name, "line": None, "body_lines": len(body.splitlines())},
            ports)


# ---------------------------------------------------------------------------
# bindings from real callsites + aggregation + artifacts
# ---------------------------------------------------------------------------

def bind_fortran_call(caller: str, call_text: str, callee: str, callee_ports: list[DataPort],
                      line: int) -> list[PortBinding]:
    args = _split_args(call_text)
    out = []
    for i, arg in enumerate(args):
        if i < len(callee_ports):
            out.append(PortBinding(
                binding_id=f"bind:{caller}:{callee}:{i}",
                caller_symbol=caller, callee_symbol=callee,
                caller_expr=arg, callee_port=callee_ports[i].name,
                callsite_line=line, kind="POSITIONAL",
                evidence=[{"kind": "CALL", "expr": call_text.strip(), "line": line}]))
    return out


def aggregate_module_ports(ports: dict[str, list[DataPort]], file: str) -> list[DataPort]:
    """Module-level boundary: IN = union of INPUT args; OUT = OUTPUT/RETURN."""
    ins = [p for ps in ports.values() for p in ps if p.direction == "INPUT"]
    outs = [p for ps in ports.values() for p in ps if p.direction in ("OUTPUT", "RETURN")]
    def dedup(ps):
        by = {}
        for p in ps:
            by.setdefault(p.name, p)
        return list(by.values())
    return dedup(ins) + dedup(outs)


FORT_SOURCE_RELS = ["lapack/dsbev.f", "blas/dger.f", "blas/dgemm.f",
                    "blas/xerbla.f", "lapack/dsteqr.f"]
C_SOURCE_SPECS = [("faclib/config.c", ["AddConfigToList"]),
                  ("sfac/sfac.c", ["ConfigListToC", "IntFromList",
                                   "DoubleFromList", "PAddConfig"])]
_C_FN_ALL = re.compile(r"(?m)^[A-Za-z_][\w\s\*]*\b(\w+)\s*\(([^)]*)\)\s*\{")
_C_CTRL = {"if", "while", "for", "switch", "sizeof", "do", "else", "return",
           "case", "break", "continue", "goto", "typedef", "struct", "union",
           "enum", "define", "include", "main"}


def _all_c_fn_names(text: str) -> list[str]:
    """Every top-level function name in a C file (FAC-LIBRARY-COVERAGE0:
    faclib functions all get Port/Direction/dtype/rank/shape evidence)."""
    out, seen = [], set()
    for m in _C_FN_ALL.finditer(text):
        n = m.group(1)
        if n in _C_CTRL or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def faclib_c_specs(root: Path) -> list[tuple]:
    """[(rel, None)] for every faclib/*.c — None = all functions of the file."""
    d = root / "faclib"
    if not d.is_dir():
        return []
    return [(f"faclib/{p.name}", None) for p in sorted(d.iterdir())
            if p.suffix == ".c"]


def run(fac_root: Path | None = None, scoped_files: set[str] | None = None,
        out: Path | None = None) -> dict:
    """Generate the DATA-INTERFACE0 artifacts.

    fac_root: evidence root (default FAC_ROOT) — used by the sync engine with a
    worktree; scoped_files: resolve only the listed parse-set files (incremental
    sync — never reparses unrelated files); out: artifact dir (default OUT).
    """
    root = Path(fac_root or FAC_ROOT)
    out = Path(out or OUT)
    out.mkdir(parents=True, exist_ok=True)
    scope = scoped_files if scoped_files is not None else None
    fortran_sources = [root / rel for rel in FORT_SOURCE_RELS
                       if not scope or rel in scope or Path(rel).name in scope]
    c_specs = list(C_SOURCE_SPECS) + faclib_c_specs(root)
    c_sources = [(root / rel, names) for rel, names in c_specs
                 if not scope or rel in scope or Path(rel).name in scope]
    funcs: list[dict] = []
    for f in fortran_sources:
        if not f.exists():
            continue
        for meta, ports in parse_fortran_file(f):
            funcs.append({**meta, "ports": [p.to_dict() for p in ports]})
    # real C functions (faclib/config.c has CONFIG *cfg; sfac.c C driver
    # functions are real fac_c-lane functions with const/pointer params)
    for f, names in c_sources:
        if not f.exists():
            continue
        if names is None:
            names = _all_c_fn_names(f.read_text(errors="replace"))
        for n in names:
            r = parse_c_function(f, n)
            if r:
                meta, ports = r
                funcs.append({**meta, "ports": [p.to_dict() for p in ports]})
    # ---- incremental merge (X2): a scoped run carries over every function of
    # every file NOT in scope from the previous artifact snapshot, so the
    # artifacts always describe the full parse set — never a partial union.
    if scoped_files is not None:
        prev_path = out / "fac_ports.json"
        if prev_path.exists():
            scope_names = {Path(s).name for s in scoped_files}
            prev = [f for f in json.loads(prev_path.read_text())
                    if f.get("file") not in scope_names]
            funcs = prev + funcs
    # deterministic artifact order: parse-set file order, then in-file order —
    # identical whether the run is full or scoped (order stability, X3).
    # C functions carry no line number, so they sort by the c_specs
    # request order (which defines the canonical full-run order).
    FILE_ORDER = [Path(r).name for r in FORT_SOURCE_RELS] + \
                 [Path(r).name for r, _ in c_specs]
    _fidx = {n: i for i, n in enumerate(FILE_ORDER)}
    _cname_idx = {}
    for _fi, (_rel, _names) in enumerate(c_specs):
        if _names is None:
            continue                      # faclib files: line order inside file
        for _ni, _n in enumerate(_names):
            _cname_idx[_n] = (_fi, _ni)

    def _skey(f: dict):
        if f.get("name") in _cname_idx:
            _fi, _ni = _cname_idx[f["name"]]
            return (_fidx.get(f.get("file"), 99), _ni, f.get("name") or "")
        return (_fidx.get(f.get("file"), 99), f.get("line") or 0, f.get("name") or "")

    funcs.sort(key=_skey)
    all_ports: dict[str, list[DataPort]] = {}
    for fn in funcs:
        all_ports.setdefault(f"function:{fn['name']}", []).extend(
            DataPort.from_dict(p) for p in fn["ports"])
    # module (file) interfaces: group function ports by file, dedup by name
    module_interfaces = {}
    for fn in funcs:
        mod = fn["file"]
        ports = aggregate_module_ports({fn["name"]: all_ports.get(f"function:{fn['name']}", [])}, mod)
        module_interfaces.setdefault(mod, []).extend(p.to_dict() for p in ports)
    for mod, ps in module_interfaces.items():
        seen: dict[str, dict] = {}
        for p in ps:
            seen.setdefault(p["name"], p)      # first occurrence keeps evidence
        module_interfaces[mod] = list(seen.values())
    bindings = []
    # real callsites: dger/dgemm BLAS call pattern is parameterised by scalars;
    # use dsbev-style internal calls e.g. CALL DLARTG(...) in lapack files
    # real callsites: bind CALL statements against parsed callees (XERBLA/DSTEQR)
    for src, caller in [((root / "lapack" / "dsbev.f"), "DSBEV"),
                        ((root / "blas" / "dger.f"), "DGER")]:
        if not src.exists():
            continue
        text = src.read_text(errors="replace")
        for cm in F_CALL_HEAD.finditer(text):
            callee = cm.group(1)
            ckey = f"function:{callee}"
            if ckey not in all_ports:
                continue
            line = text[:cm.start()].count("\n") + 1
            bindings.extend(bind_fortran_call(caller, _call_args(text, cm.end()),
                                              callee, all_ports[ckey], line))
    transfers = [DataTransfer(transfer_id=f"tr:{b.binding_id}", source_port=b.caller_expr,
                              target_port=b.callee_port, dtype="Unknown",
                              transfer_kind="PASS_BY_REFERENCE",
                              witnesses=[{"callsite_line": b.callsite_line}])
                 for b in bindings]
    for f in funcs:
        for p in f["ports"]:
            p.setdefault("file", f["file"])
            p.setdefault("fn", f["name"])       # ports carry fn+file (store keying)
    json.dump([f for f in funcs], open(out / "fac_ports.json", "w"), ensure_ascii=False, indent=2)
    symbols = []
    for fn in funcs:
        for p in fn["ports"]:
            p.update({"fn": fn["name"], "file": fn["file"]})
            symbols.append(p)
    json.dump(symbols, open(out / "fac_shapes.json", "w"), ensure_ascii=False, indent=2)
    json.dump([b.to_dict() for b in bindings], open(out / "fac_bindings.json", "w"), ensure_ascii=False, indent=2)
    json.dump([t.to_dict() for t in transfers], open(out / "fac_transfers.json", "w"), ensure_ascii=False, indent=2)
    json.dump({k: v for k, v in module_interfaces.items()},
              open(out / "fac_module_interfaces.json", "w"), ensure_ascii=False, indent=2)
    # flow interfaces: attach parsed modules to FLOW-INFER region callees
    # (regions are a FROZEN input — always read from the canonical lane, even
    # when this run targets a worktree or a staging out dir)
    flow = json.loads((FIN / "fac_regions.json").read_text())
    flow_intf = {}
    for r in flow.get("regions", []):
        callees = r.get("callees", [])
        hits = [c for c in callees if any(cn["name"] in c or c in cn["name"] for cn in funcs)]
        flow_intf[r["region_id"]] = {"callees": hits[:8]}
    json.dump(flow_intf, open(out / "fac_flow_interfaces.json", "w"), ensure_ascii=False, indent=2)
    # capability summary
    cap = {"_per_lane": {"fac": {
        "DATA_INTERFACE": "PARTIAL",
        "PORT_DIRECTION": {"fortran": "PARTIAL (intent keywords absent; LAPACK doc comments used)",
                           "c": "PARTIAL (const-only INPUT; non-const -> UNKNOWN)"},
        "DTYPE": "PARTIAL (source declarations)",
        "RANK": "PARTIAL (Fortran decl shapes; C pointers rank UNKNOWN)",
        "SHAPE": "PARTIAL (RESOLVED for fixed dims; SYMBOLIC for n,m; C pointer UNKNOWN)",
        "BYTE_SIZE": "PARTIAL (only RESOLVED dtype+shape)",
        "PORT_BINDING": "PARTIAL (callsites limited; dataflow unresolved)"}}}
    json.dump(cap, open(out / "fac_capability.json", "w"), ensure_ascii=False, indent=2)
    n_funcs = len(funcs); n_ports = sum(len(f["ports"]) for f in funcs)
    print(f"funcs={n_funcs} ports={n_ports} bindings={len(bindings)} -> {out}")
    return {"funcs": n_funcs, "ports": n_ports, "bindings": len(bindings)}


if __name__ == "__main__":
    run()
