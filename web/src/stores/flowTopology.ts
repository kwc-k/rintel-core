// FLOW-INFER0 frontend store: fetches the derived flow artifacts and holds
// the Flow-view state (app, scenario, toggles).  Read-only over the API —
// nothing here mutates canonical evidence.
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiFetch } from '../api/client'
import type { FlowArtifacts, FlowScenario, MasterFlow } from '../domain/flow-topology'

export const useFlowTopologyStore = defineStore('flowTopology', () => {
  const artifacts = ref<FlowArtifacts | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const app = ref<string>('fac')
  const scenarioId = ref<string | null>(null)      // null = All Paths
  const showSharedCore = ref(true)
  const showUnknownBranches = ref(true)
  const showPruned = ref(false)
  const selection = ref<{ type: 'node' | 'edge'; id: string } | null>(null)

  const apps = computed(() => (artifacts.value?.flows ?? []).map((f) => f.app))
  const activeFlow = computed<MasterFlow | null>(() =>
    artifacts.value?.flows.find((f) => f.app === app.value) ?? null)
  const scenarios = computed<FlowScenario[]>(() =>
    (artifacts.value?.scenarios ?? []).filter((s) => s.app === app.value))
  const activeScenario = computed<FlowScenario | null>(() =>
    scenarios.value.find((s) => s.scenario_id === scenarioId.value) ?? null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [master, scen] = await Promise.all([
        apiFetch<{ flows: MasterFlow[] }>('/flow-infer/master_flow'),
        apiFetch<{ scenarios: FlowScenario[] }>('/flow-infer/scenarios'),
      ])
      artifacts.value = { flows: master.flows ?? [], scenarios: scen.scenarios ?? [] }
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      loading.value = false
    }
  }

  function setApp(next: string): void {
    app.value = next
    scenarioId.value = null
    selection.value = null
  }

  function setScenario(id: string | null): void {
    scenarioId.value = id
    selection.value = null
  }

  function select(type: 'node' | 'edge' | null, id?: string): void {
    selection.value = type ? { type, id: id! } : null
  }

  // ---- FLOW-SEMANTIC0: human-semantic projection (annotation plane) --------
  const granularity = ref<'human' | 'region' | 'function'>('region')
  const semantic = ref<SemanticBundle | null>(null)

  interface SemanticBundle {
    flows: Array<{ flow_id: string; app: string; stage_order: string[];
                   scenario_projections: Record<string, { stage_states: Record<string, string> }> }>
    stages: Record<string, any>[]
    edges: any[]
    memberships: any[]
  }

  const activeSemanticFlow = computed(() =>
    semantic.value?.flows.find((f) => f.app === app.value) ?? null)
  const semanticStages = computed(() => {
    const f = activeSemanticFlow.value
    if (!f || !semantic.value) return []
    return f.stage_order.map((sid) => semantic.value!.stages.find((s) => s.semantic_stage_id === sid))
      .filter(Boolean) as Record<string, any>[]
  })

  async function loadSemantic(): Promise<void> {
    try {
      semantic.value = await apiFetch<SemanticBundle>(`/semantic-flow/bundle?app=${app.value}`)
    } catch {
      semantic.value = null     // annotation is optional; flow view stays usable
    }
  }

  async function updateStage(stageId: string, patch: Record<string, string | number>): Promise<boolean> {
    try {
      await apiFetch(`/semantic-flow/stage/${encodeURIComponent(stageId)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      await loadSemantic()
      return true
    } catch {
      return false
    }
  }

  function setGranularity(g: 'human' | 'region' | 'function'): void {
    granularity.value = g
    selection.value = null
  }

  // ---- RUNTIME-TRACE0: observed invocation overlay -------------------------
  const runtimeRuns = ref<Record<string, any>[]>([])
  const runtimeLoading = ref(false)
  const selectedRun = ref<string>('W1')
  const runtimeStageStats = ref<Record<string, any>>({})

  async function loadRuntime(): Promise<void> {
    runtimeLoading.value = true
    try {
      const r = await apiFetch<{ runs: Record<string, any>[] }>('/runtime/runs')
      runtimeRuns.value = r.runs ?? []
      if (runtimeRuns.value.length && !runtimeRuns.value.some((x) => x.run_id === selectedRun.value)) {
        selectedRun.value = runtimeRuns.value[0].run_id
      }
      await loadRuntimeStages()
    } catch {
      runtimeRuns.value = []
    } finally {
      runtimeLoading.value = false
    }
  }

  async function loadRuntimeStages(): Promise<void> {
    const rid = selectedRun.value
    try {
      const s = await apiFetch<{ stage_stats: Record<string, any> }>(
        `/runtime/stages?run_id=${encodeURIComponent(rid)}`)
      runtimeStageStats.value = s.stage_stats ?? {}
    } catch {
      runtimeStageStats.value = {}
    }
  }

  async function setSelectedRun(rid: string): Promise<void> {
    selectedRun.value = rid
    await loadRuntimeStages()
    await loadRuntimeData()
  }

  // ---- RUNTIME-DATA0: observed data objects --------------------------------
  const rtdObjects = ref<Record<string, any>[]>([])
  const rtdEvents = ref<Record<string, any>[]>([])
  const rtdDgesv = ref<Record<string, any> | null>(null)
  const rtdLoading = ref(false)

  async function loadRuntimeData(): Promise<void> {
    rtdLoading.value = true
    try {
      const rid = selectedRun.value
      const r = await apiFetch<{ objects: any[]; events?: any[]; observed_dim?: number;
        observed_m?: number[]; info_values?: number[]; bindings?: any[] }>(
        `/runtime/runs/${encodeURIComponent(rid)}/data`)
      rtdObjects.value = r.objects ?? []
      const e = await apiFetch<{ events: any[]; kinds: Record<string, number> }>(
        `/runtime/runs/${encodeURIComponent(rid)}/data_access?limit=400`)
      rtdEvents.value = e.events ?? []
      const d = await apiFetch<Record<string, any>>(
        `/runtime/runs/${encodeURIComponent(rid)}/dgesv`)
      rtdDgesv.value = d
    } catch {
      rtdObjects.value = []; rtdEvents.value = []; rtdDgesv.value = null
    } finally {
      rtdLoading.value = false
    }
  }

  const runtimeInvocations = ref<Record<string, any[]>>({})

  async function loadStageInvocations(symbols: string[]): Promise<void> {
    const rid = selectedRun.value
    for (const s of symbols) {
      const key = `${rid}:${s}`
      if (runtimeInvocations.value[key]) continue
      try {
        const r = await apiFetch<{ invocations: any[] }>(
          `/runtime/runs/${encodeURIComponent(rid)}/invocations?symbol=${encodeURIComponent(s)}&limit=200`)
        runtimeInvocations.value = { ...runtimeInvocations.value, [key]: r.invocations ?? [] }
      } catch { /* member without observations */ }
    }
  }

  // ---- PERF-TOPO0: performance overlay -------------------------------------
  const perfMode = ref<'time' | 'frequency' | 'critical' | 'data'>('time')
  const perfFunctions = ref<Record<string, any>[]>([])
  const perfStages = ref<Record<string, any>[]>([])
  const perfFindings = ref<Record<string, any>[]>([])
  const perfChain = ref<Record<string, any> | null>(null)
  const perfDataHotspots = ref<Record<string, any>[]>([])
  const perfHotspots = ref<Record<string, any> | null>(null)
  const perfMaxwellAudit = ref<Record<string, any> | null>(null)
  const perfCauses = ref<Record<string, any>[]>([])
  const perfCauseMeta = ref<Record<string, any> | null>(null)
  const perfLoading = ref(false)
  const selectedFinding = ref<string | null>(null)
  const perfError = ref<string | null>(null)

  async function loadPerformance(): Promise<void> {
    perfLoading.value = true
    perfError.value = null
    try {
      const rid = selectedRun.value
      const [p, h, c, d, f] = await Promise.all([
        apiFetch<{ run_id: string; functions: any[]; stages: any[]; chain: any; integrity: any }>(
          `/runtime/runs/${encodeURIComponent(rid)}/performance`),
        apiFetch<Record<string, any>>(`/runtime/runs/${encodeURIComponent(rid)}/hotspots`),
        apiFetch<Record<string, any>>(`/runtime/runs/${encodeURIComponent(rid)}/critical_path`),
        apiFetch<Record<string, any>>(`/runtime/runs/${encodeURIComponent(rid)}/data_hotspots`),
        apiFetch<{ findings: any[]; maxwell_audit?: any }>(
          `/runtime/runs/${encodeURIComponent(rid)}/findings`),
      ])
      perfFunctions.value = p.functions ?? []
      perfStages.value = p.stages ?? []
      perfChain.value = p.chain ?? null
      perfHotspots.value = h
      perfDataHotspots.value = d.hotspots ?? d.W1 ?? []
      perfFindings.value = f.findings ?? []
      perfMaxwellAudit.value = f.maxwell_audit ?? null
      // PERF-CAUSE0 cause analysis is an additive overlay: a missing artifact
      // must not break the PERF-TOPO0 panels (honest degradation).
      try {
        const cc = await apiFetch<{ findings: any[]; verdict_counts?: any }>(
          `/runtime/runs/${encodeURIComponent(rid)}/performance_causes`)
        perfCauses.value = cc.findings ?? []
        perfCauseMeta.value = {
          verdict_counts: cc.verdict_counts ?? null,
          purity_verdict: (cc as any).purity_verdict ?? null,
          summary_table: (cc as any).summary_table ?? [],
        }
      } catch {
        perfCauses.value = []
        perfCauseMeta.value = null
      }
    } catch (err) {
      perfError.value = err instanceof Error ? err.message : String(err)
      perfFunctions.value = []; perfStages.value = []; perfFindings.value = []
      perfChain.value = null; perfDataHotspots.value = []
      perfCauses.value = []; perfCauseMeta.value = null
    } finally {
      perfLoading.value = false
    }
  }

  const selectedFindingObj = computed(() =>
    perfFindings.value.find((x) => x.finding_id === selectedFinding.value) ?? null)

  // PERF-CAUSE0: causes that explicitly claim this finding as affected.
  const selectedFindingCauses = computed(() => {
    const fid = selectedFinding.value
    if (!fid) return []
    return perfCauses.value.filter(
      (c) => (c.affected_finding_id ?? []).includes(fid))
  })

  return {
    artifacts, loading, error, app, scenarioId,
    showSharedCore, showUnknownBranches, showPruned, selection,
    apps, activeFlow, scenarios, activeScenario,
    granularity, semantic, activeSemanticFlow, semanticStages,
    load, setApp, setScenario, select, loadSemantic, updateStage, setGranularity,
    runtimeRuns, runtimeLoading, selectedRun, runtimeStageStats, runtimeInvocations,
    loadRuntime, loadRuntimeStages, setSelectedRun, loadStageInvocations,
    rtdObjects, rtdEvents, rtdDgesv, rtdLoading, loadRuntimeData,
    perfMode, perfFunctions, perfStages, perfFindings, perfChain,
    perfDataHotspots, perfHotspots, perfMaxwellAudit, perfLoading,
    perfCauses, perfCauseMeta, selectedFindingCauses,
    selectedFinding, selectedFindingObj, perfError, loadPerformance,
  }
})
