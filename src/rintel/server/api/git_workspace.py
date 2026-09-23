"""REST adapter for Git-native Agent workspaces and merge projections."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from ...design_lifecycle import DesignLifecycleError, DesignLifecycleService
from ...git_workspace import GitWorkspaceError
from ...git_integration_authority import GitIntegrationAuthority
from ...store import Store
from ..deps import get_store
from ..errors import ApiError
from .local_owner import owner_principal, require_local_request

router = APIRouter(prefix="/design-changes/{change_id}/git-collaboration",
                   tags=["git-collaboration"])


class AllocateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    allowed_scope: list[str] = Field(min_length=1)
    allowed_operations: list[Literal["EDIT", "BUILD", "TEST", "INDEX", "READ"]]


class IntegrateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_ids: list[str] = Field(min_length=1)
    target_branch: str = Field(min_length=1)


class ExecuteWorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1)
    kind: Literal["BUILD", "TEST"]
    parameters: dict[str, str] = {}


class AuditIntegrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    build_profile_id: str = Field(min_length=1)
    test_profile_id: str = Field(min_length=1)
    build_parameters: dict[str, str] = {}
    test_parameters: dict[str, str] = {}


def _error(exc: GitWorkspaceError | DesignLifecycleError) -> ApiError:
    status = 404 if getattr(exc, "code", "") in {
        "MISSING_WORKTREE", "MISSING_INTEGRATION", "change_not_found"} else 409
    return ApiError(status, getattr(exc, "code", "git_workspace_error"),
                    str(exc), getattr(exc, "details", {}))


def _change_repo(change_id: str, store: Store):
    service = DesignLifecycleService(store)
    change = service.get_change(change_id)
    repo = store.repo(change.repo_id)
    if not repo:
        raise DesignLifecycleError("repo_not_found", "change repository unavailable")
    return change, repo


@router.get("")
def collaboration(change_id: str, request: Request,
                  store: Store = Depends(get_store)) -> dict:
    try:
        change, repo = _change_repo(change_id, store)
        manager = request.app.state.git_workspace_manager
        result = manager.list_for_change(change.id)
        try:
            result["repository"] = manager.inspect_repository(repo["root_path"])
        except GitWorkspaceError as exc:
            result["repository"] = {"status": exc.code, "path": repo["root_path"]}
        return result
    except (GitWorkspaceError, DesignLifecycleError) as exc:
        raise _error(exc) from exc


@router.post("/workspaces", status_code=201)
def allocate(change_id: str, body: AllocateBody, request: Request,
             store: Store = Depends(get_store)) -> dict:
    require_local_request(request, mutation=True, intent="allocate-agent-worktree")
    owner_principal(request)
    try:
        change, repo = _change_repo(change_id, store)
        manager = request.app.state.git_workspace_manager
        git_repo = manager.inspect_repository(repo["root_path"])
        if git_repo["working_tree_state"] != "CLEAN":
            raise GitWorkspaceError(git_repo["working_tree_state"],
                                    "repository worktree must be clean")
        return manager.create_agent_worktree(
            repository_root=repo["root_path"], agent_id=body.agent_id,
            task_id=body.task_id, change_id=change.id,
            base_commit=git_repo["head_commit"], base_change_version=change.version,
            base_design_revision=change.design_revision.id,
            base_canonical_revision=(change.implementation or {}).get(
                "current_evidence_revision") or change.base_canonical_revision,
            allowed_scope=body.allowed_scope,
            allowed_operations=body.allowed_operations)
    except (GitWorkspaceError, DesignLifecycleError) as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}")
def assigned(change_id: str, workspace_id: str, request: Request,
             x_rintel_agent_session: str = Header(),
             store: Store = Depends(get_store)) -> dict:
    _change_repo(change_id, store)
    require_local_request(request)
    try:
        row = request.app.state.git_workspace_manager.assigned_workspace(
            workspace_id, x_rintel_agent_session)
        if row["change_id"] != change_id:
            raise GitWorkspaceError("WORKSPACE_CHANGE_MISMATCH",
                                    "workspace belongs to another change")
        return row
    except GitWorkspaceError as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/submit")
def submit(change_id: str, workspace_id: str, request: Request,
           x_rintel_agent_session: str = Header(),
           store: Store = Depends(get_store)) -> dict:
    _change_repo(change_id, store)
    require_local_request(request)
    try:
        row = request.app.state.git_workspace_manager.assigned_workspace(
            workspace_id, x_rintel_agent_session)
        if row["change_id"] != change_id:
            raise GitWorkspaceError("WORKSPACE_CHANGE_MISMATCH",
                                    "workspace belongs to another change")
        return request.app.state.git_workspace_manager.submit_agent_result(
            workspace_id, x_rintel_agent_session)
    except GitWorkspaceError as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/executions", status_code=201)
def execute_workspace(change_id: str, workspace_id: str,
                      body: ExecuteWorkspaceBody, request: Request,
                      x_rintel_agent_session: str = Header(),
                      store: Store = Depends(get_store)) -> dict:
    change, _repo = _change_repo(change_id, store)
    require_local_request(request)
    try:
        authority = request.app.state.execution_authority
        profile = authority.profiles.get(body.profile_id)
        if profile is None or profile.repo_id != change.repo_id:
            raise GitWorkspaceError("EXECUTION_PROFILE_MISMATCH",
                                    "profile is not registered for this repository")
        row = request.app.state.git_workspace_manager.assigned_workspace(
            workspace_id, x_rintel_agent_session)
        if row["change_id"] != change_id:
            raise GitWorkspaceError("WORKSPACE_CHANGE_MISMATCH",
                                    "workspace belongs to another change")
        return request.app.state.git_workspace_manager.request_workspace_execution(
            workspace_id, x_rintel_agent_session, profile_id=body.profile_id,
            kind=body.kind, parameters=tuple(sorted(body.parameters.items())))
    except GitWorkspaceError as exc:
        raise _error(exc) from exc


@router.post("/integrations", status_code=201)
def integrate(change_id: str, body: IntegrateBody, request: Request,
              store: Store = Depends(get_store)) -> dict:
    require_local_request(request, mutation=True, intent="create-integration")
    owner_principal(request)
    try:
        _change, repo = _change_repo(change_id, store)
        return request.app.state.git_workspace_manager.create_integration(
            repository_root=repo["root_path"], workspace_ids=body.workspace_ids,
            target_branch=body.target_branch)
    except (GitWorkspaceError, DesignLifecycleError) as exc:
        raise _error(exc) from exc


@router.get("/integrations/{integration_id}")
def integration(change_id: str, integration_id: str, request: Request,
                store: Store = Depends(get_store)) -> dict:
    _change_repo(change_id, store)
    try:
        row = request.app.state.git_workspace_manager.get_integration(integration_id)
        if not set(row["workspace_ids"]).issubset({
            item["workspace_id"] for item in
            request.app.state.git_workspace_manager.list_for_change(change_id)["workspaces"]}):
            raise GitWorkspaceError("WORKSPACE_CHANGE_MISMATCH",
                                    "integration belongs to another change")
        return row.get("latest_projection") or row
    except GitWorkspaceError as exc:
        raise _error(exc) from exc


@router.post("/integrations/{integration_id}/audit", status_code=201)
def audit_integration(change_id: str, integration_id: str,
                      body: AuditIntegrationBody, request: Request,
                      store: Store = Depends(get_store)) -> dict:
    require_local_request(request, mutation=True, intent="audit-integration")
    owner_principal(request)
    change, _repo = _change_repo(change_id, store)
    manager = request.app.state.git_workspace_manager
    try:
        row = manager.get_integration(integration_id)
        if row.get("change_id") != change.id:
            raise GitWorkspaceError("WORKSPACE_CHANGE_MISMATCH",
                                    "integration belongs to another change")
        authority = request.app.state.execution_authority
        for profile_id, kind in ((body.build_profile_id, "BUILD"),
                                 (body.test_profile_id, "TEST")):
            profile = authority.profiles.get(profile_id)
            if profile is None or profile.repo_id != change.repo_id or profile.kind != kind:
                raise GitWorkspaceError("EXECUTION_PROFILE_MISMATCH",
                                        f"{kind} profile is unavailable for repository")
        integration_authority = GitIntegrationAuthority(
            manager.state_root / "integration-authority",
            production_store=store, execution_authority=authority)
        return manager.audit_integration(
            integration_id, build_profile_id=body.build_profile_id,
            test_profile_id=body.test_profile_id,
            build_parameters=tuple(sorted(body.build_parameters.items())),
            test_parameters=tuple(sorted(body.test_parameters.items())),
            authority=integration_authority)
    except GitWorkspaceError as exc:
        raise _error(exc) from exc


@router.post("/integrations/{integration_id}/merge")
def merge(change_id: str, integration_id: str, request: Request,
          store: Store = Depends(get_store)) -> dict:
    require_local_request(request, mutation=True, intent="merge-integration")
    principal = owner_principal(request)
    _change_repo(change_id, store)
    try:
        row = request.app.state.git_workspace_manager.get_integration(integration_id)
        if not set(row["workspace_ids"]).issubset({
            item["workspace_id"] for item in
            request.app.state.git_workspace_manager.list_for_change(change_id)["workspaces"]}):
            raise GitWorkspaceError("WORKSPACE_CHANGE_MISMATCH",
                                    "integration belongs to another change")
        return request.app.state.git_workspace_manager.merge_authorized(
            integration_id, principal)
    except GitWorkspaceError as exc:
        raise _error(exc) from exc


@router.post("/integrations/{integration_id}/observe", status_code=201)
def observe(change_id: str, integration_id: str, request: Request,
            store: Store = Depends(get_store)) -> dict:
    require_local_request(request, mutation=True, intent="observe-merged-integration")
    owner_principal(request)
    change, _repo = _change_repo(change_id, store)
    manager = request.app.state.git_workspace_manager
    try:
        row = manager.get_integration(integration_id)
        if row.get("change_id") != change.id:
            raise GitWorkspaceError("WORKSPACE_CHANGE_MISMATCH",
                                    "integration belongs to another change")
        authority = GitIntegrationAuthority(
            manager.state_root / "integration-authority",
            production_store=store,
            execution_authority=request.app.state.execution_authority)
        return manager.observe_merged(integration_id, authority=authority)
    except GitWorkspaceError as exc:
        raise _error(exc) from exc
