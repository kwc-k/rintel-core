<script setup lang="ts">
import { computed, ref } from 'vue'
import { useSearchStore } from '../stores/search'
import { useWorkspaceStore } from '../stores/workspace'
import { useSourceStore } from '../stores/source'
import type { SearchResultItem } from '../domain/evidence'
import { makeEvRef } from '../domain/workspace'
import SearchResults from './SearchResults.vue'

const search = useSearchStore()
const workspace = useWorkspaceStore()
const source = useSourceStore()

const focused = ref(false)

const hasQuery = computed(() => search.query.trim().length > 0)
const showDropdown = computed(() => focused.value && hasQuery.value)

function onInput() {
  if (!workspace.repoId) return
  search.search(workspace.repoId, workspace.snapshotId ?? undefined)
}

function onOpen(item: SearchResultItem) {
  workspace.setFocus({ plane: 'evidence', entityType: 'node', id: item.id })
  source.open(item.path, item.line ?? null, workspace.repoId!, workspace.snapshotId ?? undefined)
  focused.value = false
  search.clear()
}

function onBlur() {
  // Delay so a click on a result row still registers before the dropdown closes.
  setTimeout(() => (focused.value = false), 150)
}
</script>

<template>
  <div class="search-palette">
    <input
      v-model="search.query"
      type="search"
      placeholder="全局搜索符号…"
      :disabled="!workspace.repoId"
      @input="onInput"
      @focus="focused = true"
      @blur="onBlur"
    />
    <span v-if="search.inFlight" class="spinner" aria-label="搜索中"></span>
    <div v-if="showDropdown" class="dropdown">
      <SearchResults :items="search.results" @open="onOpen" />
      <div v-if="search.total > search.results.length" class="muted small foot">
        共 {{ search.total }} 条，显示前 {{ search.results.length }} 条
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-palette {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
}
input {
  width: 240px;
}
.dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  width: 560px;
  max-width: 80vw;
  max-height: 420px;
  overflow: auto;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.14);
  padding: 6px;
  z-index: 50;
}
.foot {
  padding: 4px 6px;
  border-top: 1px solid var(--border);
  margin-top: 4px;
}
</style>
