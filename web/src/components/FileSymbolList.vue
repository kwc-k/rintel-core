<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRepoStore } from '../stores/repo'
import { useWorkspaceStore } from '../stores/workspace'
import { useSourceStore } from '../stores/source'
import type { EntityRef } from '../domain/workspace'
import { sameRef } from '../domain/workspace'
import type { SymbolItem } from '../api/evidence'

const props = defineProps<{ repoId: string; path: string }>()

const repo = useRepoStore()
const workspace = useWorkspaceStore()
const source = useSourceStore()

const symbols = ref<SymbolItem[]>([])
const loading = ref(false)
const anchor = ref<EntityRef | null>(null)

function refFor(s: SymbolItem): EntityRef {
  return { plane: 'evidence', entityType: 'node', id: s.id }
}

async function load() {
  loading.value = true
  try {
    const res = await repo.getSymbols(props.repoId, props.path)
    symbols.value = res.items
  } catch {
    symbols.value = []
  } finally {
    loading.value = false
  }
}

watch(() => [props.repoId, props.path], load, { immediate: true })

function onClick(e: MouseEvent, s: SymbolItem) {
  const ref = refFor(s)
  if (e.shiftKey && anchor.value) {
    const idx = symbols.value.findIndex((x) => x.id === s.id)
    if (idx >= 0) {
      workspace.selectRange(anchor.value, symbols.value.slice(0, idx + 1).map(refFor))
    }
  } else if (e.ctrlKey || e.metaKey) {
    workspace.toggleSelect(ref)
    anchor.value = ref
  } else {
    workspace.setFocus(ref)
    anchor.value = ref
    source.open(s.path, s.line ?? null, props.repoId, workspace.snapshotId ?? undefined)
  }
}

function onCheckbox(s: SymbolItem) {
  const ref = refFor(s)
  workspace.toggleSelect(ref)
  anchor.value = ref
}
</script>

<template>
  <div class="symbol-list">
    <div v-if="loading" class="muted small">加载符号…</div>
    <div v-else-if="symbols.length === 0" class="muted small">无符号</div>
    <template v-else>
      <div
        v-for="s in symbols"
        :key="s.id"
        class="row symbol-row"
        :class="{ selected: workspace.isSelected(refFor(s)), focused: sameRef(workspace.focus, refFor(s)) }"
        @click="onClick($event, s)"
      >
        <input
          type="checkbox"
          :checked="workspace.isSelected(refFor(s))"
          @click.stop
          @change="onCheckbox(s)"
        />
        <span class="kind">{{ s.kind }}</span>
        <span class="name">{{ s.name }}</span>
        <span class="line mono muted">{{ s.line ?? '?' }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.symbol-list {
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.symbol-row {
  gap: 6px;
}
.name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.line {
  margin-left: auto;
  font-size: 10px;
  flex-shrink: 0;
}
input[type="checkbox"] {
  flex-shrink: 0;
}
.small {
  font-size: 11px;
  padding: 2px 6px;
}
</style>
