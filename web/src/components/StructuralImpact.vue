<script setup lang="ts">
import { computed, watch } from 'vue'
import { useArchStore } from '../stores/arch'
import { useSourceStore } from '../stores/source'
import { useWorkspaceStore } from '../stores/workspace'
import type { ImpactChainItem, ImpactSymbolItem } from '../domain/architecture'

/**
 * Structural Impact (S4-WEB-CONTRACT §5): changed arch entities → mapped
 * evidence → bounded traversal → potentially affected files/symbols/modules.
 * UI name is FROZEN as "Structural Impact" (never "Impact Analysis").
 *
 * S5B: impacted files/symbols are clickable → source viewer at the
 * affected location (reason-chain "back to source", ruling step 16).
 */
const arch = useArchStore()
const source = useSourceStore()
const workspace = useWorkspaceStore()

const props = defineProps<{
  /** when set, only this change's impact is requested (single-change view) */
  changeIds?: string[]
  /** re-request key: bump to refetch (e.g. the selected diff row changes) */
  requestKey?: string
}>()

const result = computed(() => arch.impact)
const loading = computed(() => arch.impactLoading)
const failed = computed(() => arch.impactError)

function run() {
  const ids = props.changeIds && props.changeIds.length > 0 ? props.changeIds : undefined
  void arch.loadImpact(ids)
}

function openImpactPath(path: string | null, line: number | null, symbolId: string | null) {
  if (!path || !workspace.repoId) return
  if (symbolId) {
    workspace.setFocus({ plane: 'evidence', entityType: 'node', id: symbolId })
  }
  source.open(path, line, workspace.repoId, workspace.snapshotId ?? undefined)
}

function openFile(f: ImpactChainItem) {
  openImpactPath(f.path, f.startLine ?? 1, `node:FILE:${f.path}`)
}

function openSymbol(s: ImpactSymbolItem) {
  openImpactPath(s.path ?? null, s.startLine ?? null, s.id)
}

watch(
  () => [props.changeIds, props.requestKey] as const,
  () => {
    if (props.changeIds && props.changeIds.length > 0) run()
  },
  { immediate: true },
)
</script>

<template>
  <div class="impact-panel">
    <div class="impact-head">
      <span class="impact-title">Structural Impact</span>
      <button type="button" class="ghost impact-refresh" title="重新计算" @click="run">↻</button>
    </div>
    <div v-if="loading" class="impact-status">计算影响…</div>
    <div v-else-if="failed" class="impact-status err">
      无法计算影响
      <button type="button" class="ghost" @click="run">重试</button>
    </div>
    <template v-else-if="result">
      <div class="impact-body">
        <div v-if="result.truncated" class="impact-truncated" title="遍历超出预算，结果被截断">truncated</div>
        <div v-if="result.changes.length === 0 || (result.changes.length === 1 && result.changes[0].files.length === 0)" class="impact-none">
          No structural impact — this change maps to no code.
        </div>
        <template v-else>
          <div v-for="ch in result.changes" :key="ch.changeId" class="impact-change">
            <div v-if="ch.files.length > 0" class="impact-section">
              <div class="impact-section-title">Files（{{ ch.files.length }}）</div>
              <div v-for="f in ch.files" :key="f.path" class="impact-file"
                   :title="`打开 ${f.path}`" @click="openFile(f)">
                <span class="if-path mono">{{ f.path }}</span>
                <span v-if="f.symbolCount > 0" class="if-count muted">{{ f.symbolCount }} symbols</span>
                <div v-if="f.chain && f.chain.length > 0" class="impact-reason">
                  because: <span class="mono">{{ f.chain.join(' ') }}</span>
                </div>
              </div>
            </div>
            <div v-if="ch.symbols.length > 0" class="impact-section">
              <div class="impact-section-title">Symbols（{{ ch.symbols.length }}）</div>
              <div v-for="s in ch.symbols.slice(0, 50)" :key="s.id" class="impact-symbol"
                   :title="s.path ? `打开 ${s.path}` : ''" @click="openSymbol(s)">
                <span class="mono">{{ s.qname ?? s.name }}</span>
                <span class="if-kind muted">{{ s.kind }}</span>
              </div>
            </div>
            <div v-if="ch.modules.length > 0" class="impact-section">
              <div class="impact-section-title">Modules（{{ ch.modules.length }}）</div>
              <div v-for="m in ch.modules.slice(0, 20)" :key="m.id" class="impact-module">
                <span class="mono">{{ m.qname ?? m.name }}</span>
              </div>
            </div>
            <div v-if="ch.edges.length > 0" class="impact-section">
              <div class="impact-section-title">Supporting edges（{{ ch.edges.length }}）</div>
              <div v-for="e in ch.edges.slice(0, 20)" :key="e.id" class="impact-edge mono muted">
                {{ e.kind }}
              </div>
            </div>
          </div>
          <div class="impact-budgets muted">
            depth {{ result.budgets.depth }} · {{ result.budgets.usedNodes }} nodes · {{ result.budgets.usedEdges }} edges
          </div>
        </template>
      </div>
    </template>
    <div v-else class="impact-status">
      <button type="button" class="ghost" @click="run">查看影响</button>
    </div>
  </div>
</template>

<style scoped>
.impact-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  background: var(--panel);
}
.impact-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.impact-title {
  font-weight: 700;
  font-size: 12px;
}
.impact-status {
  color: var(--text-muted);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.impact-status.err {
  color: var(--danger);
}
.impact-file, .impact-symbol {
  cursor: pointer;
}
.impact-file:hover, .impact-symbol:hover {
  background: var(--accent-soft, #eef2ff);
}
.impact-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 340px;
  overflow: auto;
}
.impact-truncated {
  align-self: flex-start;
  font-size: 10px;
  font-weight: 700;
  color: var(--amber-text);
  background: var(--amber-bg);
  border-radius: 4px;
  padding: 1px 6px;
}
.impact-none {
  color: var(--text-muted);
  font-size: 12px;
}
.impact-section {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.impact-section-title {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--text-muted);
}
.impact-file,
.impact-symbol,
.impact-module,
.impact-edge {
  font-size: 11px;
  padding: 2px 0;
  border-bottom: 1px dashed var(--border);
}
.impact-file {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0 8px;
}
.if-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.if-count {
  font-size: 10px;
}
.if-kind {
  font-size: 10px;
  margin-left: 6px;
}
.impact-reason {
  grid-column: 1 / -1;
  font-size: 10px;
  color: var(--text-muted);
  word-break: break-all;
}
.impact-budgets {
  font-size: 10px;
}
.mono {
  font-family: var(--mono);
}
</style>
