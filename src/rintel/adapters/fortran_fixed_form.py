"""Fixed-form (.f/.for) conservative normalizer (S6).

The free-form tree-sitter grammar cannot parse fixed-form source directly:
column rules (cols 1-5 statement label, col 6 continuation marker, cols 7-72
statement field) and legacy conventions (comment column C/c/*/D, tab format,
extended line length, blanks inside numeric literals, Hollerith constants)
all differ from the grammar's expectations.

This module rewrites fixed-form text into grammar-shaped text — nothing more.
No semantic fabrication happens: constructs the graph does not model keep
their text (they simply produce no facts beyond what they are) and every
file-level convention is recorded in `notes`/`stats`.

Frozen S5B contract: tree-sitter offsets are UTF-8 byte indexes.  The
normalizer keeps an *exact* position map from every normalized character back
to the ORIGINAL file (line + char column), so all evidence locations
(`start_line`/`start_col`) point at the original `.f`/`.for` file — never at
the normalized buffer — and multi-byte (UTF-8) content before a symbol stays
byte-correct (the S5B bug class).
"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

# legacy statements the graph does not model (recorded, never fabricated)
LEGACY_KEYWORDS = (
    "COMMON", "BLOCK DATA", "DATA", "IMPLICIT", "EQUIVALENCE", "NAMELIST",
    "EXTERNAL", "INTRINSIC", "ENTRY", "PARAMETER",
)

_LEGACY_RE = re.compile(
    r"^\s{0,5}(" + "|".join(k.replace(" ", r"\s+") for k in LEGACY_KEYWORDS) + r")\b",
    re.I)


@dataclass
class FixedFormNormalization:
    """Normalized text + exact original-position map + recorded notes."""
    text: str
    char_line: list[int]      # per normalized char: original 1-based line
    char_col: list[int]       # per normalized char: original 1-based col
    line_orig: list[int]      # per normalized line: orig line (for rows)
    line_byte_start: list[int]  # byte offset of each normalized line start
    byte_to_char: list[int]   # normalized byte offset -> char index
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def orig_pt(self, row: int, byte_col: int) -> tuple[int, int]:
        """Original (1-based line, char col) of a tree-sitter point."""
        if not self.char_line:
            return 1, 1
        gb = self._global_byte(row, byte_col)
        ci = self._char_idx(gb)
        return self.char_line[ci], self.char_col[ci]

    def orig_end(self, row: int, byte_col: int) -> tuple[int, int]:
        """Original position right AFTER the last char before `byte_col`."""
        if not self.char_line:
            return 1, 1
        gb = self._global_byte(row, byte_col) - 1
        ci = self._char_idx(gb)
        return self.char_line[ci], self.char_col[ci] + 1

    def _global_byte(self, row: int, byte_col: int) -> int:
        if row < 0 or row >= len(self.line_byte_start):
            return 0
        return min(self.line_byte_start[row] + max(0, byte_col),
                   max(0, len(self.byte_to_char) - 1))

    def _char_idx(self, byte_off: int) -> int:
        if not self.char_line:
            return 0
        if byte_off <= 0:
            return 0
        if byte_off >= len(self.byte_to_char) - 1:
            return len(self.char_line) - 1
        return min(self.byte_to_char[byte_off], len(self.char_line) - 1)


# --------------------------------------------------------------------------
# text rewrites (string-aware; operate on a statement field or logical line)
# --------------------------------------------------------------------------

def fix_numeric_spacing(s: str) -> str:
    """Remove legacy blanks inside numbers: `0.87 d0`, `8D 00`, `3344 5667`.

    String-aware: blanks inside character literals are significant and kept.
    """
    out, inq, cha = [], False, None
    for ch in s:
        if inq:
            out.append(ch)
            if ch == cha:
                inq = False
            continue
        if ch in ("'", '"'):
            inq, cha = True, ch
            out.append(ch)
            continue
        out.append(ch)
    t = "".join(out)
    t = re.sub(r"(?<=\d) +(?=\d)", "", t)                  # 1. 2 -> 1.2
    t = re.sub(r"(?<=\d) +(?=[dDeEqQ][+-]?\d)", "", t)     # 0.87 d0 -> 0.87d0
    t = re.sub(r"([0-9])[dDeEqQ] +([+-]?\d)", r"\1\2", t)  # 8D 00 -> 8D00
    # `309 .` -> `309.` (blank before a terminal decimal point); `.eq.` is
    # untouched because the dot there is followed by a letter, not a digit
    # or comma.
    t = re.sub(r"(?<=\d) +(?=\.(?:[0-9,]|$))", "", t)
    return t


def fix_hollerith(s: str) -> str:
    """Rewrite `N<hollerith>` constants into quoted strings.

    Runs on the *joined* logical statement so the count sees the full
    concatenated card text (legacy 60h constants may span continuation
    cards).  Guarded so identifier-looking text (`H43H34`) is untouched.
    """
    out, inq, i, n = [], False, 0, len(s)
    while i < n:
        ch = s[i]
        if inq:
            out.append(ch)
            if ch == "'":
                inq = False
            i += 1
            continue
        if ch == "'":
            inq = True
            out.append(ch)
            i += 1
            continue
        if ch.isdigit():
            j = i
            while j < n and s[j].isdigit():
                j += 1
            if j < n and s[j] in ("h", "H") and \
                    (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_")):
                cnt = int(s[i:j])
                content = s[j + 1:j + 1 + cnt]
                if len(content) == cnt:
                    out.append("'" + content.replace("'", "''") + "'")
                    i = j + 1 + cnt
                    continue
            out.append(s[i:j])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def collapse_blank_runs(s: str) -> str:
    """F66-era blank-insensitive identifiers: `G A M M A` -> `GAMMA`.

    Only whole runs of blank-separated letters starting at a token boundary
    collapse; `GO TO`, `CALL FOO`, `IF (X) THEN` are left alone.
    """
    return re.sub(r"(?<![0-9A-Za-z_])([A-Za-z])( [A-Za-z])+",
                  lambda m: m.group(0).replace(" ", ""), s)


# --------------------------------------------------------------------------
# per-line classification (fixed-form column rules)
# --------------------------------------------------------------------------

_COMMENT_COL1 = ("C", "*", "!", "D")


def classify(raw: str):
    """Classify one raw line -> (kind, payload...).

    kinds: blank | comment(text) | cont(text) | stmt(label, text) |
    stmt_short(text)  (<=5 cols, no label field).
    Statement fields are NOT rstrip'ed: trailing blanks of a card are part
    of the verbatim card text (matters for legacy Hollerith counts).
    """
    if not raw:
        return ("blank",)
    if raw[0] == "\t":
        rest = raw[1:]
        if rest[:1].isdigit() and rest[0] not in "0":
            return ("cont", rest[1:])                    # tab-format continuation
        if rest[:1] in ("!", "*") or \
                (rest[:1].upper() == "C" and len(rest) > 1 and rest[1] == " "):
            return ("comment", rest[1:])
        return ("stmt", "", rest)                        # tab = cols 1-6
    if len(raw) <= 5:
        if raw[:1].upper() in _COMMENT_COL1:
            return ("comment", raw.rstrip())
        return ("stmt_short", raw.rstrip())
    if raw[:1].upper() in _COMMENT_COL1:
        return ("comment", raw[1:].rstrip())
    if raw[5] not in (" ", "0"):
        return ("cont", raw[6:])                         # col-6 continuation
    return ("stmt", raw[0:5].rstrip(), raw[6:])


def _extended_layout(text: str) -> bool:
    """File written with code past col 72? (col 73+ content that is not the
    all-digit sequence-number style, and not on comment lines)."""
    for raw in text.split("\n"):
        if not raw or raw[0] == "\t":
            continue
        if len(raw) > 72 and raw[:1].upper() not in _COMMENT_COL1:
            tail = raw[71:].strip()
            if tail and not re.match(r"^[\d\s\-+]*$", tail):
                return True
    return False


# --------------------------------------------------------------------------
# the normalizer
# --------------------------------------------------------------------------

def normalize_fixed_form(relpath: str, source: str) -> FixedFormNormalization:
    """Normalize fixed-form source; returns text + original position map."""
    lines = source.split("\n")
    extended = _extended_layout(source)
    limit = None if extended else 72

    # fragments of the logical statement currently being built:
    #   (pre_text, [ (orig_line, orig_col) per char of pre_text ])
    frags: list[tuple[str, list[tuple[int, int]]]] = []
    out_lines: list[tuple[str, list[tuple[int, int]] | None, int]] = []
    # out_lines entries: (text, origins, orig_line) - None origins for blanks
    stats = {"cont_lines": 0, "comment_lines": 0, "label_lines": 0,
             "tab_lines": 0, "long_lines": 0, "hollerith": 0,
             "extended": int(extended), "legacy": {}}

    def flush() -> None:
        if frags:
            pre = "".join(f[0] for f in frags)
            pre_orig = []
            for _, o in frags:
                pre_orig.extend(o)
            out_lines.append((pre, pre_orig, frags[0][1][0][0] if frags[0][1] else 1))
            frags.clear()

    for ln_no, raw in enumerate(lines, 1):
        raw = raw.rstrip("\r")
        stats["long_lines"] += len(raw) > 72
        stats["tab_lines"] += int(raw.startswith("\t"))
        info = classify(raw)
        kind = info[0]
        if kind == "blank":
            flush()
            out_lines.append(("", None, ln_no))
            continue
        if kind == "comment":
            flush()
            stats["comment_lines"] += 1
            body = info[1]
            origins: list[tuple[int, int]] = []
            body = _norm_comment_body(raw, body, origins, ln_no)
            out_lines.append(("! " + body, origins, ln_no))
            continue
        if kind == "cont":
            stats["cont_lines"] += 1
            if raw[0] == "\t":
                field = info[1]
                start_col = 8                             # tab + marker
            else:
                field = info[1]
                start_col = 7
                if limit is not None:
                    field = field[:limit - 6]
            frags.append(_frag_for_field(field, ln_no, start_col))
            continue
        # stmt / stmt_short
        flush()
        if kind == "stmt_short":
            text = info[1]
            origins = _linear_origins(len(text), ln_no, 1)
            out_lines.append((fix_numeric_spacing(text),
                              _fix_origins_follow(
                                  origins, text, fix_numeric_spacing(text)),
                              ln_no))
            continue
        label, fld = info[1], info[2]
        if limit is not None:
            fld = fld[:limit - 6]
        fld_fixed = fix_numeric_spacing(fld)
        origins_f = _fix_origins_follow(
            _linear_origins(len(fld), ln_no, 7), fld, fld_fixed)
        if label:
            stats["label_lines"] += 1
            frags_final = (label + " " + fld_fixed,
                           _linear_origins(len(label), ln_no, 1) +
                           [(ln_no, 6)] + origins_f)
        else:
            frags_final = (fld_fixed, origins_f)
        frags.append(frags_final)
    flush()

    # ---------- legacy counting on raw statement fields ---------------------
    legacy_counts: dict[str, int] = {}
    for raw in lines:
        if not raw:
            continue
        if raw[0].upper() in _COMMENT_COL1:
            continue
        if raw[0] == "\t":
            continue
        if len(raw) > 6:
            field = raw[6:]
        else:
            continue
        m = _LEGACY_RE.match(field)
        if m:
            k = m.group(1).upper()
            legacy_counts[k] = legacy_counts.get(k, 0) + 1
    stats["legacy"] = legacy_counts

    # ---------- final transforms + char-level origin map --------------------
    final_lines: list[str] = []
    char_line: list[int] = []
    char_col: list[int] = []
    line_orig: list[int] = []
    line_byte_start: list[int] = []
    line_char_start: list[int] = []

    for text, origins, blank_line in out_lines:
        line_byte_start.append(sum(len(l) + 1 for l in final_lines))
        line_char_start.append(len(char_line))
        if origins is None:
            final_lines.append("")
            line_orig.append(blank_line)
            continue
        if text.startswith("! "):
            final_lines.append(text)
            line_orig.append(origins[0][0] if origins else blank_line)
            # `!` and the lens space are synthesized at col 1; body chars
            # keep their original columns (marker was at col 1)
            char_line.append(blank_line)
            char_col.append(1)
            char_line.append(blank_line)
            char_col.append(1)
            for origin in origins:
                char_line.append(origin[0])
                char_col.append(origin[1])
            continue
        t = fix_numeric_spacing(text)
        t2 = fix_hollerith(t)
        if t2 != t:
            stats["hollerith"] += 1
        t3 = collapse_blank_runs(t2)
        final_lines.append(t3)
        line_orig.append(origins[0][0] if origins else 1)
        _map_chars(text, t3, origins, char_line, char_col)

    norm_text = "\n".join(final_lines)
    data = norm_text.encode("utf-8")
    byte_to_char: list[int] = []
    # global byte offset -> char index (chars only; newline bytes map to the
    # last char of their line)
    for k in range(len(line_byte_start)):
        lend = line_byte_start[k + 1] - 1 if k + 1 < len(line_byte_start) \
            else len(data)
        cstart = line_char_start[k]
        local = 0
        for b in range(line_byte_start[k], lend):
            byte_to_char.append(cstart + local)
            if (data[b] & 0xC0) != 0x80:
                local += 1
        if lend < len(data):
            byte_to_char.append(cstart + max(0, local - 1))  # the newline byte
    byte_to_char.append(len(char_line) - 1 if char_line else 0)

    notes: list[str] = []
    if extended:
        notes.append("fixed_form:line_length=extended")
    if stats["tab_lines"]:
        notes.append(f"fixed_form:tab_format={stats['tab_lines']}")
    if stats["hollerith"]:
        notes.append(f"fixed_form:hollerith={stats['hollerith']}")
    for k in legacy_counts:
        notes.append(f"fixed_form:legacy={k.lower()}")

    return FixedFormNormalization(
        text=norm_text, char_line=char_line, char_col=char_col,
        line_orig=line_orig, line_byte_start=line_byte_start,
        byte_to_char=byte_to_char,
        notes=notes, stats=stats)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _linear_origins(n: int, line: int, col: int) -> list[tuple[int, int]]:
    return [(line, col + i) for i in range(n)]


def _frag_for_field(field: str, line: int, col: int):
    """A join fragment: (post-numeric-fix text, origins)."""
    fixed = fix_numeric_spacing(field)
    return fixed, _fix_origins_follow(
        _linear_origins(len(field), line, col), field, fixed)


def _norm_comment_body(raw: str, body: str, origins: list[tuple[int, int]],
                       line: int) -> str:
    """Comment body with per-char origins (body chars keep original cols)."""
    start_col = 2 if len(raw) > 1 else 1
    # comment body = chars after the col-1 marker; they keep their columns
    for i in range(len(body)):
        origins.append((line, start_col + i))
    return body


def _fix_origins_follow(origins: list[tuple[int, int]], pre: str,
                        post: str) -> list[tuple[int, int]]:
    """Origins for `post` (a rewrite of `pre`): deletions keep alignment.

    Rewrites only ever remove characters (spaces / numeric markers), so a
    two-pointer skip scan is exact: every surviving char keeps its origin.
    """
    if pre == post:
        return origins
    out: list[tuple[int, int]] = []
    i = j = 0
    while j < len(post):
        if i < len(pre) and pre[i] == post[j]:
            out.append(origins[i])
            i += 1
            j += 1
        elif i < len(pre):
            # skip deleted pre char(s): next equal char
            k = i
            while k < len(pre) and pre[k] != post[j]:
                k += 1
            if k < len(pre) and k - i <= 64:
                i = k
            else:
                # mismatch -> insertion (shouldn't happen for fix_numeric)
                out.append(origins[min(i, len(origins) - 1)] if origins else (1, 1))
                j += 1
        else:
            out.append(origins[-1] if origins else (1, 1))
            j += 1
    return out


def _map_chars(pre: str, post: str, pre_origins: list[tuple[int, int]],
               char_line: list[int], char_col: list[int]) -> None:
    """Char-by-char origin alignment of the final statement text.

    `post` = final text (after numeric / hollerith / blank-run rewrites).
    Inserted chars (quote chars around Hollerith, `!` of comments) take the
    origin of the nearest preceding aligned char; removed chars drop out.
    """
    i = j = 0
    last = pre_origins[0] if pre_origins else (1, 1)
    while j < len(post):
        if i < len(pre) and pre[i] == post[j]:
            origin = pre_origins[i] if i < len(pre_origins) else last
            char_line.append(origin[0])
            char_col.append(origin[1])
            last = origin
            i += 1
            j += 1
        elif i < len(pre):
            k = i
            while k < len(pre) and pre[k] != post[j]:
                k += 1
            if k < len(pre) and k - i <= 64:
                i = k
            else:
                char_line.append(last[0])
                char_col.append(last[1])
                j += 1
        else:
            char_line.append(last[0])
            char_col.append(last[1])
            j += 1
