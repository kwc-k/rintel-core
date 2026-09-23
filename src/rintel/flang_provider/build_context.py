"""Real FAC GFortran context translated into an honest Flang probe context."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable, Mapping

from .model import CompilerIdentity, FortranTranslationUnit, digest


class UnsupportedBuildContext(RuntimeError):
    """A production build detail has no verified Flang-probe equivalent."""


@dataclass(frozen=True)
class ModuleDependency:
    source: str
    provides: tuple[str, ...]
    uses: tuple[str, ...]


_INTRINSIC_MODULES = frozenset({
    "iso_c_binding", "iso_fortran_env", "ieee_arithmetic",
    "ieee_exceptions", "ieee_features",
})
_SOURCE_SUFFIXES = frozenset({".f", ".for", ".f90", ".f95", ".f03", ".f08"})


def _content_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _absolute_operand(value: str, directory: Path) -> str:
    path = Path(value)
    return str((directory / path).resolve()) if not path.is_absolute() else str(path.resolve())


def _translate_arguments(arguments: tuple[str, ...], *, directory: Path,
                         source: Path, frontend: CompilerIdentity) -> tuple[
                             tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    translated = [frontend.executable]
    includes: list[str] = []
    modules: list[str] = []
    definitions: list[str] = []
    index = 1
    while index < len(arguments):
        token = arguments[index]
        if token == "-c" or token == str(source) or token == source.name:
            index += 1
            continue
        if token in {"-I", "-J", "-D"}:
            if index + 1 >= len(arguments):
                raise UnsupportedBuildContext(f"{token} is missing its operand")
            operand = arguments[index + 1]
            if token == "-I":
                absolute = _absolute_operand(operand, directory)
                includes.append(absolute)
                translated.extend((token, absolute))
            elif token == "-J":
                absolute = _absolute_operand(operand, directory)
                modules.append(absolute)
                translated.extend((token, absolute))
            else:
                definitions.append(operand)
                translated.extend((token, operand))
            index += 2
            continue
        if token.startswith("-I") and len(token) > 2:
            absolute = _absolute_operand(token[2:], directory)
            includes.append(absolute)
            translated.append("-I" + absolute)
        elif token.startswith("-J") and len(token) > 2:
            absolute = _absolute_operand(token[2:], directory)
            modules.append(absolute)
            translated.append("-J" + absolute)
        elif token.startswith("-D") and len(token) > 2:
            definitions.append(token[2:])
            translated.append(token)
        elif token == "-fPIC" or re.fullmatch(r"-O(?:[0-3sg]|fast)", token):
            translated.append(token)
        elif token in {"-ffixed-form", "-ffree-form", "-cpp"} \
                or token.startswith(("-std=", "-march=", "-mcpu=", "--target=")):
            translated.append(token)
        elif token.startswith("-f"):
            raise UnsupportedBuildContext(
                f"unmapped GFortran semantic flag: {token}")
        elif Path(token).suffix.lower() in _SOURCE_SUFFIXES:
            index += 1
            continue
        else:
            raise UnsupportedBuildContext(f"unsupported build argument: {token}")
        index += 1
    translated.append(str(source))
    return tuple(translated), tuple(includes), tuple(modules), tuple(definitions)


def load_translation_unit(row: Mapping[str, object], *,
                          production: CompilerIdentity,
                          semantic_frontend: CompilerIdentity,
                          provider_version: str,
                          semantic_schema_version: str) -> FortranTranslationUnit:
    directory = Path(str(row["directory"])).resolve()
    source_value = Path(str(row["file"]))
    source = (directory / source_value).resolve() if not source_value.is_absolute() \
        else source_value.resolve()
    raw = tuple(str(item) for item in row["arguments"])
    semantic, includes, modules, definitions = _translate_arguments(
        raw, directory=directory, source=source,
        frontend=semantic_frontend,
    )
    language_mode = "fixed" if source.suffix.lower() in {".f", ".for"} else "free"
    source_digest = _content_digest(source)
    build_payload = {
        "directory": str(directory), "raw_arguments": raw,
        "production_compiler": production.to_dict(),
    }
    build_context_id = digest(build_payload)
    cache_identity = digest({
        "build_context_id": build_context_id,
        "semantic_arguments": semantic,
        "semantic_frontend": semantic_frontend.to_dict(),
        "source_digest": source_digest,
        "provider_version": provider_version,
        "semantic_schema_version": semantic_schema_version,
    })
    return FortranTranslationUnit(
        source=str(source), directory=str(directory),
        production_compiler=production,
        semantic_frontend=semantic_frontend,
        raw_arguments=raw, semantic_arguments=semantic,
        include_paths=includes, module_paths=modules,
        definitions=definitions, language_mode=language_mode,
        target=semantic_frontend.target, source_digest=source_digest,
        build_context_id=build_context_id, cache_identity=cache_identity,
    )


def topological_module_order(
        dependencies: Iterable[ModuleDependency], *,
        external_modules: frozenset[str] = _INTRINSIC_MODULES) -> tuple[str, ...]:
    rows = tuple(dependencies)
    provider: dict[str, str] = {}
    for row in rows:
        for module in row.provides:
            key = module.casefold()
            if key in provider and provider[key] != row.source:
                raise UnsupportedBuildContext(f"duplicate module producer: {module}")
            provider[key] = row.source
    prerequisites: dict[str, set[str]] = {row.source: set() for row in rows}
    for row in rows:
        for used in row.uses:
            key = used.casefold()
            if key in external_modules:
                continue
            producer = provider.get(key)
            if producer is None:
                raise UnsupportedBuildContext(
                    f"missing module producer for {used} used by {row.source}")
            if producer != row.source:
                prerequisites[row.source].add(producer)
    ordered: list[str] = []
    remaining = set(prerequisites)
    while remaining:
        ready = sorted(
            source for source in remaining
            if not prerequisites[source] & remaining)
        if not ready:
            raise UnsupportedBuildContext("module dependency cycle")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return tuple(ordered)


def make_work_layout(cache_root: Path, analysis_id: str, *,
                     fac_root: Path) -> dict[str, Path]:
    cache = cache_root.resolve()
    fac = fac_root.resolve()
    base = (cache / "analyses" / analysis_id).resolve()
    if base.is_relative_to(fac):
        raise UnsupportedBuildContext("provider work root overlaps FAC source tree")
    layout = {name: base / name for name in ("modules", "fir", "objects", "tmp")}
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=False)
    return layout


__all__ = [
    "ModuleDependency", "UnsupportedBuildContext", "load_translation_unit",
    "make_work_layout", "topological_module_order",
]
