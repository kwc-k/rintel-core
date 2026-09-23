<script setup lang="ts">
// SOFTWARE-DRC0 panel (spec §16/§17): severity counts, rule filter,
// findings list; click finding -> select topology object; click witness ->
// inspector/source.  Uncertainty is explained by the finding's `why` —
// the panel never renders red icons for UNKNOWN (D2/D9).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTopoStore } from '../../stores/topo'
import { repoPathOf } from '../../domain/topo'
import type { DrcFinding } from '../../api/topo'

const { t } = useI18n()
const store = useTopoStore()

const SEV_ORDER = ['ERROR', 'WARNING', 'INFO', 'UNKNOWN'] as const
const SEV_CLS: Record<string, string> = {
  ERROR: 'err', WARNING: 'warn', INFO: 'info', UNKNOWN: 'unk',
}

const findings = computed<DrcFinding[]>(() => {
  const all = store.drc?.findings ?? []
  return all.filter((f) => {
    if (store.drcRuleFilter && f.rule_id !== store.drcRuleFilter) return false
    if (store.drcSeverityFilter && f.severity !== store.drcSeverityFilter) {
      return false
    }
    return true
  })
})

const ruleOptions = computed<string[]>(() => {
  const set = new Set<string>()
  for (const f of store.drc?.findings ?? []) set.add(f.rule_id)
  return [...set].sort()
})

const counts = computed<Record<string, number>>(() => {
  const out: Record<string, number> = { ERROR: 0, WARNING: 0, INFO: 0, UNKNOWN: 0 }
  for (const f of store.drc?.findings ?? []) {
    out[f.severity] = (out[f.severity] ?? 0) + 1
  }
  return out
})

/** short human label for the subject (for the list row) */
function subjectLabel(f: DrcFinding): string {
  const s = f.subject_ids[0] ?? ''
  if (s.includes('suggestion') || s.startsWith('jpl-callreg') || s.startsWith('fac')) {
    return s.split(':').pop() ?? s
  }
  if (s.startsWith('node:')) {
    const parts = s.split(':')
    return parts[parts.length - 1] ?? s
  }
  return s
}

function onFindingClick(f: DrcFinding): void {
  const subject = f.subject_ids[0] ?? f.related_ids[0]
  if (!subject) return
  store.selectFindingSubject(subject)
}

function openWitness(f: DrcFinding, w: { fact_id: string; source?: { file?: string; line?: number | null } | null }): void {
  const m = store.bundle?.meta
  if (!m?.source_repo_id || !store.index) return
  const path = repoPathOf(w.source?.file, m)
    ?? (store.index.nodeById.get(f.subject_ids[0] ?? '')?.repo_path ?? null)
  if (!path) return
  store.openSource(m.source_repo_id, path, w.source?.line ?? null)
}
</script>

<template>
  <aside class="drc-panel" data-testid="drc-panel">
    <div class="drc-head">
      <span class="drc-title">{{ t('wb.toolbar.drc') }}</span>
      <span class="drc-lane mono">{{ store.drc?.lane ?? '' }}</span>
      <button type="button" class="ghost" @click="store.drcOpen = false">收起</button>
    </div>

    <div v-if="store.drcLoading" class="drc-note">{{ t('wb.drc.running') }}</div>
    <div v-else-if="store.drcError" class="drc-note err">{{ store.drcError }}</div>
    <template v-else-if="store.drc">
      <div class="drc-summary" data-testid="drc-summary">
        <span class="chip-sev" :class="SEV_CLS[sev]" v-for="sev in SEV_ORDER" :key="sev"
          :data-testid="`drc-count-${sev.toLowerCase()}`"
          @click="store.drcSeverityFilter = store.drcSeverityFilter === sev ? null : sev"
        >
          {{ sev }} {{ counts[sev] ?? 0 }}
        </span>
        <span class="drc-total">· {{ t('wb.drc.total') }} {{ store.drc.summary.total }}</span>
      </div>
      <div class="drc-filters">
        <select
          class="drc-filter" data-testid="drc-rule-filter"
          :value="store.drcRuleFilter ?? ''"
          @change="store.drcRuleFilter = (($event.target as HTMLSelectElement).value) || null"
        >
          <option value="">{{ t('wb.drc.allRules') }}</option>
          <option v-for="r in ruleOptions" :key="r" :value="r">{{ r }}</option>
        </select>
        <button
          v-if="store.drcRuleFilter || store.drcSeverityFilter"
          type="button" class="ghost" @click="store.drcRuleFilter = null; store.drcSeverityFilter = null"
        >{{ t('wb.drc.clear') }}</button>
      </div>
      <div class="drc-note" data-testid="drc-capability">
        {{ t('wb.drc.notPass', { cap: store.drc.data_capability }) }}
      </div>
      <div class="drc-list" data-testid="drc-findings">
        <div
          v-for="(f, i) in findings.slice(0, 300)" :key="i"
          class="drc-finding" :class="`sev-${f.severity.toLowerCase()}`"
          :data-testid="`drc-finding-${f.severity.toLowerCase()}`"
          @click="onFindingClick(f)"
        >
          <div class="df-head">
            <span class="chip-sev small" :class="SEV_CLS[f.severity]">{{ f.severity }}</span>
            <span class="df-rule mono">{{ f.rule_id }}</span>
            <span class="df-status">{{ f.status }}</span>
          </div>
          <div class="df-subject mono">▸ {{ subjectLabel(f) }}</div>
          <div class="df-msg">{{ f.message }}</div>
          <details class="df-why">
            <summary>{{ t('wb.drc.why') }}</summary>
            <div class="df-why-body">{{ f.why }}</div>
            <div v-if="f.truth_observed" class="mono df-observed">
              {{ t('wb.drc.observed') }} {{ JSON.stringify(f.truth_observed) }}
            </div>
            <div v-if="f.witnesses?.length" class="df-witnesses">
              <div v-for="(w, wi) in f.witnesses" :key="wi" class="mono df-witness">
                {{ w.fact_id }}
                <button
                  v-if="w.source?.file && store.bundle?.meta?.source_repo_id"
                  type="button" class="link"
                  @click.stop="openWitness(f, w)"
                >{{ t('wb.drc.source') }}</button>
              </div>
            </div>
            <div v-if="f.suggested_fix" class="df-fix">{{ t('wb.drc.fix') }} {{ f.suggested_fix }}</div>
          </details>
        </div>
        <div v-if="!findings.length" class="drc-note">{{ t('wb.drc.noFindings') }}</div>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.drc-panel {
  flex: 0 0 330px;
  width: 330px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: auto;
  padding: 10px;
  font-size: 12px;
  border-right: 1px solid var(--border);
  background: var(--panel);
}
.drc-head { display: flex; align-items: center; gap: 8px; }
.drc-title { font-weight: 700; }
.drc-lane { color: var(--text-muted); font-size: 11px; flex: 1; }
.ghost { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 11px; }
.drc-note {
  color: var(--amber-text);
  background: var(--amber-bg);
  border: 1px solid var(--sugg-border);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 11px;
}
.drc-note.err { color: var(--danger); background: var(--danger-bg); border-color: var(--danger); }
.drc-summary { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.drc-total { color: var(--text-muted); font-size: 11px; }
.chip-sev {
  font-size: 10px;
  font-weight: 700;
  border-radius: 5px;
  padding: 1px 6px;
  cursor: pointer;
  background: var(--chip-bg);
  color: var(--chip-text);
}
.chip-sev.small { cursor: default; }
.chip-sev.err { background: var(--danger-bg); color: var(--danger); }
.chip-sev.warn { background: var(--amber-bg); color: var(--amber-text); }
.chip-sev.info { background: #1e3a8a; color: #93c5fd; }
.chip-sev.unk { background: var(--chip-bg); color: var(--chip-text); }
.drc-filters { display: flex; gap: 6px; align-items: center; }
.drc-filter { font-size: 11px; padding: 2px 4px; border-radius: 6px; border: 1px solid var(--border-strong, #c9ced6); }
.drc-list { display: flex; flex-direction: column; gap: 6px; }
.drc-finding {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  cursor: pointer;
  background: var(--panel);
}
.drc-finding:hover { border-color: var(--border-strong); }
.drc-finding.sev-error { border-left: 3px solid #dc2626; }
.drc-finding.sev-warning { border-left: 3px solid #f59e0b; }
.drc-finding.sev-info { border-left: 3px solid #3b82f6; }
.drc-finding.sev-unknown { border-left: 3px solid #94a3b8; }
.df-head { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.df-rule { font-size: 10px; color: var(--chip-text); }
.df-status { font-size: 9px; color: var(--text-muted); }
.df-subject { font-size: 10px; color: var(--text); margin-top: 3px; word-break: break-all; }
.df-msg { font-size: 11px; margin-top: 2px; }
.df-why { margin-top: 3px; font-size: 11px; }
.df-why summary { cursor: pointer; color: var(--accent); }
.df-why-body { padding: 4px 0; color: var(--text-muted); }
.df-observed { font-size: 10px; color: var(--text-muted); word-break: break-all; }
.df-witnesses { margin-top: 3px; }
.df-witness { font-size: 10px; word-break: break-all; }
.df-fix { margin-top: 3px; color: var(--ok); font-size: 10px; }
.link { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 10px; padding: 0; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
