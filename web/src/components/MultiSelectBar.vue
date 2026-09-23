<script setup lang="ts">
import { computed, ref } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
import { useArchStore } from '../stores/arch'
import CreateComponentDialog from './CreateComponentDialog.vue'

const workspace = useWorkspaceStore()
const arch = useArchStore()

const count = computed(() => workspace.selected.length)
const allEvidence = computed(() => count.value > 0 && workspace.selected.every((r) => r.plane === 'evidence'))

const dialogOpen = ref(false)

/** S4: mapping target = the focused arch component (if any). */
const mapTarget = computed(() => {
  const f = workspace.focus
  if (f && f.plane === 'arch' && f.entityType === 'component') {
    return arch.componentById(f.id)
  }
  return null
})
const canMapToComponent = computed(() => allEvidence.value && mapTarget.value !== null)

function mapToComponent() {
  const target = mapTarget.value
  if (!target) return
  arch.mapSelectionToComponent(target.id).catch(() => {})
}
</script>

<template>
  <div v-if="count > 0" class="multiselect-bar">
    <span class="muted">已选择 <strong>{{ count }}</strong> 个实体</span>
    <button type="button" class="ms-create" :disabled="!allEvidence" @click="dialogOpen = true">创建 Component</button>
    <span v-if="!allEvidence" class="hint muted">（仅 evidence 实体可创建 Component）</span>
    <button
      type="button"
      class="ms-map-to-component"
      :disabled="!canMapToComponent"
      :title="mapTarget ? '映射到当前选中的组件' : '先在 Architecture 中选中一个组件'"
      @click="mapToComponent"
    >
      映射到组件{{ mapTarget ? `「${mapTarget.name}」` : '' }}
    </button>
    <button type="button" class="ghost" @click="workspace.clearSelection()">清空</button>
    <CreateComponentDialog v-model:open="dialogOpen" />
  </div>
</template>

<style scoped>
.multiselect-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--accent-soft);
  border: 1px solid rgba(37, 99, 235, 0.3);
  border-radius: 6px;
  font-size: 12px;
}
.ms-create {
  font-weight: 600;
}
.ms-map-to-component {
  font-weight: 600;
}
.ms-map-to-component:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.hint {
  font-size: 11px;
}
.ghost {
  margin-left: auto;
}
</style>
