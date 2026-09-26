<script setup lang="ts">
// Software-circuit block node (SPEC-P2 §6/§11) + TOPO-EDITOR-UX0 §19:
// Evidence / Design / Suggested / Unknown visual distinction (badge + border
// style, color is not the only signal).  Rendered inside an X6 vue-shape
// (props: `node`, `graph` from @antv/x6-vue-shape).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { uiText } from '../../lib/uiText'
import type { FlowBlock, FlowPort } from '../../domain/flow'
import { displayBlockName } from '../../domain/flow'
import { layoutNodePorts } from './port-routing'

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
const portLayout = computed(() => layoutNodePorts(block.value, ports.value))
const activePortId = ref<string | null>(null)
const activePort = computed(() => ports.value.find((port) => port.id === activePortId.value))
const popoverX = ref(0)
const popoverY = ref(0)
let portTrigger: HTMLElement | null = null

function closePortInfo(returnFocus = false): void {
  activePortId.value = null
  document.removeEventListener('pointerdown', onOutsidePointer, true)
  window.removeEventListener('keydown', onPopoverKeydown)
  window.removeEventListener('resize', onViewportMove)
  window.removeEventListener('scroll', onViewportMove, true)
  if (returnFocus) portTrigger?.focus()
  portTrigger = null
}

function onOutsidePointer(event: PointerEvent): void {
  const target = event.target
  const popover = document.getElementById(`port-info-${block.value.id}`)
  if (target instanceof Node && (portTrigger?.contains(target) || popover?.contains(target))) return
  closePortInfo()
}

function onPopoverKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closePortInfo(true)
}

function onViewportMove(): void {
  if (!portTrigger || !portTrigger.isConnected) { closePortInfo(); return }
  const rect = portTrigger.getBoundingClientRect()
  popoverX.value = Math.max(8, Math.min(rect.right + 8, window.innerWidth - 296))
  popoverY.value = Math.max(8, Math.min(rect.top, window.innerHeight - 230))
}

function openPortInfo(port: FlowPort, event: MouseEvent): void {
  if (activePortId.value === port.id) { closePortInfo(); return }
  closePortInfo()
  const trigger = event.currentTarget as HTMLElement
  portTrigger = trigger
  onViewportMove()
  activePortId.value = port.id
  document.addEventListener('pointerdown', onOutsidePointer, true)
  window.addEventListener('keydown', onPopoverKeydown)
  window.addEventListener('resize', onViewportMove)
  window.addEventListener('scroll', onViewportMove, true)
}

watch(activePort, (port) => { if (!port && activePortId.value) closePortInfo() })
onBeforeUnmount(() => closePortInfo())

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
const inputs = computed(() => portLayout.value.inputs)
const outputs = computed(() => portLayout.value.outputs)
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
      <span class="fb-name" :title="displayName"><span v-if="block.displayAddress" class="fb-address">{{ block.displayAddress }} · </span>{{ displayName }}</span>
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
          :style="{ top: `${portLayout.points[p.id]?.y ?? 44}px` }"
          @click.stop="onPortClick(p.id)"
        >
          <span v-if="p.portContract">IN{{ p.portContract.ordinal }} · </span>
          <button type="button" class="fb-port-name" :data-testid="`port-name-${p.id}`"
                  :aria-expanded="activePortId === p.id" :aria-controls="`port-info-${block.id}`"
                  aria-haspopup="dialog" @pointerdown.stop @mousedown.stop
                  @click.stop="openPortInfo(p, $event)">{{ p.name }}</button>
        </span>
      </div>
      <div class="fb-out">
        <span
          v-for="p in outputs" :key="p.id"
          class="fb-port fb-out-port"
          :data-port-id="p.id"
          :data-block-id="block.id"
          :style="{ top: `${portLayout.points[p.id]?.y ?? 44}px` }"
          @click.stop="onPortClick(p.id)"
        >
          <span v-if="p.portContract">OUT{{ p.portContract.ordinal }} · </span>
          <button type="button" class="fb-port-name" :data-testid="`port-name-${p.id}`"
                  :aria-expanded="activePortId === p.id" :aria-controls="`port-info-${block.id}`"
                  aria-haspopup="dialog" @pointerdown.stop @mousedown.stop
                  @click.stop="openPortInfo(p, $event)">{{ p.name }}</button>
        </span>
      </div>
    </div>
    <div v-if="isComposite" class="fb-composite-hint">┅ {{ uiText('wb.circuit.enterComposite') }}</div>
  </div>
  <Teleport to="body">
    <section v-if="activePort" :id="`port-info-${block.id}`" class="fb-port-popover"
             data-testid="port-type-popover" role="dialog"
             :aria-label="`Port ${activePort.name} type details`"
             :style="{ left: `${popoverX}px`, top: `${popoverY}px` }">
      <div class="fb-popover-title">{{ activePort.displayAddress || activePort.name }}</div>
      <div>Declared code type (Design): {{ activePort.codeType || 'UNKNOWN' }}</div>
      <div>Expected generic type (Design): {{ activePort.portContract?.generic_type ?? 'UNKNOWN' }}</div>
      <div>Expected dtype (Design): {{ activePort.portContract?.dtype ?? 'UNKNOWN' }}</div>
      <div>Expected shape (Design): {{ activePort.portContract?.shape ?? 'UNKNOWN' }}</div>
      <div>Actual: {{ activePort.expectedActual?.actual.status ?? 'UNKNOWN' }}</div>
      <div class="fb-popover-muted">{{ activePort.expectedActual?.actual.reason ?? 'no_bound_port_evidence' }}</div>
      <div class="fb-popover-id">port_id: {{ activePort.id }}</div>
    </section>
  </Teleport>
</template>

<style scoped>
.flow-block-node {
  position: relative;
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
.fb-address { color: var(--text-muted); font-weight: 500; }
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
.fb-ports { flex: 1; }
.fb-in, .fb-out { display: contents; }
.fb-port {
  position: absolute;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
  max-width: 46%;
  font-size: 9.5px;
  font-family: var(--mono);
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 0 2px;
}
.fb-in-port { left: 8px; }
.fb-out-port { right: 8px; }
.fb-port-name {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.fb-port-name:hover { text-decoration: underline; }
.fb-port-name:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.fb-port-popover {
  position: fixed;
  z-index: 5000;
  width: min(280px, calc(100vw - 16px));
  padding: 10px 12px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--panel);
  color: var(--text-primary);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  font: 11px/1.5 var(--mono);
  overflow-wrap: anywhere;
}
.fb-popover-title { font-weight: 700; margin-bottom: 6px; }
.fb-popover-muted, .fb-popover-id { color: var(--text-muted); }
.fb-composite-hint { font-size: 8.5px; color: var(--violet-text); margin-top: 2px; }
</style>
