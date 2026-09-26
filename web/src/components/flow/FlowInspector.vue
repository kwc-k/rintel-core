<script setup lang="ts">
// Right-hand inspector for the Software Circuit (SPEC-P2 §6/§12/§14/§16):
// Ports / Implementation (Monaco) / Binding + Preview Patch / Apply /
// Validate Against Code.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type * as Monaco from 'monaco-editor'
import { useFlowStore } from '../../stores/flow'
import { useDesignStore } from '../../stores/design'
import { displayBlockName, typeDisplay, SEMANTIC_KINDS } from '../../domain/flow'

const store = useFlowStore()
const design = useDesignStore()
const { t } = useI18n()
const emit = defineEmits<{
  (e: 'open-evidence', payload: { repoId: string; symbolId: string }): void
}>()

const nameDraft = ref('')
const nameDraftRevision = ref('')
const renameNotice = ref('')
const newPort = ref({ name: '', direction: 'input' as 'input' | 'output', semanticKind: 'data' as string, codeType: '' })
const compositeName = ref('New Group')
const showMonaco = ref(false)

const editorContainer = ref<HTMLDivElement | null>(null)
let editor: Monaco.editor.IStandaloneCodeEditor | null = null
let editorModel: Monaco.editor.ITextModel | null = null
const editorFailed = ref(false)

const block = computed(() => store.selectedBlock)
const draftCode = computed(() => store.draftFor(block.value?.id ?? '', block.value?.code ?? ''))

const validation = computed(() => store.validation)
const statusCls = computed(() => (validation.value?.status ?? '').toLowerCase())

watch(
  () => store.selectedBlockId,
  (_id, previousId) => {
    nameDraft.value = block.value?.name ?? ''
    nameDraftRevision.value = store.dto?.eda?.revision ?? ''
    renameNotice.value = ''
    if (previousId !== undefined && showMonaco.value) unmountEditor() // per-block editor state
  },
  { immediate: true },
)

watch(
  () => [block.value?.name, store.dto?.eda?.revision] as const,
  ([name, revision], [previousName]) => {
    if (revision !== nameDraftRevision.value) {
      if (nameDraft.value !== name && nameDraft.value !== previousName) {
        renameNotice.value = 'Design changed; unsaved rename was discarded.'
      }
      nameDraft.value = name ?? ''
      nameDraftRevision.value = revision ?? ''
    } else if (nameDraft.value === previousName) {
      nameDraft.value = name ?? ''
    }
  },
)

async function mountEditor(): Promise<void> {
  showMonaco.value = true
  editorFailed.value = false
  await nextTick()
  if (!editorContainer.value) return
  try {
    const { loadMonaco } = await import('../../lib/monaco')
    const monaco = await loadMonaco()
    const lang = block.value?.symbol?.language ?? 'python'
    if (editor) {
      editor.setModel(null)
    }
    editorModel = monaco.editor.createModel(draftCode.value, lang)
    editor = monaco.editor.create(editorContainer.value, {
      model: editorModel,
      readOnly: false,
      automaticLayout: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      lineNumbersMinChars: 3,
      fontSize: 12,
      // faithful programmatic input (writeback tests type whole blocks):
      // never re-indent what the author wrote
      autoIndent: 'none',
      formatOnPaste: false,
      formatOnType: false,
    })
    editor.onDidChangeModelContent(() => {
      if (editor && block.value) store.setDraft(block.value.id, editor.getValue())
    })
  } catch {
    // Monaco unavailable (test env / chunk failure) — plain-text editor,
    // same draft semantics; Preview/Apply are unaffected
    editor = null
    editorModel?.dispose()
    editorModel = null
    editorFailed.value = true
  }
}

function unmountEditor(): void {
  editorModel?.dispose()
  editorModel = null
  if (editor) { editor.setModel(null) }
  showMonaco.value = false
}

onBeforeUnmount(unmountEditor)

async function saveName(): Promise<void> {
  if (!block.value || !nameDraft.value.trim()) return
  if (nameDraftRevision.value !== (store.dto?.eda?.revision ?? '')) {
    nameDraft.value = block.value.name
    nameDraftRevision.value = store.dto?.eda?.revision ?? ''
    renameNotice.value = 'Design changed; unsaved rename was discarded.'
    return
  }
  if (nameDraft.value.trim() !== block.value.name) {
    await store.updateBlock(block.value.id, { name: nameDraft.value.trim() })
  }
}

async function addPort(): Promise<void> {
  if (!block.value || !newPort.value.name.trim()) return
  await store.addPort({
    blockId: block.value.id,
    name: newPort.value.name.trim(),
    direction: newPort.value.direction,
    semanticKind: newPort.value.semanticKind as never,
    codeType: newPort.value.codeType || null,
  })
  newPort.value = { name: '', direction: 'input', semanticKind: 'data', codeType: '' }
}

async function removePort(portId: string): Promise<void> {
  await store.deletePort(portId)
}

async function markModified(): Promise<void> {
  if (!block.value || block.value.state !== 'existing') return
  const code = store.draftFor(block.value.id, block.value.code ?? '')
  await store.updateBlock(block.value.id, { state: 'modified', code })
}

async function preview(): Promise<void> {
  if (!block.value) return
  await store.previewWriteback(block.value.id)
}

async function apply(): Promise<void> {
  if (!block.value) return
  const code = store.draftFor(block.value.id, block.value.code ?? '')
  const r = await store.applyWriteback(block.value.id, code)
  if (r?.ok) {
    unmountEditor()
  }
}

async function validateNow(): Promise<void> {
  await store.validateFlow()
}

function openEvidence(): void {
  const b = block.value
  if (!b?.symbol) return
  emit('open-evidence', { repoId: store.flow?.repoId ?? '', symbolId: b.symbol.id })
}

function short(canonical: string): string {
  const i = canonical.lastIndexOf(':')
  return i >= 0 ? canonical.slice(i + 1) : canonical
}
</script>

<template>
  <aside class="flow-inspector">
    <template v-if="block">
      <div class="fi-head">
        <span class="fi-title">{{ displayBlockName(block) }}</span>
        <button class="fi-close" title="close" @click="store.clearSelection()">×</button>
      </div>
      <div class="fi-meta">
        <span class="tag" :class="block.kind">{{ block.kind }}</span>
        <span class="tag" :class="block.state">{{ block.state }}</span>
        <span v-if="block.binding" class="tag bound" title="bound to evidence">✓</span>
      </div>
      <section v-if="block.edaAddress" class="fi-sec" aria-label="EDA address">
        <div class="mono">{{ block.displayAddress }} · node {{ block.id }}</div>
        <div class="fi-muted mono" :title="block.edaAddress.revision">Design revision {{ block.edaAddress.revision }}</div>
      </section>

      <section class="fi-sec">
        <label class="fi-label">{{ t('inspector.name') }}</label>
        <input v-model="nameDraft" class="fi-input" @input="renameNotice = ''" @change="saveName" @keydown.enter="saveName" />
        <p v-if="renameNotice" role="status" class="fi-muted">{{ renameNotice }}</p>
      </section>

      <section class="fi-sec">
        <label class="fi-label">{{ t('inspector.bindings') }}</label>
        <template v-if="block.binding">
          <div class="fi-binding mono" :title="block.binding.canonicalSymbolId">
            {{ block.binding.canonicalSymbolId }}
          </div>
          <div v-if="block.symbol" class="fi-file mono">
            {{ block.symbol.path }}:{{ block.symbol.startLine ?? '?' }}
          </div>
          <button class="fi-btn" data-testid="fi-open-evidence" @click="openEvidence">{{ t('nodeMenu.viewEvidence') }}</button>
        </template>
        <div v-else class="fi-muted">{{ t('inspector.noBinding') }}</div>
      </section>

      <section class="fi-sec">
        <label class="fi-label">{{ t('inspector.ports') }}</label>
        <ul class="fi-ports">
          <li v-for="p in block.ports ?? []" :key="p.id" class="fi-port" :class="{ sel: store.selectedPortId === p.id }">
            <span class="mono">{{ p.semanticKind }}/{{ p.direction }} <b>{{ p.name }}</b>
              <em v-if="p.codeType">: {{ typeDisplay(p.codeType) }}</em>
            </span>
            <details v-if="p.portContract" class="fi-port-contract">
              <summary class="mono">{{ p.displayAddress }} · EXPECTED / ACTUAL</summary>
              <div class="mono">port_id: {{ p.id }}</div>
              <div>EXPECTED (DESIGN): direction {{ p.portContract.direction }} · type {{ p.portContract.generic_type }} · dtype {{ p.portContract.dtype }} · shape {{ p.portContract.shape }}</div>
              <div>semantic: {{ p.portContract.semantic_kind }} / {{ p.portContract.semantic_object }}</div>
              <div>Unknown: {{ p.portContract.unknown_fields.join(', ') || 'none' }}</div>
              <div data-testid="port-actual">ACTUAL: {{ p.expectedActual?.actual.status ?? 'UNKNOWN' }} · {{ p.expectedActual?.actual.reason ?? 'no_bound_port_evidence' }}</div>
              <div>Comparison: {{ p.expectedActual?.comparison ?? 'UNKNOWN' }}</div>
            </details>
            <button class="fi-x" title="delete port" @click="removePort(p.id)">×</button>
          </li>
        </ul>
        <div class="fi-port-form">
          <input v-model="newPort.name" :placeholder="t('inspector.portName')" class="fi-input" />
          <select v-model="newPort.direction" class="fi-select">
            <option value="input">input</option>
            <option value="output">output</option>
          </select>
          <select v-model="newPort.semanticKind" class="fi-select">
            <option v-for="k in SEMANTIC_KINDS" :key="k" :value="k">{{ k }}</option>
          </select>
          <input v-model="newPort.codeType" :placeholder="t('inspector.portType')" class="fi-input" />
          <button class="fi-btn" data-testid="fi-add-port" @click="addPort">{{ t('inspector.addPort') }}</button>
        </div>
      </section>

      <section class="fi-sec">
        <label class="fi-label">{{ t('inspector.implementation') }}</label>
        <template v-if="block.state === 'existing' && !showMonaco">
          <div class="fi-muted">
            {{ t('inspector.hintExisting') }}
          </div>
          <button class="fi-btn" data-testid="fi-open-monaco-edit" @click="mountEditor">{{ t('inspector.openMonaco') }}</button>
        </template>
        <template v-else>
          <button class="fi-btn" data-testid="fi-open-monaco" @click="showMonaco ? unmountEditor() : mountEditor()">
            {{ showMonaco ? t('inspector.hideEditor') : t('inspector.openMonaco') }}
          </button>
          <div v-if="showMonaco && !editorFailed" ref="editorContainer" class="fi-editor"></div>
          <textarea
            v-if="showMonaco && editorFailed"
            class="fi-editor fixture-area mono"
            :value="draftCode"
            aria-label="implementation code"
            @input="block && store.setDraft(block.id, ($event.target as HTMLTextAreaElement).value)"
          ></textarea>
          <div v-if="showMonaco && block.state === 'existing'" class="fi-writeback-row">
            <button class="fi-btn warn" data-testid="fi-mark-modified" @click="markModified">{{ t('inspector.editAsModified') }}</button>
          </div>
          <div class="fi-writeback-row">
            <button class="fi-btn" data-testid="fi-preview" :disabled="!draftCode" @click="preview">{{ t('inspector.previewPatch') }}</button>
            <button class="fi-btn primary" data-testid="fi-apply" :disabled="store.applying" @click="apply">
              {{ store.applying ? t('inspector.applying') : t('inspector.apply') }}
            </button>
          </div>
          <div v-if="store.preview && !store.preview.errors?.length" class="fi-preview-ok">{{ t('inspector.previewOk') }}</div>
          <div v-if="block.state === 'proposed'" class="fi-hint">{{ t('inspector.hintProposed') }}</div>
          <div v-if="block.state === 'modified'" class="fi-hint">{{ t('inspector.hintModified') }}</div>
        </template>
      </section>
    </template>

    <template v-else-if="store.selectedNet">
      <div class="fi-head">
        <span class="fi-title">Net ({{ store.selectedNet.kind }})</span>
        <button class="fi-close" @click="store.clearSelection()">×</button>
      </div>
      <div class="fi-muted mono">
        {{ store.blocksById[store.selectedNet.sourceBlockId ?? '']?.name ?? '?' }}
        → {{ store.blocksById[store.selectedNet.targetBlockId ?? '']?.name ?? '?' }}
      </div>
      <div class="fi-muted">
        {{ store.selectedNet.derived ? t('inspector.derivedReadonly') : t('inspector.manualDesign') }}
      </div>
      <div v-if="store.selectedNet && !store.selectedNet.derived" class="fi-net-edit">
        <button class="fi-btn" data-testid="fi-net-guard" @click="design.agentCachePatch(store.selectedNet!.id)">{{ t('edgeMenu.addGuard') }}</button>
        <button class="fi-btn danger" data-testid="fi-net-del" @click="store.deleteNets([store.selectedNet!.id])">{{ t('inspector.deleteNet') }}</button>
      </div>
    </template>

    <template v-else>
      <div class="fi-head"><span class="fi-title">{{ t('term.softwareCircuit') }}</span></div>
      <div class="fi-muted">{{ t('inspector.emptyHint') }}</div>
    </template>

    <section class="fi-sec fi-validate">
      <button class="fi-btn wide" data-testid="fi-validate" :disabled="store.validating" @click="validateNow">
        {{ store.validating ? t('inspector.validating') : t('inspector.validate') }}
      </button>
      <div v-if="validation" class="fi-status" :class="statusCls">
        <b>{{ validation.status }}</b>
        <span class="mono">
          (expected {{ validation.expectedCallPairs }} / evidence
          {{ validation.evidenceCallPairs }} CALLS)
        </span>
        <ul v-if="validation.unbound.length" class="fi-reasons">
          <li v-for="u in validation.unbound" :key="u.blockId">
            {{ t('term.unbound') }} {{ u.blockName }}: {{ u.reason }}
          </li>
        </ul>
        <ul v-if="validation.missingCalls.length" class="fi-reasons">
          <li v-for="m in validation.missingCalls.slice(0, 5)" :key="m.src + m.dst">
            MISSING CALLS {{ short(m.dst) }}
          </li>
        </ul>
        <ul v-if="validation.unexpectedCalls.length" class="fi-reasons">
          <li v-for="m in validation.unexpectedCalls.slice(0, 5)" :key="m.src + m.dst">
            UNEXPECTED CALLS {{ short(m.dst) }}
          </li>
        </ul>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.fi-port.sel {
  outline: 1px solid var(--accent);
  background: var(--sel-bg);
}
.flow-inspector {
  width: 320px;
  border-left: 1px solid var(--border);
  background: var(--panel);
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fi-head { display: flex; align-items: center; justify-content: space-between; }
.fi-title { font-weight: 600; font-size: 13px; font-family: var(--mono); }
.fi-close { border: none; background: none; cursor: pointer; font-size: 15px; color: var(--text-muted); }
.fi-meta { display: flex; gap: 4px; flex-wrap: wrap; }
.tag {
  font-size: 9px;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--border-strong);
  color: var(--text-muted);
}
.tag.function, .tag.existing { background: var(--ok-bg); color: var(--ok); border-color: var(--ok); }
.tag.composite { background: var(--violet-bg); color: var(--violet-text); border-color: var(--border-strong); }
.tag.proposed { background: var(--danger-bg); color: var(--danger); border-color: var(--danger); }
.tag.modified { background: var(--warn-bg); color: var(--warn); border-color: var(--sugg-border); }
.tag.bound { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); }
.fi-sec { display: flex; flex-direction: column; gap: 5px; }
.fi-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--text-muted); }
.fi-input, .fi-select {
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 12px;
  background: var(--panel);
  color: var(--text);
}
.fi-binding { font-size: 10px; word-break: break-all; color: var(--text); }
.fi-file { font-size: 10px; color: var(--text-muted); }
.fi-ports { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.fi-port { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; font-size: 11px; }
.fi-port-contract { order: 2; flex-basis: 100%; color: var(--text-muted); font-size: 10px; overflow-wrap: anywhere; }
.fi-port-contract summary { cursor: pointer; }
.fi-port-contract > div { margin: 3px 0 3px 12px; }
.fi-x { border: none; background: none; color: var(--text-muted); cursor: pointer; }
.fi-port-form { display: flex; flex-wrap: wrap; gap: 3px; }
.fi-port-form .fi-input { width: 90px; }
.fi-port-form .fi-select { width: 74px; font-size: 11px; }
.fi-btn {
  border: 1px solid var(--border-strong);
  background: var(--panel);
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 11px;
  cursor: pointer;
}
.fi-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.fi-btn.warn { background: var(--warn-bg); border-color: #f0d9ac; color: var(--warn); }
.fi-btn.danger { color: var(--danger); border-color: #f0b9b5; }
.fi-btn.wide { width: 100%; }
.fi-btn:disabled { opacity: 0.5; cursor: default; }
.fi-writeback-row { display: flex; gap: 6px; }
.fi-editor { height: 220px; border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }
.fixture-area {
  width: 100%;
  height: 220px;
  padding: 8px;
  font-size: 11px;
  resize: vertical;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 4px;
}
.fi-preview-ok { font-size: 10px; color: var(--ok); }
.fi-hint { font-size: 10px; color: var(--text-muted); line-height: 1.4; }
.fi-muted { font-size: 11px; color: var(--text-muted); line-height: 1.5; }
.fi-status { margin-top: 6px; font-size: 11px; display: flex; flex-direction: column; gap: 3px; }
.fi-status b { font-size: 14px; }
.fi-status.match b { color: var(--ok); }
.fi-status.stale b { color: var(--warn); }
.fi-status.mismatch b { color: var(--danger); }
.fi-status.unbound b { color: var(--danger); }
.fi-reasons { margin: 0; padding-left: 14px; font-size: 10px; color: var(--text-muted); }
</style>
