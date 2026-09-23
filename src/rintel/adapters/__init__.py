"""Adapter registry (spec §2 REQUIRED LANGUAGES)."""
from __future__ import annotations

from .base import LanguageAdapter
from .c_adapter import CAdapter, CppAdapter
from .fortran_adapter import FortranAdapter
from .go_adapter import GoAdapter
from .java_adapter import JavaAdapter
from .python_adapter import PythonAdapter
from .ts_adapter import JavaScriptAdapter, TypeScriptAdapter

ADAPTERS: list[LanguageAdapter] = [
    PythonAdapter(),
    TypeScriptAdapter(),
    JavaScriptAdapter(),
    GoAdapter(),
    JavaAdapter(),
    CAdapter(),
    CppAdapter(),
    FortranAdapter(),
]

_BY_LANGUAGE = {a.language: a for a in ADAPTERS}


def adapter_for_language(language: str) -> LanguageAdapter:
    return _BY_LANGUAGE[language]


def adapter_for_path(relpath: str) -> LanguageAdapter | None:
    for a in ADAPTERS:
        if a.can_parse(relpath):
            return a
    return None


__all__ = ["ADAPTERS", "adapter_for_language", "adapter_for_path",
           "LanguageAdapter"]
