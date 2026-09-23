<script setup lang="ts">
// WORKBENCH §7: topology canvas pane — the drill/overlay/scope/view
// toolbar + the X6 canvas.  The workbench shell owns top bar, explorer,
// inspector rail and the bottom dock; this pane is the center content.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useTopoStore } from '../stores/topo'
import { useWorkbenchStore } from '../stores/workbench'
import { useToastStore } from '../stores/toast'
import { isApiError } from '../api/client'
import { RELATION_KINDS, type RelationKind } from '../domain/topo'
import TopoCanvas from '../components/topo/TopoCanvas.vue'
import CapabilityBar from '../components/workbench/CapabilityBar.vue'
import FlowTopologyCanvas from '../components/topology/FlowTopologyCanvas.vue'
import FlowScenarioBar from '../components/topology/FlowScenarioBar.vue'

const store = useTopoStore()
const workbench = useWorkbenchStore()
const toast = useToastStore()

const canvasRef = ref<InstanceType<typeof TopoCanvas> | null>(null)
const showCapability = ref(true)

const meta = computed(() => store.bundle?.meta ?? null)
const pathLabel = computed(() => {
  const labels: Array<{ label: string; kind: string; id: string }> = [
    { label: 'Repository', kind: 'root', id: 'root' },
    ...store.path.map((c, i) => ({
      label: c.label,
      kind: store.path[i].kind,
      id: store.path[i].id,
    })),
  ]
  return labels.map((l, i) => ({ ...l, depth: i }))
})

const overlayCount = computed(() => meta.value?.overlay_stats ?? {})

const SCOPE_BTNS = [
  ['all', 'All'],
  ['module', 'Current Module'],
  ['file', 'Current File'],
  ['selection', 'Selection'],
] as const

onMounted(async () => {
  // dev-only hook for deterministic E2E (scene counts, selection).
  if (import.meta.env.DEV) (window as any).__topoStore = store
  try {
    if (!store.lanes.length) await store.loadLanes()
    if (!store.lane) {
      const preferred = store.lanes.find((l) => l.lane === 'jpl')
      await store.loadLane(preferred?.lane ?? store.lanes[0]?.lane ?? '')
    }
    await store.loadDataInterfaces()
    await store.loadSyncStatus()
    store.startSyncPolling()     // CROSS-PROJECTION-SYNC0: revision watch
  } catch (err) {
    if (isApiError(err)) toast.reportError(err)
    else toast.reportError(err)
  }
})

onUnmounted(() => store.stopSyncPolling())

async function onSyncRefresh(): Promise<void> {
  await store.refreshSync()
}

async function onKernDebug(e: Event): Promise<void> {
  store.showKernelDebug = (e.target as HTMLInputElement).checked
  if (store.showKernelDebug && !store.kernelSummary) await store.loadKernel()
}

const syncTitle = computed(() => {
  const s = store.syncStatus
  if (!s) return 'sync status unavailable'
  const ps = s.projections ?? {}
  return Object.entries(ps)
    .map(([k, v]) => `${k}=${(v as any).status}`).join(' · ')
})

async function onLaneChange(id: string): Promise<void> {
  try {
    await store.loadLane(id)
  } catch (err) {
    if (isApiError(err)) toast.reportError(err)
    else toast.reportError(err)
  }
}

function overlayBtn(kind: RelationKind): { label: string; on: boolean; count: number } {
  const label: Record<RelationKind, string> = {
    CALL: 'CALL', DATA: 'DATA', STATE: 'STATE', CONTROL: 'CONTROL',
    TIME: 'TIME', RESOURCE: 'RESOURCE',
  }
  return {
    label: label[kind],
    on: store.overlays.has(kind),
    count: overlayCount.value[kind] ?? 0,
  }
}

function goto(depth: number): void {
  if (depth < store.path.length) store.popTo(depth)
}

function setLevel(target: 1 | 2 | 3): void {
  if (target === 1) {
    store.popTo(0)
    return
  }
  if (target === 2) {
    if (store.path.length === 0) {
      const first = store.bundle?.declared_modules[0]
      if (first) store.push({ kind: 'module', id: first, label: first })
    } else {
      store.popTo(1)
    }
    return
  }
  if (target === 3) {
    if (store.path.length === 0) {
      const firstMod = store.bundle?.declared_modules[0]
      const firstFile = firstMod ? store.index?.filesByModule.get(firstMod)?.[0] : null
      if (firstMod && firstFile) {
        store.push({ kind: 'module', id: firstMod, label: firstMod })
        store.push({ kind: 'file', id: firstFile, label: firstFile.split('/').slice(-1)[0] })
      }
    } else if (store.path.length === 1) {
      const firstFile = store.index?.filesByModule.get(store.path[0].id)?.[0]
      if (firstFile) store.push({ kind: 'file', id: firstFile, label: firstFile.split('/').slice(-1)[0] })
    } else {
      store.popTo(2)
    }
  }
}

function onDrcclick(): void {
  workbench.setDockTab('drc')
  if (!store.drc) void store.loadDrc()
}
</script>

<template>
  <div class="topo-pane">
    <header class="tp-toolbar">
      <select
        class="tp-lane" :value="store.lane ?? ''" data-testid="lane-select"
        @change="onLaneChange(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="l in store.lanes" :key="l.lane" :value="l.lane">
          {{ l.label }} ({{ l.lane }}{{ l.synthetic ? ' · synthetic' : '' }})
        </option>
      </select>

      <span class="tp-plane" data-testid="topology-plane-badge"
            :title="$t('wb.plane.asisNote')">│ {{ $t('wb.plane.staticBadge') }}</span>

      <nav class="tp-seg" data-testid="view-mode-seg">
        <button
          type="button" class="tp-seg-btn"
          :class="{ on: store.viewMode === 'flow' }"
          data-testid="view-flow"
          @click="store.viewMode = 'flow'"
        >{{ $t('wb.flow.viewFlow') }}</button>
        <button
          type="button" class="tp-seg-btn"
          :class="{ on: store.viewMode === 'topology' && store.level === 1 }"
          data-testid="view-module"
          @click="store.viewMode = 'topology'; setLevel(1)"
        >{{ $t('wb.level.module') }}</button>
        <button
          type="button" class="tp-seg-btn"
          :class="{ on: store.viewMode === 'topology' && store.level === 2 }"
          data-testid="view-file"
          @click="store.viewMode = 'topology'; setLevel(2)"
        >{{ $t('wb.level.file') }}</button>
        <button
          type="button" class="tp-seg-btn"
          :class="{ on: store.viewMode === 'topology' && store.level === 3 }"
          data-testid="view-fn"
          @click="store.viewMode = 'topology'; setLevel(3)"
        >{{ $t('wb.level.fn') }}</button>
      </nav>

      <nav class="tp-seg" data-testid="level-seg">
        <button
          v-for="lvl in ([1, 2, 3] as const)" :key="lvl"
          type="button" class="tp-seg-btn"
          :class="{ on: store.level === lvl }"
          :data-testid="`level-${lvl}`"
          @click="setLevel(lvl)"
        >{{ lvl === 1 ? $t('wb.level.module') : lvl === 2 ? $t('wb.level.file') : $t('wb.level.fn') }}</button>
      </nav>

      <nav class="tp-seg" data-testid="scope-seg">
        <button
          v-for="s in SCOPE_BTNS"
          :key="s[0]"
          type="button" class="tp-seg-btn"
          :class="{ on: store.scope === s[0] }"
          :data-testid="`scope-${s[0]}`"
          @click="store.applyScope(s[0])"
        >{{ $t(`wb.scope.${s[0]}`) }}</button>
      </nav>

      <nav class="tp-seg" data-testid="view-seg">
        <button type="button" class="tp-seg-btn" data-testid="view-fit" @click="canvasRef?.fitView()">
          {{ $t('wb.toolbar.fit') }}
        </button>
        <button type="button" class="tp-seg-btn" data-testid="view-layout" @click="store.relayout()">
          {{ $t('wb.toolbar.autoLayout') }}
        </button>
        <button
          type="button" class="tp-seg-btn" :class="{ on: store.showMiniMap }"
          data-testid="view-minimap" @click="store.setMiniMap(!store.showMiniMap)"
        >{{ $t('wb.toolbar.miniMap') }}</button>
      </nav>

      <span class="tp-spacer"></span>

      <label class="tp-check">
        <input type="checkbox" :checked="store.hideCrossCutting"
          data-testid="hide-cross-cutting"
          @change="store.setHideCrossCutting(($event.target as HTMLInputElement).checked)" />
        {{ $t('wb.toolbar.hideCC') }}
      </label>
      <button
        type="button" class="tp-btn" :class="{ active: workbench.dockTab === 'drc' && workbench.dockOpen }"
        @click="onDrcclick"
        data-testid="drc-toggle"
      >
        {{ $t('wb.toolbar.drc') }}{{ store.drc ? ` (${store.drc.summary.total})` : '' }}
      </button>
      <button
        type="button" class="tp-btn" data-testid="route-c-toggle"
        @click="store.showRouteCDebug = !store.showRouteCDebug"
      >
        {{ $t('wb.toolbar.routeC') }}
      </button>
      <button
        type="button" class="tp-btn ghosty"
        @click="showCapability = !showCapability"
        data-testid="capability-toggle"
      >
        {{ $t('wb.toolbar.capInfo') }} {{ showCapability ? '▾' : '▸' }}
      </button>
      <label class="tp-check">
        <input type="checkbox" :checked="store.showDataInterfaces" data-testid="di-ports-toggle"
          @change="store.showDataInterfaces = ($event.target as HTMLInputElement).checked" />
        Show Data Interfaces
      </label>
      <label class="tp-check">
        <input type="checkbox" :checked="store.showShapes" data-testid="di-shapes-toggle"
          @change="store.showShapes = ($event.target as HTMLInputElement).checked" />
        Shapes
      </label>
      <label class="tp-check">
        <input type="checkbox" :checked="store.showSizes" data-testid="di-sizes-toggle"
          @change="store.showSizes = ($event.target as HTMLInputElement).checked" />
        Sizes
      </label>
      <label class="tp-check" title="SEMANTIC-SUBSTRATE1 dev-mode: kernel substrate counts + support links (not a main gate)">
        <input type="checkbox" :checked="store.showKernelDebug" data-testid="kern-debug-toggle"
          @change="onKernDebug" />
        Kernel (dev)
      </label>      <span class="tp-sync" data-testid="sync-badge" :title="syncTitle">
        <span class="tp-sync-rev">rev {{ store.syncStatus?.revision_id ?? '…' }}</span>
        <button type="button" class="tp-sync-btn" data-testid="sync-refresh-btn"
          @click="onSyncRefresh">↻ sync</button>
      </span>
    </header>

    <CapabilityBar v-if="showCapability && meta" />

    <div class="tp-toolbar2">
      <nav class="tp-breadcrumb" data-testid="breadcrumb">
        <button
          v-for="(crumb, i) in pathLabel" :key="crumb.id"
          type="button" class="tp-crumb" :class="{ active: i === pathLabel.length - 1 }"
          @click="goto(crumb.depth)"
        >
          {{ crumb.kind === 'root' ? $t('wb.breadcrumb.root') : crumb.label }}
        </button>
        <span v-if="store.path.length === 0" class="tp-scope">· {{ $t('wb.breadcrumb.moduleLvl') }}</span>
        <span v-else-if="store.path.length === 1" class="tp-scope">· {{ $t('wb.breadcrumb.fileLvl') }}</span>
        <span v-else class="tp-scope">· {{ $t('wb.breadcrumb.fnLvl') }}</span>
      </nav>
      <div class="tp-overlays" data-testid="overlays">
        <button
          v-for="k in RELATION_KINDS" :key="k"
          type="button"
          class="ov-btn" :class="[`ov-${k.toLowerCase()}`, { on: overlayBtn(k).on }]"
          :data-testid="`overlay-${k.toLowerCase()}`"
          :title="`${overlayBtn(k).count} edges of kind ${k} in lane`"
          @click="store.toggleOverlay(k)"
        >
          <span class="ov-check">{{ overlayBtn(k).on ? '✓' : '' }}</span> {{ overlayBtn(k).label }}
          <span class="ov-count">{{ overlayBtn(k).count }}</span>
        </button>
      </div>
    </div>

    <FlowScenarioBar v-if="store.viewMode === 'flow'" />
    <main class="tp-canvas-wrap">
      <div v-if="store.loading" class="tp-loading">{{ $t('common.loading') }}</div>
      <div v-else-if="store.error" class="tp-error">{{ store.error }}</div>
      <FlowTopologyCanvas v-else-if="store.viewMode === 'flow'" />
      <TopoCanvas v-else ref="canvasRef" />
    </main>
  </div>
</template>

<style scoped>
.topo-pane {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--canvas-bg);
}
.tp-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
  flex-shrink: 0;
}
.tp-plane {
  font-size: 10px; color: var(--text-muted); border: 1px solid var(--border);
  border-radius: 4px; padding: 1px 6px; white-space: nowrap;
}
.tp-lane {
  font-size: 11px;
  padding: 3px 6px;
  border-radius: 4px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text-primary);
  max-width: 220px;
}
.tp-seg { display: inline-flex; border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
.tp-seg-btn {
  border: none;
  border-right: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 10.5px;
  padding: 3px 8px;
  cursor: pointer;
}
.tp-seg-btn:last-child { border-right: none; }
.tp-seg-btn.on { background: var(--sel-bg); color: var(--sel-text); font-weight: 600; }
.tp-btn {
  font-size: 11px;
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  background: var(--panel);
  color: var(--text-primary);
  padding: 3px 8px;
  cursor: pointer;
}
.tp-btn.ghosty { background: var(--surface); }
.tp-btn.active { border-color: var(--accent); color: var(--accent); }
.tp-check { font-size: 11px; display: inline-flex; align-items: center; gap: 4px; color: var(--text-secondary); }
.tp-spacer { flex: 1; }
.tp-toolbar2 {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
  flex-wrap: wrap;
  flex-shrink: 0;
}
.tp-breadcrumb { display: inline-flex; align-items: center; gap: 4px; }
.tp-crumb {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 11.5px;
  color: var(--accent);
  padding: 1px 2px;
}
.tp-crumb.active { color: var(--text-primary); font-weight: 600; cursor: default; }
.tp-scope { font-size: 10.5px; color: var(--text-muted); }
.tp-overlays { display: inline-flex; gap: 5px; flex-wrap: wrap; }
.ov-btn {
  font-size: 10.5px;
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  padding: 2px 7px;
  background: var(--panel);
  cursor: pointer;
  display: inline-flex;
  gap: 4px;
  align-items: center;
  color: var(--text-primary);
}
.ov-btn .ov-check { width: 10px; display: inline-block; color: var(--ok); font-weight: 700; }
.ov-btn .ov-count { color: var(--text-muted); font-size: 9.5px; }
.ov-btn.on { background: var(--accent-soft); border-color: var(--accent); }
.ov-call.on { background: var(--chip-bg); border-color: var(--relation-call); }
.ov-data.on { background: var(--accent-soft); border-color: var(--relation-data); }
.ov-state.on { background: var(--violet-bg); border-color: var(--relation-state); }
.ov-control.on { background: var(--amber-bg); border-color: var(--relation-control); }
.ov-time.on { background: var(--chip-bg); border-color: var(--relation-time); }
.ov-resource.on { background: var(--resource-bg); border-color: var(--relation-resource); }
.tp-canvas-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
  background: var(--canvas-bg);
}
.tp-loading, .tp-error { padding: 20px; color: var(--text-muted); font-size: 13px; }
</style>
