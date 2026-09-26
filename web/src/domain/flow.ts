// Software Circuit domain (SPEC-P2 §3): renderer-neutral netlist.
// Pure types + helpers; no Vue/Pinia/X6 imports (renderer-neutral §2.2).

export type FlowBlockKind = 'function' | 'object' | 'composite' | 'proposed'
export type FlowBlockState = 'existing' | 'modified' | 'proposed'
export type PortDirection = 'input' | 'output'
export type SemanticKind = 'data' | 'control' | 'event' | 'error' | 'resource'
export type NetKind = 'data' | 'control' | 'event' | 'error' | 'resource'
export type ValidateStatus = 'MATCH' | 'STALE' | 'MISMATCH' | 'UNBOUND'

export interface FlowModel {
  id: string
  workspaceId?: string | null
  architectureModelId?: string | null
  repoId: string
  name: string
  scopeSymbolId?: string | null
  rootBlockId?: string | null
  snapshotId: string
  status: string
  version: number
  createdAt: number
  updatedAt: number
  metaJson: string
}

export interface SymbolRef {
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
}

export interface BlockBinding {
  id: string
  flowModelId: string
  blockId: string
  snapshotId: string
  canonicalSymbolId: string
  bindingKind: string
}

/** Display ordinals are revision-scoped; machine writes still use stable IDs. */
export interface EdaAddress {
  revision: string
  machine_path: string
  display: { path: string; layer_ordinal?: number; node_ordinal?: number; port_ordinal?: number }
}

export interface PortContractV0 {
  version: string
  authority: 'DESIGN_ANNOTATION'
  port_id: string
  owner_node_id: string
  direction: 'IN' | 'OUT'
  ordinal: number
  name: string
  semantic_kind: string
  port_family: string
  semantic_object: string
  generic_type: string
  dtype: string
  rank: number | 'UNKNOWN'
  shape: string | Array<string | number>
  unknown_fields: string[]
}

export interface FlowPort {
  id: string
  flowModelId: string
  blockId: string
  name: string
  direction: PortDirection
  semanticKind: SemanticKind
  codeType?: string | null
  positionOrder: number
  displayAddress?: string
  edaAddress?: EdaAddress
  portContract?: PortContractV0
  expectedActual?: {
    version: 'port-expected-actual/v0'
    expected: PortContractV0
    actual: { status: 'UNKNOWN'; reason: string }
    comparison: 'UNKNOWN'
  }
}

export interface FlowBlock {
  id: string
  flowModelId: string
  parentBlockId?: string | null
  kind: FlowBlockKind
  name: string
  state: FlowBlockState
  code?: string | null
  metaJson: string
  binding?: BlockBinding | null
  symbol?: SymbolRef | null
  ports?: FlowPort[]
  displayAddress?: string
  edaAddress?: EdaAddress
}

export interface FlowNet {
  id: string
  flowModelId: string
  sourcePortId: string
  targetPortId: string
  kind: NetKind
  label?: string | null
  metaJson: string
  sourceBlockId?: string | null
  targetBlockId?: string | null
  derived: boolean
  displayAddress?: string
  edaAddress?: EdaAddress
}

export interface FlowLayoutPos {
  x: number
  y: number
}

export interface FlowDto {
  flow: FlowModel
  blocks: FlowBlock[]
  ports: FlowPort[]
  nets: FlowNet[]
  bindings: BlockBinding[]
  layout: Record<string, FlowLayoutPos>
  repo?: { id: string; rootPath: string } | null
  notes?: string[]
  eda?: { revision: string; address_contract_version: string; port_contract_version: string }
  designActivity?: {
    status: 'RECORDED' | 'UNKNOWN' | 'AMBIGUOUS'
    meaning?: 'last_completed_operation'
    change_id?: string
    design_revision?: string
    receipt_id?: string
    actor?: string
    operation?: string
    authority?: 'DESIGN_ANNOTATION'
    reason?: string
  }
}

export interface ValidateReport {
  status: ValidateStatus
  unbound: Array<{ blockId: string; blockName: string; reason: string }>
  missingCalls: Array<{ src: string; dst: string }>
  unexpectedCalls: Array<{ src: string; dst: string }>
  expectedCallPairs: number
  evidenceCallPairs: number
  flowId: string
  currentSnapshotId: string
  pinnedSnapshotId: string
  designNets: number
  checkedBindings: number
}

export interface WritebackPreview {
  mode: 'new_file' | 'replace_span'
  relpath: string
  diff: string
  content?: string
  errors?: string[]
}

export interface WritebackResult {
  ok: boolean
  mode: 'new_file' | 'replace_span'
  relpath: string
  snapshotId?: string | null
  block?: { id: string; state: string; kind: string } | null
  binding?: {
    snapshotId: string
    canonicalSymbolId: string
    node?: { id: string; kind: string; name: string; qname: string; path: string } | null
  } | null
  errors: string[]
}

export const BLOCK_KINDS: FlowBlockKind[] = ['function', 'object', 'composite', 'proposed']
export const BLOCK_STATES: FlowBlockState[] = ['existing', 'modified', 'proposed']
export const SEMANTIC_KINDS: SemanticKind[] = ['data', 'control', 'event', 'error', 'resource']

// ---------------------------------------------------------------------------
// TOPO-EDITOR-UX0 §6: Design Annotation (never Evidence) + agent proposal
// + design transaction (undo §11) + agent provenance (§22).
// ---------------------------------------------------------------------------
export interface DesignAnnotation {
  displayName?: string
  description?: string
  responsibility?: string
  notes?: string
  tags?: string[]
}

/** Parse meta_json 'design' section of a block/port. */
export function parseDesignAnnotation(metaJson: string | null | undefined): DesignAnnotation {
  if (!metaJson) return {}
  try {
    const meta = JSON.parse(metaJson) as { design?: DesignAnnotation }
    return meta.design ?? {}
  } catch {
    return {}
  }
}

/** Merge design annotation into a block meta patch (server merges). */
export function annotationMetaPatch(ann: DesignAnnotation): Record<string, unknown> {
  return { design: ann }
}

export function parseNetMeta(metaJson: string | null | undefined): {
  guard?: string
  timing?: string
  derived?: boolean
  [k: string]: unknown
} {
  if (!metaJson) return {}
  try {
    return JSON.parse(metaJson)
  } catch {
    return {}
  }
}

/** TOPO-EDITOR-UX0 §9: a curated topology patch (before/after diff). */
export interface TopologyPatchOp {
  op: 'addBlock' | 'removeBlock' | 'addPort' | 'removePort' |
    'addNet' | 'removeNet' | 'updateBlock' | 'updateNet'
  target?: string            // block/port/net id
  block?: {
    name: string
    kind: FlowBlockKind
    state: FlowBlockState
    parentBlockId?: string | null
  }
  port?: {
    blockId: string
    name: string
    direction: PortDirection
    semanticKind: SemanticKind
  }
  net?: {
    sourcePortId: string
    targetPortId: string
    kind: NetKind
    label?: string | null
    meta?: Record<string, unknown> | null
  }
  update?: {
    name?: string
    meta?: Record<string, unknown> | null
  }
}

export interface AgentProposal {
  agentActionId: string
  intent: string              // 'naming' | 'annotation' | 'patch' | 'sketch' | 'explain'
  prompt?: string
  summary: string
  suggestions?: Array<{
    primary: string
    alternates: string[]
    reason: string
  }>
  annotation?: Record<string, string | string[]>
  patch?: { ops: TopologyPatchOp[]; description: string }
  explanation?: string
  affectedDesignIds: string[]
}

/** TOPO-EDITOR-UX0 §11 design transaction (before/operation/after). */
export interface DesignTransaction {
  label: string
  before: string   // JSON snapshot of affected entities (ids + revisions)
  after: string
  ts: number
}

/** TOPO-EDITOR-UX0 §22 agent provenance record. */
export interface AgentActionRecord {
  agentActionId: string
  intent: string
  prompt?: string | null
  affectedDesignIds: string[]
  before?: Record<string, unknown> | null
  after?: Record<string, unknown> | null
  ts?: number | null
}

export function typeDisplay(codeType?: string | null): string {
  return codeType || 'unknown'
}

export function displayBlockName(block: { name: string; kind: string }): string {
  return block.kind === 'function' ? `${block.name}()` : block.name
}

export function portGroupOf(port: FlowPort): string {
  return `${port.semanticKind}-${port.direction}`
}
