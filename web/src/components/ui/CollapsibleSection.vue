<script setup lang="ts">
// §9 progressive disclosure + §8 "no empty giant panel": the inspector is a
// stack of sections that collapse and that RENDER NOTHING when they have no
// facts (the parent decides, this component only shows/hides).
import { ref } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  count?: number | null
  defaultOpen?: boolean
  testid?: string
  hint?: string
}>(), { defaultOpen: true })
const open = ref(props.defaultOpen)
</script>

<template>
  <section class="insp-section" :data-testid="testid">
    <button type="button" class="is-head" :aria-expanded="open" @click="open = !open">
      <span class="is-caret" aria-hidden="true">{{ open ? '▾' : '▸' }}</span>
      <span class="is-title">{{ title }}</span>
      <span v-if="count !== null && count !== undefined" class="is-count">{{ count }}</span>
      <span v-if="hint" class="is-hint" :title="hint">ⓘ</span>
    </button>
    <div v-show="open" class="is-body"><slot /></div>
  </section>
</template>

<style scoped>
.insp-section { border-bottom: 1px solid var(--border); }
.is-head {
  display: flex; align-items: center; gap: 5px; width: 100%;
  background: none; border: none; cursor: pointer; padding: 5px 8px;
  color: var(--text-secondary); font-size: 10.5px; font-weight: 600;
  letter-spacing: 0.03em; text-transform: uppercase; text-align: left;
}
.is-head:hover { background: var(--hover); }
.is-caret { font-size: 9px; color: var(--text-muted); }
.is-title { flex: 1; }
.is-count { font-weight: 600; color: var(--text-muted); }
.is-hint { color: var(--truth-heuristic); }
.is-body { padding: 2px 8px 8px; font-size: 11px; }
</style>
