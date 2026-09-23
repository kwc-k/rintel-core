<script setup lang="ts">
// UI-REALITY-ALIGN0 §13: Performance perspective.
//
// The rule: a hotspot number may never appear without its run context
// (run · workload · instrumentation · revision · integrity), and the default
// sentence is "observed software hotspot in this run" — never a bare claim
// that some function *is* the problem.
import { computed, onMounted } from 'vue'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import { useTopoStore } from '../../stores/topo'
import { useSelectionStore } from '../../stores/selection'
import { useWorkbenchStore } from '../../stores/workbench'
import { useLangStore } from '../../stores/lang'
import { spanLabel } from '../../domain/truth'
import TruthBadge from '../ui/TruthBadge.vue'
import EmptyState from '../ui/EmptyState.vue'

const store = useFlowTopologyStore()
const topo = useTopoStore()
const selection = useSelectionStore()
const workbench = useWorkbenchStore()
const lang = useLangStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const run = computed(() => store.runtimeRuns.find((r) => r.run_id === store.selectedRun) ?? null)
const findings = computed(() => store.perfFindings ?? [])
const causes = computed(() => store.perfCauses ?? [])
const maxwell = computed(() => store.perfMaxwellAudit ?? null)

const topExclusive = computed(() =>
  [...(store.perfFunctions ?? [])].sort((a, b) => (b.exclusive_ms ?? 0) - (a.exclusive_ms ?? 0)).slice(0, 14))
const topInclusive = computed(() =>
  [...(store.perfFunctions ?? [])].sort((a, b) => (b.inclusive_ms ?? 0) - (a.inclusive_ms ?? 0)).slice(0, 8))

/** findings carrying an EXACT canonical span (SOURCE-SPAN-COL0 anchors) */
function findingSpan(f: Record<string, any>): Record<string, any> | null {
  const spans = f.exact_source_spans ?? f.source_spans ?? []
  const exact = spans.find((s: any) => s.precision === 'EXACT') ?? spans[0]
  return exact ?? null
}

function causesFor(f: Record<string, any>): Record<string, any>[] {
  return causes.value.filter((c) => (c.affected_finding_id ?? []).includes(f.finding_id))
}

function openSpan(span: Record<string, any> | null, line?: number | null): void {
  if (!span?.file) return
  workbench.requestSource({ file: span.file, line: span.start_line ?? line ?? null, span })
}

function inspectFinding(f: Record<string, any>): void {
  selection.set({
    kind: 'perf-finding', id: f.finding_id, label: f.finding_id,
    detail: `${f.kind} · ${f.metric} = ${f.value} ${f.unit}`,
    file: findingSpan(f)?.file ?? null, line: findingSpan(f)?.start_line ?? null,
    span: findingSpan(f), plane: 'OBSERVED', truth_class: 'OBSERVED',
    coverage: 'COMPLETE',
    authority: `GET /api/v1/runtime/runs/${store.selectedRun}/findings`,
    payload: { finding: f, causes: causesFor(f) },
  })
}

const integrity = computed(() => store.perfChain?.coverage ?? null)

/** Which source tree the observed numbers belong to — derived from the
 *  observed files themselves, never assumed. */
const artifactScope = computed(() => {
  const dirs = new Set<string>()
  for (const f of store.perfFunctions ?? []) {
    const path = String((f as any).file ?? '')
    if (path.includes('/')) dirs.add(path.split('/')[0])
  }
  return [...dirs].sort()
})
const laneRepo = computed(() => (topo.bundle?.meta as any)?.source_repo_id ?? null)
const laneMismatch = computed(() =>
  !!laneRepo.value && artifactScope.value.length > 0
  && !artifactScope.value.includes(laneRepo.value))

onMounted(async () => {
  // §13: the run context strip needs the run metadata, not just the perf blob
  if (!store.runtimeRuns?.length) { try { await store.loadRuntime() } catch { /* shown as — */ } }
  if (!store.perfFunctions?.length) void store.loadPerformance()
})
</script>

<template>
  <div class="pf" data-testid="performance-pane">
    <header class="pf-ctx" data-testid="perf-context">
      <span class="pf-title">{{ L('性能（软件层）', 'Performance (software-level)') }}</span>
      <span class="ctx-item">run <b>{{ run?.run_id ?? store.selectedRun }}</b></span>
      <span class="ctx-item">workload <b>{{ run?.workload_id ?? '—' }}</b></span>
      <span class="ctx-item" :title="(run?.instrumentation as any)?.evidence ?? ''">
        instrumentation <b>{{ (run?.instrumentation as any)?.build ?? '-finstrument-functions (recorded in PERF-CAUSE0)' }}</b>
        <template v-if="run?.instrumentation"> · equivalence_failed
          <b>{{ (run.instrumentation as any).equivalence_failed }}</b></template>
      </span>
      <span class="ctx-item">integrity <b>{{ (run?.integrity as any)?.status ?? integrity ?? '—' }}</b></span>
      <span class="ctx-item">rev <b>{{ findings[0]?.revision ?? run?.evidence_revision ?? '—' }}</b></span>
      <span class="ctx-item">artifact scope <b>{{ artifactScope.join(', ') || '—' }}</b></span>
      <span class="pf-spacer"></span>
      <span class="pf-note" data-testid="perf-instrumentation-note">
        ⚠ {{ L('插桩时间不可当作绝对成本：PERF-CAUSE0 实测插桩膨胀 5.14×（未插桩 26.995 ms / SetEleDist）。',
                'instrumented time is not absolute cost: PERF-CAUSE0 measured 5.14× inflation (26.995 ms / SetEleDist uninstrumented).') }}
      </span>
    </header>

    <div v-if="laneMismatch" class="pf-lane-hint" data-testid="perf-lane-hint">
      {{ L('注意：当前仓库浏览器 lane 是', 'note: the explorer lane is') }}
      <b class="mono">{{ laneRepo }}</b>，
      {{ L('而这次运行的观测来自', 'while these observations come from') }}
      <b class="mono">{{ artifactScope.join(', ') }}</b>。
      {{ L('要跳到源码请先切换到对应 lane。', 'switch lanes before jumping to source.') }}
    </div>
    <EmptyState v-if="store.perfError" tone="unknown"
                :title="L('性能产物不可用', 'performance artifacts unavailable')"
                :reason="store.perfError"
                :hint="L('需要 analysis_tournament/perf_topo0 产物。', 'requires perf_topo0 artifacts')" />

    <div class="pf-body">
      <section class="pf-col">
        <h4>{{ L('热点函数（本次运行 · 独占时间）', 'hot functions (this run · exclusive)') }}</h4>
        <table class="pf-table" data-testid="perf-functions">
          <thead><tr><th>symbol</th><th>calls</th><th>excl ms</th><th>incl ms</th><th>file</th></tr></thead>
          <tbody>
            <tr v-for="f in topExclusive" :key="f.canonical_function_id ?? f.symbol_name">
              <td class="mono">{{ f.symbol_name }}</td>
              <td class="num">{{ f.count }}</td>
              <td class="num"><b>{{ f.exclusive_ms }}</b></td>
              <td class="num">{{ f.inclusive_ms }}</td>
              <td class="mono muted">
                <template v-if="f.file">{{ f.file }}<template v-if="f.line">:{{ f.line }}</template></template>
                <template v-else>{{ L('未记录位置', 'no location recorded') }}</template>
              </td>
            </tr>
          </tbody>
        </table>
        <h4>{{ L('按总时间（含子调用）', 'by inclusive time') }}</h4>
        <ul class="pf-list">
          <li v-for="f in topInclusive" :key="`i-${f.symbol_name}`">
            <span class="mono">{{ f.symbol_name }}</span>
            <span class="muted">{{ f.inclusive_ms }} ms · {{ f.count }}×</span>
          </li>
        </ul>
        <div v-if="maxwell" class="pf-maxwell" data-testid="perf-maxwell-audit">
          <div class="pf-mx-head">{{ L('Maxwell 计数闭合（审计）', 'Maxwell count closure (audited)') }}</div>
          <div class="mono">2·Σi = {{ maxwell.derived_count }} · observed {{ maxwell.runtime_count }} ·
            <b>{{ maxwell.exact_match ? 'EXACT MATCH' : 'MISMATCH' }}</b></div>
          <div class="muted">{{ L('Maxwell 总时间（插桩）', 'Maxwell total (instrumented)') }}: {{ maxwell.maxwell_total_ms }} ms ·
            {{ maxwell.maxwell_mean_ns }} ns/call</div>
        </div>
      </section>

      <section class="pf-col">
        <h4>{{ L('观测发现（本次运行）', 'observed findings (this run)') }}</h4>
        <EmptyState v-if="!findings.length" tone="unknown"
                    :title="L('没有性能发现', 'no findings')"
                    :reason="L('该运行没有 findings 产物。', 'no findings artifact for this run.')" />
        <article v-for="f in findings" :key="f.finding_id" class="pf-finding" data-testid="perf-finding"
                 @click="inspectFinding(f)">
          <div class="pff-head">
            <b>{{ f.finding_id }}</b>
            <span class="pff-kind">{{ f.kind }}</span>
            <TruthBadge kind="truth" value="OBSERVED" compact />
          </div>
          <div class="pff-line">
            {{ L('本次运行观测到的软件热点', 'observed software hotspot in this run') }}:
            <b>{{ f.metric }} = {{ f.value }} {{ f.unit }}</b>
          </div>
          <div class="muted">{{ f.baseline_context }}</div>
          <div v-if="findingSpan(f)" class="pff-span mono" @click.stop="openSpan(findingSpan(f))">
            {{ spanLabel(findingSpan(f)) }}
            <TruthBadge kind="precision" :value="findingSpan(f)?.precision" compact />
            ↗
          </div>
          <div v-for="c in causesFor(f)" :key="c.cause_id" class="pff-cause">
            <span class="pff-cid">{{ c.cause_id }}</span>
            <span class="pff-verdict" :data-verdict="c.verdict">{{ c.verdict }}</span>
            <span class="pff-hypo">{{ c.hypothesis }}</span>
          </div>
        </article>
      </section>

      <section class="pf-col">
        <h4>{{ L('PERF-CAUSE0 因果结论', 'PERF-CAUSE0 cause findings') }}
          <span class="muted">({{ causes.length }})</span></h4>
        <div class="pf-verdicts">
          <span v-for="(n, v) in (store.perfCauseMeta?.verdict_counts ?? {})" :key="v" class="pf-vd" :data-verdict="v">
            {{ v }} {{ n }}
          </span>
          <span v-if="store.perfCauseMeta?.purity_verdict" class="pf-vd muted">
            purity: {{ store.perfCauseMeta.purity_verdict }}
          </span>
        </div>
        <div v-for="c in causes" :key="c.cause_id" class="pf-cause" :data-verdict="c.verdict">
          <div class="pfc-head">
            <b class="mono">{{ c.cause_id }}</b>
            <span class="pfc-cat">{{ c.category }}</span>
            <span class="pff-verdict" :data-verdict="c.verdict">{{ c.verdict }}</span>
          </div>
          <div class="pfc-hypo">{{ c.hypothesis }}</div>
          <div v-if="c.counterevidence" class="pfc-counter">✗ {{ c.counterevidence }}</div>
          <div class="pfc-spans">
            <button v-for="(s, i) in (c.exact_source_spans ?? []).slice(0, 2)" :key="i" type="button"
                    class="pf-span-btn mono" @click="openSpan(s)">
              {{ spanLabel(s) }}
            </button>
          </div>
        </div>
        <div class="pf-note">
          {{ L('因果结论来自隔离实验与逐位一致的复现，不来自相关性；PROPOSED 优化不得当作已实施。',
               'cause findings come from isolated experiments with bitwise-identical reproduction, not correlation; proposed optimizations are not implemented.') }}
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.pf { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--canvas-bg); font-size: 11px; }
.pf-ctx { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 5px 8px; background: var(--panel); border-bottom: 1px solid var(--border); }
.pf-title { font-weight: 700; }
.ctx-item { color: var(--text-muted); font-size: 10px; }
.ctx-item b { color: var(--text-primary); }
.pf-spacer { flex: 1; }
.pf-note { font-size: 9.5px; color: var(--truth-partial); max-width: 620px; line-height: 1.5; }
.pf-lane-hint {
  padding: 4px 8px; font-size: 10px; color: var(--amber-text);
  background: var(--amber-bg); border-bottom: 1px solid var(--sugg-border);
}
.pf-body { flex: 1; min-height: 0; overflow: auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1px; background: var(--border); }
.pf-col { background: var(--canvas-bg); padding: 6px 8px 12px; }
h4 { margin: 8px 0 4px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted); }
.pf-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.pf-table th { text-align: left; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border); padding: 1px 3px; }
.pf-table td { padding: 1px 3px; border-bottom: 1px solid var(--border); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pf-list { list-style: none; margin: 0; padding: 0; font-size: 10px; }
.pf-list li { display: flex; justify-content: space-between; padding: 1px 0; border-bottom: 1px dotted var(--border); }
.pf-maxwell { border: 1px solid var(--truth-observed); background: var(--truth-observed-bg); border-radius: 5px; padding: 5px 6px; margin-top: 6px; font-size: 10px; }
.pf-mx-head { font-weight: 700; color: var(--truth-observed); margin-bottom: 2px; }
.pf-finding { border: 1px solid var(--border); border-radius: 5px; padding: 5px 6px; margin-bottom: 6px; cursor: pointer; background: var(--surface); }
.pf-finding:hover { border-color: var(--accent); }
.pff-head { display: flex; gap: 6px; align-items: center; }
.pff-kind { font-size: 9px; color: var(--text-muted); }
.pff-line { margin: 2px 0; }
.pff-span { font-size: 9.5px; color: var(--accent); cursor: pointer; }
.pff-cause { display: flex; gap: 5px; margin-top: 3px; font-size: 9.5px; }
.pff-cid { color: var(--text-muted); }
.pff-verdict { font-weight: 700; font-size: 9px; }
.pff-verdict[data-verdict="SUPPORTED"] { color: var(--truth-complete); }
.pff-verdict[data-verdict="DISPROVED"] { color: var(--danger); }
.pff-verdict[data-verdict="PARTIAL"] { color: var(--truth-partial); }
.pff-verdict[data-verdict="UNKNOWN"] { color: var(--truth-unknown); }
.pff-hypo { color: var(--text-secondary); }
.pf-verdicts { display: flex; gap: 6px; flex-wrap: wrap; font-size: 10px; margin-bottom: 4px; }
.pf-vd { border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; }
.pf-cause { border-left: 2px solid var(--border-strong); padding-left: 6px; margin-bottom: 6px; }
.pf-cause[data-verdict="SUPPORTED"] { border-left-color: var(--truth-complete); }
.pf-cause[data-verdict="DISPROVED"] { border-left-color: var(--danger); }
.pf-cause[data-verdict="PARTIAL"] { border-left-color: var(--truth-partial); }
.pf-cause[data-verdict="UNKNOWN"] { border-left-color: var(--truth-unknown); }
.pfc-head { display: flex; gap: 6px; align-items: center; }
.pfc-cat { font-size: 9px; color: var(--text-muted); }
.pfc-hypo { font-size: 10px; margin: 1px 0; }
.pfc-counter { font-size: 9.5px; color: var(--danger); }
.pfc-spans { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 2px; }
.pf-span-btn { border: 1px solid var(--border); background: var(--surface); border-radius: 3px; font-size: 9px; padding: 0 4px; cursor: pointer; color: var(--accent); }
.mono { font-family: var(--mono); }
.muted { color: var(--text-muted); }
</style>
