<script setup lang="ts">
// UI-REALITY-ALIGN0 §18/§19/§25: Agent activity dock.
//
// Only what is real is shown: external agents drove the MCP server and their
// runs were audited.  An agent's prose is a CLAIM; the tool calls and the audit
// verdict are the evidence — the panel says so instead of implying otherwise.
import { computed, onMounted, ref } from 'vue'
import { apiFetch } from '../../api/client'
import { useSelectionStore } from '../../stores/selection'
import { useLangStore } from '../../stores/lang'
import TruthBadge from '../ui/TruthBadge.vue'
import EmptyState from '../ui/EmptyState.vue'

interface AgentRun {
  stage: string; task_id: string; provider?: string | null; model?: string | null
  tool_calls?: number | null; turns?: number | null; tools_used?: string[]
  grep_fallbacks?: number | null; verdict?: string | null; trace?: string | null
  truth_class?: string; authority?: string; note?: string
}

const lang = useLangStore()
const selection = useSelectionStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const items = ref<AgentRun[]>([])
const transport = ref<string>('')
const error = ref<string | null>(null)
const expanded = ref<string | null>(null)

const byStage = computed(() => {
  const out: Record<string, AgentRun[]> = {}
  for (const it of items.value) (out[it.stage] ??= []).push(it)
  return Object.entries(out).sort((a, b) => a[0].localeCompare(b[0]))
})

const totals = computed(() => ({
  runs: items.value.length,
  calls: items.value.reduce((n, r) => n + (r.tool_calls ?? 0), 0),
  correct: items.value.filter((r) => (r.verdict ?? '').toUpperCase() === 'CORRECT').length,
  verdicts: items.value.filter((r) => r.verdict).length,
  grep: items.value.reduce((n, r) => n + (r.grep_fallbacks ?? 0), 0),
}))

onMounted(async () => {
  try {
    const d = await apiFetch<{ items: AgentRun[]; transport?: string }>('/agent-runs')
    items.value = d.items ?? []
    transport.value = d.transport ?? ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
})

function inspect(r: AgentRun): void {
  expanded.value = expanded.value === `${r.stage}:${r.task_id}` ? null : `${r.stage}:${r.task_id}`
  selection.set({
    kind: 'agent-task', id: `${r.stage}:${r.task_id}`, label: `${r.stage} · ${r.task_id}`,
    detail: `${r.tool_calls ?? '—'} tool calls · ${r.verdict ?? 'unaudited'}`,
    plane: 'OBSERVED', truth_class: 'OBSERVED',
    coverage: r.verdict ? 'COMPLETE' : 'UNKNOWN',
    authority: r.authority,
    payload: { agent: r },
  })
}
</script>

<template>
  <div class="ag" data-testid="agent-activity">
    <header class="ag-head">
      <span class="ag-title">{{ L('外部 Agent 活动', 'External agent activity') }}</span>
      <TruthBadge kind="truth" value="OBSERVED" compact />
      <span class="ag-transport mono">{{ transport || 'MCP' }}</span>
      <span class="ag-spacer"></span>
      <span class="ag-stat">{{ totals.runs }} runs · {{ totals.calls }} tool calls ·
        {{ totals.correct }}/{{ totals.verdicts }} audited CORRECT · grep fallbacks {{ totals.grep }}</span>
    </header>

    <EmptyState v-if="error" tone="unknown"
                :title="L('没有 Agent 记录', 'no agent records')"
                :reason="error"
                :hint="L('记录来自 analysis_tournament/*/agent_runs/ 的冻结产物。',
                        'records come from the frozen analysis_tournament/*/agent_runs/ artifacts.')" />
    <EmptyState v-else-if="!items.length" tone="unknown"
                :title="L('还没有 Agent 运行记录', 'no agent runs recorded yet')"
                :reason="L('外部 Agent 通过 MCP 使用 Rintel 时会留下 trace 与审计结论。',
                        'external agents using Rintel over MCP leave traces and audit verdicts.')" />

    <div v-else class="ag-body">
      <section v-for="[stage, runs] in byStage" :key="stage" class="ag-stage">
        <div class="ag-stage-head">{{ stage }} <span class="muted">({{ runs.length }})</span></div>
        <table class="ag-table">
          <thead><tr><th>task</th><th>model</th><th>tools</th><th>calls</th><th>verdict</th></tr></thead>
          <tbody>
            <template v-for="r in runs" :key="`${stage}:${r.task_id}`">
              <tr @click="inspect(r)">
                <td class="mono">{{ r.task_id }}</td>
                <td class="mono muted">{{ r.provider ?? r.model ?? '—' }}</td>
                <td class="mono muted">{{ (r.tools_used ?? []).join(', ') || '—' }}</td>
                <td class="num">{{ r.tool_calls ?? '—' }}</td>
                <td>
                  <TruthBadge v-if="r.verdict" kind="truth"
                              :value="(r.verdict ?? '').toUpperCase() === 'CORRECT' ? 'RESOLVED' : 'UNKNOWN'"
                              compact :suffix="r.verdict" />
                  <span v-else class="muted">{{ L('未审计', 'unaudited') }}</span>
                </td>
              </tr>
              <tr v-if="expanded === `${stage}:${r.task_id}`" class="ag-detail-row">
                <td colspan="5">
                  <div class="ag-detail">
                    <div>{{ L('Agent 的回答是主张（CLAIM），不是证据。', 'the agent answer is a CLAIM, not evidence.') }}</div>
                    <div class="mono">trace: {{ r.trace }}</div>
                    <div class="mono">authority: {{ r.authority }}</div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>
      <div class="ag-note">
        {{ L('Agent Host（DSH / Pi / Codex）尚未实现，属于 PLANNED，不出现在工作面。',
             'Agent hosts (DSH / Pi / Codex) are not implemented — PLANNED, not shown as available.') }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.ag { display: flex; flex-direction: column; height: 100%; min-height: 0; font-size: 11px; background: var(--surface); }
.ag-head { display: flex; gap: 6px; align-items: center; padding: 4px 8px; border-bottom: 1px solid var(--border); background: var(--panel); }
.ag-title { font-weight: 700; }
.ag-transport { font-size: 9.5px; color: var(--text-muted); }
.ag-spacer { flex: 1; }
.ag-stat { font-size: 10px; color: var(--text-muted); }
.ag-body { flex: 1; min-height: 0; overflow: auto; padding: 4px 8px 10px; }
.ag-stage-head { font-weight: 600; margin: 6px 0 2px; color: var(--text-secondary); }
.ag-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.ag-table th { text-align: left; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border); padding: 1px 3px; }
.ag-table td { padding: 1px 3px; border-bottom: 1px solid var(--border); }
.ag-table tbody tr:hover { background: var(--hover); cursor: pointer; }
.ag-detail-row:hover { background: none; cursor: default; }
.ag-detail { padding: 4px 6px; background: var(--canvas-bg); border-left: 2px solid var(--plane-probe); font-size: 10px; }
.ag-note { margin-top: 8px; font-size: 9.5px; color: var(--text-muted); }
.num { text-align: right; }
.mono { font-family: var(--mono); }
.muted { color: var(--text-muted); }
</style>
