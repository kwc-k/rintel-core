"""FAC-EQ0: unresolved-evidence quality audit.

An unresolved callsite is a reference we *observed* that may need a target;
"unresolved" means "current evidence is insufficient", NOT "evidence of
absence" (spec §32 no-fabrication principle).  This module classifies the
*why* of each unresolved callsite into honest buckets so reports / UI can
present evidence uncertainty without manufacturing certainty:

  intrinsic               language-provided intrinsic (Fortran) — never a repo
                          dependency, never an array
  true_external           well-known external library symbol (C stdlib / libm /
                          BLAS-LAPACK / Python builtins)
  ambiguous_symbol        the name matches several definitions (same-file
                          ambiguity or multiple global definitions)
  cross_language          name-mangled boundary reference (e.g. C ``daxpy_``
                          while a Fortran ``daxpy`` exists in-repo)
  call_vs_array_ambiguous Fortran ``name(args)`` in expression position:
                          could be a function call OR an array access; the
                          current evidence (no local declaration) cannot
                          decide — never guessed
  parser_noise            the callee text is not a plausible identifier
  missing_target          a genuine-looking call whose target is simply not in
                          the observation universe
  other                   anything else

Only classification labels are produced here — no resolution, no edges, no
filtering.  The local-declaration elimination (declared arrays are NOT call
sites) happens in the Fortran adapter; this module only labels.
"""
from __future__ import annotations

import re

__all__ = ["UNRESOLVED_REASONS", "classify_unresolved", "lang_of_path",
           "FORTRAN_INTRINSICS", "C_EXTERNALS", "PY_BUILTINS",
           "BLAS_LAPACK_NAMES"]

UNRESOLVED_REASONS = (
    "true_external", "intrinsic", "ambiguous_symbol", "missing_target",
    "cross_language", "call_vs_array_ambiguous", "parser_noise", "other",
)

# ---------------------------------------------------------------------------
# name sets (static, conservative: only names whose status is unambiguous)
# ---------------------------------------------------------------------------

FORTRAN_INTRINSICS: frozenset[str] = frozenset({
    # F77 intrinsics (incl. the classic double-precision variants)
    "abs", "achar", "acos", "aimag", "aint", "alog", "alog10", "amax0",
    "amax1", "amin0", "amin1", "amod", "anint", "asin", "atan", "atan2",
    "cabs", "ccos", "cexp", "char", "clog", "cmplx", "conjg", "cos", "cosh",
    "csin", "csqrt", "dabs", "dacos", "dasin", "datan", "datan2", "dble",
    "dcos", "dcosh", "ddim", "dexp", "dfloat", "dint", "dlog", "dlog10",
    "dmax1", "dmin1", "dmod", "dnint", "dprod", "dsign", "dsin", "dsinh",
    "dsqrt", "dtan", "dtanh", "exp", "float", "iabs", "ichar", "idim",
    "idint", "ifix", "index", "int", "isign", "len", "lge", "lgt", "lle",
    "llt", "log", "log10", "max", "max0", "max1", "min", "min0", "min1",
    "mod", "nint", "real", "sign", "sin", "sinh", "sngl", "sqrt", "tan",
    "tanh",
    # F90+ intrinsics
    "adjustl", "adjustr", "all", "allocated", "any", "associated", "bit_size",
    "btest", "ceiling", "count", "cshift", "dim", "dot_product", "dshiftl",
    "dshiftr", "eoshift", "epsilon", "exponent", "floor", "fraction", "huge",
    "iachar", "iand", "ibclr", "ibits", "ibset", "ieor", "ior", "ishft",
    "ishftc", "kind", "lbound", "len_trim", "logical", "matmul", "maxexponent",
    "maxloc", "maxval", "merge", "minexponent", "minloc", "minval", "modulo",
    "mvbits", "nearest", "not", "pack", "precision", "present", "product",
    "radix", "random_number", "random_seed", "range", "repeat", "reshape",
    "rrspacing", "scale", "scan", "selected_int_kind",
    "selected_real_kind", "set_exponent", "shape", "size", "spacing",
    "spread", "sum", "tiny", "transfer", "transpose", "trim", "ubound",
    "unpack", "verify",
    # common dialect / vendor math intrinsics seen in the wild
    "dconjg", "dfloat", "dimag", "dqreal", "dreal", "dqcmplx", "dcmplx",
    "dshiftl", "dsign", "izabs", "idim", "min0", "amax1", "dsin", "dcos",
})

_C_CORE = frozenset({
    # stdlib / stdio / string / memory / process (C & POSIX, lowercase)
    "abort", "abs", "atof", "atoi", "atol", "atoll", "bsearch", "calloc",
    "exit", "fclose", "feof", "ferror", "fflush", "fgetc", "fgets", "fopen",
    "fprintf", "fputc", "fputs", "fread", "free", "fscanf", "fseek", "ftell",
    "fwrite", "getc", "getchar", "getenv", "gets", "malloc", "memcmp",
    "memcpy", "memmove", "memset", "perror", "printf", "putchar", "puts",
    "qsort", "rand", "realloc", "remove", "rename", "rewind", "scanf",
    "setbuf", "signal", "snprintf", "sprintf", "srand", "strcat", "strchr",
    "strcmp", "strcpy", "strcspn", "strdup", "strlen", "strncat", "strncmp",
    "strncpy", "strpbrk", "strrchr", "strspn", "strstr", "strtod", "strtok",
    "strtol", "strtoul", "system", "time", "tmpfile", "tmpnam", "ungetc",
    "vfprintf", "vsprintf", "write", "read", "open", "close", "sleep",
    "usleep", "getpid", "kill", "fork", "exec", "socket", "bind", "listen",
    "accept", "connect", "send", "recv", "select", "poll", "inet_pton",
    "inet_ntop", "htons", "ntohs", "htonl", "ntohl", "setenv", "unsetenv",
    "getline", "strcasecmp", "strncasecmp", "strtol", "vprintf", "vsnprintf",
})
_LIBM = frozenset({
    "acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh", "cbrt",
    "ceil", "copysign", "cos", "cosh", "erf", "erfc", "exp", "exp2", "expm1",
    "fabs", "fdim", "floor", "fma", "fmax", "fmin", "fmod", "frexp", "hypot",
    "ilogb", "ldexp", "lgamma", "log", "log10", "log1p", "log2", "logb",
    "lrint", "lround", "modf", "nan", "nearbyint", "nextafter", "pow",
    "remainder", "remquo", "rint", "round", "scalbln", "scalbn", "sin",
    "sinh", "sqrt", "tan", "tanh", "tgamma", "trunc",
})
_BLAS_STEMS = [
    "axpy", "copy", "dot", "nrm2", "scal", "swap", "rot", "rotg", "rotm",
    "rotmg", "asum", "iamax", "gemv", "gemm", "ger", "syr", "syr2", "trmv",
    "trsv", "trmm", "trsm", "symm", "syrk", "syr2k", "hemv", "her", "her2",
    "herk", "her2k", "hbmv", "hpmv", "hpr", "hpr2", "sbmv", "spmv", "spr",
    "spr2", "tbmv", "tbsv", "tpmv", "tpsv",
]
_LAPACK_STEMS = [
    "getrf", "getrs", "gesv", "gesvx", "gecon", "gees", "geev", "geevx",
    "gels", "gelsd", "gelss", "gelsy", "geqrf", "geqp3", "geqr2", "gerqf",
    "gelqf", "geqlf", "gges", "ggev", "heev", "heevr", "heevd", "syev",
    "syevr", "syevd", "sytrf", "sytrs", "sytri", "sygv", "posv", "potrf",
    "potrs", "potri", "porfs", "lange", "lantr", "lansy", "lanhe", "lacpy",
    "laset", "lascl", "laswp", "lamch", "lapy2", "lapy3", "larnv", "larnd",
    "larfg", "larf", "larfb", "lartg", "lassq", "labad", "ggglm", "gglse",
    "ormqr", "orgqr", "orm2r", "org2r", "trexc", "trevc", "trsen", "trsyl",
    "steqr", "stegr", "sterf", "hseqr", "gbtrf", "gbtrs", "pbtrf", "pbtrs",
    "tbtrs", "gtcon", "gtsv", "gtrfs", "gttrf", "gttrs", "ptcon", "ptsv",
    "pttrf", "pttrs", "sbev", "sbgv", "stev", "stebz", "stemr", "tgevc",
    "tgexc", "tgsen", "tgsyl", "unmqr", "ungqr",
]
BLAS_LAPACK_NAMES: frozenset[str] = frozenset(
    p + s for p in ("s", "d", "c", "z") for s in (_BLAS_STEMS + _LAPACK_STEMS)
)

PY_BUILTINS: frozenset[str] = frozenset({
    "abs", "all", "any", "ascii", "bin", "bool", "breakpoint", "bytearray",
    "bytes", "callable", "chr", "classmethod", "compile", "complex", "delattr",
    "dict", "dir", "divmod", "enumerate", "eval", "exec", "filter", "float",
    "format", "frozenset", "getattr", "globals", "hasattr", "hash", "help",
    "hex", "id", "input", "int", "isinstance", "issubclass", "iter", "len",
    "list", "locals", "map", "max", "memoryview", "min", "next", "object",
    "oct", "open", "ord", "pow", "print", "property", "range", "repr",
    "reversed", "round", "set", "setattr", "slice", "sorted", "staticmethod",
    "str", "sum", "super", "tuple", "type", "vars", "zip", "__import__",
})

C_EXTERNALS: frozenset[str] = _C_CORE | _LIBM

_IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.]*$")


def lang_of_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("f", "for", "f77", "f90", "f95", "f03", "f08"):
        return "fortran"
    if ext in ("c", "h"):
        return "c"
    if ext in ("cpp", "cc", "cxx", "hpp", "hh", "hxx"):
        return "cpp"
    if ext == "py":
        return "python"
    if ext == "go":
        return "go"
    if ext in ("ts", "tsx", "js", "jsx", "mjs", "cts", "mts"):
        return "typescript"
    if ext == "java":
        return "java"
    return ""


def _n_defs_is_ambiguous(resolve_kind: str | None, n_defs: int) -> bool:
    return resolve_kind == "ambiguous_same_file" or n_defs > 1


def classify_unresolved(callee: str, file_path: str,
                        shape: dict | None = None,
                        resolve_kind: str | None = None,
                        n_defs: int = 0,
                        langs_by_name: dict[str, set[str]] | None = None,
                        ) -> str:
    """Label *why* a callsite stayed unresolved (never resolves, never
    fabricates: pure classification of recorded evidence).

    `shape` is the adapter-recorded call shape:
      {"form": "stmt"|"expr", "args": <int>}
    `langs_by_name`: name -> set(languages) of in-repo definitions (for the
    name-mangling evidence check).
    """
    callee = (callee or "").strip()
    if not callee or not _IDENT_RE.match(callee):
        return "parser_noise"
    lang = lang_of_path(file_path or "")
    low = callee.lower()

    # 1) language-provided intrinsic: definitive, a call-form reflex
    if lang == "fortran" and low in FORTRAN_INTRINSICS:
        return "intrinsic"

    # 2) the name corresponds to several definitions -> ambiguity, not absence
    if _n_defs_is_ambiguous(resolve_kind, n_defs):
        return "ambiguous_symbol"

    # 3) mangled-name boundary mismatch: C foo_ while foo (other lang) exists
    stripped = callee.rstrip("_")
    if stripped and stripped != callee and langs_by_name:
        langs = langs_by_name.get(stripped)
        if langs is None:
            # Fortran names are case-insensitive: look up case-folded
            want = stripped.lower()
            langs = next((v for k, v in langs_by_name.items()
                          if k.lower() == want), None)
        if langs:
            return "cross_language"

    # 4) well-known external library symbol (function-by-convention)
    if (lang in ("c", "cpp") and low in C_EXTERNALS) or \
            (lang == "python" and low in PY_BUILTINS) or \
            (lang in ("c", "cpp", "fortran") and low in BLAS_LAPACK_NAMES):
        return "true_external"

    # 5) Fortran expression-position name(args): array subscript and function
    #    call are syntactically identical -> evidence-insufficient, never guess
    if lang == "fortran" and shape and shape.get("form") == "expr" \
            and (shape.get("args") or 0) >= 1:
        return "call_vs_array_ambiguous"

    # 6) every other genuinely-looking call with no observable target
    return "missing_target"
