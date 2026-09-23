<script setup lang="ts">
import { ref } from 'vue'
import { useRepoStore } from '../stores/repo'
import { useToastStore } from '../stores/toast'
import { evidenceApi } from '../api/evidence'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void; (e: 'created', repoId: string): void }>()

const repo = useRepoStore()
const toast = useToastStore()

const rootPath = ref('')
const label = ref('')
const submitting = ref(false)
const picking = ref(false)

function close() {
  emit('update:open', false)
}

/** Native folder picker on the server host (the browser cannot reveal an
 * absolute local path). Fills the path input; label defaults to the folder
 * name when still empty. */
async function browse() {
  picking.value = true
  try {
    const res = await evidenceApi.pickRepoFolder()
    if (res?.path) {
      const p = res.path.replace(/\/+$/, '')
      rootPath.value = p
      if (!label.value.trim()) label.value = p.split('/').pop() ?? ''
      toast.reportSuccess(`已选择：${p}`)
    } else {
      toast.push('未选择文件夹（或本机无原生选择器）；请手动输入路径', 'info')
    }
  } catch {
    toast.reportError(new Error('无法打开文件夹选择器，请手动输入绝对路径'))
  } finally {
    picking.value = false
  }
}

async function submit() {
  const path = rootPath.value.trim()
  if (!path) return
  submitting.value = true
  try {
    const { repoId, jobId } = await repo.createRepo(path, label.value.trim() || undefined)
    close()
    rootPath.value = ''
    label.value = ''
    toast.push(`已添加 repository，开始索引…`, 'info')
    // SPEC §17: select the repo only after the first index job settles, so
    // the explorer/overview never hit the repo in its un-indexed state
    // (tree 404 → failed panel).  Progress shows in the TopBar meanwhile.
    repo.pollJob(jobId, async (job) => {
      await repo.loadRepos()
      if (job.status === 'done') {
        await repo.loadSnapshots(repoId)
        toast.reportSuccess('索引完成')
      } else if (job.status === 'failed') {
        toast.push('索引失败，保留旧 snapshot', 'error')
      }
      emit('created', repoId)
    }).catch(() => {})
  } catch {
    // reported by the store
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="open" class="dialog-backdrop" @click.self="close">
    <div class="dialog" role="dialog" aria-modal="true" aria-label="添加 repository">
      <div class="dialog-header">添加 repository</div>
      <div class="dialog-body">
        <label class="field">
          <span>根路径（绝对路径）</span>
          <div class="path-row">
            <input v-model="rootPath" type="text" placeholder="/absolute/path/to/repo" @keyup.enter="submit" />
            <button type="button" class="browse-btn" :disabled="picking" @click="browse">
              {{ picking ? '选择中…' : '浏览…' }}
            </button>
          </div>
          <span class="field-hint muted">「浏览…」在服务器本机打开原生文件夹选择器（macOS / Linux / Windows）</span>
        </label>
        <label class="field">
          <span>Label（可选，默认目录名）</span>
          <input v-model="label" type="text" placeholder="repo-label" @keyup.enter="submit" />
        </label>
      </div>
      <div class="dialog-footer">
        <button type="button" @click="close">取消</button>
        <button type="button" class="primary" :disabled="submitting || !rootPath.trim()" @click="submit">
          {{ submitting ? '创建中…' : '创建并索引' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.dialog {
  background: var(--panel);
  border-radius: 8px;
  width: 440px;
  max-width: 90vw;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
}
.dialog-header {
  padding: 12px 16px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
}
.dialog-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-muted);
}
.field input {
  color: var(--text);
}
.path-row {
  display: flex;
  gap: 8px;
}
.path-row input {
  flex: 1;
  min-width: 0;
}
.browse-btn {
  flex-shrink: 0;
  font-weight: 600;
}
.field-hint {
  font-size: 11px;
}
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}
</style>
