// Workbench shell state (WORKBENCH §3): active tab, bottom dock tab,
// resizable panel geometry, active circuit flow.  Pure UI state — nothing
// here mutates canonical evidence, topology or design data.
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export type WorkbenchTab = 'topology' | 'circuit' | 'drc' | 'lvs' | 'synthesis' | 'runtime'
export type DockTab = 'source' | 'search' | 'diagnostics' | 'drc' | 'lvs' | 'synthesis' | 'runtime' | 'agent'
// §23/A13: perspectives are LENSES on one canonical selection and one evidence
// universe — not separate applications.
export type Perspective = 'architecture' | 'flow' | 'circuit' | 'data' | 'runtime' | 'performance'

const LAYOUT_KEY = 'rintel.wb.layout'

function loadLayout(): { left: number; right: number; dock: number } {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (raw) {
      const v = JSON.parse(raw)
      return {
        left: typeof v.left === 'number' ? v.left : 280,
        right: typeof v.right === 'number' ? v.right : 340,
        dock: typeof v.dock === 'number' ? v.dock : 220,
      }
    }
  } catch {
    // ignore
  }
  return { left: 280, right: 340, dock: 220 }
}

export const useWorkbenchStore = defineStore('workbench', () => {
  const tab = ref<WorkbenchTab>('topology')
  const perspective = ref<Perspective>('architecture')
  const density = ref<'normal' | 'compact'>('normal')
  const realityOpen = ref(false)
  const dockTab = ref<DockTab>('source')
  const dockOpen = ref(false)
  const activeFlowId = ref<string | null>(null)

  // §21/A16: any surface can ask the shell to open an EXACT SourceSpan.
  // The shell owns the dock + the source store, so panels never re-implement
  // "open file and let the user search".
  const sourceRequest = ref<{ file: string; line: number | null; repoId?: string | null
                               span?: Record<string, any> | null } | null>(null)
  let sourceReqSeq = 0
  const sourceRequestSeq = ref(0)

  function requestSource(req: { file: string; line?: number | null; repoId?: string | null
                               span?: Record<string, any> | null }): void {
    sourceReqSeq += 1
    sourceRequestSeq.value = sourceReqSeq
    sourceRequest.value = { file: req.file, line: req.line ?? null,
                            repoId: req.repoId ?? null, span: req.span ?? null }
  }

  const leftWidth = ref(loadLayout().left)
  const rightWidth = ref(loadLayout().right)
  const dockHeight = ref(loadLayout().dock)

  const layout = loadLayout()
  leftWidth.value = layout.left
  rightWidth.value = layout.right
  dockHeight.value = layout.dock

  function persistLayout(): void {
    try {
      localStorage.setItem(
        LAYOUT_KEY,
        JSON.stringify({ left: leftWidth.value, right: rightWidth.value, dock: dockHeight.value }),
      )
    } catch {
      // ignore
    }
  }

  // The four verification lanes live in the bottom dock; selecting their
  // top tab focuses the dock (WORKBENCH §3).
  function setTab(next: WorkbenchTab): void {
    tab.value = next
    if (next === 'drc' || next === 'lvs' || next === 'synthesis' || next === 'runtime') {
      dockOpen.value = true
      dockTab.value = next
    }
  }

  function setDockTab(next: DockTab): void {
    dockTab.value = next
    dockOpen.value = true
  }

  function toggleDockTab(next: DockTab): void {
    if (dockOpen.value && dockTab.value === next) {
      dockOpen.value = false
    } else {
      dockTab.value = next
      dockOpen.value = true
    }
  }

  function toggleDock(): void {
    dockOpen.value = !dockOpen.value
  }

  function setPerspective(next: Perspective): void {
    perspective.value = next
    // the AS-IS evidence plane owns the canvas for every lens except the
    // design plane, which has its own tab.
    if (tab.value === 'circuit') tab.value = 'topology'
  }

  function toggleDensity(): void {
    density.value = density.value === 'normal' ? 'compact' : 'normal'
  }

  function setFlow(id: string | null): void {
    activeFlowId.value = id
  }

  const isTopologyContext = computed(() => tab.value === 'topology')
  const isCircuitContext = computed(() => tab.value === 'circuit')

  return {
    tab, dockTab, dockOpen, activeFlowId, sourceRequest, sourceRequestSeq, requestSource,
    perspective, setPerspective, density, toggleDensity, realityOpen,
    leftWidth, rightWidth, dockHeight,
    isTopologyContext, isCircuitContext,
    setTab, setDockTab, toggleDockTab, toggleDock, setFlow, persistLayout,
  }
})
