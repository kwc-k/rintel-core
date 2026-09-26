<script setup lang="ts">
// X6 Software Circuit canvas (SPEC-P2 §9) + TOPO-EDITOR-UX0:
// IDE-style context menus (§2 canvas / §3 node / §4 edge / §12 multi-select),
// port drag validation warnings (§5), keyboard shortcuts (§25), undo/redo.
// Renderer for the Pinia flow store — X6 never holds domain identity.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Graph, Selection, type Cell } from '@antv/x6'
import { register } from '@antv/x6-vue-shape'
import dagre from '@dagrejs/dagre'
import { useI18n } from 'vue-i18n'
import { useFlowStore } from '../../stores/flow'
import { useDesignStore, type CtxMenuItem } from '../../stores/design'
import type { FlowBlock, FlowNet, FlowPort } from '../../domain/flow'
import FlowBlockNode from './FlowBlockNode.vue'
import { FLOW_NODE_WIDTH, layoutNodePorts, x6PortGroup } from './port-routing'

const store = useFlowStore()
const design = useDesignStore()
const { t } = useI18n()
const container = ref<HTMLDivElement | null>(null)

let graph: Graph | null = null
const NODE_SHAPE = 'flow-block'
const NODE_W = FLOW_NODE_WIDTH

let registered = false
function ensureShapeRegistered(): void {
  if (registered) return
  register({ shape: NODE_SHAPE, component: FlowBlockNode })
  registered = true
}

const GROUPS: Record<string, { position: 'absolute'; attrs: Record<string, any> }> = {
  'data-in': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-data)', strokeWidth: 1.5, fill: 'var(--panel-bg)' }, portLabel: { fontSize: 8, fill: 'var(--text-secondary)' } } },
  'data-out': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-data)', strokeWidth: 1.5, fill: 'var(--panel-bg)' }, portLabel: { fontSize: 8, fill: 'var(--text-secondary)' } } },
  'control-in': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-control)', strokeWidth: 1.5, fill: 'var(--panel-bg)' }, portLabel: { fontSize: 8, fill: 'var(--text-secondary)' } } },
  'control-out': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-control)', strokeWidth: 1.5, fill: 'var(--panel-bg)' }, portLabel: { fontSize: 8, fill: 'var(--text-secondary)' } } },
  'event-in': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-time)', strokeWidth: 1.5, fill: 'var(--panel-bg)' } } },
  'event-out': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-time)', strokeWidth: 1.5, fill: 'var(--panel-bg)' } } },
  'error-in': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--status-error)', strokeWidth: 1.5, fill: 'var(--panel-bg)' } } },
  'error-out': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--status-error)', strokeWidth: 1.5, fill: 'var(--panel-bg)' } } },
  'resource-in': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-resource)', strokeWidth: 1.5, fill: 'var(--panel-bg)' } } },
  'resource-out': { position: 'absolute', attrs: { circle: { r: 4, magnet: true, stroke: 'var(--topo-resource)', strokeWidth: 1.5, fill: 'var(--panel-bg)' } } },
}

function nodeHeight(block: FlowBlock, ports: FlowPort[]): number {
  return layoutNodePorts(block, ports).height
}

/** Initial placement: stored layout, else dagre (only for missing nodes). */
function initialPositions(
  blocks: FlowBlock[], nets: FlowNet[], known: Record<string, { x: number; y: number }>,
): Record<string, { x: number; y: number }> {
  const out: Record<string, { x: number; y: number }> = {}
  const missing: string[] = []
  for (const b of blocks) {
    if (known[b.id]) out[b.id] = known[b.id]
    else missing.push(b.id)
  }
  if (missing.length === 0 || blocks.length === 0) return out
  try {
    const g = new dagre.graphlib.Graph()
    g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 90, marginx: 30, marginy: 30 })
    g.setDefaultEdgeLabel(() => ({}))
    for (const b of blocks) {
      const bports = store.ports.filter((p) => p.blockId === b.id)
      g.setNode(b.id, { width: NODE_W, height: nodeHeight(b, bports) })
    }
    for (const n of nets) {
      if (n.sourceBlockId && n.targetBlockId && blocks.some((b) => b.id === n.sourceBlockId) &&
          blocks.some((b) => b.id === n.targetBlockId)) {
        g.setEdge(n.sourceBlockId, n.targetBlockId)
      }
    }
    dagre.layout(g)
    for (const id of missing) {
      const p = g.node(id)
      if (p) out[id] = { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - nodeHeight(blocks.find((b) => b.id === id)!, store.ports.filter((q) => q.blockId === id)) / 2) }
    }
    return out
  } catch {
    let x = 40
    for (const id of missing) { out[id] = { x, y: 40 }; x += NODE_W + 60 }
    return out
  }
}

/** §12 auto-layout: dagre over all visible blocks → save positions. */
function autoLayout(): void {
  const blocks = store.visibleBlocks
  const nets = store.visibleNets
  if (!blocks.length) return
  try {
    const g = new dagre.graphlib.Graph()
    g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 90, marginx: 30, marginy: 30 })
    g.setDefaultEdgeLabel(() => ({}))
    for (const b of blocks) {
      g.setNode(b.id, { width: NODE_W, height: nodeHeight(b, store.ports.filter((p) => p.blockId === b.id)) })
    }
    for (const n of nets) {
      if (n.sourceBlockId && n.targetBlockId) g.setEdge(n.sourceBlockId, n.targetBlockId)
    }
    dagre.layout(g)
    const pos: Record<string, { x: number; y: number }> = {}
    for (const b of blocks) {
      const p = g.node(b.id)
      if (p) pos[b.id] = { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - nodeHeight(b, store.ports.filter((q) => q.blockId === b.id)) / 2) }
    }
    store.applyLayoutPositions(pos)
    syncGraph()
  } catch { /* dagre failure → keep current layout */ }
}

/** §12 align: left-edge align selected blocks. */
function alignSelectedLeft(): void {
  if (!graph) return
  const cells = graph.getSelectedCells().filter((c) => c.isNode())
  if (cells.length < 2) return
  const x = Math.min(...cells.map((c) => c.position().x))
  const pos: Record<string, { x: number; y: number }> = {}
  for (const c of cells) {
    pos[c.id] = { x, y: Math.round(c.position().y) }
  }
  store.applyLayoutPositions(pos)
  syncGraph()
}

function buildPorts(block: FlowBlock): Array<{
  id: string; group: string; args?: { x: number; y: number }
}> {
  const bports = store.ports.filter((p) => p.blockId === block.id)
  const layout = layoutNodePorts(block, bports)
  return [...layout.inputs, ...layout.outputs].map((port) => ({
    id: port.id, group: x6PortGroup(port), args: layout.points[port.id],
  }))
}

const rendered = new Map<string, Cell>()
const renderedEdges = new Map<string, Cell>()

function syncGraph(): void {
  if (!graph) return
  const blocks = store.visibleBlocks
  const nets = store.visibleNets
  const wantBlocks = new Set(blocks.map((b) => b.id))
  const wantNets = new Set(nets.map((n) => n.id))
  for (const [id, cell] of rendered) {
    if (!wantBlocks.has(id)) { graph.removeCell(cell); rendered.delete(id) }
  }
  for (const [eid, cell] of renderedEdges) {
    const sc = (cell as any).getSourceCellId?.() ?? '' ?? ''
    const dc = (cell as any).getTargetCellId?.() ?? '' ?? ''
    if (!wantBlocks.has(sc) || !wantBlocks.has(dc)) renderedEdges.delete(eid)
  }
  for (const [id, cell] of renderedEdges) {
    if (!wantNets.has(id)) { graph.removeCell(cell); renderedEdges.delete(id) }
  }
  const positions = initialPositions(blocks, nets, store.positions)
  for (const b of blocks) {
    const pos = positions[b.id] ?? store.positions[b.id] ?? { x: 40, y: 40 }
    const cell = rendered.get(b.id)
    const data = {
      block: b,
      ports: store.ports.filter((p) => p.blockId === b.id),
      selected: store.selectedBlockId === b.id,
    }
    if (!cell || !cell.isNode()) {
      const node = graph.addNode({
        id: b.id,
        shape: NODE_SHAPE,
        x: pos.x,
        y: pos.y,
        width: NODE_W,
        height: nodeHeight(b, data.ports as FlowPort[]),
        data,
        ports: { groups: GROUPS, items: buildPorts(b) },
      })
      rendered.set(b.id, node)
    } else {
      const node = cell
      let changed = false
      if (node.getSize().height !== nodeHeight(b, data.ports as FlowPort[])) {
        node.prop('size', { width: NODE_W, height: nodeHeight(b, data.ports as FlowPort[]) })
        changed = true
      }
      const prevData = node.getData()
      const dataJson = JSON.stringify(data)
      if (!prevData || JSON.stringify(prevData) !== dataJson) {
        node.setData(data)
        changed = true
      }
      const want = JSON.stringify(buildPorts(b))
      const have = JSON.stringify(node.getPorts().map((p) => ({ id: p.id, group: p.group, args: p.args })))
      if (want !== have) {
        // Update ports in place — remove+add of the same id in one tick
        // orphans the vue-shape DOM (stale .flow-block-node left behind).
        // Drop edges touching this node; the nets loop re-adds the valid
        // ones below.
        for (const [eid, cell2] of renderedEdges) {
          if ((cell2 as any).getSourceCellId?.() === b.id ||
              (cell2 as any).getTargetCellId?.() === b.id) {
            graph.removeCell(cell2)
            renderedEdges.delete(eid)
          }
        }
        node.prop('ports', { groups: GROUPS, items: buildPorts(b) })
        changed = true
      }
      // x6-vue-shape re-renders when the cell's `component` prop changes;
      // bump only for changed data/ports. A no-op sync (including node click)
      // must not unmount an open port detail card.
      if (changed) node.prop('component', ((node.getProp('component') as number) ?? 0) + 1)
    }
  }
  for (const n of nets) {
    if (renderedEdges.has(n.id)) {
      const edge = renderedEdges.get(n.id)!
      edge.setData({ net: n })
      continue
    }
    const meta = (() => { try { return JSON.parse(n.metaJson || '{}') } catch { return {} } })()
    const kindColor = n.kind === 'control' ? 'var(--topo-control)'
      : n.kind === 'data' ? 'var(--topo-data)'
      : n.kind === 'resource' ? 'var(--topo-resource)'
      : n.kind === 'event' ? 'var(--topo-time)' : 'var(--status-error)'
    const edge = graph.addEdge({
      id: n.id,
      source: { cell: n.sourceBlockId!, port: n.sourcePortId },
      target: { cell: n.targetBlockId!, port: n.targetPortId },
      data: { net: n },
      zIndex: 0,
      connector: { name: 'normal' },
      router: { name: 'manhattan', args: { padding: 12 } },
      attrs: {
        line: {
          stroke: kindColor,
          strokeWidth: 1.6,
          targetMarker: { name: 'block', args: { size: 7 } },
          // §15: an evidence projection (AS-IS, read-only) and a user-drawn
          // TO-BE design edge must never blur into one visual language.
          strokeDasharray: n.derived ? '2 3' : '6 4',
        },
      },
      labels: [{
        attrs: { text: {
          text: `${n.derived ? '[AS-IS 投影]' : '[TO-BE 设计]'} ${n.derived ? '' : (n.label ?? n.kind)}`,
          fontSize: 8, fill: n.derived ? 'var(--text-muted)' : 'var(--plane-design)' } },
        position: 0.5,
      }],
    })
    renderedEdges.set(n.id, edge)
  }
  void store.positions
}

function portKind(portId: string): { direction: string; kind: string } | null {
  const p = store.portsById[portId]
  return p ? { direction: p.direction, kind: p.semanticKind } : null
}

function findEmbedTarget(blockId: string, position: { x: number; y: number }): string | null {
  const b = store.blocksById[blockId]
  if (!b) return null
  for (const other of store.visibleBlocks) {
    if (other.id === blockId || other.kind !== 'composite') continue
    const cell = rendered.get(other.id)
    if (!cell || !cell.isNode()) continue
    const box = cell.getBBox()
    if (box.containsPoint({ x: position.x, y: position.y })) return other.id
  }
  return null
}

// ---------------------------------------------------------------------------
// TOPO-EDITOR-UX0 §24: IDE-style context menu builders (i18n via t()).
// ---------------------------------------------------------------------------
function canvasMenuItems(e: { x: number; y: number }): CtxMenuItem[] {
  const isEvidence = store.currentParentBlockId !== '' // inside composite = design scope still
  const newItems: CtxMenuItem[] = [
    { id: 'new-function', label: t('canvas.newFunction'), hint: 'F', testid: 'ctx-new-function', action: () => { void store.addBlock('function', 'NewFunction') } },
    { id: 'new-composite', label: t('canvas.newComposite'), hint: 'C', testid: 'ctx-new-composite', action: () => { void store.addBlock('composite', 'NewComposite') } },
    { id: 'new-module', label: t('canvas.newModule'), testid: 'ctx-new-module', action: () => { void store.addBlock('object', 'NewModule') } },
    { id: 'new-data', label: t('canvas.newData'), testid: 'ctx-new-data', action: () => { void store.addBlock('proposed', 'NewDataObject') } },
    { id: 'new-resource', label: t('canvas.newResource'), testid: 'ctx-new-resource', action: () => { void store.addBlock('proposed', 'NewResource') } },
    { id: 'new-annotation', label: t('canvas.newAnnotation'), testid: 'ctx-new-annotation', action: () => { void store.addBlock('proposed', 'NewAnnotation') } },
  ]
  const agentItems: CtxMenuItem[] = [
    { id: 'agent-build', label: t('canvas.agentBuildRegion'), testid: 'ctx-agent-build', action: () => design.explainTopology() },
    { id: 'agent-sketch', label: t('canvas.agentSketch'), testid: 'ctx-agent-sketch', action: () => design.openSketch() },
    { id: 'agent-explain', label: t('canvas.agentExplainTopology'), testid: 'ctx-agent-explain', action: () => design.explainTopology() },
  ]
  return [
    { id: 'new', label: t('canvas.menuNew'), children: newItems },
    { id: 'sep1', separator: true, label: '' },
    { id: 'agent', label: t('canvas.menuAgent'), children: agentItems },
    { id: 'sep2', separator: true, label: '' },
    { id: 'layout', label: t('canvas.autoLayout'), testid: 'ctx-layout', action: () => autoLayout() },
    { id: 'evidence', label: isEvidence ? 'design-scope' : 'design', disabled: true, action: undefined },
  ].filter((i) => !(i.id === 'evidence' ))
}

function nodeMenuItems(blockId: string): CtxMenuItem[] {
  const b = store.blocksById[blockId]
  if (!b) return []
  const evidenceOnly = b.state === 'existing' && !!b.binding
  const editItems: CtxMenuItem[] = [
    {
      id: 'rename', label: t('nodeMenu.rename'), hint: 'F2', testid: 'ctx-rename',
      action: () => {
        const name = window.prompt(t('nodeMenu.rename'), b.name)
        if (name && name !== b.name) void store.renameBlock(blockId, name)
      },
    },
    { id: 'edit-ann', label: t('nodeMenu.editAnnotation'), testid: 'ctx-edit-ann', action: () => design.editAnnotation(blockId) },
    { id: 'sep-ports', separator: true, label: '' },
    { id: 'port-in', label: t('nodeMenu.addInputPort'), testid: 'ctx-port-in', action: () => { void store.addPort({ blockId, name: 'input', direction: 'input', semanticKind: 'data' }) } },
    { id: 'port-out', label: t('nodeMenu.addOutputPort'), testid: 'ctx-port-out', action: () => { void store.addPort({ blockId, name: 'output', direction: 'output', semanticKind: 'data' }) } },
    { id: 'port-state', label: t('nodeMenu.addStatePort'), testid: 'ctx-port-state', action: () => { void store.addPort({ blockId, name: 'state', direction: 'input', semanticKind: 'control' }) } },
    { id: 'port-res', label: t('nodeMenu.addResourcePort'), testid: 'ctx-port-res', action: () => { void store.addPort({ blockId, name: 'store', direction: 'input', semanticKind: 'resource' }) } },
    { id: 'sep-more', separator: true, label: '' },
    { id: 'wrap-c', label: t('nodeMenu.wrapComposite'), testid: 'ctx-wrap', action: () => { void store.createComposite(`${b.name}Group`, [blockId]) } },
    { id: 'del', label: t('nodeMenu.deleteDesignNode'), hint: 'Del', danger: true, testid: 'ctx-del', action: () => { void store.deleteBlocks([blockId]) } },
  ]
  const viewItems: CtxMenuItem[] = [
    { id: 'expand', label: t('nodeMenu.expand'), testid: 'ctx-expand', disabled: b.kind !== 'composite', action: () => { if (b.kind === 'composite') store.enterComposite(blockId, b.name) } },
    { id: 'evidence', label: t('nodeMenu.viewEvidence'), testid: 'ctx-evidence', disabled: !b.symbol, action: () => { if (b.symbol) window.dispatchEvent(new CustomEvent('flow:open-evidence', { detail: { repoId: store.flow?.repoId ?? '', symbolId: b.symbol.id } })) } },
    { id: 'source', label: t('nodeMenu.openSource'), testid: 'ctx-source', disabled: !b.symbol, action: () => { if (b.symbol) window.dispatchEvent(new CustomEvent('flow:open-evidence', { detail: { repoId: store.flow?.repoId ?? '', symbolId: b.symbol.id } })) } },
    { id: 'drc', label: t('nodeMenu.drc'), testid: 'ctx-drc', action: () => { void store.validateFlow() } },
    { id: 'lvs', label: t('nodeMenu.lvs'), testid: 'ctx-lvs', action: () => { window.dispatchEvent(new CustomEvent('flow:open-lvs')) } },
  ]
  const agentItems: CtxMenuItem[] = [
    { id: 'a-name', label: t('nodeMenu.agentName'), testid: 'ctx-a-name', action: () => design.agentName([blockId]) },
    { id: 'a-desc', label: t('nodeMenu.agentDescribe'), testid: 'ctx-a-desc', action: () => design.agentDescribe(blockId) },
    { id: 'a-analyze', label: t('nodeMenu.agentAnalyze'), testid: 'ctx-a-analyze', action: () => design.agentDescribe(blockId) },
    { id: 'a-ports', label: t('nodeMenu.agentSuggestPorts'), testid: 'ctx-a-ports', action: () => design.agentDescribe(blockId) },
    { id: 'a-inner', label: t('nodeMenu.agentSuggestInner'), testid: 'ctx-a-inner', action: () => { void store.addBlock('function', `${b.name}Inner`) } },
    { id: 'a-split', label: t('nodeMenu.agentSuggestSplit'), testid: 'ctx-a-split', action: () => design.explainTopology() },
    { id: 'a-merge', label: t('nodeMenu.agentSuggestMerge'), testid: 'ctx-a-merge', action: () => design.agentName(store.visibleBlocks.filter((x) => x.id !== blockId).map((x) => x.id)) },
    { id: 'a-opt', label: t('nodeMenu.agentOptimize'), testid: 'ctx-a-opt', action: () => { const n = store.nets.find((x) => x.sourceBlockId === blockId || x.targetBlockId === blockId); if (n) design.agentCachePatch(n.id) } },
    { id: 'a-impl', label: t('nodeMenu.agentImplement'), testid: 'ctx-a-impl', action: () => { void store.updateBlock(blockId, { state: 'modified' }) } },
  ]
  if (evidenceOnly) {
    // Evidence-only nodes: 查看 group + Agent 只读意图 (no canonical edits)
    return [
      { id: 'view', label: t('nodeMenu.view'), children: viewItems },
      { id: 'sep', separator: true, label: '' },
      { id: 'agent', label: t('nodeMenu.agent'), children: [
        { id: 'a-name', label: t('nodeMenu.agentName'), action: () => design.agentName([blockId]) },
        { id: 'a-desc', label: t('nodeMenu.agentDescribe'), action: () => design.agentDescribe(blockId) },
      ] },
    ]
  }
  return [
    { id: 'edit', label: t('nodeMenu.edit'), children: editItems },
    { id: 'sep1', separator: true, label: '' },
    { id: 'view', label: t('nodeMenu.view'), children: viewItems },
    { id: 'sep2', separator: true, label: '' },
    { id: 'agent', label: t('nodeMenu.agent'), children: agentItems },
  ]
}

function edgeMenuItems(netId: string): CtxMenuItem[] {
  const n = store.nets.find((x) => x.id === netId)
  if (!n) return []
  if (n.derived) {
    return [
      { id: 'readonly', label: t('edgeMenu.readOnly'), disabled: true, testid: 'ctx-edge-ro' },
      { id: 'sep', separator: true, label: '' },
      { id: 'evidence', label: t('edgeMenu.viewEvidence'), action: () => { window.dispatchEvent(new CustomEvent('flow:open-lvs')) } },
    ]
  }
  const editItems: CtxMenuItem[] = [
    {
      id: 'kind', label: t('edgeMenu.changeKind'), testid: 'ctx-edge-kind',
      action: () => {
        const kind = window.prompt(t('edgeMenu.changeKind') + ' (data/control/event/error/resource)', n.kind)
        if (kind && ['data', 'control', 'event', 'error', 'resource'].includes(kind)) {
          void store.patchNetMeta(netId, { kind })
        }
      },
    },
    {
      id: 'guard', label: t('edgeMenu.addGuard'), testid: 'ctx-edge-guard',
      action: () => {
        const guard = window.prompt(t('inspector.guardLabel'))
        if (guard !== null) void store.patchNetMeta(netId, { meta: { guard } })
      },
    },
    {
      id: 'timing', label: t('edgeMenu.addTiming'), testid: 'ctx-edge-timing',
      action: () => {
        const timing = window.prompt(t('inspector.timingLabel'))
        if (timing !== null) void store.patchNetMeta(netId, { meta: { timing } })
      },
    },
  ]
  const agentItems: CtxMenuItem[] = [
    { id: 'a-explain', label: t('edgeMenu.agentExplain'), testid: 'ctx-edge-a-explain', action: () => design.explainTopology() },
    {
      id: 'a-check', label: t('edgeMenu.agentCheck'), testid: 'ctx-edge-a-check',
      action: () => design.agentCachePatch(netId),
    },
    {
      id: 'a-alt', label: t('edgeMenu.agentAlternative'), testid: 'ctx-edge-a-alt',
      action: () => design.agentCachePatch(netId),
    },
  ]
  return [
    { id: 'edit', label: t('edgeMenu.editConnection'), children: editItems },
    { id: 'sep1', separator: true, label: '' },
    { id: 'explain', label: t('edgeMenu.explain'), testid: 'ctx-edge-explain', action: () => design.explainTopology() },
    { id: 'sep2', separator: true, label: '' },
    { id: 'agent', label: t('canvas.menuAgent'), children: agentItems },
    { id: 'sep3', separator: true, label: '' },
    { id: 'del', label: t('edgeMenu.deleteDesignConnection'), danger: true, testid: 'ctx-edge-del', action: () => { void store.deleteNets([netId]) } },
  ]
}

function portMenuItems(blockId: string, portId: string): CtxMenuItem[] {
  const port = store.ports.find((p) => p.id === portId)
  const isInput = port?.direction === 'input'
  return [
    {
      id: 'connect', label: t('portMenu.startConnect'), testid: 'ctx-port-connect',
      action: () => {
        design.connectingPortId = portId
        design.closeCtx()
      },
    },
    { id: 'sep1', separator: true, label: '' },
    {
      id: 'edit', label: t('portMenu.editPort'), testid: 'ctx-port-edit',
      action: () => store.selectPort(blockId, portId),
    },
    {
      id: 'del', label: t('portMenu.deletePort'), danger: true, testid: 'ctx-port-del',
      action: () => void store.deletePort(portId),
    },
    { id: 'sep2', separator: true, label: '' },
    {
      id: 'dir', label: isInput ? t('portMenu.dirIn') : t('portMenu.dirOut'),
      disabled: true, testid: 'ctx-port-dir',
    },
  ]
}

function multiMenuItems(ids: string[]): CtxMenuItem[] {
  return [
    { id: 'composite', label: t('multi.createComposite'), testid: 'ctx-multi-composite', action: () => { void store.createComposite('NewComposite', ids) } },
    { id: 'sep1', separator: true, label: '' },
    { id: 'a-name', label: t('multi.agentName'), testid: 'ctx-multi-a-name', action: () => design.agentName(ids) },
    { id: 'a-summary', label: t('multi.agentSummary'), testid: 'ctx-multi-a-summary', action: () => design.explainTopology() },
    { id: 'a-module', label: t('multi.agentModule'), testid: 'ctx-multi-a-module', action: () => design.agentName(ids) },
    { id: 'sep2', separator: true, label: '' },
    { id: 'align', label: t('multi.align'), testid: 'ctx-multi-align', action: () => alignSelectedLeft() },
    { id: 'layout', label: t('multi.autoLayout'), testid: 'ctx-multi-layout', action: () => autoLayout() },
    { id: 'sep3', separator: true, label: '' },
    { id: 'del', label: t('multi.deleteDesignObjects'), danger: true, testid: 'ctx-multi-del', action: () => { void store.deleteBlocks(ids) } },
  ]
}

function mount(): void {
  if (!container.value) return
  ensureShapeRegistered()
  graph = new Graph({
    container: container.value,
    grid: { visible: true, size: 12, type: 'dot' },
    panning: { enabled: true, eventTypes: ['rightMouseDown'] },
    mousewheel: { enabled: true, minScale: 0.2, maxScale: 2.5, zoomAtMousePosition: true },
    connecting: {
      allowBlank: false,
      allowLoop: true,
      allowNode: false,
      allowEdge: false,
      allowMulti: true,
      highlight: true,
      snap: true,
      connector: { name: 'normal' },
      validateConnection({ sourcePort, targetPort }) {
        if (!sourcePort || !targetPort) return false
        const s = portKind(sourcePort)
        const t2 = portKind(targetPort)
        if (!s || !t2) return false
        // §5: obvious-illegal connections blocked in front end
        if (s.direction !== 'output' || t2.direction !== 'input') return false
        if (s.kind !== t2.kind) return false
        return true
      },
    },
  })
  graph.use(new Selection({ enabled: true, rubberband: true, multiple: true, movable: true, strict: true }))
  ;(window as any).__flowGraph = graph // debug hook
  ;(window as any).__flowStore = store // automation hook (tests/walkthrough)

  graph.on('node:click', ({ node }) => {
    store.selectBlock(node.id)
    design.closeCtx()
    syncGraph()
  })
  graph.on('node:dblclick', ({ node }) => {
    const b = store.blocksById[node.id]
    if (b?.kind === 'composite') store.enterComposite(b.id, b.name)
  })
  graph.on('node:moved', ({ node }) => {
    const pos = node.position()
    store.applyLayoutPositions({ [node.id]: { x: Math.round(pos.x), y: Math.round(pos.y) } })
    const host = findEmbedTarget(node.id, { x: pos.x + NODE_W / 2, y: pos.y + node.getSize().height / 2 })
    const b = store.blocksById[node.id]
    const current = b?.parentBlockId ?? ''
    if (host && host !== current) void store.reparentBlock(node.id, host)
    else if (!host && current && b?.kind !== 'composite') void store.reparentBlock(node.id, null)
  })
  graph.on('edge:connected', ({ edge, isNew }) => {
    if (!isNew) return
    const sp = edge.getSourcePortId()
    const tp = edge.getTargetPortId()
    if (sp && tp) void store.connectPorts(sp, tp)
    edge.remove()
  })
  graph.on('edge:click', ({ edge }) => { store.selectNet(edge.id); design.closeCtx() })
  graph.on('blank:click', () => { store.clearSelection(); design.closeCtx() })
  graph.on('selection:changed', ({ selected }) => {
    const ids = (selected as Cell[]).map((c) => c.id)
    if (ids.length > 0) store.selectBlock(ids[0])
    syncGraph()
  })
  graph.on('selection:selected', () => { /* composite button reads plugin */ })

  // -- TOPO-EDITOR-UX0 §2/§3/§4/§12: context menus -------------------------
  function openCellMenu(cell: Cell, ev: MouseEvent): void {
    const pos = { x: ev.clientX, y: ev.clientY }
    const selected = graph?.getSelectedCells() ?? []
    const multi = selected.filter((c) => c.isNode()).length > 1
    if (multi) {
      design.openCtx(pos.x, pos.y, multiMenuItems(selected.filter((c) => c.isNode()).map((c) => c.id)))
      return
    }
    if (cell.isNode()) design.openCtx(pos.x, pos.y, nodeMenuItems(cell.id))
    else if (cell.isEdge()) design.openCtx(pos.x, pos.y, edgeMenuItems(cell.id))
  }
  graph.on('cell:contextmenu', ({ cell, e }) => {
    e.preventDefault()
    openCellMenu(cell, e as unknown as MouseEvent)
  })
  graph.on('node:contextmenu', ({ node, e }) => {
    e.preventDefault()
    openCellMenu(node, e as unknown as MouseEvent)
  })
  graph.on('edge:contextmenu', ({ edge, e }) => {
    e.preventDefault()
    openCellMenu(edge, e as unknown as MouseEvent)
  })
  graph.on('blank:contextmenu', ({ e }) => {
    e.preventDefault()
    const ev = e as unknown as MouseEvent
    design.openCtx(ev.clientX, ev.clientY, canvasMenuItems({ x: ev.clientX, y: ev.clientY }))
  })

  // Port right-click: X6 v3 does NOT emit `node:port:contextmenu`, and the
  // ports live inside the x6-vue-shape DOM — bind a capture listener on the
  // canvas container so the browser native menu never appears over a port
  // (WORKBENCH-CONTEXTMENU §1-§4).
  container.value?.addEventListener('contextmenu', onPortContextMenu, true)

  window.addEventListener('keydown', onKeyDown)
  syncGraph()
}

function onPortContextMenu(e: MouseEvent): void {
  const el = e.target as HTMLElement | null
  const port = el?.closest?.('.fb-port') as HTMLElement | null
  if (!port) return                      // let X6 handle node/edge/blank
  e.preventDefault()
  e.stopPropagation()                    // never reach X6's node:contextmenu
  const blockId = port.getAttribute('data-block-id') ?? ''
  const portId = port.getAttribute('data-port-id') ?? ''
  design.openCtx(e.clientX, e.clientY, portMenuItems(blockId, portId))
}

function onKeyDown(e: KeyboardEvent): void {
  if (!graph) return
  const t2 = e.target as HTMLElement | null
  if (t2 && (t2.tagName === 'INPUT' || t2.tagName === 'TEXTAREA' || t2.closest('.monaco-editor'))) return
  // §25: undo/redo
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
    e.preventDefault()
    if (e.shiftKey) void store.redo()
    else void store.undo()
    return
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'y') {
    e.preventDefault()
    void store.redo()
    return
  }
  // §25: Agent palette
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    const sel = graph.getSelectedCells().filter((c) => c.isNode()).map((c) => c.id)
    if (sel.length) design.agentName(sel)
    return
  }
  if (e.key !== 'Delete' && e.key !== 'Backspace') return
  const cells = graph.getSelectedCells()
  if (!cells.length) return
  e.preventDefault()
  const blockIds = cells.filter((c) => c.isNode()).map((c) => c.id)
  const edgeIds = cells.filter((c) => c.isEdge()).map((c) => c.id)
  if (edgeIds.length) void store.deleteNets(edgeIds)
  if (blockIds.length) void store.deleteBlocks(blockIds)
  graph.cleanSelection()
}

onMounted(mount)

watch(
  () => [store.dto, store.currentParentBlockId, store.selectedBlockId],
  () => syncGraph(),
  { deep: false },
)

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  graph?.dispose()
  graph = null
  rendered.clear()
  renderedEdges.clear()
})

defineExpose({
  getSelectedBlockIds(): string[] {
    if (!graph) return []
    return graph.getSelectedCells().filter((c) => c.isNode()).map((c) => c.id)
  },
  selectOnly(ids: string[]): void {
    if (!graph) return
    graph.resetSelection(ids)
  },
  /** Programmatic port connection (deterministic tests / automation). */
  connectPorts(sourcePortId: string, targetPortId: string): void {
    void store.connectPorts(sourcePortId, targetPortId)
  },
})
</script>

<template>
  <div ref="container" class="flow-canvas"></div>
</template>

<style>
.flow-canvas {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
  background: var(--canvas-bg);
}
.flow-canvas .x6-graph-svg { display: block; }
.flow-canvas .x6-node text { cursor: default; }
.flow-canvas .x6-edge { cursor: pointer; }
.flow-canvas .x6-port-body {
  transition: r 0.15s ease;
}
.flow-canvas .x6-node:hover .x6-port-body { r: 6px; }
</style>
