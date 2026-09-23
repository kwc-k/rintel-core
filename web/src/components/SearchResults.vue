<script setup lang="ts">
import { computed } from 'vue'
import type { SearchResultItem } from '../domain/evidence'

const props = defineProps<{ items: SearchResultItem[] }>()
const emit = defineEmits<{ (e: 'open', item: SearchResultItem): void }>()

type Group = { key: string; label: string; items: SearchResultItem[] }

function matchGroup(match: string): string {
  if (match.startsWith('exact')) return 'exact'
  if (match === 'fts') return 'fts'
  if (match === 'trigram') return 'trigram'
  return 'like'
}

const groupLabel: Record<string, string> = {
  exact: '精确匹配',
  fts: '全文匹配',
  trigram: '模糊匹配',
  like: '相似匹配',
}

const groups = computed<Group[]>(() => {
  const order = ['exact', 'fts', 'trigram', 'like']
  const buckets = new Map<string, SearchResultItem[]>()
  for (const item of props.items) {
    const key = matchGroup(item.match)
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key)!.push(item)
  }
  return order
    .filter((key) => buckets.has(key))
    .map((key) => ({ key, label: groupLabel[key] ?? key, items: buckets.get(key)! }))
})
</script>

<template>
  <div class="search-results">
    <div v-if="items.length === 0" class="empty small">无匹配结果</div>
    <div v-for="group in groups" :key="group.key" class="group">
      <div class="group-label muted">{{ group.label }}（{{ group.items.length }}）</div>
      <div
        v-for="item in group.items"
        :key="item.id"
        class="result-row"
        role="button"
        tabindex="0"
        @click="emit('open', item)"
        @keyup.enter="emit('open', item)"
      >
        <span class="kind">{{ item.kind }}</span>
        <span class="name">{{ item.name }}</span>
        <span class="qname mono muted">{{ item.qname }}</span>
        <span class="loc mono muted">{{ item.path }}:{{ item.line ?? '?' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-results {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.group {
  display: flex;
  flex-direction: column;
}
.group-label {
  font-size: 10px;
  padding: 4px 6px 2px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.result-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 4px;
  cursor: pointer;
  overflow: hidden;
}
.result-row:hover {
  background: var(--accent-soft);
}
.name {
  font-weight: 600;
  white-space: nowrap;
}
.qname {
  color: var(--text-muted);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.loc {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-muted);
  flex-shrink: 0;
}
</style>
