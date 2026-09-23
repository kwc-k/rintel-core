// Single source of truth for selection/focus + workspace UI state
// (S2-WEB-CONTRACT §4). Panels never keep private selection copies: every
// selection read/write goes through this store.

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { EntityRef } from '../domain/workspace'
import { sameRef } from '../domain/workspace'

export const UI_STATE_KEY = 'rintel.s2.ui'
const PERSIST_DEBOUNCE_MS = 800

export type CenterTab = 'structure' | 'architecture'

/** S4 design mode (SPEC-P1 §13): the canvas editing mode. */
export type ModelMode = 'as_is' | 'to_be' | 'diff'

export interface PersistedUiState {
  repoId: string | null
  snapshotId: string | null
  workspaceId: string | null
  centerTab: CenterTab
  mode: ModelMode
  activeModelId: string | null
  expanded: string[]
  selected: EntityRef[]
  focus: EntityRef | null
  activeTab: string
}

function isEntityRef(value: unknown): value is EntityRef {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    (v.plane === 'evidence' || v.plane === 'arch') &&
    typeof v.entityType === 'string' &&
    typeof v.id === 'string'
  )
}

export const useWorkspaceStore = defineStore('workspace', () => {
  const repoId = ref<string | null>(null)
  const snapshotId = ref<string | null>(null)
  const workspaceId = ref<string | null>(null)
  const centerTab = ref<CenterTab>('structure')
  /** S4: design mode; model kind guards live in the arch store (needs the
   * bootstrap to know the active model kind). */
  const mode = ref<ModelMode>('as_is')
  const activeModelId = ref<string | null>(null)
  const focus = ref<EntityRef | null>(null)
  const selected = ref<EntityRef[]>([])
  const expanded = ref<string[]>([])
  const activeTab = ref('explorer')

  let persistTimer: ReturnType<typeof setTimeout> | null = null

  function serialize(): PersistedUiState {
    return {
      repoId: repoId.value,
      snapshotId: snapshotId.value,
      workspaceId: workspaceId.value,
      centerTab: centerTab.value,
      mode: mode.value,
      activeModelId: activeModelId.value,
      expanded: [...expanded.value],
      selected: selected.value.map((r) => ({ ...r })),
      focus: focus.value ? { ...focus.value } : null,
      activeTab: activeTab.value,
    }
  }

  function persistUiState(): void {
    try {
      localStorage.setItem(UI_STATE_KEY, JSON.stringify(serialize()))
    } catch {
      // storage may be unavailable (private mode / quota) — best-effort only
    }
  }

  function schedulePersist(): void {
    if (persistTimer) clearTimeout(persistTimer)
    persistTimer = setTimeout(persistUiState, PERSIST_DEBOUNCE_MS)
  }

  /** Write the UI state to localStorage immediately (e.g. before leaving a
   * page whose mount-side hydrate() would otherwise restore stale state). */
  function flushPersist(): void {
    if (persistTimer) clearTimeout(persistTimer)
    persistUiState()
  }

  function hydrate(): void {
    let raw: string | null = null
    try {
      raw = localStorage.getItem(UI_STATE_KEY)
    } catch {
      return
    }
    if (!raw) return
    try {
      const s = JSON.parse(raw) as Partial<PersistedUiState>
      if (typeof s.repoId === 'string') repoId.value = s.repoId
      if (typeof s.snapshotId === 'string') snapshotId.value = s.snapshotId
      if (typeof s.workspaceId === 'string' || s.workspaceId === null) workspaceId.value = s.workspaceId
      if (s.centerTab === 'structure' || s.centerTab === 'architecture') centerTab.value = s.centerTab
      if (s.mode === 'as_is' || s.mode === 'to_be' || s.mode === 'diff') mode.value = s.mode
      if (typeof s.activeModelId === 'string' || s.activeModelId === null) activeModelId.value = s.activeModelId
      if (Array.isArray(s.expanded)) expanded.value = s.expanded.filter((x) => typeof x === 'string')
      if (Array.isArray(s.selected)) selected.value = s.selected.filter(isEntityRef)
      focus.value = isEntityRef(s.focus) ? s.focus : null
      if (typeof s.activeTab === 'string') activeTab.value = s.activeTab
    } catch {
      // corrupted state — start fresh
    }
  }

  function setRepo(id: string): void {
    repoId.value = id
    snapshotId.value = null
    workspaceId.value = null
    focus.value = null
    selected.value = []
    expanded.value = []
    schedulePersist()
  }

  function setSnapshotId(id: string | null): void {
    snapshotId.value = id
    schedulePersist()
  }

  function setWorkspaceId(id: string | null): void {
    // Only clear focus/selection when the workspace actually changes:
    // hydration re-resolves an arch focus against the fresh bootstrap, so a
    // no-op assignment must not wipe the focus we are about to restore.
    if (workspaceId.value === id) return
    workspaceId.value = id
    // Workspace switch invalidates ARCH references (components/mappings);
    // an evidence focus (repo-scoped) stays valid.
    if (focus.value?.plane === 'arch') {
      focus.value = null
      selected.value = []
    }
    schedulePersist()
  }

  function setCenterTab(tab: CenterTab): void {
    centerTab.value = tab
    schedulePersist()
  }

  function setMode(m: ModelMode): void {
    mode.value = m
    schedulePersist()
  }

  function setActiveModelId(id: string | null): void {
    activeModelId.value = id
    // Only an ARCH-plane focus goes stale when the active model changes;
    // an evidence focus stays valid (evidence -> arch navigation).
    if (focus.value?.plane === 'arch') {
      focus.value = null
      selected.value = []
    }
    schedulePersist()
  }

  function setActiveTab(tab: string): void {
    activeTab.value = tab
    schedulePersist()
  }

  function setFocus(ref: EntityRef | null): void {
    focus.value = ref
    schedulePersist()
  }

  function isSelected(ref: EntityRef): boolean {
    return selected.value.some((r) => sameRef(r, ref))
  }

  function toggleSelect(ref: EntityRef): void {
    const idx = selected.value.findIndex((r) => sameRef(r, ref))
    if (idx >= 0) {
      selected.value.splice(idx, 1)
    } else {
      // Plane-exclusive selection (P1-DEBT-FIX0): evidence checkboxes and the
      // arch-canvas selection bridge feed the same selection, but the
      // operations on it (batch-create / map-to-component) are evidence-plane
      // only.  A mixed [arch + evidence] selection silently disables every
      // create action and previously degraded batch create to a plain,
      // unmapped component.  Adding a ref of one plane drops refs of the
      // other, so the selection is always a single, unambiguous plane.
      selected.value = selected.value.filter((r) => r.plane === ref.plane)
      selected.value.push({ ...ref })
    }
    schedulePersist()
  }

  function clearSelection(): void {
    if (selected.value.length === 0) return
    selected.value = []
    schedulePersist()
  }

  /** Replace the whole selection (used by the arch canvas node selection —
   *  arch-plane only; always single-plane by construction). */
  function replaceSelection(refs: EntityRef[]): void {
    selected.value = refs.map((r) => ({ ...r }))
    schedulePersist()
  }

  /**
   * Shift-range selection. `refsInVisibleOrder` is the ordered list of
   * selectable entities currently visible, ending at the shift-clicked
   * target. Everything from `anchor` to that target (inclusive) is added
   * to the current selection.
   */
  function selectRange(anchor: EntityRef, refsInVisibleOrder: EntityRef[]): void {
    if (refsInVisibleOrder.length === 0) return
    const anchorIdx = refsInVisibleOrder.findIndex((r) => sameRef(r, anchor))
    if (anchorIdx < 0) return
    const targetIdx = refsInVisibleOrder.length - 1
    const [from, to] = anchorIdx <= targetIdx ? [anchorIdx, targetIdx] : [targetIdx, anchorIdx]
    for (let i = from; i <= to; i++) {
      const r = refsInVisibleOrder[i]
      if (!selected.value.some((x) => sameRef(x, r))) selected.value.push({ ...r })
    }
    schedulePersist()
  }

  function setExpanded(path: string, value: boolean): void {
    if (value) {
      if (!expanded.value.includes(path)) expanded.value.push(path)
    } else {
      expanded.value = expanded.value.filter((p) => p !== path)
    }
    schedulePersist()
  }

  function expandAncestors(path: string): void {
    const parts = path.split('/')
    let acc = ''
    for (let i = 0; i < parts.length - 1; i++) {
      acc = acc ? `${acc}/${parts[i]}` : parts[i]
      if (!expanded.value.includes(acc)) expanded.value.push(acc)
    }
    schedulePersist()
  }

  watch(
    [repoId, snapshotId, workspaceId, centerTab, mode, activeModelId, focus, selected, expanded, activeTab],
    schedulePersist,
  )

  return {
    repoId,
    snapshotId,
    workspaceId,
    centerTab,
    mode,
    activeModelId,
    focus,
    selected,
    expanded,
    activeTab,
    setRepo,
    setSnapshotId,
    setWorkspaceId,
    setCenterTab,
    setMode,
    setActiveModelId,
    setActiveTab,
    setFocus,
    isSelected,
    toggleSelect,
    clearSelection,
    replaceSelection,
    selectRange,
    setExpanded,
    expandAncestors,
    hydrate,
    persistUiState,
    flushPersist,
  }
})
