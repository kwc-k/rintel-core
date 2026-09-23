// TOPO-UI0 Software Topology API client (read-only frozen artifacts).
import { apiGet } from './client'
import type { RouteCDebug, Suggestion, TopoBundle, TopoEdge, TopoMeta, TopoNode } from '../domain/topo'

export interface LaneSummary {
  lane: string
  label: string
  languages: string[]
  data_capability: string
  synthetic: boolean
  node_count: number
  edge_count: number
}

export interface LanesResponse {
  items: LaneSummary[]
  total: number
}

interface RawSuggestion {
  origin: string
  candidate_id?: string
  candidate_kind: string
  members: string[]
  score: number | null
  score_components?: Record<string, unknown> | null
  interface_diagnostics?: Record<string, unknown> | null
  soft_features?: Record<string, unknown> | null
  hard_features?: Record<string, unknown> | null
  why?: string[]
  legal?: unknown[]
  classification?: Suggestion['classification']
  confidence?: number | null
  witnesses?: Array<Record<string, unknown>>
  rejection_reasons?: string[]
  boundary?: Suggestion['boundary']
  truth_class: string
  data_capability?: string
}

interface RawBundle {
  lane: string
  meta: Omit<TopoMeta, 'overlay_stats'> & { overlay_stats: Record<string, number> }
  nodes: TopoNode[]
  edges: TopoEdge[]
  declared_modules: string[]
  cross_cutting: string[]
  suggestions: RawSuggestion[]
  route_c: RouteCDebug | null
}

function mapSuggestion(s: RawSuggestion): Suggestion {
  return {
    origin: s.origin,
    candidate_id: s.candidate_id,
    candidate_kind: (['MODULE', 'FLOW', 'COMPOSITE'].includes(s.candidate_kind)
      ? s.candidate_kind : 'MODULE') as Suggestion['candidate_kind'],
    members: s.members,
    score: s.score,
    score_components: s.score_components,
    interface_diagnostics: s.interface_diagnostics,
    soft_features: s.soft_features,
    hard_features: s.hard_features,
    why: s.why ?? [],
    legal: s.legal ?? [],
    classification: s.classification,
    confidence: s.confidence,
    witnesses: s.witnesses ?? [],
    rejection_reasons: s.rejection_reasons ?? [],
    boundary: s.boundary,
    truth_class: s.truth_class ?? 'SUGGESTED',
    data_capability: s.data_capability,
  }
}

export async function getLanes(): Promise<LaneSummary[]> {
  const res = await apiGet<LanesResponse>('/topology-view/lanes')
  return res.items
}

export async function getTopologyBundle(lane: string): Promise<TopoBundle> {
  const raw = await apiGet<RawBundle>(`/topology-view/${encodeURIComponent(lane)}`)
  return {
    lane: raw.lane,
    meta: { ...raw.meta },
    nodes: raw.nodes,
    edges: raw.edges,
    declared_modules: raw.declared_modules,
    cross_cutting: raw.cross_cutting,
    suggestions: raw.suggestions.map(mapSuggestion),
    route_c: raw.route_c,
  }
}

// ---------------------------------------------------------------------------
// SOFTWARE-DRC0 findings
// ---------------------------------------------------------------------------

export interface DrcFinding {
  rule_id: string
  severity: 'ERROR' | 'WARNING' | 'INFO' | 'UNKNOWN'
  status: 'VIOLATION' | 'WARNING' | 'UNKNOWN' | 'PASS'
  subject_ids: string[]
  related_ids: string[]
  message: string
  why: string
  truth_requirements: string[]
  truth_observed: Record<string, unknown> | null
  witnesses: Array<{ fact_id: string; provider?: string; source?: { file?: string; line?: number | null }; expr?: string | null }>
  source_locations: Array<{ file?: string; line?: number | null }>
  coverage: string
  confidence: number
  suggested_fix?: string | null
}

export interface DrcResult {
  lane: string
  data_capability: string
  environment: Record<string, unknown>
  summary: {
    lane: string
    total: number
    by_severity: Record<string, number>
    by_rule: Record<string, number>
    data_capability: string
    environment?: string
    pass_count: number
  }
  findings: DrcFinding[]
  rules_run: string[]
  not_applicable: string[]
  notes: string[]
}

export async function getDrc(lane: string): Promise<DrcResult> {
  return apiGet<DrcResult>(`/topology-view/${encodeURIComponent(lane)}/drc`)
}
