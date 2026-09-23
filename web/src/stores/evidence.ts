import { defineStore } from 'pinia'
import { ref } from 'vue'
import { evidenceApi } from '../api/evidence'
import type { EvidenceRecord, NeighborhoodResult, StructuralOverview } from '../domain/evidence'
import { LruCache } from '../lib/lru'
import { useToastStore } from './toast'

const EVIDENCE_CACHE_SIZE = 50

export const useEvidenceStore = defineStore('evidence', () => {
  const overview = ref<StructuralOverview | null>(null)
  const overviewLoading = ref(false)

  const evidenceCache = new LruCache<string, EvidenceRecord[]>(EVIDENCE_CACHE_SIZE)
  const evidenceLoading = ref(false)

  const neighborhood = ref<NeighborhoodResult | null>(null)
  const neighborhoodLoading = ref(false)
  const neighborhoodDirection = ref<'in' | 'out' | 'both'>('both')

  const toast = useToastStore()

  async function fetchOverview(repoId: string, snapshot?: string): Promise<StructuralOverview> {
    overviewLoading.value = true
    try {
      const result = await evidenceApi.getStructuralOverview(repoId, { snapshot })
      overview.value = result
      return result
    } catch (err) {
      toast.reportError(err)
      throw err
    } finally {
      overviewLoading.value = false
    }
  }

  async function fetchEvidence(repoId: string, entityType: 'node' | 'edge', entityId: string, snapshot?: string): Promise<EvidenceRecord[]> {
    const key = `${repoId}:${snapshot ?? 'latest'}:${entityId}`
    const cached = evidenceCache.get(key)
    if (cached) return cached

    evidenceLoading.value = true
    try {
      const res = await evidenceApi.getEvidence(entityType, entityId, repoId, snapshot)
      evidenceCache.set(key, res.items)
      return res.items
    } catch (err) {
      toast.reportError(err)
      throw err
    } finally {
      evidenceLoading.value = false
    }
  }

  async function fetchNeighborhood(
    repoId: string,
    node: string,
    opts: { snapshot?: string; depth?: number; nodeBudget?: number; edgeBudget?: number; direction?: 'in' | 'out' | 'both' } = {},
  ): Promise<NeighborhoodResult> {
    neighborhoodLoading.value = true
    neighborhoodDirection.value = opts.direction ?? 'both'
    try {
      const result = await evidenceApi.getNeighborhood(repoId, node, opts)
      neighborhood.value = result
      return result
    } catch (err) {
      toast.reportError(err)
      throw err
    } finally {
      neighborhoodLoading.value = false
    }
  }

  /** Invalidate all cached evidence after an index completes (SPEC §17). */
  function invalidate(): void {
    evidenceCache.clear()
    overview.value = null
    neighborhood.value = null
  }

  return {
    overview,
    overviewLoading,
    evidenceCache,
    evidenceLoading,
    neighborhood,
    neighborhoodLoading,
    neighborhoodDirection,
    fetchOverview,
    fetchEvidence,
    fetchNeighborhood,
    invalidate,
  }
})
