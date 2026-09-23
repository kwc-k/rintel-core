<script setup lang="ts">
import { computed } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
import { useRepoStore } from '../stores/repo'

const workspace = useWorkspaceStore()
const repo = useRepoStore()

const snapshot = computed(() => {
  if (!workspace.snapshotId) return null
  return repo.snapshots.find((s) => s.id === workspace.snapshotId) ?? null
})

const shortId = computed(() => (workspace.snapshotId ? workspace.snapshotId.slice(0, 8) : ''))
</script>

<template>
  <span v-if="workspace.snapshotId" class="snapshot" :title="workspace.snapshotId">
    <span class="badge ok">快照 {{ shortId }}</span>
    <span v-if="snapshot?.commitSha" class="commit mono">{{ snapshot.commitSha.slice(0, 7) }}</span>
    <span v-else class="muted small">working tree</span>
  </span>
  <span v-else class="badge">无快照</span>
</template>

<style scoped>
.snapshot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.commit {
  font-size: 11px;
  color: var(--text-muted);
}
.small {
  font-size: 11px;
}
</style>
