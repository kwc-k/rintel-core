"""Flow endpoints — P2-FLOW0 software circuit (SPEC-P2 §18, minimal surface).

Route bodies are thin: all domain logic lives in FlowService /
FlowProjectionService / FlowValidationService / CodeWritebackService (§20).
FlowError -> 4xx unified envelope; unknown errors -> 500.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...flow.projection import FlowProjectionService
from ...flow.service import FlowService
from ...flow.validation import FlowValidationService
from ...flow.writeback import CodeWritebackService
from ...store import FlowError, Store
from ..deps import build_store, get_store, resolve_snapshot
from ..errors import ApiError
from .design_write import legacy_design_write_deprecated

router = APIRouter(prefix="/flows", tags=["flows"])


def _flow_error(exc: FlowError) -> ApiError:
    status = 404 if exc.code in ("flow_not_found", "block_not_found",
                                 "port_not_found", "symbol_not_found",
                                 "workspace_not_found",
                                 "component_not_found", "repo_not_found",
                                 "repo_not_indexed") else 400
    return ApiError(status, exc.code, exc.message, exc.details)


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------
class FromSymbolRequest(BaseModel):
    repo_id: str
    snapshot_id: Optional[str] = None
    symbol: str                      # canonical id or qname
    name: Optional[str] = None
    include_callees: bool = True
    workspace_id: Optional[str] = None
    architecture_model_id: Optional[str] = None


class FromComponentRequest(BaseModel):
    workspace_id: str
    model_id: str
    component_id: str
    name: Optional[str] = None


class BlankFlowRequest(BaseModel):
    """TOPO-EDITOR-UX0 §1: create a completely empty design netlist.
    The repo must be indexed (a snapshot exists); no evidence is read
    and nothing canonical is touched — the flow is pure TO-BE design."""
    repo_id: str
    name: Optional[str] = None
    snapshot_id: Optional[str] = None


class FlowPatch(BaseModel):
    name: Optional[str] = None
    layout: Optional[dict] = None
    meta: Optional[dict] = None


class BlockCreate(BaseModel):
    kind: str = "proposed"
    name: str = Field(min_length=1)
    state: str = "proposed"
    parent_block_id: Optional[str] = None
    code: Optional[str] = None
    meta: Optional[dict] = None


class BlockPatch(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    state: Optional[str] = None
    parent_block_id: Optional[str] = None
    clear_parent: bool = False
    meta: Optional[dict] = None


class PortCreate(BaseModel):
    block_id: str
    name: str = Field(min_length=1)
    direction: str
    semantic_kind: str
    code_type: Optional[str] = None
    position_order: int = 0
    meta: Optional[dict] = None


class PortPatch(BaseModel):
    name: Optional[str] = None
    semantic_kind: Optional[str] = None
    code_type: Optional[str] = None
    clear_type: bool = False
    position_order: Optional[int] = None
    meta: Optional[dict] = None


class NetCreate(BaseModel):
    source_port_id: str
    target_port_id: str
    kind: str = "control"
    label: Optional[str] = None
    meta: Optional[dict] = None


class NetPatch(BaseModel):
    kind: Optional[str] = None
    label: Optional[str] = None
    meta: Optional[dict] = None
    clear_label: bool = False


class AgentActionRecord(BaseModel):
    """TOPO-EDITOR-UX0 §22: agent provenance — design-plane only,
    never written into canonical evidence tables."""
    agent_action_id: str
    intent: str
    prompt: Optional[str] = None
    affected_design_ids: list[str] = []
    before: Optional[dict] = None
    after: Optional[dict] = None
    ts: Optional[int] = None


class CompositeCreate(BaseModel):
    name: str = Field(min_length=1)
    block_ids: list[str] = Field(min_length=2)


class WritebackRequest(BaseModel):
    block_id: str
    code: Optional[str] = None


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------
@router.post("/blank")
def flows_blank(req: BlankFlowRequest,
               store: Store = Depends(get_store)) -> dict:
    """TOPO-EDITOR-UX0 §1: blank software circuit (empty SoftwareNetlist).
    Design-plane only: creates a FlowModel with no blocks/ports/nets.
    Every object a user then adds carries state=proposed / truth=DESIGN.
    """
    legacy_design_write_deprecated()
    try:
        sid = resolve_snapshot(store, req.repo_id, req.snapshot_id)
        flow = FlowService(store).create_flow(
            req.repo_id, req.name or "Untitled Topology", sid)
        return FlowService(store).get_flow(flow["id"])
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/from-symbol")
def flows_from_symbol(req: FromSymbolRequest,
                      store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        sid = resolve_snapshot(store, req.repo_id, req.snapshot_id)
        return FlowProjectionService(store).from_symbol(
            req.repo_id, sid, req.symbol, name=req.name,
            include_callees=req.include_callees,
            workspace_id=req.workspace_id,
            architecture_model_id=req.architecture_model_id)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/from-component")
def flows_from_component(req: FromComponentRequest,
                         store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowProjectionService(store).from_component(
            req.workspace_id, req.model_id, req.component_id, name=req.name)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.get("")
def list_flows(repo_id: Optional[str] = None,
               store: Store = Depends(get_store)) -> dict:
    return {"flows": store.flow_models(repo_id)}


# ---------------------------------------------------------------------------
# flow model
# ---------------------------------------------------------------------------
@router.get("/{flow_id}")
def get_flow(flow_id: str, store: Store = Depends(get_store)) -> dict:
    try:
        return FlowService(store).get_flow(flow_id)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.patch("/{flow_id}")
def patch_flow(flow_id: str, req: FlowPatch,
               store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).update_flow(flow_id, name=req.name,
                                              layout=req.layout,
                                              meta=req.meta)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/{flow_id}/agent-actions")
def record_agent_action(flow_id: str, req: AgentActionRecord,
                        store: Store = Depends(get_store)) -> dict:
    """TOPO-EDITOR-UX0 §22: persist one agent provenance record on the
    design model (meta_json.log.agent_actions).  Never touches evidence."""
    legacy_design_write_deprecated()
    try:
        return FlowService(store).record_agent_action(flow_id, req.model_dump())
    except FlowError as exc:
        raise _flow_error(exc) from exc


# ---------------------------------------------------------------------------
# blocks / ports / nets
# ---------------------------------------------------------------------------
@router.post("/{flow_id}/blocks")
def create_block(flow_id: str, req: BlockCreate,
                 store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).add_block(
            flow_id, req.kind, req.name, state=req.state,
            parent_block_id=req.parent_block_id, code=req.code,
            meta=req.meta)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.patch("/{flow_id}/blocks/{block_id}")
def patch_block(flow_id: str, block_id: str, req: BlockPatch,
                store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).update_block(
            flow_id, block_id, name=req.name, code=req.code, state=req.state,
            parent_block_id=req.parent_block_id, clear_parent=req.clear_parent,
            meta=req.meta)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.delete("/{flow_id}/blocks/{block_id}")
def delete_block(flow_id: str, block_id: str,
                 store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).delete_block(flow_id, block_id)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/{flow_id}/ports")
def create_port(flow_id: str, req: PortCreate,
                store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).add_port(
            flow_id, block_id=req.block_id, name=req.name,
            direction=req.direction, semantic_kind=req.semantic_kind,
            code_type=req.code_type, position_order=req.position_order,
            meta=req.meta)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.patch("/{flow_id}/ports/{port_id}")
def patch_port(flow_id: str, port_id: str, req: PortPatch,
               store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).update_port(
            flow_id, port_id, name=req.name, semantic_kind=req.semantic_kind,
            code_type=req.code_type, clear_type=req.clear_type,
            position_order=req.position_order, meta=req.meta)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.delete("/{flow_id}/ports/{port_id}")
def delete_port(flow_id: str, port_id: str,
                store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).delete_port(flow_id, port_id)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/{flow_id}/nets")
def create_net(flow_id: str, req: NetCreate,
               store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).add_net(
            flow_id, source_port_id=req.source_port_id,
            target_port_id=req.target_port_id, kind=req.kind,
            label=req.label, meta=req.meta)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.patch("/{flow_id}/nets/{net_id}")
def patch_net(flow_id: str, net_id: str, req: NetPatch,
              store: Store = Depends(get_store)) -> dict:
    """TOPO-EDITOR-UX0 §4: edit connection (kind / label / guard+timing
    meta).  Design nets only — derived (evidence-projected) nets are
    read-only and rejected here."""
    legacy_design_write_deprecated()
    try:
        return FlowService(store).update_net(
            flow_id, net_id, kind=req.kind, label=req.label,
            meta=req.meta, clear_label=req.clear_label)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.delete("/{flow_id}/nets/{net_id}")
def delete_net(flow_id: str, net_id: str,
               store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).delete_net(flow_id, net_id)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/{flow_id}/composite")
def create_composite(flow_id: str, req: CompositeCreate,
                     store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowService(store).create_composite(flow_id, req.name,
                                                   req.block_ids)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/{flow_id}/expand")
def expand_neighbors(flow_id: str, store: Store = Depends(get_store)) -> dict:
    legacy_design_write_deprecated()
    try:
        return FlowProjectionService(store).expand(flow_id)
    except FlowError as exc:
        raise _flow_error(exc) from exc


# ---------------------------------------------------------------------------
# writeback (§13/§14) + validation (§16)
# ---------------------------------------------------------------------------
@router.post("/{flow_id}/synthesis/{mode}")
def flow_synthesis(flow_id: str, mode: str,
                   store: Store = Depends(get_store)) -> dict:
    """SYNTHESIS0 (spec §23/§24): plan → preview → apply as three explicit
    steps.  `apply` writes through the synthesis apply path and reindexes —
    canonical truth only changes after the reindex.
    """
    from ...synthesis.flow_adapter import synthesize_flow
    if mode not in ("plan", "preview", "apply"):
        raise ApiError(404, "synthesis_mode_not_found",
                       f"unknown synthesis mode '{mode}'", {})
    if mode == "apply":
        legacy_design_write_deprecated()
    try:
        return synthesize_flow(store, flow_id, mode)
    except FlowError as exc:
        raise _flow_error(exc)


@router.post("/{flow_id}/lvs")
def flow_lvs(flow_id: str, store: Store = Depends(get_store)) -> dict:
    """SOFTWARE-LVS1: design SoftwareNetlist vs canonical code topology.

    Read-only comparison (§2): the design is the stored FlowModel; the
    code side is the canonical index + the real source tree.  Never
    mutates either side.
    """
    from ...lvs.flow_runner import lvs_flow
    try:
        return lvs_flow(store, flow_id)
    except FlowError as exc:
        raise _flow_error(exc)


@router.post("/{flow_id}/writeback/preview")
def writeback_preview(flow_id: str, req: WritebackRequest,
                      store: Store = Depends(get_store)) -> dict:
    try:
        return CodeWritebackService(store).preview(flow_id, req.block_id,
                                                   code=req.code)
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/{flow_id}/writeback/apply")
def writeback_apply(flow_id: str, req: WritebackRequest,
                    store: Store = Depends(get_store),
                    request: Request = None) -> dict:
    legacy_design_write_deprecated()
    try:
        # reindexing uses its own store connection (Indexer lifecycle);
        # the request store stays untouched.  The factory comes from app
        # state so tests/embedded deployments bind the same backend.
        factory = getattr(request.app.state, "store_factory", build_store)
        idx_store = factory()
        try:
            return CodeWritebackService(store).apply(flow_id, req.block_id,
                                                     code=req.code,
                                                     store_factory=lambda:
                                                     idx_store)
        finally:
            idx_store.close()
    except FlowError as exc:
        raise _flow_error(exc) from exc


@router.post("/{flow_id}/validate")
def validate_flow(flow_id: str, store: Store = Depends(get_store)) -> dict:
    try:
        return FlowValidationService(store).validate(flow_id)
    except FlowError as exc:
        raise _flow_error(exc) from exc
