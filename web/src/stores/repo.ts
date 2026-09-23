import { defineStore } from 'pinia'
import { ref } from 'vue'
import { evidenceApi } from '../api/evidence'
import { isApiError } from '../api/client'
import type { JobInfo, RepoInfo, SnapshotInfo, TreeItem } from '../domain/evidence'
import { LruCache } from '../lib/lru'
import { useToastStore } from './toast'

const TREE_CACHE_SIZE = 200

export interface TreePage {
  items: TreeItem[]
  total: number
}

export const useRepoStore = defineStore('repo', () => {
  const repos = ref<RepoInfo[]>([])
  const reposLoading = ref(false)
  const snapshots = ref<SnapshotInfo[]>([])
  const snapshotsLoading = ref(false)
  const job = ref<JobInfo | null>(null)
  const jobPolling = ref(false)
  const treeLoading = ref<Set<string>>(new Set())

  const treeCache = new LruCache<string, TreePage>(TREE_CACHE_SIZE)

  let pollTimer: ReturnType<typeof setTimeout> | null = null

  const toast = useToastStore()

  async function loadRepos(): Promise<void> {
    reposLoading.value = true
    try {
      const res = await evidenceApi.listRepos()
      repos.value = res.items
    } catch (err) {
      toast.reportError(err)
      throw err
    } finally {
      reposLoading.value = false
    }
  }

  async function loadSnapshots(repoId: string): Promise<void> {
    snapshotsLoading.value = true
    try {
      const res = await evidenceApi.listSnapshots(repoId)
      snapshots.value = res.items
    } catch (err) {
      toast.reportError(err)
      throw err
    } finally {
      snapshotsLoading.value = false
    }
  }

  async function getTree(repoId: string, path: string, q?: string): Promise<TreePage> {
    const key = `${repoId}::${path}::${q ?? ''}`
    const cached = treeCache.get(key)
    if (cached) return cached

    treeLoading.value = new Set(treeLoading.value).add(key)
    try {
      const page = await evidenceApi.getTree(repoId, { path, q })
      treeCache.set(key, page)
      return page
    } catch (err) {
      toast.reportError(err)
      throw err
    } finally {
      const next = new Set(treeLoading.value)
      next.delete(key)
      treeLoading.value = next
    }
  }

  async function getSymbols(repoId: string, path: string) {
    try {
      return await evidenceApi.getSymbols(repoId, path)
    } catch (err) {
      toast.reportError(err)
      throw err
    }
  }

  function isLoadingTree(repoId: string, path: string): boolean {
    return treeLoading.value.has(`${repoId}::${path}::`)
  }

  function invalidateTree(): void {
    treeCache.clear()
  }

  function stopPolling(): void {
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  async function pollJob(jobId: string, onDone?: (job: JobInfo) => void): Promise<JobInfo> {
    stopPolling()
    jobPolling.value = true
    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const current = await evidenceApi.getJob(jobId)
        job.value = current
        if (current.status === 'done' || current.status === 'failed') {
          jobPolling.value = false
          onDone?.(current)
          return current
        }
        await new Promise<void>((resolve) => {
          pollTimer = setTimeout(resolve, 800)
        })
      }
    } catch (err) {
      jobPolling.value = false
      toast.reportError(err)
      throw err
    }
  }

  async function createRepo(rootPath: string, label?: string): Promise<{ repoId: string; jobId: string }> {
    try {
      const res = await evidenceApi.createRepo(rootPath, label)
      await loadRepos()
      return { repoId: res.repo_id, jobId: res.job_id }
    } catch (err) {
      if (isApiError(err) && err.code !== 'network') toast.push(err.message, 'error')
      else toast.reportError(err)
      throw err
    }
  }

  async function triggerIndex(repoId: string, force = false): Promise<{ jobId: string }> {
    try {
      const res = await evidenceApi.triggerIndex(repoId, force)
      return { jobId: res.job_id }
    } catch (err) {
      toast.reportError(err)
      throw err
    }
  }

  return {
    repos,
    reposLoading,
    snapshots,
    snapshotsLoading,
    job,
    jobPolling,
    loadRepos,
    loadSnapshots,
    getTree,
    getSymbols,
    isLoadingTree,
    invalidateTree,
    stopPolling,
    pollJob,
    createRepo,
    triggerIndex,
  }
})
