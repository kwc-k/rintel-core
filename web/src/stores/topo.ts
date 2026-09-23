// Software Topology store (TOPO-UI0).  Holds the frozen-artifact bundle +
// view state; scene projection is pure (domain/topo.ts) and the canvas is
// just a renderer.  Nothing here mutates canonical topology (U4).
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as topoApi from '../api/topo'
import type { DrcFinding, DrcResult, LaneSummary } from '../api/topo'
import {
  buildIndex,
  type ContainerRef,
  projectScene,
  RELATION_KINDS,
  type RelationKind,
  type Scene,
  type TopoBundle,
} from '../domain/topo'

const DEFAULT_OVERLAYS = new Set<RelationKind>(['CALL', 'DATA'])
const ALL_OVERLAYS = new Set<RelationKind>(RELATION_KINDS)

function fileModuleOf(file: string, idx: NonNullable<ReturnType<typeof buildIndex>>): string | null {
  for (const [mod, files] of idx.filesByModule) {
    if (files.includes(file)) return mod
  }
  return null
}

export const useTopoStore = defineStore('topo', () => {
  const lanes = ref<LaneSummary[]>([])
  const lane = ref<string | null>(null)
  const bundle = ref<TopoBundle | null>(null)
  const index = ref<ReturnType<typeof buildIndex> | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const level = ref<1 | 2 | 3>(1)
  const path = ref<ContainerRef[]>([])
  const overlays = ref<Set<RelationKind>>(new Set(DEFAULT_OVERLAYS))
  const hideCrossCutting = ref(false)
  const showRouteCDebug = ref(false)

  // WORKBENCH §7: view state — scope filter, mini-map, manual re-layout.
  const scope = ref<'all' | 'module' | 'file' | 'selection'>('all')
  // DATA-INTERFACE0: derived port/shape enrichment (read-only display)
  const dataPorts = ref<Record<string, any[]>>({})
  const moduleInterfaces = ref<Record<string, any[]>>({})
  const flowInterfaces = ref<Record<string, any>>({})
  const diCapability = ref<Record<string, any> | null>(null)
  const showDataInterfaces = ref(true)
  const showShapes = ref(true)
  const showSizes = ref(true)
  const viewMode = ref<'flow' | 'topology'>('topology')
  const showMiniMap = ref(false)
  const layoutNonce = ref(0)

  // selection: node | edge | none
  const selection = ref<{ type: 'node' | 'edge'; id: string } | null>(null)
  // drill-down for module/file-level edges: expanded member-edge chain
  const drillEdge = ref<{ id: string } | null>(null)
  // source dock
  const sourceTarget = ref<{ repoId: string | null; path: string; line: number | null
                             span?: Record<string, any> | null } | null>(null)

  // SOFTWARE-DRC0 (spec §16)
  const drc = ref<DrcResult | null>(null)
  const drcLoading = ref(false)
  const drcError = ref<string | null>(null)
  const drcOpen = ref(false)
  const drcRuleFilter = ref<string | null>(null)
  const drcSeverityFilter = ref<string | null>(null)

  async function loadDrc(): Promise<void> {
    if (!lane.value) return
    drcLoading.value = true
    drcError.value = null
    try {
      drc.value = await topoApi.getDrc(lane.value)
    } catch (err) {
      drcError.value = err instanceof Error ? err.message : String(err)
    } finally {
      drcLoading.value = false
    }
  }

  async function loadLane(id: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const b = await topoApi.getTopologyBundle(id)
      bundle.value = b
      index.value = buildIndex(b)
      lane.value = id
      resetView()
      void loadDrc()
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      loading.value = false
    }
  }

  const scene = computed<Scene>(() => {
    if (!bundle.value || !index.value) {
      return { level: 1 as const, path: [], nodes: [], edges: [] }
    }
    return projectScene(bundle.value, index.value, {
      path: path.value,
      overlays: overlays.value,
      hideCrossCutting: hideCrossCutting.value,
    })
  })

  async function loadDataInterfaces(): Promise<void> {
    try {
      const { apiFetch } = await import('../api/client')
      // ports/shapes artifact = flat port records (each with fn + file)
      const ports = await apiFetch<any[]>('/data-interface/shapes')
      const byName: Record<string, any[]> = {}
      for (const p of ports) {
        byName[p.fn] = byName[p.fn] ?? []
        byName[p.fn].push(p)
      }
      dataPorts.value = byName
      moduleInterfaces.value = await apiFetch('/data-interface/module_interfaces')
      flowInterfaces.value = await apiFetch('/data-interface/flow_interfaces')
      diCapability.value = await apiFetch('/data-interface/capability')
    } catch { /* enrichment is optional; topology remains fully usable */ }
  }

  // ---- SEMANTIC-SUBSTRATE1 (dev-mode kernel debug) ------------------------
  // §32: dev-mode Inspector only — NOT a main gate.  Loads the kernel
  // summary + support refs (lightweight); the full 19MB bundle stays on
  // demand behind the kernel_lookup helper, not preloaded.
  const showKernelDebug = ref(false)
  const kernelSummary = ref<Record<string, any> | null>(null)
  const kernelRefs = ref<Record<string, any[]> | null>(null)

  async function loadKernel(): Promise<void> {
    try {
      const { apiFetch } = await import('../api/client')
      kernelSummary.value = await apiFetch('/data-interface/kernel_summary')
      kernelRefs.value = await apiFetch('/data-interface/support_refs')
    } catch { /* dev-mode enrichment is optional */ }
  }

  function kernelSupportFor(name: string): Record<string, number> {
    const refs = kernelRefs.value ?? {}
    const out: Record<string, number> = { ports: 0, flow_regions: 0, semantic_stages: 0 }
    const fn = (refs.functions ?? [] as any[]).find((f: any) => f.canonical_id === `function:${name}`)
    if (!fn) return out
    const sid = fn.kernel_symbol_id
    for (const k of ['ports', 'flow_regions', 'semantic_stages']) {
      out[k] = (refs[k] ?? []).filter((r: any) => r.kernel_symbol_id === sid).length
    }
    return out
  }

  // ---- CROSS-PROJECTION-SYNC0 --------------------------------------------
  // published evidence revision + projection statuses; the pane polls this
  // and re-loads the derived enrichment when the revision moves — the X6
  // graph state (selection, layout, drill path) is NEVER reset.
  const syncStatus = ref<Record<string, any> | null>(null)
  // optional fac_root override for the refresh POST (worktree runs/E2E)
  const syncFacRoot = ref<string>('')
  let syncPoll: ReturnType<typeof setInterval> | null = null

  async function loadSyncStatus(): Promise<void> {
    try {
      const { apiFetch } = await import('../api/client')
      syncStatus.value = await apiFetch('/sync/status')
    } catch { /* not served yet */ }
  }

  async function refreshSync(): Promise<boolean> {
    try {
      const { apiFetch } = await import('../api/client')
      const body = syncFacRoot.value ? { fac_root: syncFacRoot.value } : {}
      const st = await apiFetch<any>('/sync/refresh', { method: 'POST',
        body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } })
      await loadSyncStatus()
      if (st && st.revision_id) await loadDataInterfaces()
      return true
    } catch { return false }
  }

  function startSyncPolling(ms = 5000): void {
    if (syncPoll) return
    syncPoll = setInterval(async () => {
      const prev = (syncStatus.value as any)?.revision_id
      await loadSyncStatus()
      const cur = (syncStatus.value as any)?.revision_id
      if (prev && cur && cur !== prev) {
        await loadDataInterfaces()      // partial enrichment refresh only
      }
    }, ms)
  }

  function stopSyncPolling(): void {
    if (syncPoll) { clearInterval(syncPoll); syncPoll = null }
  }

  async function loadLanes(): Promise<void> {
    lanes.value = await topoApi.getLanes()
  }

  function resetView(): void {
    level.value = 1
    path.value = []
    overlays.value = new Set(DEFAULT_OVERLAYS)
    hideCrossCutting.value = false
    selection.value = null
    drillEdge.value = null
    sourceTarget.value = null
    drc.value = null
    drcRuleFilter.value = null
    drcSeverityFilter.value = null
    drcOpen.value = false
  }

  function setOverlay(kind: RelationKind, on: boolean): void {
    const next = new Set(overlays.value)
    if (on) next.add(kind)
    else next.delete(kind)
    overlays.value = next
  }

  function toggleOverlay(kind: RelationKind): void {
    setOverlay(kind, !overlays.value.has(kind))
  }

  function enableAllOverlays(): void {
    overlays.value = new Set(ALL_OVERLAYS)
  }

  function setHideCrossCutting(on: boolean): void {
    hideCrossCutting.value = on
  }

  /** WORKBENCH §7 scope filter — attach the CURRENT selection's container
   * to the drill path (honest: scope is the container, never a fabricated
   * subgraph). */
  function applyScope(next: 'all' | 'module' | 'file' | 'selection'): void {
    scope.value = next
    if (next === 'all') {
      popTo(0)
      return
    }
    const sel = selection.value
    if (!sel || sel.type !== 'node') return
    const id = sel.id
    const idx = index.value
    if (next === 'module') {
      if (id.startsWith('module:')) { popTo(0); selectNode(id); return }
      if (id.startsWith('sugg:')) { popTo(0); selectNode(id); return }
      const mod = id.startsWith('file:')
        ? (idx?.filesByModule ? fileModuleOf(id.slice('file:'.length), idx) : null)
        : idx?.modOfFn.get(id)
      if (mod) {
        popTo(0)
        push({ kind: 'module', id: mod, label: mod })
        selectNode(null)
      }
      return
    }
    if (next === 'file') {
      const file = id.startsWith('file:')
        ? id.slice('file:'.length)
        : idx?.fileOf.get(id)
      const fn = file ? idx?.fnIdsByFile.get(file) ?? [] : []
      if (file && fn.length) {
        const mod = fileModuleOf(file, idx!)
        popTo(0)
        if (mod) push({ kind: 'module', id: mod, label: mod })
        push({ kind: 'file', id: file, label: file.split('/').slice(-1)[0] })
        selectNode(null)
      }
      return
    }
    // selection: keep the selected node as-is (canvas fit handles zoom)
  }

  function relayout(): void {
    layoutNonce.value += 1
  }

  function setMiniMap(on: boolean): void {
    showMiniMap.value = on
  }

  function push(container: ContainerRef): void {
    path.value = [...path.value, container]
    level.value = (path.value.length + 1) as 1 | 2 | 3
    selection.value = null
    drillEdge.value = null
  }

  function popTo(depth: number): void {
    path.value = path.value.slice(0, depth)
    level.value = (path.value.length + 1) as 1 | 2 | 3
    selection.value = null
    drillEdge.value = null
  }

  function pop(): void {
    popTo(path.value.length - 1)
  }

  // children containers of a scene node (used for expansion decision)
  function childrenOf(nodeId: string): ContainerRef[] {
    if (!bundle.value || !index.value) return []
    const idx = index.value
    if (nodeId.startsWith('module:')) {
      const mod = nodeId.slice('module:'.length)
      const files = idx.filesByModule.get(mod) ?? []
      if (!files.length) return []
      return files.map((f) => ({
        kind: 'file' as const,
        id: f,
        label: f.split('/').slice(-1)[0],
      }))
    }
    if (nodeId.startsWith('sugg:')) {
      const i = Number(nodeId.slice('sugg:'.length))
      const files = idx.suggFiles.get(i) ?? []
      if (!files.length) return []
      return files.map((f) => ({
        kind: 'file' as const,
        id: f,
        label: f.split('/').slice(-1)[0],
      }))
    }
    if (nodeId.startsWith('file:')) {
      const f = nodeId.slice('file:'.length)
      const fns = idx.fnIdsByFile.get(f) ?? []
      return fns.map((cid) => ({
        kind: 'file' as const,
        id: cid,
        label: 'function',
      }))
    }
    return []
  }

  function selectNode(id: string | null): void {
    if (!id) selection.value = null
    else selection.value = { type: 'node', id }
  }

  function selectEdge(id: string | null): void {
    if (!id) selection.value = null
    else selection.value = { type: 'edge', id }
  }

  function selectDrillEdge(id: string | null): void {
    drillEdge.value = id ? { id } : null
  }

  function openSource(repoId: string | null, path: string, line: number | null,
                      span?: Record<string, any> | null): void {
    sourceTarget.value = { repoId, path, line, span: span ?? null }
  }

  /** DRC finding → select the referenced topology object (§16). */
  function selectFindingSubject(subject: string): void {
    if (!bundle.value || !index.value) return
    const idx = index.value
    // suggestion candidate?
    const si = bundle.value.suggestions.findIndex((s) => s.candidate_id === subject)
    if (si >= 0) {
      popTo(0)
      const mod = idx.suggModule.get(si) ?? null
      if (mod) selectNode(`module:${mod}`)
      else selectNode(`sugg:${si}`)
      return
    }
    // canonical function? drill to its file and select the function node
    if (idx.nodeById.has(subject)) {
      const mod = idx.modOfFn.get(subject)
      const file = idx.fileOf.get(subject)
      if (mod && file) {
        popTo(0)
        push({ kind: 'module', id: mod, label: mod })
        push({ kind: 'file', id: file, label: file.split('/').slice(-1)[0] })
        selectNode(subject)
        return
      }
      popTo(0)
      const mod2 = idx.modOfFn.get(subject)
      if (mod2) selectNode(`module:${mod2}`)
      return
    }
    if (subject.startsWith('resource:')) {
      popTo(0)
      selectNode(subject)
    }
  }

  return {
    lanes, lane, bundle, index, loading, error,
    level, path, overlays, hideCrossCutting, showRouteCDebug,
    scope, viewMode, showMiniMap, layoutNonce,
    dataPorts, moduleInterfaces, flowInterfaces, diCapability,
    showDataInterfaces, showShapes, showSizes, loadDataInterfaces,
    showKernelDebug, kernelSummary, kernelRefs, loadKernel, kernelSupportFor,
    syncStatus, syncFacRoot, loadSyncStatus, refreshSync, startSyncPolling, stopSyncPolling,
    selection, drillEdge, sourceTarget, scene,
    drc, drcLoading, drcError, drcOpen, drcRuleFilter, drcSeverityFilter,
    loadLanes, loadLane, loadDrc, resetView, setOverlay, toggleOverlay,
    enableAllOverlays, setHideCrossCutting, push, popTo, pop, childrenOf,
    selectNode, selectEdge, selectDrillEdge, openSource,
    selectFindingSubject, applyScope, relayout, setMiniMap,
  }
})
