<script setup lang="ts">
import { ref, watch } from 'vue'
import { useEvidenceStore } from '../stores/evidence'
import { useWorkspaceStore } from '../stores/workspace'
import { useSourceStore } from '../stores/source'
import type { EvidenceNode, NeighborhoodResult } from '../domain/evidence'

const props = defineProps<{ repoId: string; nodeId: string }>()

const evidence = useEvidenceStore()
const workspace = useWorkspaceStore()
const source = useSourceStore()

const incoming = ref<NeighborhoodResult | null>(null)
const outgoing = ref<NeighborhoodResult | null>(null)
const loading = ref(false)

function neighbors(result: NeighborhoodResult | null): EvidenceNode[] {
  if (!result) return []
  return result.nodes.filter((n) => n.id !== props.nodeId)
}

async function load() {
  loading.value = true
  incoming.value = null
  outgoing.value = null
  try {
    const snapshot = workspace.snapshotId ?? undefined
    const [inn, out] = await Promise.all([
      evidence.fetchNeighborhood(props.repoId, props.nodeId, { snapshot, depth: 1, direction: 'in', nodeBudget: 60, edgeBudget: 300 }),
      evidence.fetchNeighborhood(props.repoId, props.nodeId, { snapshot, depth: 1, direction: 'out', nodeBudget: 60, edgeBudget: 300 }),
    ])
    incoming.value = inn
    outgoing.value = out
  } catch {
    // reported by store
  } finally {
    loading.value = false
  }
}

watch(() => [props.repoId, props.nodeId], load, { immediate: true })

function openNode(n: EvidenceNode) {
  workspace.setFocus({ plane: 'evidence', entityType: 'node', id: n.id })
  if (n.path) source.open(n.path, n.startLine ?? null, props.repoId, workspace.snapshotId ?? undefined)
}
</script>

<template>
  <div class="deps">
    <div class="deps-title">依赖（in / out）</div>
    <div v-if="loading" class="muted small">加载邻域…</div>
    <div v-else class="cols">
      <div class="col">
        <div class="col-title muted">Incoming（{{ neighbors(incoming).length }}）</div>
        <div v-if="neighbors(incoming).length === 0" class="muted small">—</div>
        <div v-for="n in neighbors(incoming)" :key="n.id" class="row dep-row" @click="openNode(n)">
          <span class="kind">{{ n.kind }}</span>
          <span class="name">{{ n.name }}</span>
          <span class="mono muted small">{{ n.path }}</span>
        </div>
      </div>
      <div class="col">
        <div class="col-title muted">Outgoing（{{ neighbors(outgoing).length }}）</div>
        <div v-if="neighbors(outgoing).length === 0" class="muted small">—</div>
        <div v-for="n in neighbors(outgoing)" :key="n.id" class="row dep-row" @click="openNode(n)">
          <span class="kind">{{ n.kind }}</span>
          <span class="name">{{ n.name }}</span>
          <span class="mono muted small">{{ n.path }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.deps {
  margin-top: 4px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}
.deps-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-bottom: 6px;
}
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.col-title {
  font-size: 10px;
  text-transform: uppercase;
  margin-bottom: 2px;
}
.dep-row {
  gap: 4px;
}
.name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.small {
  font-size: 10px;
  flex-shrink: 0;
}
</style>
