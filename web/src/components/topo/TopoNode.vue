<script setup lang="ts">
// Software Topology node body (TOPO-UI0).  Rendered inside an X6
// vue-shape node; this component only renders the card.  Suggested nodes
// are visually and semantically distinct from canonical facts (U4):
// amber dashed frame + SUGGESTED badge + score — never shown as canonical.
import { computed } from 'vue'
import { uiText, uiIsZh } from '../../lib/uiText'
import type { SceneNode } from '../../domain/topo'

const props = defineProps<{
  node: any
  graph: any
}>()

const sn = computed<SceneNode>(() => props.node?.data?.scene ?? {})
const selected = computed<boolean>(() => !!props.node?.data?.selected)
const expandable = computed<boolean>(() => !!props.node?.data?.expandable)
const childLabel = computed<string>(() => props.node?.data?.childLabel ?? '')

const kindClass = computed(() => {
  switch (sn.value.kind) {
    case 'module': return 'kind-module'
    case 'suggested-module':
    case 'suggested-flow':
    case 'suggested-composite': return 'kind-suggested'
    case 'file': return 'kind-file'
    case 'function': return 'kind-function'
    case 'resource': return 'kind-resource'
    case 'unknown': return 'kind-unknown'
    case 'external': return 'kind-external'
    default: return 'kind-module'
  }
})

const icon = computed(() => {
  switch (sn.value.kind) {
    case 'module': return '▣'
    case 'suggested-module': return '✦'
    case 'suggested-flow': return '≫'
    case 'suggested-composite': return '⬡'
    case 'file': return '▤'
    case 'function': return 'ƒ'
    case 'resource': return '▣'
    case 'unknown': return '⚡'
    case 'external': return '⧉'
    default: return '●'
  }
})

const kindLabel = computed(() => {
  switch (sn.value.kind) {
    case 'module': return uiText('wb.node.declaredModule')
    case 'suggested-module': return uiText('wb.node.suggestedModule')
    case 'suggested-flow': return uiText('wb.node.suggestedFlow')
    case 'suggested-composite': return uiText('wb.node.suggestedComposite')
    case 'file': return uiText('wb.node.file')
    case 'function': return uiText('wb.node.fn')
    case 'resource': return uiText('wb.node.resource')
    case 'unknown': return uiText('wb.node.unknown')
    case 'external': return uiText('wb.node.external')
    default: return ''
  }
})

const ports = computed<any[]>(() => props.node?.data?.ports ?? [])
const showDataPorts = computed<boolean>(() => props.node?.data?.ports !== undefined)
const showShapes = computed<boolean>(() => !!props.node?.data?.showShapes)
const showSizes = computed<boolean>(() => !!props.node?.data?.showSizes)

function shapeBadge(p: any): string {
  const dims = Array.isArray(p.shape) && p.shape.length ? p.shape.join('×') : '?'
  return `[${dims}]`
}

const nodeSub = computed(() => {
  const s = sn.value.sub
  if (sn.value.kind === 'resource') return uiText('wb.node.resourcePort')
  if (!uiIsZh()) return s
  // counts → Chinese units without touching technical annotations
  return s
    .replace(/(\d+) functions/g, `$1 ${uiText('wb.node.fns')}`)
    .replace(/(\d+) files/g, `$1 ${uiText('wb.node.files')}`)
    .replace(/· (\d+) cross-cutting/g, `· ${uiText('wb.node.cc')} ×$1`)
    .replace(/outside declared modules/g, uiText('wb.node.outsideDeclared'))
    .replace(/no declared container/g, uiText('wb.node.noDeclared'))
    .replace(/outside this file/g, uiText('wb.node.outsideFile'))
    .replace(/no host file/g, uiText('wb.node.noHostFile'))
})
</script>

<template>
  <div class="topo-node" :class="[kindClass, { selected, expandable }]">
    <div class="tn-head">
      <span class="tn-icon">{{ icon }}</span>
      <span class="tn-name" :title="sn.label">{{ sn.label }}</span>
      <span v-if="sn.kind === 'unknown'" class="tn-kchip">{{ uiText('wb.node.unknownChip') }}</span>
      <span v-else-if="sn.badge || sn.suggestionIdx !== undefined" class="tn-kchip sugg">{{ uiText('wb.node.sugg') }}</span>
      <span v-else-if="sn.isCrossCutting" class="tn-kchip cc">{{ uiText('wb.node.cc') }}</span>
    </div>
    <div class="tn-kind">{{ kindLabel }}</div>
    <div class="tn-sub">{{ nodeSub }}</div>
    <div v-if="sn.declRelation" class="tn-rel">{{ sn.declRelation }}</div>
    <div v-if="sn.score !== null && sn.score !== undefined" class="tn-score" :class="{ pos: sn.score > 0 }">
      {{ uiText('wb.node.score') }} {{ sn.score.toFixed(3) }}<span v-if="sn.confidence !== null && sn.confidence !== undefined"> · {{ uiText('wb.node.conf') }} {{ sn.confidence }}</span>
    </div>
    <div v-if="ports.length && showDataPorts" class="tn-ports" data-testid="tn-ports">
      <div v-for="p in ports.slice(0, 6)" :key="p.port_id" class="tn-port"
           :class="`pdir-${p.direction.toLowerCase()}`" :data-testid="`tn-port-${p.name}`">
        <span class="tn-port-name">{{ p.name }}</span>
        <span class="tn-port-type">{{ p.dtype }}<template v-if="p.rank != null">[{{ p.rank }}]</template></span>
        <span v-if="showShapes" class="tn-port-shape" :class="`sh-${p.shape_status.toLowerCase()}`">
          {{ shapeBadge(p) }}</span>
        <span v-if="showSizes && p.byte_size" class="tn-port-size">{{ p.byte_size }}B</span>
      </div>
    </div>
    <div v-if="expandable" class="tn-expand">▸ {{ childLabel }}</div>
  </div>
</template>

<style scoped>
.topo-node {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  padding: 6px 8px;
  border-radius: 8px;
  background: var(--node-bg);
  border: 1.5px solid var(--border-strong);
  font-size: 11px;
  line-height: 1.35;
  color: var(--text);
  overflow: hidden;
  text-align: left;
}
.topo-node.selected {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.tn-head {
  display: flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.tn-icon {
  flex-shrink: 0;
  font-size: 12px;
  width: 14px;
  text-align: center;
}
.tn-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}
.tn-kind {
  font-size: 9px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-top: 2px;
}
.tn-sub {
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tn-kchip {
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.05em;
  border-radius: 4px;
  padding: 1px 4px;
  background: var(--chip-bg);
  color: var(--chip-text);
  flex-shrink: 0;
}
.tn-kchip.sugg {
  background: var(--amber-bg);
  color: var(--amber-text);
}
.tn-kchip.cc {
  background: var(--chip-bg);
  color: var(--chip-text);
}
.tn-rel {
  font-size: 9px;
  color: var(--amber-text);
  margin-top: 2px;
}
.tn-score {
  font-size: 9px;
  color: var(--text-muted);
  margin-top: 2px;
}
.tn-score.pos {
  color: var(--ok);
}
.tn-ports { margin-top: 3px; display: flex; flex-direction: column; gap: 1px; }
.tn-port {
  display: flex; gap: 4px; align-items: center; font-size: 8.5px;
  padding: 0 2px; border-radius: 3px; background: var(--chip-bg);
  border-left: 2px solid var(--border-strong);
}
.tn-port.pdir-input { border-left-color: var(--relation-resource); }
.tn-port.pdir-output, .tn-port.pdir-return { border-left-color: var(--relation-call); }
.tn-port.pdir-inout { border-left-color: var(--relation-state); }
.tn-port.pdir-unknown { border-left-color: var(--text-muted); }
.tn-port-name { color: var(--text-primary); font-weight: 600; }
.tn-port-type { color: var(--text-muted); }
.tn-port-shape {
  margin-left: auto; font-size: 8px; padding: 0 2px; border-radius: 2px;
}
.sh-resolved { background: var(--ok-bg); color: var(--ok); }
.sh-symbolic { background: var(--violet-bg); color: var(--violet-text); }
.sh-partial { background: var(--amber-bg); color: var(--amber-text); }
.sh-unknown { background: var(--chip-bg); color: var(--text-muted); }
.tn-port-size { color: var(--text-muted); font-size: 8px; }
.tn-expand {
  font-size: 9px;
  color: var(--accent);
  margin-top: 3px;
}
/* node kinds */
.kind-module {
  background: var(--module-bg);
  border-color: var(--node-border);
}
.kind-file {
  background: var(--file-bg);
  border-color: var(--node-border);
}
.kind-function {
  background: var(--node-bg);
  border-color: var(--node-border);
}
.kind-resource {
  background: var(--resource-bg);
  border-color: var(--ok);
}
.kind-unknown {
  background: var(--unknown-bg);
  border-color: var(--border-strong);
  border-style: dashed;
  color: var(--text-muted);
}
/* WORKBENCH §13: [External] stub — read-only, muted, outside scope */
.kind-external {
  background: var(--panel-bg);
  border-color: var(--border);
  border-style: dotted;
  color: var(--text-muted);
}
/* Suggested: visually and semantically distinct from canonical facts */
.kind-suggested {
  background: var(--sugg-bg);
  border-color: var(--sugg-border);
  border-style: dashed;
  border-width: 2px;
}
</style>
