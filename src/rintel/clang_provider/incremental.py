"""Atomic provider-local TU cache and dependency-aware invalidation.

This module owns no canonical state. It only decides which compiler inputs
must be observed again and whether their provider-semantic fact set changed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

from .compile_context import load_translation_unit
from .extractor import extract_facts, extract_include_facts
from .frontend import ClangFrontend, dependency_paths
from .model import ClangIdentity, digest


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_fact(fact: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(fact, sort_keys=True))
    value.pop("provider_fact_id", None)
    value.pop("source_span", None)
    obj = value.get("object", {})
    literal = obj.get("literal") if isinstance(obj, dict) else None
    if isinstance(literal, dict):
        literal.pop("tu_identity", None)
        if value.get("kind") == "MacroExpansion":
            literal.pop("spelling_location", None)
            literal.pop("expansion_location", None)
    return value


def semantic_fact_digest(facts: Iterable[Mapping[str, Any]]) -> str:
    normalized = sorted(
        (_semantic_fact(fact) for fact in facts),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    return digest(normalized)


@dataclass(frozen=True)
class IncrementalRun:
    tus_reparsed: tuple[str, ...]
    dependency_invalidated: tuple[str, ...]
    identity_invalidated: tuple[str, ...]
    semantic_changed: tuple[str, ...]
    semantic_reused: tuple[str, ...]
    facts_changed: int
    facts_reused: int
    cutoff_reason: str
    result_identity: str
    facts: tuple[dict[str, Any], ...]
    dependency_manifest: dict[str, tuple[str, ...]]
    cache_state: dict[str, Any]


class IncrementalAnalyzer:
    def __init__(self, *, repo_root: str | Path,
                 compile_commands: Iterable[Mapping[str, Any]],
                 clang: ClangIdentity, cache_root: str | Path,
                 provider_version: str,
                 semantic_schema_version: str,
                 max_output_bytes: int = 768 * 1024 * 1024):
        self.repo_root = Path(repo_root).resolve()
        self.rows = tuple(dict(row) for row in compile_commands)
        self.clang = clang
        self.cache_root = Path(cache_root)
        self.provider_version = provider_version
        self.semantic_schema_version = semantic_schema_version
        self.max_output_bytes = max_output_bytes

    @property
    def state_path(self) -> Path:
        return self.cache_root / "current.json"

    def _relative(self, path: str | Path) -> str:
        return Path(path).resolve().relative_to(self.repo_root).as_posix()

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"translation_units": {}}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(
                value.get("translation_units"), dict):
            raise ValueError("provider cache state has unsupported schema")
        return value

    def _publish(self, value: Mapping[str, Any]) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":"))
                   + "\n").encode()
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".clang-provider-", suffix=".tmp", dir=self.cache_root)
        temp = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.state_path)
            directory_fd = os.open(self.cache_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temp.exists():
                temp.unlink()

    def publish(self, run: IncrementalRun) -> None:
        self._publish(run.cache_state)

    def run(self, *, publish: bool = True) -> IncrementalRun:
        previous = self._load()
        previous_units = previous["translation_units"]
        units = {
            self._relative(row["file"]): load_translation_unit(
                row, self.clang,
                provider_version=self.provider_version,
                semantic_schema_version=self.semantic_schema_version,
            )
            for row in self.rows
        }
        reparse: set[str] = set()
        dependency_invalidated: set[str] = set()
        identity_invalidated: set[str] = set()
        for relpath, unit in units.items():
            old = previous_units.get(relpath)
            if old is None:
                reparse.add(relpath)
                continue
            if old["tu_identity"] != unit.tu_identity:
                reparse.add(relpath)
                identity_invalidated.add(relpath)
                continue
            for dependency, old_digest in old.get("dependency_digests", {}).items():
                path = Path(dependency)
                current = _file_digest(path) if path.is_file() else None
                if current != old_digest:
                    reparse.add(relpath)
                    dependency_invalidated.add(relpath)
                    break

        next_units: dict[str, Any] = {}
        semantic_changed: list[str] = []
        semantic_reused: list[str] = []
        all_facts: list[dict[str, Any]] = []
        facts_changed = 0
        facts_reused = 0
        frontend = ClangFrontend(max_output_bytes=self.max_output_bytes)
        for relpath, unit in sorted(units.items()):
            old = previous_units.get(relpath)
            if relpath not in reparse and old is not None:
                entry = old
                facts = entry["facts"]
                semantic_reused.append(relpath)
                facts_reused += len(facts)
            else:
                result = frontend.analyze(unit)
                dependencies = dependency_paths(unit)
                facts = list(extract_facts(
                    result.ast, unit, repo_root=self.repo_root))
                facts.extend(extract_include_facts(
                    unit, dependencies, repo_root=self.repo_root))
                facts.sort(key=lambda item: item["provider_fact_id"])
                semantic = semantic_fact_digest(facts)
                if old is not None and old["semantic_digest"] == semantic:
                    semantic_reused.append(relpath)
                    facts_reused += len(facts)
                else:
                    semantic_changed.append(relpath)
                    facts_changed += len(facts)
                entry = {
                    "tu_identity": unit.tu_identity,
                    "source_digest": unit.source_digest,
                    "semantic_digest": semantic,
                    "dependency_digests": {
                        str(path): _file_digest(path)
                        for path in dependencies if path.is_file()
                    },
                    "dependencies": [str(path) for path in dependencies],
                    "facts": facts,
                }
            next_units[relpath] = entry
            all_facts.extend(facts)

        all_facts.sort(key=lambda item: item["provider_fact_id"])
        result_identity = digest({
            relpath: entry["semantic_digest"]
            for relpath, entry in sorted(next_units.items())
        })
        state = {
            "schema_version": "1.0",
            "provider_version": self.provider_version,
            "semantic_schema_version": self.semantic_schema_version,
            "clang_identity": self.clang.to_dict(),
            "result_identity": result_identity,
            "translation_units": next_units,
        }
        if not reparse:
            cutoff = "INPUT_IDENTITY_UNCHANGED"
        elif not semantic_changed:
            cutoff = "SEMANTIC_DIGEST_UNCHANGED"
        else:
            cutoff = "NONE"
        manifest = {
            relpath: tuple(
                self._relative(path) if Path(path).is_relative_to(self.repo_root)
                else str(path)
                for path in entry["dependencies"])
            for relpath, entry in next_units.items()
        }
        run = IncrementalRun(
            tus_reparsed=tuple(sorted(reparse)),
            dependency_invalidated=tuple(sorted(dependency_invalidated)),
            identity_invalidated=tuple(sorted(identity_invalidated)),
            semantic_changed=tuple(sorted(semantic_changed)),
            semantic_reused=tuple(sorted(semantic_reused)),
            facts_changed=facts_changed,
            facts_reused=facts_reused,
            cutoff_reason=cutoff,
            result_identity=result_identity,
            facts=tuple(all_facts),
            dependency_manifest=manifest,
            cache_state=state,
        )
        if publish:
            self.publish(run)
        return run


__all__ = [
    "IncrementalAnalyzer", "IncrementalRun", "semantic_fact_digest",
]
