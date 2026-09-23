<script setup lang="ts">
// §27/§28: empty states must say WHAT is missing and WHY — never a bare 0.
// `reason` names the missing authority; `action` offers the real next step.
import { useLangStore } from '../../stores/lang'

const props = defineProps<{
  title?: string
  reason?: string
  hint?: string
  tone?: 'unknown' | 'partial' | 'empty'
  testid?: string
}>()
const lang = useLangStore()
const glyph = props.tone === 'partial' ? '◐' : props.tone === 'unknown' ? '?' : '○'
const zhTitle: Record<string, string> = {
  unknown: '未知 —— 没有足够证据', partial: '部分覆盖', empty: '此处暂无内容',
}
</script>

<template>
  <div class="empty-state" :class="`es-${tone ?? 'empty'}`" :data-testid="testid ?? 'empty-state'">
    <div class="es-head"><span class="es-glyph">{{ glyph }}</span>
      <span class="es-title">{{ title ?? (lang.isZh ? zhTitle[tone ?? 'empty'] : 'Nothing here yet') }}</span>
    </div>
    <div v-if="reason" class="es-reason">{{ reason }}</div>
    <div v-if="hint" class="es-hint">{{ hint }}</div>
    <slot />
  </div>
</template>

<style scoped>
.empty-state {
  border: 1px dashed var(--border-strong);
  border-radius: 6px;
  padding: 8px 10px;
  margin: 6px;
  background: var(--surface);
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.6;
}
.es-head { display: flex; gap: 6px; align-items: baseline; font-weight: 600; color: var(--text-secondary); }
.es-glyph { font-size: 11px; }
.es-unknown .es-glyph { color: var(--truth-unknown); }
.es-partial { border-color: var(--truth-partial); background: var(--truth-partial-bg); }
.es-partial .es-head, .es-partial .es-reason { color: var(--amber-text); }
.es-reason { margin-top: 3px; }
.es-hint { margin-top: 3px; opacity: 0.85; }
</style>
