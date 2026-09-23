"""Immutable RPP v1 models owned by the Rintel Host."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from rintel.provider_arch import ProviderKind

PROTOCOL_VERSION = "rpp/1"


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class PublicationPolicy(str, Enum):
    REQUIRE_COMPLETE = "REQUIRE_COMPLETE"
    STAGE_ONLY = "STAGE_ONLY"


@dataclass(frozen=True)
class BuildContext:
    defines: tuple[str, ...] = ()
    include_paths: tuple[str, ...] = ()
    compiler_flags: tuple[str, ...] = ()
    language_standard: str = ""
    target: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "defines": list(self.defines),
            "include_paths": list(self.include_paths),
            "compiler_flags": list(self.compiler_flags),
            "language_standard": self.language_standard,
            "target": self.target,
        }


@dataclass(frozen=True)
class IncrementalCapability:
    modes: tuple[str, ...] = ()
    accepts_changed_scope: bool = False
    reports_invalidated_scope: bool = False
    reports_dependency_manifest: bool = False
    reports_semantic_digest: bool = False
    reports_reuse_source: bool = False

    def to_wire(self) -> dict[str, Any]:
        return {
            "modes": list(self.modes),
            "accepts_changed_scope": self.accepts_changed_scope,
            "reports_invalidated_scope": self.reports_invalidated_scope,
            "reports_dependency_manifest": self.reports_dependency_manifest,
            "reports_semantic_digest": self.reports_semantic_digest,
            "reports_reuse_source": self.reports_reuse_source,
        }


@dataclass(frozen=True)
class ProviderAdvertisement:
    provider_id: str
    provider_version: str
    provider_kind: ProviderKind
    provider_config_digest: str
    languages: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    incremental: IncrementalCapability = field(default_factory=IncrementalCapability)

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.provider_version.strip():
            raise ValueError("provider id and version are required")
        if not self.provider_config_digest.strip():
            raise ValueError("provider_config_digest is required")

    def to_wire(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "provider_kind": self.provider_kind.value,
            "provider_config_digest": self.provider_config_digest,
            "languages": list(self.languages),
            "capabilities": list(self.capabilities),
            "incremental_capability": self.incremental.to_wire(),
        }


@dataclass(frozen=True)
class AnalysisContract:
    analysis_id: str
    repo_id: str
    repo_snapshot: str
    root: str
    provider_config: Mapping[str, Any]
    build_context_id: str
    build_context: BuildContext
    input_content_digest: str
    semantic_digest: str | None
    semantic_identity_schema_version: str
    changed_scope: tuple[str, ...]
    invalidated_scope: tuple[str, ...]
    dependency_manifest: Mapping[str, tuple[str, ...]]
    dependency_manifest_digest: str
    projection_identity: str
    projection_version: str
    publication_policy: PublicationPolicy = PublicationPolicy.REQUIRE_COMPLETE

    def __post_init__(self) -> None:
        required = {
            "analysis_id": self.analysis_id,
            "repo_id": self.repo_id,
            "repo_snapshot": self.repo_snapshot,
            "root": self.root,
            "build_context_id": self.build_context_id,
            "input_content_digest": self.input_content_digest,
            "semantic_identity_schema_version": self.semantic_identity_schema_version,
            "dependency_manifest_digest": self.dependency_manifest_digest,
            "projection_identity": self.projection_identity,
            "projection_version": self.projection_version,
        }
        missing = [key for key, value in required.items() if not value.strip()]
        if missing:
            raise ValueError("required analysis contract fields missing: " + ", ".join(missing))

    def cache_identity(self, provider: ProviderAdvertisement) -> str:
        return _digest({
            "protocol_version": PROTOCOL_VERSION,
            "repo_snapshot": self.repo_snapshot,
            "provider_id": provider.provider_id,
            "provider_version": provider.provider_version,
            "provider_kind": provider.provider_kind.value,
            "provider_config": dict(self.provider_config),
            "provider_config_digest": provider.provider_config_digest,
            "build_context_id": self.build_context_id,
            "build_context": self.build_context.to_wire(),
            "input_content_digest": self.input_content_digest,
            "semantic_digest": self.semantic_digest,
            "semantic_identity_schema_version": self.semantic_identity_schema_version,
            "changed_scope": list(self.changed_scope),
            "invalidated_scope": list(self.invalidated_scope),
            "dependency_manifest": {
                source: list(dependencies)
                for source, dependencies in self.dependency_manifest.items()
            },
            "dependency_manifest_digest": self.dependency_manifest_digest,
            "incremental_capability": provider.incremental.to_wire(),
            "projection_identity": self.projection_identity,
            "projection_version": self.projection_version,
            "publication_policy": self.publication_policy.value,
        })

    def to_wire(self, provider: ProviderAdvertisement) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "repo_id": self.repo_id,
            "repo_snapshot": self.repo_snapshot,
            "root": self.root,
            "provider_id": provider.provider_id,
            "provider_version": provider.provider_version,
            "provider_config": dict(self.provider_config),
            "provider_config_digest": provider.provider_config_digest,
            "build_context_id": self.build_context_id,
            "build_context": self.build_context.to_wire(),
            "input_content_digest": self.input_content_digest,
            "semantic_digest": self.semantic_digest,
            "semantic_identity_schema_version": self.semantic_identity_schema_version,
            "changed_scope": list(self.changed_scope),
            "invalidated_scope": list(self.invalidated_scope),
            "dependency_manifest": {
                source: list(dependencies)
                for source, dependencies in self.dependency_manifest.items()
            },
            "dependency_manifest_digest": self.dependency_manifest_digest,
            "incremental_capability": provider.incremental.to_wire(),
            "cache_identity": self.cache_identity(provider),
            "cutoff_contract": {
                "reason_required": True,
                "reuse_source_required_when_reused": True,
            },
            "projection_identity": self.projection_identity,
            "projection_version": self.projection_version,
            "publication_policy": self.publication_policy.value,
        }


__all__ = [
    "PROTOCOL_VERSION", "AnalysisContract", "BuildContext",
    "IncrementalCapability", "ProviderAdvertisement", "PublicationPolicy",
]
