<script setup lang="ts">
// Software-circuit block node (SPEC-P2 §6/§11) + TOPO-EDITOR-UX0 §19:
// Evidence / Design / Suggested / Unknown visual distinction (badge + border
// style, color is not the only signal).  Rendered inside an X6 vue-shape
// (props: `node`, `graph` from @antv/x6-vue-shape).
import { computed } from 'vue'
import { uiText } from '../../lib/uiText'
import type { FlowBlock, FlowPort } from '../../domain/flow'
import { displayBlockName, typeDisplay } from '../../domain/flow'

const props = defineProps<{
  node: any
  graph: any
}>()

function onPortClick(portId: string): void {
  // "开始连接" mode from the port context menu: clicking a target port
  // completes the connection (single command path with store.connectPorts).
  void import('../../stores/design').then(({ useDesignStore }) => {
    const design = useDesignStore()
    if (!design.connectingPortId) return
    const from = design.connectingPortId
    design.connectingPortId = ''
    void import('../../stores/flow').then(({ useFlowStore }) => {
      void useFlowStore().connectPorts(from, portId)
    })
  })
}

const block = computed<FlowBlock>(() => props.node?.data?.block ?? {})
const ports = computed<FlowPort[]>(() => props.node?.data?.ports ?? [])
const selected = computed<boolean>(() => !!props.node?.data?.selected)

const ann = computed(() => {
  try {
    return JSON.parse(block.value.metaJson || '{}')?.design ?? {}
  } catch {
    return {}
  }
})
const displayName = computed<string>(() =>
  (ann.value as { displayName?: string }).displayName || displayBlockName(block.value))
const isSuggested = computed<boolean>(() => {
  try {
    return !!JSON.parse(block.value.metaJson || '{}')?.suggested
  } catch {
    return false
  }
})
const isEvidence = computed<boolean>(() => block.value.state === 'existing' && !!block.value.binding)
const isUnknown = computed<boolean>(() => block.value.state === 'existing' && !block.value.binding)

const badge = computed(() => {
  if (isSuggested.value) return { text: 'SUGGESTED', cls: 'sugg' }
  if (block.value.state === 'proposed') return { text: 'DESIGN', cls: 'proposed' }
  if (block.value.state === 'modified') return { text: 'MODIFIED', cls: 'modified' }
  if (isUnknown.value) return { text: 'UNKNOWN', cls: 'unknown' }
  return { text: 'EXISTING', cls: 'existing' }
})

const file = computed(() => block.value.symbol?.path ?? '')
const inputs = computed(() =>
  ports.value
    .filter((p) => p.direction === 'input' && p.semanticKind !== 'control')
    .sort((a, b) => a.positionOrder - b.positionOrder))
const outputs = computed(() =>
  ports.value
    .filter((p) => p.direction === 'output' && p.semanticKind !== 'control')
    .sort((a, b) => a.positionOrder - b.positionOrder))
const isComposite = computed(() => block.value.kind === 'composite')
const isFunction = computed(() => block.value.kind === 'function')
</script>

<template>
  <div
    class="flow-block-node"
    :class="[
      `kind-${block.kind}`,
      {
        selected,
        proposed: block.state === 'proposed',
        modified: block.state === 'modified',
        suggested: isSuggested,
        unknown: isUnknown,
      },
    ]"
  >
    <div class="fb-head">
      <span class="fb-kind">{{ isFunction ? 'ƒ' : isComposite ? '▣' : '◇' }}</span>
      <span class="fb-name" :title="displayName">{{ displayName }}</span>
      <span class="fb-badge" :class="badge.cls" :data-testid="`fb-badge-${block.id}`">{{ badge.text }}</span>
    </div>
    <div v-if="ann.description" class="fb-desc">{{ ann.description }}</div>
    <div v-if="file" class="fb-file">{{ file }}</div>
    <div v-else-if="isUnknown" class="fb-file">?</div>
    <div class="fb-ports">
      <div class="fb-in">
        <span
          v-for="p in inputs" :key="p.id"
          class="fb-port fb-in-port"
          :data-port-id="p.id"
          :data-block-id="block.id"
          @click.stop="onPortClick(p.id)"
        >
          ● {{ p.name }}<em v-if="p.codeType">: {{ typeDisplay(p.codeType) }}</em>
        </span>
      </div>
      <div class="fb-out">
        <span
          v-for="p in outputs" :key="p.id"
          class="fb-port fb-out-port"
          :data-port-id="p.id"
          :data-block-id="block.id"
          @click.stop="onPortClick(p.id)"
        >
          {{ p.name }} ●<em v-if="p.codeType">: {{ typeDisplay(p.codeType) }}</em>
        </span>
      </div>
    </div>
    <div v-if="isComposite" class="fb-composite-hint">┅ {{ uiText('wb.circuit.enterComposite') }}</div>
  </div>
</template>

<style scoped>
.flow-block-node {
  width: 100%;
  height: 100%;
  background: var(--panel);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
  padding: 6px 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  user-select: none;
}
.flow-block-node.kind-composite {
  border: 2px dashed var(--violet-text);
  background: var(--accent-soft);
}
.flow-block-node.kind-object { border-color: var(--topo-state); }
.flow-block-node.proposed {
  border-style: dashed;
  background: var(--amber-bg);
  border-color: var(--warn);
}
.flow-block-node.modified {
  border-color: var(--warn);
  background: var(--sugg-bg);
}
.flow-block-node.suggested {
  border-style: dashed;
  background: var(--sugg-bg);
  border-color: var(--sugg-border);
}
.flow-block-node.unknown { border-color: var(--status-unknown); }
.flow-block-node.selected {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.fb-head { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
.fb-kind { color: var(--text-muted); font-size: 12px; }
.fb-name {
  font-weight: 600;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  font-family: var(--mono);
  color: var(--text-primary);
}
.fb-badge { font-size: 8px; letter-spacing: 0.5px; border-radius: 3px; padding: 1px 4px; }
.fb-badge.existing { background: var(--ok-bg); color: var(--ok); }
.fb-badge.modified { background: var(--warn-bg); color: var(--warn); }
.fb-badge.proposed { background: var(--danger-bg); color: var(--danger); }
.fb-badge.sugg { background: var(--sugg-bg); color: var(--amber-text); }
.fb-badge.unknown { background: var(--chip-bg); color: var(--text-secondary); }
.fb-desc {
  font-size: 9.5px;
  color: var(--text-secondary);
  margin-top: 2px;
  line-height: 1.4;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.fb-file {
  font-size: 10px;
  color: var(--text-muted);
  font-family: var(--mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fb-ports { display: flex; justify-content: space-between; gap: 6px; margin-top: 3px; flex: 1; }
.fb-in, .fb-out { display: flex; flex-direction: column; justify-content: space-evenly; gap: 1px; min-width: 0; }
.fb-port {
  font-size: 9.5px;
  font-family: var(--mono);
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 0 2px;
}
.fb-port em { color: var(--text-muted); font-style: normal; font-size: 8.5px; }
.fb-in-port { align-self: flex-start; }
.fb-out-port { align-self: flex-end; }
.fb-composite-hint { font-size: 8.5px; color: var(--violet-text); margin-top: 2px; }
</style>
