// Typed architecture-plane API (S3-WEB-CONTRACT §2). All server rows arrive
// snake_case and are mapped to the camelCase domain model in
// `src/domain/architecture.ts`.

import { apiGet, apiPost, qs } from './client'
import { applyDesignMutation } from './design-lifecycle'
import type {
  ArchBootstrap,
  ArchComponent,
  ArchDiff,
  ArchKind,
  ArchLayout,
  ArchMapping,
  ArchMappingEntity,
  ArchMappingStale,
  ArchModel,
  ArchRelation,
  ArchWorkspace,
  DiffBucket,
  DiffMapping,
  DiffRow,
  ImpactResult,
  ModelKind,
  RelationKind,
} from '../domain/architecture'

// --- raw (snake_case) DTOs -------------------------------------------------

interface RawWorkspace {
  id: string
  repo_id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
}

interface RawModel {
  id: string
  workspace_id: string
  kind: string
  name: string
  description: string
  status: string
  parent_model_id: string | null
  base_evidence_snapshot_id: string | null
  baseline_schema_version: number
  has_baseline: boolean
  created_at: string
  updated_at: string
}

interface RawComponent {
  id: string
  model_id: string
  kind: string
  name: string
  description: string
  parent_id: string | null
  sort_order: number
  created_at: string
  updated_at: string
}

interface RawRelation {
  id: string
  model_id: string
  kind: string
  src_id: string
  dst_id: string
  label: string | null
  created_at: string
  updated_at: string
}

interface RawMappingEntity {
  id: string
  kind: string
  name: string
  qname: string
  language: string
  path: string
  start_line?: number
  start_col?: number
  end_line?: number
  end_col?: number
  src_id?: string
  dst_id?: string
  confidence?: number
}

interface RawMapping {
  id: string
  component_id: string
  entity_type: string
  entity_id: string
  evidence_snapshot_id: string | null
  note: string | null
  created_at: string
  updated_at: string
  stale: boolean
  entity: RawMappingEntity | null
}

interface RawBootstrap {
  workspace: RawWorkspace
  models: RawModel[]
  components: RawComponent[]
  relations: RawRelation[]
  mappings: RawMapping[]
  layout: ArchLayout | null
  layouts: Record<string, ArchLayout> | null
  has_components: boolean
  snapshot: string | null
  mapping_staleness: { mapping_id: string; entity_type: string; entity_id: string }[]
  annotations: unknown[]
}

// --- S4 design-plane raw DTOs ------------------------------------------------

interface RawDiffRow {
  change_id: string
  bucket: string
  change: string
  entity: RawComponent | RawRelation | null
  baseline: Record<string, unknown> | null
  mappings: (RawMapping & { source?: string })[]
  detail: Record<string, unknown>
}

interface RawDiff {
  model_id: string
  kind: string
  baseline_schema_version: number
  base_evidence_snapshot_id: string | null
  is_empty: boolean
  counts: Record<string, number>
  rows: RawDiffRow[]
}

interface RawImpact {
  model_id: string
  snapshot_id: string | null
  budgets: { depth: number; node_budget: number; edge_budget: number; used_nodes: number; used_edges: number }
  truncated: boolean
  changes: RawImpactChange[]
  totals: { files: number; symbols: number; modules: number; edges: number }
}

interface RawImpactChange {
  change_id: string
  bucket: string
  entity_name: string
  entity_kind: string
  seeds: { entity_type: string; entity_id: string; source?: string; entity: RawMappingEntity | null }[]
  stale_seeds: { entity_type: string; entity_id: string }[]
  files: { path: string; symbol_count: number; chain: string[]; start_line?: number | null }[]
  symbols: RawImpactSymbol[]
  modules: RawImpactSymbol[]
  edges: { id: string; kind: string; src_id: string; dst_id: string }[]
  truncated: boolean
}

interface RawImpactSymbol {
  id: string
  kind: string
  name: string
  qname: string | null
  path: string | null
  chain: string[]
  start_line?: number | null
}

// --- mappers ----------------------------------------------------------------

function mapWorkspace(raw: RawWorkspace): ArchWorkspace {
  return {
    id: raw.id,
    repoId: raw.repo_id,
    name: raw.name,
    description: raw.description ?? null,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

function mapModel(raw: RawModel): ArchModel {
  return {
    id: raw.id,
    workspaceId: raw.workspace_id,
    kind: (raw.kind === 'proposal' ? 'proposal' : 'as_is') as ModelKind,
    name: raw.name,
    description: raw.description ?? '',
    status: raw.status ?? 'active',
    parentModelId: raw.parent_model_id ?? null,
    baseEvidenceSnapshotId: raw.base_evidence_snapshot_id ?? null,
    baselineSchemaVersion: raw.baseline_schema_version ?? 0,
    hasBaseline: raw.has_baseline ?? false,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

function mapComponent(raw: RawComponent): ArchComponent {
  return {
    id: raw.id,
    modelId: raw.model_id,
    kind: raw.kind as ArchKind,
    name: raw.name,
    description: raw.description ?? '',
    parentId: raw.parent_id ?? null,
    sortOrder: raw.sort_order ?? 0,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

function mapRelation(raw: RawRelation): ArchRelation {
  return {
    id: raw.id,
    modelId: raw.model_id,
    kind: raw.kind as RelationKind,
    srcId: raw.src_id,
    dstId: raw.dst_id,
    label: raw.label ?? null,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  }
}

function mapMappingEntity(raw: RawMappingEntity | null): ArchMappingEntity | null {
  if (!raw) return null
  return {
    id: raw.id,
    kind: raw.kind,
    name: raw.name,
    qname: raw.qname,
    language: raw.language,
    path: raw.path,
    startLine: raw.start_line,
    startCol: raw.start_col,
    endLine: raw.end_line,
    endCol: raw.end_col,
    srcId: raw.src_id,
    dstId: raw.dst_id,
    confidence: raw.confidence,
  }
}

function mapMapping(raw: RawMapping): ArchMapping {
  return {
    id: raw.id,
    componentId: raw.component_id,
    entityType: raw.entity_type === 'edge' ? 'edge' : 'node',
    entityId: raw.entity_id,
    evidenceSnapshotId: raw.evidence_snapshot_id ?? null,
    note: raw.note ?? null,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    stale: raw.stale ?? false,
    entity: mapMappingEntity(raw.entity),
  }
}

export function mapBootstrap(raw: RawBootstrap): ArchBootstrap {
  return {
    workspace: mapWorkspace(raw.workspace),
    models: (raw.models ?? []).map(mapModel),
    components: (raw.components ?? []).map(mapComponent),
    relations: (raw.relations ?? []).map(mapRelation),
    mappings: (raw.mappings ?? []).map(mapMapping),
    layout: raw.layout ?? {},
    layouts: raw.layouts ?? {},
    hasComponents: raw.has_components ?? false,
    snapshot: raw.snapshot ?? null,
    mappingStaleness: (raw.mapping_staleness ?? []).map(
      (s): ArchMappingStale => ({
        mappingId: s.mapping_id,
        entityType: s.entity_type === 'edge' ? 'edge' : 'node',
        entityId: s.entity_id,
      }),
    ),
    annotations: [],
  }
}

// --- S4 design-plane mappers -------------------------------------------------

function isRelationRow(x: RawComponent | RawRelation | null): x is RawRelation {
  return x !== null && 'src_id' in x
}

function mapDiffMapping(raw: RawMapping & { source?: string }): DiffMapping {
  const m = mapMapping(raw)
  return {
    entityType: m.entityType,
    entityId: m.entityId,
    note: m.note,
    source: raw.source === 'baseline' ? 'baseline' : 'proposal',
    stale: m.stale,
    entity: m.entity,
  }
}

const DIFF_BUCKET_SET = new Set<string>([
  'added_components',
  'removed_components',
  'modified_components',
  'moved_components',
  'added_relations',
  'removed_relations',
  'mapping_changes',
])

function mapDiffRow(raw: RawDiffRow): DiffRow {
  let entity: DiffRow['entity'] = null
  if (raw.entity) {
    if (isRelationRow(raw.entity)) {
      entity = {
        ...mapRelation(raw.entity),
        srcName: (raw.entity as RawRelation & { src_name?: string | null }).src_name ?? null,
        dstName: (raw.entity as RawRelation & { dst_name?: string | null }).dst_name ?? null,
      }
    } else {
      entity = mapComponent(raw.entity)
    }
  }
  return {
    changeId: raw.change_id,
    bucket: (DIFF_BUCKET_SET.has(raw.bucket) ? raw.bucket : 'added_components') as DiffBucket,
    change: raw.change as DiffRow['change'],
    entity,
    baseline: raw.baseline,
    mappings: (raw.mappings ?? []).map(mapDiffMapping),
    detail: raw.detail ?? {},
  }
}

export function mapDiff(raw: RawDiff): ArchDiff {
  const rows = (raw.rows ?? []).map(mapDiffRow)
  const counts = {} as ArchDiff['counts']
  for (const bucket of DIFF_BUCKET_SET) {
    counts[bucket as DiffBucket] = raw.counts?.[bucket] ?? rows.filter((r) => r.bucket === bucket).length
  }
  return {
    modelId: raw.model_id,
    kind: raw.kind === 'proposal' ? 'proposal' : 'as_is',
    baselineSchemaVersion: raw.baseline_schema_version ?? 0,
    baseEvidenceSnapshotId: raw.base_evidence_snapshot_id ?? null,
    isEmpty: raw.is_empty ?? rows.length === 0,
    counts,
    rows,
  }
}

function mapImpact(raw: RawImpact): ImpactResult {
  return {
    modelId: raw.model_id,
    snapshotId: raw.snapshot_id ?? null,
    budgets: {
      depth: raw.budgets?.depth ?? 2,
      nodeBudget: raw.budgets?.node_budget ?? 2000,
      edgeBudget: raw.budgets?.edge_budget ?? 4000,
      usedNodes: raw.budgets?.used_nodes ?? 0,
      usedEdges: raw.budgets?.used_edges ?? 0,
    },
    truncated: raw.truncated ?? false,
    changes: (raw.changes ?? []).map((c) => ({
      changeId: c.change_id,
      bucket: (DIFF_BUCKET_SET.has(c.bucket) ? c.bucket : 'added_components') as DiffBucket,
      entityName: c.entity_name,
      entityKind: c.entity_kind,
      seeds: (c.seeds ?? []).map((s) => ({
        entityType: s.entity_type === 'edge' ? 'edge' : 'node',
        entityId: s.entity_id,
        source: s.source === 'baseline' ? 'baseline' : 'proposal',
        entity: mapMappingEntity(s.entity ?? null),
      })),
      staleSeeds: (c.stale_seeds ?? []).map((s) => ({ entityType: s.entity_type, entityId: s.entity_id })),
      files: (c.files ?? []).map((f) => ({ path: f.path, symbolCount: f.symbol_count, chain: f.chain ?? [], startLine: f.start_line ?? null })),
      symbols: (c.symbols ?? []).map(mapImpactSymbol),
      modules: (c.modules ?? []).map(mapImpactSymbol),
      edges: (c.edges ?? []).map((e) => ({ id: e.id, kind: e.kind, srcId: e.src_id, dstId: e.dst_id })),
      truncated: c.truncated ?? false,
    })),
    totals: raw.totals ?? { files: 0, symbols: 0, modules: 0, edges: 0 },
  }
}

function mapImpactSymbol(raw: RawImpactSymbol): ImpactResult['changes'][number]['symbols'][number] {
  return {
    id: raw.id,
    kind: raw.kind,
    name: raw.name,
    qname: raw.qname ?? null,
    path: raw.path ?? null,
    chain: raw.chain ?? [],
    startLine: raw.start_line ?? null,
  }
}

// --- request payloads -------------------------------------------------------

export interface CreateWorkspaceInput {
  repoId: string
  name: string
  description?: string
}

export interface CreateComponentInput {
  modelId: string
  kind: ArchKind
  name: string
  parentId?: string | null
  description?: string
}

export interface UpdateComponentInput {
  modelId: string
  name?: string
  description?: string
  kind?: ArchKind
  parentId?: string | null
}

export interface CreateComponentBatchInput {
  modelId: string
  kind: ArchKind
  name: string
  entityIds: string[]
  parentId?: string
  description?: string
  note?: string
}

export interface CreateRelationInput {
  modelId: string
  kind: RelationKind
  srcId: string
  dstId: string
  label?: string
}

export interface UpdateRelationInput {
  modelId: string
  kind?: RelationKind
  label?: string
}

export interface CreateMappingInput {
  componentId: string
  entityType: 'node' | 'edge'
  entityId: string
  note?: string
}

export interface CreateMappingsBatchInput {
  componentId: string
  entityIds: string[]
  note?: string
}

export interface DeleteResult {
  ok: boolean
  deleted?: { components: unknown[]; relations: unknown[]; mappings: unknown[] }
}

function enc(segment: string): string {
  return encodeURIComponent(segment)
}

export const archApi = {
  async createWorkspace(input: CreateWorkspaceInput): Promise<{ workspace: ArchWorkspace; model: ArchModel }> {
    const raw = await applyDesignMutation<{ workspace: RawWorkspace; model: RawModel }>('architecture', 'create_workspace', {
      repo_id: input.repoId,
      name: input.name,
      description: input.description,
    })
    return { workspace: mapWorkspace(raw.workspace), model: mapModel(raw.model) }
  },

  async listWorkspaces(): Promise<{ items: ArchWorkspace[]; total: number }> {
    const raw = await apiGet<{ items: RawWorkspace[]; total: number }>('/workspaces')
    return { items: (raw.items ?? []).map(mapWorkspace), total: raw.total }
  },

  async getBootstrap(workspaceId: string, snapshot?: string): Promise<ArchBootstrap> {
    const raw = await apiGet<RawBootstrap>(`/workspaces/${enc(workspaceId)}${qs({ snapshot })}`)
    return mapBootstrap(raw)
  },

  async createComponent(workspaceId: string, input: CreateComponentInput): Promise<ArchComponent> {
    const raw = await applyDesignMutation<RawComponent>('architecture', 'create_component', {
      workspace_id: workspaceId,
      model_id: input.modelId,
      kind: input.kind,
      name: input.name,
      parent_id: input.parentId ?? undefined,
      description: input.description,
    })
    return mapComponent(raw)
  },

  async updateComponent(workspaceId: string, componentId: string, input: UpdateComponentInput): Promise<ArchComponent> {
    const raw = await applyDesignMutation<RawComponent>('architecture', 'update_component', {
      workspace_id: workspaceId,
      component_id: componentId,
      model_id: input.modelId,
      name: input.name,
      description: input.description,
      kind: input.kind,
      parent_id: input.parentId,
      clear_parent: input.parentId === null,
    })
    return mapComponent(raw)
  },

  async deleteComponent(workspaceId: string, componentId: string, opts: { modelId: string; subtree?: boolean }): Promise<DeleteResult> {
    const deleted = await applyDesignMutation<DeleteResult['deleted']>('architecture', 'delete_component', {
      workspace_id: workspaceId, component_id: componentId,
      model_id: opts.modelId, subtree: opts.subtree ?? false,
    })
    return { ok: true, deleted }
  },

  async createComponentBatch(workspaceId: string, input: CreateComponentBatchInput): Promise<{ component: ArchComponent; mappings: ArchMapping[] }> {
    const raw = await applyDesignMutation<{ component: RawComponent; mappings: RawMapping[] }>(
      'architecture', 'batch_component', {
        workspace_id: workspaceId,
        model_id: input.modelId,
        kind: input.kind,
        name: input.name,
        entities: input.entityIds.map((id) => [id.startsWith('edge:') ? 'edge' : 'node', id, input.note ?? '']),
        parent_id: input.parentId,
        description: input.description,
        note: input.note,
      },
    )
    return { component: mapComponent(raw.component), mappings: (raw.mappings ?? []).map(mapMapping) }
  },

  async createRelation(workspaceId: string, input: CreateRelationInput): Promise<ArchRelation> {
    const raw = await applyDesignMutation<RawRelation>('architecture', 'create_relation', {
      workspace_id: workspaceId,
      model_id: input.modelId,
      kind: input.kind,
      src_id: input.srcId,
      dst_id: input.dstId,
      label: input.label,
    })
    return mapRelation(raw)
  },

  async updateRelation(workspaceId: string, relationId: string, input: UpdateRelationInput): Promise<ArchRelation> {
    const raw = await applyDesignMutation<RawRelation>('architecture', 'update_relation', {
      workspace_id: workspaceId, relation_id: relationId,
      model_id: input.modelId,
      kind: input.kind,
      label: input.label,
    })
    return mapRelation(raw)
  },

  async deleteRelation(workspaceId: string, relationId: string, modelId: string): Promise<{ ok: boolean }> {
    await applyDesignMutation('architecture', 'delete_relation', {
      workspace_id: workspaceId, relation_id: relationId, model_id: modelId,
    })
    return { ok: true }
  },

  async createMapping(workspaceId: string, input: CreateMappingInput): Promise<ArchMapping> {
    const raw = await applyDesignMutation<{ mappings: RawMapping[] }>('architecture', 'create_mapping', {
      workspace_id: workspaceId,
      component_id: input.componentId,
      entities: [[input.entityType, input.entityId, input.note ?? '']],
    })
    return mapMapping(raw.mappings[0])
  },

  async createMappingsBatch(workspaceId: string, input: CreateMappingsBatchInput): Promise<{ mappings: ArchMapping[] }> {
    const raw = await applyDesignMutation<{ mappings: RawMapping[] }>('architecture', 'batch_mappings', {
      workspace_id: workspaceId,
      component_id: input.componentId,
      entities: input.entityIds.map((id) => [id.startsWith('edge:') ? 'edge' : 'node', id, input.note ?? '']),
    })
    return { mappings: (raw.mappings ?? []).map(mapMapping) }
  },

  async deleteMapping(workspaceId: string, mappingId: string): Promise<{ ok: boolean }> {
    await applyDesignMutation('architecture', 'delete_mapping', {
      workspace_id: workspaceId, mapping_id: mappingId,
    })
    return { ok: true }
  },

  async putLayout(workspaceId: string, modelId: string, layout: ArchLayout): Promise<{ modelId: string; updatedAt: string }> {
    const raw = await applyDesignMutation<{ model_id: string; updated_at: string }>(
      'architecture', 'put_layout', { workspace_id: workspaceId, model_id: modelId, layout },
    )
    return { modelId: raw.model_id, updatedAt: raw.updated_at }
  },

  // --- S4 design plane -------------------------------------------------------

  async forkModel(workspaceId: string, input: { kind: 'proposal'; name: string; description?: string }): Promise<{ model: ArchModel; diff: ArchDiff }> {
    const model = await applyDesignMutation<RawModel>('architecture', 'fork_proposal', {
      workspace_id: workspaceId,
      name: input.name,
      description: input.description,
    })
    const diff = await this.getModelDiff(workspaceId, model.id)
    return { model: mapModel(model), diff }
  },

  async getModelDiff(workspaceId: string, modelId: string, snapshot?: string): Promise<ArchDiff> {
    const raw = await apiGet<RawDiff>(`/workspaces/${enc(workspaceId)}/models/${enc(modelId)}/diff${qs({ snapshot })}`)
    return mapDiff(raw)
  },

  async postImpact(
    workspaceId: string,
    modelId: string,
    input: { snapshotId?: string; changeIds?: string[] },
  ): Promise<ImpactResult> {
    const raw = await apiPost<RawImpact>(`/workspaces/${enc(workspaceId)}/models/${enc(modelId)}/impact`, {
      snapshot_id: input.snapshotId,
      change_ids: input.changeIds,
    })
    return mapImpact(raw)
  },
}
