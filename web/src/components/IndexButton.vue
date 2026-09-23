<script setup lang="ts">
import { computed, ref } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
import { useRepoStore } from '../stores/repo'
import { useEvidenceStore } from '../stores/evidence'
import { useToastStore } from '../stores/toast'

const workspace = useWorkspaceStore()
const repo = useRepoStore()
const evidence = useEvidenceStore()
const toast = useToastStore()

const busy = ref(false)

const disabled = computed(() => !workspace.repoId || busy.value || repo.jobPolling)

async function run() {
  if (!workspace.repoId) return
  busy.value = true
  try {
    const { jobId } = await repo.triggerIndex(workspace.repoId)
    await repo.pollJob(jobId, async (job) => {
      if (job.status === 'done') {
        toast.reportSuccess('索引完成')
        // SPEC §17: refresh the snapshot badge + invalidate evidence caches,
        // but never auto-switch the user's snapshot selection.
        await repo.loadSnapshots(workspace.repoId!)
        evidence.invalidate()
      } else {
        toast.push(job.error ? `索引失败：${JSON.stringify(job.error)}` : '索引失败', 'error')
      }
    })
  } catch {
    // already reported by the stores
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <button type="button" :disabled="disabled" :title="disabled ? '无仓库或索引进行中' : '触发增量索引'" @click="run">
    {{ repo.jobPolling ? '索引中…' : '索引' }}
  </button>
</template>
