<script setup lang="ts">
// UI-REALITY-ALIGN0 A1-A17: Software Circuit canvas
// (Blender-node-inspired: typed sockets + typed edges + local perspective).
//
// Nothing here invents structure: nodes/sockets/edges come from
// GET /api/v1/software-circuit, which re-projects the repo graph, the
// DATA-INTERFACE0 ports and the RUNTIME-TRACE0/RUNTIME-DATA0 observations.
// Positions, frames and reroutes are UI layout state (A7/A8/A9) and are
// persisted separately — they can never change canonical relations.
import { computed, onMounted, ref } from 'vue'
import { useCircuitStore, type CircuitEdge, type CircuitNode } from '../../stores/circuit'
import { useSelectionStore } from '../../stores/selection'
import { useWorkbenchStore } from '../../stores/workbench'
import { useLangStore } from '../../stores/lang'
import { edgePlaneLabel, formatShape, planeInfo, spanLabel } from '../../domain/truth'
import TruthBadge from '../ui/TruthBadge.vue'
import EmptyState from '../ui/EmptyState.vue'

const circuit = useCircuitStore()
const selection = useSelectionStore()
const workbench = useWorkbenchStore()
const lang = useLangStore()

const L = (zh: string, en: string): string => (lang.isZh ? zh : en)
const NODE_W = 250
const NODE_H_COLLAPSED = 46
const SOCKET_H = 20

const bundleEdges = ref(false)
const rootHistory = ref<string[]>([])
const canvasRef = ref<HTMLElement | null>(null)
const dragState = ref<{ id: string; dx: number; dy: number } | null>(null)

onMounted(() => {
  circuit.loadLayout()
  if (!circuit.data) void circuit.load()
})

function nodeHeight(n: CircuitNode): number {
  if (!circuit.layoutExpanded && !circuit.expandedNodes.includes(n.id)) return NODE_H_COLLAPSED
  return NODE_H_COLLAPSED + n.sockets.length * SOCKET_H + 8
}

function pos(n: CircuitNode, i: number): { x: number; y: number } {
  return circuit.positionOf(n.id, i)
}

/** Deterministic layered layout: BFS distance from the root decides the column. */
const levels = computed<Record<string, number>>(() => {
  const adj: Record<string, string[]> = {}
  for (const e of circuit.visibleEdges) {
    if (e.kind !== 'CALLS') continue
    ;(adj[e.src] ??= []).push(e.dst)
    ;(adj[e.dst] ??= []).push(e.src)
  }
  const out: Record<string, number> = { [circuit.root]: 0 }
  const queue = [circuit.root]
  while (queue.length) {
    const cur = queue.shift()!
    for (const nx of adj[cur] ?? []) {
      if (out[nx] === undefined) { out[nx] = out[cur] + 1; queue.push(nx) }
    }
  }
  return out
})

const laidOut = computed(() => {
  const perLevel: Record<number, number> = {}
  return circuit.nodes.map((n, i) => {
    const explicit = circuit.positions[n.id]
    if (explicit) return { node: n, ...explicit, height: nodeHeight(n) }
    const lvl = levels.value[n.id] ?? 0
    const row = perLevel[lvl] ?? 0
    perLevel[lvl] = row + 1
    return { node: n, x: 40 + lvl * 340, y: 40 + row * 220, height: nodeHeight(n) }
  })
})

const posById = computed(() => Object.fromEntries(laidOut.value.map((l) => [l.node.id, l])))

function socketRows(n: CircuitNode) {
  const order = ['INPUT', 'INOUT', 'OUTPUT', 'RETURN', 'STATE', 'RESOURCE']
  return [...n.sockets].sort((a, b) => order.indexOf(a.direction) - order.indexOf(b.direction))
}

function isOpen(n: CircuitNode): boolean {
  return circuit.layoutExpanded || circuit.expandedNodes.includes(n.id)
}

function edgePath(e: CircuitEdge): string {
  const a = posById.value[e.src]
  const b = posById.value[e.dst]
  if (!a || !b) return ''
  const x1 = a.x + NODE_W, y1 = a.y + a.height / 2
  const x2 = b.x, y2 = b.y + b.height / 2
  const waypoints = circuit.reroutesFor(e.id)
  const mid = waypoints.length
    ? waypoints.map((w) => `${w.x},${w.y}`).join(' L ')
    : `${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2}`
  return `M ${x1},${y1} L ${mid} L ${x2},${y2}`
}

function edgeDashed(e: CircuitEdge): string | undefined {
  const plane = e.plane === 'OBSERVED' && !e.observed ? 'UNKNOWN' : e.plane
  const dash = planeInfo(plane).dash
  return dash === 'none' ? undefined : dash
}

function edgeStroke(e: CircuitEdge): string {
  if (e.kind === 'CONTAINS') return 'var(--border-strong)'
  if (e.plane === 'OBSERVED') return 'var(--truth-observed)'
  if (e.observed) return 'var(--relation-call)'
  return 'var(--edge-stroke)'
}

/** A11: bundle many same-kind edges behind one representative edge. */
const bundleInfo = computed(() => {
  const byKind: Record<string, CircuitEdge[]> = {}
  for (const e of circuit.visibleEdges) (byKind[e.kind] ??= []).push(e)
  return Object.entries(byKind)
    .filter(([, list]) => list.length > 1)
    .map(([kind, list]) => ({ kind, list, collapsed: bundleEdges.value && list.length > 4 }))
})

const drawnEdges = computed(() => {
  if (!bundleEdges.value) return circuit.visibleEdges
  return bundleInfo.value.flatMap((b) => (b.collapsed ? [b.list[0]] : b.list))
})

function selectNode(n: CircuitNode): void {
  circuit.expandedNodes = circuit.expandedNodes.includes(n.id) ? circuit.expandedNodes : [...circuit.expandedNodes, n.id]
  selection.set({
    kind: 'function', id: n.id, label: n.name, detail: n.qname,
    file: n.path ?? null, line: n.start_line ?? null, end_line: n.end_line ?? null,
    span: n.span ?? null, plane: 'STATIC', truth_class: n.truth_class,
    coverage: n.socket_coverage, authority: n.authority,
    payload: { observed: n.observed, groups: n.groups, sockets: n.sockets, notes: n.socket_notes },
  })
  circuit.selectedEdge = null
  circuit.selectedSocket = null
}

function selectSocket(n: CircuitNode, s: Record<string, any>): void {
  circuit.selectedSocket = { node: n.id, socket: s.socket_id }
  const observed = s.observed ?? null
  selection.set({
    kind: 'socket', id: `${n.id}::${s.socket_id}`, label: `${n.name}.${s.name}`,
    detail: `${s.direction} · ${s.semantic_type ?? 'DATA'}`,
    file: observed?.source_line ? String(observed.source_line).split(':')[0] : null,
    line: observed?.source_line ? Number(String(observed.source_line).split(':')[1]) : null,
    plane: s.plane?.startsWith('OBSERVED') ? 'OBSERVED' : 'STATIC',
    truth_class: s.truth_class, coverage: s.coverage, authority: s.authority,
    payload: { socket: s, node: n.name, node_id: n.id },
  })
}

function selectEdge(e: CircuitEdge): void {
  circuit.selectedEdge = e.id
  circuit.selectedSocket = null
  const span = e.span ?? null
  selection.set({
    kind: 'circuit-edge', id: e.id, label: `${shortName(e.src)} → ${shortName(e.dst)}`,
    detail: edgePlaneLabel(e.plane === 'STATIC', e.observed?.count ?? null),
    file: span?.file ?? null, line: span?.start_line ?? null, span,
    plane: e.observed ? 'OBSERVED' : 'STATIC', truth_class: e.observed ? 'OBSERVED' : e.truth_class,
    coverage: e.observed ? 'COMPLETE' : 'UNKNOWN', authority: e.authority,
    payload: { edge: e },
  })
}

function shortName(id: string): string {
  return circuit.nodeById[id]?.name ?? id.split(':').slice(-1)[0]
}

function openSource(span: Record<string, any> | null | undefined, line?: number | null): void {
  if (!span?.file) return
  workbench.requestSource({ file: span.file, line: span.start_line ?? line ?? null, span,
                            repoId: circuit.data?.repo_id ?? null })
}

/** A10: expansion is a LOCAL perspective change (re-root), never "show all". */
function expandTo(nodeId: string): void {
  rootHistory.value = [...rootHistory.value, circuit.root]
  void circuit.load(nodeId, 1)
}
function goBackRoot(): void {
  const prev = rootHistory.value.pop()
  if (prev) void circuit.load(prev, circuit.depth)
}

/** A6/A17: focus a real group (Human Semantic stage / declared module). */
function focusGroup(g: { members: string[] }): void {
  if (g.members.length) expandTo(g.members[0])
}

function onNodePointerDown(ev: PointerEvent, n: CircuitNode, i: number): void {
  const start = pos(n, i)
  dragState.value = { id: n.id, dx: ev.clientX - start.x, dy: ev.clientY - start.y }
  const move = (e: PointerEvent): void => {
    if (!dragState.value) return
    circuit.setPosition(dragState.value.id, e.clientX - dragState.value.dx, e.clientY - dragState.value.dy)
  }
  const up = (): void => {
    dragState.value = null
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
  ev.preventDefault()
}

function onEdgeDblClick(e: CircuitEdge): void {
  openSource(e.span, null)
}

const coverageNotes = computed(() => {
  const unknown = circuit.nodes.filter((n) => n.socket_coverage === 'UNKNOWN')
  const partial = circuit.nodes.filter((n) => n.socket_coverage === 'PARTIAL')
  return { unknown: unknown.length, partial: partial.length }
})
</script>

<template>
  <div class="circuit" data-testid="software-circuit">
    <header class="cc-toolbar">
      <span class="cc-title">{{ L('软件电路', 'Software Circuit') }}</span>
      <span class="cc-root mono" :title="circuit.root">{{ shortName(circuit.root) }}</span>
      <button v-if="rootHistory.length" type="button" class="cc-btn" data-testid="cc-root-back" @click="goBackRoot">
        ← {{ L('返回', 'back') }}
      </button>
      <span class="cc-sep"></span>
      <button type="button" class="cc-btn" data-testid="cc-expand-callees"
              :title="L('以选中的被调用者为中心重新投影（局部视角）', 're-root on the selected callee (local perspective)')"
              @click="expandTo(selection.current?.id ?? circuit.root)">
        {{ L('展开被调用者', 'Expand callees') }}
      </button>
      <button type="button" class="cc-btn" data-testid="cc-depth"
              @click="circuit.load(circuit.root, circuit.depth >= 2 ? 1 : 2)">
        depth {{ circuit.depth }}
      </button>
      <button type="button" class="cc-btn" data-testid="cc-expand-data"
              :class="{ on: circuit.showData }" @click="circuit.showData = !circuit.showData">
        {{ L('数据', 'data') }}
      </button>
      <button type="button" class="cc-btn" data-testid="cc-expand-runtime"
              :class="{ on: circuit.showObserved }" @click="circuit.showObserved = !circuit.showObserved">
        {{ L('运行时', 'runtime') }}
      </button>
      <button type="button" class="cc-btn" :class="{ on: circuit.showStatic }"
              data-testid="cc-static" @click="circuit.showStatic = !circuit.showStatic">
        {{ L('静态', 'static') }}
      </button>
      <button type="button" class="cc-btn" :class="{ on: circuit.showContainment }"
              data-testid="cc-contain" @click="circuit.showContainment = !circuit.showContainment">
        {{ L('包含关系', 'containment') }}
      </button>
      <button type="button" class="cc-btn" data-testid="cc-bundle" :class="{ on: bundleEdges }"
              @click="bundleEdges = !bundleEdges">
        {{ L('边折叠', 'bundle edges') }}
      </button>
      <button type="button" class="cc-btn" data-testid="cc-layout"
              @click="circuit.layoutExpanded = !circuit.layoutExpanded">
        {{ circuit.layoutExpanded ? L('收起端口', 'collapse sockets') : L('展开全部端口', 'expand sockets') }}
      </button>
      <button type="button" class="cc-btn ghost" data-testid="cc-reset-layout" @click="circuit.resetLayout()">
        {{ L('重置布局', 'reset layout') }}
      </button>
      <span class="cc-spacer"></span>
      <span class="cc-legend mono">
        <span class="lg static">│ {{ L('静态', 'static') }}</span>
        <span class="lg observed">◉ {{ L('已观测', 'observed') }}</span>
        <span class="lg unknown">◌ {{ L('探针/未知', 'probe/unknown') }}</span>
      </span>
    </header>

    <div v-if="circuit.loading" class="cc-note">{{ L('加载中…', 'loading…') }}</div>
    <EmptyState v-else-if="circuit.error" tone="unknown"
      :title="L('无法投影该邻域', 'projection unavailable')"
      :reason="circuit.error"
      :hint="L('后端 /api/v1/software-circuit 需要 repo_id 与存在的 node id。', 'the endpoint needs repo_id + an existing node id')" />
    <template v-else-if="circuit.data">
      <!-- A6/A17: groups carry their real source type; never all called "module" -->
      <div v-if="circuit.groups.length" class="cc-groups" data-testid="cc-groups">
        <span class="ccg-label">{{ L('分组（来源类型已标注）', 'groups (source type shown)') }}:</span>
        <button v-for="g in circuit.groups" :key="g.id" type="button" class="ccg-chip"
                :data-group-source="g.source_type" :title="`${g.source_type} · ${g.members.length} members`"
                @click="focusGroup(g)">
          <TruthBadge kind="plane" value="ANNOTATION" compact />
          <span class="ccg-src">{{ g.source_type }}</span>
          {{ g.label }} <span class="ccg-n">×{{ g.members.length }}</span>
        </button>
      </div>

      <div ref="canvasRef" class="cc-canvas" data-testid="cc-canvas">
        <!-- A7: visual frames — layout only, explicitly labelled -->
        <div v-for="f in circuit.frames" :key="f.id" class="cc-frame" :style="{ left: f.x + 'px', top: f.y + 'px', width: f.w + 'px', height: f.h + 'px' }">
          <span class="ccf-label">{{ f.label }} · {{ L('视觉框（仅排版）', 'visual frame (layout only)') }}</span>
          <button type="button" class="ccf-x" @click="circuit.removeFrame(f.id)">×</button>
        </div>

        <svg class="cc-edges" data-testid="cc-edges">
          <path
            v-for="e in drawnEdges" :key="e.id"
            :d="edgePath(e)" fill="none"
            :stroke="edgeStroke(e)" :stroke-width="e.observed ? 2 : 1.3"
            :stroke-dasharray="edgeDashed(e)"
            class="cc-edge" :class="{ sel: circuit.selectedEdge === e.id }"
            :data-edge-plane="e.observed ? 'OBSERVED' : 'STATIC'"
            :data-edge-id="e.id"
            @click="selectEdge(e)" @dblclick="onEdgeDblClick(e)"
          />
          <circle v-for="r in circuit.reroutes" :key="r.id" :cx="r.x" :cy="r.y" r="5" class="cc-reroute" />
        </svg>

        <!-- edge labels / plane badges -->
        <template v-for="e in drawnEdges" :key="`lbl-${e.id}`">
          <span v-if="posById[e.src] && posById[e.dst]" class="cc-edge-label"
                :style="{ left: ((posById[e.src].x + posById[e.dst].x + NODE_W) / 2) + 'px',
                          top: ((posById[e.src].y + posById[e.dst].y) / 2 + 10) + 'px' }"
                @click="selectEdge(e)" @dblclick="onEdgeDblClick(e)"
                :data-edge-label-id="e.id">
            <span class="ccel-kind">{{ e.kind }}</span>
            <!-- §11: the canonical reading, next to its own count -->
            <span class="ccel-read" :data-edge-reading="edgePlaneLabel(e.plane === 'STATIC', e.observed?.count ?? null)">
              {{ edgePlaneLabel(e.plane === 'STATIC', e.observed?.count ?? null) }}
            </span>
            <span v-if="e.observed" class="ccel-obs">◉ {{ e.observed.count }}×</span>
            <span v-if="e.observed?.via_macro" class="ccel-macro" :title="e.observed.note || ''">macro</span>
          </span>
        </template>

        <!-- nodes -->
        <div
          v-for="(l, i) in laidOut" :key="l.node.id"
          class="cc-node" :class="{ sel: selection.current?.id === l.node.id, compact: circuit.compact }"
          :style="{ left: l.x + 'px', top: l.y + 'px', width: NODE_W + 'px' }"
          :data-node-id="l.node.id" :data-socket-coverage="l.node.socket_coverage"
          @pointerdown="onNodePointerDown($event, l.node, i)"
          @click="selectNode(l.node)" @dblclick="openSource(l.node.span, l.node.start_line)"
        >
          <div class="ccn-head">
            <span class="ccn-name">{{ l.node.name }}</span>
            <span class="ccn-kind">{{ l.node.kind }}</span>
            <TruthBadge v-if="l.node.observed" kind="truth" value="OBSERVED" compact />
          </div>
          <div class="ccn-sub mono">
            {{ spanLabel(l.node.span) || l.node.path }}
            <span v-if="l.node.observed" class="ccn-obs">· {{ l.node.observed.calls }}× · {{ l.node.observed.exclusive_ms }}ms excl</span>
          </div>
          <div class="ccn-badges">
            <TruthBadge kind="coverage" :value="l.node.socket_coverage" compact />
            <span v-if="l.node.socket_notes?.length" class="ccn-warn"
                  :title="l.node.socket_notes.join('\n')">ⓘ</span>
            <span v-if="isOpen(l.node)" class="ccn-sockets-n">{{ l.node.sockets.length }} sockets</span>
          </div>
          <ul v-if="isOpen(l.node)" class="ccn-sockets" data-testid="cc-sockets">
            <li v-for="s in socketRows(l.node)" :key="s.socket_id"
                class="ccs" :class="[`dir-${s.direction.toLowerCase()}`, { sel: circuit.selectedSocket?.socket === s.socket_id }]"
                :data-socket-direction="s.direction"
                :data-socket-plane="s.plane?.startsWith('OBSERVED') ? 'OBSERVED' : 'STATIC'"
                @click.stop="selectSocket(l.node, s)">
              <span class="ccs-dot" aria-hidden="true"></span>
              <span class="ccs-dir">{{ s.direction }}</span>
              <span class="ccs-name">{{ s.name }}</span>
              <span class="ccs-type mono">{{ s.dtype }}{{ formatShape(s.shape) }}</span>
              <span v-if="s.plane?.startsWith('OBSERVED')" class="ccs-obs" :title="`${s.observed?.kind} @ ${s.observed?.source_line}`">◉</span>
            </li>
          </ul>
        </div>
      </div>

      <footer class="cc-status">
        <span>{{ circuit.counts.node ?? 0 }} nodes · {{ circuit.counts.edge ?? 0 }} edges</span>
        <span>· {{ circuit.counts.socket_static ?? 0 }} static sockets · {{ circuit.counts.socket_observed ?? 0 }} observed sockets</span>
        <span>· {{ circuit.counts.observed_edge ?? 0 }} observed edges · {{ circuit.counts.static_only_edge ?? 0 }} static-only</span>
        <span v-if="coverageNotes.unknown" class="cc-warn" data-testid="cc-unknown-coverage">
          · ⓘ {{ coverageNotes.unknown }} {{ L('个节点端口覆盖 UNKNOWN（= 未建模，不是"没有输入"）', 'nodes have UNKNOWN socket coverage (not modelled ≠ no inputs)') }}
        </span>
        <span class="cc-spacer"></span>
        <span class="mono">repo {{ circuit.data.repo_id }} · {{ circuit.run.run_id }} · rev {{ circuit.run.evidence_revision }}</span>
      </footer>
    </template>
    <EmptyState v-else tone="unknown"
      :title="L('尚未选择电路根节点', 'no circuit root selected')"
      :reason="L('请在左侧选择函数，或使用搜索/仓库浏览器定位一个符号。', 'select a function in the explorer or search for a symbol.')"
      :hint="L('默认锚点：BlockPopulation（W1 布居求解链）。', 'default anchor: BlockPopulation (W1 population solve chain).')">
      <button type="button" class="cc-btn" data-testid="cc-load-default" @click="circuit.load('node:FUNCTION:BlockPopulation', 1)">
        {{ L('加载 BlockPopulation 邻域', 'load the BlockPopulation neighborhood') }}
      </button>
    </EmptyState>
    <div v-if="circuit.data && circuit.planNotes.length" class="cc-notes">
      <div v-for="n in circuit.planNotes" :key="n">• {{ n }}</div>
    </div>
  </div>
</template>

<style scoped>
.circuit { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--canvas-bg); }
.cc-toolbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 5px 8px; background: var(--panel); border-bottom: 1px solid var(--border); font-size: 11px; }
.cc-title { font-weight: 700; }
.cc-root { font-size: 10.5px; color: var(--text-secondary); background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; }
.cc-sep { width: 1px; height: 14px; background: var(--border); }
.cc-btn { border: 1px solid var(--border-strong); background: var(--panel); color: var(--text-primary); border-radius: 4px; padding: 2px 7px; font-size: 10.5px; cursor: pointer; }
.cc-btn.on { background: var(--sel-bg); border-color: var(--accent); }
.cc-btn.ghost { background: var(--surface); }
.cc-spacer { flex: 1; }
.cc-legend { display: inline-flex; gap: 8px; font-size: 9.5px; color: var(--text-muted); }
.cc-legend .observed { color: var(--truth-observed); }
.cc-groups { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; padding: 4px 8px; border-bottom: 1px solid var(--border); background: var(--surface); font-size: 10.5px; }
.ccg-label { color: var(--text-muted); }
.ccg-chip { display: inline-flex; gap: 4px; align-items: center; border: 1px dashed var(--plane-annotation); background: var(--truth-derived-bg); color: var(--truth-derived); border-radius: 4px; padding: 1px 6px; font-size: 10px; cursor: pointer; }
.ccg-n { color: var(--text-muted); }
.ccg-src { font-size: 9px; letter-spacing: 0.02em; color: var(--text-muted); }
.cc-canvas { position: relative; flex: 1; min-height: 520px; overflow: auto; background:
  linear-gradient(var(--border) 1px, transparent 1px) 0 0 / 100% 24px,
  linear-gradient(90deg, var(--border) 1px, transparent 1px) 0 0 / 24px 100%; }
.cc-edges { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.cc-edge { pointer-events: stroke; cursor: pointer; }
.cc-edge.sel { stroke-width: 3; }
.cc-reroute { fill: var(--plane-probe); }
.cc-edge-label { position: absolute; display: inline-flex; gap: 4px; font-size: 9px; background: var(--edge-bg); border: 1px solid var(--border); border-radius: 3px; padding: 0 4px; cursor: pointer; z-index: 3; }
.ccel-kind { color: var(--text-secondary); font-weight: 600; }
.ccel-obs { color: var(--truth-observed); }
.ccel-read { font-size: 8px; color: var(--text-muted); letter-spacing: 0.02em; }
.ccel-macro { color: var(--plane-probe); }
.cc-node { position: absolute; background: var(--node-bg); border: 1px solid var(--node-border); border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); cursor: grab; z-index: 2; }
.cc-node.sel { border-color: var(--accent); box-shadow: 0 0 0 2px var(--sel-bg); }
.ccn-head { display: flex; align-items: center; gap: 5px; padding: 4px 7px 0; }
.ccn-name { font-weight: 700; font-size: 11.5px; }
.ccn-kind { font-size: 9px; color: var(--text-muted); text-transform: uppercase; }
.ccn-sub { padding: 0 7px; font-size: 9px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ccn-obs { color: var(--truth-observed); }
.ccn-badges { display: flex; gap: 4px; align-items: center; padding: 3px 7px 5px; }
.ccn-warn { color: var(--truth-heuristic); cursor: help; }
.ccn-sockets-n { font-size: 9px; color: var(--text-muted); }
.ccn-sockets { list-style: none; margin: 0; padding: 2px 0 6px; }
.ccs { display: flex; align-items: center; gap: 4px; padding: 0 7px; height: 20px; font-size: 10px; cursor: pointer; }
.ccs:hover { background: var(--hover); }
.ccs.sel { background: var(--sel-bg); }
.ccs-dot { width: 7px; height: 7px; border-radius: 2px; border: 1px solid var(--border-strong); flex-shrink: 0; }
.dir-input .ccs-dot { background: var(--relation-data); }
.dir-output .ccs-dot { background: var(--relation-call); border-radius: 50%; }
.dir-inout .ccs-dot { background: var(--relation-state); }
.dir-return .ccs-dot { background: var(--relation-time); }
.dir-state .ccs-dot { background: var(--truth-observed); border-style: dashed; }
.dir-resource .ccs-dot { background: var(--relation-resource); }
.ccs-dir { width: 52px; color: var(--text-muted); font-size: 8.5px; letter-spacing: 0.02em; }
.ccs-name { flex: 1; }
.ccs-type { font-size: 9px; color: var(--text-muted); }
.ccs-obs { color: var(--truth-observed); }
.cc-frame { position: absolute; border: 1px dashed var(--plane-annotation); border-radius: 8px; background: rgba(124, 58, 237, 0.04); z-index: 1; }
.ccf-label { position: absolute; top: -9px; left: 8px; font-size: 9px; color: var(--plane-annotation); background: var(--panel); padding: 0 4px; }
.ccf-x { position: absolute; top: 2px; right: 4px; border: none; background: none; color: var(--text-muted); cursor: pointer; }
.cc-status { display: flex; gap: 4px; align-items: center; padding: 3px 8px; border-top: 1px solid var(--border); background: var(--panel); font-size: 10px; color: var(--text-muted); }
.cc-warn { color: var(--truth-partial); }
.cc-notes { padding: 3px 8px 6px; background: var(--panel); font-size: 9.5px; color: var(--text-muted); }
.cc-note { padding: 10px; color: var(--text-muted); font-size: 12px; }
.mono { font-family: var(--mono); }
</style>
