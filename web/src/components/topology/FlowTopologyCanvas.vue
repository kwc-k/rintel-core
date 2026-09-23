<script setup lang="ts">
// FLOW-INFER0 Flow view canvas (§16-§19).  Renders the DERIVED Master Flow
// (entry → init → dispatch → regions → commands → shared core / unknown
// callees → exit) with scenario isolation (FI8).  Flow ≠ evidence (FI1).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Graph } from '@antv/x6'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import { useSourceStore } from '../../stores/source'
import { useTopoStore } from '../../stores/topo'
import {
  guardStatus, nodeStatus,
  type FlowEdge, type FlowNode, type FlowScenario, type MasterFlow,
} from '../../domain/flow-topology'

const store = useFlowTopologyStore()
const source = useSourceStore()
const topo = useTopoStore()
const mount = ref<HTMLDivElement | null>(null)
// UI-REALITY-ALIGN0: any consumer (and any test) must only ever see a canvas
// that already contains its projection — never a half-built one.
const rendered = ref(false)
let graph: Graph | null = null

const NODE_H: Record<string, number> = {
  entry: 46, init: 46, dispatch: 56, region: 58, command: 40,
  shared: 42, unknown: 42, exit: 38,
}

function kindIcon(kind: string): string {
  switch (kind) {
    case 'entry': return '▶'
    case 'init': return '⚙'
    case 'dispatch': return '⌘'
    case 'region': return '▣'
    case 'command': return '∙'
    case 'shared': return '◇'
    case 'unknown': return '?'
    case 'exit': return '■'
    default: return '●'
  }
}

const regionOf = new Map<string, string>()      // cmd id -> region id
function buildRegions(flow: MasterFlow): void {
  regionOf.clear()
  for (const r of flow.regions) {
    for (const m of r.methods) regionOf.set(`cmd:${flow.app}:${m}`, r.region_id)
  }
}

function nodeColor(node: FlowNode, scenario: FlowScenario | null, showPruned: boolean): string {
  const st = nodeStatus(node, scenario, (id) => regionOf.get(id) ?? null)
  const base: Record<string, string> = {
    entry: 'var(--ok)', init: 'var(--topo-control)', dispatch: 'var(--relation-call)',
    region: 'var(--relation-state)', command: 'var(--text-muted)',
    shared: 'var(--relation-resource)', unknown: 'var(--status-warning)', exit: 'var(--relation-time)',
  }
  const c = base[node.kind] ?? 'var(--text-muted)'
  if (st === 'INACTIVE') return 'var(--text-muted)'
  if (st === 'UNKNOWN') return 'var(--status-warning)'
  return c
}

function buildNode(n: FlowNode, flow: MasterFlow, scenario: FlowScenario | null, showPruned: boolean): Record<string, unknown> {
  const st = nodeStatus(n, scenario, (id) => regionOf.get(id) ?? null)
  const hidden = st === 'INACTIVE' && !showPruned
  const sub = n.kind === 'region'
    ? `${n.count ?? 0} commands · ${n.capability ?? ''}`
    : n.kind === 'shared' ? `shared core · ${n.count ?? 0} regions`
    : n.kind === 'unknown' ? `callee · ${n.capability ?? 'unresolved'}`
    : n.kind === 'command' ? (n.members?.[0] ?? '') : ''
  return {
    id: n.node_id,
    shape: 'rect',
    x: 0, y: 0,
    width: n.kind === 'command' ? 150 : 210,
    height: NODE_H[n.kind] ?? 42,
    hidden,
    attrs: {
      body: {
        fill: 'var(--node-bg)',
        stroke: nodeColor(n, scenario, showPruned),
        strokeWidth: 1.4,
        rx: 4,
        strokeDasharray: n.kind === 'unknown' ? '4 3' : n.kind === 'shared' ? '2 3' : undefined,
      },
      label: {
        text: `${kindIcon(n.kind)} ${n.label}`,
        fill: 'var(--text-primary)',
        fontSize: 10,
        textWrap: { width: n.kind === 'command' ? 140 : 200, height: NODE_H[n.kind] ?? 42, ellipsis: true },
      },
    },
    data: { flowNode: n, sub },
  }
}

function edgeStrokeColor(e: FlowEdge, scenario: FlowScenario | null, showPruned: boolean): string {
  const gs = e.guard_id ? guardStatus(e.guard_id, scenario) : 'ACTIVE'
  if (gs === 'INACTIVE' && !showPruned) return 'var(--text-muted)'
  if (gs === 'UNKNOWN' || e.target_resolution === 'UNKNOWN') return 'var(--status-warning)'
  return 'var(--topo-call)'
}

function buildEdge(e: FlowEdge, scenario: FlowScenario | null, showPruned: boolean): Record<string, unknown> {
  const gs = e.guard_id ? guardStatus(e.guard_id, scenario) : 'ACTIVE'
  const label = e.guard_id
    ? `[${e.guard_id.split(':').pop()}] ${gs}`
    : e.target_resolution === 'UNKNOWN' ? `→ ${e.witnesses?.[0]?.expr ?? ''} (unknown)` : ''
  const color = gs === 'INACTIVE' ? 'var(--text-muted)'
    : gs === 'UNKNOWN' ? 'var(--status-warning)'
    : e.target_resolution === 'UNKNOWN' ? 'var(--status-warning)'
    : 'var(--topo-call)'
  return {
    id: e.edge_id,
    shape: 'edge',
    source: e.source,
    target: e.target,
    zIndex: -1,
    router: { name: 'manhattan' },
    connector: { name: 'rounded' },
    hidden: gs === 'INACTIVE' && !showPruned,
    attrs: {
      line: {
        stroke: color,
        strokeWidth: 1.4,
        strokeDasharray: gs === 'UNKNOWN' ? '3 3' : undefined,
        targetMarker: { name: 'block', size: 6 },
      },
    },
    labels: label ? [{
      attrs: { label: { text: label, fill: 'var(--text-muted)', fontSize: 8.5, paddingX: 3, paddingY: 1 } },
      position: 0.5,
    }] : [],
    data: { flowEdge: e },
  }
}

// ---- FLOW-SEMANTIC0: Human view (§12-§15) ----------------------------------
const KIND_ICON: Record<string, string> = {
  INPUT: '▼', INITIALIZATION: '⚙', CONFIGURATION: '⚙', PREPARATION: '✚',
  COMPUTE: '⇄', SOLVER: '∑', TRANSFORM: '⟳', ITERATION: '↻', VALIDATION: '✓',
  POSTPROCESS: '✎', OUTPUT: '▤', DISPATCH: '⌘', SHARED_CORE: '◇', OTHER: '?',
}

function stageCounts(s: Record<string, any>): string {
  const m = s.members ?? {}
  const regions = m.flow_region_ids?.length ?? 0
  const fns = m.canonical_symbol_ids?.length ?? 0
  const mat = [...(s.inputs ?? []), ...(s.outputs ?? [])]
    .filter((l: string) => /\[2\]/.test(l)).length
  const parts: string[] = []
  if (regions) parts.push(`${regions} regions`)
  if (fns) parts.push(`${fns} functions`)
  if (mat) parts.push(`${mat} matrix ports`)
  return parts.join(' · ') || 'annotation stage'
}

function stageStatusText(s: Record<string, any>): string {
  return `${s.stage_kind} · ${s.origin} · ${s.truth_class} · ${s.status}`
}

function varColor(st: string): string {
  return st === 'UNKNOWN' ? 'var(--status-warning)'
    : st === 'INACTIVE' ? 'var(--text-muted)' : 'var(--amber-text)'
}

function renderSemantic(flow: MasterFlow): void {
  const stages = store.semanticStages
  const edges = (store.semantic?.edges ?? []).filter((e) =>
    stages.some((s) => s.semantic_stage_id === e.source_stage) &&
    stages.some((s) => s.semantic_stage_id === e.target_stage))
  const proj = store.activeSemanticFlow?.scenario_projections?.[store.scenarioId ?? ''] ?? null
  const state = (sid: string): string => proj?.stage_states?.[sid] ?? 'ACTIVE'
  const showPruned = store.showPruned
  const byId = new Map(graph!.getNodes().map((c) => [c.id, c]))
  const want = new Set<string>()
  stages.forEach((s, i) => {
    const sid = s.semantic_stage_id
    const st = state(sid)
    const hidden = st === 'INACTIVE' && !showPruned
    want.add(sid)
    const pos = { x: 90, y: 24 + i * 118 }
    const border = st === 'UNKNOWN' ? 'var(--status-warning)'
      : st === 'INACTIVE' ? 'var(--text-muted)'
      : s.status === 'ACCEPTED' ? 'var(--ok)' : 'var(--relation-state)'
    const data = { semanticStage: s, statusText: stageStatusText(s), stageState: st }
    if (byId.has(sid)) {
      const cell = byId.get(sid)!
      cell.setData(data)
      cell.attr('label/text', `⚑ Human Semantic · ${KIND_ICON[s.stage_kind] ?? '●'} ${s.display_name}${st === 'UNKNOWN' ? ' (?)' : ''}`
        + `\n${stageCounts(s)} · ${stageStatusText(s)}`)
      cell.attr('body/stroke', border)
      cell.attr('body/strokeDasharray', st === 'UNKNOWN' ? '5 4' : undefined)
      cell.attr('label/fill', 'var(--text-primary)')
    } else {
      graph!.addNode({
        id: sid, shape: 'rect', width: 320, height: 92, x: pos.x, y: pos.y,
        hidden,
        attrs: {
          body: {
            fill: 'var(--node-bg)', stroke: border, strokeWidth: 1.6, rx: 10,
            strokeDasharray: st === 'UNKNOWN' ? '5 4' : undefined,
          },
          label: {
            text: `${KIND_ICON[s.stage_kind] ?? '●'} ${s.display_name}${st === 'UNKNOWN' ? ' (?)' : ''}`
              + `\n${stageCounts(s)} · ${stageStatusText(s)}`,
            fill: 'var(--text-primary)', fontSize: 10,
            fontWeight: 700,
            lineHeight: 1.45,
            textWrap: { width: 300, height: 84, ellipsis: true },
            refX: 0.5, refY: 0.5, textAnchor: 'middle', textVerticalAnchor: 'middle',
          },
        },
      } as any)
    }
  })
  for (const c of graph!.getNodes()) {
    if (!want.has(c.id)) graph!.removeCell(c.id)
  }
  const eBy = new Map(graph!.getEdges().map((c) => [c.id, c]))
  const wantE = new Set<string>()
  for (const e of edges) {
    const eid = e.edge_id
    wantE.add(eid)
    const solid = (e.witness_refs?.length ?? 0) > 0
    if (eBy.has(eid)) {
      eBy.get(eid)!.setData({ semanticEdge: e })
    } else {
      graph!.addEdge({
        id: eid, shape: 'edge', source: e.source_stage, target: e.target_stage,
        zIndex: -1, router: { name: 'manhattan' }, connector: { name: 'rounded' },
        attrs: {
          line: {
            stroke: e.kind === 'DATA' ? 'var(--accent)' : 'var(--topo-call)',
            strokeWidth: 1.8,
            strokeDasharray: solid ? undefined : '4 4',
            targetMarker: { name: 'block', size: 7 },
          },
        },
        labels: [{
          attrs: { label: {
            text: `${e.kind} · ${(e.witness_refs?.length ?? 0)} witness(es)`,
            fill: 'var(--text-muted)', fontSize: 8.5, paddingX: 3, paddingY: 1 } },
          position: 0.5,
        }],
      } as any)
    }
  }
  for (const c of graph!.getEdges()) {
    if (!wantE.has(c.id)) graph!.removeCell(c.id)
  }
  if (stages.length) graph!.zoomToFit({ padding: 40, maxScale: 0.9 })
  rendered.value = true
}

function render(): void {
  if (!graph) return
  const flow = store.activeFlow
  if (!flow) return
  if (store.granularity === 'human' && store.semantic) {
    renderSemantic(flow)
    return
  }
  buildRegions(flow)
  const scenario = store.activeScenario
  const showPruned = store.showPruned
  const nodes = store.granularity === 'function'
    ? flow.nodes.filter((n) => n.kind !== 'region')   // §11: Function level = commands/callees/specials
    : flow.nodes
  const nodeSet = new Set(nodes.map((n) => n.node_id))


  // nodes
  const byId = new Map(graph.getNodes().map((c) => [c.id, c]))
  const want = new Set<string>()
  let maxX = 0
  let xi = 0, yi = 0
  const regionCol = new Map<string, number>()
  for (const n of nodes) {
    const st = nodeStatus(n, scenario, (id) => regionOf.get(id) ?? null)
    if (st === 'INACTIVE' && !showPruned) {
      const ex = byId.get(n.node_id)
      if (ex) graph!.removeCell(ex.id)
      continue
    }
    want.add(n.node_id)
    const pos = layoutPos(n, flow, regionCol, xi, yi)
    xi += 1
    if (xi >= 3) { xi = 0; yi += 1 }
    maxX = Math.max(maxX, pos.x)
    const data = { flowNode: n }
    if (byId.has(n.node_id)) {
      byId.get(n.node_id)!.setData(data)
      byId.get(n.node_id)!.attr('body/stroke', nodeColor(n, scenario, showPruned))
      byId.get(n.node_id)!.attr('label/text', `${kindIcon(n.kind)} ${n.label}`)
    } else {
      const cell = graph.addNode(buildNode(n, flow, scenario, showPruned) as any)
      if (cell) cell.position(pos.x, pos.y)
    }
  }
  void maxX
  for (const c of graph.getNodes()) {
    if (!want.has(c.id)) graph!.removeCell(c.id)
  }

  // edges (in-place; avoid the SVG-DOM leak from remove+add)
  const edgeById = new Map(graph.getEdges().map((c) => [c.id, c]))
  const wantE = new Set<string>()
  for (const e of flow.edges) {
    if (!nodeSet.has(e.source) || !nodeSet.has(e.target)) continue
    const gs = e.guard_id ? guardStatus(e.guard_id, scenario) : 'ACTIVE'
    if (gs === 'INACTIVE' && !showPruned) {
      const ex = edgeById.get(e.edge_id)
      if (ex) graph!.removeCell(ex.id)
      continue
    }
    wantE.add(e.edge_id)
    if (edgeById.has(e.edge_id)) {
      const cell = edgeById.get(e.edge_id)!
      cell.setData({ flowEdge: e })
      cell.attr('line/stroke', edgeStrokeColor(e, scenario, showPruned))
    } else {
      graph.addEdge(buildEdge(e, scenario, showPruned) as any)
    }
  }
  for (const c of graph.getEdges()) {
    if (!wantE.has(c.id)) graph!.removeCell(c.id)
  }

  if (flow.nodes.length) graph.zoomToFit({ padding: 28, maxScale: 1.1 })
  rendered.value = true
}

function layoutPos(
  n: FlowNode, flow: MasterFlow, regionCol: Map<string, number>,
  xi: number, yi: number,
): { x: number; y: number } {
  // simple layered layout: entry/init/dispatch at top, regions medium,
  // commands/shared at bottom rows (deterministic; auto-layout kept honest)
  const GAP = 36
  let gx: number
  let gy: number
  if (n.kind === 'entry') { gx = 120; gy = 12 }
  else if (n.kind === 'init') { gx = 360; gy = 12 }
  else if (n.kind === 'dispatch') { gx = 600; gy = 12 }
  else if (n.kind === 'exit') { gx = 880; gy = 12 }
  else if (n.kind === 'region') {
    const col = regionCol.get(n.node_id) ?? (regionCol.set(n.node_id, regionCol.size), regionCol.size - 1)
    gx = 120 + col * 230
    gy = 90
  } else {
    const rid = regionOf.get(n.node_id) ?? 'misc'
    const col = [...(regionCol.keys())].indexOf(rid) ?? 0
    gx = 120 + Math.max(col, 0) * 230 + (xi % 2) * 20
    gy = 160 + yi * 46
  }
  void flow
  return { x: gx, y: gy }
}

onMounted(async () => {
  graph = new Graph({
    container: mount.value!,
    autoResize: true,
    panning: true,
    mousewheel: { enabled: true, modifiers: [] },
    interacting: { edgeMovable: false, nodeMovable: true },
    grid: { size: 10, visible: false },
  })
  if (import.meta.env.DEV) (window as any).__flowTopoGraph = graph
  graph.on('node:click', ({ node }) => store.select('node', node.id))
  graph.on('edge:click', ({ edge }) => store.select('edge', edge.id))
  graph.on('blank:click', () => store.select(null))
  if (!store.artifacts) await store.load()
  await store.loadSemantic()      // FLOW-SEMANTIC0 annotation plane (optional)
  render()
})

onBeforeUnmount(() => {
  graph?.dispose()
  graph = null
})

watch(() => [store.app, store.scenarioId, store.showSharedCore, store.showUnknownBranches,
  store.showPruned, store.artifacts, store.granularity, store.semantic],
  () => render(), { deep: true })
</script>

<template>
  <!-- UI-REALITY-ALIGN0: the canvas is only presented once its artifacts are
       loaded.  Before that the pane owns the loading state, so no consumer (and
       no test) can read a half-built projection (1 scenario option / 0 edges). -->
  <div ref="mount" class="flow-topo-canvas" data-testid="flow-topo-canvas"
       v-show="rendered && !store.error">
    <div v-if="!rendered" class="ft-loading">loading flow projection…</div>
    <div v-else-if="store.error" class="ft-error">{{ store.error }}</div>
    <div v-else-if="!store.activeFlow" class="ft-empty">
      <div>
        <b>{{ $t('wb.flow.emptyTitle') }}</b><br />
        {{ $t('wb.flow.emptyReason') }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.flow-topo-canvas {
  width: 100%;
  height: 100%;
  min-height: 320px;
  background: var(--canvas-bg);
  position: relative;
}
.ft-loading, .ft-error, .ft-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 12px;
  z-index: 10;
  pointer-events: none;
}
</style>
