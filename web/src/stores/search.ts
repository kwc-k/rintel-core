import { defineStore } from 'pinia'
import { ref } from 'vue'
import { evidenceApi } from '../api/evidence'
import type { SearchResultItem } from '../domain/evidence'
import { useToastStore } from './toast'

const DEBOUNCE_MS = 250

export const useSearchStore = defineStore('search', () => {
  const query = ref('')
  const results = ref<SearchResultItem[]>([])
  const total = ref(0)
  const inFlight = ref(false)

  const toast = useToastStore()

  let debounceTimer: ReturnType<typeof setTimeout> | null = null
  let seq = 0

  function setQuery(q: string): void {
    query.value = q
  }

  async function search(repoId: string, snapshot?: string, opts: { limit?: number; debounce?: boolean } = {}): Promise<void> {
    const q = query.value.trim()
    if (!q) {
      results.value = []
      total.value = 0
      inFlight.value = false
      return
    }
    if (debounceTimer) clearTimeout(debounceTimer)
    if (opts.debounce === false) {
      await runSearch(repoId, snapshot, opts.limit)
      return
    }
    return new Promise<void>((resolve) => {
      debounceTimer = setTimeout(() => {
        runSearch(repoId, snapshot, opts.limit).finally(resolve)
      }, DEBOUNCE_MS)
    })
  }

  async function runSearch(repoId: string, snapshot?: string, limit = 50): Promise<void> {
    const mySeq = ++seq
    inFlight.value = true
    try {
      const res = await evidenceApi.search(query.value.trim(), repoId, { snapshot, limit })
      if (mySeq !== seq) return // a newer search superseded this one
      results.value = res.items
      total.value = res.total
    } catch (err) {
      if (mySeq === seq) {
        results.value = []
        total.value = 0
      }
      toast.reportError(err)
    } finally {
      if (mySeq === seq) inFlight.value = false
    }
  }

  function clear(): void {
    query.value = ''
    results.value = []
    total.value = 0
    inFlight.value = false
    if (debounceTimer) clearTimeout(debounceTimer)
  }

  return { query, results, total, inFlight, setQuery, search, clear }
})
