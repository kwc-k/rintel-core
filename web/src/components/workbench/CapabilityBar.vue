<script setup lang="ts">
// WORKBENCH §8-§9: per-relation-type capability bar.  Honest chip row:
// CALL COMPLETE / DATA PARTIAL / STATE UNKNOWN … derived ONLY from the
// frozen artifacts (domain/topo.ts relationCapabilities).  Clicking a
// PARTIAL/UNKNOWN chip opens the evidence-backed "why" drawer: analyzer
// lane, known limitation, affected relation, witness source.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTopoStore } from '../../stores/topo'
import { useLangStore } from '../../stores/lang'
import { relationCapabilities, type RelationCapability } from '../../domain/topo'

const { t } = useI18n()
const store = useTopoStore()
const lang = useLangStore()

const capabilities = computed<RelationCapability[]>(() =>
  store.bundle ? relationCapabilities(store.bundle) : [])

const whyKind = ref<string | null>(null)

const witnessesByKind = computed<Record<string, string[]>>(() => {
  const out: Record<string, string[]> = {}
  for (const e of store.bundle?.edges ?? []) {
    const kind = e.kind
    for (const w of e.representative_witnesses ?? []) {
      if (!out[kind]) out[kind] = []
      if (!out[kind].includes(w.provider)) out[kind].push(w.provider)
    }
  }
  return out
})

const whyCap = computed<RelationCapability | null>(() =>
  capabilities.value.find((c) => c.kind === whyKind.value) ?? null)

function chipLabel(c: RelationCapability): string {
  const gloss = lang.isZh ? `（${t(`wb.cap.levels.${c.capability}`)}）` : ''
  if (c.kind === 'DATA') {
    // keep the frozen-substring prefix 'DATA capability: PARTIAL'
    return `${t('wb.cap.dataPrefix')}${c.capability}${gloss}，${c.total} ${t('wb.cap.edges')}`
  }
  return `${c.kind} ${c.capability}${gloss} (${c.total})`
}

function toggleWhy(kind: string): void {
  whyKind.value = whyKind.value === kind ? null : kind
}
</script>

<template>
  <div class="cap-bar" data-testid="capability-bar">
    <div class="cap-row" data-testid="capability-chips">
      <span class="chip cap-title" data-testid="capability-title">{{ t('wb.cap.title') }}</span>
      <button
        v-for="c in capabilities" :key="c.kind"
        type="button"
        class="chip cap-chip"
        :class="`cap-${c.capability.toLowerCase()}`"
        :data-testid="`cap-${c.kind.toLowerCase()}`"
        :title="c.why.join('\n')"
        @click="toggleWhy(c.kind)"
      >
        {{ chipLabel(c) }}
        <span v-if="c.capability !== 'COMPLETE'" class="cap-why">ⓘ</span>
      </button>
      <span v-if="store.bundle?.meta.synthetic" class="chip synth">{{ t('wb.toolbar.synthLane') }}</span>
      <span class="languages mono">{{ store.bundle?.meta.languages.join(' · ') }}</span>
    </div>

    <div v-if="whyCap" class="cap-why" data-testid="capability-why">
      <div class="why-head">
        <span class="why-kind">{{ whyCap?.kind }} {{ whyCap?.capability
          }}{{ lang.isZh ? `（${t('wb.cap.levels.' + (whyCap?.capability ?? 'UNKNOWN'))}）` : '' }}</span>
        <span class="why-close" @click="whyKind = null">×</span>
      </div>
      <div class="why-grid">
        <div class="why-label">{{ t('wb.cap.why') }}</div>
        <div class="why-value">
          <div v-for="w in whyCap?.why ?? []" :key="w">• {{ w }}</div>
        </div>
        <div class="why-label">{{ t('wb.cap.lane') }}</div>
        <div class="why-value mono">{{ store.bundle?.meta.label }} ({{ store.lane }})</div>
        <div class="why-label">{{ t('wb.cap.languages') }}</div>
        <div class="why-value mono">{{ store.bundle?.meta.languages.join(', ') }}</div>
        <div class="why-label">{{ t('wb.cap.affected') }}</div>
        <div class="why-value">{{ whyCap?.total }} {{ t('wb.cap.edges') }} · {{ whyCap?.complete }} complete · {{ whyCap?.unknownCov }} coverage=UNKNOWN</div>
        <div class="why-label">{{ t('wb.cap.witnesses') }}</div>
        <div class="why-value mono">{{ witnessesByKind[whyCap?.kind ?? '']?.join(', ') || '—' }}</div>
      </div>
    </div>

    <div v-for="n in store.bundle?.meta.capability_notes ?? []" :key="n" class="cap-note">⚠ {{ n }}</div>
  </div>
</template>

<style scoped>
.cap-bar {
  background: var(--amber-bg);
  border-bottom: 1px solid var(--sugg-border);
  padding: 5px 12px;
  font-size: 11px;
}
.cap-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.chip {
  font-size: 10px;
  border-radius: 5px;
  padding: 1px 6px;
  font-weight: 600;
  background: var(--chip-bg);
  color: var(--chip-text);
}
.cap-title { background: var(--chip-bg); color: var(--chip-text); }
.cap-chip { cursor: pointer; border: 1px solid var(--border); }
.cap-chip:hover { border-color: var(--accent); }
.cap-ok { background: var(--ok-bg); color: var(--ok); }
.cap-partial { background: var(--amber-bg); color: var(--amber-text); }
.cap-unknown { background: var(--chip-bg); color: var(--text-muted); }
.cap-mixed { background: var(--amber-bg); color: var(--amber-text); }
.cap-why { font-weight: 700; }
.chip.synth { background: var(--danger-bg); color: var(--danger); }
.languages { font-size: 10px; color: var(--text-muted); }
.cap-why { border-top: 1px solid var(--sugg-border); margin-top: 4px; padding-top: 4px; }
.why-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.why-kind { font-weight: 700; color: var(--amber-text); font-size: 11px; }
.why-close { cursor: pointer; color: var(--text-muted); padding: 0 4px; }
.why-grid {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 2px 8px;
  font-size: 10.5px;
}
.why-label { color: var(--text-muted); }
.why-value { color: var(--text-primary); }
.cap-note { color: var(--amber-text); margin-top: 3px; }
</style>
