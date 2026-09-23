<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspaceStore } from '../stores/workspace'
import { useArchStore } from '../stores/arch'
import { useSourceStore } from '../stores/source'
import { useFlowStore } from '../stores/flow'
import { mappingCounts, mappingEntityKind } from '../domain/architecture'
import type { ArchComponent, ArchMapping, ArchRelation } from '../domain/architecture'

const workspace = useWorkspaceStore()
const arch = useArchStore()
const source = useSourceStore()
const flow = useFlowStore()
const router = useRouter()

type InspectorTab = 'overview' | 'mappings' | 'dependencies' | 'notes'
const tab = ref<InspectorTab>('overview')

const focusId = computed(() =>
  workspace.focus?.plane === 'arch' && workspace.focus.entityType === 'component' ? workspace.focus.id : null,
)

const component = computed<ArchComponent | null>(() => (focusId.value ? arch.componentById(focusId.value) : null))
const parent = computed(() => (component.value?.parentId ? arch.componentById(component.value.parentId) : null))
const children = computed(() =>
  component.value ? (arch.bootstrap?.components.filter((c) => c.parentId === component.value!.id) ?? []) : [],
)
const mappings = computed(() => (component.value ? arch.mappingsForComponent(component.value.id) : []))
const counts = computed(() => mappingCounts(mappings.value))

const relations = computed(() => arch.bootstrap?.relations ?? [])
const incoming = computed(() => (component.value ? relations.value.filter((r) => r.dstId === component.value!.id) : []))
const outgoing = computed(() => (component.value ? relations.value.filter((r) => r.srcId === component.value!.id) : []))

const noteDraft = ref('')
const nameDraft = ref('')
const openFlowBusy = ref(false)
watch(
  component,
  (c) => {
    noteDraft.value = c?.description ?? ''
    nameDraft.value = c?.name ?? ''
    tab.value = 'overview'
  },
  { immediate: true },
)

/** P2-FLOW0 §17: component-scoped Software Circuit from mapped functions. */
async function openComponentFlow() {
  if (!component.value) return
  const workspaceId = workspace.workspaceId
  const modelId = arch.activeModel()?.id ?? null
  if (!workspaceId || !modelId) return
  openFlowBusy.value = true
  try {
    const flowId = await flow.createFromComponent({
      workspaceId, modelId, componentId: component.value.id,
    })
    router.push(`/flows/${flowId}`)
  } catch (err) {
    console.error('open component flow failed', err)
  } finally {
    openFlowBusy.value = false
  }
}

const syncBadge = computed(() => {
  if (arch.syncState === 'saving') return 'Saving…'
  if (arch.syncState === 'failed') return 'Failed'
  return 'Saved'
})

/** S4: rename via the Overview name input (blur commits, .ai-name-input). */
function saveName() {
  const comp = component.value
  if (!comp) return
  const trimmed = nameDraft.value.trim()
  if (!trimmed) {
    nameDraft.value = comp.name
    return
  }
  if (trimmed === comp.name) return
  arch.updateComponent(comp.id, { name: trimmed }).catch(() => {
    nameDraft.value = component.value?.name ?? trimmed
  })
}

async function saveNotes() {
  if (!component.value) return
  try {
    await arch.updateComponent(component.value.id, { description: noteDraft.value })
  } catch {
    // reported by the store
  }
}

/** S4: remove one mapping (mapping change → shows up in the frozen diff). */
function removeMapping(m: ArchMapping) {
  arch.deleteMapping(m.id).catch(() => {})
}

function detach(childId: string) {
  arch.updateComponent(childId, { parentId: null }).catch(() => {})
}

function detachSelf() {
  if (!component.value) return
  arch.updateComponent(component.value.id, { parentId: null }).catch(() => {})
}

function focusParent() {
  if (parent.value) workspace.setFocus({ plane: 'arch', entityType: 'component', id: parent.value.id })
}

function openMapping(m: ArchMapping) {
  const e = m.entity
  if (!e) return
  if (e.path && workspace.repoId) {
    const line = mappingEntityKind(m) === 'FILE' ? 1 : (e.startLine ?? null)
    workspace.setFocus({ plane: 'evidence', entityType: 'node', id: e.id })
    source.open(e.path, line, workspace.repoId, workspace.snapshotId ?? undefined)
  } else if (m.entityType === 'edge') {
    workspace.setFocus({ plane: 'evidence', entityType: 'edge', id: m.entityId })
  }
}

function focusPeer(rel: ArchRelation, direction: 'in' | 'out') {
  const peerId = direction === 'in' ? rel.srcId : rel.dstId
  workspace.setFocus({ plane: 'arch', entityType: 'component', id: peerId })
}

function peerName(id: string): string {
  return arch.componentById(id)?.name ?? id
}

function mappingLocation(m: ArchMapping): string {
  const e = m.entity
  if (!e) return '当前 snapshot 不存在'
  if (!e.path) return m.entityType === 'edge' ? `${mappingEntityKind(m)} ${e.srcId ?? ''} → ${e.dstId ?? ''}` : e.qname || e.name
  const line = mappingEntityKind(m) === 'FILE' ? 1 : (e.startLine ?? '?')
  return `${e.path}:${line}`
}
</script>

<template>
  <div class="panel inspector arch-inspector">
    <div class="panel-header">Architecture Inspector</div>
    <div class="panel-body">
      <div v-if="!component" class="empty">
        <div class="empty-title">未选择组件</div>
        <div>在 Architecture Canvas 中点击一个组件查看详情。</div>
      </div>

      <template v-else>
        <div class="ai-tabs">
          <button
            v-for="t in (['overview', 'mappings', 'dependencies', 'notes'] as InspectorTab[])"
            :key="t"
            type="button"
            class="ai-tab"
            :class="{ active: tab === t }"
            :data-tab="t"
            @click="tab = t"
          >
            {{ t }}
          </button>
        </div>

        <section v-if="tab === 'overview'" class="ai-overview">
          <div class="ai-head">
            <input
              v-model="nameDraft"
              class="ai-name-input"
              :aria-label="`组件名称 ${component.name}`"
              @blur="saveName"
              @keyup.enter="($event.target as HTMLInputElement).blur()"
            />
            <span class="kind">{{ component.kind }}</span>
            <span class="badge" :class="arch.syncState === 'failed' ? 'err' : 'ok'">{{ syncBadge }}</span>
          </div>
          <div class="ai-meta">
            <div v-if="parent" class="ai-line">
              <span class="lbl muted">parent</span>
              <span class="link" @click="focusParent">{{ parent.name }}</span>
              <button type="button" class="ghost" @click="detachSelf">移出</button>
            </div>
            <div class="ai-line">
              <span class="lbl muted">mappings</span>
              <span>{{ counts.files }} files · {{ counts.symbols }} symbols</span>
            </div>
            <div class="ai-line">
              <span class="lbl muted">description</span>
              <span class="muted">{{ component.description || '—' }}</span>
            </div>
            <div class="ai-line">
              <button type="button" class="ai-open-flow" :disabled="openFlowBusy"
                @click="openComponentFlow">
                {{ openFlowBusy ? 'Opening…' : 'Open Flow (Software Circuit)' }}
              </button>
            </div>
          </div>

          <div class="ai-children">
            <div class="ai-section-title">children（{{ children.length }}）</div>
            <div v-if="children.length === 0" class="muted small">（无）</div>
            <div v-for="c in children" :key="c.id" class="row ai-child">
              <span class="name" @click="workspace.setFocus({ plane: 'arch', entityType: 'component', id: c.id })">{{ c.name }}</span>
              <span class="kind">{{ c.kind }}</span>
              <button type="button" class="ghost" @click="detach(c.id)">移出</button>
            </div>
          </div>
        </section>

        <section v-else-if="tab === 'mappings'" class="ai-mappings">
          <div v-if="mappings.length === 0" class="muted small">无映射</div>
          <div
            v-for="m in mappings"
            :key="m.id"
            class="ai-map"
            :class="{ stale: m.stale }"
            :title="m.stale ? '当前 snapshot 不存在' : undefined"
            @click="openMapping(m)"
          >
            <span class="m-kind">{{ mappingEntityKind(m) }}</span>
            <span class="m-name">{{ m.entity?.name ?? m.entity?.qname ?? m.entityId }}</span>
            <span class="m-loc mono muted">{{ mappingLocation(m) }}</span>
            <button
              type="button"
              class="ghost ai-map-del"
              title="移除映射"
              @click.stop="removeMapping(m)"
            >
              ✕
            </button>
          </div>
        </section>

        <section v-else-if="tab === 'dependencies'" class="ai-deps">
          <div class="ai-section-title">Incoming（{{ incoming.length }}）</div>
          <div v-if="incoming.length === 0" class="muted small">（无）</div>
          <div v-for="r in incoming" :key="r.id" class="row ai-rel" @click="focusPeer(r, 'in')">
            <span class="rel-dir">←</span>
            <span class="kind">{{ r.kind }}</span>
            <span class="name">{{ peerName(r.srcId) }}</span>
          </div>

          <div class="ai-section-title">Outgoing（{{ outgoing.length }}）</div>
          <div v-if="outgoing.length === 0" class="muted small">（无）</div>
          <div v-for="r in outgoing" :key="r.id" class="row ai-rel" @click="focusPeer(r, 'out')">
            <span class="rel-dir">→</span>
            <span class="kind">{{ r.kind }}</span>
            <span class="name">{{ peerName(r.dstId) }}</span>
          </div>
        </section>

        <section v-else class="ai-notes">
          <div class="ai-notes-head">
            <span class="badge" :class="arch.syncState === 'failed' ? 'err' : 'ok'">{{ syncBadge }}</span>
          </div>
          <textarea v-model="noteDraft" rows="8" placeholder="组件描述 / 架构师笔记…"></textarea>
          <button type="button" class="primary" @click="saveNotes">保存</button>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.inspector {
  height: 100%;
}
.panel-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 6px;
}
.ai-tab {
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: transparent;
  padding: 4px 8px;
  font-size: 11px;
  text-transform: capitalize;
}
.ai-tab.active {
  border-bottom-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.ai-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ai-head .name {
  font-weight: 600;
}
.ai-name-input {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 13px;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 2px 4px;
  background: transparent;
}
.ai-name-input:hover {
  border-color: var(--border-strong);
  background: var(--panel);
}
.ai-name-input:focus {
  border-color: var(--accent);
  outline: none;
  background: var(--panel);
}
.ai-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.ai-open-flow {
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 5px;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}
.ai-open-flow:disabled { opacity: 0.55; cursor: default; }
.ai-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.lbl {
  width: 64px;
  flex-shrink: 0;
  font-size: 11px;
}
.link {
  color: var(--accent);
  cursor: pointer;
}
.ai-section-title {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin: 4px 0 2px;
}
.ai-children {
  border-top: 1px solid var(--border);
  padding-top: 4px;
}
.ai-child .name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-map {
  display: grid;
  grid-template-columns: 64px 1fr auto;
  gap: 2px 8px;
  padding: 5px 6px;
  border: 1px solid var(--border);
  border-radius: 5px;
  margin-bottom: 4px;
  cursor: pointer;
  align-items: center;
}
.ai-map:hover {
  background: var(--accent-soft);
}
.ai-map.stale {
  opacity: 0.55;
  border-style: dashed;
}
.ai-map-del {
  color: var(--text-muted);
  font-size: 11px;
  padding: 0 2px;
}
.ai-map-del:hover {
  color: var(--danger);
}
.m-kind {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-muted);
}
.m-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-loc {
  grid-column: 1 / -1;
  font-size: 10px;
}
.ai-rel .name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rel-dir {
  color: var(--text-muted);
}
.ai-notes {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-notes-head {
  display: flex;
  align-items: center;
}
textarea {
  width: 100%;
  font-family: inherit;
  font-size: 12px;
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  padding: 6px 8px;
  resize: vertical;
}
.small {
  font-size: 11px;
}
</style>
