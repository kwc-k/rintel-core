// FLOW-INFER0 View model (renderer-neutral).  Mirrors the backend artifact
// types one-to-one; adds scenario-guard status helpers (FI8: scenario is a
// constraint projection of the master flow, never a mutation).
export interface FlowNode {
  node_id: string
  kind: 'entry' | 'init' | 'dispatch' | 'region' | 'command' | 'shared' | 'unknown' | 'exit'
  label: string
  app?: string | null
  members?: string[]
  count?: number | null
  truth_class?: string
  capability?: string | null
  why?: string[]
  witness_count?: number
  representative_witnesses?: Record<string, unknown>[]
}

export interface FlowEdge {
  edge_id: string
  source: string
  target: string
  kind: string
  guard_id?: string | null
  truth_class?: string
  target_resolution?: string
  witnesses?: Array<{ fact_id?: string; expr?: string; line?: number | null }>
  resolution?: 'ACTIVE' | 'INACTIVE' | 'MAY' | 'UNKNOWN'
  why?: string[]
}

export interface FlowRegion {
  region_id: string
  name: string
  app: string
  methods: string[]
  handler_symbols: string[]
  callees: string[]
  derived: boolean
  why: string[]
  witnesses: Array<{ fact_id?: string; expr?: string; line?: number | null }>
}

export interface FlowScenario {
  scenario_id: string
  name: string
  app: string
  constraints: string[]
  active_regions: string[]
  inactive_regions: string[]
  unknown_regions: string[]
  shared_regions: string[]
  witness_count?: number
  guard_resolutions?: Array<{ guard_id: string; resolution: string }>
  coverage?: string
}

export interface MasterFlow {
  flow_id: string
  name: string
  app: string
  entry_symbols: string[]
  exit_symbols: string[]
  nodes: FlowNode[]
  edges: FlowEdge[]
  regions: FlowRegion[]
  scenario_ids: string[]
  truth_summary: Record<string, number>
  capability_summary: Record<string, string>
  shared_core: Array<{ name: string; regions: string[] }>
}

export interface FlowArtifacts {
  flows: MasterFlow[]
  scenarios: FlowScenario[]
}

export type EdgeStatus = 'ACTIVE' | 'INACTIVE' | 'MAY' | 'UNKNOWN'

/** Dispatch-guard status under a scenario (§19): TRUE=ACTIVE, FALSE=INACTIVE,
 *  anything else stays UNKNOWN — never guessed (FI3). */
export function guardStatus(
  guardId: string | null | undefined,
  scenario: FlowScenario | null,
): EdgeStatus {
  if (!guardId || !scenario) return 'ACTIVE'
  const g = scenario.guard_resolutions?.find((x) => x.guard_id === guardId)
  if (!g) return 'UNKNOWN'
  if (g.resolution === 'TRUE') return 'ACTIVE'
  if (g.resolution === 'FALSE') return 'INACTIVE'
  return 'UNKNOWN'
}

/** Node status under a scenario (FI3/FI8): active → ACTIVE, inactive-region
 *  → INACTIVE (hidden unless "show pruned"), no region → UNKNOWN (kept). */
export function nodeStatus(
  node: FlowNode,
  scenario: FlowScenario | null,
  regionOf: (nodeId: string) => string | null,
): EdgeStatus {
  if (!scenario) return 'ACTIVE'
  if (node.kind === 'shared' || node.kind === 'unknown') return 'ACTIVE'
  if (node.kind === 'entry' || node.kind === 'init' || node.kind === 'dispatch' || node.kind === 'exit') {
    return 'ACTIVE'
  }
  const rid = node.kind === 'region' ? node.node_id : regionOf(node.node_id)
  if (rid && scenario.active_regions.includes(rid)) return 'ACTIVE'
  if (rid && scenario.inactive_regions.includes(rid)) return 'INACTIVE'
  return 'UNKNOWN'
}
