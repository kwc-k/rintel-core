"""Tree-sitter language loading with API-version shims."""
from __future__ import annotations

import importlib

import tree_sitter
import tree_sitter_c
import tree_sitter_cpp
import tree_sitter_fortran
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Parser

_parsers: dict[str, Parser] = {}


def _language(fn):
    """Return a tree_sitter.Language from a grammar module's language fn,
    tolerant of the 0.22 / 0.23+ binding API differences."""
    try:
        return tree_sitter.Language(fn())
    except TypeError:
        return tree_sitter.Language(fn)  # ctypes library object (0.22)


def _make_parser(lang) -> Parser:
    try:
        return Parser(lang)  # 0.23+
    except TypeError:
        p = Parser()
        p.language = lang  # 0.22
        return p


def grammar_version(name: str) -> str:
    mod = importlib.import_module(f"tree_sitter_{name}")
    return getattr(mod, "__version__", "unknown")


def parser_for(language: str) -> Parser:
    if language in _parsers:
        return _parsers[language]
    if language == "python":
        lang = _language(tree_sitter_python.language)
    elif language == "fortran":
        lang = _language(tree_sitter_fortran.language)
    elif language == "c":
        lang = _language(tree_sitter_c.language)
    elif language == "cpp":
        lang = _language(tree_sitter_cpp.language)
    elif language == "go":
        lang = _language(tree_sitter_go.language)
    elif language == "java":
        lang = _language(tree_sitter_java.language)
    elif language == "typescript":
        lang = _language(tree_sitter_typescript.language_typescript)
    elif language == "javascript":
        lang = _language(tree_sitter_javascript.language)
    else:
        raise KeyError(f"no grammar registered for {language!r}")
    _parsers[language] = _make_parser(lang)
    return _parsers[language]
