<script setup lang="ts">
// X6 Software Topology canvas (TOPO-UI0).  Pure renderer for the Pinia
// topo store — X6 never holds domain identity.  Scene projection happens
// in domain/topo.ts; this component maps Scene -> X6 cells (dagre layout,
// overlay-aware styles, kind colors, truth dash, selected state).
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Graph } from '@antv/x6'
import { register } from '@antv/x6-vue-shape'
import dagre from '@dagrejs/dagre'
import { useTopoStore } from '../../stores/topo'
import { useThemeStore } from '../../stores/theme'
import { useDesignStore, type CtxMenuItem } from '../../stores/design'
import {
  type ContainerRef,
  type RelationKind,
  type SceneEdge,
  type SceneNode,
} from '../../domain/topo'
import TopoNode from './TopoNode.vue'

const store = useTopoStore()
const theme = useThemeStore()
const design = useDesignStore()

// ---- evidence-plane context menus (WORKBENCH-CONTEXTMENU §5/C5) -----------
// Topology is the read-only evidence plane: right-click offers VIEW actions
// only — never canonical edits, never fabricated facts.
function evidenceNodeMenu(nodeId: string): CtxMenuItem[] {
  const n = store.scene.nodes.find((x) => x.id === nodeId)
  return [
    {
      id: 'select', label: '查看证据详情', testid: 'ctx-topo-node-view',
      action: () => store.selectNode(nodeId),
    },
    {
      id: 'src', label: '打开源码 (Monaco)', testid: 'ctx-topo-node-src',
      action: () => {
        const fn = n?.canonicalId
        const nd = fn ? store.index?.nodeById.get(fn) : null
        if (nd?.repo_path) store.openSource(store.bundle?.meta.source_repo_id ?? null, nd.repo_path, null)
      },
    },
    { id: 'sep', separator: true, label: '' },
    { id: 'ro', label: '只读：证据平面，无编辑动作 (Evidence plane — read only)', disabled: true, testid: 'ctx-topo-ro' },
  ]
}

function evidenceEdgeMenu(edgeId: string): CtxMenuItem[] {
  return [
    {
      id: 'view', label: '查看证据事实 (witness)', testid: 'ctx-topo-edge-view',
      action: () => store.selectEdge(edgeId),
    },
    { id: 'sep', separator: true, label: '' },
    { id: 'ro', label: '只读：证据平面，无编辑动作 (Evidence plane — read only)', disabled: true, testid: 'ctx-topo-ro' },
  ]
}

function evidenceBlankMenu(x: number, y: number): CtxMenuItem[] {
  void x; void y
  return [
    { id: 'fit', label: '适配视图', testid: 'ctx-topo-fit', action: () => fitView() },
    { id: 'sep', separator: true, label: '' },
    { id: 'ro', label: '只读：证据平面，无编辑动作 (Evidence plane — read only)', disabled: true, testid: 'ctx-topo-ro' },
  ]
}
const mount = ref<HTMLDivElement | null>(null)
let graph: Graph | null = null

const NODE_W = 200

const NODE_H: Record<SceneNode['kind'], number> = {
  module: 92,
  'suggested-module': 104,
  'suggested-flow': 104,
  'suggested-composite': 104,
  file: 70,
  function: 62,
  resource: 48,
  unknown: 66,
  external: 52,
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

// WORKBENCH §26-27: relation colors are CSS variables (--relation-*),
// theme-reactive; color is only an aid — text carries the meaning.
const KIND_VAR: Record<string, string> = {
  CALL: '--relation-call', DATA: '--relation-data', STATE: '--relation-state',
  CONTROL: '--relation-control', TIME: '--relation-time', RESOURCE: '--relation-resource',
}

function kindColor(kind: string): string {
  const v = KIND_VAR[kind]
  return (v ? cssVar(v) : '') || '#64748b'
}

let registered = false
function ensureShapeRegistered(): void {
  if (registered) return
  register({ shape: 'topo-node', component: TopoNode })
  registered = true
}

function kindOfNode(cellId: string): SceneNode['kind'] | null {
  const node = store.scene.nodes.find((n) => n.id === cellId)
  return node?.kind ?? null
}

function dominantKind(e: SceneEdge): RelationKind | null {
  const keys = Object.keys(e.kinds) as RelationKind[]
  if (keys.length === 0) return null
  if (keys.length === 1) return keys[0]
  let best: RelationKind = keys[0]
  let bestCount = 0
  for (const k of keys) {
    const c = e.kinds[k] ?? 0
    if (c > bestCount) { best = k; bestCount = c }
  }
  return best
}

function truthDash(e: SceneEdge): string {
  const truth = e.raw[0]?.truth_class ?? 'UNKNOWN'
  if (truth === 'INFERRED') return '6 4'
  if (truth === 'HEURISTIC') return '2 4'
  if (truth === 'UNKNOWN') return '1 4'
  return ''                                    // OBSERVED / RESOLVED solid
}

function simpleName(id: string): string {
  const parts = id.split(/[:.]+/).filter(Boolean)
  return parts[parts.length - 1] ?? id
}

function edgeLabel(e: SceneEdge): string {
  const parts = (Object.entries(e.kinds) as Array<[RelationKind, number]>)
    .sort((a, b) => b[1] - a[1])
  // WORKBENCH §12: meaningful label — the actual member (expr / symbol)
  const raw0 = e.raw[0]
  const member = raw0?.representative_witnesses?.[0]?.expr
    ?? raw0?.data?.token
    ?? simpleName(raw0?.target ?? '')
  const labels = parts.map(([k]) => (k === 'CALL' && member ? `${k} ${member}` : k))
  let text = labels.join(' · ')
  if (parts.length > 1) text += ` ×${parts.map(([, c]) => c).join('/')}`
  const truth = raw0?.truth_class ?? 'UNKNOWN'
  if (truth !== 'OBSERVED' && truth !== 'RESOLVED') text += ` · ${truth}`
  if (raw0?.execution_modality === 'MAY') text += ' · MAY'
  return text
}

function buildEdge(cell: SceneEdge): Record<string, unknown> {
  const kind = dominantKind(cell)
  const color = kind ? kindColor(kind) : '#475569'
  const mixed = Object.keys(cell.kinds).length > 1
  const dash = mixed ? (truthDash(cell) || '3 3') : truthDash(cell)
  return {
    id: cell.id,
    shape: 'edge',
    source: cell.source,
    target: cell.target,
    zIndex: -1,
    router: { name: 'manhattan' },
    connector: { name: 'rounded' },
    attrs: {
      line: {
        stroke: color,
        strokeWidth: 1.6,
        strokeDasharray: dash || undefined,
        targetMarker: { name: 'block', size: 7 },
      },
    },
    labels: [{
      attrs: {
        label: {
          text: edgeLabel(cell),
          fill: cssVar('--text-muted'),
          fontSize: 9,
          paddingX: 3,
          paddingY: 1,
        },
      },
      position: 'middle',
    }],
    data: { sceneEdge: true },
  }
}

// ---- layout ---------------------------------------------------------------
const posCache = new Map<string, { x: number; y: number }>()

function nodeHeight(n: SceneNode): number {
  return NODE_H[n.kind] ?? 70
}

function layoutMissing(nodes: SceneNode[], edges: SceneEdge[]): void {
  const missing = nodes.filter((n) => !posCache.has(n.id))
  if (missing.length === 0 || nodes.length === 0) return
  try {
    const g = new dagre.graphlib.Graph()
    g.setGraph({ rankdir: 'LR', nodesep: 26, ranksep: 110, marginx: 40, marginy: 40 })
    g.setDefaultEdgeLabel(() => ({}))
    const knownIds = new Set(nodes.map((n) => n.id))
    for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: nodeHeight(n) })
    for (const e of edges) {
      if (knownIds.has(e.source) && knownIds.has(e.target)) g.setEdge(e.source, e.target)
    }
    dagre.layout(g)
    for (const n of missing) {
      const p = g.node(n.id)
      if (p) {
        posCache.set(n.id, {
          x: Math.round(p.x - NODE_W / 2),
          y: Math.round(p.y - nodeHeight(n) / 2),
        })
      }
    }
  } catch {
    let x = 40
    for (const n of missing) {
      posCache.set(n.id, { x, y: 40 })
      x += NODE_W + 40
    }
  }
}

// ---- sync ------------------------------------------------------------------
function childLabelOf(n: SceneNode): string {
  if (n.kind === 'module') return '文件'
  if (n.kind === 'suggested-module' || n.kind === 'suggested-flow' || n.kind === 'suggested-composite') return '文件'
  if (n.kind === 'file') return '函数'
  return ''
}

function isExpandable(n: SceneNode): boolean {
  if (n.kind === 'module' || n.kind === 'file') return true
  if (n.kind.startsWith('suggested')) return true
  return false
}

function render(): void {
  if (!graph) return
  const scene = store.scene
  const byId = new Map(graph.getNodes().map((c) => [c.id, c]))
  const wantIds = new Set(scene.nodes.map((n) => n.id))

  for (const cell of graph.getNodes()) {
    if (!wantIds.has(cell.id)) graph!.removeCell(cell.id)
  }
  layoutMissing(scene.nodes, scene.edges)

  for (const n of scene.nodes) {
    const pos = posCache.get(n.id) ?? { x: 40, y: 40 }
    const data = {
      scene: n,
      selected: store.selection?.type === 'node' && store.selection.id === n.id,
      expandable: isExpandable(n),
      childLabel: childLabelOf(n),
      ports: (n.kind === 'function' && n.canonicalId && store.showDataInterfaces)
        ? (store.dataPorts[n.label] ?? []) : [],
      showShapes: store.showShapes,
      showSizes: store.showSizes,
    }
    const existing = byId.get(n.id)
    if (existing) {
      existing.setData(data)
      continue
    }
    graph.addNode({
      id: n.id,
      shape: 'topo-node',
      x: pos.x,
      y: pos.y,
      width: NODE_W,
      height: nodeHeight(n),
      data,
    })
  }

  // edges: IN-PLACE update (X6 v3 remove+add leaks SVG DOM — stale edge
  // paths accumulate and keep old colors; see WB theme-reactivity checks)
  const edgeById = new Map(graph.getEdges().map((c) => [c.id, c]))
  const known = new Set(scene.nodes.map((n) => n.id))
  const wantEdges = new Set<string>()
  for (const e of scene.edges) {
    if (!known.has(e.source) || !known.has(e.target)) continue
    wantEdges.add(e.id)
    const existing = edgeById.get(e.id)
    if (!existing) {
      graph.addEdge(buildEdge(e) as any)
      continue
    }
    const kind = dominantKind(e)
    const color = kind ? kindColor(kind) : '#475569'
    const dash = (Object.keys(e.kinds).length > 1 ? (truthDash(e) || '3 3') : truthDash(e))
    existing.attr('line/stroke', color)
    existing.attr('line/strokeWidth', 1.6)
    existing.attr('line/strokeDasharray', dash || undefined)
    existing.attr('line/targetMarker', { name: 'block', size: 7 })
    if ((existing as any).setLabels) {
      ;(existing as any).setLabels([{
        attrs: { label: { text: edgeLabel(e), fill: cssVar('--text-muted'), fontSize: 9, paddingX: 3, paddingY: 1 } },
        position: 'middle' as const,
      }])
    }
  }
  for (const c of graph.getEdges()) {
    if (!wantEdges.has(c.id)) graph.removeCell(c.id)
  }

  // selection highlight on edges
  for (const c of graph.getCells()) {
    if (!c.isEdge()) continue
    const sel = store.selection?.type === 'edge' && store.selection.id === c.id
    if (sel) {
      c.attr('line/strokeWidth', 3)
      c.attr('line/stroke', '#1d4ed8')
    }
  }

  if (scene.nodes.length) graph.zoomToFit({ padding: 24, maxScale: 1.25 })
  updateMiniMap()
}

// ---- minimap (WORKBENCH §7) ------------------------------------------------
interface MiniMapState { x: number; y: number; w: number; h: number; boxes: Array<{ x: number; y: number; w: number; h: number }> }
const miniMap = ref<MiniMapState | null>(null)

function updateMiniMap(): void {
  if (!store.showMiniMap || !graph) { miniMap.value = null; return }
  const ns = graph.getNodes()
  if (!ns.length) { miniMap.value = null; return }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  const boxes = ns.map((n) => {
    const p = n.position()
    const s = n.size()
    const box = { x: p.x, y: p.y, w: Math.max(s.width, 24), h: Math.max(s.height, 12) }
    minX = Math.min(minX, box.x); minY = Math.min(minY, box.y)
    maxX = Math.max(maxX, box.x + box.w); maxY = Math.max(maxY, box.y + box.h)
    return box
  })
  miniMap.value = { x: minX, y: minY, w: maxX - minX + 40, h: maxY - minY + 40, boxes }
}

function onMiniMapClick(ev: MouseEvent): void {
  const mm = miniMap.value
  if (!mm || !graph) return
  const el = ev.currentTarget as SVGSVGElement
  const rect = el.getBoundingClientRect()
  const fx = (ev.clientX - rect.left) / rect.width
  const fy = (ev.clientY - rect.top) / rect.height
  const cx = mm.x + fx * mm.w
  const cy = mm.y + fy * mm.h
  try { (graph as any).centerPoint(cx, cy) } catch { /* older API */ }
}

function containerFor(nodeId: string): ContainerRef | null {
  const n = store.scene.nodes.find((x) => x.id === nodeId)
  if (!n) return null
  if (n.kind === 'module') return { kind: 'module', id: n.label, label: n.label }
  if (n.kind === 'file') {
    const file = n.file ?? n.label
    return { kind: 'file', id: file, label: n.label }
  }
  if (n.kind.startsWith('suggested') && n.suggestionIdx !== undefined) {
    return { kind: 'sugg', id: String(n.suggestionIdx), label: n.label }
  }
  return null
}

function fitView(): void {
  graph?.zoomToFit({ padding: 24, maxScale: 1.25 })
}

defineExpose({ fitView })

onMounted(() => {
  ensureShapeRegistered()
  graph = new Graph({
    container: mount.value!,
    autoResize: true,
    panning: true,
    mousewheel: { enabled: true, modifiers: [] },
    interacting: { edgeMovable: false, nodeMovable: true },
    grid: { size: 10, visible: false },
  })
  // dev-only hook for deterministic E2E (edge selection + scene reads).
  if (import.meta.env.DEV) (window as any).__topoGraph = graph
  graph.on('node:click', ({ node }) => {
    store.selectNode(node.id)
  })
  graph.on('node:dblclick', ({ node }) => {
    const c = containerFor(node.id)
    if (c) {
      store.push(c)
      store.selectNode(null)
    }
  })
  graph.on('blank:click', () => {
    store.selectNode(null)
    store.selectEdge(null)
  })
  graph.on('edge:click', ({ edge }) => {
    store.selectEdge(edge.id)
  })
  graph.on('blank:contextmenu', ({ e }) => {
    e.preventDefault()
    const ev = e as unknown as MouseEvent
    design.openCtx(ev.clientX, ev.clientY, evidenceBlankMenu(ev.clientX, ev.clientY))
  })
  graph.on('node:contextmenu', ({ node, e }) => {
    e.preventDefault()
    design.openCtx((e as unknown as MouseEvent).clientX, (e as unknown as MouseEvent).clientY,
      evidenceNodeMenu(node.id))
  })
  graph.on('edge:contextmenu', ({ edge, e }) => {
    e.preventDefault()
    design.openCtx((e as unknown as MouseEvent).clientX, (e as unknown as MouseEvent).clientY,
      evidenceEdgeMenu(edge.id))
  })
  render()
})

onBeforeUnmount(() => {
  graph?.dispose()
  graph = null
  posCache.clear()
})

watch(() => store.scene, () => render(), { deep: true })
watch(() => store.selection, () => render(), { deep: true })
watch(() => store.layoutNonce, () => { posCache.clear(); render() })
watch(() => store.showMiniMap, () => updateMiniMap())
// WORKBENCH §26: theme change must recolor edges (drawn once with the
// resolved var value) — resync the scene.
watch(() => theme.resolved, () => render())
</script>

<template>
  <div class="topo-canvas">
    <div ref="mount" class="topo-canvas-mount" />
    <svg
      v-if="miniMap"
      class="topo-minimap"
      data-testid="topo-minimap"
      :viewBox="`${miniMap.x} ${miniMap.y} ${miniMap.w} ${miniMap.h}`"
      preserveAspectRatio="xMidYMid meet"
      @click="onMiniMapClick"
    >
      <rect v-for="(b, i) in miniMap.boxes" :key="i"
            :x="b.x" :y="b.y" :width="b.w" :height="b.h" class="mm-box" />
    </svg>
  </div>
</template>

<style scoped>
.topo-canvas {
  width: 100%;
  height: 100%;
  min-height: 320px;
  background: var(--canvas-bg);
  position: relative;
}
.topo-canvas-mount {
  width: 100%;
  height: 100%;
}
.topo-minimap {
  position: absolute;
  right: 12px;
  bottom: 12px;
  width: 170px;
  height: 120px;
  background: var(--panel-bg, var(--panel));
  border: 1px solid var(--border);
  border-radius: 4px;
  opacity: 0.92;
  cursor: crosshair;
}
.mm-box {
  fill: var(--chip-bg, #e2e8f0);
  stroke: var(--border-strong, #c9ced6);
  stroke-width: 1;
}
</style>
