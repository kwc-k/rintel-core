"""Thin REST adapter over the single DesignLifecycleService write seam."""
from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ...design_lifecycle import (
    AbandonChange,
    AdoptIndexJob,
    AcceptanceCriterion,
    BeginImplementation,
    CloseChange,
    DesignLifecycleError,
    DesignLifecycleService,
    DesignMutation,
    DesignRef,
    EvaluateExpectedActual,
    PlanChange,
    RecordReindex,
    RequestReindex,
    RunReindex,
    VerifyChange,
)
from ...design_lifecycle.projection import workspace_projection
from ...design_lifecycle.source_projection import project_source_span
from ...store import Store
from ..deps import get_store
from ..errors import ApiError
from ..jobs import make_index_runner
from ..api.local_owner import require_local_request, owner_principal
from ...local_owner_auth import COOKIE_NAME
from .source import source as read_source

router = APIRouter(prefix="/design-changes", tags=["design-lifecycle"])


class DesignRefBody(BaseModel):
    identity: str
    revision: str


class CriterionBody(BaseModel):
    id: str
    kind: Literal["structural", "semantic", "test", "runtime", "manual"]
    required: bool = True
    description: str = ""


class OpenChangeBody(BaseModel):
    repo_id: str
    base_canonical_revision: str
    intent: str = Field(min_length=1)
    scope: dict[str, Any] = {}
    acceptance_criteria: list[CriterionBody] = []
    actor: str
    architecture_proposal_ref: DesignRefBody | None = None
    flow_model_ref: DesignRefBody | None = None


class CommandBody(BaseModel):
    command: Literal[
        "plan", "begin_implementation", "record_reindex", "run_reindex",
        "evaluate_expected_actual", "verify", "close", "abandon",
        "design_mutation",
    ]
    actor: str
    change_version: int | None = None
    expected_design_revision: str | None = None
    expected_changes: list[dict[str, Any]] = []
    architecture_proposal_ref: DesignRefBody | None = None
    flow_model_ref: DesignRefBody | None = None
    expected_touched_scope: dict[str, Any] = {}
    final_canonical_revision: str | None = None
    actual_changed_files: list[str] = []
    actual_changed_symbols: list[str] = []
    actual_evidence: list[dict[str, Any]] = []
    verification_results: list[dict[str, Any]] = []
    rule_versions: dict[str, str] = {}
    reason: str = ""
    plane: Literal["architecture", "flow"] | None = None
    operation: str = ""
    payload: dict[str, Any] = {}


def _error(exc: DesignLifecycleError) -> ApiError:
    status = 404 if exc.code in {"change_not_found", "design_revision_not_found"} else 409
    return ApiError(status, exc.code, exc.message, exc.details)


def _ref(value: DesignRefBody | None) -> DesignRef | None:
    return DesignRef(**value.model_dump()) if value else None


def _owner_service(store: Store, request: Request) -> DesignLifecycleService:
    return DesignLifecycleService(
        store, job_lookup=request.app.state.jobs.get,
        authorization_lookup=lambda identity, _change: (
            request.app.state.local_owner_auth.approval_authorization(identity)))


def _local_session_identity(request: Request) -> str | None:
    try:
        require_local_request(request)
    except ApiError:
        return None
    return request.cookies.get(COOKIE_NAME)


@router.post("", status_code=201)
def open_change(req: OpenChangeBody, store: Store = Depends(get_store)) -> dict:
    try:
        return DesignLifecycleService(store).open_change(
            repo_id=req.repo_id,
            base_canonical_revision=req.base_canonical_revision,
            intent=req.intent, scope=req.scope,
            acceptance_criteria=[AcceptanceCriterion(**x.model_dump())
                                 for x in req.acceptance_criteria],
            actor=req.actor,
            architecture_proposal_ref=_ref(req.architecture_proposal_ref),
            flow_model_ref=_ref(req.flow_model_ref),
        ).to_dict()
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.get("")
def list_changes(repo_id: str | None = None,
                 store: Store = Depends(get_store)) -> dict:
    try:
        return {"changes": [x.to_dict() for x in
                            DesignLifecycleService(store).list_changes(
                                repo_id=repo_id)]}
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.get("/workspace-list")
def workspace_list(repo_id: str | None = None,
                   store: Store = Depends(get_store)) -> dict:
    changes = DesignLifecycleService(store).list_changes(repo_id=repo_id)
    return {"schema_version": "change-workspace-list/1", "changes": [{
        "id": change.id, "repo_id": change.repo_id,
        "intent": change.intent, "state": change.state.value,
        "version": change.version,
        "baseline_revision": change.base_canonical_revision,
        "design_revision": change.design_revision.id,
        "actual_revision": (change.implementation or {}).get(
            "current_evidence_revision"),
    } for change in changes]}


@router.get("/{change_id}")
def get_change(change_id: str, store: Store = Depends(get_store)) -> dict:
    try:
        return DesignLifecycleService(store).get_change(change_id).to_dict()
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.get("/{change_id}/receipts")
def receipts(change_id: str, store: Store = Depends(get_store)) -> dict:
    try:
        rows = DesignLifecycleService(store).receipts(change_id)
        return {"receipts": [asdict(x) for x in rows]}
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.get("/{change_id}/workspace")
def workspace(change_id: str, request: Request,
              store: Store = Depends(get_store)) -> dict:
    selector_keys = ("relation_kind", "source", "target",
                     "call_semantics", "translation_unit", "build_context",
                     "source_revision", "binary_sha256", "build_identity",
                     "runtime_relation_semantics", "bridge_receipt_id")
    selector = {key: request.query_params[key] for key in selector_keys
                if key in request.query_params}
    if "runtime_run_id" in request.query_params:
        selector["runtime_run_ids"] = request.query_params.getlist("runtime_run_id")
    try:
        return workspace_projection(_owner_service(store, request), change_id,
            relation_selector=selector if selector else None,
            authorization_identity=_local_session_identity(request))
    except DesignLifecycleError as exc:
        raise _error(exc) from exc
    except ValueError as exc:
        raise ApiError(422, "invalid_relation_selector", str(exc)) from exc


@router.get("/{change_id}/preflight")
def preflight(change_id: str, request: Request,
              store: Store = Depends(get_store)) -> dict:
    try:
        return _owner_service(store, request).preflight(
            change_id, authorization_identity=_local_session_identity(request))
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    expected_version: int
    expected_design_revision: str


@router.post("/{change_id}/approvals", status_code=201)
def approve(change_id: str, body: ApprovalBody, request: Request,
            store: Store = Depends(get_store)) -> dict:
    require_local_request(request, mutation=True, intent="approve")
    owner_principal(request)
    service = _owner_service(store, request)
    try:
        change = service.get_change(change_id)
        if change.design_revision.id != body.expected_design_revision:
            raise DesignLifecycleError(
                "stale_design_revision", "design revision has advanced")
        return service.record_approval(
            change_id, authorization_identity=request.cookies[COOKIE_NAME],
            decision=body.decision, expected_version=body.expected_version)
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


class RequestReindexBody(BaseModel):
    actor: str


class AdoptIndexJobBody(BaseModel):
    actor: str
    actual_evidence_selectors: list[dict[str, Any]] = []


@router.post("/{change_id}/reindex-jobs", status_code=202)
def request_reindex(change_id: str, body: RequestReindexBody, request: Request,
                    store: Store = Depends(get_store)) -> dict:
    jobs = request.app.state.jobs
    service = DesignLifecycleService(store, job_lookup=jobs.get)
    try:
        change = service.get_change(change_id)
        # Validate state before creating a generic job.
        preflight_result = service.preflight(change_id)
        if not preflight_result["actions"]["request_reindex"]["allowed"]:
            raise DesignLifecycleError(
                "request_reindex_denied", "re-index is not eligible",
                {"reasons": preflight_result["actions"]["request_reindex"]["denial_reasons"]})
        job = jobs.create("index", change.repo_id)
        result = service.apply_command(change_id, RequestReindex(
            actor=body.actor, job_id=job["id"]))
        bound_revision = result.implementation["index_job"]["input_canonical_revision"]
        snapshot = store.snapshot(bound_revision)
        source_commit = snapshot.get("commit_sha") if snapshot else None
        jobs.enqueue(job, make_index_runner(
            request.app.state.store_factory, change.repo_id,
            commit=source_commit))
        return {"change_id": result.id, "change_version": result.version,
                "job_id": job["id"], "status": "pending"}
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.post("/{change_id}/reindex-jobs/{job_id}/adopt")
def adopt_index_job(change_id: str, job_id: str, body: AdoptIndexJobBody,
                    request: Request, store: Store = Depends(get_store)) -> dict:
    try:
        service = DesignLifecycleService(
            store, job_lookup=request.app.state.jobs.get)
        return service.apply_command(change_id, AdoptIndexJob(
            actor=body.actor, job_id=job_id,
            actual_evidence_selectors=tuple(body.actual_evidence_selectors))).to_dict()
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.get("/{change_id}/design-revisions/{revision_id}")
def design_revision(change_id: str, revision_id: str,
                    store: Store = Depends(get_store)) -> dict:
    try:
        return DesignLifecycleService(store).get_design_revision(
            change_id, revision_id)
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.get("/{change_id}/source")
def bound_source(
    change_id: str, revision: str, entity_type: Literal["node", "edge"],
    entity_id: str, evidence_ref: str, path: str,
    start_line: int = Query(..., ge=1), end_line: int = Query(..., ge=1),
    store: Store = Depends(get_store),
) -> dict:
    """Resolve a pinned source span or fail closed; never jump to HEAD."""
    service = DesignLifecycleService(store)
    try:
        change = service.get_change(change_id)
        allowed = {change.base_canonical_revision}
        allowed.update(
            binding.get("actual_revision")
            for receipt in service.receipts(change_id)
            if (binding := receipt.payload.get("result_binding")))
        allowed.discard(None)
        if revision not in allowed:
            raise ApiError(409, "identity_mismatch",
                           "revision is not bound to this change")
        snap = store.snapshot(revision)
        if not snap or snap.get("repo_id") != change.repo_id:
            raise ApiError(409, "identity_mismatch",
                           "snapshot does not belong to change repository")
        rows = [r for r in store.evidence_for(
            change.repo_id, revision, entity_id)
            if r.get("entity_type") == entity_type]
        prefix = f"{revision}:{entity_type}:{entity_id}:"
        if not evidence_ref.startswith(prefix):
            raise ApiError(409, "identity_mismatch", "evidence ref mismatch")
        try:
            index = int(evidence_ref[len(prefix):])
            if index < 0:
                raise IndexError(index)
            row = rows[index]
        except (ValueError, IndexError):
            raise ApiError(409, "source_unresolved", "evidence ref unavailable") from None
        import json
        location = row.get("location_json")
        location = json.loads(location) if isinstance(location, str) else location
        if not isinstance(location, dict) or location.get("path") != path:
            raise ApiError(409, "identity_mismatch", "source path mismatch")
        if entity_type == "node":
            node = store.node_by_id(change.repo_id, revision, entity_id)
            if not node or node.get("path") != path or (
                node.get("start_line") != start_line or
                node.get("end_line") != end_line
            ):
                raise ApiError(409, "identity_mismatch", "source span owner mismatch")
        else:
            edge = next((e for e in store.all_edges(change.repo_id, revision)
                         if e.get("id") == entity_id), None)
            line = location.get("line") or location.get("start_line")
            if not edge or line != start_line or end_line != start_line:
                raise ApiError(409, "identity_mismatch", "edge span mismatch")
        if not snap.get("commit_sha") and (
            revision != store.current_snapshot(change.repo_id)
        ):
            raise ApiError(409, "source_unresolved",
                           "historical working-tree source is unavailable")
        result = read_source(
            repo_id=change.repo_id, path=path, snapshot=revision,
            start=start_line, end=end_line, store=store)
        if not snap.get("commit_sha") and result.get("drift") is not False:
            raise ApiError(409, "source_unresolved",
                           "working-tree source identity is unverified")
        return {
            "status": "RESOLVED", "subject": change_id,
            "canonical_revision": revision, "entity_type": entity_type,
            "entity_id": entity_id, "evidence_ref": evidence_ref,
            "source_span": project_source_span(
                {"path": path, "start_line": start_line, "end_line": end_line},
                revision=revision, subject=entity_id, entity_type=entity_type,
                evidence_ref=evidence_ref),
            "content": result["content"], "etag": result["etag"],
        }
    except DesignLifecycleError as exc:
        raise _error(exc) from exc


@router.post("/{change_id}/commands")
def command(change_id: str, req: CommandBody,
            store: Store = Depends(get_store)) -> dict:
    try:
        if req.command == "design_mutation":
            if req.plane is None or not req.operation:
                raise DesignLifecycleError(
                    "design_mutation_invalid",
                    "design_mutation requires plane and operation")
            cmd = DesignMutation(
                actor=req.actor, plane=req.plane,
                operation=req.operation, payload=req.payload,
                expected_design_revision=req.expected_design_revision)
        elif req.command == "plan":
            cmd = PlanChange(
                actor=req.actor, expected_changes=tuple(req.expected_changes),
                architecture_proposal_ref=_ref(req.architecture_proposal_ref),
                flow_model_ref=_ref(req.flow_model_ref))
        elif req.command == "begin_implementation":
            cmd = BeginImplementation(
                actor=req.actor,
                expected_touched_scope=req.expected_touched_scope)
        elif req.command == "record_reindex":
            if not req.final_canonical_revision:
                raise DesignLifecycleError(
                    "final_revision_required", "record_reindex needs a revision")
            cmd = RecordReindex(
                actor=req.actor,
                final_canonical_revision=req.final_canonical_revision,
                actual_changed_files=tuple(req.actual_changed_files),
                actual_changed_symbols=tuple(req.actual_changed_symbols),
                actual_evidence=tuple(req.actual_evidence))
        elif req.command == "run_reindex":
            cmd = RunReindex(
                actor=req.actor,
                actual_evidence_selectors=tuple(req.actual_evidence))
        elif req.command == "evaluate_expected_actual":
            cmd = EvaluateExpectedActual(actor=req.actor)
        elif req.command == "verify":
            cmd = VerifyChange(actor=req.actor,
                               results=tuple(req.verification_results))
        elif req.command == "close":
            cmd = CloseChange(actor=req.actor,
                              rule_versions=req.rule_versions)
        else:
            cmd = AbandonChange(actor=req.actor, reason=req.reason)
        if req.change_version is not None:
            cmd = replace(cmd, expected_version=req.change_version)
        service = DesignLifecycleService(store)
        if req.expected_design_revision is not None and (
            service.get_change(change_id).design_revision.id != req.expected_design_revision
        ):
            raise DesignLifecycleError(
                "stale_design_revision", "design revision has advanced")
        result = service.apply_command(change_id, cmd).to_dict()
        if req.command == "design_mutation":
            result["mutation_result"] = service.receipts(change_id)[-1].payload["result"]
            result["write_surface"] = {
                "status": "DEPRECATED_COMPATIBILITY",
                "replacement": "mutate_flow_design for revision-bound Flow MCP writes",
                "required_context": ["change_id", "expected_design_revision",
                                     "change_version", "stable_id"],
            }
        return result
    except DesignLifecycleError as exc:
        raise _error(exc) from exc
