"""Canonical identity construction and language-specific qname derivation."""
from __future__ import annotations

import hashlib
import json
from typing import Any


SYMBOL_IDENTITY_SCHEMA_VERSION = "symbol-identity/v2"
LEGACY_IDENTITY_SCHEMA_VERSION = "symbol-identity/v1"
_SYNTHETIC_KINDS = frozenset({"REPOSITORY", "DIRECTORY", "FILE", "COMMIT"})


def symbol_identity_descriptor(*, kind: str, qname: str, language: str,
                               path: str, meta: dict[str, Any]) -> dict[str, str]:
    """Rintel-owned stable symbol identity, independent of snapshot and spans.

    Qualified name is still retained for search, not treated as a sufficient
    identity.  Source path participates only for file-local/internal linkage;
    external declarations and definitions may therefore reconcile across
    files.  A semantic signature discriminator is used only when an adapter
    has supplied an explicit stable one, never from presentation text.
    """
    if not kind or not qname or not language:
        raise ValueError("symbol identity requires kind, qname and language")
    linkage = str(meta.get("linkage") or
                  ("INTERNAL" if meta.get("static") else "EXTERNAL"))
    local = (linkage.upper() in {"INTERNAL", "LOCAL", "PRIVATE"}
             or meta.get("definition_source_scoped") is True)
    result = {
        "identity_schema_version": SYMBOL_IDENTITY_SCHEMA_VERSION,
        "language": language.lower(),
        "kind": kind,
        "qualified_name": qname,
        "linkage": linkage.upper(),
    }
    if local:
        if not path:
            raise ValueError("local symbol identity requires source path")
        result["definition_source"] = path.replace("\\", "/")
        if meta.get("definition_source_scoped") is True:
            result["source_scope_reason"] = "top_level_definition"
    scope = meta.get("scope_identity")
    if scope:
        result["scope_identity"] = str(scope)
    signature = meta.get("semantic_signature")
    if signature:
        result["semantic_signature"] = str(signature)
    return result


def canonical_node_id(*, kind: str, qname: str, language: str,
                      path: str, meta: dict[str, Any]) -> str:
    if kind in _SYNTHETIC_KINDS:
        return f"node:{kind}:{qname}"
    descriptor = symbol_identity_descriptor(
        kind=kind, qname=qname, language=language, path=path, meta=meta)
    encoded = json.dumps(descriptor, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"node:{kind}:v2:{digest}"


def canonical_provider_entity_id(entity: dict[str, Any]) -> str:
    """Resolve an rpp/1 local descriptor under Rintel's v2 identity rules.

    The wire entity deliberately has no canonical-id field.  Unsupported
    compiler-semantic distinctions fail closed rather than taking a bare name.
    """
    kind = str(entity["kind"]).upper()
    name = str(entity["name"])
    qname = str(entity["qualified_name"])
    path = str(entity["path"])
    language = str(entity["language"]).lower()
    if language in {"fortran77", "fortran90", "fortran95", "f77", "f90", "f95"}:
        language = "fortran"
    if language in {"c++", "cpp", "cxx"} and kind in {
            "FUNCTION", "METHOD", "PROCEDURE"}:
        raise ValueError(
            "compiler-semantic C++ overload identity is outside current admission scope")
    meta: dict[str, Any] = {}
    if language == "c" and qname == f"{path}::{name}":
        meta["static"] = True
    if language == "c" and kind == "FUNCTION" and name == "main":
        meta["definition_source_scoped"] = True
    if language == "fortran" and kind in {
            "FUNCTION", "SUBROUTINE", "PROCEDURE", "PROGRAM"} \
            and qname.lower() == name.lower():
        meta["definition_source_scoped"] = True
    if kind in {"CONTEXT", "EXPRESSION", "OPERATION"}:
        meta["definition_source_scoped"] = True
    return canonical_node_id(kind=kind, qname=qname, language=language,
                             path=path, meta=meta)


def identity_schema_version(kind: str) -> str:
    return (LEGACY_IDENTITY_SCHEMA_VERSION if kind in _SYNTHETIC_KINDS
            else SYMBOL_IDENTITY_SCHEMA_VERSION)


def py_module_qname(relpath: str) -> str:
    """Python module qname from repo-relative path.

    `src/checkout/payments.py` -> `checkout.payments`
    `checkout/__init__.py`     -> `checkout`
    A leading `src/` (conventional source root) is stripped; otherwise the
    full path relative to the repository root is used, deterministically.
    """
    p = relpath.replace("\\", "/")
    if p.endswith(".py"):
        p = p[:-3]
    parts = [x for x in p.split("/") if x]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "module"


def ts_module_qname(relpath: str) -> str:
    """TS/JS module qname from repo-relative path (same convention)."""
    p = relpath.replace("\\", "/")
    for ext in (".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs"):
        if p.endswith(ext):
            p = p[: -len(ext)]
            break
    parts = [x for x in p.split("/") if x]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(parts) if parts else "module"


def join_qname(*parts: str) -> str:
    return ".".join(p for p in parts if p)


def c_global_name(name: str) -> str:
    """C symbol identity: non-static symbols are global by name."""
    return name


def go_qname(package: str, name: str) -> str:
    return f"{package}.{name}"


def java_qname(package: str, cls: str) -> str:
    return f"{package}.{cls}" if package else cls
