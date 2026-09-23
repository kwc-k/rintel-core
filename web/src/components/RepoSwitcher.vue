<script setup lang="ts">
import { computed, ref } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
import { useRepoStore } from '../stores/repo'
import { useEvidenceStore } from '../stores/evidence'
import { useSourceStore } from '../stores/source'
import { useArchStore } from '../stores/arch'
import OpenRepoDialog from './OpenRepoDialog.vue'

const workspace = useWorkspaceStore()
const repo = useRepoStore()
const evidence = useEvidenceStore()
const source = useSourceStore()
const arch = useArchStore()

const dialogOpen = ref(false)
const menuOpen = ref(false)
const selecting = ref(false)
const selectionError = ref('')

const options = computed(() => repo.repos.map((r) => ({ value: r.id, label: `${r.id} — ${r.rootPath}` })))
const selectedLabel = computed(() => options.value.find((o) => o.value === workspace.repoId)?.label ?? '选择 repository…')

async function onSelect(id: string) {
  if (!id) return
  selectionError.value = ''
  menuOpen.value = false
  if (!options.value.some((o) => o.value === id)) {
    selectionError.value = `Repository ${id} 不在当前列表中`
    return
  }
  selecting.value = true
  try {
    await repo.loadSnapshots(id)
    workspace.setRepo(id)
    source.reset()
    const latest = repo.snapshots[repo.snapshots.length - 1]?.id ?? null
    workspace.setSnapshotId(latest)
    if (latest) await evidence.fetchOverview(id, latest)
    await arch.ensureWorkspace()
  } catch (err) {
    selectionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    selecting.value = false
  }
}
</script>

<template>
  <div class="repo-switcher" @keydown.esc="menuOpen = false">
    <div class="repo-choice">
      <button type="button" aria-haspopup="listbox" :aria-expanded="menuOpen"
        :disabled="selecting" @click="menuOpen = !menuOpen">{{ selectedLabel }}</button>
      <div v-if="menuOpen" role="listbox" aria-label="Repository choices" class="repo-options">
        <button v-for="o in options" :key="o.value" type="button" role="option"
          :aria-selected="workspace.repoId === o.value"
          :aria-label="`Select repository ${o.value}`" @click="onSelect(o.value)">{{ o.label }}</button>
        <p v-if="!options.length">没有可选 repository</p>
      </div>
    </div>
    <button type="button" @click="dialogOpen = true">添加 repository</button>
    <span v-if="selectionError" role="alert">{{ selectionError }}</span>
    <span class="sr-only" aria-live="polite">{{ workspace.repoId ? `已选择 ${workspace.repoId}` : '未选择 repository' }}</span>
    <OpenRepoDialog v-model:open="dialogOpen" @created="onSelect" />
  </div>
</template>

<style scoped>
.repo-switcher {
  display: flex;
  align-items: center;
  gap: 6px;
}
.repo-choice { position: relative; }
.repo-choice > button {
  font-family: inherit;
  font-size: 12px;
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  padding: 4px 8px;
  max-width: 320px;
  background: var(--panel);
}
.repo-options { position: absolute; top: 100%; left: 0; z-index: 20; min-width: 320px;
  max-height: 320px; overflow: auto; background: var(--panel); border: 1px solid var(--border-strong); }
.repo-options button { display: block; width: 100%; text-align: left; padding: 8px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
</style>
