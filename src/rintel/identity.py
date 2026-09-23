"""Canonical identity construction (spec §4, §7): qname derivation rules."""
from __future__ import annotations


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
