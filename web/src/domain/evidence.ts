// Evidence Model — direct camelCase mapping of the server DTOs (SPEC-P1 §8).
// Pure types + pure mapping functions. No Vue/Pinia imports allowed here.

export interface EvidenceNode {
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
  meta: Record<string, unknown>
}

export interface EvidenceLocation {
  path: string
  line?: number
  startLine?: number
  endLine?: number
}

export interface EvidenceRecord {
  source: string
  confidence: number
  location?: EvidenceLocation | null
  payload: Record<string, unknown> | null
  ts: number
}

export interface EvidenceEdge {
  id: string
  kind: string
  srcId: string
  dstId: string
  confidence: number
  meta: Record<string, unknown>
  evidence: EvidenceRecord[]
}

export interface SnapshotInfo {
  id: string
  parentId?: string
  commitSha?: string
  createdAt: number
  meta: Record<string, unknown>
}

export interface RepoInfo {
  id: string
  rootPath: string
  createdAt: number
  latestSnapshot: string | null
  nodeCount: number
  edgeCount: number
}

export interface TreeItem {
  id: string
  kind: 'dir' | 'file'
  name: string
  path: string
  symbolCount: number
  hasChildren: boolean
}

export interface StructuralEdge {
  src: string
  dst: string
  kind: string
  count: number
}

export interface StructuralOverview {
  repoId: string
  snapshot: string
  tree: TreeItem[]
  topUnits: EvidenceNode[]
  edges: StructuralEdge[]
  counts: Record<string, number | Record<string, number>>
  /** FAC-EQ0: unresolved-evidence reason histogram (bucketed causes),
   *  inside counts under the snake_case key `unresolved_reasons` */
  budgets: {
    nodeBudget: number
    edgeBudget: number
    nodesReturned: number
    edgesReturned: number
  }
  truncated: boolean
}

export interface SearchResultItem {
  id: string
  kind: string
  name: string
  qname: string
  language: string
  path: string
  line?: number
  startLine?: number
  endLine?: number
  match: string
}

export interface NeighborhoodResult {
  repoId: string
  snapshot: string
  nodes: EvidenceNode[]
  edges: EvidenceEdge[]
  budgets: {
    nodeBudget: number
    edgeBudget: number
    nodesReturned: number
    edgesReturned: number
  }
  truncated: boolean
}

export interface SourceResponse {
  path: string
  language: string | null
  content: string
  etag: string
  snapshot: string
  drift?: boolean
}

export interface JobInfo {
  id: string
  kind: string
  repoId: string
  status: 'pending' | 'running' | 'done' | 'failed'
  progress: number
  phase?: string
  result: unknown
  error: unknown
  createdAt: number
  finishedAt: number | null
}

// ---------------------------------------------------------------------------
// Raw (snake_case) DTO shapes + camelCase mappers. The backend speaks
// snake_case; the frontend domain model is camelCase (SPEC-P1 §8).
// ---------------------------------------------------------------------------

export interface RawNode {
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
  meta?: Record<string, unknown>
}

export interface RawEvidenceRecord {
  source: string
  confidence: number
  location?: { path: string; line?: number; start_line?: number; end_line?: number } | null
  payload?: Record<string, unknown> | null
  ts: number
}

export interface RawEdge {
  id: string
  kind: string
  src_id: string
  dst_id: string
  confidence?: number
  meta?: Record<string, unknown>
}

export interface RawTreeItem {
  id: string
  kind: string
  name: string
  path: string
  symbol_count?: number
  has_children?: boolean
}

export interface RawStructuralOverview {
  repo_id: string
  snapshot: string
  tree: RawTreeItem[]
  top_units: RawNode[]
  edges: { src: string; dst: string; kind: string; count: number }[]
  counts: Record<string, number>
  budgets?: {
    node_budget: number
    edge_budget: number
    nodes_returned?: number
    edges_returned?: number
  }
  truncated?: boolean
}

export interface RawNeighborhood {
  repo_id: string
  snapshot: string
  nodes: RawNode[]
  edges: RawEdge[]
  budgets: { node_budget: number; edge_budget: number; nodes_returned: number; edges_returned: number }
  truncated?: boolean
}

export interface RawSearchItem {
  id: string
  kind: string
  name: string
  qname: string
  language: string
  path: string
  line?: number
  start_line?: number
  end_line?: number
  match: string
}

export function mapNode(raw: RawNode): EvidenceNode {
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
    meta: raw.meta ?? {},
  }
}

export function mapEvidenceRecord(raw: RawEvidenceRecord): EvidenceRecord {
  return {
    source: raw.source,
    confidence: raw.confidence,
    location: raw.location
      ? {
          path: raw.location.path,
          line: raw.location.line,
          startLine: raw.location.start_line,
          endLine: raw.location.end_line,
        }
      : (raw.location ?? null),
    payload: raw.payload ?? null,
    ts: raw.ts,
  }
}

export function mapEdge(raw: RawEdge): EvidenceEdge {
  return {
    id: raw.id,
    kind: raw.kind,
    srcId: raw.src_id,
    dstId: raw.dst_id,
    confidence: raw.confidence ?? 1.0,
    meta: raw.meta ?? {},
    evidence: [],
  }
}

export function mapTreeItem(raw: RawTreeItem): TreeItem {
  return {
    id: raw.id,
    kind: raw.kind === 'file' ? 'file' : 'dir',
    name: raw.name,
    path: raw.path,
    symbolCount: raw.symbol_count ?? 0,
    hasChildren: raw.has_children ?? false,
  }
}

export function mapSearchItem(raw: RawSearchItem): SearchResultItem {
  return {
    id: raw.id,
    kind: raw.kind,
    name: raw.name,
    qname: raw.qname,
    language: raw.language,
    path: raw.path,
    line: raw.line,
    startLine: raw.start_line,
    endLine: raw.end_line,
    match: raw.match,
  }
}

export function mapStructuralOverview(raw: RawStructuralOverview): StructuralOverview {
  return {
    repoId: raw.repo_id,
    snapshot: raw.snapshot,
    tree: (raw.tree ?? []).map(mapTreeItem),
    topUnits: (raw.top_units ?? []).map(mapNode),
    edges: raw.edges ?? [],
    counts: raw.counts ?? {},
    budgets: {
      nodeBudget: raw.budgets?.node_budget ?? 0,
      edgeBudget: raw.budgets?.edge_budget ?? 0,
      nodesReturned: raw.budgets?.nodes_returned ?? (raw.top_units ?? []).length,
      edgesReturned: raw.budgets?.edges_returned ?? (raw.edges ?? []).length,
    },
    truncated: raw.truncated ?? false,
  }
}

export function mapNeighborhood(raw: RawNeighborhood): NeighborhoodResult {
  return {
    repoId: raw.repo_id,
    snapshot: raw.snapshot,
    nodes: (raw.nodes ?? []).map(mapNode),
    edges: (raw.edges ?? []).map(mapEdge),
    budgets: {
      nodeBudget: raw.budgets?.node_budget ?? 0,
      edgeBudget: raw.budgets?.edge_budget ?? 0,
      nodesReturned: raw.budgets?.nodes_returned ?? (raw.nodes ?? []).length,
      edgesReturned: raw.budgets?.edges_returned ?? (raw.edges ?? []).length,
    },
    truncated: raw.truncated ?? false,
  }
}
