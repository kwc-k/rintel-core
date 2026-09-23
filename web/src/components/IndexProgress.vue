<script setup lang="ts">
import { computed } from 'vue'
import { useRepoStore } from '../stores/repo'

const repo = useRepoStore()

const visible = computed(() => {
  const j = repo.job
  return j && (j.status === 'pending' || j.status === 'running')
})

const pct = computed(() => Math.max(0, Math.min(100, repo.job?.progress ?? 0)))
</script>

<template>
  <div v-if="visible" class="index-progress" role="progressbar" :aria-valuenow="pct">
    <div class="track">
      <div class="fill" :style="{ width: pct + '%' }"></div>
    </div>
    <span class="muted small">{{ repo.job?.phase ?? '索引中' }} {{ pct }}%</span>
  </div>
</template>

<style scoped>
.index-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 180px;
}
.track {
  flex: 1;
  height: 6px;
  background: var(--hover);
  border-radius: 999px;
  overflow: hidden;
}
.fill {
  height: 100%;
  background: var(--accent);
  transition: width 0.2s ease;
}
.small {
  font-size: 11px;
}
</style>
