<script setup lang="ts">
// UI-REALITY-ALIGN0 §5/§23/§24/A13: the Perspective rail.
//
// One evidence universe, several lenses.  Switching a lens keeps the current
// selection (the rail shows what is selected, so the user never has to
// remember an ID or re-find the object).
import { computed } from 'vue'
import { useWorkbenchStore, type Perspective } from '../../stores/workbench'
import { useSelectionStore } from '../../stores/selection'
import { useCircuitStore } from '../../stores/circuit'
import { useLangStore } from '../../stores/lang'

const wb = useWorkbenchStore()
const selection = useSelectionStore()
const circuit = useCircuitStore()
const lang = useLangStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const ITEMS: Array<{ key: Perspective; glyph: string; zh: string; en: string; hint: string }> = [
  { key: 'architecture', glyph: '▣', zh: '架构', en: 'Architecture', hint: '静态结构（声明模块/文件/函数，只读证据平面）' },
  { key: 'flow', glyph: '⇄', zh: '流程', en: 'Flow', hint: '派生主流程投影 + 人工语义阶段（标注平面）' },
  { key: 'circuit', glyph: '⬡', zh: '节点电路', en: 'Circuit', hint: 'AS-IS 节点/端口/边投影（静态 + 本次运行观测）' },
  { key: 'data', glyph: '⌗', zh: '数据', en: 'Data', hint: '端口/形状/绑定：静态 SYMBOLIC vs 运行期 OBSERVED' },
  { key: 'runtime', glyph: '◉', zh: '运行时', en: 'Runtime', hint: '一次运行的调用/阶段/数据观测（覆盖 PARTIAL）' },
  { key: 'performance', glyph: '⚡', zh: '性能', en: 'Performance', hint: '软件层热点与因果结论（插桩值 != 绝对成本）' },
]

const current = computed(() => selection.current)
function pick(p: Perspective): void {
  wb.setPerspective(p)
  // A13: the circuit lens opens on the current selection when it names a node.
  if (p === 'circuit' && selection.current?.id?.startsWith('node:')) {
    void circuit.load(selection.current.id, circuit.depth)
  }
}
</script>

<template>
  <nav class="pr" data-testid="perspective-rail" :aria-label="L('视角', 'perspectives')">
    <button
      v-for="it in ITEMS" :key="it.key"
      type="button" class="pr-item" :class="{ on: wb.perspective === it.key && wb.tab !== 'circuit' }"
      :data-testid="`persp-${it.key}`" :title="`${L(it.zh, it.en)} — ${it.hint}`"
      @click="pick(it.key)"
    >
      <span class="pr-glyph">{{ it.glyph }}</span>
      <span class="pr-label">{{ L(it.zh, it.en) }}</span>
    </button>

    <div class="pr-sep"></div>
    <div class="pr-sel" data-testid="perspective-selection" :title="current ? `${current.kind}: ${current.label}` : L('未选择', 'nothing selected')">
      <span class="pr-sel-label">{{ L('当前选择', 'selection') }}</span>
      <span v-if="current" class="pr-sel-name">{{ current.label }}</span>
      <span v-else class="pr-sel-none">{{ L('未选择', 'none') }}</span>
    </div>
    <button v-if="selection.history.length" type="button" class="pr-back" data-testid="selection-back"
            :title="L('返回上一个选择', 'back to the previous selection')" @click="selection.goBack()">↩</button>
    <div class="pr-note">{{ L('所有视角共享同一个选择与同一份证据。', 'all lenses share one selection and one evidence universe.') }}</div>
  </nav>
</template>

<style scoped>
.pr {
  display: flex; flex-direction: column; gap: 2px; flex-shrink: 0; width: 92px;
  padding: 6px 4px; background: var(--rail-bg); border-right: 1px solid var(--border);
}
.pr-item {
  display: flex; flex-direction: column; align-items: center; gap: 1px;
  border: 1px solid transparent; border-radius: 5px; background: none;
  padding: 5px 2px; cursor: pointer; color: var(--text-secondary); font-size: 10px;
}
.pr-item:hover { background: var(--hover); }
.pr-item.on { background: var(--sel-bg); border-color: var(--accent); color: var(--sel-text); font-weight: 600; }
.pr-glyph { font-size: 13px; }
.pr-label { font-size: 9.5px; }
.pr-sep { height: 1px; background: var(--border); margin: 5px 3px; }
.pr-sel { display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 2px; text-align: center; }
.pr-sel-label { font-size: 8.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.pr-sel-name { font-size: 10px; font-weight: 600; word-break: break-word; }
.pr-sel-none { font-size: 9.5px; color: var(--text-muted); }
.pr-back { border: 1px solid var(--border); background: var(--surface); border-radius: 4px; cursor: pointer; font-size: 11px; }
.pr-note { margin-top: auto; font-size: 8.5px; color: var(--text-muted); line-height: 1.4; padding: 4px 2px 0; }
</style>
