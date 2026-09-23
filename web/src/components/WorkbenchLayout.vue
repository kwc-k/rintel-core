<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onConnectivityChange } from '../api/client'
import { useWorkspaceStore } from '../stores/workspace'
import { useRepoStore } from '../stores/repo'
import { useEvidenceStore } from '../stores/evidence'
import { useSourceStore } from '../stores/source'
import { useToastStore } from '../stores/toast'
import { useArchStore } from '../stores/arch'
import TopBar from './TopBar.vue'
import OfflineBanner from './OfflineBanner.vue'
import RepositoryExplorer from './RepositoryExplorer.vue'
import StructuralOverviewPanel from './StructuralOverviewPanel.vue'
import EvidenceInspector from './EvidenceInspector.vue'
import ArchitectureCanvas from './ArchitectureCanvas.vue'
import ArchitectureInspector from './ArchitectureInspector.vue'
import DiffPanel from './DiffPanel.vue'
import SourceViewer from './SourceViewer.vue'
import SearchResults from './SearchResults.vue'
import { useSearchStore } from '../stores/search'
import type { SearchResultItem } from '../domain/evidence'

const workspace = useWorkspaceStore()
const repo = useRepoStore()
const evidence = useEvidenceStore()
const source = useSourceStore()
const toast = useToastStore()
const search = useSearchStore()
const arch = useArchStore()

const bottomTab = ref<'source' | 'search'>('source')

const centerTab = computed(() => workspace.centerTab)
const inspectorMode = computed(() =>
  workspace.focus?.plane === 'arch' && workspace.focus.entityType === 'component' ? 'arch' : 'evidence',
)
/** S4: DIFF mode replaces the inspector rail with the frozen-diff panel. */
const showDiffPanel = computed(() => workspace.mode === 'diff')

function onBeforeUnload(): void {
  workspace.persistUiState()
}

onMounted(async () => {
  workspace.hydrate()
  onConnectivityChange((online) => toast.setOffline(!online))
  window.addEventListener('beforeunload', onBeforeUnload)

  try {
    await repo.loadRepos()
  } catch {
    // reported by store
  }

  // Hydrated repo → re-resolve its latest snapshot + overview.
  if (workspace.repoId) {
    try {
      await repo.loadSnapshots(workspace.repoId)
      const latest = repo.snapshots[repo.snapshots.length - 1]?.id ?? null
      if (!workspace.snapshotId) workspace.setSnapshotId(latest)
      await evidence.fetchOverview(workspace.repoId, workspace.snapshotId ?? undefined)
    } catch {
      // reported by store
    }

    // S3: resolve the repo's architecture workspace and load its bootstrap
    // (hydration order §5). The bootstrap re-resolves any restored arch focus.
    try {
      await arch.ensureWorkspace()
    } catch {
      // reported by store
    }
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
  repo.stopPolling()
})

// Auto-open the source dock whenever a source is opened elsewhere.
watch(
  () => source.openPath,
  (path) => {
    if (path) bottomTab.value = 'source'
  },
)

function openSearchResult(item: SearchResultItem): void {
  if (!workspace.repoId) return
  workspace.setFocus({ plane: 'evidence', entityType: 'node', id: item.id })
  source.open(item.path, item.line ?? null, workspace.repoId, workspace.snapshotId ?? undefined)
}
</script>

<template>
  <div class="workbench">
    <TopBar />
    <OfflineBanner />

    <div class="columns">
      <RepositoryExplorer />
      <div class="center-pane">
        <div class="center-tabs">
          <button
            type="button"
            class="center-tab"
            :class="{ active: centerTab === 'structure' }"
            data-tab="structure"
            @click="workspace.setCenterTab('structure')"
          >
            Code Structure
          </button>
          <button
            type="button"
            class="center-tab"
            :class="{ active: centerTab === 'architecture' }"
            data-tab="architecture"
            @click="workspace.setCenterTab('architecture')"
          >
            Architecture
          </button>
        </div>
        <div class="center-body">
          <StructuralOverviewPanel v-if="centerTab === 'structure'" />
          <ArchitectureCanvas v-else />
        </div>
      </div>
      <DiffPanel v-if="centerTab === 'architecture' && showDiffPanel" />
      <EvidenceInspector v-else-if="inspectorMode === 'evidence'" />
      <ArchitectureInspector v-else />
    </div>

    <div class="bottom-dock">
      <div class="dock-tabs">
        <button type="button" :class="{ active: bottomTab === 'source' }" @click="bottomTab = 'source'">源码</button>
        <button type="button" :class="{ active: bottomTab === 'search' }" @click="bottomTab = 'search'">
          搜索<span v-if="search.total">（{{ search.total }}）</span>
        </button>
      </div>
      <div class="dock-body">
        <SourceViewer v-show="bottomTab === 'source'" :repo-id="workspace.repoId" />
        <div v-show="bottomTab === 'search'" class="search-pane">
          <SearchResults v-if="search.results.length > 0" :items="search.results" @open="openSearchResult" />
          <div v-else class="empty">输入关键词开始搜索。</div>
        </div>
      </div>
    </div>

    <div class="toasts">
      <div v-for="t in toast.toasts" :key="t.id" class="toast" :class="t.kind">
        <span>{{ t.message }}</span>
        <button type="button" class="ghost" @click="toast.dismiss(t.id)">×</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.workbench {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}
.columns {
  flex: 1;
  display: grid;
  grid-template-columns: 300px 1fr 360px;
  gap: 8px;
  padding: 8px;
  min-height: 0;
}
.center-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
}
.center-tabs {
  display: flex;
  gap: 4px;
  padding: 2px 4px 6px;
  flex-shrink: 0;
}
.center-tab {
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: transparent;
  padding: 6px 12px;
  font-size: 12px;
}
.center-tab.active {
  border-bottom-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.center-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.center-body > * {
  flex: 1;
  min-height: 0;
}
.bottom-dock {
  height: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--border);
  background: var(--panel);
}
.dock-tabs {
  display: flex;
  gap: 4px;
  padding: 6px 10px 0;
  border-bottom: 1px solid var(--border);
}
.dock-tabs button {
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: transparent;
  padding: 6px 12px;
}
.dock-tabs button.active {
  border-bottom-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.dock-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.search-pane {
  flex: 1;
  overflow: auto;
  padding: 8px;
}
.toasts {
  position: fixed;
  right: 12px;
  bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 200;
}
.toast {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  font-size: 12px;
  max-width: 420px;
}
.toast.success {
  border-left-color: var(--ok);
}
.toast.error {
  border-left-color: var(--danger);
}
.ghost {
  border: none;
  background: transparent;
  padding: 0 2px;
  font-size: 14px;
}
</style>
