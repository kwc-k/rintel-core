<script setup lang="ts">
// UI-REALITY-ALIGN0 §3/§16/§17/§18/§30 (UR1/UR10): Product Reality dialog.
//
// The UI states what it really is: each feature carries status
// AVAILABLE / PARTIAL / PLANNED plus its backend authority, truth boundary,
// coverage and the action it actually performs.  PLANNED items appear here
// (Roadmap) and NOWHERE on the main work surface.
import { computed, onMounted, ref } from 'vue'
import { apiFetch } from '../../api/client'
import { useLangStore } from '../../stores/lang'
import TruthBadge from '../ui/TruthBadge.vue'

interface LedgerFeature {
  feature_id: string
  ui_location: string
  status: 'AVAILABLE' | 'PARTIAL' | 'PLANNED'
  backend_authority: string
  data_source: string
  truth_boundary: string
  coverage: string
  current_action: string
  notes?: string
}
interface Ledger { stage: string; revision: string; features: LedgerFeature[]; summary?: Record<string, number> }

const emit = defineEmits<{ (e: 'close'): void }>()
const lang = useLangStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const ledger = ref<Ledger | null>(null)
const screenAudit = ref<Record<string, any> | null>(null)
const error = ref<string | null>(null)
const filter = ref<'all' | 'AVAILABLE' | 'PARTIAL' | 'PLANNED'>('all')

const features = computed(() => (ledger.value?.features ?? [])
  .filter((f) => filter.value === 'all' || f.status === filter.value))
const summary = computed(() => ledger.value?.summary ?? {})

onMounted(async () => {
  try {
    const d = await apiFetch<Record<string, any>>('/ui-reality')
    ledger.value = d.reality_ledger as Ledger
    screenAudit.value = d.screen_audit ?? null
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
})
</script>

<template>
  <div class="rp-backdrop" data-testid="reality-panel" @click.self="emit('close')">
    <div class="rp">
      <header class="rp-head">
        <b>{{ L('产品现实账本', 'Product reality ledger') }}</b>
        <span class="mono muted" v-if="ledger">{{ ledger.stage }} · rev {{ ledger.revision }}</span>
        <span class="rp-spacer"></span>
        <span class="rp-counts">
          <span class="rc ok">{{ summary.AVAILABLE ?? 0 }} AVAILABLE</span>
          <span class="rc part">{{ summary.PARTIAL ?? 0 }} PARTIAL</span>
          <span class="rc planned">{{ summary.PLANNED ?? 0 }} PLANNED</span>
        </span>
        <button type="button" class="rp-x" data-testid="reality-close" @click="emit('close')">×</button>
      </header>

      <nav class="rp-filters">
        <button v-for="f in (['all', 'AVAILABLE', 'PARTIAL', 'PLANNED'] as const)" :key="f"
                type="button" class="rp-filter" :class="{ on: filter === f }"
                :data-testid="`reality-filter-${f.toLowerCase()}`" @click="filter = f">{{ f }}</button>
        <span class="rp-spacer"></span>
        <span class="rp-hint">{{ L('PLANNED 永不出现在主工作面（§3）', 'PLANNED never appears on the main surface (§3)') }}</span>
      </nav>

      <div v-if="error" class="rp-err">
        {{ L('现实账本不可用', 'reality ledger unavailable') }}: {{ error }}
      </div>

      <div v-else class="rp-body">
        <table class="rp-table" data-testid="reality-table">
          <thead>
            <tr>
              <th>feature</th><th>where</th><th>status</th><th>authority</th>
              <th>truth boundary</th><th>coverage</th><th>{{ L('点击后真正能做什么', 'what it really does') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in features" :key="f.feature_id" :data-feature="f.feature_id" :data-status="f.status">
              <td class="mono">{{ f.feature_id }}</td>
              <td class="mono muted">{{ f.ui_location }}</td>
              <td><TruthBadge kind="coverage" :value="f.status === 'AVAILABLE' ? 'COMPLETE' : f.status === 'PARTIAL' ? 'PARTIAL' : 'UNKNOWN'" compact
                              :suffix="f.status" /></td>
              <td class="mono">{{ f.backend_authority }}</td>
              <td>{{ f.truth_boundary }}</td>
              <td>{{ f.coverage }}</td>
              <td>{{ f.current_action }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="screenAudit" class="rp-screens">
          <b>{{ L('屏幕审计', 'Screen audit') }}:</b>
          <span v-for="(v, k) in (screenAudit.screens ?? {})" :key="k" class="rp-screen mono">
            {{ k }}: {{ (v as any).verdicts?.join('/') }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rp-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 300; }
.rp { background: var(--panel); border: 1px solid var(--border-strong); border-radius: 8px; width: min(1180px, 94vw); max-height: 86vh; display: flex; flex-direction: column; box-shadow: 0 18px 48px rgba(0,0,0,0.3); }
.rp-head { display: flex; gap: 8px; align-items: center; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }
.rp-spacer { flex: 1; }
.rp-counts { display: flex; gap: 6px; font-size: 10px; }
.rc { border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; }
.rc.ok { color: var(--truth-complete); border-color: var(--truth-complete); }
.rc.part { color: var(--truth-partial); border-color: var(--truth-partial); }
.rc.planned { color: var(--text-muted); }
.rp-x { border: none; background: none; font-size: 16px; cursor: pointer; color: var(--text-muted); }
.rp-filters { display: flex; gap: 4px; align-items: center; padding: 5px 12px; border-bottom: 1px solid var(--border); }
.rp-filter { border: 1px solid var(--border); background: var(--surface); border-radius: 4px; font-size: 10.5px; padding: 1px 8px; cursor: pointer; }
.rp-filter.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
.rp-hint { font-size: 9.5px; color: var(--text-muted); }
.rp-body { overflow: auto; padding: 0 2px 10px; }
.rp-table { width: 100%; border-collapse: collapse; font-size: 10.5px; }
.rp-table th { position: sticky; top: 0; background: var(--panel); text-align: left; font-weight: 600; border-bottom: 1px solid var(--border); padding: 4px 6px; color: var(--text-muted); }
.rp-table td { border-bottom: 1px solid var(--border); padding: 3px 6px; vertical-align: top; }
.rp-table tr[data-status="PLANNED"] { opacity: 0.65; }
.rp-err { padding: 14px; color: var(--danger); }
.rp-screens { padding: 8px 10px; display: flex; flex-wrap: wrap; gap: 8px; font-size: 10px; }
.rp-screen { border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; color: var(--text-muted); }
.mono { font-family: var(--mono); }
.muted { color: var(--text-muted); }
</style>
