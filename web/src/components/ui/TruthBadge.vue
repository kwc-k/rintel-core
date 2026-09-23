<script setup lang="ts">
// §10: ONE badge for truth class / coverage / plane / span precision.
// Always glyph + text (+ tooltip); never colour-only.
import { computed } from 'vue'
import { useLangStore } from '../../stores/lang'
import { truthInfo, coverageInfo, planeInfo, precisionInfo } from '../../domain/truth'

const props = defineProps<{
  kind?: 'truth' | 'coverage' | 'plane' | 'precision'
  value?: string | null
  compact?: boolean
  suffix?: string
}>()
const lang = useLangStore()

const info = computed(() => {
  switch (props.kind ?? 'truth') {
    case 'coverage': return coverageInfo(props.value)
    case 'plane': return planeInfo(props.value)
    case 'precision': return precisionInfo(props.value as never)
    default: return truthInfo(props.value)
  }
})
const tone = computed(() => {
  const i: any = info.value
  return i.tone ?? String(props.value ?? 'unknown').toLowerCase()
})
const text = computed(() => {
  const i: any = info.value
  return lang.isZh ? i.zh : i.en
})
const glyph = computed(() => {
  const i: any = info.value
  return i.glyph ?? (props.kind === 'plane' ? '│' : '●')
})
const why = computed(() => (info.value as any).why ?? '')
</script>

<template>
  <span
    class="tb"
    :class="[`tone-${tone}`, { compact }]"
    :data-truth-kind="kind ?? 'truth'"
    :data-truth-value="(value ?? 'UNKNOWN').toUpperCase()"
    :title="`${text} — ${why}`"
  >
    <span class="tb-glyph" aria-hidden="true">{{ glyph }}</span>
    <span class="tb-text">{{ text }}{{ suffix ? ` ${suffix}` : '' }}</span>
  </span>
</template>

<style scoped>
.tb {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1.5;
  padding: 0 5px;
  border-radius: 4px;
  border: 1px solid transparent;
  white-space: nowrap;
}
.tb.compact { padding: 0 3px; font-size: 9.5px; }
.tb-glyph { font-size: 9px; }
.tone-observed { background: var(--truth-observed-bg); color: var(--truth-observed); border-color: var(--truth-observed); }
.tone-resolved { background: var(--truth-resolved-bg); color: var(--truth-resolved); border-color: var(--truth-resolved); }
.tone-derived { background: var(--truth-derived-bg); color: var(--truth-derived); border-color: var(--truth-derived); }
.tone-heuristic { background: var(--truth-heuristic-bg); color: var(--truth-heuristic); border-color: var(--truth-heuristic); }
.tone-complete { background: var(--truth-complete-bg); color: var(--truth-complete); border-color: var(--truth-complete); }
.tone-partial { background: var(--truth-partial-bg); color: var(--truth-partial); border-color: var(--truth-partial); }
.tone-unknown { background: var(--truth-unknown-bg); color: var(--truth-unknown); border-color: var(--truth-unknown); }
.tone-design { background: var(--danger-bg); color: var(--plane-design); border-color: var(--plane-design); }
.tone-annotation { background: var(--truth-derived-bg); color: var(--plane-annotation); border-color: var(--plane-annotation); }
.tone-probe { background: var(--truth-observed-bg); color: var(--plane-probe); border-color: var(--plane-probe); }
</style>
