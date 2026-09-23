<script setup lang="ts">
// UI-REALITY-ALIGN0 §12/A5: Data perspective.
//
// The two facts that must never be blurred:
//   static  : symbolic shape from declarations (may be SYMBOLIC / UNKNOWN)
//   runtime : resolved shape observed in ONE run (may simply be absent)
// plus the classic trap: logical_size is NOT transferred bytes.
import { computed, onMounted, ref, watch } from 'vue'
import { apiFetch } from '../../api/client'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import { useSelectionStore } from '../../stores/selection'
import { useLangStore } from '../../stores/lang'
import { formatShape } from '../../domain/truth'
import TruthBadge from '../ui/TruthBadge.vue'
import EmptyState from '../ui/EmptyState.vue'

const store = useFlowTopologyStore()
const selection = useSelectionStore()
const lang = useLangStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const symbol = ref('BlockPopulation')
const ports = ref<Record<string, any>[]>([])
const shapes = ref<Record<string, any>[]>([])
const bindings = ref<Record<string, any>[]>([])
const capability = ref<Record<string, any> | null>(null)
const interfaces = ref<Record<string, any> | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)

const runtimeObjects = computed(() => store.rtdObjects ?? [])
const portRecords = computed(() => ports.value.flatMap((p) => p.ports ?? []))
const shapeByPort = computed(() => Object.fromEntries(shapes.value.map((s) => [s.port_id, s])))

/** A join is only claimed when the port name matches an observed object name. */
function observedFor(portName: string): Record<string, any> | null {
  return runtimeObjects.value.find((o) => {
    const region = String(o.region ?? '')
    const sym = String(o.canonical_symbol_id ?? '').split('::').slice(-1)[0]
    return region === portName || sym === portName
  }) ?? null
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const q = encodeURIComponent(symbol.value)
    const [p, s, b, cap, fi] = await Promise.all([
      apiFetch<Record<string, any>[]>(`/data-interface/ports?fn=${q}`),
      apiFetch<Record<string, any>[]>(`/data-interface/shapes?fn=${q}`),
      apiFetch<Record<string, any>[]>(`/data-interface/bindings?fn=${q}`),
      apiFetch<Record<string, any>>('/data-interface/capability'),
      apiFetch<Record<string, any>>('/data-interface/flow_interfaces'),
    ])
    ports.value = p ?? []
    shapes.value = s ?? []
    bindings.value = b ?? []
    capability.value = (cap as any)?._per_lane?.fac ?? cap ?? null
    interfaces.value = fi ?? null
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (!store.rtdObjects?.length) { try { await store.loadRuntimeData() } catch { /* absent */ } }
  await load()
})

// §24: switching perspective keeps the selection — follow it when it names a symbol
watch(() => selection.current?.label, (label) => {
  if (label && selection.current?.kind === 'function' && label !== symbol.value) {
    symbol.value = label
    void load()
  }
})
</script>

<template>
  <div class="dp" data-testid="data-pane">
    <header class="dp-ctx" data-testid="data-context">
      <span class="dp-title">{{ L('数据接口', 'Data interfaces') }}</span>
      <input v-model="symbol" class="dp-input mono" data-testid="data-symbol" @keyup.enter="load()" />
      <button type="button" class="dp-btn" data-testid="data-load" @click="load()">{{ L('加载', 'load') }}</button>
      <span class="dp-spacer"></span>
      <TruthBadge kind="coverage" value="PARTIAL" compact />
      <span class="dp-note">{{ L('DATA-INTERFACE0 能力：C 指针 rank/shape 多为 UNKNOWN，端口绑定只覆盖部分调用点。',
                                 'DATA-INTERFACE0 capability: C pointer rank/shape mostly UNKNOWN; port binding covers only some callsites.') }}</span>
    </header>

    <EmptyState v-if="error" tone="unknown" :title="L('数据接口产物不可用', 'data-interface artifacts unavailable')"
                :reason="error" :hint="L('需要 analysis_tournament/data_interface 产物。', 'requires data_interface artifacts')" />

    <div class="dp-body">
      <section class="dp-col">
        <h4>{{ L('静态端口（声明推导 · SYMBOLIC）', 'static ports (from declarations · SYMBOLIC)') }}</h4>
        <EmptyState v-if="!portRecords.length && !loading" tone="unknown"
                    :title="L('该函数没有端口记录', 'no port records for this symbol')"
                    :reason="L('DATA-INTERFACE0 未建模该函数的参数 —— 这是「未建模」，不是「没有参数」。',
                              'DATA-INTERFACE0 did not model this function — that is “not modelled”, not “no parameters”.')" />
        <table v-else class="dp-table" data-testid="data-ports">
          <thead><tr><th>port</th><th>dir</th><th>dtype</th><th>rank</th><th>shape</th><th>bytes</th><th>truth</th><th>cov</th></tr></thead>
          <tbody>
            <tr v-for="p in portRecords" :key="p.port_id">
              <td class="mono">{{ p.name }}</td>
              <td>{{ p.direction }}</td>
              <td class="mono">{{ p.dtype }}</td>
              <td class="num">{{ p.rank ?? '—' }}</td>
              <td class="mono">
                <template v-if="p.shape && p.shape.length">{{ formatShape(p.shape) }}</template>
                <span v-else class="muted">{{ p.shape_status }}</span>
              </td>
              <td class="num">{{ p.byte_size ?? '—' }}</td>
              <td><TruthBadge kind="truth" :value="p.truth_class" compact /></td>
              <td><TruthBadge kind="coverage" :value="p.coverage" compact /></td>
            </tr>
          </tbody>
        </table>
        <h4>{{ L('端口绑定（调用点 → 形参）', 'port bindings (callsite → formal)') }}</h4>
        <EmptyState v-if="!bindings.length" tone="unknown"
                    :title="L('该符号没有绑定记录', 'no bindings for this symbol')"
                    :reason="L('端口绑定只覆盖部分调用点（PORT_BINDING = PARTIAL）。',
                              'port binding covers only some callsites (PORT_BINDING = PARTIAL).')" />
        <table v-else class="dp-table" data-testid="data-bindings">
          <thead><tr><th>caller</th><th>callee</th><th>port</th><th>expr</th><th>line</th><th>kind</th><th>truth</th></tr></thead>
          <tbody>
            <tr v-for="b in bindings" :key="b.binding_id">
              <td class="mono">{{ b.caller_symbol }}</td>
              <td class="mono">{{ b.callee_symbol }}</td>
              <td class="mono">{{ b.callee_port }}</td>
              <td class="mono">{{ b.caller_expr }}</td>
              <td class="num">{{ b.callsite_line }}</td>
              <td>{{ b.kind }}</td>
              <td><TruthBadge kind="truth" :value="b.truth_class" compact /></td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="dp-col">
        <h4>{{ L('运行期观测（本次运行 · RESOLVED）', 'runtime observation (this run · RESOLVED)') }}</h4>
        <div class="dp-warn">
          <TruthBadge kind="coverage" value="PARTIAL" compact />
          {{ L('选择性运行时探针：只包含被插桩的对象，不代表全部数据流。',
               'selective runtime probes: only instrumented objects — not the whole dataflow.') }}
        </div>
        <table class="dp-table" data-testid="data-runtime-objects">
          <thead><tr><th>object</th><th>shape</th><th>dtype</th><th>scope</th><th>bytes</th><th>cov</th></tr></thead>
          <tbody>
            <tr v-for="o in runtimeObjects" :key="o.runtime_data_id">
              <td class="mono">{{ o.region }}</td>
              <td class="mono">{{ formatShape(o.shape) }} <TruthBadge kind="truth" value="OBSERVED" compact /></td>
              <td class="mono">{{ o.dtype }}</td>
              <td>{{ o.scope }}</td>
              <td class="num">{{ o.byte_size }}</td>
              <td><TruthBadge kind="coverage" :value="o.coverage" compact /></td>
            </tr>
          </tbody>
        </table>
        <h4>{{ L('静态 ↔ 运行期对照', 'static ↔ runtime comparison') }}</h4>
        <table class="dp-table" data-testid="data-compare">
          <thead><tr><th>port</th><th>static</th><th>runtime</th></tr></thead>
          <tbody>
            <tr v-for="p in portRecords" :key="`c-${p.port_id}`">
              <td class="mono">{{ p.name }}</td>
              <td class="mono">
                <template v-if="p.shape && p.shape.length">{{ formatShape(p.shape) }} SYMBOLIC</template>
                <template v-else>{{ p.shape_status }}</template>
              </td>
              <td class="mono">
                <template v-if="observedFor(p.name)">{{ formatShape(observedFor(p.name)!.shape) }} OBSERVED</template>
                <template v-else><span class="muted">{{ L('本次未观测', 'not observed this run') }}</span></template>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="dp-note">
          {{ L('logical_size / byte_size 是单个对象的字节数，不是传输量（≠ traffic）。',
               'logical_size / byte_size is per-object size, not transferred volume (≠ traffic).') }}
        </div>
      </section>

      <section class="dp-col">
        <h4>{{ L('能力如实显示（逐项）', 'capability, item by item') }}</h4>
        <table class="dp-table" data-testid="data-capability">
          <tbody>
            <tr v-for="(v, k) in (capability ?? {})" :key="k">
              <td class="mono">{{ k }}</td>
              <td>
                <template v-if="typeof v === 'string'">
                  <TruthBadge kind="coverage" :value="String(v).split(' ')[0]" compact />
                  <span class="muted"> {{ String(v).split(' ').slice(1).join(' ') }}</span>
                </template>
                <template v-else>
                  <div v-for="(vv, kk) in v" :key="kk" class="muted">{{ kk }}: {{ vv }}</div>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
        <h4>{{ L('流区域接口', 'flow-region interfaces') }}</h4>
        <div class="dp-note">
          {{ L('已建模流区域', 'modelled flow regions') }}: <b>{{ Object.keys(interfaces ?? {}).length }}</b>
          {{ L('（每个区域给出其 callees，属于派生投影）', '(each lists its callees; a derived projection)') }}
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.dp { display: flex; flex-direction: column; height: 100%; min-height: 0; background: var(--canvas-bg); font-size: 11px; }
.dp-ctx { display: flex; gap: 8px; align-items: center; padding: 5px 8px; background: var(--panel); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.dp-title { font-weight: 700; }
.dp-input { font-size: 11px; padding: 2px 5px; border: 1px solid var(--border-strong); border-radius: 4px; background: var(--surface); color: var(--text-primary); width: 200px; }
.dp-btn { border: 1px solid var(--border-strong); background: var(--panel); border-radius: 4px; padding: 2px 7px; font-size: 10.5px; cursor: pointer; }
.dp-spacer { flex: 1; }
.dp-note { font-size: 9.5px; color: var(--text-muted); line-height: 1.6; margin-top: 4px; }
.dp-body { flex: 1; min-height: 0; overflow: auto; display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 1px; background: var(--border); }
.dp-col { background: var(--canvas-bg); padding: 6px 8px 12px; }
h4 { margin: 8px 0 4px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted); }
.dp-table { width: 100%; border-collapse: collapse; font-size: 10px; }
.dp-table th { text-align: left; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border); padding: 1px 3px; }
.dp-table td { padding: 1px 3px; border-bottom: 1px solid var(--border); vertical-align: top; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.dp-warn { display: flex; gap: 5px; align-items: baseline; font-size: 10px; color: var(--truth-partial); border: 1px dashed var(--truth-partial); border-radius: 4px; padding: 3px 5px; margin: 3px 0; }
.mono { font-family: var(--mono); }
.muted { color: var(--text-muted); }
</style>
