// UI-REALITY-ALIGN0 §7/§24/A13: ONE "Current Selection" for the workbench.
//
// Perspectives (Architecture / Flow / Data / Runtime / Performance / Circuit)
// are lenses on the SAME selection — switching a lens must not lose it.
// This store is UI state only: it never mutates canonical evidence, and it is
// the single place a "what am I looking at?" answer lives.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Plane } from '../domain/truth'

export type SelectionKind =
  | 'function' | 'file' | 'module' | 'flow-region' | 'semantic-stage'
  | 'data-port' | 'data-object' | 'runtime-invocation' | 'perf-finding'
  | 'source-span' | 'graph-node' | 'graph-edge' | 'socket' | 'circuit-edge' | 'agent-task'

export interface SelectionItem {
  kind: SelectionKind
  id: string
  label: string
  detail?: string
  file?: string | null
  line?: number | null
  end_line?: number | null
  span?: Record<string, any> | null
  plane?: Plane
  truth_class?: string
  coverage?: string
  authority?: string
  payload?: Record<string, any>
  at: number
}

const MAX_HISTORY = 40

export const useSelectionStore = defineStore('selection', () => {
  const current = ref<SelectionItem | null>(null)
  const history = ref<SelectionItem[]>([])

  function set(item: Omit<SelectionItem, 'at'> | null): void {
    if (!item) { current.value = null; return }
    const prev = current.value
    // re-selecting the same object must not grow history (dead-end clicks)
    if (prev && prev.kind === item.kind && prev.id === item.id) {
      current.value = { ...prev, ...item, at: Date.now() }
      return
    }
    if (prev) {
      history.value = [...history.value.slice(-(MAX_HISTORY - 1)), prev]
    }
    current.value = { ...item, at: Date.now() }
  }

  function clear(): void { current.value = null }

  const back = computed(() => history.value.length > 0)
  function goBack(): SelectionItem | null {
    const prev = history.value[history.value.length - 1] ?? null
    if (!prev) return null
    history.value = history.value.slice(0, -1)
    current.value = { ...prev, at: Date.now() }
    return current.value
  }

  /** §7: the semantic scope a lens should open on (never an ID the user types). */
  const scopeSymbol = computed(() => {
    const c = current.value
    if (!c) return null
    if (c.kind === 'function' || c.kind === 'graph-node') return c.label
    if (c.payload?.symbol) return String(c.payload.symbol)
    return null
  })

  function seedFromSymbol(name: string, extra: Partial<SelectionItem> = {}): void {
    set({ kind: 'function', id: extra.id ?? `fn:${name}`, label: name, ...extra })
  }

  return { current, history, set, clear, back, goBack, scopeSymbol, seedFromSymbol }
})
