<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ARCH_KINDS } from '../domain/architecture'
import type { ArchKind } from '../domain/architecture'
import { isApiError } from '../api/client'
import { useArchStore } from '../stores/arch'
import { useWorkspaceStore } from '../stores/workspace'
import { evidenceEntityOf } from '../domain/workspace'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const arch = useArchStore()
const workspace = useWorkspaceStore()

const name = ref('')
const kind = ref<ArchKind>('component')
const error = ref('')
const submitting = ref(false)

/** S4: without an evidence selection the dialog creates a plain component
 * (POST /components) instead of the S3 batch-with-mappings flow. */
const hasEvidence = computed(() =>
  workspace.selected.length > 0 && workspace.selected.every((r) => evidenceEntityOf(r) !== null),
)

/** P1-DEBT-FIX0: a selection that mixes evidence refs with non-evidence refs
 * is an ambiguous intent — never silently downgrade it to a plain (unmapped)
 * component.  The dialog surfaces the conflict instead. */
const hasPartialEvidence = computed(() =>
  workspace.selected.some((r) => evidenceEntityOf(r) !== null) &&
  workspace.selected.some((r) => evidenceEntityOf(r) === null),
)

watch(
  () => props.open,
  (o) => {
    if (o) {
      name.value = ''
      kind.value = 'component'
      error.value = ''
      submitting.value = false
    }
  },
)

async function submit() {
  const trimmed = name.value.trim()
  if (!trimmed) {
    error.value = '请输入组件名称'
    return
  }
  submitting.value = true
  error.value = ''
  try {
    if (hasEvidence.value) {
      await arch.createComponentBatch(trimmed, kind.value)
    } else if (hasPartialEvidence.value) {
      // P1-DEBT-FIX0: the user checked evidence entities but the selection
      // also carries non-evidence refs — creating a plain component here
      // would silently drop the intended mappings (the empty-impact bug).
      error.value = '选择中包含非 Evidence 实体，无法创建映射组件；请清空后仅选择 Evidence 实体'
      return
    } else {
      await arch.createComponent(trimmed, kind.value)
    }
    emit('update:open', false)
  } catch (err) {
    // 422/409 → inline error, dialog stays open. Other failures are already
    // surfaced by the store (toast/offline banner) and the dialog stays open.
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
    <div class="dialog create-comp-dialog" role="dialog" aria-modal="true" aria-label="创建 Component">
      <div class="dialog-header">创建 Component</div>
      <div class="dialog-body">
        <label class="field">
          <span>名称</span>
          <input v-model="name" type="text" class="cc-name" placeholder="Component name" @keyup.enter="submit" />
        </label>
        <label class="field">
          <span>类型</span>
          <select v-model="kind" class="cc-kind">
            <option v-for="k in ARCH_KINDS" :key="k" :value="k">{{ k }}</option>
          </select>
        </label>
        <div v-if="error" class="cc-error" role="alert">{{ error }}</div>
      </div>
      <div class="dialog-footer">
        <button type="button" @click="emit('update:open', false)">取消</button>
        <button type="button" class="primary cc-submit" :disabled="submitting || !name.trim()" @click="submit">
          {{ submitting ? '创建中…' : '创建 Component' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cc-error {
  color: var(--danger);
  background: var(--danger-bg);
  border: 1px solid #fecaca;
  border-radius: 5px;
  padding: 6px 8px;
  font-size: 12px;
}
</style>
