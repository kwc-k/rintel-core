<script setup lang="ts">
// Patch preview dialog (SPEC-P2 §14): shows the exact diff before apply.
import { computed } from 'vue'
import { useFlowStore } from '../../stores/flow'

const store = useFlowStore()
const emit = defineEmits<{ (e: 'close'): void }>()

const preview = computed(() => store.preview)

const diffLines = computed(() => (preview.value?.diff ?? '').split('\n'))

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'p-head'
  if (line.startsWith('+')) return 'p-add'
  if (line.startsWith('-')) return 'p-del'
  if (line.startsWith('@@')) return 'p-hunk'
  return 'p-ctx'
}

async function apply(): Promise<void> {
  const block = store.selectedBlock
  if (!block) return
  const code = store.draftFor(block.id, block.code ?? '')
  const r = await store.applyWriteback(block.id, code)
  if (r?.ok) emit('close')
}
</script>

<template>
  <div class="pp-overlay" @click.self="emit('close')">
    <div class="pp-dialog">
      <div class="pp-head">
        <span class="pp-title">Patch Preview</span>
        <span class="pp-target mono">{{ preview?.mode }} · {{ preview?.relpath }}</span>
        <button class="pp-close" @click="emit('close')">×</button>
      </div>
      <pre class="pp-diff mono"><code><span
        v-for="(l, i) in diffLines"
        :key="i"
        :class="lineClass(l)"
      >{{ l || ' ' }}<br /></span></code></pre>
      <div class="pp-actions">
        <span v-if="preview?.errors?.length" class="pp-error">{{ preview.errors.join('; ') }}</span>
        <button class="pp-btn" @click="emit('close')">Cancel</button>
        <button class="pp-btn primary" data-testid="pp-apply" :disabled="store.applying" @click="apply">
          {{ store.applying ? 'Applying…' : 'Apply Patch' }}
        </button>
      </div>
      <div class="pp-note">
        Apply writes the working tree, reindexes, and rebinds the block to the
        new canonical symbol. Failure keeps the block proposed/modified.
      </div>
    </div>
  </div>
</template>

<style scoped>
.pp-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.pp-dialog {
  width: min(760px, 90vw);
  max-height: 80vh;
  background: var(--panel);
  border-radius: 8px;
  box-shadow: 0 10px 40px rgba(15, 23, 42, 0.3);
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 8px;
}
.pp-head { display: flex; align-items: center; gap: 10px; }
.pp-title { font-weight: 600; font-size: 13px; }
.pp-target { font-size: 10px; color: var(--text-muted); flex: 1; }
.pp-close { border: none; background: none; font-size: 16px; cursor: pointer; }
.pp-diff {
  flex: 1;
  overflow: auto;
  margin: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px;
  font-size: 11px;
  line-height: 1.45;
}
.p-add { background: var(--ok-bg); color: var(--ok); }
.p-del { background: var(--danger-bg); color: var(--danger); }
.p-hunk { color: var(--violet-text); }
.p-head { color: var(--text-muted); }
.p-ctx { color: var(--text); }
.pp-actions { display: flex; justify-content: flex-end; gap: 8px; align-items: center; }
.pp-error { color: var(--danger); font-size: 11px; margin-right: auto; }
.pp-btn {
  border: 1px solid var(--border-strong);
  background: var(--panel);
  border-radius: 4px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
}
.pp-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.pp-note { font-size: 10px; color: var(--text-muted); }
</style>
