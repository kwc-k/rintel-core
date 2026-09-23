<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
import { useArchStore } from '../stores/arch'
import { useSourceStore } from '../stores/source'
import { bucketMeta, DIFF_BUCKETS, mappingEntityKind } from '../domain/architecture'
import type { ArchRelation, DiffBucket, DiffMapping, DiffRow } from '../domain/architecture'
import StructuralImpact from './StructuralImpact.vue'

/**
 * DIFF side panel (S4-WEB-CONTRACT §5): frozen diff grouped by bucket.
 * Rendered in the right rail while mode === 'diff'. Clicking a row expands
 * its detail (mappings + Structural Impact for that change); working-side
 * rows also focus the corresponding architecture entity on the canvas.
 */
const workspace = useWorkspaceStore()
const arch = useArchStore()
const source = useSourceStore()

const diff = computed(() => arch.diff)
const loading = computed(() => arch.diffLoading)
const expandedChangeId = ref<string | null>(null)
const expandedKey = ref(0)

watch(
  () => diff.value?.modelId,
  () => {
    expandedChangeId.value = null
  },
)

const groups = computed(() => {
  const rows = diff.value?.rows ?? []
  const byBucket = new Map<DiffBucket, DiffRow[]>()
  for (const bucket of DIFF_BUCKETS) byBucket.set(bucket, [])
  for (const row of rows) {
    byBucket.get(row.bucket)?.push(row)
  }
  return DIFF_BUCKETS.map((bucket) => ({
    bucket,
    meta: bucketMeta(bucket),
    rows: byBucket.get(bucket) ?? [],
  })).filter((g) => g.rows.length > 0)
})

// --- row rendering helpers ---------------------------------------------------

function rowTitle(row: DiffRow): string {
  const e = row.entity ?? (row.baseline as Record<string, unknown> | null)
  if (e && typeof e === 'object') {
    const name = (e as Record<string, unknown>).name
    const kind = (e as Record<string, unknown>).kind
    if (typeof name === 'string') return name
    if (typeof kind === 'string') return `${kind} relation`
  }
  return '?'
}

function rowKind(row: DiffRow): string {
  const e = row.entity ?? (row.baseline as Record<string, unknown> | null)
  if (e && typeof e === 'object') {
    const kind = (e as Record<string, unknown>).kind
    if (typeof kind === 'string') return kind
  }
  return ''
}

function rowDetailText(row: DiffRow): string {
  const d = row.detail
  if (row.bucket === 'moved_components') {
    return `↳ ${d.from_parent_name ?? '—'} → ${d.to_parent_name ?? '顶层'}`
  }
  if (row.bucket === 'modified_components') {
    const fields = (d.fields as string[] | undefined) ?? []
    return `~ ${fields.join(', ')}`
  }
  if (row.bucket === 'mapping_changes') {
    const cname = typeof d.component_name === 'string' ? d.component_name : '?'
    const e = row.mappings[0]
    const label = e?.entity?.name ?? e?.entity?.qname ?? e?.entityId ?? '?'
    return `${row.change === 'added' ? '+' : '−'} ${label}`
  }
  return ''
}

function relationText(row: DiffRow): string {
  const e = row.entity
  if (e && 'srcId' in e) {
    const rel = e as ArchRelation & { srcName?: string | null; dstName?: string | null }
    return `${rel.kind} ${rel.srcName ?? rel.srcId} → ${rel.dstName ?? rel.dstId}`
  }
  const d = row.detail
  return `${rowKind(row)} ${d.src_name ?? '?'} → ${d.dst_name ?? '?'}`
}

function mappingLabel(m: DiffMapping): string {
  const name = m.entity?.name ?? m.entity?.qname
  if (name) return `${m.entity?.kind ?? ''} ${name}`
  return `${m.entityType} ${m.entityId}`
}

// --- interactions -------------------------------------------------------------

function onRowClick(row: DiffRow) {
  // working-side entities focus on the canvas; removed rows only expand
  if (row.entity && row.bucket !== 'removed_components' && row.bucket !== 'removed_relations') {
    if ('srcId' in row.entity) {
      workspace.setFocus({ plane: 'arch', entityType: 'relation', id: row.entity.id })
    } else {
      workspace.setFocus({ plane: 'arch', entityType: 'component', id: row.entity.id })
    }
  }
  expandedChangeId.value = expandedChangeId.value === row.changeId ? null : row.changeId
  expandedKey.value++
}

function openMapping(m: DiffMapping) {
  const e = m.entity
  if (!e) return
  if (e.path && workspace.repoId) {
    const line = mappingEntityKind(m) === 'FILE' ? 1 : (e.startLine ?? null)
    workspace.setFocus({ plane: 'evidence', entityType: 'node', id: e.id })
    source.open(e.path, line, workspace.repoId, workspace.snapshotId ?? undefined)
  }
}

function allImpact() {
  void arch.loadImpact()
}
</script>

<template>
  <div class="panel diff-panel">
    <div class="panel-header">Diff vs Frozen Baseline</div>
    <div class="panel-body">
      <div v-if="loading" class="empty">加载 diff…</div>
      <div v-else-if="!diff" class="empty">
        无法加载 diff
        <button type="button" class="ghost" @click="arch.loadDiff(true)">重试</button>
      </div>
      <div v-else-if="diff.isEmpty" class="empty diff-empty">
        No differences from the frozen baseline yet.
      </div>
      <template v-else>
        <div v-for="g in groups" :key="g.bucket" class="diff-group" :data-bucket="g.bucket">
          <div class="dg-head">
            <span class="dg-sign" :class="g.meta.cls">{{ g.meta.sign }}</span>
            <span class="dg-label">{{ g.meta.label }}</span>
            <span class="dg-count muted">{{ g.rows.length }}</span>
          </div>
          <div
            v-for="row in g.rows"
            :key="row.changeId"
            class="diff-item"
            :class="[g.meta.cls, { expanded: expandedChangeId === row.changeId }]"
            :data-change-id="row.changeId"
            @click="onRowClick(row)"
          >
            <div class="di-line">
              <span class="di-title">{{ rowTitle(row) }}</span>
              <span v-if="rowKind(row)" class="di-kind muted">{{ rowKind(row) }}</span>
              <span v-if="rowDetailText(row)" class="di-detail muted">{{ rowDetailText(row) }}</span>
            </div>
            <div v-if="row.bucket === 'added_relations' || row.bucket === 'removed_relations'" class="di-sub mono muted">
              {{ relationText(row) }}
            </div>
            <div v-if="row.bucket === 'mapping_changes'" class="di-sub mono muted">
              {{ (row.detail.component_name as string) ?? '?' }}
            </div>

            <div v-if="expandedChangeId === row.changeId" class="diff-detail" @click.stop>
              <div v-if="row.mappings.length > 0" class="dd-section">
                <div class="dd-title">Evidence mappings</div>
                <div
                  v-for="(m, i) in row.mappings"
                  :key="i"
                  class="diff-mapping"
                  :class="{ stale: m.stale }"
                  :title="m.stale ? '当前 snapshot 不存在' : `${m.source} mapping`"
                  @click="openMapping(m)"
                >
                  <span class="dm-source muted">{{ m.source }}</span>
                  <span class="dm-kind muted">{{ m.entity?.kind ?? m.entityType }}</span>
                  <span class="dm-name">{{ mappingLabel(m) }}</span>
                </div>
              </div>
              <StructuralImpact
                :key="`${row.changeId}-${expandedKey}`"
                :change-ids="[row.changeId]"
                :request-key="`${row.changeId}-${expandedKey}`"
              />
            </div>
          </div>
        </div>
        <button type="button" class="ghost impact-all-btn" @click="allImpact">查看全部影响</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.diff-panel {
  height: 100%;
}
.panel-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.diff-empty {
  color: var(--text-muted);
  font-size: 12px;
}
.diff-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.dg-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  padding: 2px 0;
  margin-top: 4px;
}
.dg-sign {
  font-weight: 700;
}
.dg-sign.added {
  color: var(--ok);
}
.dg-sign.removed {
  color: var(--danger);
}
.dg-sign.modified,
.dg-sign.moved {
  color: var(--warn);
}
.dg-count {
  margin-left: auto;
}
.diff-item {
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-radius: 5px;
  padding: 5px 7px;
  cursor: pointer;
  font-size: 12px;
  background: var(--panel);
}
.diff-item:hover {
  background: var(--accent-soft);
}
.diff-item.added {
  border-left-color: var(--ok);
}
.diff-item.removed {
  border-left-color: var(--danger);
  opacity: 0.85;
}
.diff-item.modified {
  border-left-color: var(--warn);
}
.diff-item.moved {
  border-left-color: var(--warn);
}
.di-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.di-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.di-kind {
  font-size: 10px;
}
.di-detail {
  font-size: 10px;
  margin-left: auto;
  flex-shrink: 0;
}
.di-sub {
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.diff-detail {
  margin-top: 6px;
  border-top: 1px dashed var(--border);
  padding-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dd-title {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.03em;
}
.diff-mapping {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  padding: 2px 4px;
  border-radius: 4px;
  cursor: pointer;
}
.diff-mapping:hover {
  background: var(--accent-soft);
}
.diff-mapping.stale {
  opacity: 0.55;
}
.dm-source,
.dm-kind {
  font-size: 9px;
  flex-shrink: 0;
}
.dm-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.impact-all-btn {
  align-self: flex-start;
  margin-top: 4px;
  color: var(--accent);
}
.mono {
  font-family: var(--mono);
}
</style>
