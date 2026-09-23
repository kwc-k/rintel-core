<script setup lang="ts">
import type { ComponentBlockers } from '../stores/arch'

const props = defineProps<{
  open: boolean
  componentName: string
  blockers: ComponentBlockers
  mappingCount: number
}>()

const emit = defineEmits<{ (e: 'update:open', value: boolean): void; (e: 'confirm'): void }>()
</script>

<template>
  <div v-if="open" class="dialog-backdrop" @click.self="emit('update:open', false)">
    <div class="dialog del-dialog" role="dialog" aria-modal="true" aria-label="删除组件">
      <div class="dialog-header">删除组件</div>
      <div class="dialog-body">
        <p class="del-hint">
          组件「{{ componentName }}」存在 {{ blockers.children.length }} 个子组件、{{ blockers.relations.length }} 条关系、
          {{ mappingCount }} 条映射。删除子树将一并移除这些资产。
        </p>
        <div v-if="blockers.children.length" class="del-block">
          <div class="del-block-title">子组件</div>
          <div v-for="c in blockers.children" :key="c.id" class="muted mono">{{ c.name }}</div>
        </div>
        <div v-if="blockers.relations.length" class="del-block">
          <div class="del-block-title">关系</div>
          <div v-for="r in blockers.relations" :key="r.id" class="muted mono">{{ r.kind }} {{ r.srcId }} → {{ r.dstId }}</div>
        </div>
      </div>
      <div class="dialog-footer">
        <button type="button" class="del-cancel" @click="emit('update:open', false)">取消</button>
        <button type="button" class="primary del-confirm" @click="emit('confirm')">
          删除子树（含 {{ blockers.children.length }} 子组件/{{ blockers.relations.length }} 关系/{{ mappingCount }} 映射）
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.del-hint {
  margin: 0;
  font-size: 12px;
}
.del-block {
  margin-top: 4px;
}
.del-block-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
}
</style>
