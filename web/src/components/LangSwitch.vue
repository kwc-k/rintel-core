<script setup lang="ts">
// TOPO-EDITOR-UX0 §13: global language switcher (跟随系统 / 中文 / English).
import { useLangStore } from '../stores/lang'
import type { LangMode } from '../lib/lang'

const lang = useLangStore()
const MODES: Array<{ value: LangMode; label: string }> = [
  { value: 'system', label: '跟随系统' },
  { value: 'zh-CN', label: '中文' },
  { value: 'en-US', label: 'English' },
]
</script>

<template>
  <div class="lang-switch" data-testid="lang-switch" role="group" aria-label="Language">
    <button
      v-for="m in MODES" :key="m.value"
      type="button"
      class="ls-btn"
      :class="{ active: lang.mode === m.value }"
      :data-testid="`lang-${m.value}`"
      :aria-pressed="lang.mode === m.value"
      @click="lang.setMode(m.value)"
    >
      {{ m.label }}
    </button>
  </div>
</template>

<style scoped>
.lang-switch {
  display: inline-flex;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  overflow: hidden;
  background: var(--panel);
  flex-shrink: 0;
}
.ls-btn {
  border: none;
  border-right: 1px solid var(--border);
  border-radius: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  padding: 3px 8px;
}
.ls-btn:last-child { border-right: none; }
.ls-btn.active {
  background: var(--accent);
  color: #fff;
}
.ls-btn:hover:not(:disabled):not(.active) {
  background: var(--hover);
}
</style>
