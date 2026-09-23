// Architecture Model — pure types + pure helpers for the S3 Human
// Architecture Canvas (SPEC-P1 §8 / S3-WEB-CONTRACT §3). No Vue/Vue Flow
// imports allowed here; DTO→domain mapping lives in `src/api/arch.ts`.

export type ArchKind =
  | 'component'
  | 'subsystem'
  | 'layer'
  | 'service'
  | 'boundary'
  | 'interface'
  | 'datastore'
  | 'external_system'
  | 'group'

export type ModelKind = 'as_is' | 'proposal'

/** S4 Design state machine (SPEC-P1 §13 / S4-WEB-CONTRACT §1). */
export type ModelMode = 'as_is' | 'to_be' | 'diff'

export type RelationKind = 'DEPENDS_ON' | 'USES' | 'CALLS' | 'DATA_FLOW' | 'PROVIDES' | 'CONTAINS'

export const ARCH_KINDS: ArchKind[] = [
  'component',
  'subsystem',
  'layer',
  'service',
  'boundary',
  'interface',
  'datastore',
  'external_system',
  'group',
]

export const RELATION_KINDS: RelationKind[] = ['DEPENDS_ON', 'USES', 'CALLS', 'DATA_FLOW', 'PROVIDES', 'CONTAINS']

/** Safety guard for the human canvas (SPEC-P1 §11). */
export const ARCH_NODE_LIMIT = 500

export interface ArchWorkspace {
  id: string
  repoId: string
  name: string
  description: string | null
  createdAt: string
  updatedAt: string
}

export interface ArchModel {
  id: string
  workspaceId: string
  kind: ModelKind
  name: string
  description: string
  status: string
  /** fork source (proposal only) */
  parentModelId: string | null
  /** evidence snapshot pinned at fork time (proposal only) */
  baseEvidenceSnapshotId: string | null
  baselineSchemaVersion: number
  hasBaseline: boolean
  createdAt: string
  updatedAt: string
}

/** The frozen-baseline doc version (SPEC-P1 §6.1-13). */
export const BASELINE_SCHEMA_VERSION = 1

export interface ArchComponent {
  id: string
  modelId: string
  kind: ArchKind
  name: string
  description: string
  parentId: string | null
  sortOrder: number
  createdAt: string
  updatedAt: string
}

export interface ArchRelation {
  id: string
  modelId: string
  kind: RelationKind
  srcId: string
  dstId: string
  label: string | null
  createdAt: string
  updatedAt: string
}

/** Resolved evidence row attached to a mapping (null when stale/absent). */
export interface ArchMappingEntity {
  id: string
  kind: string
  name: string
  qname: string
  language: string
  path: string
  startLine?: number
  startCol?: number
  endLine?: number
  endCol?: number
  // edge-mapping variant
  srcId?: string
  dstId?: string
  confidence?: number
}

export interface ArchMapping {
  id: string
  componentId: string
  entityType: 'node' | 'edge'
  entityId: string
  evidenceSnapshotId: string | null
  note: string | null
  createdAt: string
  updatedAt: string
  stale: boolean
  entity: ArchMappingEntity | null
}

export interface ArchAnnotation {
  id: string
  entityType: 'component' | 'relation' | 'mapping' | 'workspace'
  entityId: string
  body: string
  modelId: string | null
}

export type ArchLayout = Record<string, { x: number; y: number }>

export interface ArchMappingStale {
  mappingId: string
  entityType: 'node' | 'edge'
  entityId: string
}

export interface ArchBootstrap {
  workspace: ArchWorkspace
  models: ArchModel[]
  components: ArchComponent[]
  relations: ArchRelation[]
  mappings: ArchMapping[]
  /** AS-IS layout (S3 compatibility — the S4 source of truth is `layouts`) */
  layout: ArchLayout
  /** per-model layouts (S4): modelId → positions */
  layouts: Record<string, ArchLayout>
  hasComponents: boolean
  snapshot: string | null
  mappingStaleness: ArchMappingStale[]
  annotations: ArchAnnotation[]
}

/** First (and, for AS-IS, only) AS-IS model of a bootstrap payload. */
export function asIsModelId(bootstrap: ArchBootstrap | null): string | null {
  if (!bootstrap) return null
  return bootstrap.models.find((m) => m.kind === 'as_is')?.id ?? null
}

/** Kind string for a mapping, resilient to a stale/null resolved entity.
 * Accepts both live ArchMapping rows and baseline diff mappings. */
export function mappingEntityKind(mapping: { entity: ArchMappingEntity | null; entityId: string }): string {
  if (mapping.entity && mapping.entity.kind) return mapping.entity.kind
  // Fallback: canonical ids are `node:{kind}:{...}` / `edge:{kind}:{...}`.
  const parts = mapping.entityId.split(':')
  return parts[1] ?? ''
}

export function isFileMapping(mapping: { entity: ArchMappingEntity | null; entityId: string }): boolean {
  return mappingEntityKind(mapping) === 'FILE'
}

export interface MappingCounts {
  files: number
  symbols: number
}

/**
 * Canvas counts: files = mappings whose resolved entity kind is FILE,
 * symbols = every other mapping (S3-WEB-CONTRACT §3).
 */
export function mappingCounts(mappings: ArchMapping[]): MappingCounts {
  let files = 0
  let symbols = 0
  for (const m of mappings) {
    if (isFileMapping(m)) files++
    else symbols++
  }
  return { files, symbols }
}

// ---------------------------------------------------------------------------
// S4 Design: frozen diff + structural impact types (S4-WEB-CONTRACT §3)
// ---------------------------------------------------------------------------

export type DiffBucket =
  | 'added_components'
  | 'removed_components'
  | 'modified_components'
  | 'moved_components'
  | 'added_relations'
  | 'removed_relations'
  | 'mapping_changes'

export const DIFF_BUCKETS: DiffBucket[] = [
  'added_components',
  'removed_components',
  'modified_components',
  'moved_components',
  'added_relations',
  'removed_relations',
  'mapping_changes',
]

export type DiffChange = 'added' | 'removed' | 'modified' | 'moved'

/** Mapping attached to a diff row (impact seeds source label). */
export interface DiffMapping {
  entityType: 'node' | 'edge'
  entityId: string
  note: string | null
  source: 'proposal' | 'baseline'
  stale: boolean
  entity: ArchMappingEntity | null
}

/** Diff row entity: working-side component/relation json (+ names). */
export type DiffEntity =
  | ArchComponent
  | (ArchRelation & { srcName?: string | null; dstName?: string | null })

export interface DiffRow {
  changeId: string // "{bucket}:{seq}"
  bucket: DiffBucket
  change: DiffChange
  entity: DiffEntity | null
  baseline: Record<string, unknown> | null
  mappings: DiffMapping[]
  detail: Record<string, unknown>
}

export type DiffCounts = Record<DiffBucket, number>

export interface ArchDiff {
  modelId: string
  kind: ModelKind
  baselineSchemaVersion: number
  baseEvidenceSnapshotId: string | null
  isEmpty: boolean
  counts: DiffCounts
  rows: DiffRow[]
}

export interface ImpactChainItem {
  path: string
  symbolCount: number
  chain: string[]
  /** location of the file node (null for synthetic paths) */
  startLine?: number | null
}

export interface ImpactSymbolItem {
  id: string
  kind: string
  name: string
  qname: string | null
  path: string | null
  chain: string[]
  /** evidence location of the affected symbol (jump-to-source) */
  startLine?: number | null
}

export interface ImpactSeed {
  entityType: 'node' | 'edge'
  entityId: string
  source: 'proposal' | 'baseline'
  entity: ArchMappingEntity | null
}

export interface ImpactChange {
  changeId: string
  bucket: DiffBucket
  entityName: string
  entityKind: string
  seeds: ImpactSeed[]
  staleSeeds: { entityType: string; entityId: string }[]
  files: ImpactChainItem[]
  symbols: ImpactSymbolItem[]
  modules: ImpactSymbolItem[]
  edges: { id: string; kind: string; srcId: string; dstId: string }[]
  truncated: boolean
}

export interface ImpactResult {
  modelId: string
  snapshotId: string | null
  budgets: {
    depth: number
    nodeBudget: number
    edgeBudget: number
    usedNodes: number
    usedEdges: number
  }
  truncated: boolean
  changes: ImpactChange[]
  totals: { files: number; symbols: number; modules: number; edges: number }
}

export interface DiffBucketMeta {
  label: string
  sign: '+' | '−' | '~' | '↳' | '±'
  cls: 'added' | 'removed' | 'modified' | 'moved'
}

/** Presentation metadata per diff bucket (UI labels, frozen in the E2E). */
export function bucketMeta(bucket: DiffBucket): DiffBucketMeta {
  switch (bucket) {
    case 'added_components':
      return { label: 'Added Components', sign: '+', cls: 'added' }
    case 'removed_components':
      return { label: 'Removed Components', sign: '−', cls: 'removed' }
    case 'modified_components':
      return { label: 'Modified Components', sign: '~', cls: 'modified' }
    case 'moved_components':
      return { label: 'Moved Components', sign: '↳', cls: 'moved' }
    case 'added_relations':
      return { label: 'Added Relations', sign: '+', cls: 'added' }
    case 'removed_relations':
      return { label: 'Removed Relations', sign: '−', cls: 'removed' }
    case 'mapping_changes':
      return { label: 'Mapping Changes', sign: '±', cls: 'modified' }
  }
}

/** Diff badge class for a working-side node on the DIFF canvas. */
export function nodeDiffClass(row: DiffRow | undefined): string | null {
  if (!row) return null
  switch (row.bucket) {
    case 'added_components':
      return 'vf-diff-added'
    case 'modified_components':
      return 'vf-diff-modified'
    case 'moved_components':
      return 'vf-diff-moved'
    default:
      return null
  }
}

export function nodeDiffBadge(row: DiffRow | undefined): 'modified' | 'moved' | null {
  if (!row) return null
  if (row.bucket === 'modified_components') return 'modified'
  if (row.bucket === 'moved_components') return 'moved'
  return null
}
