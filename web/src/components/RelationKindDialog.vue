<script setup lang="ts">
import { ref, watch } from 'vue'
import { RELATION_KINDS } from '../domain/architecture'
import type { RelationKind } from '../domain/architecture'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void; (e: 'confirm', kind: RelationKind, label: string): void }>()

const kind = ref<RelationKind>('DEPENDS_ON')
const label = ref('')

watch(
  () => props.open,
  (o) => {
    if (o) {
      kind.value = 'DEPENDS_ON'
      label.value = ''
    }
  },
)

function confirm() {
  emit('confirm', kind.value, label.value.trim())
}
</script>

<template>
  <div v-if="open" class="dialog-backdrop" @click.self="emit('update:open', false)">
    <div class="dialog rel-dialog" role="dialog" aria-modal="true" aria-label="新建关系">
      <div class="dialog-header">新建关系</div>
      <div class="dialog-body">
        <label class="field">
          <span>关系类型</span>
          <select v-model="kind" class="rl-kind">
            <option v-for="k in RELATION_KINDS" :key="k" :value="k">{{ k }}</option>
          </select>
        </label>
        <label class="field">
          <span>Label（可选）</span>
          <input v-model="label" type="text" placeholder="关系标签" @keyup.enter="confirm" />
        </label>
      </div>
      <div class="dialog-footer">
        <button type="button" @click="emit('update:open', false)">取消</button>
        <button type="button" class="primary rl-submit" @click="confirm">创建</button>
      </div>
    </div>
  </div>
</template>
