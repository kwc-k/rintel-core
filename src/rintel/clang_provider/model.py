"""Immutable identities used by the external Clang Provider."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


def digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ClangIdentity:
    executable: str
    version: str
    target: str
    binary_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TranslationUnit:
    source: str
    directory: str
    build_compiler: str
    raw_arguments: tuple[str, ...]
    analysis_arguments: tuple[str, ...]
    source_digest: str
    raw_command_digest: str
    semantic_command_digest: str
    tu_identity: str
    language: str
    defines: tuple[str, ...]
    include_paths: tuple[str, ...]
    language_standard: str
    target: str
    relevant_flags: tuple[str, ...]
    clang: ClangIdentity


__all__ = ["ClangIdentity", "TranslationUnit", "digest"]
