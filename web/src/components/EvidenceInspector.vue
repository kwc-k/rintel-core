<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useWorkspaceStore } from '../stores/workspace'
import { useEvidenceStore } from '../stores/evidence'
import { useSourceStore } from '../stores/source'
import { useFlowStore } from '../stores/flow'
import { isApiError } from '../api/client'
import type { EntityRef } from '../domain/workspace'
import type { EvidenceLocation, EvidenceNode, EvidenceRecord } from '../domain/evidence'
import DependenciesTab from './DependenciesTab.vue'

const workspace = useWorkspaceStore()
const evidence = useEvidenceStore()
const source = useSourceStore()
const flow = useFlowStore()
const router = useRouter()

const focus = computed(() => workspace.focus)
const repoId = computed(() => workspace.repoId)

const identityNode = ref<EvidenceNode | null>(null)
const records = ref<EvidenceRecord[]>([])
const loading = ref(false)
const openFlowBusy = ref(false)

/** node kinds that can open as a software circuit (SPEC-P2 §5/§3.2). */
const PROJECTABLE = new Set([
  'FUNCTION', 'METHOD', 'SUBROUTINE', 'PROGRAM', 'INTERFACE',
  'CLASS', 'TYPE', 'MODULE', 'SUBMODULE', 'PACKAGE',
])

const canOpenFlow = computed(() =>
  focus.value?.entityType === 'node' && !!identityNode.value &&
  PROJECTABLE.has(identityNode.value.kind) && !!repoId.value)

async function openAsFlow() {
  if (!identityNode.value || !repoId.value) return
  openFlowBusy.value = true
  try {
    const flowId = await flow.createFromSymbol({
      repoId: repoId.value,
      symbol: identityNode.value.id,
      snapshotId: workspace.snapshotId ?? null,
    })
    router.push(`/flows/${flowId}`)
  } catch (err) {
    console.error('open-as-flow failed', err)
  } finally {
    openFlowBusy.value = false
  }
}

function edgeParts(ref: EntityRef): { kind: string; src: string; dst: string } | null {
  // id = edge:{kind}:{src}:{dst}
  const parts = ref.id.split(':')
  if (parts.length < 4) return null
  return { kind: parts[1], src: parts[2], dst: parts.slice(3).join(':') }
}

async function reload() {
  identityNode.value = null
  records.value = []
  const f = focus.value
  if (!f || !repoId.value) return
  const snapshot = workspace.snapshotId ?? undefined
  if (f.entityType === 'node') {
    loading.value = true
    try {
      const nb = await evidence.fetchNeighborhood(repoId.value, f.id, { snapshot, depth: 0, nodeBudget: 10, edgeBudget: 10 })
      identityNode.value = nb.nodes.find((n) => n.id === f.id) ?? null
      records.value = await evidence.fetchEvidence(repoId.value, 'node', f.id, snapshot)
    } catch {
      // reported by store
    } finally {
      loading.value = false
    }
  } else if (f.entityType === 'edge') {
    loading.value = true
    try {
      records.value = await evidence.fetchEvidence(repoId.value, 'edge', f.id, snapshot)
    } catch {
      // reported by store
    } finally {
      loading.value = false
    }
  }
}

watch([focus, repoId, () => workspace.snapshotId], reload, { immediate: true })

function openLocation(loc: EvidenceLocation | null | undefined) {
  if (!loc?.path || !repoId.value) return
  source.open(loc.path, loc.line ?? loc.startLine ?? null, repoId.value, workspace.snapshotId ?? undefined)
}

function openIdentitySource() {
  if (!identityNode.value?.path || !repoId.value) return
  source.open(identityNode.value.path, identityNode.value.startLine ?? null, repoId.value, workspace.snapshotId ?? undefined)
}

function formatTs(ts: number): string {
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return String(ts)
  }
}

const unresolvedRepoCount = computed(() => evidence.overview?.counts.unresolved ?? null)
</script>

<template>
  <div class="panel inspector">
    <div class="panel-header">Evidence Inspector</div>
    <div class="panel-body">
      <div v-if="!focus" class="empty">
        <div class="empty-title">未选择实体</div>
        <div>在 Explorer / Overview / Search 中点击一个实体查看证据。</div>
      </div>

      <template v-else>
        <!-- identity -->
        <div class="identity">
          <span class="kind">{{ focus.entityType }}</span>
          <span v-if="focus.entityType === 'node' && identityNode" class="name">{{ identityNode.name }}</span>
          <span v-else class="name mono">{{ focus.id }}</span>
        </div>
        <div v-if="focus.entityType === 'node' && identityNode" class="meta">
          <div><span class="lbl muted">qname</span> <code>{{ identityNode.qname }}</code></div>
          <div><span class="lbl muted">kind</span> <code>{{ identityNode.kind }}</code></div>
          <div>
            <span class="lbl muted">位置</span>
            <a v-if="identityNode.path" href="#" class="link" @click.prevent="openIdentitySource">
              {{ identityNode.path }}:{{ identityNode.startLine ?? '?' }}
            </a>
            <span v-else class="muted">—</span>
          </div>
        </div>
        <div v-else-if="focus.entityType === 'edge'" class="meta">
          <div><span class="lbl muted">kind</span> <code>{{ edgeParts(focus)?.kind }}</code></div>
          <div><span class="lbl muted">src</span> <code class="mono">{{ edgeParts(focus)?.src }}</code></div>
          <div><span class="lbl muted">dst</span> <code class="mono">{{ edgeParts(focus)?.dst }}</code></div>
        </div>
        <div v-else class="meta">
          <div><span class="lbl muted">路径</span> <code class="mono">{{ focus.id }}</code></div>
        </div>

        <!-- evidence list -->
        <section v-if="focus.entityType === 'node' || focus.entityType === 'edge'">
          <h4>Evidence（{{ records.length }}）</h4>
          <div v-if="loading" class="muted small">加载证据…</div>
          <div v-else-if="records.length === 0" class="muted small">无证据记录（evidence of absence）</div>
          <div v-for="(r, i) in records" :key="i" class="ev">
            <div class="ev-head">
              <span class="badge ok">{{ r.source }}</span>
              <span class="conf muted">confidence {{ r.confidence }}</span>
              <span class="ts muted">{{ formatTs(r.ts) }}</span>
            </div>
            <div v-if="r.location?.path" class="ev-loc">
              <a href="#" class="link" @click.prevent="openLocation(r.location)">
                {{ r.location.path }}:{{ r.location.line ?? r.location.startLine ?? '?' }}
              </a>
            </div>
            <pre v-if="r.payload && Object.keys(r.payload).length" class="payload mono">{{ JSON.stringify(r.payload, null, 2) }}</pre>
          </div>
        </section>

        <!-- dependencies -->
        <DependenciesTab v-if="focus.entityType === 'node'" :repo-id="repoId!" :node-id="focus.id" />

        <!-- P2-FLOW0 entry: open the symbol as a Software Circuit -->
        <button v-if="canOpenFlow" class="open-flow-btn" :disabled="openFlowBusy"
          @click="openAsFlow">
          {{ openFlowBusy ? 'Opening…' : 'Open as Flow (Software Circuit)' }}
        </button>

        <div v-if="unresolvedRepoCount != null" class="unresolved muted small"
          title="观察到可能需要解析的引用，但当前证据不足以确定目标（unresolved / evidence uncertainty）——不是“缺失证据”的证明。原因分布见 Structural Overview。">
          仓库级 unresolved / 证据不确定引用：{{ unresolvedRepoCount }}（见 Structural Overview）
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.inspector {
  height: 100%;
}
.open-flow-btn {
  width: 100%;
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 5px;
  padding: 5px 8px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}
.open-flow-btn:disabled { opacity: 0.55; cursor: default; }
.panel-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.identity {
  display: flex;
  align-items: center;
  gap: 8px;
}
.name {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.lbl {
  display: inline-block;
  width: 44px;
  font-size: 11px;
}
.meta code {
  font-size: 11px;
}
.link {
  color: var(--accent);
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
h4 {
  margin: 0 0 4px;
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.ev {
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 6px 8px;
  margin-bottom: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ev-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.conf,
.ts {
  font-size: 11px;
}
.ev-loc {
  font-size: 12px;
}
.payload {
  margin: 0;
  font-size: 11px;
  background: var(--surface);
  border-radius: 4px;
  padding: 6px;
  overflow: auto;
  max-height: 160px;
}
.unresolved {
  border-top: 1px solid var(--border);
  padding-top: 6px;
}
.small {
  font-size: 11px;
}
</style>
