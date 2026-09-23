// Software Circuit API client (SPEC-P2 §18 minimal surface).
// The server returns store-shaped (snake_case) rows; this module maps them
// onto the camelCase domain model at the API boundary (single mapping point).
import { apiFetch } from './client'
import { applyDesignMutation } from './design-lifecycle'
import type {
  FlowDto, FlowNet, FlowPort, FlowBlock, FlowModel, ValidateReport,
  WritebackPreview, WritebackResult,
} from '../domain/flow'

const SNAKE: Record<string, string> = {
  flow_model_id: 'flowModelId',
  workspace_id: 'workspaceId',
  architecture_model_id: 'architectureModelId',
  repo_id: 'repoId',
  scope_symbol_id: 'scopeSymbolId',
  root_block_id: 'rootBlockId',
  snapshot_id: 'snapshotId',
  created_at: 'createdAt',
  updated_at: 'updatedAt',
  parent_block_id: 'parentBlockId',
  block_id: 'blockId',
  canonical_symbol_id: 'canonicalSymbolId',
  binding_kind: 'bindingKind',
  code_type: 'codeType',
  semantic_kind: 'semanticKind',
  position_order: 'positionOrder',
  source_port_id: 'sourcePortId',
  target_port_id: 'targetPortId',
  source_block_id: 'sourceBlockId',
  target_block_id: 'targetBlockId',
  meta_json: 'metaJson',
  missing_calls: 'missingCalls',
  unexpected_calls: 'unexpectedCalls',
  expected_call_pairs: 'expectedCallPairs',
  evidence_call_pairs: 'evidenceCallPairs',
  current_snapshot_id: 'currentSnapshotId',
  pinned_snapshot_id: 'pinnedSnapshotId',
  design_nets: 'designNets',
  checked_bindings: 'checkedBindings',
  root_path: 'rootPath',
}

function camelizeRow<T>(row: unknown): T {
  if (!row || typeof row !== 'object') return row as T
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(row as Record<string, unknown>)) {
    out[SNAKE[k] ?? k] = v
  }
  return out as T
}

function camelizeDto(raw: unknown): FlowDto {
  const d = raw as Record<string, any>
  return {
    flow: camelizeRow<FlowModel>(d.flow),
    blocks: (d.blocks ?? []).map((b: any) => ({
      ...camelizeRow<FlowBlock>(b),
      binding: b.binding ? camelizeRow(b.binding) : null,
      symbol: b.symbol ?? null,
      ports: (b.ports ?? []).map((p: any) => camelizeRow<FlowPort>(p)),
    })),
    ports: (d.ports ?? []).map((p: any) => camelizeRow<FlowPort>(p)),
    nets: (d.nets ?? []).map((n: any) => camelizeRow<FlowNet>(n)),
    bindings: (d.bindings ?? []).map((b: any) => camelizeRow(b)),
    layout: d.layout ?? {},
    repo: d.repo ? { id: d.repo.id, rootPath: d.repo.root_path } : null,
    notes: d.notes ?? [],
  }
}

function camelizeValidate(raw: unknown): ValidateReport {
  return camelizeRow<ValidateReport>(raw)
}

function camelizeResult(raw: unknown): WritebackResult {
  const r = camelizeRow<WritebackResult>(raw)
  return {
    ...r,
    binding: r.binding ? camelizeRow(r.binding) : null,
    block: r.block ? camelizeRow(r.block) : null,
  }
}

export interface BlockCreateInput {
  kind: string
  name: string
  state?: string
  parentBlockId?: string | null
  code?: string | null
  meta?: Record<string, unknown> | null
}

export interface PortCreateInput {
  blockId: string
  name: string
  direction: string
  semanticKind: string
  codeType?: string | null
  positionOrder?: number
  meta?: Record<string, unknown> | null
}

export interface NetCreateInput {
  sourcePortId: string
  targetPortId: string
  kind?: string
  label?: string | null
  meta?: Record<string, unknown> | null
}

export function blankFlow(input: {
  repoId: string
  name?: string | null
  snapshotId?: string | null
}): Promise<FlowDto> {
  return applyDesignMutation<FlowDto>('flow', 'create_blank', {
      repo_id: input.repoId,
      name: input.name ?? null,
      snapshot_id: input.snapshotId ?? null,
  }).then(camelizeDto)
}

export function fromSymbol(input: {
  repoId: string
  snapshotId?: string | null
  symbol: string
  name?: string | null
  includeCallees?: boolean
  workspaceId?: string | null
  architectureModelId?: string | null
}): Promise<FlowDto> {
  return applyDesignMutation<FlowDto>('flow', 'from_symbol', {
      repo_id: input.repoId,
      snapshot_id: input.snapshotId ?? null,
      symbol: input.symbol,
      name: input.name ?? null,
      include_callees: input.includeCallees ?? true,
      workspace_id: input.workspaceId ?? null,
      architecture_model_id: input.architectureModelId ?? null,
  }).then(camelizeDto)
}

export function fromComponent(input: {
  workspaceId: string
  modelId: string
  componentId: string
  name?: string | null
}): Promise<FlowDto> {
  return applyDesignMutation<FlowDto>('flow', 'from_component', {
      workspace_id: input.workspaceId,
      model_id: input.modelId,
      component_id: input.componentId,
      name: input.name ?? null,
  }).then(camelizeDto)
}

export function listFlows(repoId?: string | null): Promise<{ flows: FlowModel[] }> {
  const qs = repoId ? `?repo_id=${encodeURIComponent(repoId)}` : ''
  return apiFetch<{ flows: FlowModel[] }>(`/flows${qs}`)
}

export function getFlow(flowId: string): Promise<FlowDto> {
  return apiFetch<FlowDto>(`/flows/${flowId}`).then(camelizeDto)
}

export function patchFlow(
  flowId: string,
  body: {
    name?: string
    layout?: Record<string, { x: number; y: number }>
    meta?: Record<string, unknown> | null
  },
): Promise<FlowDto> {
  return applyDesignMutation<FlowDto>('flow', 'update_flow', {
    flow_id: flowId, ...body,
  }).then(camelizeDto)
}

export function createBlock(flowId: string, input: BlockCreateInput): Promise<FlowBlock> {
  return applyDesignMutation<FlowBlock>('flow', 'add_block', {
      flow_id: flowId,
      kind: input.kind,
      name: input.name,
      state: input.state ?? 'proposed',
      parent_block_id: input.parentBlockId ?? null,
      code: input.code ?? null,
      meta: input.meta ?? null,
  })
}

export function patchBlock(flowId: string, blockId: string, body: {
  name?: string; code?: string; state?: string; parentBlockId?: string | null
  clearParent?: boolean; meta?: Record<string, unknown> | null
}): Promise<FlowBlock> {
  return applyDesignMutation<FlowBlock>('flow', 'update_block', {
      flow_id: flowId, block_id: blockId,
      name: body.name ?? null,
      code: body.code ?? null,
      state: body.state ?? null,
      parent_block_id: body.parentBlockId ?? null,
      clear_parent: body.clearParent ?? false,
      meta: body.meta ?? null,
  })
}

export function deleteBlock(flowId: string, blockId: string): Promise<unknown> {
  return applyDesignMutation('flow', 'delete_block', { flow_id: flowId, block_id: blockId })
}

export function createPort(flowId: string, input: PortCreateInput): Promise<FlowPort> {
  return applyDesignMutation<FlowPort>('flow', 'add_port', {
      flow_id: flowId,
      block_id: input.blockId,
      name: input.name,
      direction: input.direction,
      semantic_kind: input.semanticKind,
      code_type: input.codeType ?? null,
      position_order: input.positionOrder ?? 0,
      meta: input.meta ?? null,
  })
}

export function patchPort(flowId: string, portId: string, body: {
  name?: string; semanticKind?: string; codeType?: string | null
  clearType?: boolean; positionOrder?: number; meta?: Record<string, unknown> | null
}): Promise<FlowPort> {
  return applyDesignMutation<FlowPort>('flow', 'update_port', {
      flow_id: flowId, port_id: portId,
      name: body.name ?? null,
      semantic_kind: body.semanticKind ?? null,
      code_type: body.codeType ?? null,
      clear_type: body.clearType ?? false,
      position_order: body.positionOrder ?? null,
      meta: body.meta ?? null,
  })
}

export function deletePort(flowId: string, portId: string): Promise<unknown> {
  return applyDesignMutation('flow', 'delete_port', { flow_id: flowId, port_id: portId })
}

export function createNet(flowId: string, input: NetCreateInput): Promise<FlowNet> {
  return applyDesignMutation<FlowNet>('flow', 'add_net', {
      flow_id: flowId,
      source_port_id: input.sourcePortId,
      target_port_id: input.targetPortId,
      kind: input.kind ?? 'control',
      label: input.label ?? null,
      meta: input.meta ?? null,
  })
}

export function patchNet(flowId: string, netId: string, body: {
  kind?: string; label?: string | null; clearLabel?: boolean
  meta?: Record<string, unknown> | null
}): Promise<FlowNet> {
  return applyDesignMutation<FlowNet>('flow', 'update_net', {
      flow_id: flowId, net_id: netId,
      kind: body.kind ?? null,
      label: body.label ?? null,
      clear_label: body.clearLabel ?? false,
      meta: body.meta ?? null,
  })
}

export function recordAgentAction(flowId: string, action: {
  agentActionId: string
  intent: string
  prompt?: string | null
  affectedDesignIds?: string[]
  before?: Record<string, unknown> | null
  after?: Record<string, unknown> | null
  ts?: number | null
}): Promise<{ recorded: string; total: number }> {
  return applyDesignMutation<{ recorded: string; total: number }>(
    'flow', 'record_agent_action', { flow_id: flowId, action: {
        agent_action_id: action.agentActionId,
        intent: action.intent,
        prompt: action.prompt ?? null,
        affected_design_ids: action.affectedDesignIds ?? [],
        before: action.before ?? null,
        after: action.after ?? null,
        ts: action.ts ?? null,
    } })
}

export function deleteNet(flowId: string, netId: string): Promise<unknown> {
  return applyDesignMutation('flow', 'delete_net', { flow_id: flowId, net_id: netId })
}

export function createComposite(
  flowId: string, name: string, blockIds: string[],
): Promise<FlowDto> {
  return applyDesignMutation<FlowDto>('flow', 'create_composite', {
    flow_id: flowId, name, block_ids: blockIds,
  }).then(camelizeDto)
}

export function expandFlow(flowId: string): Promise<FlowDto> {
  return applyDesignMutation<FlowDto>('flow', 'expand', { flow_id: flowId })
    .then(camelizeDto)
}

export function writebackPreview(
  flowId: string, blockId: string, code?: string | null,
): Promise<WritebackPreview> {
  return apiFetch<WritebackPreview>(`/flows/${flowId}/writeback/preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ block_id: blockId, code: code ?? null }),
  })
}

export function writebackApply(
  flowId: string, blockId: string, code?: string | null,
): Promise<WritebackResult> {
  void flowId; void blockId; void code; void camelizeResult
  return Promise.reject(new Error(
    'Writeback apply requires lifecycle implementation orchestration; BUILD-RECOVERY0 is out of scope',
  ))
}

export function validateFlow(flowId: string): Promise<ValidateReport> {
  return apiFetch<ValidateReport>(`/flows/${flowId}/validate`, { method: 'POST' })
    .then(camelizeValidate)
}
