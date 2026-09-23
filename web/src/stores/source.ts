import { defineStore } from 'pinia'
import { ref } from 'vue'
import { evidenceApi } from '../api/evidence'
import { isApiError } from '../api/client'
import type { SourceResponse } from '../domain/evidence'
import { useToastStore } from './toast'

interface CachedSource {
  response: SourceResponse
}

export const useSourceStore = defineStore('source', () => {
  const current = ref<SourceResponse | null>(null)
  const error = ref<string | null>(null)
  const loading = ref(false)
  const openPath = ref<string | null>(null)
  const openLine = ref<number | null>(null)
  // SOURCE-SPAN-COL0 §21: optional exact range (1-based, inclusive end column)
  const openSpan = ref<Record<string, any> | null>(null)

  const cache = new Map<string, CachedSource>()
  const toast = useToastStore()

  function cacheKey(repoId: string, path: string, snapshot?: string): string {
    return `${repoId}:${snapshot ?? 'latest'}:${path}`
  }

  async function open(path: string, line: number | null, repoId: string, snapshot?: string,
                      span?: Record<string, any> | null): Promise<void> {
    openSpan.value = span ?? null
    const key = cacheKey(repoId, path, snapshot)
    const cached = cache.get(key)
    if (cached) {
      current.value = cached.response
      error.value = null
      openPath.value = path
      openLine.value = line
      loading.value = false
      return
    }

    loading.value = true
    error.value = null
    openPath.value = path
    openLine.value = line
    current.value = null
    try {
      const res = await evidenceApi.getSource(repoId, path, { snapshot })
      cache.set(key, { response: res })
      current.value = res
      openLine.value = line ?? null
    } catch (err) {
      current.value = null
      if (isApiError(err)) {
        error.value = err.code
        // A 404 source_unavailable must be an explicit error, never a
        // fabricated fallback. Non-network API errors are surfaced in place.
        if (err.isNetwork) toast.reportError(err)
      } else {
        error.value = 'unknown'
        toast.reportError(err)
      }
    } finally {
      loading.value = false
    }
  }

  function clearError(): void {
    error.value = null
  }

  function reset(): void {
    current.value = null
    error.value = null
    openPath.value = null
    openLine.value = null
  }

  return { current, error, loading, openPath, openLine, openSpan, open, clearError, reset }
})
