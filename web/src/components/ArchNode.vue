<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import type { ArchComponent } from '../domain/architecture'

export interface ArchNodeData {
  component: ArchComponent
  files: number
  symbols: number
  /** S4 DIFF overlay badge: 'modified' (~) or 'moved' (↳); null otherwise. */
  diffBadge?: 'modified' | 'moved' | null
}

defineProps<{ id: string; data: ArchNodeData; selected: boolean }>()
</script>

<template>
  <div class="arch-node" :class="[`kind-${data.component.kind}`, { selected }]">
    <Handle type="target" :position="Position.Top" id="target" />
    <span v-if="data.diffBadge" class="node-diff-badge" :class="data.diffBadge">{{ data.diffBadge === 'modified' ? '~' : '↳' }}</span>
    <div class="an-name">{{ data.component.name }}</div>
    <div class="an-kind">{{ data.component.kind }}</div>
    <div class="an-stats">{{ data.files }} files · {{ data.symbols }} symbols</div>
    <Handle type="source" :position="Position.Bottom" id="source" />
  </div>
</template>

<style scoped>
.arch-node {
  width: 200px;
  background: var(--panel);
  border: 1px solid var(--border-strong);
  border-left: 4px solid var(--accent);
  border-radius: 6px;
  padding: 8px 10px;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.08);
  font-size: 12px;
  position: relative;
}
.arch-node.selected {
  outline: 2px solid var(--accent);
}
.an-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.an-kind {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  margin-top: 2px;
}
.an-stats {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
}
.node-diff-badge {
  position: absolute;
  top: -7px;
  right: -7px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: #b45309;
}
.node-diff-badge.modified {
  background: #d97706;
}
.node-diff-badge.moved {
  background: #b45309;
}
/* kind accent colors (light professional palette) */
.kind-component { border-left-color: var(--accent); }
.kind-subsystem { border-left-color: #7c3aed; }
.kind-layer { border-left-color: #0891b2; }
.kind-service { border-left-color: #16a34a; }
.kind-boundary { border-left-color: #d97706; }
.kind-interface { border-left-color: #db2777; }
.kind-datastore { border-left-color: #0f766e; }
.kind-external_system { border-left-color: #64748b; }
.kind-group { border-left-color: #4f46e5; }
</style>
