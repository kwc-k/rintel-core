"""Strict compilation-database loading and Clang analysis argv derivation."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shlex
from typing import Any, Mapping

from .model import ClangIdentity, TranslationUnit, digest


class UnsupportedCompileCommand(ValueError):
    pass


_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx"})
_DROP_WITH_OPERAND = frozenset({"-o", "-MF", "-MT", "-MQ", "-MJ"})
_DROP_EXACT = frozenset({"-c", "-M", "-MM", "-MD", "-MMD", "-MP"})


def _arguments(row: Mapping[str, Any]) -> list[str]:
    if isinstance(row.get("arguments"), list):
        values = row["arguments"]
        if all(isinstance(item, str) for item in values):
            return list(values)
    if isinstance(row.get("command"), str):
        return shlex.split(row["command"])
    raise UnsupportedCompileCommand("row requires string arguments or command")


def _analysis_arguments(argv: list[str], source: Path,
                        clang: ClangIdentity) -> tuple[str, ...]:
    if not argv:
        raise UnsupportedCompileCommand("empty compiler command")
    result = [clang.executable]
    index = 1
    while index < len(argv):
        token = argv[index]
        if token in _DROP_WITH_OPERAND:
            if index + 1 >= len(argv):
                raise UnsupportedCompileCommand(f"{token} requires an operand")
            index += 2
            continue
        if token in _DROP_EXACT:
            index += 1
            continue
        if Path(token).suffix.lower() in _SOURCE_SUFFIXES:
            index += 1
            continue
        if token.startswith(("-l", "-L", "-Wl,")):
            index += 1
            continue
        result.append(token)
        index += 1
    result.extend((
        "-fsyntax-only", "-Xclang", "-ast-dump=json",
        "-fdiagnostics-format=sarif", "-Wno-sarif-format-unstable",
        str(source),
    ))
    return tuple(result)


def _dimensions(argv: tuple[str, ...], directory: Path
                ) -> tuple[tuple[str, ...], tuple[str, ...], str, str, tuple[str, ...]]:
    defines: list[str] = []
    includes: list[str] = []
    standard = ""
    target = ""
    flags: list[str] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "-D" and index + 1 < len(argv):
            defines.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("-D") and len(token) > 2:
            defines.append(token[2:])
        elif token == "-I" and index + 1 < len(argv):
            includes.append(str((directory / argv[index + 1]).resolve()))
            index += 2
            continue
        elif token.startswith("-I") and len(token) > 2:
            includes.append(str((directory / token[2:]).resolve()))
        elif token == "-std" and index + 1 < len(argv):
            standard = argv[index + 1]
            index += 2
            continue
        elif token.startswith("-std="):
            standard = token.split("=", 1)[1]
        elif token in {"-target", "--target"} and index + 1 < len(argv):
            target = argv[index + 1]
            index += 2
            continue
        elif token.startswith("--target="):
            target = token.split("=", 1)[1]
        elif token.startswith("-") and token not in {
                "-fsyntax-only", "-Xclang", "-ast-dump=json",
                "-fdiagnostics-format=sarif", "-Wno-sarif-format-unstable"}:
            flags.append(token)
        index += 1
    return (tuple(defines), tuple(includes), standard, target, tuple(flags))


def load_translation_unit(
        row: Mapping[str, Any], clang: ClangIdentity, *,
        provider_version: str = "provider-clang0",
        semantic_schema_version: str = "clang-ast-v1") -> TranslationUnit:
    directory = Path(str(row.get("directory", ""))).resolve()
    source = Path(str(row.get("file", "")))
    if not source.is_absolute():
        source = directory / source
    source = source.resolve()
    if not source.is_file():
        raise UnsupportedCompileCommand(f"source does not exist: {source}")
    argv = _arguments(row)
    analysis = _analysis_arguments(argv, source, clang)
    defines, includes, standard, target, flags = _dimensions(analysis, directory)
    source_digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    raw_payload = {"directory": str(directory), "arguments": argv,
                   "source": str(source)}
    raw_digest = digest(raw_payload)
    semantic_payload = {
        "build_compiler": argv[0],
        "directory": str(directory),
        "analysis_arguments": list(analysis),
        "language": source.suffix.lower(),
        "defines": list(defines),
        "include_paths": list(includes),
        "language_standard": standard,
        "target": target,
        "relevant_flags": list(flags),
    }
    semantic_digest = digest(semantic_payload)
    identity = digest({
        "raw_command_digest": raw_digest,
        "semantic_command_digest": semantic_digest,
        "source_digest": source_digest,
        "clang": clang.to_dict(),
        "provider_version": provider_version,
        "semantic_schema_version": semantic_schema_version,
    })
    return TranslationUnit(
        source=str(source), directory=str(directory), build_compiler=argv[0],
        raw_arguments=tuple(argv), analysis_arguments=analysis,
        source_digest=source_digest, raw_command_digest=raw_digest,
        semantic_command_digest=semantic_digest, tu_identity=identity,
        language="c++" if source.suffix.lower() != ".c" else "c",
        defines=defines, include_paths=includes, language_standard=standard,
        target=target, relevant_flags=flags, clang=clang,
    )


__all__ = ["UnsupportedCompileCommand", "load_translation_unit"]
