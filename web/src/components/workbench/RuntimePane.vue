<script setup lang="ts">
// UI-REALITY-ALIGN0 §11-§13/§25/§29: Runtime perspective.
//
// Truth rules this pane enforces:
//   * every number travels with its run context (run / workload /
//     instrumentation / revision) — never a bare "hot function";
//   * runtime data coverage is labelled PARTIAL (selective probes) instead of
//     implying that all memory traffic was captured;
//   * static and observed invocations are separate facts.
import { computed, onMounted, ref } from 'vue'
import { apiFetch } from '../../api/client'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import { useTopoStore } from '../../stores/topo'
import { useSelectionStore } from '../../stores/selection'
import { useWorkbenchStore } from '../../stores/workbench'
import { useLangStore } from '../../stores/lang'
import { formatShape } from '../../domain/truth'
import TruthBadge from '../ui/TruthBadge.vue'
import EmptyState from '../ui/EmptyState.vue'

const store = useFlowTopologyStore()
const topo = useTopoStore()
const selection = useSelectionStore()
const workbench = useWorkbenchStore()
const lang = useLangStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const detail = ref<Record<string, any> | null>(null)
const error = ref<string | null>(null)

const run = computed(() => store.runtimeRuns.find((r) => r.run_id === store.selectedRun) ?? null)
const stats = computed<Array<[string, Record<string, any>]>>(() => {
  const s = (detail.value?.stats ?? {}) as Record<string, Record<string, any>>
  return Object.entries(s).sort((a, b) => (b[1].exclusive_ms ?? 0) - (a[1].exclusive_ms ?? 0))
})
const observedEdges = computed(() => (detail.value?.edges ?? []) as Record<string, any>[])
/** Source scope of the observed files (evidence-derived, not assumed). */
const artifactScope = computed(() => {
  const dirs = new Set<string>()
  for (const [, st] of stats.value) {
    const path = String((st as any).file ?? '')
    if (path.includes('/')) dirs.add(path.split('/')[0])
  }
  return [...dirs].sort()
})
const stageStats = computed<Record<string, any>>(() => store.runtimeStageStats ?? {})

const dataObjects = computed(() => store.rtdObjects ?? [])
const dataEvents = computed(() => store.rtdEvents ?? [])
const observedDim = computed(() => (store.rtdDgesv as any)?.observed_dim ?? null)
const dgesvSpans = computed(() => (store.rtdDgesv as any)?.source_spans ?? [])
const dgesvBindings = computed(() => (store.rtdDgesv as any)?.bindings ?? [])
const dgesvEvents = computed(() => dataEvents.value.filter((e) => String(e.note ?? '').toLowerCase().includes('dgesv')
  || String(e.invocation_id ?? '').includes('dgesv')).slice(0, 6))

async function load(): Promise<void> {
  try {
    await store.loadRuntime()
    detail.value = await apiFetch<Record<string, any>>(
      `/runtime/runs/${encodeURIComponent(store.selectedRun)}`)
    await store.loadRuntimeData()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function setRun(rid: string): Promise<void> {
  await store.setSelectedRun(rid)
  detail.value = await apiFetch<Record<string, any>>(`/runtime/runs/${encodeURIComponent(rid)}`)
}

function inspect(name: string, stat: Record<string, any>): void {
  selection.set({
    kind: 'runtime-invocation', id: `rtsym:${name}`, label: name,
    detail: `${stat.count}× · excl ${stat.exclusive_ms} ms`, file: stat.file ?? null,
    plane: 'OBSERVED', truth_class: 'OBSERVED', coverage: 'COMPLETE',
    authority: `GET /api/v1/runtime/runs/${store.selectedRun}`,
    payload: { runtime_symbol: stat, run: run.value },
  })
}

onMounted(() => { void load() })
</script>

<template>
  <div class="rt" data-testid="runtime-pane">
    <!-- §13: the run context header is not optional -->
    <header class="rt-ctx" data-testid="runtime-context">
      <select class="rt-sel" :value="store.selectedRun" data-testid="runtime-run-select"
              @change="setRun(($event.target as HTMLSelectElement).value)">
        <option v-for="r in store.runtimeRuns" :key="r.run_id" :value="r.run_id">
          {{ r.run_id }} · {{ r.workload_id }}
        </option>
      </select>
      <template v-if="run">
        <span class="ctx-item">workload <b>{{ run.workload_id }}</b></span>
        <span class="ctx-item" :title="(run.instrumentation as any)?.evidence ?? ''">
          instrumentation <b>{{ (run.instrumentation as any)?.build ?? 'not recorded by the artifact' }}</b>
          <template v-if="run.instrumentation">
            · {{ L('输出等价性已核验', 'output equivalence verified') }}
            <b>equivalence_failed={{ (run.instrumentation as any).equivalence_failed }}</b>
          </template>
        </span>
        <span class="ctx-item">rev <b>{{ run.evidence_revision }}</b></span>
        <span class="ctx-item">records <b>{{ run.records }}</b></span>
        <span class="ctx-item">artifact scope <b>{{ artifactScope.join(', ') || '—' }}</b></span>
        <span class="ctx-item">exit <b>{{ run.exit_code }}</b></span>
      </template>
      <span class="rt-spacer"></span>
      <span class="ctx-note">{{ L('所有数字都属于这一次运行', 'every number belongs to this run') }}</span>
    </header>

    <EmptyState v-if="error" tone="unknown" :title="L('运行时产物不可用', 'runtime artifacts unavailable')"
                :reason="error" :hint="L('需要 analysis_tournament/runtime_trace0 产物。', 'requires runtime_trace0 artifacts')" />

    <div class="rt-body">
      <section class="rt-col">
        <h4>{{ L('按独占时间排序（本次运行）', 'by exclusive time (this run)') }}</h4>
        <table class="rt-table" data-testid="runtime-hot">
          <thead><tr><th>symbol</th><th>calls</th><th>excl ms</th><th>incl ms</th><th>binding</th></tr></thead>
          <tbody>
            <tr v-for="[name, st] in stats.slice(0, 18)" :key="name" @click="inspect(name, st)">
              <td class="mono">{{ name }}</td>
              <td class="num">{{ st.count }}</td>
              <td class="num"><b>{{ st.exclusive_ms }}</b></td>
              <td class="num">{{ st.inclusive_ms }}</td>
              <td><TruthBadge kind="truth" :value="st.binding === 'EXACT' ? 'RESOLVED' : 'UNKNOWN'" compact :suffix="st.binding" /></td>
            </tr>
          </tbody>
        </table>
        <div class="rt-note">{{ L('独占时间不含子调用；插桩值不能当绝对成本（见 Performance 视角）。',
                               'exclusive time excludes children; instrumented values are not absolute cost (see the Performance perspective).') }}</div>
      </section>

      <section class="rt-col">
        <h4>{{ L('Human Semantic 阶段（本次运行）', 'human-semantic stages (this run)') }}</h4>
        <EmptyState v-if="!Object.keys(stageStats).length" tone="unknown"
                    :title="L('没有阶段观测', 'no stage observations')"
                    :reason="L('该运行没有 stage_stats。', 'this run has no stage_stats.')" />
        <table v-else class="rt-table">
          <thead><tr><th>stage</th><th>calls</th><th>incl ms</th></tr></thead>
          <tbody>
            <tr v-for="(v, k) in stageStats" :key="k">
              <td class="mono">{{ k }}</td>
              <td class="num">{{ v.count ?? v.calls ?? '—' }}</td>
              <td class="num">{{ v.inclusive_ms ?? '—' }}</td>
            </tr>
          </tbody>
        </table>

        <h4>{{ L('运行时数据（选择性探针）', 'runtime data (selective probes)') }}</h4>
        <div class="rt-cov">
          <TruthBadge kind="coverage" value="PARTIAL" compact />
          <span>{{ L('覆盖是选择性的：只包含被插桩的点，不代表全部内存访问。', 'coverage is selective: only instrumented sites — not all memory access.') }}</span>
        </div>
        <div v-if="observedDim" class="rt-dim">
          DGESV: n = <b>{{ observedDim }}</b> <TruthBadge kind="truth" value="OBSERVED" compact />
          <span v-for="(s, i) in dgesvSpans" :key="i" class="mono rt-span" data-testid="runtime-dgesv-span"> {{ s.text }}</span>
        </div>
        <table class="rt-table" data-testid="runtime-objects">
          <thead><tr><th>object</th><th>shape</th><th>dtype</th><th>scope</th><th>bytes</th><th>cov</th></tr></thead>
          <tbody>
            <tr v-for="o in dataObjects" :key="o.runtime_data_id">
              <td class="mono">{{ o.region ?? o.canonical_location_id?.split(':').slice(-2)[0] }}</td>
              <td class="mono">{{ formatShape(o.shape) }}</td>
              <td class="mono">{{ o.dtype }}</td>
              <td>{{ o.scope }}</td>
              <td class="num">{{ o.byte_size }}</td>
              <td><TruthBadge kind="coverage" :value="o.coverage" compact /></td>
            </tr>
          </tbody>
        </table>
        <div v-if="dataEvents.length" class="rt-events" data-testid="runtime-events">
          <div class="rt-ev-head">{{ L('访问事件（前 8 条，共', 'access events (first 8 of') }} {{ dataEvents.length }}）</div>
          <div v-for="e in dataEvents.slice(0, 8)" :key="e.event_id" class="rt-ev mono">
            {{ e.order }} · {{ e.operation_kind }} · {{ e.runtime_data_id.split(':').slice(-1)[0] }} ·
            {{ e.byte_extent }}B · {{ e.source_line }}
            <span v-if="String(e.invocation_id).startsWith('rt:probe')" class="rt-probe">probe</span>
          </div>
        </div>
      </section>

      <section class="rt-col">
        <h4>{{ L('已观测调用（本次运行）', 'observed calls (this run)') }}</h4>
        <table class="rt-table" data-testid="runtime-edges">
          <thead><tr><th>caller</th><th>callee</th><th>calls</th><th>binding</th></tr></thead>
          <tbody>
            <tr v-for="(e, i) in observedEdges.slice(0, 24)" :key="i">
              <td class="mono">{{ e.parent_symbol }}</td>
              <td class="mono">{{ e.child_symbol }}</td>
              <td class="num">{{ e.count }}</td>
              <td><TruthBadge kind="truth" :value="e.child_binding === 'EXACT' ? 'RESOLVED' : 'UNKNOWN'" compact :suffix="e.child_binding" /></td>
            </tr>
          </tbody>
        </table>
        <h4>{{ L('静态 ↔ 运行期绑定', 'static ↔ runtime binding') }}</h4>
        <div class="rt-callout">
          {{ L('静态“可能调用”与运行期“确实调用”是两件事：同一条边可能 STATIC + OBSERVED，也可能 STATIC · NOT OBSERVED THIS RUN。',
               '“may call” (static) and “did call” (observed) are different facts: an edge can be STATIC + OBSERVED or STATIC · NOT OBSERVED THIS RUN.') }}
          <button type="button" class="rt-link" data-testid="runtime-to-circuit"
                  @click="workbench.setPerspective('circuit')">
            {{ L('在软件电路中查看', 'view in the software circuit') }} →
          </button>
        </div>
        <h4>{{ L('DGESV 形参绑定（位置 → 实参）', 'DGESV argument bindings') }}</h4>
        <EmptyState v-if="!dgesvBindings.length" tone="unknown"
                    :title="L('没有绑定记录', 'no binding records')"
                    :reason="L('该运行没有 DGESV binding 产物。', 'no DGESV bindings in this run.')" />
        <table v-else class="rt-table" data-testid="runtime-bindings">
          <thead><tr><th>pos</th><th>formal</th><th>value</th><th>truth</th></tr></thead>
          <tbody>
            <tr v-for="b in dgesvBindings" :key="b.binding_id">
              <td class="num">{{ b.position }}</td>
              <td class="mono">{{ b.formal_symbol_id?.split(':').slice(-1)[0] }}</td>
              <td class="num">{{ b.actual_value }}</td>
              <td><TruthBadge kind="truth" :value="b.truth === 'EXACT' ? 'RESOLVED' : 'UNKNOWN'" compact :suffix="b.truth" /></td>
            </tr>
          </tbody>
        </table>
        <div v-if="dgesvEvents.length" class="rt-note mono">
          {{ L('DGESV 探针事件', 'DGESV probe events') }}: {{ dgesvEvents.length }}
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.rt { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--canvas-bg); font-size: 11px; }
.rt-ctx { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 5px 8px; background: var(--panel); border-bottom: 1px solid var(--border); }
.rt-sel { font-size: 11px; padding: 2px 5px; border-radius: 4px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text-primary); }
.ctx-item { color: var(--text-muted); font-size: 10px; }
.ctx-item b { color: var(--text-primary); }
.ctx-note { font-size: 9.5px; color: var(--text-muted); }
.rt-spacer { flex: 1; }
.rt-body { flex: 1; min-height: 0; overflow: auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 1px; background: var(--border); }
.rt-col { background: var(--canvas-bg); padding: 6px 8px 12px; }
h4 { margin: 8px 0 4px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted); }
.rt-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.rt-table th { text-align: left; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border); padding: 1px 3px; }
.rt-table td { padding: 1px 3px; border-bottom: 1px solid var(--border); }
.rt-table tbody tr:hover { background: var(--hover); cursor: pointer; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.rt-note { margin-top: 4px; font-size: 9.5px; color: var(--text-muted); line-height: 1.6; }
.rt-cov { display: flex; gap: 5px; align-items: baseline; margin: 3px 0; color: var(--text-muted); font-size: 10px; }
.rt-dim { margin: 3px 0; font-size: 10.5px; }
.rt-span { color: var(--truth-observed); }
.rt-events { margin-top: 5px; }
.rt-ev-head { font-size: 10px; color: var(--text-muted); margin-bottom: 2px; }
.rt-ev { font-size: 9.5px; padding: 1px 0; border-bottom: 1px dotted var(--border); }
.rt-probe { color: var(--plane-probe); margin-left: 4px; }
.rt-callout { border: 1px dashed var(--border-strong); border-radius: 5px; padding: 5px 6px; color: var(--text-muted); font-size: 10px; line-height: 1.6; }
.rt-link { margin-left: 6px; border: none; background: none; color: var(--accent); cursor: pointer; font-size: 10px; }
.mono { font-family: var(--mono); }
</style>
