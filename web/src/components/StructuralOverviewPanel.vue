<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
import { useEvidenceStore } from '../stores/evidence'
import { useSourceStore } from '../stores/source'
import type { EvidenceNode, StructuralEdge, TreeItem } from '../domain/evidence'
import { sameRef } from '../domain/workspace'

const workspace = useWorkspaceStore()
const evidence = useEvidenceStore()
const source = useSourceStore()

const overview = computed(() => evidence.overview)

const counts = computed(() => overview.value?.counts ?? {})

const unresolvedReasons = computed(() => {
  const raw = counts.value.unresolved_reasons
  if (raw && typeof raw === 'object') {
    return raw as Record<string, number>
  }
  return {} as Record<string, number>
})
const topReasons = computed(() =>
  Object.entries(unresolvedReasons.value).sort((a, b) => b[1] - a[1]).slice(0, 3))

const bannerText = 'Evidence / Code Structure — 基于证据的代码结构，非自动架构判断'

onMounted(async () => {
  if (workspace.repoId && !evidence.overview && !evidence.overviewLoading) {
    try {
      await evidence.fetchOverview(workspace.repoId, workspace.snapshotId ?? undefined)
    } catch {
      // reported by store
    }
  }
})

function treeRef(item: TreeItem) {
  return {
    plane: 'evidence' as const,
    entityType: item.kind === 'file' ? ('file' as const) : ('dir' as const),
    id: item.path,
  }
}

function onTreeClick(item: TreeItem) {
  workspace.setFocus(treeRef(item))
  if (item.kind === 'file') source.open(item.path, null, workspace.repoId!, workspace.snapshotId ?? undefined)
}

function onUnitClick(unit: EvidenceNode) {
  workspace.setFocus({ plane: 'evidence', entityType: 'node', id: unit.id })
  if (unit.path) source.open(unit.path, unit.startLine ?? null, workspace.repoId!, workspace.snapshotId ?? undefined)
}

function onEdgeClick(edge: StructuralEdge) {
  // Aggregated module-pair row has no single canonical edge id, so focus the
  // source module (a top-level DIRECTORY path) instead of fabricating an edge.
  workspace.setFocus({ plane: 'evidence', entityType: 'dir', id: edge.src })
}

function isTreeSelected(item: TreeItem) {
  return workspace.isSelected(treeRef(item))
}

function reasonLabel(key: string): string {
  const labels: Record<string, string> = {
    missing_target: '目标不在观察域',
    call_vs_array_ambiguous: '调用/数组下标无法区分',
    true_external: '外部库符号',
    intrinsic: '语言内建函数',
    ambiguous_symbol: '同名歧义',
    cross_language: '跨语言命名',
    parser_noise: '解析噪声',
    other: '其他',
  }
  return labels[key] ?? key
}
</script>

<template>
  <div class="panel overview">
    <div class="panel-header">Structural Overview</div>
    <div class="banner">{{ bannerText }}</div>
    <div class="panel-body">
      <div v-if="!workspace.repoId" class="empty">
        <div class="empty-title">暂无结构</div>
        <div>打开一个 repository 后显示 Evidence 结构总览。</div>
      </div>
      <div v-else-if="evidence.overviewLoading && !overview" class="empty">加载中…</div>
      <div v-else-if="!overview" class="empty">
        <div class="empty-title">无快照</div>
        <div>索引完成后显示结构总览。</div>
      </div>
      <template v-else>
        <div class="counts">
          <span class="badge">{{ counts.files ?? 0 }} 文件</span>
          <span class="badge">{{ counts.symbols ?? 0 }} 符号</span>
          <span class="badge">{{ counts.edges ?? 0 }} 边</span>
          <span class="badge">{{ counts.modules ?? 0 }} 模块</span>
          <span class="badge warn" v-if="counts.unresolved"
            title="观察到的可能需要解析的引用，但当前证据不足以确定目标——不是“缺失证据”的证明，是证据不确定性（unresolved / evidence uncertainty）">
            {{ counts.unresolved }} unresolved / 证据不确定
          </span>
        </div>

        <div v-if="topReasons.length" class="muted small reasons">
          <span v-for="r in topReasons" :key="r[0]" class="reason">≈{{ r[1] }} {{ reasonLabel(r[0]) }}</span>
        </div>

        <div v-if="overview.truncated" class="truncated warn-banner">
          显示 {{ overview.budgets.nodesReturned }} / {{ overview.budgets.nodeBudget }} 节点、{{ overview.budgets.edgesReturned }} / {{ overview.budgets.edgeBudget }} 边（预算截断）
        </div>

        <section>
          <h4>模块树</h4>
          <div v-if="overview.tree.length === 0" class="muted small">（空）</div>
          <div
            v-for="item in overview.tree"
            :key="item.id"
            class="row tree-item"
            :class="{ selected: isTreeSelected(item), focused: sameRef(workspace.focus, treeRef(item)) }"
            @click="onTreeClick(item)"
          >
            <span class="icon">{{ item.kind === 'file' ? '📄' : '📁' }}</span>
            <span class="name">{{ item.path }}</span>
            <span v-if="item.symbolCount > 0" class="muted small">{{ item.symbolCount }}</span>
          </div>
        </section>

        <section>
          <h4>顶层单元（{{ overview.topUnits.length }}）</h4>
          <div class="table">
            <div class="thead">
              <span>kind</span><span>name</span><span>qname</span><span>位置</span>
            </div>
            <div v-if="overview.topUnits.length === 0" class="muted small">（空）</div>
            <div v-for="u in overview.topUnits" :key="u.id" class="tr" @click="onUnitClick(u)">
              <span class="kind">{{ u.kind }}</span>
              <span class="name">{{ u.name }}</span>
              <span class="mono muted ell">{{ u.qname }}</span>
              <span class="mono muted small">{{ u.path }}:{{ u.startLine ?? '?' }}</span>
            </div>
          </div>
        </section>

        <section>
          <h4>顶层关系（{{ overview.edges.length }}）</h4>
          <div class="table">
            <div class="thead"><span>src</span><span>kind</span><span>dst</span><span>count</span></div>
            <div v-if="overview.edges.length === 0" class="muted small">（空）</div>
            <div v-for="e in overview.edges" :key="`${e.src}:${e.kind}:${e.dst}`" class="tr" :title="'聚合关系，点击聚焦源模块'" @click="onEdgeClick(e)">
              <span class="mono ell">{{ e.src }}</span>
              <span class="kind">{{ e.kind }}</span>
              <span class="mono ell">{{ e.dst }}</span>
              <span class="muted small">{{ e.count }}</span>
            </div>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.overview {
  height: 100%;
}
.banner {
  background: var(--accent-soft);
  border-bottom: 1px solid rgba(37, 99, 235, 0.2);
  color: var(--accent);
  padding: 6px 10px;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}
.panel-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.counts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: -2px;
}
.reason {
  white-space: nowrap;
}
.truncated {
  font-size: 11px;
}
h4 {
  margin: 0 0 4px;
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.tree-item {
  gap: 6px;
}
.icon {
  flex-shrink: 0;
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.small {
  font-size: 11px;
  flex-shrink: 0;
}
.table {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 5px;
  overflow: hidden;
}
.thead,
.tr {
  display: grid;
  grid-template-columns: 90px 1.2fr 1.4fr 1fr;
  gap: 6px;
  padding: 4px 6px;
  align-items: center;
  min-width: 0;
}
.thead {
  background: var(--surface);
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  border-bottom: 1px solid var(--border);
}
.tr {
  cursor: pointer;
  border-bottom: 1px solid #f0f1f4;
}
.tr:last-child {
  border-bottom: none;
}
.tr:hover {
  background: var(--accent-soft);
}
.ell {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
