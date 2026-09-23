<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRepoStore } from '../stores/repo'
import { useWorkspaceStore } from '../stores/workspace'
import { useSourceStore } from '../stores/source'
import type { TreeItem } from '../domain/evidence'
import type { EntityRef } from '../domain/workspace'
import { sameRef } from '../domain/workspace'
import FileSymbolList from './FileSymbolList.vue'

defineOptions({ name: 'TreeBrowser' })

const props = withDefaults(defineProps<{ repoId: string; path?: string }>(), { path: '' })

const repo = useRepoStore()
const workspace = useWorkspaceStore()
const source = useSourceStore()

const items = ref<TreeItem[]>([])
const loading = ref(false)
const failed = ref(false)
const anchor = ref<EntityRef | null>(null)

function refFor(item: TreeItem): EntityRef {
  return { plane: 'evidence', entityType: item.kind === 'file' ? 'file' : 'dir', id: item.path }
}

function isExpanded(item: TreeItem): boolean {
  return workspace.expanded.includes(item.path)
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    const page = await repo.getTree(props.repoId, props.path)
    items.value = page.items
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

watch(() => [props.repoId, props.path], load, { immediate: true })

function toggle(item: TreeItem) {
  workspace.setExpanded(item.path, !isExpanded(item))
}

function onNameClick(item: TreeItem, event: MouseEvent) {
  if (event.shiftKey && anchor.value) {
    const idx = items.value.findIndex((x) => x.id === item.id)
    if (idx >= 0) {
      workspace.selectRange(anchor.value, items.value.slice(0, idx + 1).map(refFor))
    }
    return
  }
  anchor.value = refFor(item)
  workspace.setFocus(refFor(item))
  if (item.kind === 'file') {
    source.open(item.path, null, props.repoId, workspace.snapshotId ?? undefined)
  } else {
    workspace.setExpanded(item.path, !isExpanded(item))
  }
}

function onCheckbox(item: TreeItem) {
  anchor.value = refFor(item)
  workspace.toggleSelect(refFor(item))
}
</script>

<template>
  <div class="tree">
    <div v-if="loading" class="muted small">加载…</div>
    <div v-else-if="failed" class="muted small">加载失败</div>
    <div v-else-if="items.length === 0" class="muted small">（空）</div>
    <template v-else>
      <template v-for="item in items" :key="item.id">
        <div
          class="row tree-row"
          :class="{
            [item.kind]: true,
            selected: workspace.isSelected(refFor(item)),
            focused: sameRef(workspace.focus, refFor(item)),
          }"
        >
          <button class="chevron" type="button" :disabled="item.kind === 'file' && item.symbolCount === 0" @click.stop="toggle(item)">
            {{ isExpanded(item) ? '▾' : '▸' }}
          </button>
          <input type="checkbox" :checked="workspace.isSelected(refFor(item))" @click.stop @change="onCheckbox(item)" />
          <span class="icon">{{ item.kind === 'file' ? '📄' : '📁' }}</span>
          <span class="name" @click="onNameClick(item, $event)">{{ item.name }}</span>
          <span v-if="item.kind === 'file' && item.symbolCount > 0" class="count muted">{{ item.symbolCount }}</span>
        </div>
        <div v-if="isExpanded(item) && item.kind === 'dir'" class="children">
          <TreeBrowser :repo-id="repoId" :path="item.path" />
        </div>
        <div v-if="isExpanded(item) && item.kind === 'file'" class="children">
          <FileSymbolList :repo-id="repoId" :path="item.path" />
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.tree {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.tree-row {
  gap: 2px;
}
.chevron {
  border: none;
  background: transparent;
  padding: 0 2px;
  width: 16px;
  font-size: 10px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.chevron:disabled {
  opacity: 0.25;
}
.icon {
  flex-shrink: 0;
  font-size: 11px;
}
.name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.count {
  margin-left: auto;
  font-size: 10px;
  flex-shrink: 0;
}
.children {
  padding-left: 14px;
}
input[type="checkbox"] {
  flex-shrink: 0;
}
.small {
  font-size: 11px;
  padding: 2px 6px;
}
</style>
