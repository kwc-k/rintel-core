<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listChangeWorkspaces, openDesignChange } from '../api/design-lifecycle'
import type { ChangeWorkspaceSummary } from '../api/design-lifecycle'

const changes = ref<ChangeWorkspaceSummary[]>([])
const error = ref('')
const router = useRouter()
const repoId = ref('')
const baseRevision = ref('')
const intent = ref('')
const opening = ref(false)

async function openChange(): Promise<void> {
  opening.value = true
  error.value = ''
  try {
    const change = await openDesignChange({
      repo_id: repoId.value.trim(), base_canonical_revision: baseRevision.value.trim(),
      intent: intent.value.trim(), scope: {}, acceptance_criteria: [], actor: 'human:ui',
    })
    await router.push(`/changes/${encodeURIComponent(change.id)}`)
  } catch (err) { error.value = err instanceof Error ? err.message : String(err) }
  finally { opening.value = false }
}
onMounted(async () => {
  try { changes.value = await listChangeWorkspaces() }
  catch (err) { error.value = err instanceof Error ? err.message : String(err) }
})
</script>

<template>
  <main class="change-list">
    <RouterLink to="/">← rintel Workbench</RouterLink>
    <h1>Design Changes</h1>
    <p>每项设计改动有自己的基线、设计修订与观察。选择一项进入 Change Workspace。</p>
    <form class="open-change" @submit.prevent="openChange">
      <h2>Open Change</h2>
      <label>Repository ID <input v-model="repoId" required autocomplete="off" /></label>
      <label>Canonical revision <input v-model="baseRevision" required autocomplete="off" /></label>
      <label>Intent <input v-model="intent" required /></label>
      <button type="submit" :disabled="opening">{{ opening ? 'Opening…' : 'Open Change' }}</button>
      <p>服务端验证 repository 与 canonical revision；新 Change 使用空 scope，后续设计修订必须经过 DesignLifecycle。</p>
    </form>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else-if="!changes.length">没有可显示的 DesignChange。</p>
    <RouterLink v-for="change in changes" :key="change.id"
      :to="`/changes/${encodeURIComponent(change.id)}`" class="change-row">
      <strong>{{ change.intent }}</strong>
      <span>{{ change.id }} · {{ change.repo_id }} · {{ change.state }}</span>
      <small>AS-IS {{ change.baseline_revision }} · TO-BE {{ change.design_revision }} · ACTUAL {{ change.actual_revision ?? 'UNAVAILABLE' }}</small>
    </RouterLink>
  </main>
</template>

<style scoped>
.change-list { padding: 32px; max-width: 950px; margin: auto; color: var(--text); }
.change-row { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; padding: 18px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; color: var(--text); text-decoration: none; }
.change-row small, .change-row span { color: var(--text-muted); }
.open-change { display: grid; gap: 10px; padding: 16px; background: var(--panel); border: 1px solid var(--border); }
.open-change label { display: grid; gap: 4px; }
.open-change input { color: var(--text); background: var(--panel); border: 1px solid var(--border-strong); padding: 6px; }
</style>
