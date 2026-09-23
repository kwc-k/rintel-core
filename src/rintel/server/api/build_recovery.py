"""Build observation API. Commands are selected from host-owned profiles only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ...build_recovery import BuildProfile, BuildRecoveryError, BuildRecoveryService
from ...build_artifact_projection import (ArtifactReadError, MAX_DISPLAY_BYTES,
                                          read_build_artifact)
from ...design_lifecycle import DesignLifecycleError, DesignLifecycleService
from ...design_lifecycle.source_projection import project_source_span
from ...e2e_profile import load_e2e_profile
from ...execution_authority import ExecutionError
from ...store import Store
from ..deps import get_store
from ..errors import ApiError
from ..execution_config import load_execution_registry

router = APIRouter(prefix="/design-changes/{change_id}/build", tags=["build-recovery"])


def _service(request: Request, store: Store) -> BuildRecoveryService:
    settings = request.app.state.settings
    try:
        profiles, authority = load_execution_registry(settings)
    except (ExecutionError, BuildRecoveryError) as exc:
        raise ApiError(503, "build_profile_invalid", "host build profile configuration invalid") from exc
    lifecycle = DesignLifecycleService(store)
    return BuildRecoveryService(settings.build_artifacts_path, profiles,
                                change_lookup=lifecycle.get_change,
                                application_recorder=lifecycle.apply_command,
                                execution_authority=authority)


def _artifact_service(request: Request) -> BuildRecoveryService:
    """Artifact reads use the immutable attempt receipt, without a datastore connection."""
    settings = request.app.state.settings
    try:
        profiles, authority = load_execution_registry(settings)
    except (ExecutionError, BuildRecoveryError) as exc:
        raise ApiError(503, "build_profile_invalid", "host build profile configuration invalid") from exc
    return BuildRecoveryService(settings.build_artifacts_path, profiles,
                                execution_authority=authority)


def _require_bound(service: BuildRecoveryService, change_id: str,
                   attempt_id: str) -> dict:
    attempt = service.get_attempt(attempt_id)
    if attempt["change_id"] != change_id:
        raise BuildRecoveryError("attempt is not bound to DesignChange")
    return attempt


def _error(exc: BuildRecoveryError | DesignLifecycleError) -> ApiError:
    return ApiError(409, "build_recovery_denied", str(exc))


class RunBody(BaseModel):
    profile_id: str
    previous_attempt: str | None = None
    application_id: str | None = None


class ApplicationBody(BaseModel):
    previous_attempt: str
    repair_candidate_id: str
    actor: str


@router.post("/attempts", status_code=201)
def run_build(change_id: str, body: RunBody, request: Request,
              store: Store = Depends(get_store)) -> dict:
    service = _service(request, store)
    try:
        if body.previous_attempt:
            _require_bound(service, change_id, body.previous_attempt)
        return service.run(body.profile_id, change_id=change_id,
                           previous_attempt=body.previous_attempt,
                           application_id=body.application_id)
    except (BuildRecoveryError, DesignLifecycleError) as exc:
        raise _error(exc) from exc


@router.get("/attempts")
def list_builds(change_id: str, request: Request,
                store: Store = Depends(get_store)) -> dict:
    service = _service(request, store)
    return {"attempts": service.list_attempts(change_id=change_id)}


@router.get("/attempts/{attempt_id}")
def get_build(change_id: str, attempt_id: str, request: Request,
              test_run_id: str | None = None,
              store: Store = Depends(get_store)) -> dict:
    service = _service(request, store)
    try:
        _require_bound(service, change_id, attempt_id)
        return service.failure_package(
            attempt_id, store, test_run_id=test_run_id,
            lifecycle_service=DesignLifecycleService(store))
    except (BuildRecoveryError, DesignLifecycleError) as exc:
        raise _error(exc) from exc


@router.get("/attempts/{attempt_id}/{stream}")
def get_raw(change_id: str, attempt_id: str, stream: str,
            request: Request) -> PlainTextResponse:
    service = _artifact_service(request)
    try:
        attempt = _require_bound(service, change_id, attempt_id)
        if stream not in {"stdout", "stderr"}:
            raise ArtifactReadError("invalid artifact kind")
        result = read_build_artifact(service.root, attempt, stream.upper(),
                                     attempt[f"{stream}_ref"], attempt[f"{stream}_sha256"])
        return PlainTextResponse(result["content"], headers={
            "X-Artifact-SHA256": result["sha256"],
            "X-Artifact-Truncated": str(result["truncated"]).lower()})
    except (BuildRecoveryError, DesignLifecycleError, ArtifactReadError, KeyError) as exc:
        raise _error(exc) from exc


@router.get("/attempts/{attempt_id}/artifacts/{kind}")
def get_bound_artifact(change_id: str, attempt_id: str, kind: str,
                       request: Request, artifact_id: str, expected_digest: str,
                       diagnostic_id: str | None = None,
                       max_bytes: int = Query(8192, ge=1, le=MAX_DISPLAY_BYTES)) -> dict:
    service = _artifact_service(request)
    try:
        attempt = _require_bound(service, change_id, attempt_id)
        return read_build_artifact(service.root, attempt, kind, artifact_id,
                                   expected_digest, diagnostic_id=diagnostic_id,
                                   max_bytes=max_bytes)
    except (BuildRecoveryError, DesignLifecycleError, ArtifactReadError) as exc:
        raise ApiError(409, "artifact_binding_mismatch", str(exc)) from exc


@router.get("/attempts/{attempt_id}/diagnostics/{diagnostic_id}/source")
def diagnostic_source(
    change_id: str, attempt_id: str, diagnostic_id: str, request: Request,
    revision: str, path: str, start_line: int, end_line: int,
    store: Store = Depends(get_store),
) -> dict:
    """Read only the exact source bytes recorded for this compiler attempt."""
    service = _service(request, store)
    try:
        attempt = _require_bound(service, change_id, attempt_id)
        diagnostic = next((item for item in attempt.get("diagnostics", [])
                           if item.get("id") == diagnostic_id), None)
        span = diagnostic.get("source_span") if diagnostic else None
        if (revision != attempt.get("source_revision") or not isinstance(span, dict)
                or span.get("path") != path or span.get("start_line") != start_line
                or (span.get("end_line") or start_line) != end_line):
            raise ApiError(409, "identity_mismatch", "diagnostic source binding mismatch")
        root = Path(attempt["cwd"]).resolve(strict=True)
        if not root.is_dir():
            raise ApiError(409, "source_unresolved", "attempt root unavailable")
        source = (root / path).resolve(strict=True)
        profile = load_e2e_profile()
        using_archive = False
        if profile is not None:
            manifest_path = Path(profile["witness"]["manifest_path"])
            manifest = json.loads(manifest_path.read_text())
            archived = (manifest.get("attempt_source_snapshots", {})
                        .get(attempt_id, {}).get(path))
            if archived:
                using_archive = True
                source = (manifest_path.parent / archived).resolve(strict=True)
                if not source.is_relative_to(manifest_path.parent.resolve()):
                    raise ApiError(409, "source_unresolved", "archive outside bundle")
        if not source.is_file() or (not using_archive and not source.is_relative_to(root)):
            raise ApiError(409, "source_unresolved", "source path is outside attempt root")
        data = source.read_bytes()
        expected = attempt.get("source_files", {}).get(path)
        if not expected or hashlib.sha256(data).hexdigest() != expected:
            raise ApiError(409, "source_unresolved", "attempt source bytes have drifted")
        lines = data.decode("utf-8", errors="replace").splitlines()
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            raise ApiError(409, "source_unresolved", "diagnostic line is unavailable")
        return {"status": "RESOLVED", "change_id": change_id,
                "attempt_id": attempt_id, "diagnostic_id": diagnostic_id,
                "source_revision": revision,
                "source_span": project_source_span(
                    {"path": path, "start_line": start_line, "end_line": end_line,
                     "start_column": span.get("start_column"),
                     "end_column": span.get("end_column")},
                    revision=revision, subject=diagnostic_id,
                    entity_type="diagnostic", evidence_ref=diagnostic.get("raw_ref")),
                "content": "\n".join(lines[start_line - 1:end_line]),
                "source_sha256": expected}
    except (BuildRecoveryError, DesignLifecycleError) as exc:
        raise _error(exc) from exc
    except (OSError, KeyError):
        raise ApiError(409, "source_unresolved", "attempt source is unavailable") from None


@router.post("/applications", status_code=201)
def record_application(change_id: str, body: ApplicationBody, request: Request,
                       store: Store = Depends(get_store)) -> dict:
    service = _service(request, store)
    try:
        _require_bound(service, change_id, body.previous_attempt)
        return service.record_application(
            previous_attempt=body.previous_attempt,
            repair_candidate_id=body.repair_candidate_id,
            change_id=change_id, actor=body.actor)
    except (BuildRecoveryError, DesignLifecycleError) as exc:
        raise _error(exc) from exc
