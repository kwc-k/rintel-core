"""Thin REST adapter over the server-owned ExecutionAuthority."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from ...design_lifecycle import DesignLifecycleError, DesignLifecycleService
from ...execution_authority import ExecutionError, ExecutionRequest
from ...store import Store
from ..deps import get_store
from ..errors import ApiError
from ..execution_config import load_execution_registry

router = APIRouter(prefix="/design-changes/{change_id}/executions",
                   tags=["execution-authority"])


class ExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    kind: Literal["BUILD", "TEST"]
    parameters: dict[str, str] = {}
    expected_source_revision: str | None = None
    previous_execution: str | None = None
    lineage_ref: str | None = None


def _authority(request: Request):
    try:
        return load_execution_registry(request.app.state.settings)[1]
    except ExecutionError as exc:
        raise ApiError(503, "execution_registry_invalid", str(exc)) from exc


def _error(exc: ExecutionError | DesignLifecycleError) -> ApiError:
    return ApiError(409, "execution_denied", str(exc))


@router.post("", status_code=201)
def execute(change_id: str, body: ExecuteBody, request: Request,
            store: Store = Depends(get_store)) -> dict:
    authority = _authority(request)
    lifecycle = DesignLifecycleService(store)
    try:
        change = lifecycle.get_change(change_id)
        profile = authority.profiles.get(body.profile_id)
        if profile is None or profile.repo_id != change.repo_id:
            raise ExecutionError("profile not registered for DesignChange repo")
        if change.state.value in {"CLOSED", "ABANDONED"}:
            raise ExecutionError("terminal DesignChange cannot execute")
        receipt = authority.execute(ExecutionRequest(
            profile_id=body.profile_id, kind=body.kind,
            parameters=tuple(sorted(body.parameters.items())), change_id=change_id,
            canonical_revision=(change.implementation or {}).get("current_evidence_revision")
            or change.base_canonical_revision,
            design_revision=change.design_revision.id,
            expected_source_revision=body.expected_source_revision,
            previous_execution=body.previous_execution,
            lineage_ref=body.lineage_ref))
        return receipt
    except (ExecutionError, DesignLifecycleError) as exc:
        raise _error(exc) from exc


@router.get("")
def list_executions(change_id: str, request: Request,
                    store: Store = Depends(get_store)) -> dict:
    DesignLifecycleService(store).get_change(change_id)
    authority = _authority(request)
    return {"executions": authority.list_receipts(change_id=change_id)}


@router.get("/{execution_id}")
def get_execution(change_id: str, execution_id: str, request: Request,
                  store: Store = Depends(get_store)) -> dict:
    authority = _authority(request)
    try:
        receipt = authority.get_receipt(execution_id)
        if receipt["change_id"] != change_id:
            raise ExecutionError("execution not bound to DesignChange")
        return receipt
    except ExecutionError as exc:
        raise _error(exc) from exc


@router.get("/{execution_id}/test-run")
def get_test_run(change_id: str, execution_id: str, request: Request,
                 store: Store = Depends(get_store)) -> dict:
    authority = _authority(request)
    try:
        execution = authority.get_receipt(execution_id)
        if execution["change_id"] != change_id or not execution["test_run_id"]:
            raise ExecutionError("trusted test run not bound to DesignChange")
        return authority.get_test_run(execution["test_run_id"])
    except ExecutionError as exc:
        raise _error(exc) from exc


@router.post("/{execution_id}/test-run/adopt")
def adopt_test_run(change_id: str, execution_id: str, request: Request,
                   store: Store = Depends(get_store)) -> dict:
    authority = _authority(request)
    try:
        execution = authority.get_receipt(execution_id)
        if execution["change_id"] != change_id or not execution["test_run_id"]:
            raise ExecutionError("trusted test run not bound to DesignChange")
        service = DesignLifecycleService(store,
                                         test_run_lookup=authority.test_run_lookup)
        return service.record_test_run(change_id, execution["test_run_id"])
    except (ExecutionError, DesignLifecycleError) as exc:
        raise _error(exc) from exc
