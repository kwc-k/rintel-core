<script setup lang="ts">
// FLOW-INFER0 §14/§18/§22: Flow inspector — selected node/edge details,
// drill-down to members → frozen call facts → Monaco source; guard status.
import { computed, ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import { useTopoStore } from '../../stores/topo'
import { guardStatus } from '../../domain/flow-topology'

const { t } = useI18n()
const store = useFlowTopologyStore()
const topo = useTopoStore()

onMounted(() => {
  if (!store.runtimeRuns.length) void store.loadRuntime().then(() => store.loadRuntimeData())
  else void store.loadRuntimeData()
  void store.loadPerformance()
})

// ---- RUNTIME-TRACE0: observed invocation overlay ---------------------------
const rtSelected = ref<string | null>(null)
const rtStageRows = computed(() => {
  const out: { sid: string; display: string; stats: Record<string, any> }[] = []
  for (const st of store.semanticStages) {
    const stats = store.runtimeStageStats[st.semantic_stage_id]
    if (!stats) continue
    out.push({ sid: st.semantic_stage_id, display: st.display_name ?? st.semantic_stage_id, stats })
  }
  return out
})
const rtSelectedRow = computed(() =>
  rtStageRows.value.find((r) => r.sid === rtSelected.value) ?? null)
const rtInvocations = computed<Record<string, any>[]>(() => {
  const sel = rtSelectedRow.value
  if (!sel) return []
  const out: Record<string, any>[] = []
  for (const n of stageNamesFor(sel.sid)) {
    for (const inv of store.runtimeInvocations[`${store.selectedRun}:${n}`] ?? []) out.push(inv)
  }
  return out
})
// ---- RUNTIME-DATA0: observed data objects ----------------------------------
const rtdEventsFor = (id: string) =>
  store.rtdEvents.filter((e) => e.runtime_data_id === id)
const rtdShape = (o: any) => (o.shape && o.shape.length ? o.shape.join('×') : 'scalar')
const rtdHuman = (id: string) => {
  const o = store.rtdObjects.find((x) => x.runtime_data_id === id)
  if (!o) return id
  const region = o.canonical_location_id.split(':').slice(-2)[0]
  const sym = (o.canonical_symbol_id ?? '').split('::').pop()
  const symNote = sym && sym !== region ? ` (${sym})` : ''
  return `${region}${symNote} [${rtdShape(o)}] ${o.dtype} ${o.shape_status}`
}
function stageNamesFor(sid: string): string[] {
  const st = (store.semantic?.stages ?? []).find((x) => x.semantic_stage_id === sid)
  const names: string[] = []
  for (const cid of st?.members?.canonical_symbol_ids ?? []) {
    names.push(cid.split(':')[1] ?? '')
  }
  for (const nid of st?.members?.node_ids ?? []) {
    if (nid.startsWith('cmd:crm:')) names.push('P' + nid.split(':')[2])
  }
  return names
}
async function openRtStage(sid: string): Promise<void> {
  rtSelected.value = rtSelected.value === sid ? null : sid
  if (rtSelected.value) await store.loadStageInvocations(stageNamesFor(rtSelected.value))
}
function fmtMs(v: number | undefined): string {
  if (v === undefined || v === null) return '—'
  if (v >= 1000) return (v / 1000).toFixed(2) + ' s'
  return v.toFixed(3) + ' ms'
}

const selNode = computed(() => {
  const s = store.selection
  if (!s || s.type !== 'node') return null
  return store.activeFlow?.nodes.find((n) => n.node_id === s.id) ?? null
})

const selEdge = computed(() => {
  const s = store.selection
  if (!s || s.type !== 'edge') return null
  return store.activeFlow?.edges.find((e) => e.edge_id === s.id) ?? null
})

// FLOW-SEMANTIC0 §26: SemanticStage inspector (annotation plane; edits POST to
// the semantic overlay; canonical evidence untouched — FS8)
const selStage = computed(() => {
  const s = store.selection
  if (!s || s.type !== 'node' || !s.id.startsWith('stage:')) return null
  return (store.semantic?.stages ?? []).find((x) => x.semantic_stage_id === s.id) ?? null
})

const stageMembers = computed(() => {
  const st = selStage.value
  if (!st) return []
  return (store.semantic?.memberships ?? []).filter((x) => x.semantic_stage_id === st.semantic_stage_id)
})

const regionOf = computed(() => {
  const map = new Map<string, string>()
  for (const r of store.activeFlow?.regions ?? []) {
    for (const m of r.methods) map.set(`cmd:${store.app}:${m}`, r.region_id)
  }
  return map
})

// DATA-INTERFACE0 enrichment: parsed real function interfaces per region
const regionIntf = computed(() => {
  const n = selNode.value
  if (!n || n.kind !== 'region') return null
  return topo.flowInterfaces[n.node_id] ?? null
})

const regionCalleePorts = computed(() => {
  const ri = regionIntf.value
  if (!ri?.callees?.length) return []
  const out: { callee: string; ports: any[] }[] = []
  for (const c of ri.callees) {
    const ports = topo.dataPorts[c] ?? []
    if (ports.length) out.push({ callee: c, ports })
  }
  return out
})

const edgeStatus = computed(() => {
  const e = selEdge.value
  if (!e) return null
  return e.guard_id ? guardStatus(e.guard_id, store.activeScenario) : 'ACTIVE'
})

const stageNameDraft = ref('')
const stageDescDraft = ref('')
watch(() => store.selection, () => {
  if (selStage.value) {
    stageNameDraft.value = selStage.value.display_name ?? ''
    stageDescDraft.value = selStage.value.description ?? ''
  }
}, { deep: true })

async function saveStageName(): Promise<void> {
  if (!selStage.value || !stageNameDraft.value.trim()) return
  await store.updateStage(selStage.value.semantic_stage_id, { display_name: stageNameDraft.value.trim() })
}
async function saveStageDesc(): Promise<void> {
  if (!selStage.value) return
  await store.updateStage(selStage.value.semantic_stage_id, { description: stageDescDraft.value.trim() })
}
async function setStageStatus(status: string): Promise<void> {
  if (!selStage.value) return
  await store.updateStage(selStage.value.semantic_stage_id, { status })
}
// ---- PERF-TOPO0: performance overlay --------------------------------------
const pfFuncs = computed(() => {
  const fs = [...store.perfFunctions]
  if (store.perfMode === 'frequency') fs.sort((a, b) => b.count - a.count)
  else if (store.perfMode === 'time') fs.sort((a, b) => b.inclusive_ms - a.inclusive_ms)
  else return fs
  return fs.slice(0, 15)
})
const pfStages = computed(() =>
  [...store.perfStages].sort((a, b) => b.top_inclusive_ms - a.top_inclusive_ms))
const pfChain = computed(() =>
  (store.perfChain?.critical_chain_exact_invocations ?? []).slice(0, 20))
const pfMaxFrac = computed(() =>
  Math.max(...store.perfFunctions.map((f) => f.fraction_of_root ?? 0), 0.01))
const pfSelSpan = computed(() => {
  const s = (store.selectedFindingObj?.source_spans ?? [])[0]
  return s && s.file && s.start_line ? s : null
})
function openFindingSource(f: Record<string, any> | null): void {
  if (!f) return
  const spans = (f.source_spans ?? []) as Array<Record<string, any>>
  // §7/§18: prefer the CALLSITE anchor (the hot call expression) over a
  // definition anchor when a finding has several.
  const s = spans.find((x) => x.span_kind === 'CALLSITE' && x.start_column)
    ?? spans.find((x) => x.start_column) ?? spans[0]
  if (!s || !s.file || !s.start_line) return
  topo.openSource('fac', s.file, s.start_line, s.start_column ? s : null)
}
function openDataHotspotSource(d: Record<string, any>): void {
  const s = ((d.source_spans ?? []) as Array<Record<string, any>>)
    .find((x) => x.start_column) ?? (d.source_spans ?? [])[0]
  if (!s || !s.file || !s.start_line) return
  topo.openSource('fac', s.file, s.start_line, s.start_column ? s : null)
}
function spanText(sp: Record<string, any> | null | undefined): string {
  if (!sp || !sp.file) return '—'
  if (sp.text) return sp.text
  const head = `${sp.file}:${sp.start_line}`
  return sp.start_column ? `${head}:${sp.start_column}-${sp.end_column}` : head
}
// ---- PERF-CAUSE0: cause analysis -----------------------------------------
const pfCauses = computed(() => store.selectedFindingCauses ?? [])
function causeSpan(c: Record<string, any>): Record<string, any> | null {
  const spans = (c.exact_source_spans ?? []) as Array<Record<string, any>>
  // the hottest anchor of a loop-invariant cause is the distribution call
  // itself; fall back to any callsite, then to any located span.
  return spans.find((x) => x.start_column && x.span_kind === 'CALLSITE'
      && (x.evidence?.name === 'dist' || x.evidence?.name === 'pow'))
    ?? spans.find((x) => x.span_kind === 'CALLSITE' && x.start_column)
    ?? spans.find((x) => x.start_column) ?? spans[0] ?? null
}
function openCauseSource(c: Record<string, any>): void {
  const s = causeSpan(c)
  if (!s || !s.file || !s.start_line) return
  topo.openSource('fac', s.file, s.start_line, s.start_column ? s : null)
}
function causeVerdictClass(v: string | undefined): string {
  return v === 'SUPPORTED' ? 'ok' : v === 'DISPROVED' ? 'bad'
    : v === 'PARTIAL' ? 'warn' : 'muted'
}
function pfBar(frac: number | undefined): Record<string, string> {
  return { width: `${Math.min(100, Math.max(0, ((frac ?? 0) / pfMaxFrac.value) * 100))}%` }
}

function openStageSource(kind: string, id: string): void {
  if (kind === 'canonical_symbol') {
    const fn = id.split(':').pop() ?? id
    topo.openSource('fac', pathFor(fn) ?? 'lapack/dsbev.f', null)
  } else {
    // CRM command handlers live in sfac/scrm.c (not sfac/sfac.c)
    const file = id.startsWith('cmd:crm') || id.startsWith('regioncal:crm')
      ? 'sfac/scrm.c' : 'sfac/sfac.c'
    topo.openSource('fac', file, null)
  }
}
function pathFor(fn: string): string | null {
  const map: Record<string, string> = {
    DSBEV: 'lapack/dsbev.f', DSTEQR: 'lapack/dsteqr.f', DGER: 'blas/dger.f',
    DGEMM: 'blas/dgemm.f', XERBLA: 'blas/xerbla.f',
    AddConfigToList: 'faclib/config.c', ConfigListToC: 'sfac/sfac.c',
    IntFromList: 'sfac/sfac.c', DoubleFromList: 'sfac/sfac.c', PAddConfig: 'sfac/sfac.c',
    // CRM-SEMANTIC1: faclib computation kernels (population/rate chain)
    LevelPopulation: 'faclib/crm.c', BlockPopulation: 'faclib/crm.c',
    BlockMatrix: 'faclib/crm.c', BlockRelaxation: 'faclib/crm.c', FixNorm: 'faclib/crm.c',
    Cascade: 'faclib/crm.c', SpecTable: 'faclib/crm.c', SetBlocks: 'faclib/crm.c',
    InitBlocks: 'faclib/crm.c', SingleLevelBlock: 'faclib/crm.c', NewLevelBlock: 'faclib/crm.c',
    CopyNComplex: 'faclib/crm.c', DRBranch: 'faclib/crm.c', DRStrength: 'faclib/crm.c',
    DRSuppression: 'faclib/crm.c', DRSupFactor: 'faclib/crm.c', TabNLTE: 'faclib/crm.c',
    vanregemoter: 'faclib/crm.c', MExpIntOne: 'faclib/crm.c', RydBranch: 'faclib/crm.c',
    RateCoefficients: 'faclib/crm.c', DumpRates: 'faclib/crm.c', ModifyRates: 'faclib/crm.c',
    SetCERates: 'faclib/crm.c', SetCIRates: 'faclib/crm.c', SetRRRates: 'faclib/crm.c',
    SetTRRates: 'faclib/crm.c', SetAIRates: 'faclib/crm.c', SetAIRatesInner: 'faclib/crm.c',
    SetCXRates: 'faclib/crm.c', SetRateMultiplier: 'faclib/crm.c', SetEleDensity: 'faclib/crm.c',
    SetPhoDensity: 'faclib/crm.c', SetAbund: 'faclib/crm.c', SetNumSingleBlocks: 'faclib/crm.c',
    SetIteration: 'faclib/crm.c', NormalizeMode: 'faclib/crm.c', ReinitCRM: 'faclib/crm.c',
    SortBranches: 'faclib/crm.c', SetCascade: 'faclib/crm.c', SetStarkZMP: 'faclib/crm.c',
    SetExtrapolate: 'faclib/crm.c', SetEMinAI: 'faclib/crm.c', SetInnerAuger: 'faclib/crm.c',
    SetCxtDensity: 'faclib/crm.c', InitCRM0: 'faclib/crm.c',
    SetEleDist: 'faclib/rates.c', SetPhoDist: 'faclib/rates.c', SetCxtDist: 'faclib/rates.c',
    SetRateAccuracy: 'faclib/rates.c', SetGamma3B: 'faclib/rates.c',
    SetCXLDist: 'faclib/mpiutil.c', FindLevelBlock: 'faclib/dbase.c', SetUTA: 'faclib/dbase.c',
    SetOption: 'faclib/init.c',
  }
  return map[fn] ?? null
}
function openSourceLine(w: { fact_id?: string; expr?: string; line?: number | null
                             file?: string | null
                             source_span?: Record<string, any> | null } | undefined): void {
  if (!w) return
  const repoId = topo.bundle?.meta.source_repo_id ?? 'fac'
  // §19/§21: use the witness's OWN file and canonical span (never a hardcoded
  // lane file) — LINE_ONLY witnesses still jump to the right line.
  const sp = w.source_span ?? null
  const file = (sp?.file as string | undefined) ?? w.file ?? 'sfac/sfac.c'
  const line = (sp?.start_line as number | undefined) ?? w.line ?? null
  topo.openSource(repoId, file, line, sp && sp.start_column ? sp : null)
}
</script>

<template>
  <aside class="flow-inspect" data-testid="flow-inspector">
    <div v-if="store.runtimeRuns.length" class="fi2-section" data-testid="rt-panel">
      <div class="fi2-title">§ Runtime Trace（真实运行观测 — OBSERVED）</div>
      <div class="fi2-row">
        <label class="fi2-note">run
          <select :value="store.selectedRun" data-testid="rt-run-select"
                  @change="(e) => store.setSelectedRun((e.target as HTMLSelectElement).value)">
            <option v-for="r in store.runtimeRuns" :key="r.run_id" :value="r.run_id">
              {{ r.label }} [{{ r.run_id }}]
            </option>
          </select>
        </label>
        <span class="fi2-note muted">integrity:
          {{ store.runtimeRuns.find((r) => r.run_id === store.selectedRun)?.integrity?.status ?? '—' }}
        </span>
      </div>
      <div class="fi2-note muted">阶段观测次数/时长（仅真实发生；静态 MAY ≠ 本次发生）</div>
      <div v-for="row in rtStageRows" :key="row.sid" class="rt-stage"
           :data-testid="`rt-stage-row-${row.sid}`" @click="openRtStage(row.sid)">
        <span class="chip sem">{{ row.display }}</span>
        <span data-testid="rt-stage-inv">{{ row.stats.invocations }}</span> calls ·
        <span class="mono">{{ fmtMs(row.stats.top_inclusive_ms) }}</span>
        <span class="muted">top-incl / {{ fmtMs(row.stats.exclusive_ms) }} excl</span>
      </div>
      <div v-if="rtSelectedRow" class="fi2-invlist" data-testid="rt-stage-detail"
           :data-sid="rtSelectedRow.sid">
        <div class="fi2-note mono">共 {{ rtInvocations.length }} 条观测展示</div>
        <div v-for="inv in rtInvocations.slice(0, 40)" :key="inv.invocation_id"
             class="fi2-note mono" :data-testid="`rt-inv-row-${inv.invocation_id}`">
          {{ String(inv.start_ts).padStart(9) }}ms ·
          {{ inv.symbol_name }} ·
          {{ inv.duration_ms.toFixed(3) }}ms ·
          {{ inv.binding ?? 'UNKNOWN' }} {{ (inv.file ?? '').split('/').pop() }}:{{ inv.line }}
          · {{ inv.truth_class }}
          <span class="muted">⇐ {{ inv.call_site_file ? (inv.call_site_file.split('/').pop() + ':' + inv.call_site_line) : 'entry' }}</span>
        </div>
      </div>
    </div>
    <div v-if="store.perfFunctions.length" class="fi2-section" data-testid="pf-panel">
      <div class="fi2-title">Performance Overlay（PERF-TOPO0 — OBSERVED/DERIVED，非硬件归因）</div>
      <div class="fi2-row fi2-modes">
        <button type="button" class="chip sem mode" :class="{ on: store.perfMode === 'time' }"
                data-testid="pf-mode-time" @click="store.perfMode = 'time'">Time</button>
        <button type="button" class="chip sem mode" :class="{ on: store.perfMode === 'frequency' }"
                data-testid="pf-mode-frequency" @click="store.perfMode = 'frequency'">Call Frequency</button>
        <button type="button" class="chip sem mode" :class="{ on: store.perfMode === 'critical' }"
                data-testid="pf-mode-critical" @click="store.perfMode = 'critical'">Critical Path</button>
        <button type="button" class="chip sem mode" :class="{ on: store.perfMode === 'data' }"
                data-testid="pf-mode-data" @click="store.perfMode = 'data'">Data Access</button>
      </div>
      <div class="fi2-note muted">run: {{ store.selectedRun }} ·
        revision {{ (store.perfChain ?? {}).revision || store.perfFunctions[0]?.revision || 'r103' }}</div>

      <template v-if="store.perfMode === 'time'">
        <div class="fi2-note muted">Top functions by observed inclusive time（榜单分离 §26；exclusive 仅在可归因时）</div>
        <div class="fi2-note mono" v-for="f in pfFuncs.slice(0, 12)" :key="f.symbol_name"
             :data-testid="'pf-fn-' + f.symbol_name">
          <span class="muted">{{ f.symbol_name }}</span> ·
          {{ fmtMs(f.inclusive_ms) }} (<b>{{ ((f.fraction_of_root ?? 0) * 100).toFixed(1) }}%</b>) ·
          <b>{{ f.count.toLocaleString() }}</b> calls
          <span class="muted">· excl {{ fmtMs(f.exclusive_ms) }}{{ f.exclusive_reliable ? '' : ' [unreliable]' }}</span>
        </div>
        <div class="fi2-sub">Stages by time</div>
        <div class="fi2-note mono" v-for="st in pfStages" :key="st.stage_id"
             :data-testid="'pf-stage-' + st.stage_id">
          <span class="chip sem">{{ st.display_name }}</span>
          {{ fmtMs(st.top_inclusive_ms) }} ·
          <b>{{ ((st.fraction_of_root ?? 0) * 100).toFixed(1) }}%</b> of observed root
          <span class="muted">({{ st.invocations.toLocaleString() }} invocations)</span>
        </div>
      </template>

      <template v-else-if="store.perfMode === 'frequency'">
        <div class="fi2-note muted">Top functions by observed call count（次数 ≠ 单次昂贵：区分两类）</div>
        <div class="fi2-note mono" v-for="f in pfFuncs.slice(0, 12)" :key="f.symbol_name"
             :data-testid="'pf-cnt-' + f.symbol_name">
          {{ f.symbol_name }} · <b>{{ f.count.toLocaleString() }}</b> calls ·
          {{ fmtMs(f.inclusive_ms) }} · mean {{ (f.mean_dur_ms != null ? (f.mean_dur_ms * 1e6).toFixed(1) : '—') }} ns
          <span class="muted">· median {{ fmtMs(f.median_dur_ms) }}</span>
        </div>
      </template>

      <template v-else-if="store.perfMode === 'critical'">
        <div class="fi2-note muted">Critical path（coverage={{ store.perfChain?.coverage }}：单线程 trace，
          时长可直接相加；判定=每层最大 inclusive 子调用）</div>
        <div class="fi2-note mono" v-for="(c, i) in pfChain" :key="i"
             :data-testid="'pf-chain-' + (i)">
          {{ i > 0 ? '↓ ' : '' }}{{ c.symbol_name }}
          <span class="muted">· {{ c.start_ms }}ms · dur {{ fmtMs(c.duration_ms) }}
            · {{ c.stage ? c.stage : '—' }}</span>
        </div>
        <div class="fi2-note muted" v-if="(store.perfChain?.unclosed_frames ?? []).length">
          注意：{{ (store.perfChain?.unclosed_frames ?? []).length }} 个 frame 在 trace 结束（exit() 先于 unwind）时未闭合 —
          其 exclusive 归因不可靠（见报告）
        </div>
        <div class="fi2-sub">Maxwell audit（runtime count + loop source = EXACT）</div>
        <div class="fi2-note mono" data-testid="pf-maxwell-audit">
          {{ store.perfMaxwellAudit ? '3,998,000 = 2 × sum(i=1..N3BRI-1,i) (N3BRI=2000) exact=' + store.perfMaxwellAudit.exact_match : '—' }}
          <span v-if="store.perfMaxwellAudit" class="muted"> · mean={{ store.perfMaxwellAudit.maxwell_mean_ns }}ns
            · callsite <span class="mono">{{ spanText(store.perfMaxwellAudit.callsite) }}</span></span>
        </div>
      </template>

      <template v-else>
        <div class="fi2-note muted">Data-aware hotspots（logical_access_weight = 访问次数 × logical_size；
          ≠ bytes_transferred / bandwidth §7）</div>
        <div class="fi2-note mono" v-for="d in store.perfDataHotspots.slice(0, 10)" :key="d.runtime_data_id"
             :data-testid="'pf-data-' + d.region">
          <span class="chip sem">{{ d.region }}</span>
          [{{ (d.shape?.length ? d.shape : ['?']).join('×') }}] {{ d.dtype }} ·
          logical {{ d.logical_size }}B · {{ d.access_events }} events
          (R{{ d.read_events }}/W{{ d.write_events }}/RW{{ d.readwrite_events }})
          · weight <b>{{ d.logical_access_weight }}</b>B
          <span class="muted">· {{ d.scope }}</span>
          <button v-if="(d.source_spans ?? []).length" type="button" class="link"
                  :data-testid="'pf-data-open-' + d.region"
                  @click.stop="openDataHotspotSource(d)">
            <span class="mono">{{ spanText((d.source_spans ?? [])[0]) }}</span>
          </button>
        </div>
      </template>

      <div class="fi2-sub" style="margin-top: 6px">Findings（点击查看；仅调查建议，不改代码 §27）</div>
      <div class="fi2-note mono" v-for="f in store.perfFindings" :key="f.finding_id"
           :class="{ sel: store.selectedFinding === f.finding_id }"
           :data-testid="'pf-find-' + f.finding_id" @click="store.selectedFinding = f.finding_id">
        <span class="chip sem">{{ f.kind }}</span>
        {{ f.metric }} = {{ f.value.toLocaleString() }} {{ f.unit }}
        <span class="muted">· {{ (f.affected_objects ?? [])[0] }}</span>
      </div>
      <div v-if="store.selectedFindingObj" class="fi2-block" data-testid="pf-finding-detail">
        <div class="fi2-sub">Finding {{ store.selectedFindingObj.finding_id }}
          ({{ store.selectedFindingObj.truth_class }})</div>
        <div class="fi2-note">run={{ store.selectedFindingObj.run_id }} ·
          kind=<b>{{ store.selectedFindingObj.kind }}</b></div>
        <div class="fi2-note">affected: {{ (store.selectedFindingObj.affected_objects ?? []).join(' · ') }}</div>
        <div class="fi2-note">metric: <b>{{ store.selectedFindingObj.metric }} =
          {{ store.selectedFindingObj.value.toLocaleString() }} {{ store.selectedFindingObj.unit }}</b></div>
        <div class="fi2-note">context: {{ store.selectedFindingObj.baseline_context }}</div>
        <div class="fi2-note">data/evidence:
          {{ (store.selectedFindingObj.runtime_witness ?? []).map((w: any) => w.symbol || w.stage_id || w.fact || w.data_id).join(' · ') || '—' }}</div>
        <div class="fi2-note" data-testid="pf-finding-spans">source:
          <span class="mono">{{ (store.selectedFindingObj.source_spans ?? []).map((sp: any) => spanText(sp)).join(', ') || '—' }}</span>
        </div>
        <div class="fi2-note muted">anchor kind/precision:
          {{ (store.selectedFindingObj.source_spans ?? []).map((sp: any) => (sp.span_kind ?? '?') + '/' + (sp.precision ?? '?')).join(', ') || '—' }}</div>
        <div class="fi2-note muted">limitations: {{ (store.selectedFindingObj.limitations ?? []).join(' | ') || '—' }}</div>
        <button v-if="pfSelSpan" type="button" class="link" data-testid="pf-finding-open-source"
                @click="openFindingSource(store.selectedFindingObj)">
          打开源码 (跳到 {{ pfSelSpan.start_column ? 'line:column 范围' : 'line' }})
        </button>
        <div v-else class="fi2-note muted">该 finding 无单一源码锚点（聚合/未绑定符号），
          不提供跳转 — 诚实优于跳错（spec §30）</div>
      </div>
      <div v-if="store.selectedFindingObj && pfCauses.length" class="fi2-block"
           data-testid="pf-cause-analysis">
        <div class="fi2-sub">Cause Analysis（PERF-CAUSE0 — 只分析，不产生 patch）</div>
        <div v-if="store.perfCauseMeta?.purity_verdict" class="fi2-note muted"
             data-testid="pf-cause-purity">
          Maxwell purity: {{ store.perfCauseMeta.purity_verdict }} ·
          verdicts: {{ JSON.stringify(store.perfCauseMeta.verdict_counts ?? {}) }}
        </div>
        <div v-for="c in pfCauses" :key="c.cause_id" class="fi2-cause"
             :data-testid="'pf-cause-' + c.cause_id">
          <div class="fi2-row">
            <span class="chip sem">{{ c.cause_id }}</span>
            <span class="chip" :class="causeVerdictClass(c.verdict)"
                  :data-testid="'pf-cause-verdict-' + c.cause_id">{{ c.verdict }}</span>
            <span class="mono">{{ c.kind }}</span>
          </div>
          <div class="fi2-note"><b>Hypothesis</b> {{ c.hypothesis }}</div>
          <div class="fi2-note" :data-testid="'pf-cause-evidence-' + c.cause_id">
            <b>Evidence</b>
            <span class="mono">{{ JSON.stringify(c.evidence) }}</span>
          </div>
          <div class="fi2-note" v-if="(c.counterevidence ?? []).length">
            <b>Counterevidence</b>
            <span class="mono">{{ (c.counterevidence ?? []).join(' | ') }}</span>
          </div>
          <div class="fi2-note muted" v-if="c.mathematical_derivation">
            <b>Math</b> {{ c.mathematical_derivation }}
          </div>
          <div class="fi2-note" :data-testid="'pf-cause-spans-' + c.cause_id">source:
            <span class="mono">{{ (c.exact_source_spans ?? []).map((sp: any) => spanText(sp)).join(', ') || '—' }}</span>
          </div>
          <button v-if="causeSpan(c)" type="button" class="link"
                  :data-testid="'pf-cause-open-' + c.cause_id"
                  @click="openCauseSource(c)">
            打开源码 ({{ spanText(causeSpan(c)) }})
          </button>
        </div>
      </div>
    </div>

    <div v-if="store.rtdObjects.length" class="fi2-section" data-testid="rtd-panel">
      <div class="fi2-title">§ Runtime Data（观测 DATA 对象 + DGESV 绑定 — OBSERVED）</div>
      <div class="fi2-note muted">共享 static 缓冲区 bmatrix 的分区（STATE / LOCAL 游标）；
        shape 为运行时 RESOLVED，logical_size ≠ 搬运量</div>
      <div class="rtd-objrow" v-for="o in store.rtdObjects" :key="o.runtime_data_id"
           :data-testid="`rtd-obj-${o.region ?? o.runtime_data_id}`">
        <span class="chip sem">{{ o.region ?? o.runtime_data_id }}</span>
        <span class="mono" data-testid="rtd-shape">{{ rtdShape(o) }}</span>
        <span class="muted">{{ o.dtype }} · {{ o.shape_status }} · {{ o.scope }} ·
          {{ o.coverage }} · logical {{ o.logical_size }} B</span>
      </div>
      <div class="fi2-note muted">数据访问事件（probe 序列）：</div>
      <div class="fi2-note mono" v-for="e in store.rtdEvents.slice(0, 30)" :key="e.event_id"
           :data-testid="`rtd-ev-${e.event_id}`">
        {{ e.operation_kind }} {{ rtdHuman(e.runtime_data_id) }}
        <span class="muted">@{{ (e.source_line ?? '').split(':').pop() }} ·
          inv {{ e.invocation_id }}</span>
      </div>
      <div v-if="store.rtdDgesv" class="fi2-note mono" data-testid="rtd-dgesv">
        DGESV: N={{ store.rtdDgesv.observed_m?.join(',') }} NRHS=1
        LDA=LDB={{ store.rtdDgesv.observed_dim }}
        A={{ rtdShape(store.rtdObjects.find((x) => x.region === 'a') ?? { shape: [] }) }}
        B={{ rtdShape(store.rtdObjects.find((x) => x.region === 'b') ?? { shape: [] }) }}
        INFO={{ store.rtdDgesv.info_values?.join(',') }}
      </div>
    </div>
    <div v-if="selStage" class="fi2-section" data-testid="fs-stage">
      <div class="fi2-title">§ Semantic Stage（标注投影 — 非 Evidence）</div>
      <div class="fi2-row"><b data-testid="fs-stage-name">{{ selStage.display_name }}</b>
        <span class="chip sem" data-testid="fs-stage-kind">{{ selStage.stage_kind }}</span></div>
      <input v-model="stageNameDraft" class="sem-input" data-testid="fs-stage-name-input"
             @change="saveStageName" @keydown.enter="saveStageName" />
      <textarea v-model="stageDescDraft" class="sem-input sem-area" data-testid="fs-stage-desc-input"
                @change="saveStageDesc"></textarea>
      <div class="fi2-row">
        origin: <b>{{ selStage.origin }}</b> · truth: <b data-testid="fs-stage-truth">{{ selStage.truth_class }}</b>
        · status: <b data-testid="fs-stage-status">{{ selStage.status }}</b> · coverage: {{ selStage.coverage }}
      </div>
      <div class="fi2-row">
        <button type="button" class="link" data-testid="fs-stage-accept" :disabled="selStage.status === 'ACCEPTED'"
                @click="setStageStatus('ACCEPTED')">Accept</button>
        <button type="button" class="link" data-testid="fs-stage-reject" :disabled="selStage.status === 'REJECTED'"
                @click="setStageStatus('REJECTED')">Reject</button>
        <button type="button" class="link" data-testid="fs-stage-reset" @click="setStageStatus('SUGGESTED')">Reset</button>
      </div>
      <div class="fi2-block">
        <div class="fi2-sub">Members（逐层下钻 §9）</div>
        <div class="fi2-note mono" v-for="m in stageMembers" :key="m.id">
          {{ m.member_kind }} ▸ {{ m.id }}
          <button type="button" class="link" data-testid="fs-member-open"
                  @click="openStageSource(m.member_kind, m.id)">源码</button>
        </div>
      </div>
      <div class="fi2-block">
        <div class="fi2-sub">Inputs（DATA-INTERFACE0）</div>
        <div v-for="(l, i) in selStage.inputs ?? []" :key="'i' + i" class="fi2-note mono">▸ {{ l }}</div>
        <div v-if="!(selStage.inputs ?? []).length" class="fi2-note muted">无端口证据（本阶段不从 DATA-INTERFACE0 汇总）</div>
      </div>
      <div class="fi2-block">
        <div class="fi2-sub">Outputs（DATA-INTERFACE0）</div>
        <div v-for="(l, i) in selStage.outputs ?? []" :key="'o' + i" class="fi2-note mono">▸ {{ l }}</div>
        <div v-if="!(selStage.outputs ?? []).length" class="fi2-note muted">无端口证据</div>
      </div>
      <div class="fi2-block">
        <div class="fi2-sub">Evidence refs</div>
        <div v-for="(e, i) in selStage.evidence_refs ?? []" :key="'e' + i" class="fi2-note mono">• {{ e }}</div>
        <div v-if="!(selStage.evidence_refs ?? []).length" class="fi2-note muted">无证据引用 — 该阶段为名称/提示级</div>
      </div>
      <div class="fi2-block" data-testid="fs-stage-unknowns">
        <div class="fi2-sub">Unknowns</div>
        <div v-for="(u, i) in selStage.unknowns ?? []" :key="'u' + i" class="fi2-note mono">? {{ u }}</div>
        <div v-if="!(selStage.unknowns ?? []).length" class="fi2-note muted">无已知未知（不代表完备）</div>
      </div>
    </div>

    <div v-else-if="selNode" class="fi2-section" data-testid="fi2-node">
      <div class="fi2-title">{{ selNode.label }}</div>
      <div class="fi2-row">{{ t('wb.flow.kind') }}: <b>{{ selNode.kind }}</b></div>
      <div class="fi2-row">{{ t('wb.flow.truth') }}: {{ selNode.truth_class }}（DERIVED — {{ t('wb.flow.notEvidence') }}）</div>
      <div v-if="selNode.count != null" class="fi2-row">{{ t('wb.flow.members') }}: {{ selNode.count }}</div>
      <div v-if="selNode.members?.length" class="fi2-row mono muted">{{ selNode.members.slice(0, 8).join(' · ') }}</div>
      <div v-if="selNode.capability" class="fi2-row warn">capability: {{ selNode.capability }}</div>
      <div v-if="selNode.why?.length" class="fi2-block">
        <div class="fi2-sub">{{ t('wb.flow.why') }}</div>
        <div v-for="w in selNode.why" :key="w" class="fi2-note">• {{ w }}</div>
      </div>
      <div v-if="selNode.witness_count" class="fi2-sub">{{ t('wb.flow.witnesses') }}: {{ selNode.witness_count }}</div>
      <div v-if="selNode.kind === 'region'" class="fi2-block" data-testid="di-region">
        <div class="fi2-sub">Data Interface (Region) — DERIVED</div>
        <div v-for="g in regionCalleePorts" :key="g.callee" class="fi2-block">
          <div class="fi2-note mono">{{ g.callee }}()</div>
          <div v-for="p in g.ports" :key="p.port_id" class="fi2-note mono">
            {{ p.name }} · {{ p.direction }} · {{ p.dtype }}{{ p.rank != null ? `[${p.rank}]` : '' }}
            · [{{ (p.shape && p.shape.length ? p.shape : ['?']).join('×') }}] ({{ p.shape_status }})
          </div>
        </div>
        <div v-if="!regionCalleePorts.length" class="fi2-note muted">
          该 region 的 callee 未命中 DATA-INTERFACE 解析集（无伪造端口）
        </div>
      </div>
    </div>

    <div v-else-if="selEdge" class="fi2-section" data-testid="fi2-edge">
      <div class="fi2-title">{{ selEdge.source }} → {{ selEdge.target }}</div>
      <div class="fi2-row">
        {{ t('wb.flow.status') }}:
        <b :class="`st-${String(edgeStatus).toLowerCase()}`" data-testid="fi2-guard-status">{{ edgeStatus }}</b>
      </div>
      <div class="fi2-row">{{ t('wb.flow.truth') }}: {{ selEdge.truth_class }} · {{ t('wb.flow.resolution') }}: {{ selEdge.target_resolution }}</div>
      <div v-if="selEdge.why?.length" class="fi2-block">
        <div class="fi2-sub">{{ t('wb.flow.why') }}</div>
        <div v-for="w in selEdge.why" :key="w" class="fi2-note">• {{ w }}</div>
      </div>
      <div class="fi2-block" data-testid="fi2-witnesses">
        <div class="fi2-sub">{{ t('wb.flow.witnesses') }}</div>
        <div v-for="(w, i) in selEdge.witnesses ?? []" :key="i" class="fi2-note mono">
          {{ w.fact_id }} @L{{ w.line }}
          <button type="button" class="link" data-testid="fi2-open-source" @click="openSourceLine(w)">
            {{ t('common.openSource', { fallback: '打开源码 (Monaco)' }) }}
          </button>
        </div>
        <div v-if="!selEdge.witnesses?.length" class="fi2-note muted">derived edge — no direct call fact（投影边，无直接 CALL 事实）</div>
      </div>
    </div>

    <div v-else class="fi2-section muted">
      {{ t('wb.flow.hint') }}
    </div>
  </aside>
</template>

<style scoped>
.flow-inspect {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 10px;
  font-size: 11.5px;
  background: var(--panel);
}
.fi2-section {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  background: var(--surface);
}
.fi2-title { font-weight: 700; font-size: 12px; margin-bottom: 4px; }
.fi2-row { margin-top: 2px; color: var(--text-primary); }
.fi2-row.warn { color: var(--amber-text); }
.fi2-row .st-active { color: var(--ok); }
.fi2-row .st-inactive { color: var(--text-muted); }
.fi2-row .st-unknown, .fi2-row .st-may { color: var(--status-warning); }
.fi2-block { margin-top: 6px; }
.fi2-sub { font-weight: 600; font-size: 10.5px; color: var(--text-muted); }
.fi2-note { color: var(--text-secondary); font-size: 10.5px; margin-top: 2px; word-break: break-all; }
.link { border: none; background: none; color: var(--accent); cursor: pointer; font-size: 10.5px; }
.muted { color: var(--text-muted); }
.chip.sem {
  font-size: 9px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
}
.fi2-cause {
  margin-top: 4px; padding: 4px 6px; border-left: 2px solid var(--border-strong);
  background: var(--surface-raised, transparent);
}
.chip.ok { font-size: 9px; padding: 1px 6px; border-radius: 4px; color: var(--status-ok, #2e7d32); }
.chip.bad { font-size: 9px; padding: 1px 6px; border-radius: 4px; color: var(--status-error, #c62828); }
.chip.warn { font-size: 9px; padding: 1px 6px; border-radius: 4px; color: var(--status-warning); }
.chip.muted { font-size: 9px; padding: 1px 6px; border-radius: 4px; color: var(--text-muted); }
.fi2-modes { display: flex; gap: 4px; flex-wrap: wrap; }
.fi2-modes .mode { cursor: pointer; }
.fi2-modes .mode.on { background: var(--accent); color: var(--surface); }
.fi2-note.sel { outline: 1px solid var(--accent); padding: 1px 3px; }
.sem-input {
  width: 100%;
  margin-top: 3px;
  padding: 3px 6px;
  font-size: 11px;
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  background: var(--surface);
  color: var(--text-primary);
}
.sem-area { min-height: 40px; resize: vertical; }
</style>
