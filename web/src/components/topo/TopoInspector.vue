<script setup lang="ts">
// Software Topology inspector (TOPO-UI0).  Node / edge / overview panel.
// Truth fields (truth_class / execution_modality / target_resolution /
// coverage) are always rendered raw here; simplified chips (Resolved /
// Inferred / May / Unknown / Partial) are labels only, the full fields stay
// visible (U1).  Module/File-level edges expose their member (function)
// edges + witnesses directly so every high-level edge is explainable (U5).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTopoStore } from '../../stores/topo'
import { useLangStore } from '../../stores/lang'
import { useWorkbenchStore } from '../../stores/workbench'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import SuggestedDetails from './SuggestedDetails.vue'
import {
  repoPathOf,
  resolveWitnessFile,
  type RelationKind,
  type SceneEdge,
  type SceneNode,
  type Suggestion,
  type TopoEdge,
  type Witness,
} from '../../domain/topo'

const { t } = useI18n()
const lang = useLangStore()
const store = useTopoStore()
const wb = useWorkbenchStore()
const flowTopo = useFlowTopologyStore()

/** CROSS-PROJECTION-SYNC0 "Explore in": jump the selected function to the
 *  related projection view (same evidence revision — XS4/§34). */
function explore(target: 'human' | 'region' | 'module' | 'file' | 'di' | 'source'): void {
  const n = selectedNode.value
  if (!n) return
  switch (target) {
    case 'human':
      wb.setTab('circuit')
      flowTopo.setGranularity('human')
      return
    case 'region':
      wb.setTab('circuit')
      flowTopo.setGranularity('region')
      return
    case 'module':
      store.applyScope('module')
      return
    case 'file':
      store.applyScope('file')
      return
    case 'di':
      store.showDataInterfaces = true
      return
    case 'source':
      wb.setDockTab('source')
      wb.toggleDock()
      return
  }
}

const functionPorts = computed(() => {
  const n = selectedNode.value
  if (!n || n.kind !== 'function' || !n.canonicalId) return []
  return store.dataPorts[n.label] ?? []
})
const modulePorts = computed(() => {
  const n = selectedNode.value
  if (!n || (n.kind !== 'module' && n.kind !== 'file')) return []
  const file = n.label
  return store.moduleInterfaces[file] ?? []
})
const diCapLines = computed(() => {
  const cap = store.diCapability?._per_lane?.fac ?? {}
  return Object.entries(cap).map(([k, v]) =>
    `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
})
// SEMANTIC-SUBSTRATE1 dev-mode: kernel support links for the selected function
const kernSupport = computed(() => {
  const n = selectedNode.value
  if (!n || n.kind !== 'function' || !n.label) return null
  return store.kernelSupportFor(n.label)
})

const nodeSub = computed(() => {
  let s = selectedNode.value?.sub ?? ''
  if (!lang.isZh) return s
  return s
    .replace(/(\d+) functions/g, `$1 ${t('wb.node.fns')}`)
    .replace(/(\d+) files/g, `$1 ${t('wb.node.files')}`)
    .replace(/outside declared modules/g, t('wb.node.outsideDeclared'))
    .replace(/no declared container/g, t('wb.node.noDeclared'))
    .replace(/outside this file/g, t('wb.node.outsideFile'))
    .replace(/no host file/g, t('wb.node.noHostFile'))
})

const bundle = computed(() => store.bundle)
const meta = computed(() => bundle.value?.meta ?? null)
const scene = computed(() => store.scene)

const selectedNode = computed<SceneNode | null>(() => {
  if (store.selection?.type !== 'node') return null
  return scene.value.nodes.find((n) => n.id === store.selection!.id) ?? null
})

const selectedEdge = computed<SceneEdge | null>(() => {
  if (store.selection?.type !== 'edge') return null
  return scene.value.edges.find((e) => e.id === store.selection!.id) ?? null
})

const nodeById = computed(() => scene.value.nodes.reduce((m, n) => m.set(n.id, n), new Map<string, SceneNode>()))

function labelOf(id: string): string {
  if (id === 'unknown-dynamic') return '[Unknown Dynamic Target]'
  const n = nodeById.value.get(id)
  if (n) return n.label
  return id.split(':').pop() ?? id
}

function fileOf(cid: string): string {
  return store.index?.fileOf.get(cid) ?? ''
}

function memberInfo(cid: string): { name: string; file: string; cc: boolean } {
  const n = store.index?.nodeById.get(cid)
  return {
    name: n?.name ?? cid.split(':').pop() ?? cid,
    file: n?.file ?? '',
    cc: store.index?.cc.has(cid) ?? false,
  }
}

// ---- simplified chips (U1: labels only; raw fields always shown below) ----
// Honesty order: Unknown target > Partial coverage > May modality > Inferred
// (the strongest uncertainty first — never 'Resolved' when the dependency
// endpoint or coverage is still uncertain).
function chipOf(e: TopoEdge): { text: string; cls: string } {
  const truth = e.truth_class ?? 'UNKNOWN'
  const modality = e.execution_modality ?? 'UNKNOWN'
  const resolution = e.target_resolution ?? 'UNKNOWN'
  const coverage = e.coverage ?? 'UNKNOWN'
  if (resolution === 'UNKNOWN') return { text: 'Unknown', cls: 'unknown' }
  if (coverage !== 'COMPLETE') return { text: 'Partial', cls: 'partial' }
  if (modality === 'MAY') return { text: 'May', cls: 'may' }
  if (truth === 'INFERRED') return { text: 'Inferred', cls: 'inferred' }
  if (truth === 'OBSERVED' || truth === 'RESOLVED') return { text: 'Resolved', cls: 'resolved' }
  return { text: truth, cls: 'unknown' }
}

const CHIP_ZH: Record<string, string> = {
  Unknown: '未知', Partial: '部分', May: '可能',
  Inferred: '推断', Resolved: '已解析',
}

function chipText(e: TopoEdge): string {
  const c = chipOf(e)
  return lang.isZh ? (CHIP_ZH[c.text] ?? c.text) : c.text
}

function shield(s: Suggestion): { confidence: number | null; why: string[]; score: number | null } {
  return {
    confidence: s.confidence ?? null,
    why: s.why ?? [],
    score: s.score ?? null,
  }
}

// ---- source dock -----------------------------------------------------------
// Upstream witnesses for JPL carry only the repo root as source.file; fall
// back to the call-site node's file, then to a fact_id-embedded path.
// Never fabricate: if nothing resolvable, the Monaco link is hidden.
function witnessFileOf(w: Witness, raw: TopoEdge): string | null {
  const m = meta.value
  if (!m || !store.index) return null
  return resolveWitnessFile(w, raw, m, store.index)
}

function openWitnessSource(w: Witness, raw: TopoEdge): void {
  const m = meta.value
  if (!m?.source_repo_id) return
  const path = witnessFileOf(w, raw)
  if (!path) return
  store.openSource(m.source_repo_id, path, w.source?.line ?? null)
}

function openNodeSource(n: SceneNode): void {
  const m = meta.value
  if (!m?.source_repo_id) return
  const path = n.repoPath ?? (n.file ? repoPathOf(n.file, m) : null)
  if (!path) return
  store.openSource(m.source_repo_id, path, null)
}

// ---- drill-down grouping (module/file edge -> file edges -> fn edges) ------
interface FileGroup {
  srcFile: string
  tgtFile: string
  edges: TopoEdge[]
  kinds: Record<string, number>
}

const drillGroups = computed<FileGroup[]>(() => {
  const e = selectedEdge.value
  if (!e || !store.index) return []
  const idx = store.index
  const groups = new Map<string, FileGroup>()
  for (const raw of e.raw) {
    const srcF = idx.fileOf.get(raw.source) ?? '(resource)'
    const tgtF = raw.target === '[Unknown Dynamic Target]'
      ? '[Unknown Dynamic Target]'
      : (idx.fileOf.get(raw.target) ?? '(resource)')
    if (raw.kind === 'RESOURCE') continue     // handled by kind group, no file pair
    if (srcF === tgtF) continue               // same-file edges are internal
    const key = `${srcF} -> ${tgtF}`
    if (!groups.has(key)) groups.set(key, { srcFile: srcF, tgtFile: tgtF, edges: [], kinds: {} })
    const g = groups.get(key)!
    g.edges.push(raw)
    g.kinds[raw.kind] = (g.kinds[raw.kind] ?? 0) + 1
  }
  return [...groups.values()]
})

function expandFileEdge(cid: string): void {
  const f = store.index?.fileOf.get(cid)
  if (!f) return
  const n = nodeById.value.get(`file:${f}`)
  if (n) {
    store.selectNode(n.id)
    store.selectEdge(null)
  }
}

function boundaryRows(b: NonNullable<Suggestion['boundary']>) {
  return [
    { label: 'Inputs', items: b.data_in ?? [], hint: 'data_in' },
    { label: 'Outputs', items: b.data_out ?? [], hint: 'data_out' },
    { label: 'State crossings', items: b.state_inout ?? [], hint: 'state_inout' },
    { label: 'Resource ports', items: b.resource_ports ?? [], hint: 'resource_ports' },
    { label: 'Calls in', items: b.calls_in ?? [], hint: 'calls_in' },
    { label: 'Calls out', items: b.calls_out ?? [], hint: 'calls_out' },
  ]
}

function resourceUsers(name: string): string[] {
  const out: string[] = []
  for (const e of bundle.value?.edges ?? []) {
    if (e.kind === 'RESOURCE' && e.target === name) {
      const n = store.index?.nodeById.get(e.source)
      if (n && out.length < 20) out.push(`${n.name} (${n.file})`)
    }
  }
  return out
}

function unknownCallers(): string[] {
  const out: string[] = []
  for (const e of bundle.value?.edges ?? []) {
    if (e.target === '[Unknown Dynamic Target]') {
      const n = store.index?.nodeById.get(e.source)
      if (n && out.length < 20) out.push(`${n.name} (${n.file})`)
    }
  }
  return out
}

const kindLabel: Record<string, string> = {
  CALL: 'Call', DATA: 'Data', STATE: 'State', CONTROL: 'Control',
  TIME: 'Time', RESOURCE: 'Resource',
}

function onSelectSuggestion(s: Suggestion, i: number): void {
  // select the corresponding scene node (aligned -> its module; else sugg)
  const mod = store.index?.suggModule.get(i) ?? null
  if (mod) store.selectNode(`module:${mod}`)
  else store.selectNode(`sugg:${i}`)
}
</script>

<template>
  <aside class="topo-inspector" data-testid="topo-inspector">
    <!-- ================= overview ================= -->
    <template v-if="!selectedNode && !selectedEdge">
      <div v-if="bundle" class="ti-section">
        <div class="ti-title">{{ t('wb.inspector.lane') }}</div>
        <div class="ti-row"><b>{{ meta?.label }}</b> <span class="mono">{{ bundle.lane }}</span></div>
        <div class="ti-row">
          <span class="chip cap" data-testid="capability-badge">{{ meta?.data_capability }}</span>
          <span v-if="meta?.synthetic" class="chip synth">合成 (SYNTHETIC)</span>
        </div>
        <div class="ti-row muted">
          {{ meta?.languages?.join(', ') }} · {{ bundle.nodes.length }} nodes ·
          {{ bundle.edges.length }} edges
        </div>
        <div v-for="n in meta?.capability_notes" :key="n" class="ti-note">⚠ {{ n }}</div>
      </div>

      <div v-if="bundle" class="ti-section" data-testid="di-lane">
        <div class="ti-title">Data Interface (FAC parse set) — DERIVED</div>
        <div class="ti-row muted">
          {{ Object.keys(store.dataPorts).length }} functions ·
          {{ Object.values(store.dataPorts).reduce((n, ps) => n + ps.length, 0) }} ports
          — 真实 FAC 源码解析 (lapack/blas Fortran + sfac.c C)；非本拓扑节点也会列出（解析集独立于拓扑 lane）
        </div>
        <div class="di-fn" v-for="(ps, fn) in store.dataPorts" :key="fn" :data-testid="`di-fn-${fn}`">
          <div class="di-fn-name mono">{{ fn }} <span class="muted">({{ ps[0]?.file }})</span></div>
          <div
            v-for="p in ps" :key="p.port_id"
            class="di-port" :data-testid="`di-fn-port-${fn}-${p.name}`"
          >
            <span class="di-name">{{ p.name }}</span>
            <span class="di-dir">{{ p.direction }}</span>
            <span class="di-type">{{ p.dtype }}{{ p.rank != null ? `[${p.rank}]` : '' }}</span>
            <span class="di-shape" :class="`sh-${p.shape_status.toLowerCase()}`">
              [{{ (p.shape && p.shape.length ? p.shape : ['?']).join('×') }}]
            </span>
            <span v-if="p.byte_size != null" class="di-size">{{ p.byte_size }}B</span>
            <span v-else-if="p.byte_size_expr" class="di-size expr">{{ p.byte_size_expr }}</span>
            <span class="di-status">{{ p.shape_status }}</span>
          </div>
        </div>
        <div class="di-cap">
          capability (FAC):
          <div v-for="(l, i) in diCapLines" :key="i" class="muted">{{ l }}</div>
        </div>
      </div>

      <div v-if="bundle" class="ti-section" data-testid="overlay-stats">
        <div class="ti-title">{{ t('wb.inspector.overlayCounts') }}</div>
        <table class="ti-table">
          <tr><th>kind</th><th>edges</th></tr>
          <tr v-for="k in (['CALL','DATA','STATE','CONTROL','TIME','RESOURCE'] as RelationKind[])" :key="k">
            <td>{{ k }}</td><td class="mono">{{ meta?.overlay_stats?.[k] ?? 0 }}</td>
          </tr>
        </table>
        <div v-if="meta && (meta.overlay_stats.DATA ?? 0) === 0" class="ti-note">
          DATA capability {{ meta.data_capability }}: 0 resolved DATA wires in
          this topology — wires are NOT fabricated by the UI (U8).
        </div>
      </div>

      <div v-if="bundle && bundle.suggestions.length" class="ti-section" data-testid="suggestions-list">
        <div class="ti-title">
          {{ t('wb.inspector.suggestions') }} ({{ meta?.suggestion_origin === 'route_b' ? 'MODULE-OPT Route B' : 'MODULE-INFER0' }})
        </div>
        <button
          v-for="(s, i) in bundle.suggestions" :key="i"
          type="button" class="sugg-row" :class="{ pos: (s.score ?? 0) > 0 }"
          @click="onSelectSuggestion(s, i)"
        >
          <span class="sr-name">✦ {{ s.classification?.declared_module ?? `suggestion ${i + 1}` }}</span>
          <span class="sr-score">{{ s.score?.toFixed(3) }}</span>
        </button>
        <div class="ti-note">建议 = 分析假设，不是规范事实 (U4)。提升为架构是单独的显式动作 —— 此处不可用。</div>
      </div>

      <div v-if="bundle?.route_c && store.showRouteCDebug" class="ti-section" data-testid="route-c-debug">
        <div class="ti-title">{{ t('wb.inspector.routeC') }}</div>
        <div class="ti-note warn">⚠ 社区挑战者 (Community Challenger) — 非生产推断 (Not Production Inference)。
          MODULE-OPT0 未发现生产价值；仅诊断用途。</div>
        <table class="ti-table">
          <tr><th>id</th><th>size</th><th>legal</th><th>score</th></tr>
          <tr v-for="c in bundle.route_c.communities ?? []" :key="c.community_id">
            <td>{{ c.community_id }}</td><td>{{ c.size }}</td>
            <td>{{ c.legal_status ?? '' }}</td><td>{{ c.score?.toFixed(3) ?? '' }}</td>
          </tr>
        </table>
        <div class="ti-row muted">模块度 (modularity) {{ bundle.route_c.modularity }} · 合法社区 {{ bundle.route_c.legal_count }}</div>
      </div>

      <div class="ti-section hint-section">
        <div class="ti-title">{{ t('wb.inspector.ops') }}</div>
        <div class="ti-note">双击 Module / Suggested 展开 Files；双击 File 展开 Functions。
          点击边缘查看真相字段与 witness。面包屑返回上层。</div>
      </div>
    </template>

    <!-- ================= node ================= -->
    <template v-else-if="selectedNode">
      <div class="ti-section">
        <div class="ti-title">
          {{ selectedNode.kind === 'unknown' ? '⚡ [未知动态目标] [Unknown Dynamic Target]' :
             selectedNode.kind === 'resource' ? selectedNode.label :
             selectedNode.kind.startsWith('suggested') ? '✦ 建议 (Suggested)' :
             selectedNode.kind === 'file' ? '文件 (File)' :
             selectedNode.kind === 'function' ? '函数 (Function)' : '声明模块 (Declared Module)' }}
        </div>
        <div class="ti-row"><b>{{ selectedNode.label }}</b></div>
        <div class="ti-row muted">{{ nodeSub }}</div>

        <!-- unknown dynamic target honesty (U6) -->
        <template v-if="selectedNode.kind === 'unknown'">
          <div class="ti-note warn" data-testid="unknown-truth">
            不是真实源码函数 (NOT a real source function)。target_resolution = UNKNOWN ·
            coverage = UNKNOWN/PARTIAL（下方为原始边字段）。
          </div>
          <div class="ti-table-wrap">
            <div class="ti-title small">{{ t('wb.inspector.callers') }}</div>
            <div v-for="c in unknownCallers()" :key="c" class="mono muted ti-row">{{ c }}</div>
          </div>
        </template>

        <!-- resource node (U8: resource stays a Resource node, not a Function) -->
        <template v-else-if="selectedNode.kind === 'resource'">
          <div class="ti-row">{{ t('wb.inspector.resourcePortNote') }}</div>
          <div class="ti-title small">{{ t('wb.inspector.users') }}</div>
          <div v-for="u in resourceUsers(selectedNode.label)" :key="u" class="mono muted ti-row">{{ u }}</div>
        </template>

        <!-- function: DATA-INTERFACE0 port chips (derived; never canonical) -->
        <template v-else-if="selectedNode.kind === 'function'">
          <!-- CROSS-PROJECTION-SYNC0: cross-projection "Explore in" jump menu -->
          <div class="ti-section" data-testid="explore-in">
            <div class="ti-title small">Explore in</div>
            <div class="explore-row">
              <button type="button" class="explore-btn" data-testid="explore-in-human"
                @click="explore('human')">Human Flow</button>
              <button type="button" class="explore-btn" data-testid="explore-in-region"
                @click="explore('region')">Flow Region</button>
              <button type="button" class="explore-btn" data-testid="explore-in-module"
                @click="explore('module')">Module</button>
              <button type="button" class="explore-btn" data-testid="explore-in-file"
                @click="explore('file')">File</button>
              <button type="button" class="explore-btn" data-testid="explore-in-di"
                @click="explore('di')">Data Interface</button>
              <button type="button" class="explore-btn" data-testid="explore-in-source"
                @click="explore('source')">Source</button>
            </div>
          </div>
          <div class="ti-section" data-testid="di-ports">
            <div class="ti-title small">Data Interface (Ports) — DERIVED</div>
            <div
              v-for="p in functionPorts" :key="p.port_id"
              class="di-port" :data-testid="`di-port-${p.name}`"
            >
              <span class="di-name">{{ p.name }}</span>
              <span class="di-dir">{{ p.direction }}</span>
              <span class="di-type">{{ p.dtype }}{{ p.rank != null ? `[${p.rank}]` : '' }}</span>
              <span class="di-shape" :class="`sh-${p.shape_status.toLowerCase()}`">
                [{{ (p.shape && p.shape.length ? p.shape : ['?']).join('×') }}]
              </span>
              <span v-if="p.byte_size" class="di-size">{{ p.byte_size }}B</span>
              <span v-else-if="p.byte_size_expr" class="di-size expr">{{ p.byte_size_expr }}</span>
            </div>
            <div v-if="!functionPorts.length" class="ti-note muted">
              无已解析端口（此函数不在 DATA-INTERFACE 解析集内）
            </div>
            <div class="di-cap">
              <div v-for="(l, i) in diCapLines" :key="i" class="muted">{{ l }}</div>
            </div>
          </div>

          <div v-if="store.showKernelDebug" class="ti-section" data-testid="kern-debug">
            <div class="ti-title small">Kernel Substrate — DEV (SEMANTIC-SUBSTRATE1)</div>
            <div v-if="store.kernelSummary?.counts" class="di-cap" data-testid="kern-counts">
              <span
                v-for="(v, k) in store.kernelSummary.counts" :key="k"
                class="mono muted kern-count" :data-testid="`kern-count-${k}`"
              >{{ k }}: {{ v }}</span>
            </div>
            <div v-if="kernSupport" class="muted" data-testid="kern-support">
              support → ports {{ kernSupport.ports }} · flow regions
              {{ kernSupport.flow_regions }} · semantic stages
              {{ kernSupport.semantic_stages }}
            </div>
            <div v-else class="ti-note muted">无 kernel 支持链接(此函数不在解析集内)</div>
          </div>

          <div class="ti-title small">{{ t('wb.inspector.identity') }}</div>
          <div class="mono muted ti-row">{{ selectedNode.canonicalId }}</div>
          <div class="ti-row">文件: <span class="mono">{{ selectedNode.file }}</span></div>
          <div v-if="selectedNode.language" class="ti-row">语言: {{ selectedNode.language }}</div>
          <div v-if="selectedNode.isCrossCutting" class="ti-row"><span class="chip cc">{{ t('wb.node.cc') }}</span></div>
          <div v-if="selectedNode.repoPath" class="ti-row">
            <button type="button" class="link" @click="openNodeSource(selectedNode!)">打开源码 (Monaco)</button>
          </div>
          <template v-if="selectedNode.canonicalId">
            <div class="ti-title small">{{ t('wb.inspector.resources') }}</div>
            <div class="ti-row muted">
              {{ store.index?.nodeById.get(selectedNode.canonicalId)?.resources?.join(', ') || t('wb.inspector.none') }}
            </div>
            <div class="ti-title small">{{ t('wb.inspector.control') }}</div>
            <div
              v-for="(cl, i) in store.index?.nodeById.get(selectedNode.canonicalId)?.control_landmarks ?? []"
              :key="i" class="ti-row mono muted"
            >
              {{ cl.semantic_kind }}: {{ cl.guard }}<span v-if="cl.source?.line"> @L{{ cl.source.line }}</span>
            </div>
          </template>
        </template>

        <!-- file -->
        <template v-else-if="selectedNode.kind === 'file'">
          <div class="ti-section" data-testid="di-module">
            <div class="ti-title small">Data Interface (Module/File) — DERIVED</div>
            <div
              v-for="p in modulePorts" :key="p.port_id"
              class="di-port" :data-testid="`di-module-port-${p.name}`"
            >
              <span class="di-name">{{ p.name }}</span>
              <span class="di-dir">{{ p.direction }}</span>
              <span class="di-type">{{ p.dtype }}{{ p.rank != null ? `[${p.rank}]` : '' }}</span>
              <span class="di-shape" :class="`sh-${p.shape_status.toLowerCase()}`">
                [{{ (p.shape && p.shape.length ? p.shape : ['?']).join('×') }}]
              </span>
              <span v-if="p.byte_size" class="di-size">{{ p.byte_size }}B</span>
              <span v-else-if="p.byte_size_expr" class="di-size expr">{{ p.byte_size_expr }}</span>
            </div>
            <div v-if="!modulePorts.length" class="ti-note muted">
              无已解析模块端口（此文件不在 DATA-INTERFACE 解析集内）
            </div>
          </div>
          <div class="ti-row mono">{{ selectedNode.file }}</div>
          <div v-if="selectedNode.repoPath" class="ti-row">
            <button type="button" class="link" @click="openNodeSource(selectedNode!)">打开源码 (Monaco)</button>
          </div>
          <div class="ti-row">
            <button type="button" class="ghost" @click="store.push({ kind: 'file', id: selectedNode.file!, label: selectedNode.label })">展开 Functions ▸</button>
          </div>
        </template>

        <!-- declared module -->
        <template v-else-if="selectedNode.kind === 'module'">
          <div class="ti-section" data-testid="di-module">
            <div class="ti-title small">Data Interface (Module) — DERIVED</div>
            <div
              v-for="p in modulePorts" :key="p.port_id"
              class="di-port" :data-testid="`di-module-port-${p.name}`"
            >
              <span class="di-name">{{ p.name }}</span>
              <span class="di-dir">{{ p.direction }}</span>
              <span class="di-type">{{ p.dtype }}{{ p.rank != null ? `[${p.rank}]` : '' }}</span>
              <span class="di-shape" :class="`sh-${p.shape_status.toLowerCase()}`">
                [{{ (p.shape && p.shape.length ? p.shape : ['?']).join('×') }}]
              </span>
              <span v-if="p.byte_size" class="di-size">{{ p.byte_size }}B</span>
              <span v-else-if="p.byte_size_expr" class="di-size expr">{{ p.byte_size_expr }}</span>
            </div>
            <div v-if="!modulePorts.length" class="ti-note muted">
              无已解析模块端口（此模块不在 DATA-INTERFACE 解析集内）
            </div>
          </div>
          <div class="ti-row">functions: {{ selectedNode.fnCount }} · files: {{ selectedNode.fileCount }}</div>
          <div v-if="selectedNode.crossCuttingCount" class="ti-row">
            <span class="chip cc">cross-cutting members: {{ selectedNode.crossCuttingCount }}</span>
          </div>
          <div v-if="selectedNode.resources?.length" class="ti-row">resources: {{ selectedNode.resources.join(', ') }}</div>
          <div class="ti-row">
            <button type="button" class="ghost" @click="store.push({ kind: 'module', id: selectedNode.label, label: selectedNode.label })">展开 Files ▸</button>
          </div>
          <template v-if="selectedNode.badge">
            <div class="ti-divider"></div>
            <SuggestedDetails :s="selectedNode.badge" />
          </template>
          <template v-else>
            <div class="ti-note">该声明模块没有对齐的 MODULE-OPT0 建议。</div>
          </template>
        </template>

        <!-- suggested -->
        <template v-else>
          <div class="ti-kinds">
            <span class="chip sugg">{{ t('wb.node.sugg') }}</span>
            <span v-if="selectedNode.score !== null && selectedNode.score !== undefined" class="chip" :class="selectedNode.score > 0 ? 'ok' : 'neg'">
              score {{ selectedNode.score.toFixed(3) }}
            </span>
          </div>
          <template v-if="selectedNode.suggestionIdx !== undefined && bundle">
            <SuggestedDetails :s="bundle.suggestions[selectedNode.suggestionIdx]" />
          </template>
          <div class="ti-row">
            <button type="button" class="ghost" @click="store.push({ kind: 'sugg', id: String(selectedNode.suggestionIdx), label: selectedNode.label })">展开 Files ▸</button>
          </div>
        </template>
      </div>
    </template>

    <!-- ================= edge ================= -->
    <template v-else-if="selectedEdge">
      <div class="ti-section" data-testid="edge-inspector">
        <div class="ti-title">{{ t('wb.inspector.edge') }}</div>
        <div class="ti-row">
          <span class="mono">{{ labelOf(selectedEdge.source) }}</span> →
          <span class="mono">{{ labelOf(selectedEdge.target) }}</span>
        </div>
        <div v-if="selectedEdge.raw.length > 1" class="ti-row muted">
          {{ t('wb.inspector.aggregate', { n: selectedEdge.raw.length }) }}
        </div>

        <!-- per-kind groups (real relation types, never merged) -->
        <div v-for="(count, kind) in selectedEdge.kinds" :key="kind" class="ti-group">
          <div class="ti-title small">{{ kindLabel[kind] ?? kind }} × {{ count }}</div>
          <div
            v-for="(raw, ri) in selectedEdge.raw.filter((r) => r.kind === kind)"
            :key="ri" class="edge-raw" :data-testid="`raw-edge-${kind}`"
          >
            <div class="er-line">
              <span class="mono">{{ memberInfo(raw.source).name }}</span>
              <span class="er-arrow">→</span>
              <span class="mono">{{ raw.target === '[Unknown Dynamic Target]' ? '[Unknown Dynamic Target]' : memberInfo(raw.target).name }}</span>
              <span class="chip" :class="chipOf(raw).cls">{{ chipText(raw) }}</span>
            </div>
            <div class="er-file muted">
              {{ memberInfo(raw.source).file }}{{ raw.source_span?.line ? `:${raw.source_span.line}` : '' }}
            </div>
            <!-- data wire: Function.output -> Data -> Function.input (U3/U8) -->
            <div v-if="raw.kind === 'DATA'" class="er-data mono" :data-testid="`data-wire-${kind}`">
              数据 token: {{ raw.data?.token ?? '?' }}<span v-if="raw.data?.flow"> · 流向: {{ raw.data.flow }}</span>
              <span class="chip" :class="chipOf(raw).cls">{{ chipText(raw) }} — {{ raw.truth_class }} 时不被视为已解析数据链</span>
            </div>
            <div v-if="raw.guard" class="er-guard mono">守卫: {{ raw.guard }}</div>
            <div v-if="raw.kind === 'TIME'" class="er-time mono" data-testid="time-edge">
              {{ raw.execution_modality === 'MAY' ? 'MAY_PRECEDE' : 'MUST_PRECEDE' }}
              <span v-if="!selectedEdge.raw.some((r) => r.truth_class === 'OBSERVED')" class="er-runtime-note">
                — no runtime evidence (OBSERVED_HAPPENS_BEFORE) in this topology; timeline not invented
              </span>
            </div>
          </div>
        </div>

        <!-- full truth fields, raw (U1) -->
        <div v-if="selectedEdge.raw[0]" class="ti-section-inner" data-testid="truth-fields">
          <div class="ti-title small">{{ t('wb.inspector.truth') }}</div>
          <div v-for="(raw, i) in selectedEdge.raw" :key="i" class="ti-row mono muted truth-row">
            #{{ i + 1 }} truth_class={{ raw.truth_class }} modality={{ raw.execution_modality }}
            target_resolution={{ raw.target_resolution }} coverage={{ raw.coverage }}
          </div>
        </div>

        <!-- drill-down: module/file edge -> file edges -> function edges (U5) -->
        <div v-if="drillGroups.length" class="ti-section-inner" data-testid="drilldown">
          <div class="ti-title small">{{ t('wb.inspector.drilldown') }}</div>
          <div v-for="g in drillGroups" :key="g.srcFile + g.tgtFile" class="dd-group">
            <div class="dd-file-line">
              <span class="mono">{{ g.srcFile }}</span> →
              <span class="mono">{{ g.tgtFile }}</span>
              <span class="muted"> · {{ Object.entries(g.kinds).map(([k, c]) => `${k}×${c}`).join(' ') }}</span>
              <button type="button" class="link" @click="expandFileEdge(g.edges[0]!.source)">定位</button>
            </div>
            <div
              v-for="(fnEdge, fi) in g.edges" :key="fi"
              class="dd-fn-line"
            >
              <span class="mono">{{ memberInfo(fnEdge.source).name }}</span> →
              <span class="mono">{{ memberInfo(fnEdge.target).name }}</span>
              <span class="muted">{{ fnEdge.source_span?.line ? `@L${fnEdge.source_span.line}` : '' }}</span>
            </div>
          </div>
        </div>

        <!-- witnesses -> AnalysisFact -> source (U5) -->
        <div v-if="selectedEdge.raw.some((r) => r.representative_witnesses?.length)" class="ti-section-inner" data-testid="witnesses">
          <div class="ti-title small">{{ t('wb.inspector.witnesses') }}</div>
          <div
            v-for="(raw, i) in selectedEdge.raw" :key="i"
          >
            <div v-for="(w, wi) in raw.representative_witnesses ?? []" :key="wi" class="witness mono">
              <div class="w-fact">{{ w.fact_id }} <span class="chip prov">{{ w.provider }}</span></div>
              <div v-if="witnessFileOf(w, raw)" class="w-src">
                {{ witnessFileOf(w, raw) }}{{ w.source?.line ? `:${w.source.line}` : '' }}
                <button type="button" class="link" @click="openWitnessSource(w, raw)">打开源码 (Monaco)</button>
              </div>
              <div v-else class="w-src muted">源文件: 调用点解析未知（不伪造路径）</div>
              <div v-if="w.expr" class="w-expr muted">{{ w.expr }}</div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- source dock shadow (rendered by parent; this panel explains) -->
  </aside>
</template>

<style scoped>
.topo-inspector {
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: auto;
  padding: 10px;
  font-size: 12px;
}
.ti-row.mono {
  word-break: break-all;
}
.ti-section {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
}
.ti-title {
  font-weight: 700;
  margin-bottom: 6px;
}
.ti-title.small {
  font-size: 11px;
  margin-top: 8px;
  color: var(--text);
}
.ti-row {
  margin-bottom: 4px;
}
.ti-note {
  color: var(--amber-text);
  background: var(--amber-bg);
  border: 1px solid var(--sugg-border);
  border-radius: 6px;
  padding: 6px 8px;
  margin-top: 6px;
  font-size: 11px;
}
.ti-note.warn {
  color: var(--danger);
  background: var(--danger-bg);
  border-color: var(--danger);
}
.ti-divider {
  border-top: 1px dashed var(--border-strong, #c9ced6);
  margin: 8px 0;
}
.ti-kinds {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.chip {
  font-size: 10px;
  border-radius: 5px;
  padding: 1px 6px;
  background: var(--chip-bg);
  color: var(--chip-text);
  font-weight: 600;
  white-space: nowrap;
}
.chip.cap { background: var(--amber-bg); color: var(--amber-text); }
.chip.synth { background: var(--danger-bg); color: var(--danger); }
.chip.sugg { background: var(--amber-bg); color: var(--amber-text); }
.chip.cc { background: var(--chip-bg); color: var(--chip-text); }
.chip.ok { background: #16512e; color: #86efac; }
.chip.neg { background: var(--danger-bg); color: var(--danger); }
.chip.resolved { background: #16512e; color: #86efac; }
.chip.inferred { background: #1e3a8a; color: #93c5fd; }
.chip.may { background: var(--amber-bg); color: var(--amber-text); }
.chip.unknown { background: var(--chip-bg); color: var(--chip-text); }
.chip.partial { background: #3a2f12; color: #f5c96b; }
.chip.prov { background: var(--chip-bg); color: var(--chip-text); font-weight: 400; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
.muted { color: var(--text-muted); }
.link {
  color: var(--accent);
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-size: 11px;
}
.ghost {
  margin-top: 4px;
  font-size: 11px;
}
.sugg-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--sugg-bg);
  padding: 5px 8px;
  margin-bottom: 4px;
  cursor: pointer;
  text-align: left;
}
.sugg-row:hover { border-color: var(--sugg-border); }
.sugg-row .sr-name { font-weight: 600; }
.sugg-row .sr-score { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: var(--text-muted); }
.sugg-row.pos .sr-score { color: var(--ok); }
.ti-table-wrap { margin-top: 6px; }
.edge-raw {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 6px;
  background: var(--surface);
}
.er-line { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.er-arrow { color: var(--text-muted); }
.er-file { font-size: 10px; margin-top: 2px; }
.er-data, .er-guard, .er-time {
  margin-top: 4px;
  font-size: 10px;
  background: var(--accent-soft);
  border-radius: 4px;
  padding: 3px 6px;
  display: inline-block;
}
.er-time { background: var(--resource-bg); }
.er-runtime-note { color: var(--amber-text); }
.truth-row { font-size: 10px; }
.dd-group { margin-bottom: 6px; }
.dd-file-line { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.dd-fn-line { padding-left: 12px; font-size: 11px; }
.witness { margin-bottom: 6px; }
.w-fact { word-break: break-all; }
.w-src { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-top: 2px; }
.w-expr { font-style: italic; }
.hint-section .ti-note { background: var(--surface); color: var(--chip-text); border-color: var(--border); }
/* DATA-INTERFACE0 port rows */
.di-port {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  padding: 3px 6px;
  margin: 3px 0;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  font-size: 11px;
}
.di-name { font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.di-dir {
  font-size: 9px;
  font-weight: 700;
  border-radius: 4px;
  padding: 0 5px;
  background: var(--accent-soft);
  color: var(--accent);
}
.di-type { color: var(--text-muted); }
.di-shape { font-family: ui-monospace, Menlo, monospace; }
.di-shape.sh-resolved { color: var(--ok); }
.di-shape.sh-symbolic { color: var(--amber-text); }
.di-shape.sh-partial { color: #f5c96b; }
.di-shape.sh-unknown { color: var(--text-muted); }
.di-size { font-size: 10px; color: var(--text-muted); }
.di-size.expr { font-family: ui-monospace, Menlo, monospace; font-style: italic; }
.di-cap {
  margin-top: 6px;
  font-size: 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  word-break: break-all;
}
.di-fn { margin-top: 6px; }
.di-fn-name { font-weight: 700; margin-bottom: 2px; font-size: 11px; }
.di-status { margin-left: auto; font-size: 9px; color: var(--text-muted); }
</style>
