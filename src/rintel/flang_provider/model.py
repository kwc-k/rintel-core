"""Immutable identities for the pinned Flang semantic probe."""
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
class CompilerIdentity:
    role: str
    executable: str
    version: str
    target: str
    binary_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FortranTranslationUnit:
    source: str
    directory: str
    production_compiler: CompilerIdentity
    semantic_frontend: CompilerIdentity
    raw_arguments: tuple[str, ...]
    semantic_arguments: tuple[str, ...]
    include_paths: tuple[str, ...]
    module_paths: tuple[str, ...]
    definitions: tuple[str, ...]
    language_mode: str
    target: str
    source_digest: str
    build_context_id: str
    cache_identity: str


__all__ = ["CompilerIdentity", "FortranTranslationUnit", "digest"]
