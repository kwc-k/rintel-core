"""Reference RPP Host for external Provider processes."""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from enum import Enum
import json
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence

from rintel.analysis.contract import Coverage, ExecutionModality, TargetResolution, TruthClass
from rintel.provider_arch import (
    CanonicalPublicationStore,
    EvidenceCandidate,
    ProviderKind,
    ProviderResult,
)
from rintel.evidence_authority import CanonicalStateRef, DEFAULT_ENGINE

from .model import (
    PROTOCOL_VERSION,
    AnalysisContract,
    IncrementalCapability,
    ProviderAdvertisement,
    PublicationPolicy,
)
from .staging import StagingArea
from .validation import ProtocolError, validate_message


class HostRunStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    STAGED = "STAGED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()


class _CancellationRequested(RuntimeError):
    pass


@dataclass(frozen=True)
class HostRunResult:
    status: HostRunStatus
    analysis_id: str
    provider_pid: int | None
    provider: ProviderAdvertisement | None
    batch_count: int
    fact_count: int
    coverage: Mapping[str, Any]
    publication: Mapping[str, Any] | None
    staged_result: ProviderResult | None
    message_types: tuple[str, ...]
    staging_path: str
    error: str | None = None


def _advertisement(wire: Mapping[str, Any]) -> ProviderAdvertisement:
    inc = wire["incremental_capability"]
    return ProviderAdvertisement(
        provider_id=wire["provider_id"],
        provider_version=wire["provider_version"],
        provider_kind=ProviderKind(wire["provider_kind"]),
        provider_config_digest=wire["provider_config_digest"],
        languages=tuple(wire["languages"]),
        capabilities=tuple(wire["capabilities"]),
        incremental=IncrementalCapability(
            modes=tuple(inc["modes"]),
            accepts_changed_scope=inc["accepts_changed_scope"],
            reports_invalidated_scope=inc["reports_invalidated_scope"],
            reports_dependency_manifest=inc["reports_dependency_manifest"],
            reports_semantic_digest=inc["reports_semantic_digest"],
            reports_reuse_source=inc["reports_reuse_source"],
        ),
    )


def _canonical_entity(entity: Mapping[str, Any]) -> str:
    kind = str(entity["kind"]).upper()
    qname = str(entity["qualified_name"]).strip()
    if not qname:
        raise ProtocolError("empty semantic qualified_name")
    return f"node:{kind}:{qname}"


def _truth(basis: str) -> TruthClass:
    return {
        "DIRECT": TruthClass.OBSERVED,
        "DERIVED": TruthClass.INFERRED,
        "HEURISTIC": TruthClass.HEURISTIC,
    }[basis]


def _candidate(raw: Mapping[str, Any], *, provider: ProviderAdvertisement,
               contract: AnalysisContract, coverage: Coverage) -> EvidenceCandidate:
    obj = raw["object"]
    if "entity" in obj:
        target: Any = _canonical_entity(obj["entity"])
        resolution = TargetResolution.EXACT
        object_is_identity = True
    elif "candidates" in obj:
        target = [_canonical_entity(item) for item in obj["candidates"]]
        resolution = TargetResolution.CANDIDATE_SET if target else TargetResolution.UNKNOWN
        object_is_identity = True
    elif "literal" in obj:
        target = obj["literal"]
        resolution = TargetResolution.UNKNOWN
        object_is_identity = False
    else:
        target = None
        resolution = TargetResolution.UNKNOWN
        object_is_identity = False
    provenance = {
        "provider_id": provider.provider_id,
        "provider_version": provider.provider_version,
        "provider_config_digest": provider.provider_config_digest,
        "analysis_id": contract.analysis_id,
        "repo_snapshot": contract.repo_snapshot,
        "build_context_id": contract.build_context_id,
        "provider_fact_id": raw["provider_fact_id"],
        "source_span": dict(raw["source_span"]),
    }
    return EvidenceCandidate(
        provider_id=provider.provider_id,
        provider_fact_id=raw["provider_fact_id"],
        subject=_canonical_entity(raw["subject"]),
        predicate=raw["predicate"],
        object=target,
        source_span=dict(raw["source_span"]),
        truth_class=_truth(raw["claim"]["observation_basis"]),
        coverage=coverage,
        resolution=resolution,
        revision_input=contract.repo_snapshot,
        execution_modality=ExecutionModality(raw["claim"]["execution_modality"]),
        witness={"provider_local": dict(raw), "provenance": provenance},
        object_is_identity=object_is_identity,
    )


class ProviderHost:
    """Deep module implementing the complete external Provider lifecycle."""

    def __init__(self, command: Sequence[str], *, staging_root: str | Path,
                 publication_store: CanonicalPublicationStore,
                 lane_id: str,
                 timeout_s: float = 30.0):
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("an explicit argv command is required")
        self.command = tuple(command)
        self.staging_root = Path(staging_root)
        self.publication_store = publication_store
        self.lane_id = lane_id
        self.timeout_s = timeout_s

    def run_analysis(self, contract: AnalysisContract, *,
                     publication_revision: str,
                     cancellation: CancellationToken | None = None) -> HostRunResult:
        transcript: list[str] = []
        provider: ProviderAdvertisement | None = None
        provider_pid: int | None = None
        staging = StagingArea(self.staging_root, contract.analysis_id)
        staging.start({
            "analysis_id": contract.analysis_id,
            "repo_snapshot": contract.repo_snapshot,
            "build_context_id": contract.build_context_id,
        })
        process: subprocess.Popen[str] | None = None
        stderr_tail: deque[str] = deque(maxlen=32)
        try:
            clean_env = {
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONUNBUFFERED": "1",
            }
            process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1, env=clean_env,
            )
            provider_pid = process.pid
            reader: queue.Queue[str | None] = queue.Queue()
            assert process.stdout is not None
            def read_stdout() -> None:
                for line in process.stdout:
                    reader.put(line)
                reader.put(None)
            threading.Thread(target=read_stdout, daemon=True).start()
            assert process.stderr is not None
            def read_stderr() -> None:
                while chunk := process.stderr.read(4096):
                    stderr_tail.append(chunk)
            threading.Thread(target=read_stderr, daemon=True).start()
            self._send(process, {
                "protocol_version": PROTOCOL_VERSION,
                "type": "hello",
                "host": "rintel",
                "authority": "RINTEL_CANONICAL_ONLY",
            }, transcript)
            ack = self._receive(process, reader, transcript, "hello_ack")
            provider = _advertisement(ack["advertisement"])
            wire_contract = contract.to_wire(provider)
            staging.bind_session(provider.to_wire(), wire_contract)
            self._send(process, {
                "protocol_version": PROTOCOL_VERSION,
                "type": "analyze",
                "contract": wire_contract,
            }, transcript)
            started = self._receive(process, reader, transcript, "analysis_started")
            self._same_analysis(contract, started)
            if cancellation is not None and cancellation.requested:
                return self._cancel_session(
                    process, reader, transcript, contract, staging,
                    provider, provider_pid)
            completion: dict[str, Any] | None = None
            while completion is None:
                try:
                    message = self._receive(
                        process, reader, transcript, cancellation=cancellation)
                except _CancellationRequested:
                    return self._cancel_session(
                        process, reader, transcript, contract, staging,
                        provider, provider_pid)
                self._same_analysis(contract, message)
                if message["type"] == "fact_batch":
                    staging.append_batch(message["sequence"], message["facts"])
                elif message["type"] == "analysis_complete":
                    completion = message
                elif message["type"] == "protocol_error":
                    raise ProtocolError(
                        f"provider protocol error {message['code']}: {message['message']}")
                else:
                    raise ProtocolError(f"unexpected provider message: {message['type']}")
            self._validate_completion(contract, provider, staging, completion)
            staging.complete(completion)
            self._send(process, {
                "protocol_version": PROTOCOL_VERSION, "type": "shutdown",
            }, transcript)
            self._receive(process, reader, transcript, "shutdown_ack")
            exit_code = process.wait(timeout=self.timeout_s)
            if exit_code != 0:
                raise ProtocolError(f"provider exited with status {exit_code}")

            coverage = Coverage(completion["coverage"]["level"])
            candidates = tuple(
                _candidate(raw, provider=provider, contract=contract, coverage=coverage)
                for raw in staging.facts()
            )
            publication = None
            status = HostRunStatus.STAGED
            result = ProviderResult(
                provider.provider_id, provider.provider_version,
                contract.repo_snapshot, facts=candidates,
                coverage={
                    "run": dict(completion["coverage"]),
                    "capability": provider.to_wire(),
                },
            )
            if (completion["status"] == "COMPLETE"
                    and contract.publication_policy is PublicationPolicy.REQUIRE_COMPLETE):
                current = self.publication_store.current_revision() or "UNPUBLISHED"
                state = CanonicalStateRef(
                    revision_id=current,
                    state_hash="sha256:" + hashlib.sha256(current.encode()).hexdigest(),
                )
                batch = DEFAULT_ENGINE.prepare_publication(
                    result, lane_id=self.lane_id, state=state,
                    build_context_id=contract.build_context_id,
                    provider_config_digest=provider.provider_config_digest)
                publication = self.publication_store.publish(publication_revision, batch)
                status = HostRunStatus.PUBLISHED
            return HostRunResult(
                status=status, analysis_id=contract.analysis_id,
                provider_pid=provider_pid, provider=provider,
                batch_count=staging.batch_count, fact_count=staging.fact_count,
                coverage=dict(completion["coverage"]), publication=publication,
                staged_result=result,
                message_types=tuple(transcript), staging_path=str(staging.path),
            )
        except Exception as exc:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            detail = str(exc)
            if stderr_tail:
                tail = "".join(stderr_tail)[-4096:].strip()
                if tail:
                    detail += " | provider stderr tail: " + tail
            failed_path = staging.quarantine(detail)
            return HostRunResult(
                status=HostRunStatus.FAILED, analysis_id=contract.analysis_id,
                provider_pid=provider_pid, provider=provider,
                batch_count=staging.batch_count, fact_count=staging.fact_count,
                coverage={}, publication=None, staged_result=None,
                message_types=tuple(transcript),
                staging_path=str(failed_path), error=detail,
            )

    def _cancel_session(self, process: subprocess.Popen[str],
                        reader: queue.Queue[str | None], transcript: list[str],
                        contract: AnalysisContract, staging: StagingArea,
                        provider: ProviderAdvertisement,
                        provider_pid: int) -> HostRunResult:
        self._send(process, {
            "protocol_version": PROTOCOL_VERSION, "type": "cancel",
            "analysis_id": contract.analysis_id,
        }, transcript)
        cancelled = self._receive(process, reader, transcript, "cancelled")
        self._same_analysis(contract, cancelled)
        self._send(process, {
            "protocol_version": PROTOCOL_VERSION, "type": "shutdown",
        }, transcript)
        self._receive(process, reader, transcript, "shutdown_ack")
        exit_code = process.wait(timeout=self.timeout_s)
        if exit_code != 0:
            raise ProtocolError(f"provider exited with status {exit_code}")
        cancelled_path = staging.quarantine("cancelled by Host")
        return HostRunResult(
            status=HostRunStatus.CANCELLED,
            analysis_id=contract.analysis_id, provider_pid=provider_pid,
            provider=provider, batch_count=staging.batch_count,
            fact_count=staging.fact_count, coverage={}, publication=None,
            staged_result=None,
            message_types=tuple(transcript), staging_path=str(cancelled_path),
        )

    def _send(self, process: subprocess.Popen[str], message: dict[str, Any],
              transcript: list[str]) -> None:
        validate_message(message)
        assert process.stdin is not None
        process.stdin.write(json.dumps(message, sort_keys=True) + "\n")
        process.stdin.flush()
        transcript.append(message["type"])

    def _receive(self, process: subprocess.Popen[str], reader: queue.Queue[str | None],
                 transcript: list[str],
                 expected_type: str | None = None,
                 cancellation: CancellationToken | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_s
        while True:
            if cancellation is not None and cancellation.requested:
                raise _CancellationRequested
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProtocolError("provider response timeout")
            try:
                line = reader.get(timeout=min(0.05, remaining))
                break
            except queue.Empty:
                continue
        if line is None:
            code = process.poll()
            raise ProtocolError(f"provider stream closed before completion (status={code})")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError("provider emitted invalid JSON") from exc
        validate_message(message, expected_type=expected_type)
        transcript.append(message["type"])
        return message

    @staticmethod
    def _same_analysis(contract: AnalysisContract, message: Mapping[str, Any]) -> None:
        if message.get("analysis_id") != contract.analysis_id:
            raise ProtocolError("analysis_id mismatch")

    @staticmethod
    def _validate_completion(contract: AnalysisContract,
                             provider: ProviderAdvertisement,
                             staging: StagingArea,
                             completion: Mapping[str, Any]) -> None:
        if completion["batch_count"] != staging.batch_count \
                or completion["fact_count"] != staging.fact_count:
            raise ProtocolError("completion count mismatch")
        if completion["status"] == "FAILED":
            raise ProtocolError("provider reported FAILED completion")
        coverage = completion["coverage"]
        if coverage["level"] != completion["status"] and completion["status"] != "FAILED":
            raise ProtocolError("completion status/coverage mismatch")
        observed = set(coverage["capabilities_observed"])
        if not observed <= set(provider.capabilities):
            raise ProtocolError("run coverage claims undeclared capability")
        inc = completion["incremental_result"]
        expected = contract.cache_identity(provider)
        if inc["cache_identity"] != expected:
            raise ProtocolError("completion cache identity mismatch")
        if inc["semantic_identity_schema_version"] != contract.semantic_identity_schema_version:
            raise ProtocolError("semantic identity schema mismatch")
        if inc["projection_identity"] != contract.projection_identity \
                or inc["projection_version"] != contract.projection_version:
            raise ProtocolError("projection identity mismatch")
        cutoff_reason = inc["cutoff_reason"]
        reuse_source = inc["reuse_source"]
        valid_cutoffs = {
            "NONE", "NO_CHANGE", "SEMANTIC_UNCHANGED",
            "DEPENDENCY_UNAFFECTED", "CACHE_HIT",
        }
        if cutoff_reason not in valid_cutoffs:
            raise ProtocolError("unknown cutoff reason")
        if cutoff_reason != "NONE" and not reuse_source:
            raise ProtocolError("reuse source required for cutoff")
        if cutoff_reason == "NONE" and reuse_source is not None:
            raise ProtocolError("reuse source forbidden without cutoff")


__all__ = ["CancellationToken", "HostRunResult", "HostRunStatus", "ProviderHost"]
