"""SOFTWARE-LVS1 code-side extraction helpers.

Signatures come from the REAL source at the canonical node's file/line
span — never from AnalysisFact mutation.  Span lookup is line-based and
deterministic (the tree-sitter node offsets in this environment are
span-relative, so file-level name matching is done by line scanning).
Parse failure yields an empty signature and port comparators answer
UNKNOWN (L3).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .code_side import LvsCodeSide, LvsDesign, resolve_code, resolve_design


def signature_from_span(lines: list[str], start_line: int | None,
                        end_line: int | None, language: str) -> dict:
    """Signature of a definition span via the FLOW0 extractor (span text)."""
    from ..flow.signature import extract_signature
    if not lines or not start_line:
        return {"params": [], "returns": True, "return_type": None,
                "parse_failed": True}
    end = end_line or start_line + 1
    if start_line < 1:
        return {"params": [], "returns": True, "return_type": None,
                "parse_failed": True}
    span = "".join(lines[start_line - 1:end])
    try:
        sig = extract_signature(language, span, "function")
        return {
            "params": [{"name": p.get("name"), "type": p.get("type"),
                        "has_type": bool(p.get("has_type"))}
                       for p in sig.params],
            "returns": sig.returns,
            "return_type": sig.return_type,
            "parse_failed": sig.parse_failed,
        }
    except Exception:
        return {"params": [], "returns": True, "return_type": None,
                "parse_failed": True}


def signature_of_name(text: str | None, name: str, language: str) -> dict:
    """Fallback: locate `name` by line scanning (python/c only)."""
    if not text:
        return {"params": [], "returns": True, "return_type": None,
                "parse_failed": True}
    lines = text.splitlines(keepends=True)
    if language == "python":
        idx = _find_py_def(lines, name)
        if idx is None:
            return {"params": [], "returns": True, "return_type": None,
                    "parse_failed": True}
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        end = idx + 1
        while end < len(lines):
            line = lines[end]
            if line.strip() and not line.startswith(" " * (indent + 1)):
                break
            end += 1
        return signature_from_span(lines, idx + 1, end, language)
    if language in ("c", "cpp", "go", "java", "typescript", "javascript"):
        idx = _find_c_def(lines, name)
        if idx is None:
            return {"params": [], "returns": True, "return_type": None,
                    "parse_failed": True}
        # brace balance from the line containing the opening paren
        start_idx = idx
        depth = 0
        end = start_idx
        started = False
        for i in range(start_idx, len(lines)):
            for ch in lines[i]:
                if ch == "(":
                    started = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if started and depth <= 0:
                        end = i + 1
                        break
            if end == i + 1 and depth <= 0 and started:
                break
        return signature_from_span(lines, start_idx + 1, end, language)
    return {"params": [], "returns": True, "return_type": None,
            "parse_failed": True}


def _find_py_def(lines: list[str], name: str) -> int | None:
    pat = re.compile(rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(")
    for i, line in enumerate(lines):
        if pat.match(line):
            return i
    return None


def _find_c_def(lines: list[str], name: str) -> int | None:
    pat = re.compile(rf"^\s*\w[\w\s\*]*\b{re.escape(name)}\s*\(")
    for i, line in enumerate(lines):
        if pat.match(line):
            return i
    return None


def build_code_side_from_rows(rows: list[dict], edges: list[dict],
                              root: str | None,
                              snapshot_id: str | None) -> LvsCodeSide:
    """Canonical index rows + real source signatures."""
    funcs: dict[str, dict] = {}
    cache: dict[str, list[str]] = {}
    for row in rows:
        cid = row["id"]
        path = row.get("path", "")
        language = row.get("language", "python")
        lines = cache.get(path)
        if lines is None and root:
            full = Path(root) / _resolve(root, path)
            try:
                src = full.read_text() if full.is_file() else ""
            except OSError:
                src = ""
            lines = src.splitlines(keepends=True)
            cache[path] = lines
        sig = signature_from_span(lines or [], row.get("start_line"),
                                  row.get("end_line"), language)
        if sig["parse_failed"] and lines:
            sig2 = signature_of_name("".join(lines) if lines else None,
                                     row.get("name", ""), language)
            if not sig2["parse_failed"]:
                sig = sig2
        funcs[cid] = {
            "name": row.get("name", ""),
            "file": path,
            "language": language,
            "signature": sig,
            "span": {"start_line": row.get("start_line"),
                     "end_line": row.get("end_line")},
            "source_location": {"file": path,
                                "line": row.get("start_line")},
        }
    side = LvsCodeSide(snapshot_id=snapshot_id, functions=funcs,
                       edges=sorted(edges, key=lambda e: (
                           str(e.get("source", "")), str(e.get("target", "")))))
    side.resources = sorted({e["target"] for e in side.edges
                             if e.get("kind") == "RESOURCE"})
    return side


def _resolve(root: str, path: str) -> str:
    try:
        from ..topology_view.projector import resolve_repo_path
        rel = resolve_repo_path(root, path)
        return rel or path
    except Exception:
        return path
