<script setup lang="ts">
import { computed, markRaw, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { VueFlow } from '@vue-flow/core'
import type { Connection, Edge, EdgeChange, Node, NodeChange, NodeDragEvent, NodeMouseEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
// NOTE: @vue-flow/background ships no separate stylesheet (patterns are
// inline SVG); its package exposes no `dist/style.css` export.
import '@vue-flow/controls/dist/style.css'
import { ARCH_NODE_LIMIT, mappingCounts, nodeDiffBadge, nodeDiffClass } from '../domain/architecture'
import type { ArchComponent, DiffRow, RelationKind } from '../domain/architecture'
import { useWorkspaceStore } from '../stores/workspace'
import type { ModelMode } from '../stores/workspace'
import { useArchStore } from '../stores/arch'
import type { ComponentBlockers } from '../stores/arch'
import { useToastStore } from '../stores/toast'
import ArchNode from './ArchNode.vue'
import type { ArchNodeData } from './ArchNode.vue'
import RelationKindDialog from './RelationKindDialog.vue'
import DeleteComponentDialog from './DeleteComponentDialog.vue'
import CreateComponentDialog from './CreateComponentDialog.vue'
import CreateProposalDialog from './CreateProposalDialog.vue'

const workspace = useWorkspaceStore()
const arch = useArchStore()
const toast = useToastStore()

const nodeTypes: Record<string, any> = { arch: markRaw(ArchNode) }

// ---------------------------------------------------------------------------
// active model + mode
// ---------------------------------------------------------------------------
const activeModel = computed(() => arch.activeModel())
const mode = computed<ModelMode>(() => workspace.mode)
const isDiff = computed(() => mode.value === 'diff' && activeModel.value?.kind === 'proposal')
const canEdit = computed(() => !isDiff.value && !!activeModel.value)

const components = computed<ArchComponent[]>(() =>
  activeModel.value ? arch.componentsOfModel(activeModel.value.id) : [],
)

// diff lookup: component id → row (working-side buckets only)
const diffByComponentId = computed<Map<string, DiffRow>>(() => {
  const m = new Map<string, DiffRow>()
  for (const row of arch.diff?.rows ?? []) {
    if (row.entity && 'modelId' in row.entity && row.bucket !== 'removed_components') {
      m.set((row.entity as ArchComponent).id, row)
    }
  }
  return m
})

const nodes = computed<Node<ArchNodeData>[]>(() => {
  const list = components.value.slice(0, ARCH_NODE_LIMIT)
  const layout = activeModel.value ? arch.layoutOfModel(activeModel.value.id) : {}
  const rowById = diffByComponentId.value
  return list.map((c, index) => {
    const counts = mappingCounts(arch.mappingsForComponent(c.id))
    const row = isDiff.value ? rowById.get(c.id) : undefined
    const cls = nodeDiffClass(row)
    return {
      id: c.id,
      type: 'arch',
      position: layout[c.id] ?? { x: (index % 2) * 280, y: Math.floor(index / 2) * 200 },
      data: {
        component: c,
        files: counts.files,
        symbols: counts.symbols,
        diffBadge: isDiff.value ? nodeDiffBadge(row) : null,
      },
      class: cls ? `${cls} vf-diff-node` : undefined,
      domAttributes: { 'data-nid': c.id } as Node<ArchNodeData>['domAttributes'],
    }
  })
})

const edges = computed<Edge[]>(() => {
  const modelId = activeModel.value?.id
  return (arch.bootstrap?.relations ?? [])
    .filter((r) => !modelId || r.modelId === modelId)
    .map((r) => ({
      id: r.id,
      source: r.srcId,
      target: r.dstId,
      label: r.kind,
      selectable: canEdit.value,
      focusable: canEdit.value,
    }))
})

// ---------------------------------------------------------------------------
// selection bridge (nodes + edges)
// ---------------------------------------------------------------------------
const selectedIds = new Set<string>()
const selectedEdgeIds = new Set<string>()

function syncSelection() {
  const refs: { plane: 'arch'; entityType: 'component' | 'relation'; id: string }[] = []
  for (const id of selectedIds) refs.push({ plane: 'arch', entityType: 'component', id })
  for (const id of selectedEdgeIds) refs.push({ plane: 'arch', entityType: 'relation', id })
  workspace.replaceSelection(refs)
}

function onNodesChange(changes: NodeChange[]) {
  let touched = false
  for (const c of changes) {
    if (c.type === 'select') {
      touched = true
      if (c.selected) selectedIds.add(c.id)
      else selectedIds.delete(c.id)
    }
  }
  if (touched) syncSelection()
}

function onEdgesChange(changes: EdgeChange[]) {
  let touched = false
  for (const c of changes) {
    if (c.type === 'select') {
      touched = true
      if (c.selected) selectedEdgeIds.add(c.id)
      else selectedEdgeIds.delete(c.id)
    }
  }
  if (touched) syncSelection()
}

function onNodeClick(evt: NodeMouseEvent) {
  workspace.setFocus({ plane: 'arch', entityType: 'component', id: evt.node.id })
}

function onPaneClick() {
  workspace.clearSelection()
  selectedIds.clear()
  selectedEdgeIds.clear()
}

// ---------------------------------------------------------------------------
// drag / reparent (disabled in DIFF mode)
// ---------------------------------------------------------------------------
/** Minimal node shape needed for drop-target detection (position +
 * dimensions in flow coordinates). Works for both vue-flow GraphNode
 * instances and our computed node objects. */
interface DropNode {
  id: string
  position: { x: number; y: number }
  dimensions?: { width: number; height: number } | null
}

function findDropTarget(node: DropNode, all: DropNode[]): DropNode | null {
  const w = node.dimensions?.width ?? 200
  const h = node.dimensions?.height ?? 80
  const cx = node.position.x + w / 2
  const cy = node.position.y + h / 2
  for (const other of all) {
    if (other.id === node.id) continue
    const ow = other.dimensions?.width ?? 200
    const oh = other.dimensions?.height ?? 80
    if (cx >= other.position.x && cx <= other.position.x + ow && cy >= other.position.y && cy <= other.position.y + oh) {
      return other
    }
  }
  return null
}

function onNodeDragStop(evt: NodeDragEvent) {
  if (!canEdit.value) return
  const node = evt.node
  arch.saveLayoutDebounced({ [node.id]: { x: node.position.x, y: node.position.y } })

  // vue-flow's NodeDragEvent.nodes only contains the dragged node, so the
  // drop target must be looked up against the full node set (static
  // positions are fine: only the dragged node moves).
  const target = findDropTarget(node, nodes.value)
  const currentParent = (node.data as ArchNodeData).component.parentId
  if (target && target.id !== node.id) {
    if (currentParent !== target.id) arch.updateComponent(node.id, { parentId: target.id }).catch(() => {})
  } else if (currentParent) {
    arch.updateComponent(node.id, { parentId: null }).catch(() => {})
  }
}

// ---------------------------------------------------------------------------
// connect → relation dialog
// ---------------------------------------------------------------------------
const pendingConnection = ref<{ srcId: string; dstId: string } | null>(null)
const relationDialogOpen = ref(false)

function onConnect(connection: Connection) {
  if (!canEdit.value) return
  if (!connection.source || !connection.target || connection.source === connection.target) return
  pendingConnection.value = { srcId: connection.source, dstId: connection.target }
  relationDialogOpen.value = true
}

async function confirmRelation(kind: RelationKind, label: string) {
  const conn = pendingConnection.value
  if (!conn) return
  try {
    await arch.createRelation({ srcId: conn.srcId, dstId: conn.dstId, kind, label: label || undefined })
  } catch {
    // reported by the store
  }
  relationDialogOpen.value = false
  pendingConnection.value = null
}

// ---------------------------------------------------------------------------
// delete flow (components + edges)
// ---------------------------------------------------------------------------
const deleteDialogOpen = ref(false)
const pendingDelete = ref<{ id: string; name: string; blockers: ComponentBlockers; mappingCount: number } | null>(null)

async function deleteSelectedEdges(): Promise<boolean> {
  const edgeIds = [...selectedEdgeIds]
  if (edgeIds.length === 0) return false
  for (const id of edgeIds) {
    try {
      await arch.deleteRelation(id)
      selectedEdgeIds.delete(id)
    } catch {
      return true // reported by the store; stop the batch
    }
  }
  syncSelection()
  return true
}

async function deleteSelected() {
  if (await deleteSelectedEdges()) return
  const ids = workspace.selected
    .filter((r) => r.plane === 'arch' && r.entityType === 'component')
    .map((r) => r.id)
  for (const id of ids) {
    try {
      const result = await arch.deleteComponent(id)
      if (result.status === 'blocked') {
        const comp = arch.componentById(id)
        pendingDelete.value = {
          id,
          name: comp?.name ?? id,
          blockers: result.blockers,
          mappingCount: arch.mappingsForComponent(id).length,
        }
        deleteDialogOpen.value = true
        return
      }
    } catch {
      // reported by the store
      return
    }
  }
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  try {
    await arch.deleteComponent(pendingDelete.value.id, { subtree: true })
    deleteDialogOpen.value = false
    pendingDelete.value = null
  } catch {
    // keep dialog open on failure; the store surfaced the error
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key !== 'Delete' && e.key !== 'Backspace') return
  if (!canEdit.value) return
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
  void deleteSelected()
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
  if (workspace.workspaceId && !arch.bootstrap && arch.loadState !== 'loading') {
    arch.loadBootstrap()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
})

// mode switching + diff loading
function setMode(m: ModelMode) {
  if (m === 'as_is') {
    workspace.setMode('as_is')
    return
  }
  if (activeModel.value?.kind !== 'proposal') {
    toast.push('请先创建或选择 Proposal', 'error')
    return
  }
  workspace.setMode(m)
  if (m === 'diff') void arch.loadDiff(true)
}

function selectModel(modelId: string) {
  const model = arch.modelById(modelId)
  if (!model) return
  workspace.setActiveModelId(modelId)
  if (model.kind === 'as_is') {
    workspace.setMode('as_is')
  } else if (workspace.mode === 'as_is') {
    workspace.setMode('to_be')
  }
}

watch(
  isDiff,
  (v) => {
    if (v) void arch.loadDiff(true)
  },
  { immediate: true },
)

const modelOptions = computed(() => arch.bootstrap?.models ?? [])
const isToolbarVisible = computed(() => arch.bootstrap !== null)

// dialogs
const proposeOpen = ref(false)
const addComponentOpen = ref(false)

// --- node limit guard (SPEC-P1 §11; effectively unreachable in this slice) --
let warnedOverLimit = false
watch(
  () => components.value.length,
  (n) => {
    if (n > ARCH_NODE_LIMIT && !warnedOverLimit) {
      warnedOverLimit = true
      toast.push(`架构组件超过 ${ARCH_NODE_LIMIT} 上限，画布仅显示前 ${ARCH_NODE_LIMIT} 个`, 'error')
    }
  },
)

function goToEvidence() {
  workspace.setCenterTab('structure')
}
</script>

<template>
  <div class="arch-canvas" tabindex="0">
    <div v-if="isToolbarVisible" class="arch-toolbar">
      <select
        v-if="modelOptions.length > 1"
        class="model-select"
        :value="activeModel?.id ?? ''"
        aria-label="架构模型"
        @change="selectModel(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="m in modelOptions" :key="m.id" :value="m.id">
          {{ m.kind === 'as_is' ? 'AS-IS' : m.name }}
        </option>
      </select>
      <div class="mode-tabs">
        <button
          type="button"
          class="mode-tab"
          :class="{ active: mode === 'as_is', 'is-disabled': false }"
          data-mode="as_is"
          @click="setMode('as_is')"
        >
          AS-IS
        </button>
        <button
          type="button"
          class="mode-tab"
          :class="{ active: mode === 'to_be', 'is-disabled': activeModel?.kind !== 'proposal' }"
          data-mode="to_be"
          :title="activeModel?.kind !== 'proposal' ? '请先创建或选择 Proposal' : undefined"
          @click="setMode('to_be')"
        >
          TO-BE
        </button>
        <button
          type="button"
          class="mode-tab"
          :class="{ active: mode === 'diff', 'is-disabled': activeModel?.kind !== 'proposal' }"
          data-mode="diff"
          :title="activeModel?.kind !== 'proposal' ? '请先创建或选择 Proposal' : undefined"
          @click="setMode('diff')"
        >
          DIFF
        </button>
      </div>
      <div class="toolbar-actions">
        <button type="button" class="ghost propose-btn" @click="proposeOpen = true">创建 Proposal</button>
        <button
          v-if="activeModel"
          type="button"
          class="ghost add-comp-btn"
          :disabled="!canEdit"
          @click="addComponentOpen = true"
        >
          添加组件
        </button>
      </div>
    </div>

    <div v-if="arch.loadState === 'loading'" class="canvas-status">加载架构…</div>
    <div v-else-if="arch.loadState === 'error'" class="canvas-status">
      无法加载架构
      <button type="button" class="ghost" @click="arch.loadBootstrap()">重试</button>
    </div>

    <div v-else-if="!activeModel" class="canvas-status">无可用架构模型</div>

    <div v-else-if="components.length === 0 && activeModel.kind === 'as_is'" class="arch-empty">
      <div class="arch-empty-title">No architecture components yet.</div>
      <div class="arch-empty-sub">Select code evidence and create your first component.</div>
      <button type="button" class="primary cta" @click="goToEvidence">Go to Code Structure</button>
    </div>

    <div v-else-if="components.length === 0" class="arch-empty">
      <div class="arch-empty-title">This Proposal is empty.</div>
      <div class="arch-empty-sub">Add components in TO-BE mode or fork from a non-empty AS-IS.</div>
      <button type="button" class="primary cta" @click="setMode('to_be')">Go to TO-BE</button>
    </div>

    <VueFlow
      v-else
      :nodes="nodes"
      :edges="edges"
      :node-types="nodeTypes"
      :delete-key-code="null"
      :auto-pan-on-connect="false"
      :auto-pan-on-node-drag="false"
      :nodes-draggable="canEdit"
      :nodes-connectable="canEdit"
      :elements-selectable="true"
      @nodes-change="onNodesChange"
      @edges-change="onEdgesChange"
      @node-click="onNodeClick"
      @node-drag-stop="onNodeDragStop"
      @connect="onConnect"
      @pane-click="onPaneClick"
    >
      <Background />
      <Controls />
    </VueFlow>

    <RelationKindDialog v-model:open="relationDialogOpen" @confirm="confirmRelation" />
    <DeleteComponentDialog
      v-model:open="deleteDialogOpen"
      :component-name="pendingDelete?.name ?? ''"
      :blockers="pendingDelete?.blockers ?? { children: [], relations: [] }"
      :mapping-count="pendingDelete?.mappingCount ?? 0"
      @confirm="confirmDelete"
    />
    <CreateComponentDialog v-model:open="addComponentOpen" />
    <CreateProposalDialog v-model:open="proposeOpen" />
  </div>
</template>

<style scoped>
.arch-canvas {
  height: 100%;
  min-height: 0;
  position: relative;
  background: var(--panel);
  display: flex;
  flex-direction: column;
}
.arch-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--panel);
  z-index: 5;
}
.model-select {
  font-size: 12px;
  padding: 3px 6px;
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  background: var(--panel);
}
.mode-tabs {
  display: flex;
  gap: 2px;
}
.mode-tab {
  border: 1px solid transparent;
  border-bottom: 2px solid transparent;
  border-radius: 4px 4px 0 0;
  background: transparent;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}
.mode-tab.active {
  border-bottom-color: var(--accent);
  color: var(--accent);
}
.mode-tab.is-disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}
.propose-btn {
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 5px;
  padding: 3px 8px;
  font-size: 11px;
}
.add-comp-btn {
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  padding: 3px 8px;
  font-size: 11px;
}
.arch-canvas > :deep(.vue-flow) {
  flex: 1;
  min-height: 0;
}
.canvas-status {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-muted);
}
.arch-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  text-align: center;
}
.arch-empty-title {
  font-weight: 600;
  font-size: 15px;
  color: var(--text);
}
.arch-empty-sub {
  color: var(--text-muted);
  margin-bottom: 6px;
}
</style>
