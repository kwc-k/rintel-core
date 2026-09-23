<script setup lang="ts">
// SYNTHESIS0 plan/preview/apply panel (spec §23/§24) — three explicit steps,
// never one-click code writes.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiPost } from '../../api/client'

const props = defineProps<{ flowId: string }>()
const { t } = useI18n()

const busy = ref(false)
const error = ref<string | null>(null)
const plan = ref<any>(null)
const preview = ref<any>(null)
const patchDiff = ref('')
const result = ref<any>(null)

const canPreview = computed(() => !!plan.value)
const canApply = computed(() => !!preview.value)

async function run(mode: 'plan' | 'preview' | 'apply'): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const res = await apiPost(`/flows/${props.flowId}/synthesis/${mode}`)
    if (mode === 'plan') {
      plan.value = res
      preview.value = null
      patchDiff.value = ''
      result.value = null
    } else if (mode === 'preview') {
      preview.value = res
      result.value = null
    } else {
      result.value = res
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="synth-panel" data-testid="synth-panel">
    <div class="synth-head">
      <span class="synth-title">{{ t('synth.genPlan') }}</span>
      <span v-if="plan?.plan?.status" class="synth-status" data-testid="synth-status">
        {{ plan.plan.status }}
      </span>
      <span class="synth-steps">
        <button type="button" class="ghost" :disabled="busy"
          @click="run('plan')" data-testid="synth-plan">1 · {{ t('synth.stepPlan') }}</button>
        <button type="button" class="ghost" :disabled="busy || !canPreview"
          @click="run('preview')" data-testid="synth-preview">2 · {{ t('synth.stepPreview') }}</button>
        <button type="button" class="ghost" :disabled="busy || !canApply"
          @click="run('apply')" data-testid="synth-apply">3 · {{ t('synth.stepApply') }}</button>
      </span>
    </div>

    <div v-if="busy" class="muted small">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="synth-note err">{{ error }}</div>

    <template v-if="plan?.plan">
      <div class="synth-section">
        <div class="synth-sec-title">{{ t('synth.designChanges') }}</div>
        <div v-for="c in plan.plan.changes" :key="c.change_id"
          class="synth-change" :data-testid="`synth-change-${c.change_id}`">
          <b>{{ c.change_id }} · {{ c.synthesis_type }} · {{ c.design_diff.kind }}</b>
          <span class="mono"> {{ c.design_diff.design_object }}</span>
          <span v-if="!c.supported" class="chip plan-only">PLAN_ONLY</span>
          <div class="muted small">
            files: {{ (c.affected_files || []).join(', ') || '—' }} ·
            symbols: {{ c.affected_symbols?.length ?? 0 }} ·
            coverage: {{ c.uncertainty?.coverage ?? '?' }}
          </div>
          <div v-if="c.uncertainty?.detail?.length" class="muted small">
            ⚠ {{ c.uncertainty.detail[0] }}
          </div>
        </div>
      </div>
      <div class="synth-section">
        <div class="synth-sec-title">{{ t('synth.risk') }}</div>
        <div v-for="(r, i) in plan.plan.risk_summary" :key="i" class="muted small">• {{ r }}</div>
        <div v-if="!plan.plan.risk_summary.length" class="muted small">{{ t('synth.noRisk') }}</div>
      </div>
      <div class="synth-section">
        <div class="synth-sec-title">{{ t('synth.expectedLvs') }}</div>
        <div v-for="(e, i) in plan.plan.expected_lvs_delta" :key="i"
          class="muted small">• {{ e }}</div>
      </div>
    </template>

    <template v-if="preview">
      <div class="synth-section" data-testid="synth-preview-section">
        <div class="synth-sec-title">{{ t('synth.patchPreview') }}</div>
        <div class="muted small">
          files {{ preview.preview.files_changed }} ·
          +{{ preview.preview.lines_added }} −{{ preview.preview.lines_removed }}
        </div>
        <pre class="synth-diff mono">{{ patchDiff }}</pre>
      </div>
    </template>

    <template v-if="result?.result">
      <div class="synth-section" data-testid="synth-result-section">
        <div class="synth-sec-title">{{ t('synth.applyResult') }}</div>
        <div class="synth-status final">{{ result.result.status }}</div>
        <div class="muted small">
          reindex snapshot: {{ result.result.apply_result?.new_snapshot ?? '—' }}
        </div>
        <div class="muted small">
          DRC errors: {{ result.result.post_drc?.errors_total ?? '?' }}
          (new: {{ (result.result.post_drc?.new_errors || []).length }})
        </div>
        <div class="muted small">
          LVS: {{ result.result.post_lvs?.overall ?? '—' }}
          {{ JSON.stringify(result.result.post_lvs?.by_status ?? {}) }}
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.synth-panel {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  max-height: 45%;
  z-index: 30;
  border-top: 1px solid var(--border-strong, #c9ced6);
  background: var(--panel);
  max-height: 340px;
  overflow: auto;
  padding: 8px 12px;
  font-size: 12px;
}
.synth-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.synth-title { font-weight: 700; }
.synth-steps { display: inline-flex; gap: 6px; }
.ghost { background: none; border: 1px solid var(--border-strong, #c9ced6); border-radius: 6px; cursor: pointer; font-size: 11px; padding: 2px 8px; }
.ghost:disabled { opacity: 0.45; cursor: default; }
.synth-status {
  font-weight: 700; border-radius: 5px; padding: 1px 8px;
  background: var(--chip-bg); color: var(--chip-text);
}
.synth-status.final { background: #16512e; color: #86efac; }
.synth-note.err { color: var(--danger); background: var(--danger-bg); padding: 4px 8px; border-radius: 6px; }
.synth-section { margin-top: 8px; }
.synth-sec-title {
  font-weight: 700; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; color: var(--chip-text); margin-bottom: 4px;
}
.synth-change {
  border: 1px solid var(--border); border-radius: 6px;
  padding: 4px 8px; margin-bottom: 4px;
}
.chip.plan-only { background: var(--amber-bg); color: var(--amber-text); font-size: 9px; border-radius: 4px; padding: 0 4px; }
.synth-diff { font-size: 10px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 6px; max-height: 220px; overflow: auto; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.muted { color: var(--text-muted); }
.small { font-size: 11px; }
</style>
