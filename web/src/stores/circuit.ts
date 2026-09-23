// UI-REALITY-ALIGN0 A1-A19: Software Circuit state.
//
// The projection itself comes from the backend (`/api/v1/software-circuit`),
// which re-projects frozen authorities only.  THIS store owns nothing but UI
// layout state — node positions, visual frames, reroute waypoints, expansion
// choices.  Per A7/A8/A9 none of it can reach the canonical graph.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { apiFetch } from '../api/client'

export interface CircuitSocket {
  socket_id: string
  direction: 'INPUT' | 'OUTPUT' | 'INOUT' | 'STATE' | 'RESOURCE' | 'RETURN' | string
  name: string
  semantic_type?: string
  dtype?: string | null
  rank?: number | null
  shape?: unknown[]
  shape_status?: string
  byte_size?: number | null
  truth_class?: string
  coverage?: string
  plane?: string
  authority?: string
  observed?: Record<string, any> | null
}

export interface CircuitNode {
  id: string
  kind?: string
  name: string
  qname?: string
  language?: string
  path?: string
  start_line?: number
  end_line?: number
  span?: Record<string, any>
  truth_class?: string
  authority?: string
  observed?: Record<string, any> | null
  sockets: CircuitSocket[]
  socket_coverage?: string
  socket_notes?: string[]
  groups?: Array<Record<string, any>>
}

export interface CircuitEdge {
  id: string
  kind: string
  src: string
  dst: string
  confidence?: number | null
  truth_class?: string
  plane?: string
  authority?: string
  observed?: Record<string, any> | null
  span?: Record<string, any> | null
}

export interface CircuitPayload {
  repo_id: string
  snapshot: string
  root: string
  run_id: string
  run?: Record<string, any>
  nodes: CircuitNode[]
  edges: CircuitEdge[]
  counts?: Record<string, number>
  notes?: string[]
}

export interface VisualFrame { id: string; label: string; members: string[]; x: number; y: number; w: number; h: number }
export interface ReroutePoint { id: string; edge_id: string; x: number; y: number }

const LAYOUT_KEY = 'rintel.circuit.layout'
const FRAME_KEY = 'rintel.circuit.frames'

export const useCircuitStore = defineStore('circuit', () => {
  const root = ref<string>('node:FUNCTION:BlockPopulation')
  const runId = ref('W1')
  const depth = ref(1)
  const data = ref<CircuitPayload | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // visibility controls (A10/A14/A15)
  const showStatic = ref(true)
  const showObserved = ref(true)
  const showData = ref(true)
  const showContainment = ref(false)
  const expandedNodes = ref<string[]>([])
  const compact = ref(true)

  // UI-only layout state
  const positions = ref<Record<string, { x: number; y: number }>>({})
  const frames = ref<VisualFrame[]>([])
  const reroutes = ref<ReroutePoint[]>([])
  const selectedEdge = ref<string | null>(null)
  const selectedSocket = ref<{ node: string; socket: string } | null>(null)
  const layoutExpanded = ref(false)

  const nodes = computed(() => data.value?.nodes ?? [])
  const edges = computed(() => data.value?.edges ?? [])
  const nodeById = computed(() => Object.fromEntries(nodes.value.map((n) => [n.id, n])))
  const visibleEdges = computed(() =>
    edges.value.filter((e) => {
      if (e.kind === 'CONTAINS') return showContainment.value
      if (!showStatic.value && e.plane === 'STATIC') return false
      if (!showObserved.value && e.plane === 'OBSERVED') return false
      return true
    }))
  const counts = computed(() => data.value?.counts ?? {})
  /** Run context of the observed overlay — the number never travels alone. */
  const run = computed<Record<string, any>>(() => ({
    ...(data.value?.run ?? {}),
    run_id: data.value?.run?.run_id ?? runId.value,
  }))

  const planNotes = computed(() => data.value?.notes ?? [])

  /** A6/A17: groups come from the backend with their real source type. */
  const groups = computed(() => {
    const seen = new Map<string, { id: string; label: string; source_type: string; members: string[]; truth_class?: string }>()
    for (const n of nodes.value) {
      for (const g of n.groups ?? []) {
        const id = String(g.group_id)
        if (!seen.has(id)) {
          seen.set(id, { id, label: String(g.label), source_type: String(g.source_type),
                         members: [], truth_class: g.truth_class })
        }
        seen.get(id)!.members.push(n.id)
      }
    }
    return [...seen.values()]
  })

  function loadLayout(): void {
    try {
      const raw = localStorage.getItem(LAYOUT_KEY)
      if (raw) positions.value = JSON.parse(raw)
      const fr = localStorage.getItem(FRAME_KEY)
      if (fr) frames.value = JSON.parse(fr)
    } catch { /* ignore */ }
  }
  function persistLayout(): void {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(positions.value))
      localStorage.setItem(FRAME_KEY, JSON.stringify(frames.value))
    } catch { /* ignore */ }
  }

  function resetLayout(): void {
    positions.value = {}
    frames.value = []
    reroutes.value = []
    persistLayout()
  }

  async function load(nextRoot?: string, nextDepth?: number): Promise<void> {
    if (nextRoot) root.value = nextRoot
    if (nextDepth) depth.value = nextDepth
    loading.value = true
    error.value = null
    try {
      data.value = await apiFetch<CircuitPayload>(
        `/software-circuit?repo_id=fac&node=${encodeURIComponent(root.value)}` +
        `&depth=${depth.value}&run_id=${encodeURIComponent(runId.value)}`)
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      data.value = null
    } finally {
      loading.value = false
    }
  }

  function positionOf(id: string, index: number): { x: number; y: number } {
    const p = positions.value[id]
    if (p) return p
    const col = index % 3
    const row = Math.floor(index / 3)
    return { x: 40 + col * 320, y: 30 + row * 260 }
  }

  function setPosition(id: string, x: number, y: number): void {
    positions.value = { ...positions.value, [id]: { x, y } }
    persistLayout()
  }

  function toggleExpanded(id: string): void {
    expandedNodes.value = expandedNodes.value.includes(id)
      ? expandedNodes.value.filter((x) => x !== id)
      : [...expandedNodes.value, id]
  }

  /** A6: a frame is a VISUAL group.  It is never canonical membership. */
  function addFrame(label: string, members: string[]): void {
    frames.value = [...frames.value, {
      id: `frame:${Date.now()}`, label, members,
      x: 20, y: 20, w: 300, h: 200,
    }]
    persistLayout()
  }
  function removeFrame(id: string): void {
    frames.value = frames.value.filter((f) => f.id !== id)
    persistLayout()
  }

  /** A9: reroute is a UI-only waypoint on one edge. */
  function addReroute(edgeId: string, x: number, y: number): void {
    reroutes.value = [...reroutes.value, { id: `rr:${Date.now()}`, edge_id: edgeId, x, y }]
  }
  function removeReroute(id: string): void {
    reroutes.value = reroutes.value.filter((r) => r.id !== id)
  }
  function moveReroute(id: string, x: number, y: number): void {
    reroutes.value = reroutes.value.map((r) => (r.id === id ? { ...r, x, y } : r))
  }
  const reroutesFor = (edgeId: string) => reroutes.value.filter((r) => r.edge_id === edgeId)

  return {
    root, runId, depth, data, loading, error,
    showStatic, showObserved, showData, showContainment, expandedNodes, compact,
    positions, frames, reroutes, selectedEdge, selectedSocket, layoutExpanded,
    nodes, edges, nodeById, visibleEdges, counts, run, planNotes, groups,
    load, loadLayout, persistLayout, resetLayout, positionOf, setPosition, toggleExpanded,
    addFrame, removeFrame, addReroute, removeReroute, moveReroute, reroutesFor,
  }
})
