<script setup lang="ts">
// WORKBENCH §3: Software EDA / Architecture Workbench shell.
// Compact top bar (brand · tabs · +new · lang/theme) | left Repository
// Explorer | center canvas | right Inspector | bottom dock (Source/Search/
// DRC/LVS/Synthesis/Runtime).  All rails resizable (§3).  Product planes
// unchanged: 拓扑 = evidence plane (read-only), 软件电路 = Design TO-BE.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useWorkbenchStore, type DockTab, type WorkbenchTab } from '../stores/workbench'
import { useTopoStore } from '../stores/topo'
import { useFlowStore } from '../stores/flow'
import { useDesignStore } from '../stores/design'
import { useSourceStore } from '../stores/source'
import { useSearchStore } from '../stores/search'
import { useWorkspaceStore } from '../stores/workspace'
import LangSwitch from '../components/LangSwitch.vue'
import ThemeSwitch from '../components/ThemeSwitch.vue'
import NewTopologyDialog from '../components/NewTopologyDialog.vue'
import TopologyTree from '../components/workbench/TopologyTree.vue'
import SourceViewer from '../components/SourceViewer.vue'
import ContextMenu from '../components/ContextMenu.vue'
import SearchResults from '../components/SearchResults.vue'
import TopoInspector from '../components/topo/TopoInspector.vue'
import FlowInspector from '../components/topology/FlowInspector.vue'
import DrcPanel from '../components/topo/DrcPanel.vue'
import LvsPanel from '../components/flow/LvsPanel.vue'
import SynthesisPanel from '../components/flow/SynthesisPanel.vue'
import TopologyPane from './TopologyPane.vue'
import FlowView from './FlowView.vue'
// UI-REALITY-ALIGN0: perspectives (one selection, one evidence universe)
import { useSelectionStore } from '../stores/selection'
import { useCircuitStore } from '../stores/circuit'
import PerspectiveRail from '../components/workbench/PerspectiveRail.vue'
import SoftwareCircuit from '../components/circuit/SoftwareCircuit.vue'
import CircuitInspector from '../components/circuit/CircuitInspector.vue'
import RuntimePane from '../components/workbench/RuntimePane.vue'
import PerformancePane from '../components/workbench/PerformancePane.vue'
import DataPane from '../components/workbench/DataPane.vue'
import RuntimeTimeline from '../components/workbench/RuntimeTimeline.vue'
import AgentActivityPanel from '../components/workbench/AgentActivityPanel.vue'
import RealityPanel from '../components/workbench/RealityPanel.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const wb = useWorkbenchStore()
const topo = useTopoStore()
const flow = useFlowStore()
const design = useDesignStore()
const source = useSourceStore()
const search = useSearchStore()
const workspace = useWorkspaceStore()
const selection = useSelectionStore()
const circuit = useCircuitStore()

const TABS: Array<{ key: WorkbenchTab; label: () => string }> = [
  { key: 'topology', label: () => t('wb.tabs.topology') },
  { key: 'circuit', label: () => t('wb.tabs.circuit') },
  { key: 'drc', label: () => t('wb.tabs.drc') },
  { key: 'lvs', label: () => t('wb.tabs.lvs') },
  { key: 'synthesis', label: () => t('wb.tabs.synthesis') },
  { key: 'runtime', label: () => t('wb.tabs.runtime') },
]
const DOCK_TABS: Array<{ key: DockTab; label: () => string }> = [
  { key: 'source', label: () => t('wb.dock.source') },
  { key: 'search', label: () => t('wb.dock.search') },
  { key: 'drc', label: () => t('wb.dock.drc') },
  { key: 'lvs', label: () => t('wb.dock.lvs') },
  { key: 'synthesis', label: () => t('wb.dock.synthesis') },
  { key: 'runtime', label: () => t('wb.dock.runtime') },
  { key: 'agent', label: () => t('wb.dock.agent') },
]

// §23: the AS-IS evidence plane renders the selected PERSPECTIVE; the design
// plane (软件电路 tab) is a different product plane (TO-BE) and stays separate.
const perspective = computed(() => wb.perspective)
// The inspector follows BOTH the plane and the lens: the AS-IS architecture and
// flow lenses keep their own inspectors (which own the flow/semantic panels),
// while the new lenses (circuit/data/runtime/performance) share the
// selection-driven CircuitInspector.
const inspectorKind = computed(() => {
  if (wb.tab === 'circuit') return 'design'
  if (perspective.value === 'circuit' || perspective.value === 'data'
      || perspective.value === 'runtime' || perspective.value === 'performance') return 'circuit'
  return topo.viewMode === 'flow' ? 'flow' : 'topo'
})

watch(perspective, (p) => {
  if (wb.tab === 'circuit') return
  topo.viewMode = p === 'flow' ? 'flow' : 'topology'
}, { immediate: true })

const showExplorer = computed(() => wb.tab !== 'circuit')
const showInspector = computed(() => wb.tab !== 'circuit')

const sourceRepoId = computed(() =>
  topo.bundle?.meta.source_repo_id ?? flow.flow?.repoId ?? workspace.repoId)

const hasFlow = computed(() => !!flow.flow)

// §26: density is one switch, applied as an attribute — not a settings sprawl.
watch(() => wb.density, (d) => {
  document.documentElement.dataset.density = d
}, { immediate: true })

onMounted(async () => {
  if (import.meta.env.DEV) (window as any).__workbench = wb
  window.addEventListener('keydown', onSearchKey)
  // route → tab
  if (route.name === 'workbench-flow' || route.path.startsWith('/flows/')) {
    const id = route.params.id as string
    wb.setFlow(id)
    wb.setTab('circuit')
  } else {
    wb.setTab('topology')
  }
})

// open the source dock when the topology store asks for a source (§6)
watch(
  () => topo.sourceTarget,
  async (target) => {
    if (!target) return
    wb.setDockTab('source')
    await source.open(target.path, target.line, target.repoId ?? '', undefined,
                      target.span ?? null)
  },
)

// §21/A16: any surface can request an EXACT SourceSpan; the shell opens the
// source dock with the real range instead of "open file and search".
watch(
  () => wb.sourceRequestSeq,
  async () => {
    const req = wb.sourceRequest
    if (!req) return
    wb.setDockTab('source')
    // repo resolution order: explicit request → lane bundle → hydrated workspace
    const repoId = req.repoId || sourceRepoId.value || workspace.repoId || ''
    await source.open(req.file, req.line, repoId, undefined, req.span ?? null)
  },
)

// §7: one Current Selection — the existing canvases feed it (no second model).
watch(() => topo.selection, (sel) => {
  if (!sel) return
  const node = sel.type === 'node' ? topo.scene?.nodes?.find((n: any) => n.id === sel.id) : null
  if (node) {
    const span = (node as any).span ?? null
    selection.set({
      kind: 'graph-node', id: sel.id, label: (node as any).label ?? sel.id,
      file: span?.file ?? null, line: span?.start_line ?? null, span,
      plane: 'STATIC', truth_class: 'RESOLVED',
      coverage: (node as any).capability ?? 'UNKNOWN',
      authority: 'GET /api/v1/topology-view/{lane}',
      payload: { node },
    })
  } else if (sel.type === 'edge') {
    selection.set({ kind: 'graph-edge', id: sel.id, label: sel.id, plane: 'STATIC' })
  }
}, { deep: true })

// open lane data eagerly so dock lanes (DRC) are usable from any tab
onMounted(async () => {
  try {
    if (!topo.lanes.length) await topo.loadLanes()
    if (!topo.lane && topo.lanes.length) {
      const preferred = topo.lanes.find((l) => l.lane === 'jpl')
      await topo.loadLane(preferred?.lane ?? topo.lanes[0].lane ?? '')
    }
  } catch {
    // reported by the pane/store
  }
})

function onTabClick(key: WorkbenchTab): void {
  if (key === 'topology') {
    wb.setTab('topology')
    if (route.name === 'workbench-flow' || route.path.startsWith('/flows/')) router.replace('/topology')
    return
  }
  if (key === 'drc' || key === 'lvs' || key === 'synthesis' || key === 'runtime') {
    wb.setTab(key)
    if (key === 'drc' && topo.lane && !topo.drc) void topo.loadDrc()
    return
  }
  wb.setTab(key)
}

// ---- resizable rails (§3) --------------------------------------------------
const MIN_LEFT = 180, MAX_LEFT = 520, MIN_RIGHT = 220, MAX_RIGHT = 620
const MIN_DOCK = 90, MAX_DOCK = 520
const dragging = ref(false)

function startDragRail(kind: 'left' | 'right' | 'dock', ev: PointerEvent): void {
  const startX = ev.clientX
  const startY = ev.clientY
  const w0 = wb.leftWidth
  const r0 = wb.rightWidth
  const d0 = wb.dockHeight
  dragging.value = true
  const onMove = (e: PointerEvent): void => {
    if (kind === 'left') {
      const next = Math.min(MAX_LEFT, Math.max(MIN_LEFT, w0 + e.clientX - startX))
      wb.leftWidth = next
    } else if (kind === 'right') {
      const next = Math.min(MAX_RIGHT, Math.max(MIN_RIGHT, r0 - (e.clientX - startX)))
      wb.rightWidth = next
    } else {
      const next = Math.min(MAX_DOCK, Math.max(MIN_DOCK, d0 - (e.clientY - startY)))
      wb.dockHeight = next
    }
  }
  const onUp = (): void => {
    dragging.value = false
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    wb.persistLayout()
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  ev.preventDefault()
}

function onFlowCreated(flowId: string): void {
  design.showNewTopology = false
  wb.setFlow(flowId)
  wb.setTab('circuit')
  router.push(`/flows/${flowId}`)
}

// §20 navigation: search + selection + breadcrumb instead of remembering IDs.
const searchQuery = ref('')
async function runSearch(): Promise<void> {
  search.setQuery(searchQuery.value)
  await search.search(sourceRepoId.value ?? workspace.repoId ?? '', undefined,
                      { debounce: false })
  wb.setDockTab('search')
}

function onShellSearchResult(item: any): void {
  openSearchResult(item)
  wb.setDockTab('source')
}

function openSearchResult(item: { id: string; name?: string; path?: string; line?: number | null }): void {
  selection.set({
    kind: item.id.startsWith('node:') ? 'graph-node' : 'file',
    id: item.id, label: item.name ?? item.id,
    file: item.path ?? null, line: item.line ?? null, plane: 'STATIC',
    truth_class: 'RESOLVED', coverage: 'COMPLETE',
    authority: 'GET /api/v1/search',
  })
  if (workspace.repoId) workspace.setFocus({ plane: 'evidence', entityType: 'node', id: item.id })
  if (item.path) {
    void source.open(item.path, item.line ?? null,
                     sourceRepoId.value ?? workspace.repoId ?? '', undefined, null)
  }
}

function onSearchKey(ev: KeyboardEvent): void {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'f') {
    ev.preventDefault()
    const el = document.querySelector<HTMLInputElement>('[data-testid="shell-search"]')
    el?.focus()
  }
}
</script>

<template>
  <div class="workbench-view" data-testid="topology-view">
    <header class="wb-topbar">
      <button class="wb-back" :title="t('wb.backTitle')" @click="router.push('/')">←</button>
      <span class="wb-brand">rintel <span class="wb-brand-sub">Software EDA</span></span>
      <span class="wb-ctx mono" data-testid="topbar-context" :title="topo.bundle?.meta.label ?? ''">
        {{ topo.lane ?? '—' }}<template v-if="topo.bundle">&nbsp;·&nbsp;{{ topo.bundle.meta.languages.join('/') }}</template>
      </span>
      <nav class="wb-tabs" data-testid="wb-tabs">
        <button
          v-for="tab in TABS" :key="tab.key"
          type="button" class="wb-tab"
          :class="{ active: wb.tab === tab.key || (wb.dockOpen && wb.dockTab === tab.key) }"
          :data-testid="`wbtab-${tab.key}`"
          @click="onTabClick(tab.key)"
        >{{ tab.label() }}</button>
      </nav>
      <span class="wb-spacer"></span>
      <input
        v-model="searchQuery" class="wb-search" data-testid="shell-search"
        :placeholder="t('wb.explorer.search')" @keyup.enter="runSearch"
      />
      <button type="button" class="wb-btn" data-testid="shell-search-run" @click="runSearch">🔍</button>
      <button
        type="button" class="wb-btn" data-testid="topology-new"
        @click="design.showNewTopology = true"
      >{{ t('topbar.newTopology') }}</button>
      <button type="button" class="wb-btn" data-testid="density-toggle"
              :title="t('wb.density.hint')" @click="wb.toggleDensity()">
        {{ wb.density === 'compact' ? t('wb.density.compact') : t('wb.density.normal') }}
      </button>
      <button type="button" class="wb-btn" data-testid="reality-open"
              :title="t('wb.reality.hint')" @click="wb.realityOpen = true">
        {{ t('wb.reality.title') }}
      </button>
      <LangSwitch />
      <ThemeSwitch />
    </header>

    <div class="wb-body">
      <aside v-if="showExplorer" class="wb-left" :style="{ width: wb.leftWidth + 'px' }">
        <div class="wb-panel-head">{{ t('wb.explorer.title') }} <span class="wb-panel-note">{{ t('wb.explorer.lane') }} {{ topo.lane ?? '—' }}</span></div>
        <div class="wb-panel-body">
          <TopologyTree />
        </div>
      </aside>
      <div v-if="showExplorer" class="wb-split v" data-testid="split-left"
           @pointerdown="startDragRail('left', $event)"><div class="wb-split-grip"></div></div>

      <PerspectiveRail v-if="wb.tab !== 'circuit'" />

      <main class="wb-center">
        <template v-if="wb.tab !== 'circuit'">
          <TopologyPane v-if="perspective === 'architecture' || perspective === 'flow'" />
          <SoftwareCircuit v-else-if="perspective === 'circuit'" />
          <DataPane v-else-if="perspective === 'data'" />
          <RuntimePane v-else-if="perspective === 'runtime'" />
          <PerformancePane v-else-if="perspective === 'performance'" />
        </template>
        <FlowView v-else />
      </main>

      <div v-if="showInspector" class="wb-split v" data-testid="split-right"
           @pointerdown="startDragRail('right', $event)"><div class="wb-split-grip"></div></div>
      <aside v-if="showInspector" class="wb-right" :style="{ width: wb.rightWidth + 'px' }">
        <div class="wb-panel-head">
          {{ t('wb.toolbar.inspector') }}
          <span class="wb-panel-note mono">{{ inspectorKind }}</span>
        </div>
        <div class="wb-panel-body">
          <CircuitInspector v-if="inspectorKind === 'circuit'" />
          <FlowInspector v-else-if="inspectorKind === 'flow'" />
          <TopoInspector v-else />
        </div>
      </aside>
    </div>

    <div v-if="wb.dockOpen" class="wb-dock" data-testid="bottom-dock" :style="{ height: wb.dockHeight + 'px' }">
      <div class="wb-split h" data-testid="split-dock"
           @pointerdown="startDragRail('dock', $event)"><div class="wb-split-grip"></div></div>
      <nav class="wb-dock-tabs" data-testid="dock-tabs">
        <button
          v-for="dt in DOCK_TABS" :key="dt.key"
          type="button" class="wb-dock-tab"
          :class="{ active: wb.dockTab === dt.key }"
          :data-testid="`${dt.key}-docktab`"
          @click="wb.setDockTab(dt.key)"
        >{{ dt.label() }}</button>
        <button type="button" class="wb-dock-close" data-testid="dock-close" @click="wb.dockOpen = false">×</button>
      </nav>
      <div class="wb-dock-body">
        <div v-show="wb.dockTab === 'source'" class="wb-dock-pane" data-testid="source-dock">
          <div class="dock-head">
            <span class="mono muted">{{ source.openPath ?? '' }}</span>
          </div>
          <div class="dock-content">
            <SourceViewer :repo-id="sourceRepoId" />
          </div>
        </div>
        <div v-show="wb.dockTab === 'search'" class="wb-dock-pane" data-testid="search-dock">
          <SearchResults v-if="search.results.length > 0" :items="search.results"
                         @open="onShellSearchResult" />
          <div v-else class="empty">{{ $t('wb.search.empty') }}</div>
        </div>
        <div v-show="wb.dockTab === 'drc'" class="wb-dock-pane">
          <DrcPanel />
        </div>
        <div v-show="wb.dockTab === 'lvs'" class="wb-dock-pane">
          <LvsPanel
            v-if="hasFlow"
            :flow-id="flow.flow!.id"
            :repo-id="flow.flow!.repoId ?? null"
          />
          <div v-else class="empty">{{ t('wb.circuit.emptyTitle') }}</div>
        </div>
        <div v-show="wb.dockTab === 'synthesis'" class="wb-dock-pane">
          <SynthesisPanel v-if="hasFlow" :flow-id="flow.flow!.id" />
          <div v-else class="empty">{{ t('wb.circuit.emptyTitle') }}</div>
        </div>
        <div v-show="wb.dockTab === 'runtime'" class="wb-dock-pane" data-testid="runtime-dock-pane">
          <RuntimeTimeline />
        </div>
        <div v-show="wb.dockTab === 'agent'" class="wb-dock-pane" data-testid="agent-dock-pane">
          <AgentActivityPanel />
        </div>
      </div>
    </div>

    <ContextMenu
      v-if="design.ctx"
      :x="design.ctx.x"
      :y="design.ctx.y"
      :items="design.ctx.items as never"
      @close="design.closeCtx()"
    />
    <NewTopologyDialog
      v-if="design.showNewTopology"
      :repo-id="sourceRepoId"
      @close="design.showNewTopology = false"
      @created="onFlowCreated"
    />
    <RealityPanel v-if="wb.realityOpen" @close="wb.realityOpen = false" />
  </div>
</template>

<style scoped>
.workbench-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--app-bg);
  color: var(--text-primary);
  font-size: 12px;
}
/* ---- top bar ---- */
.wb-topbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  min-height: 36px;
}
.wb-back {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-secondary);
  border-radius: 4px;
  cursor: pointer;
  padding: 1px 7px;
  font-size: 13px;
}
.wb-brand { font-weight: 700; font-size: 13px; letter-spacing: -0.01em; }
.wb-brand-sub { color: var(--text-muted); font-weight: 500; font-size: 11px; }
.wb-ctx {
  font-size: 10.5px;
  color: var(--text-secondary);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 6px;
  flex-shrink: 0;
}
.wb-tabs { display: inline-flex; border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
.wb-tab {
  border: none;
  border-right: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 11px;
  padding: 3px 10px;
  cursor: pointer;
}
.wb-tab:last-child { border-right: none; }
.wb-tab.active { background: var(--sel-bg); color: var(--sel-text); font-weight: 600; }
.wb-search {
  font-size: 11px;
  padding: 2px 7px;
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  background: var(--surface);
  color: var(--text-primary);
  width: 200px;
}
.wb-btn {
  border: 1px solid var(--border-strong);
  background: var(--panel);
  color: var(--text-primary);
  border-radius: 4px;
  padding: 3px 9px;
  font-size: 11px;
  cursor: pointer;
}
.wb-btn:hover { border-color: var(--accent); color: var(--accent); }
.wb-spacer { flex: 1; }
/* ---- body rails ---- */
.wb-body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.wb-left, .wb-right {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--panel);
}
.wb-panel-head {
  padding: 4px 8px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  flex-shrink: 0;
}
.wb-panel-note { text-transform: none; font-weight: 400; }
.wb-panel-body { flex: 1; min-height: 0; overflow: hidden; }
.wb-center {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.wb-center > * { flex: 1; min-height: 0; }
/* ---- splitters ---- */
.wb-split {
  flex-shrink: 0;
  background: var(--panel);
  position: relative;
}
.wb-split.v { width: 5px; cursor: col-resize; }
.wb-split.h { height: 5px; cursor: row-resize; }
.wb-split:hover, .wb-split:active { background: var(--accent); opacity: 0.6; }
/* ---- dock ---- */
.wb-dock {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border-top: 1px solid var(--border);
  min-height: 0;
}
.wb-dock-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px 8px 0;
  flex-shrink: 0;
}
.wb-dock-tab {
  border: 1px solid transparent;
  border-bottom: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 10.5px;
  padding: 3px 10px;
  border-radius: 4px 4px 0 0;
  cursor: pointer;
}
.wb-dock-tab.active {
  background: var(--surface);
  border-color: var(--border);
  color: var(--text-primary);
  font-weight: 600;
}
.wb-dock-close {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 13px;
  padding: 0 6px;
}
.wb-dock-close:hover { color: var(--danger); }
.wb-dock-body { flex: 1; min-height: 0; overflow: auto; background: var(--surface); }
.wb-dock-pane { height: 100%; min-height: 0; display: flex; flex-direction: column; }
.dock-head {
  display: flex;
  align-items: center;
  padding: 3px 10px;
  border-bottom: 1px solid var(--border);
  font-size: 10.5px;
}
.dock-content { flex: 1; min-height: 0; }
.empty { padding: 18px 14px; color: var(--text-muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.muted { color: var(--text-muted); }
.runtime-title { padding: 10px 12px 4px; font-weight: 600; font-size: 12px; }
.runtime-note { padding: 0 12px 12px; color: var(--text-muted); font-size: 11px; line-height: 1.6; }
</style>
