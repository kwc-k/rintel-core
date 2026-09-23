<script setup lang="ts">
// TOPO-EDITOR-UX0 §1/§20: 新建拓扑 dialog — blank / from repo / from
// function / from suggested module.  All create pure TO-BE design.
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFlowStore } from '../stores/flow'
import { useWorkspaceStore } from '../stores/workspace'
import { isApiError } from '../api/client'

const props = defineProps<{ repoId?: string | null; defaultName?: string | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', flowId: string): void
}>()

const { t } = useI18n()
const flow = useFlowStore()
const workspace = useWorkspaceStore()

const KIND = 'blank' as 'blank' | 'repo' | 'function' | 'suggestion'
const entry = ref<typeof KIND | 'repo' | 'function' | 'suggestion'>('blank')
const name = ref(props.defaultName ?? '')
const creating = ref(false)
const error = ref('')
const fallbackRepo = ref('')

const repoId = computed(() => props.repoId ?? workspace.repoId ?? fallbackRepo.value)

onMounted(async () => {
  if (repoId.value) return
  // lane may still be loading → fall back to the repo list (first indexed repo)
  try {
    const r = await fetch('/api/v1/repos')
    if (r.ok) {
      const data = await r.json() as { items?: Array<{ id: string }> }
      if (data.items?.length) fallbackRepo.value = data.items[0].id
    }
  } catch { /* best effort */ }
})
const entries: Array<{
  key: typeof KIND | 'repo' | 'function' | 'suggestion'
  title: string
  hint: string
}> = [
  { key: 'blank', title: t('topbar.blank'), hint: t('topbar.blankHint') },
  { key: 'repo', title: t('topbar.fromRepo'), hint: t('topbar.fromRepoHint') },
  { key: 'function', title: t('topbar.fromFunction'), hint: t('topbar.fromFunctionHint') },
  { key: 'suggestion', title: t('topbar.fromSuggestedModule'), hint: t('topbar.fromSuggestedModuleHint') },
]

async function create(): Promise<void> {
  if (!repoId.value) { error.value = t('newTopology.noRepo'); return }
  creating.value = true
  error.value = ''
  try {
    const flowId = await flow.createBlank({
      repoId: repoId.value,
      name: name.value.trim() || 'Untitled Topology',
    })
    emit('created', flowId)
  } catch (err) {
    error.value = isApiError(err) ? err.message : String(err)
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div class="nt-overlay" @click.self="emit('close')">
    <div class="nt-dialog" role="dialog" :aria-label="t('newTopology.title')" data-testid="new-topology-dialog">
      <header class="nt-head">{{ t('newTopology.title') }}</header>
      <div class="nt-entries">
        <label v-for="e in entries" :key="e.key" class="nt-entry" :class="{ on: entry === e.key }">
          <input type="radio" :value="e.key" v-model="entry" :data-testid="`nt-${e.key}`" />
          <div class="nt-entry-body">
            <div class="nt-entry-title">{{ e.title }}</div>
            <div class="nt-entry-hint">{{ e.hint }}</div>
          </div>
        </label>
      </div>
      <div class="nt-form">
        <input
          v-model="name"
          class="nt-input"
          :placeholder="t('newTopology.namePlaceholder')"
          :aria-label="t('newTopology.name')"
          data-testid="nt-name"
          @keydown.enter="create"
        />
        <button class="nt-btn primary" :disabled="creating" data-testid="nt-create" @click="create">
          {{ creating ? t('newTopology.creating') : t('newTopology.create') }}
        </button>
      </div>
      <div v-if="error" class="nt-error">{{ error }}</div>
      <button class="nt-btn" @click="emit('close')">{{ t('common.cancel') }}</button>
    </div>
  </div>
</template>

<style scoped>
.nt-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 900;
  display: flex;
  align-items: center;
  justify-content: center;
}
.nt-dialog {
  width: 420px;
  background: var(--panel-bg);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  color: var(--text-primary);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.25);
}
.nt-head { font-weight: 700; font-size: 14px; }
.nt-entries { display: flex; flex-direction: column; gap: 6px; }
.nt-entry {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
  cursor: pointer;
}
.nt-entry.on { border-color: var(--accent); background: var(--accent-soft); }
.nt-entry-body { display: flex; flex-direction: column; gap: 2px; }
.nt-entry-title { font-weight: 600; font-size: 12px; }
.nt-entry-hint { font-size: 10px; color: var(--text-secondary); }
.nt-form { display: flex; gap: 6px; }
.nt-input {
  flex: 1;
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  padding: 5px 8px;
  font-size: 12px;
  background: var(--panel-bg);
  color: var(--text-primary);
}
.nt-btn {
  border: 1px solid var(--border-strong);
  background: var(--panel-bg);
  color: var(--text-primary);
  border-radius: 5px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
}
.nt-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.nt-btn:disabled { opacity: 0.5; }
.nt-error { color: var(--status-error); font-size: 11px; }
</style>
