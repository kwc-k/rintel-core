<script setup lang="ts">
// Suggested-module details pane (TOPO-UI0 §10/§12): confidence, why[],
// boundary (members/inputs/outputs/state/resource/calls), uncertainty,
// declared-module relation, witnesses.  Never presented as canonical fact:
// truth_class stays SUGGESTED and data_capability is displayed verbatim.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTopoStore } from '../../stores/topo'
import type { Suggestion } from '../../domain/topo'

const props = defineProps<{ s: Suggestion }>()
const { t } = useI18n()
const store = useTopoStore()

const idx = computed(() => store.index)

function memberName(cid: string): string {
  const n = idx.value?.nodeById.get(cid)
  const name = n?.name ?? cid.split(':').pop() ?? cid
  const file = n?.file ?? ''
  return file ? `${name} (${file})` : name
}

function callRow(item: unknown): string {
  return typeof item === 'object' && item !== null
    ? String((item as any).to ?? (item as any).resource ?? '?')
    : String(item)
}

function callCount(item: unknown): string | null {
  if (typeof item === 'object' && item !== null && (item as any).count !== undefined) {
    return String((item as any).count)
  }
  return null
}

const boundaryRows = computed(() => {
  const b = props.s.boundary
  if (!b) return []
  return [
    { label: 'Inputs', items: b.data_in ?? [], hint: 'data_in' },
    { label: 'Outputs', items: b.data_out ?? [], hint: 'data_out' },
    { label: 'State crossings', items: b.state_inout ?? [], hint: 'state_inout' },
    { label: 'Resource ports', items: b.resource_ports ?? [], hint: 'resource_ports' },
    { label: 'Calls in', items: b.calls_in ?? [], hint: 'calls_in' },
    { label: 'Calls out', items: b.calls_out ?? [], hint: 'calls_out' },
  ]
})

const uncertainty = computed(() => {
  const hf = props.s.hard_features as any
  if (hf && typeof hf === 'object' && hf.uncertainty !== undefined) {
    return String(hf.uncertainty)
  }
  const sc = props.s.score_components as any
  const up = sc?.derived?.uncertainty_penalty ?? sc?.uncertainty_penalty
  return up !== undefined ? String(up) : 'UNKNOWN'
})

const members = computed(() => props.s.members ?? [])
</script>

<template>
  <div class="sugg-details" data-testid="suggested-details">
    <div class="ti-title small">{{ t('sugg.title') }}</div>
    <div class="ti-row">
      <span class="chip sugg">{{ t('sugg.badge') }}</span>
      <span v-if="s.confidence !== null && s.confidence !== undefined" class="chip ok" data-testid="sugg-confidence">
        {{ t('sugg.confidence') }} {{ s.confidence }}
      </span>
      <span v-if="s.score !== null && s.score !== undefined" class="chip" data-testid="sugg-score">
        MODULE-OPT {{ t('sugg.score') }} {{ s.score.toFixed(3) }}（{{ t('sugg.notFrozen') }}）
      </span>
    </div>
    <div class="ti-row">
      <span class="chip cap">DATA: {{ s.data_capability }}</span>
    </div>

    <div v-if="s.classification" class="ti-block">
      <div class="ti-title small">{{ t('sugg.declRelation') }}</div>
      <div class="ti-row">
        <b>{{ s.classification.declared_module_relation }}</b>
        <span v-if="s.classification.declared_module" class="mono muted"> {{ s.classification.declared_module }}</span>
      </div>
      <div v-if="s.classification.description" class="ti-note">{{ s.classification.description }}</div>
    </div>

    <div class="ti-block">
      <div class="ti-title small">{{ t('sugg.members') }} ({{ members.length }})</div>
      <div v-for="m in members.slice(0, 120)" :key="m" class="mono muted ti-row member">
        {{ memberName(m) }}
      </div>
      <div v-if="members.length > 120" class="muted">… {{ t('sugg.more') }} {{ members.length - 120 }}</div>
    </div>

    <div class="ti-block">
      <div class="ti-title small">{{ t('sugg.boundary') }}</div>
      <div v-for="row in boundaryRows" :key="row.hint" class="ti-row">
        <b>{{ row.label }}:</b>
        <span class="muted"> {{ row.items.length ? row.items.length : '0' }}</span>
        <div v-if="row.items.length" class="mono muted bd-list">
          <div v-for="(item, i) in row.items.slice(0, 24)" :key="i">
            {{ callRow(item) }}<span v-if="callCount(item)"> ×{{ callCount(item) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="ti-block">
      <div class="ti-title small">{{ t('sugg.why') }}</div>
      <div v-for="(w, i) in s.why" :key="i" class="ti-row mono">• {{ w }}</div>
      <div v-if="s.interface_diagnostics" class="mono muted ti-row">
        {{ t('sugg.interface') }}: {{ JSON.stringify(s.interface_diagnostics) }}
      </div>
    </div>

    <div class="ti-block">
      <div class="ti-title small">{{ t('sugg.uncertainty') }}</div>
      <div class="ti-row mono">{{ uncertainty }}</div>
      <div v-if="s.rejection_reasons?.length" class="ti-note warn">
        {{ t('sugg.rejection') }}: {{ s.rejection_reasons.join(', ') }}
      </div>
    </div>

    <div v-if="s.witnesses?.length" class="ti-block">
      <div class="ti-title small">{{ t('sugg.witnesses') }}</div>
      <div v-for="(w, i) in s.witnesses.slice(0, 3)" :key="i" class="mono muted ti-row">
        {{ Object.entries(w).map(([k, v]) => `${k}: [${(Array.isArray(v) ? v : [v]).length}]`).join(' · ') }}
      </div>
    </div>

    <div v-if="s.legal && s.legal.length" class="ti-block">
      <div class="ti-title small">{{ t('sugg.legality') }}</div>
      <div v-for="(l, i) in s.legal" :key="i" class="mono muted ti-row">{{ JSON.stringify(l) }}</div>
    </div>
  </div>
</template>

<style scoped>
.sugg-details .ti-block {
  margin-top: 8px;
}
.member {
  font-size: 10px;
  word-break: break-all;
}
.bd-list {
  font-size: 10px;
  margin-top: 2px;
  padding-left: 10px;
}
.ti-title.small { font-size: 11px; color: var(--text); font-weight: 700; margin: 4px 0; }
.ti-row { margin-bottom: 3px; }
.ti-note {
  color: var(--amber-text);
  background: var(--sugg-bg);
  border: 1px solid var(--sugg-border);
  border-radius: 6px;
  padding: 5px 7px;
  margin-top: 5px;
  font-size: 11px;
}
.ti-note.warn { color: var(--danger); background: var(--danger-bg); border-color: var(--danger); }
.chip {
  font-size: 10px;
  border-radius: 5px;
  padding: 1px 6px;
  background: var(--chip-bg);
  color: var(--chip-text);
  font-weight: 600;
  display: inline-block;
}
.chip.sugg { background: var(--amber-bg); color: var(--amber-text); }
.chip.ok { background: var(--ok-bg); color: var(--ok); }
.chip.cap { background: var(--amber-bg); color: var(--amber-text); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.muted { color: var(--text-muted); }
</style>
