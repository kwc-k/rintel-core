<script setup lang="ts">
import { ref, watch } from 'vue'
import { isApiError } from '../api/client'
import { useArchStore } from '../stores/arch'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const arch = useArchStore()

const name = ref('')
const error = ref('')
const submitting = ref(false)

watch(
  () => props.open,
  (o) => {
    if (o) {
      name.value = ''
      error.value = ''
      submitting.value = false
    }
  },
)

async function submit() {
  const trimmed = name.value.trim()
  if (!trimmed) {
    error.value = '请输入 Proposal 名称'
    return
  }
  submitting.value = true
  error.value = ''
  try {
    await arch.forkProposal(trimmed)
    emit('update:open', false)
  } catch (err) {
    if (isApiError(err) && (err.status === 422 || err.status === 409)) {
      error.value = err.message
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="open" class="dialog-backdrop" @click.self="emit('update:open', false)">
    <div class="dialog proposal-dialog" role="dialog" aria-modal="true" aria-label="创建 Proposal">
      <div class="dialog-header">创建 Proposal</div>
      <div class="dialog-body">
        <label class="field">
          <span>名称</span>
          <input v-model="name" type="text" class="pp-name" placeholder="例如 Payment Refactor" @keyup.enter="submit" />
        </label>
        <div class="hint muted">将当前 AS-IS 架构复制为 Proposal，并冻结其基线（baseline schema v1）。</div>
        <div v-if="error" class="pp-error" role="alert">{{ error }}</div>
      </div>
      <div class="dialog-footer">
        <button type="button" @click="emit('update:open', false)">取消</button>
        <button type="button" class="primary pp-submit" :disabled="submitting || !name.trim()" @click="submit">
          {{ submitting ? '创建中…' : '创建 Proposal' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hint {
  font-size: 11px;
}
.pp-error {
  color: var(--danger);
  background: var(--danger-bg);
  border: 1px solid #fecaca;
  border-radius: 5px;
  padding: 6px 8px;
  font-size: 12px;
}
</style>
