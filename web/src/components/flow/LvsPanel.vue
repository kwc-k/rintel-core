<script setup lang="ts">
// SOFTWARE-LVS1 panel on the Software Circuit page (spec §22/§24).
// Shows MATCH / MISMATCH / STALE / UNBOUND / UNKNOWN grouped by
// Block / Port / Data / Call / Resource / Boundary / Control / Timing;
// clicking a diff selects the corresponding design block on the canvas
// (design object ↔ code topology ↔ source via the flow block binding).
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { apiPost } from '../../api/client'
import { useFlowStore } from '../../stores/flow'
import { useSourceStore } from '../../stores/source'

const props = defineProps<{ flowId: string; repoId: string | null }>()
const flow = useFlowStore()
const source = useSourceStore()
const { t } = useI18n()

const loading = ref(false)
const error = ref<string | null>(null)
const result = ref<any>(null)

const STATUS_ORDER = ['MISMATCH', 'STALE', 'UNBOUND', 'UNKNOWN', 'MATCH']
const STATUS_CLS: Record<string, string> = {
  mismatch: 'st-mismatch', stale: 'st-stale', unbound: 'st-unbound',
  unknown: 'st-unknown', match: 'st-match',
}

const SECTIONS: Array<{ key: string; label: string }> = [
  { key: 'block_diffs', label: 'Block' },
  { key: 'port_diffs', label: 'Port' },
  { key: 'call_diffs', label: 'Call' },
  { key: 'data_diffs', label: 'Data' },
  { key: 'state_diffs', label: 'State' },
  { key: 'resource_diffs', label: 'Resource' },
  { key: 'boundary_diffs', label: 'Boundary' },
  { key: 'control_diffs', label: 'Control' },
  { key: 'timing_diffs', label: 'Timing' },
]

const sections = computed(() =>
  SECTIONS.map((s) => ({ ...s, diffs: result.value?.[s.key] ?? [] }))
    .filter((s) => s.diffs.length > 0))

async function run(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    result.value = await apiPost(`/flows/${props.flowId}/lvs`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function onDiff(diff: any): void {
  // design object → select the corresponding canvas block
  if (diff.kind === 'block' && diff.design_object.startsWith('block:')) {
    const name = diff.design_object.slice('block:'.length)
    const block = Object.values(flow.blocksById)
      .find((b) => b.name === name) as any
    if (block) flow.selectBlock(block.id)
  }
  // code topology → open the source of the bound function
  if (diff.code_object && String(diff.code_object).startsWith('node:')) {
    const block = Object.values(flow.blocksById)
      .find((b) => (b as any).binding === diff.code_object) as any
    if (block?.symbol?.path && props.repoId) {
      void source.open(block.symbol.path, null, props.repoId)
    }
  }
}

onMounted(run)
</script>

<template>
  <div class="lvs-panel" data-testid="lvs-panel">
    <div class="lvs-head">
      <span class="lvs-title">{{ t('lvs.title') }}</span>
      <span v-if="result" class="lvs-overall" :class="STATUS_CLS[result.overall_status.toLowerCase()]"
        data-testid="lvs-overall">
        {{ result.overall_status }}
      </span>
      <span class="mono muted small">
        design {{ result?.design_snapshot ?? '–' }} · code
        {{ result?.code_snapshot ?? '–' }} · call cap {{ result?._meta?.call_capability ?? '–' }}
      </span>
      <button type="button" class="ghost" @click="run">{{ t('lvs.rerun') }}</button>
    </div>
    <div v-if="loading" class="muted small">{{ t('lvs.running') }}</div>
    <div v-else-if="error" class="lvs-note err">{{ error }}</div>
    <template v-else-if="result">
      <div class="lvs-summary">
        <span v-for="s in STATUS_ORDER" :key="s"
          class="chip" :class="STATUS_CLS[s.toLowerCase()]">
          {{ s }} {{ result.by_status?.[s] ?? 0 }}
        </span>
      </div>
      <div v-if="!sections.length" class="muted small">{{ t('lvs.noDiffs') }}</div>
      <div v-for="s in sections" :key="s.key" class="lvs-section"
        :data-testid="`lvs-section-${s.key}`">
        <div class="lvs-sec-title">{{ s.label }}</div>
        <div v-for="(d, i) in s.diffs" :key="i" class="lvs-diff"
          :class="STATUS_CLS[d.status.toLowerCase()]" @click="onDiff(d)">
          <span class="chip small" :class="STATUS_CLS[d.status.toLowerCase()]">{{ d.status }}</span>
          <span class="mono do">{{ d.design_object }}</span>
          <span v-if="d.code_object" class="mono co">→ {{ d.code_object }}</span>
          <div class="msg">{{ d.message }}</div>
          <div v-if="d.why" class="why muted">{{ d.why }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lvs-panel {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  max-height: 45%;
  z-index: 30;
  border-top: 1px solid var(--border-strong, #c9ced6);
  background: var(--panel);
  max-height: 320px;
  overflow: auto;
  padding: 8px 12px;
  font-size: 12px;
}
.lvs-head { display: flex; align-items: center; gap: 10px; }
.lvs-title { font-weight: 700; }
.lvs-overall { font-weight: 700; border-radius: 5px; padding: 1px 8px; }
.st-match { background: #16512e; color: #86efac; }
.st-mismatch { background: var(--danger-bg); color: var(--danger); }
.st-stale { background: var(--amber-bg); color: var(--amber-text); }
.st-unbound { background: #312e81; color: #a5b4fc; }
.st-unknown { background: var(--chip-bg); color: var(--chip-text); }
.chip {
  font-size: 10px; font-weight: 700; border-radius: 5px; padding: 1px 6px;
  background: var(--chip-bg); color: var(--chip-text);
}
.chip.small { font-size: 9px; }
.ghost { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 11px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.muted { color: var(--text-muted); }
.small { font-size: 11px; }
.lvs-summary { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.lvs-section { margin-bottom: 8px; }
.lvs-sec-title {
  font-weight: 700; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; color: var(--chip-text); margin-bottom: 4px;
}
.lvs-diff {
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-radius: 6px;
  padding: 4px 8px;
  margin-bottom: 4px;
  cursor: pointer;
}
.lvs-diff.st-mismatch { border-left-color: var(--danger); }
.lvs-diff.st-stale { border-left-color: var(--warn); }
.lvs-diff.st-unbound { border-left-color: var(--accent); }
.lvs-diff.st-unknown { border-left-color: var(--border-strong); }
.lvs-diff.st-match { border-left-color: var(--ok); }
.do, .co { font-size: 10px; word-break: break-all; }
.msg { margin-top: 2px; }
.why { font-size: 11px; margin-top: 2px; }
.lvs-note.err { color: var(--danger); background: var(--danger-bg); padding: 4px 8px; border-radius: 6px; }
</style>
