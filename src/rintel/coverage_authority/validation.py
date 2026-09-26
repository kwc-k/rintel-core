"""Exact query containment and live-input freshness for negative claims."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from rintel.clang_provider.compile_context import load_translation_unit
from rintel.clang_provider.frontend import FrontendError, detect_clang_identity
from rintel.clang_provider.process import (
    PROVIDER_CONFIG_DIGEST, PROVIDER_VERSION,
)
from rintel.evidence_authority import DEFAULT_ENGINE

from .model import (CAPABILITY_REGISTRY, CAPABILITY_REGISTRY_VERSION,
                    ISSUER_ID, RULE_VERSION, digest)


def exact_compile_row(path: Path, source: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("compile_commands must be an array")
    matches: list[Mapping[str, Any]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError("compile_commands row must be an object")
        directory = Path(str(row.get("directory", "")))
        if not directory.is_absolute():
            directory = (path.parent / directory).resolve()
        candidate = Path(str(row.get("file", "")))
        if not candidate.is_absolute():
            candidate = directory / candidate
        if candidate.resolve() == source.resolve():
            matches.append({**row, "directory": str(directory)})
    if len(matches) != 1:
        raise ValueError("exact TU requires one and only one compile command")
    return matches[0]


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def applicable_complete(
        certificate: Mapping[str, Any], *, repo_id: str, revision: str,
        expected: Mapping[str, Any], store: Any) -> tuple[bool, str]:
    """A positive fact never calls this. Failure preserves UNKNOWN absence."""
    cert = dict(certificate)
    if expected.get("kind") != "CALLS" or expected.get(
            "call_semantics") != "DIRECT_STATIC_CALL":
        return False, "query_not_explicitly_direct_static"
    if cert.get("schema_version") != "coverage-certificate/1" or cert.get(
            "issuer") != ISSUER_ID or cert.get("completeness") != "COMPLETE":
        return False, "not_production_complete_certificate"
    if not any(row.get("analyzer") == cert.get("analyzer")
               and row.get("relation") == cert.get("relation_kind")
               and row.get("scope") == cert.get("subject_scope")
               and row.get("language") == cert.get("language")
               and row.get("maximum") == "COMPLETE"
               for row in CAPABILITY_REGISTRY):
        return False, "issuer_capability_not_complete"
    if (cert.get("repo_id") != repo_id or
            cert.get("canonical_revision") != revision or
            cert.get("relation_kind") != "DIRECT_STATIC_CALL" or
            cert.get("subject") != expected.get("source")):
        return False, "query_outside_certificate_domain"
    if (expected.get("translation_unit") is not None and
            expected.get("translation_unit") != cert.get("translation_unit")):
        return False, "query_translation_unit_mismatch"
    if (expected.get("build_context") is not None and
            expected.get("build_context") != cert.get("build_context")):
        return False, "query_build_context_mismatch"
    domain = cert.get("included_domain")
    if not isinstance(domain, dict) or domain != {
            "scope_type": "EXACT_TU_FUNCTION_BODY",
            "translation_unit": cert.get("translation_unit"),
            "subject": cert.get("subject"),
            "relation": "DIRECT_STATIC_CALL"}:
        return False, "domain_identity_mismatch"
    if cert.get("unsupported_constructs") or cert.get("parse_status") != "COMPLETE" \
            or cert.get("analysis_status") != "COMPLETE":
        return False, "analysis_not_exhaustive"
    if (cert.get("rule_version") != RULE_VERSION or
            cert.get("registry_version") != DEFAULT_ENGINE.registry.version or
            cert.get("capability_registry_version") != CAPABILITY_REGISTRY_VERSION or
            cert.get("provider_version") != PROVIDER_VERSION or
            cert.get("provider_config_digest") != PROVIDER_CONFIG_DIGEST):
        return False, "issuer_or_authority_version_changed"
    identity = cert.get("receipt_identity")
    payload = {key: value for key, value in cert.items()
               if key not in {"id", "receipt_identity"}}
    if identity != digest(payload) or cert.get("id") != (
            "coverage-" + str(identity).split(":", 1)[-1][:32]):
        return False, "certificate_receipt_identity_mismatch"
    snap = store.snapshot(revision)
    if not snap or snap.get("repo_id") != repo_id or snap.get(
            "publication_status") != "published":
        return False, "canonical_revision_unavailable"
    repo = store.repo(repo_id)
    if not repo:
        return False, "repository_unavailable"
    root = Path(repo["root_path"]).resolve()
    tu = Path(str(cert.get("translation_unit") or ""))
    if tu.is_absolute() or ".." in tu.parts:
        return False, "translation_unit_outside_repository"
    source = (root / tu).resolve()
    try:
        source.relative_to(root)
        if not source.is_file() or _sha(source) != cert.get("source_digest"):
            return False, "source_digest_changed"
        dependencies = cert.get("dependency_digests")
        if not isinstance(dependencies, dict):
            return False, "dependency_manifest_malformed"
        for raw_path, old_digest in dependencies.items():
            path = Path(raw_path)
            if not path.is_file() or _sha(path) != old_digest:
                return False, "dependency_digest_changed"
        clang = detect_clang_identity(str(cert["analyzer_executable"]))
        if clang.to_dict() != cert.get("analyzer_identity"):
            return False, "analyzer_binary_or_version_changed"
        row = exact_compile_row(Path(cert["compile_commands_path"]), source)
        unit = load_translation_unit(row, clang, provider_version=PROVIDER_VERSION,
                                     semantic_schema_version="clang-ast-v1")
        if (unit.source_digest != cert.get("source_digest") or
                unit.raw_command_digest != cert.get("compile_command_digest") or
                unit.semantic_command_digest != cert.get("build_context") or
                unit.tu_identity != cert.get("tu_identity")):
            return False, "build_context_or_compile_command_changed"
    except (OSError, ValueError, KeyError, TypeError, FrontendError,
            json.JSONDecodeError):
        return False, "current_input_identity_unavailable"
    subject = store.node_by_id(repo_id, revision, str(expected.get("source")))
    if not subject or subject.get("path") != tu.as_posix() or subject.get(
            "kind") != "FUNCTION":
        return False, "subject_not_in_exact_tu"
    supports = store.support_receipts(repo_id, revision, subject["id"])
    if not any(item.get("support_receipt_id") in cert.get("support_receipt_ids", [])
               and item.get("lane_id") == "clang_provider"
               and item.get("provenance", {}).get("analysis_id") == cert.get(
                   "analyzer_run_id") for item in supports):
        return False, "analyzer_run_lacks_canonical_support"
    return True, "bounded_direct_static_domain_complete"


__all__ = ["applicable_complete", "exact_compile_row"]
