"""Production Clang issuer for one exact C translation-unit function body.

The external RPP provider supplies positive candidates. A separate, strict
Clang AST audit proves the bounded empty slot. Neither provider coverage=COMPLETE
nor a caller's requested value can mint absence authority by itself.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid
from typing import Any

from rintel.analysis.contract import (
    Coverage, ExecutionModality, TargetResolution, TruthClass,
)
from rintel.clang_provider.compile_context import load_translation_unit
from rintel.clang_provider.frontend import (
    ClangFrontend, dependency_paths, detect_clang_identity,
)
from rintel.clang_provider.process import (
    PROVIDER_CONFIG_DIGEST, PROVIDER_ID, PROVIDER_VERSION,
)
from rintel.evidence_authority import (
    CanonicalStateRef, ClaimPayload, DEFAULT_ENGINE, EvidenceEnvelope,
)
from rintel.indexer import Indexer
from rintel.provider_arch import CanonicalPublicationStore, EvidenceCandidate
from rintel.provider_protocol import (
    AnalysisContract, BuildContext, HostRunStatus, ProviderHost,
    PublicationPolicy,
)

from .audit import audit_direct_body
from .model import (CAPABILITY_REGISTRY_VERSION, CoverageCertificate, ISSUER_ID,
                    RULE_VERSION, _issue, digest)
from .validation import exact_compile_row


class CoverageUnavailable(RuntimeError):
    """No production certificate was published; absence remains UNKNOWN."""


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class CoverageAuthority:
    """One public issuance seam, with real Clang and Store adapters behind it."""

    def __init__(self, store: Any, *, clang_executable: str = "/usr/bin/clang"):
        self.store = store
        self.clang_executable = clang_executable

    def issue_direct_static_call(
            self, *, repo_id: str, base_revision: str,
            compile_commands: str | Path, translation_unit: str,
            subject_id: str) -> CoverageCertificate:
        repo = self.store.repo(repo_id)
        snap = self.store.snapshot(base_revision)
        if (not repo or not snap or snap.get("repo_id") != repo_id or
                snap.get("publication_status") != "published" or
                self.store.current_snapshot(repo_id) != base_revision):
            raise CoverageUnavailable("exact current production revision required")
        root = Path(repo["root_path"]).resolve()
        relative = Path(translation_unit)
        if relative.is_absolute() or ".." in relative.parts:
            raise CoverageUnavailable("translation unit must be within repository")
        source = (root / relative).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise CoverageUnavailable("translation unit escapes repository") from exc
        if not source.is_file():
            raise CoverageUnavailable("translation unit unavailable")
        subject = self.store.node_by_id(repo_id, base_revision, subject_id)
        if (not subject or subject.get("kind") != "FUNCTION" or
                subject.get("path") != relative.as_posix()):
            raise CoverageUnavailable("subject is not a canonical function in exact TU")
        compdb = Path(compile_commands).resolve()
        compdb_digest = _sha(compdb)
        clang = detect_clang_identity(self.clang_executable)
        row = exact_compile_row(compdb, source)
        unit = load_translation_unit(row, clang, provider_version=PROVIDER_VERSION,
                                     semantic_schema_version="clang-ast-v1")
        indexed_source = self.store.file_state(repo_id, relative.as_posix())
        if (not indexed_source or indexed_source.get("hash") !=
                unit.source_digest.removeprefix("sha256:")):
            raise CoverageUnavailable("source differs from current canonical index input")
        dependencies = dependency_paths(unit)
        dependency_digests = {str(path): _sha(path) for path in dependencies
                              if path.resolve() != source}
        analysis_id = f"coverage-clang-{uuid.uuid4().hex}"
        manifest = {relative.as_posix(): tuple(sorted(dependency_digests))}
        contract = AnalysisContract(
            analysis_id=analysis_id, repo_id=repo_id,
            repo_snapshot=digest({"base_revision": base_revision,
                                  "tu_identity": unit.tu_identity,
                                  "dependencies": dependency_digests}),
            root=str(root),
            provider_config={"compile_commands": str(compdb),
                             "clang_executable": clang.executable},
            build_context_id=unit.semantic_command_digest,
            build_context=BuildContext(
                defines=unit.defines, include_paths=unit.include_paths,
                compiler_flags=unit.relevant_flags,
                language_standard=unit.language_standard, target=unit.target),
            input_content_digest=unit.source_digest, semantic_digest=None,
            semantic_identity_schema_version="clang-ast-v1",
            changed_scope=(relative.as_posix(),),
            invalidated_scope=(relative.as_posix(),),
            dependency_manifest=manifest,
            dependency_manifest_digest=digest(manifest),
            projection_identity="canonical-evidence", projection_version="1",
            publication_policy=PublicationPolicy.STAGE_ONLY,
        )
        with tempfile.TemporaryDirectory(prefix="rintel-coverage-") as scratch:
            host_result = ProviderHost(
                [sys.executable, "-m", "rintel.clang_provider.process"],
                staging_root=Path(scratch) / "staging",
                publication_store=CanonicalPublicationStore(
                    Path(scratch) / "unused-publication"),
                lane_id="clang_provider", timeout_s=120,
            ).run_analysis(contract, publication_revision=analysis_id)
        if (host_result.status is not HostRunStatus.STAGED or
                host_result.staged_result is None or
                host_result.provider is None or
                host_result.coverage.get("level") != "COMPLETE" or
                host_result.provider.provider_id != PROVIDER_ID or
                host_result.provider.provider_version != PROVIDER_VERSION or
                host_result.provider.provider_config_digest != PROVIDER_CONFIG_DIGEST):
            raise CoverageUnavailable(
                f"external Clang RPP run not complete: {host_result.error}")
        # A fresh direct frontend audit does not trust RPP's generic coverage
        # level as direct-call absence authority.
        frontend = ClangFrontend(max_output_bytes=768 * 1024 * 1024).analyze(unit)
        # rpp/1 still carries provider-local legacy spellings.  Rintel, not
        # the provider, binds them to the exact current C identity.  A same-
        # named Python/Fortran symbol is never an alternative C target.
        from dataclasses import replace
        current_c_functions = [node for node in self.store.all_nodes(
            repo_id, base_revision) if node["kind"] == "FUNCTION"
            and node["language"] == "c"]

        def resolve_c_name(name: str) -> str | None:
            matches = [node["id"] for node in current_c_functions
                       if node["qname"] == name]
            return matches[0] if len(matches) == 1 else None

        def bind_provider_id(value: str) -> str:
            if not value.startswith("node:FUNCTION:") or ":v2:" in value:
                return value
            bound = resolve_c_name(value.removeprefix("node:FUNCTION:"))
            if bound is None:
                raise CoverageUnavailable(
                    f"Clang provider identity not uniquely bound: {value}")
            return bound

        candidates = tuple(
            replace(fact, subject=bind_provider_id(fact.subject),
                    object=(bind_provider_id(fact.object)
                            if isinstance(fact.object, str) and
                            fact.object_is_identity else fact.object))
            if (fact.witness.get("provider_local", {}).get("kind") == "Function"
                or fact.predicate == "CALL") else fact
            for fact in host_result.staged_result.facts)
        edges = {row["id"] for row in self.store.all_edges(repo_id, base_revision)}
        exhaustive, reasons, direct = audit_direct_body(
            frontend.ast, frontend.diagnostics, source=source,
            subject_id=subject_id, candidates=candidates,
            existing_edges=edges, subject_name=subject["name"],
            resolve_target=resolve_c_name)
        function_facts = [fact for fact in candidates
                          if fact.subject == subject_id and fact.witness.get(
                              "provider_local", {}).get("kind") == "Function"
                          and fact.source_span and fact.source_span.get(
                              "file") == relative.as_posix()]
        if len(function_facts) != 1:
            raise CoverageUnavailable("Clang subject function identity not unique")
        if unit.language != "c":
            reasons.append("cxx_outside_first_complete_capability")
        if _sha(compdb) != compdb_digest or _sha(source) != unit.source_digest or any(
                _sha(Path(path)) != prior
                for path, prior in dependency_digests.items()):
            raise CoverageUnavailable(
                "compile command, source, or dependency changed during analysis")
        completeness = "COMPLETE" if exhaustive and not reasons else "PARTIAL"
        # SQLite's LifecycleRepository DDL uses executescript, which commits
        # any open transaction. Initialize it before staging publication.
        from rintel.design_lifecycle.repository import LifecycleRepository
        certificate_repository = LifecycleRepository(self.store)
        supports: list[str] = []
        try:
            self.store.begin(repo_id)
            if self.store.current_snapshot(repo_id) != base_revision:
                raise CoverageUnavailable("canonical CURRENT moved during analyzer run")
            meta = snap.get("meta_json", snap.get("meta", {}))
            meta = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
            meta.update({"run_id": analysis_id, "mode": "coverage_support_overlay",
                         "coverage_parent_revision": base_revision})
            revision = self.store.new_snapshot(
                repo_id, base_revision, commit=snap.get("commit_sha"), meta=meta)
            # Exact graph copy; this issuer adds provenance, not graph facts.
            Indexer(self.store, str(root), repo_id=repo_id)._copy_previous(
                revision, base_revision)
            state = CanonicalStateRef(base_revision,
                                      f"snapshot-ref:{base_revision}")
            supports.append(self._support_candidate(
                function_facts[0], revision=revision, owner_path=relative.as_posix(),
                kind="node", fact_kind="FUNCTION", fact_id=subject_id,
                source_ids=None, state=state, unit=unit,
                analysis_id=analysis_id, qname=subject["qname"],
                compile_commands_path=str(compdb),
                compile_commands_digest=compdb_digest,
                dependency_digests=dependency_digests))
            if exhaustive:
                for fact in candidates:
                    if fact.subject != subject_id or fact.predicate != "CALL" \
                            or not isinstance(fact.object, str) \
                            or fact.object not in direct:
                        continue
                    edge_id = f"edge:CALLS:{subject_id}:{fact.object}"
                    supports.append(self._support_candidate(
                        fact, revision=revision, owner_path=relative.as_posix(),
                        kind="edge", fact_kind="CALLS", fact_id=edge_id,
                        source_ids=(subject_id, fact.object), state=state,
                        unit=unit, analysis_id=analysis_id,
                        compile_commands_path=str(compdb),
                        compile_commands_digest=compdb_digest,
                        dependency_digests=dependency_digests))
            if self.store.unsupported_new_facts(repo_id, revision, base_revision):
                raise CoverageUnavailable("support overlay lost canonical lineage")
            self.store.publish_snapshot(repo_id, revision)
        except BaseException:
            self.store.rollback()
            raise
        payload = {
            "schema_version": "coverage-certificate/1",
            "issuer": ISSUER_ID, "repo_id": repo_id,
            "canonical_revision": revision, "analyzer_run_id": analysis_id,
            "analyzer": PROVIDER_ID, "provider": PROVIDER_ID,
            "provider_version": PROVIDER_VERSION,
            "provider_config_digest": PROVIDER_CONFIG_DIGEST,
            "analyzer_executable": clang.executable,
            "analyzer_identity": clang.to_dict(),
            "language": unit.language, "relation_kind": "DIRECT_STATIC_CALL",
            "subject": subject_id, "subject_scope": "EXACT_TU_FUNCTION_BODY",
            "translation_unit": relative.as_posix(),
            "build_context": unit.semantic_command_digest,
            "compile_command_digest": unit.raw_command_digest,
            "compile_commands_path": str(compdb),
            "tu_identity": unit.tu_identity,
            "source_digest": unit.source_digest,
            "dependency_digests": dependency_digests,
            "included_domain": {
                "scope_type": "EXACT_TU_FUNCTION_BODY",
                "translation_unit": relative.as_posix(), "subject": subject_id,
                "relation": "DIRECT_STATIC_CALL"},
            "excluded_domain": [
                "indirect/function-pointer calls", "virtual dispatch",
                "callbacks and runtime-generated calls", "whole-program behavior",
                "cross-language calls", "dynamic loading and JIT"],
            "parse_status": "COMPLETE", "analysis_status": "COMPLETE",
            "unsupported_constructs": sorted(set(reasons)),
            "completeness": completeness,
            "rule_version": RULE_VERSION,
            "registry_version": DEFAULT_ENGINE.registry.version,
            "capability_registry_version": CAPABILITY_REGISTRY_VERSION,
            "support_receipt_ids": supports,
            "direct_call_targets": dict(sorted(direct.items())),
            "rpp_fact_count": host_result.fact_count,
            "rpp_run_coverage": dict(host_result.coverage),
            "ast_digest": digest(frontend.ast),
            "diagnostics_digest": digest(frontend.diagnostics),
            "created_at": self.store.now(),
        }
        try:
            certificate = _issue(payload)
            # The repository commits the certificate and the still-open
            # publication transaction together. A failed insert rolls back
            # both graph overlay and certificate.
            certificate_repository._record_issued_coverage_certificate(certificate)
        except BaseException:
            self.store.rollback()
            raise
        return certificate

    def _support_candidate(
            self, fact: EvidenceCandidate, *, revision: str, owner_path: str,
            kind: str, fact_kind: str, fact_id: str,
            source_ids: tuple[str, str] | None, state: CanonicalStateRef,
            unit: Any, analysis_id: str, qname: str | None = None,
            compile_commands_path: str,
            compile_commands_digest: str,
            dependency_digests: dict[str, str]) -> str:
        claim = ClaimPayload(
            claim_id=fact.provider_fact_id, subject=fact.subject,
            predicate="NODE" if kind == "node" else fact_kind,
            object=({"kind": fact_kind, "qname": qname}
                    if kind == "node" else fact.object),
            source_span=dict(fact.source_span or {}),
            truth_class=TruthClass.OBSERVED,
            resolution=TargetResolution.EXACT,
            coverage=Coverage.UNKNOWN,
            execution_modality=(ExecutionModality.UNKNOWN if kind == "node"
                                else ExecutionModality.MAY),
            revision_input=unit.tu_identity,
            object_is_identity=kind == "edge",
            witness={"source_provider_fact_id": fact.provider_fact_id,
                     "adapted_relation": "CALL→CALLS" if kind == "edge" else
                     "Function→FUNCTION node"},
        )
        provenance = dict(fact.witness.get("provenance", {}))
        provenance.update({"analysis_id": analysis_id,
                           "clang_binary_sha256": unit.clang.binary_sha256,
                           "clang_executable": unit.clang.executable,
                           "compile_commands_path": compile_commands_path,
                           "compile_commands_sha256": compile_commands_digest,
                           "dependency_digests": dict(dependency_digests),
                           "raw_command_digest": unit.raw_command_digest,
                           "source_digest": unit.source_digest,
                           "tu_identity": unit.tu_identity})
        envelope = EvidenceEnvelope.for_claim(
            lane_id="clang_provider", producer=PROVIDER_ID,
            producer_version=PROVIDER_VERSION, scope=(owner_path,),
            provenance=provenance,
            limitations=("PILOT_ONLY Clang AST interface",
                         "positive fact support is not absence completeness"),
            claim=claim)
        admission = DEFAULT_ENGINE.admit(envelope, state)
        receipt = DEFAULT_ENGINE.issue_fact_support(
            admission, envelope, canonical_revision=revision,
            canonical_fact_id=fact_id, canonical_fact_kind=fact_kind,
            canonical_fact_type=kind, owner_path=owner_path,
            build_context_id=unit.semantic_command_digest,
            source_digest=unit.source_digest, source_ids=source_ids)
        self.store.add_support_receipt(
            self.store.snapshot(revision)["repo_id"], revision, receipt)
        return receipt.to_dict()["support_receipt_id"]


__all__ = ["CoverageAuthority", "CoverageUnavailable"]
