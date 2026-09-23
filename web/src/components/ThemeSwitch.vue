<script setup lang="ts">
// THEME0 switcher: Appearance System / Light / Dark (segmented).
import { useThemeStore } from '../stores/theme'
import type { ThemeMode } from '../lib/theme'

const theme = useThemeStore()
const MODES: Array<{ value: ThemeMode; label: string }> = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]
</script>

<template>
  <div class="theme-switch" data-testid="theme-switch" role="group" aria-label="Appearance">
    <button
      v-for="m in MODES" :key="m.value"
      type="button"
      class="ts-btn"
      :class="{ active: theme.mode === m.value }"
      :data-testid="`theme-${m.value}`"
      :aria-pressed="theme.mode === m.value"
      @click="theme.setMode(m.value)"
    >
      {{ m.label }}
    </button>
  </div>
</template>

<style scoped>
.theme-switch {
  display: inline-flex;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  overflow: hidden;
  background: var(--panel);
  flex-shrink: 0;
}
.ts-btn {
  border: none;
  border-right: 1px solid var(--border);
  border-radius: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  padding: 3px 8px;
}
.ts-btn:last-child { border-right: none; }
.ts-btn.active {
  background: var(--accent);
  color: #fff;
}
.ts-btn:hover:not(:disabled):not(.active) {
  background: var(--hover);
}
</style>
