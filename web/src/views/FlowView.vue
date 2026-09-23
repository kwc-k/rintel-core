<script setup lang="ts">
// Software Topology Workbench (SPEC-P2 §22 journey + TOPO-EDITOR-UX0).
// Circuit pane inside the workbench shell: header (breadcrumb + undo/redo
// + DRC/LVS/Synthesis toggles + design status), palette, X6 canvas,
// inspector, agent chrome.  LVS/Synthesis panels live in the shell dock.
import { onMounted, ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useFlowStore, registerFlowToast } from '../stores/flow'
import { useDesignStore } from '../stores/design'
import { useToastStore } from '../stores/toast'
import { useWorkspaceStore } from '../stores/workspace'
import { useWorkbenchStore } from '../stores/workbench'
import { isApiError } from '../api/client'
import * as flowApi from '../api/flow'
import FlowCanvas from '../components/flow/FlowCanvas.vue'
import FlowInspector from '../components/flow/FlowInspector.vue'
import PatchPreviewDialog from '../components/flow/PatchPreviewDialog.vue'
import ContextMenu from '../components/ContextMenu.vue'
import AgentPreviewDialog from '../components/AgentPreviewDialog.vue'
import AnnotationPanel from '../components/AnnotationPanel.vue'
import SketchDialog from '../components/SketchDialog.vue'
import TruthBadge from '../components/ui/TruthBadge.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const flow = useFlowStore()
const design = useDesignStore()
const toastStore = useToastStore()
const workspace = useWorkspaceStore()
const wb = useWorkbenchStore()

registerFlowToast((msg, kind) => {
  toastStore.push(msg, kind ?? 'info')
})

const canvasRef = ref<InstanceType<typeof FlowCanvas> | null>(null)
const newBlockName = ref('')
const newCompositeName = ref('')
const promptError = ref('')
const showCompositeForm = ref(false)

const noFlow = computed(() => !flow.flow && !flow.loading)

const scopeName = computed(() => {
  if (!flow.currentParentBlockId) return flow.flow?.name ?? ''
  return flow.blocksById[flow.currentParentBlockId]?.name ?? ''
})

onMounted(async () => {
  const id = (route.params.id as string | undefined) ?? wb.activeFlowId
  if (!id) return
  wb.setFlow(id)
  try {
    await flow.loadFlow(id)
  } catch {
    // pane keeps its empty state; user can create/open a circuit
  }
})

async function createBlock(kind: 'function' | 'object' | 'composite' | 'proposed'): Promise<void> {
  const name = newBlockName.value.trim() || (kind === 'function' ? 'NewFunction'
    : kind === 'composite' ? 'NewComposite' : 'NewModule')
  void await flow.addBlock(kind, name)
}

async function createComposite(): Promise<void> {
  const ids = canvasRef.value?.getSelectedBlockIds() ?? []
  if (ids.length < 2) { promptError.value = t('wb.circuit.boxSel'); return }
  promptError.value = ''
  await flow.createComposite(newCompositeName.value.trim() || 'Group', ids)
  showCompositeForm.value = false
  newCompositeName.value = ''
}

async function expand(): Promise<void> {
  await flow.expandNeighbors()
}

function openEvidence(payload: { repoId: string; symbolId: string }): void {
  workspace.setRepo(payload.repoId)
  workspace.setFocus({ plane: 'evidence', entityType: 'node', id: payload.symbolId })
  workspace.flushPersist()
  router.push('/')
}

async function renameFlow(): Promise<void> {
  if (!flow.flow) return
  const name = window.prompt(t('newTopology.title'), flow.flow.name)
  if (name && name !== flow.flow.name) {
    try {
      await flowApi.patchFlow(flow.flow.id, { name })
      await flow.refresh()
    } catch (err) {
      promptError.value = isApiError(err) ? err.message : String(err)
    }
  }
}
</script>

<template>
  <div class="flow-view">
    <header class="fv-header">
      <button class="fv-name mono" :title="flow.flow?.name" @click="renameFlow">
        {{ flow.flow?.name ?? '…' }}
      </button>
      <button class="fv-btn" data-testid="new-topology" @click="design.showNewTopology = true">
        {{ t('topbar.newTopology') }}
      </button>
      <nav class="fv-breadcrumb">
        <button class="fv-crumb" @click="flow.exitComposite()">{{ flow.flow?.name ?? 'Flow' }}</button>
        <template v-for="c in flow.breadcrumb" :key="c.id">
          <span class="fv-sep">›</span>
          <button class="fv-crumb" @click="flow.enterComposite(c.id, c.name)">{{ c.name }}</button>
        </template>
        <span v-if="flow.currentParentBlockId" class="fv-scope">· {{ t('topbar.newTopologyTitle') }}: {{ scopeName }}</span>
      </nav>
      <div class="fv-spacer"></div>
      <span
        v-if="flow.designModified"
        class="pill modified"
        data-testid="design-modified"
        :title="t('status.lvsNotMatch')"
      >{{ t('topbar.designModified') }}</span>
      <span class="fv-status pill" :class="(flow.validation?.status ?? 'none').toLowerCase()">
        {{ flow.statusText }}
      </span>
      <button class="fv-btn" :disabled="!flow.canUndo" data-testid="undo-btn" title="⌘Z" @click="flow.undo()">
        ⟲ {{ t('undo.undo') }}
      </button>
      <button class="fv-btn" :disabled="!flow.canRedo" data-testid="redo-btn" title="⌘⇧Z" @click="flow.redo()">
        ⟳ {{ t('undo.redo') }}
      </button>
      <button class="fv-btn" data-testid="drc-toggle" @click="flow.validateFlow()">
        {{ t('status.runDrc') }}
      </button>
      <button class="fv-btn" data-testid="lvs-toggle" @click="wb.toggleDockTab('lvs')">
        {{ t('status.runLvs') }}
      </button>
      <button class="fv-btn" data-testid="synth-toggle" @click="wb.toggleDockTab('synthesis')">
        {{ t('status.genPlan') }}
      </button>
    </header>

    <div class="fv-plane" data-testid="design-plane-banner">
      <TruthBadge kind="plane" value="DESIGN" />
      <span>{{ $t('wb.plane.designNote') }}</span>
      <TruthBadge kind="plane" value="STATIC" compact />
      <span class="fv-plane-note">{{ $t('wb.plane.asisNote') }}</span>
    </div>

    <div v-if="noFlow" class="fv-empty" data-testid="circuit-empty">
      <div class="fv-empty-title">{{ t('wb.circuit.emptyTitle') }}</div>
      <div class="fv-empty-hint">{{ t('wb.circuit.emptyHint') }}</div>
      <button class="fv-btn" data-testid="circuit-new" @click="design.showNewTopology = true">
        + {{ t('wb.circuit.newTopology') }}
      </button>
    </div>

    <div v-else class="fv-body">
      <aside class="fv-palette">
        <div class="fv-palette-title">{{ t('canvas.menuNew') }}</div>
        <input v-model="newBlockName" class="fv-input" :placeholder="t('inspector.portName')" data-testid="new-block-name" @keydown.enter="createBlock('function')" />
        <button class="fv-btn wide" data-testid="add-function" @click="createBlock('function')">+ {{ t('canvas.newFunction') }}</button>
        <button class="fv-btn wide" data-testid="add-composite" @click="createBlock('composite')">+ {{ t('canvas.newComposite') }}</button>
        <div class="fv-palette-title">{{ t('multi.createComposite') }}</div>
        <input v-model="newCompositeName" class="fv-input" :placeholder="t('wb.circuit.group')" data-testid="composite-name" @keydown.enter="createComposite" />
        <button class="fv-btn wide" data-testid="wrap-composite" @click="createComposite">
          {{ t('multi.createComposite') }}
        </button>
        <div class="fv-palette-hint">
          {{ t('inspector.emptyHint') }}
        </div>
        <div class="fv-palette-hint">
          {{ t('shortcuts.undo') }} · {{ t('shortcuts.redo') }} · {{ t('shortcuts.agent') }}
        </div>
      </aside>
      <main class="fv-canvas-wrap">
        <div v-if="flow.loading" class="fv-loading">{{ t('common.loading') }}</div>
        <FlowCanvas v-else ref="canvasRef" />
      </main>
      <FlowInspector @open-evidence="openEvidence" />
    </div>
    <PatchPreviewDialog v-if="flow.preview" @close="() => { flow.preview = null }" />

    <!-- TOPO-EDITOR-UX0 chrome (ContextMenu is mounted once by the shell) -->
    <AgentPreviewDialog
      v-if="design.proposal"
      :proposal="design.proposal"
      @close="design.closeProposal()"
      @applied="design.closeProposal()"
    />
    <AnnotationPanel v-if="design.annotationBlockId" />
    <SketchDialog v-if="design.showSketch" />
  </div>
</template>

<style scoped>
.fv-plane {
  display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
  padding: 3px 10px; border-bottom: 1px solid var(--border);
  background: var(--truth-derived-bg); font-size: 10.5px; color: var(--text-secondary);
}
.fv-plane-note { color: var(--text-muted); }
.flow-view {
  height: 100%;
  min-height: 420px;
  overflow: hidden;
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}
.fv-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.fv-back {
  border: 1px solid var(--border-strong);
  background: var(--panel);
  border-radius: 4px;
  cursor: pointer;
  color: var(--text-primary);
}
.fv-name { font-size: 13px; font-weight: 600; border: none; background: none; cursor: pointer; color: var(--text-primary); }
.fv-breadcrumb { display: flex; align-items: center; gap: 4px; font-size: 11px; }
.fv-crumb { border: none; background: none; color: var(--accent); cursor: pointer; font-size: 11px; }
.fv-sep { color: var(--text-muted); }
.fv-scope { color: var(--text-secondary); }
.fv-spacer { flex: 1; }
.pill {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid var(--border-strong);
  color: var(--text-muted);
}
.pill.modified { background: var(--warn-bg); color: var(--warn); border-color: var(--sugg-border); }
.pill.match { background: var(--ok-bg); color: var(--ok); border-color: var(--ok); }
.pill.stale { background: var(--warn-bg); color: var(--warn); border-color: var(--sugg-border); }
.pill.mismatch, .pill.unbound { background: var(--danger-bg); color: var(--danger); border-color: var(--danger); }
.fv-btn {
  border: 1px solid var(--border-strong);
  background: var(--panel);
  border-radius: 4px;
  padding: 4px 9px;
  font-size: 11px;
  cursor: pointer;
  color: var(--text-primary);
}
.fv-btn:disabled { opacity: 0.5; cursor: default; }
.fv-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.fv-btn.wide { width: 100%; }
.fv-input {
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 11px;
  background: var(--panel);
  color: var(--text-primary);
}
.fv-composite-form { display: inline-flex; gap: 4px; align-items: center; }
.fv-error { color: var(--danger); font-size: 10px; }
.fv-body { flex: 1; display: flex; min-height: 0; }
.fv-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: var(--canvas-bg);
  padding: 24px;
}
.fv-empty-title { font-size: 15px; font-weight: 700; }
.fv-empty-hint { font-size: 12px; color: var(--text-muted); max-width: 460px; line-height: 1.6; text-align: center; }
.fv-palette {
  width: 200px;
  border-right: 1px solid var(--border);
  background: var(--panel);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fv-palette-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--text-muted); }
.fv-palette-hint { font-size: 9.5px; color: var(--text-muted); line-height: 1.5; }
.fv-canvas-wrap { flex: 1; min-width: 0; position: relative; }
.fv-loading { padding: 40px; color: var(--text-muted); }
</style>
