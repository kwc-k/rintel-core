<script setup lang="ts">
// UI-REALITY-ALIGN0 §8/§9/A5/A15: the Inspector for the Software Circuit.
//
// Only sections with real facts render (§8: no empty giant panels), everything
// expands progressively (§9), and every value states the authority it came
// from plus its truth class / coverage (UR10).
import { computed } from 'vue'
import { useSelectionStore } from '../../stores/selection'
import { useCircuitStore } from '../../stores/circuit'
import { useWorkbenchStore } from '../../stores/workbench'
import { useLangStore } from '../../stores/lang'
import { edgePlaneLabel, formatShape, spanLabel, precisionInfo } from '../../domain/truth'
import TruthBadge from '../ui/TruthBadge.vue'
import EmptyState from '../ui/EmptyState.vue'
import CollapsibleSection from '../ui/CollapsibleSection.vue'

const selection = useSelectionStore()
const circuit = useCircuitStore()
const workbench = useWorkbenchStore()
const lang = useLangStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const cur = computed(() => selection.current)
const payload = computed<Record<string, any>>(() => cur.value?.payload ?? {})
const socket = computed<Record<string, any> | null>(() => payload.value.socket ?? null)
const edge = computed<Record<string, any> | null>(() => payload.value.edge ?? null)
const nodeInfo = computed<Record<string, any> | null>(
  () => (cur.value?.kind === 'function' && cur.value?.id?.startsWith('node:') ? payload.value : null))

const precision = computed(() => String(cur.value?.span?.precision ?? 'UNKNOWN').toUpperCase())
const spanText = computed(() => spanLabel(cur.value?.span) || (cur.value?.file ? `${cur.value.file}:${cur.value.line ?? '?'}` : ''))
const canOpenSource = computed(() => !!cur.value?.file)

function openSource(): void {
  const c = cur.value
  if (!c?.file) return
  workbench.requestSource({ file: c.file, line: c.line ?? null, span: c.span ?? null,
                            repoId: circuit.data?.repo_id ?? null })
}
function openSocketLine(): void {
  const s = socket.value
  const line = s?.observed?.source_line
  if (!line) return
  const [file, ln] = String(line).split(':')
  workbench.requestSource({ file, line: Number(ln), span: null,
                            repoId: circuit.data?.repo_id ?? null })
}

/** A5: static symbolic shape vs runtime resolved shape are different facts. */
const shapeCompare = computed(() => {
  const s = socket.value
  if (!s) return null
  const staticShape = (s.shape && s.shape.length) ? `[${s.shape}]` : (s.dtype ? String(s.dtype) : null)
  const runtime = s.observed ? s : null
  return {
    direction: s.direction,
    static: staticShape ? `${staticShape} ${s.shape_status ?? ''}`.trim() : null,
    staticStatus: s.shape_status ?? null,
    runtimeShape: runtime && s.shape && s.shape.length ? `[${s.shape}]` : null,
    runtimeDtype: s.dtype ?? null,
    runtimeKind: runtime?.observed?.kind ?? null,
    logical: s.byte_size,
  }
})
</script>

<template>
  <div class="ci" data-testid="circuit-inspector">
    <EmptyState v-if="!cur" tone="empty"
      :title="L('未选择对象', 'nothing selected')"
      :reason="L('点击电路中的节点、端口或边 —— Inspector 只显示当前对象。', 'click a node, socket or edge — the inspector shows only the current object.')"
      :hint="L('其他视角（Architecture / Flow / Data / Runtime / Performance）会保持同一个选择。', 'other perspectives keep the same selection.')" />

    <template v-else>
      <header class="ci-head">
        <div class="ci-title">
          <span class="ci-kind">{{ cur.kind }}</span>
          <span class="ci-name">{{ cur.label }}</span>
        </div>
        <div class="ci-badges">
          <TruthBadge v-if="cur.truth_class" kind="truth" :value="cur.truth_class" compact />
          <TruthBadge v-if="cur.coverage" kind="coverage" :value="cur.coverage" compact />
          <TruthBadge v-if="cur.plane" kind="plane" :value="cur.plane" compact />
        </div>
        <div v-if="cur.detail" class="ci-detail">{{ cur.detail }}</div>
      </header>

      <!-- SOURCE (§21/A16): always the exact range when we have one -->
      <CollapsibleSection v-if="canOpenSource" :title="L('源码范围', 'Source')" testid="ci-source"
                          :hint="precisionInfo(precision).why">
        <div class="ci-row">
          <span class="ci-k">{{ L('位置', 'span') }}</span>
          <span class="ci-v mono">{{ spanText }}</span>
        </div>
        <div class="ci-row">
          <span class="ci-k">{{ L('精度', 'precision') }}</span>
          <span class="ci-v">
            <TruthBadge kind="precision" :value="precision" compact />
            <span class="ci-note">{{ lang.isZh ? precisionInfo(precision).zh : precisionInfo(precision).en }}</span>
          </span>
        </div>
        <div class="ci-row">
          <span class="ci-k">{{ L('证据来源', 'authority') }}</span>
          <span class="ci-v mono">{{ cur.authority ?? cur.span?.evidence?.authority ?? '—' }}</span>
        </div>
        <button type="button" class="ci-btn" data-testid="ci-open-source" @click="openSource">
          {{ precision === 'EXACT' ? L('打开精确范围', 'open exact range') : L('打开该行（无列信息）', 'open line (no columns)') }}
        </button>
      </CollapsibleSection>

      <!-- A5: DATA socket detail — the reason the node editor is worth it -->
      <CollapsibleSection v-if="socket" :title="L('数据上下文', 'Data')" testid="ci-socket-data"
                          :hint="L('静态形状与运行期形状是两个事实', 'static shape and runtime shape are different facts')">
        <div class="ci-row"><span class="ci-k">{{ L('方向', 'direction') }}</span><span class="ci-v">{{ shapeCompare?.direction }}</span></div>
        <div class="ci-row">
          <span class="ci-k">{{ L('静态形状', 'static shape') }}</span>
          <span class="ci-v mono" data-testid="ci-static-shape">
            {{ shapeCompare?.static || L('未建模 / UNKNOWN', 'not modelled / UNKNOWN') }}
            <span class="ci-note">{{ shapeCompare?.static ? 'SYMBOLIC' : '' }}</span>
          </span>
        </div>
        <div class="ci-row">
          <span class="ci-k">{{ L('运行期形状', 'runtime shape') }}</span>
          <span class="ci-v mono" data-testid="ci-runtime-shape">
            <template v-if="shapeCompare?.runtimeShape">{{ shapeCompare.runtimeShape }} OBSERVED <TruthBadge kind="truth" value="OBSERVED" compact /></template>
            <template v-else>{{ L('本次没有观测（≠ 不存在）', 'not observed this run (≠ does not exist)') }}</template>
          </span>
        </div>
        <div class="ci-row"><span class="ci-k">dtype</span><span class="ci-v mono">{{ shapeCompare?.runtimeDtype }}</span></div>
        <div v-if="socket.observed" class="ci-row">
          <span class="ci-k">{{ L('访问', 'access') }}</span>
          <span class="ci-v mono">{{ socket.observed.kind }} · {{ socket.observed.byte_extent }} B ·
            <span class="ci-note">{{ socket.observed.source_line }}</span>
            <TruthBadge v-if="socket.observed.probe" kind="plane" value="PROBE" compact />
          </span>
        </div>
        <div v-if="socket.observed?.note" class="ci-row">
          <span class="ci-k">{{ L('探针站点', 'probe site') }}</span><span class="ci-v mono">{{ socket.observed.note }}</span>
        </div>
        <div class="ci-row">
          <span class="ci-k">logical_size</span>
          <span class="ci-v mono">{{ shapeCompare?.logical ?? '—' }} B
            <span class="ci-note">{{ L('≠ 传输量 / 流量', '≠ transfer volume') }}</span>
          </span>
        </div>
        <button v-if="socket.observed" type="button" class="ci-btn" data-testid="ci-open-socket-line" @click="openSocketLine">
          {{ L('打开观测点源码', 'open the observed site') }}
        </button>
      </CollapsibleSection>

      <!-- Edge: static + observed are both facts, both visible -->
      <CollapsibleSection v-if="edge" :title="L('关系事实', 'Evidence')" testid="ci-edge">
        <div class="ci-row"><span class="ci-k">{{ L('读法', 'reading') }}</span>
          <span class="ci-v">{{ edgePlaneLabel(edge.plane === 'STATIC', edge.observed?.count ?? null) }}</span></div>
        <div class="ci-row"><span class="ci-k">{{ L('静态', 'static') }}</span>
          <span class="ci-v mono">{{ edge.kind }} · confidence {{ edge.confidence ?? '—' }}
            <span class="ci-note">{{ edge.authority }}</span></span></div>
        <div v-if="edge.observed" class="ci-row"><span class="ci-k">{{ L('已观测', 'observed') }}</span>
          <span class="ci-v mono">{{ edge.observed.count }}× · {{ edge.observed.child_symbol }}
            <span v-if="edge.observed.via_macro" class="ci-note">via macro → {{ L('宏展开的 C 符号', 'macro-expanded C symbol') }}</span>
          </span></div>
        <div v-else class="ci-row"><span class="ci-k">{{ L('已观测', 'observed') }}</span>
          <span class="ci-v ci-note">{{ L('本次运行未观测到（≠ 不会发生）', 'not observed this run (≠ cannot happen)') }}</span></div>
        <div v-if="edge.span?.prefix !== undefined" class="ci-row"><span class="ci-k">span</span>
          <span class="ci-v mono">{{ spanLabel(edge.span) }}</span></div>
      </CollapsibleSection>

      <!-- Node: identity + sockets summary + real group memberships -->
      <CollapsibleSection v-if="nodeInfo" :title="L('概览', 'Overview')" testid="ci-node-overview">
        <div class="ci-row"><span class="ci-k">{{ L('定义', 'definition') }}</span>
          <span class="ci-v mono">{{ spanLabel(cur.span) }}</span></div>
        <div v-if="nodeInfo.observed" class="ci-row"><span class="ci-k">{{ L('本次运行', 'this run') }}</span>
          <span class="ci-v mono">{{ nodeInfo.observed.calls }} calls · excl {{ nodeInfo.observed.exclusive_ms }} ms ·
            incl {{ nodeInfo.observed.inclusive_ms }} ms <TruthBadge kind="truth" value="OBSERVED" compact /></span></div>
        <div class="ci-row"><span class="ci-k">{{ L('端口', 'sockets') }}</span>
          <span class="ci-v">{{ nodeInfo.sockets?.length ?? 0 }} · <TruthBadge kind="coverage" :value="cur.coverage" compact /></span></div>
        <div v-for="n in (nodeInfo.notes ?? [])" :key="n" class="ci-warn">⚠ {{ n }}</div>
        <div v-if="(nodeInfo.groups ?? []).length" class="ci-groups">
          <div v-for="g in nodeInfo.groups" :key="g.group_id" class="ci-group"
               :data-group-source="g.source_type">
            <TruthBadge kind="plane" value="ANNOTATION" compact />
            <span>{{ g.label }}</span>
            <span class="ci-note">{{ g.source_type }} · {{ g.truth_class }} · {{ g.origin }}</span>
          </div>
        </div>
      </CollapsibleSection>

      <CollapsibleSection v-if="nodeInfo?.sockets?.length" :title="L('端口明细', 'Data')"
                          testid="ci-node-sockets" :default-open="false">
        <div v-for="s in nodeInfo.sockets" :key="s.socket_id" class="ci-sock"
             :data-socket-plane="s.plane?.startsWith('OBSERVED') ? 'OBSERVED' : 'STATIC'">
          <span class="ci-sock-dir">{{ s.direction }}</span>
          <span class="ci-sock-name">{{ s.name }}</span>
          <span class="ci-sock-type mono">{{ s.dtype }}{{ formatShape(s.shape) }}</span>
          <TruthBadge kind="truth" :value="s.truth_class" compact />
        </div>
      </CollapsibleSection>
    </template>
  </div>
</template>

<style scoped>
.ci { display: flex; flex-direction: column; height: 100%; overflow: auto; font-size: 11px; }
.ci-head { padding: 6px 8px; border-bottom: 1px solid var(--border); }
.ci-title { display: flex; gap: 6px; align-items: baseline; }
.ci-kind { font-size: 9px; text-transform: uppercase; color: var(--text-muted); }
.ci-name { font-weight: 700; font-size: 12.5px; }
.ci-badges { display: flex; gap: 4px; margin-top: 3px; flex-wrap: wrap; }
.ci-detail { margin-top: 2px; color: var(--text-muted); font-size: 10px; }
.ci-row { display: grid; grid-template-columns: 84px 1fr; gap: 4px; padding: 1px 0; align-items: baseline; }
.ci-k { color: var(--text-muted); font-size: 10px; }
.ci-v { word-break: break-word; }
.ci-note { color: var(--text-muted); font-size: 9.5px; margin-left: 4px; }
.ci-btn { margin-top: 5px; border: 1px solid var(--border-strong); background: var(--panel); border-radius: 4px; padding: 2px 7px; font-size: 10.5px; cursor: pointer; }
.ci-btn:hover { border-color: var(--accent); color: var(--accent); }
.ci-warn { color: var(--truth-partial); font-size: 10px; padding: 1px 0; }
.ci-groups { margin-top: 4px; display: flex; flex-direction: column; gap: 3px; }
.ci-group { display: flex; gap: 5px; align-items: center; border-left: 2px solid var(--plane-annotation); padding-left: 5px; }
.ci-sock { display: grid; grid-template-columns: 54px 1fr auto auto; gap: 5px; align-items: center; padding: 1px 0; }
.ci-sock-dir { font-size: 9px; color: var(--text-muted); }
.ci-sock-type { font-size: 9.5px; color: var(--text-muted); }
.mono { font-family: var(--mono); }
</style>
