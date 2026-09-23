<script setup lang="ts">
// §25: bottom-dock Runtime TIMELINE (cross-object, sustained view).
// Object detail belongs to the Runtime perspective, not to the dock.
import { computed, onMounted } from 'vue'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import { useWorkbenchStore } from '../../stores/workbench'
import { useLangStore } from '../../stores/lang'
import TruthBadge from '../ui/TruthBadge.vue'

const store = useFlowTopologyStore()
const wb = useWorkbenchStore()
const lang = useLangStore()
const L = (zh: string, en: string): string => (lang.isZh ? zh : en)

const run = computed(() => store.runtimeRuns.find((r) => r.run_id === store.selectedRun) ?? null)
const stages = computed(() => Object.entries(store.runtimeStageStats ?? {}))

onMounted(() => { if (!store.runtimeRuns.length) void store.loadRuntime() })
</script>

<template>
  <div class="rtl" data-testid="runtime-panel">
    <header class="rtl-head">
      <b>{{ L('运行时时间线', 'Runtime timeline') }}</b>
      <TruthBadge kind="truth" value="OBSERVED" compact />
      <select class="rtl-sel" :value="store.selectedRun" data-testid="rtl-run"
              @change="store.setSelectedRun(($event.target as HTMLSelectElement).value)">
        <option v-for="r in store.runtimeRuns" :key="r.run_id" :value="r.run_id">{{ r.run_id }}</option>
      </select>
      <span v-if="run" class="rtl-meta">
        workload {{ run.workload_id }} · records {{ run.records }} · integrity
        <b>{{ (run.integrity as any)?.status ?? '—' }}</b>
      </span>
      <span class="rtl-spacer"></span>
      <button type="button" class="rtl-btn" @click="wb.setPerspective('runtime'); wb.dockOpen = false">
        {{ L('打开运行时视角', 'open runtime perspective') }} →
      </button>
    </header>
    <div class="rtl-body">
      <div class="rtl-cols">
        <div class="rtl-col">
          <div class="rtl-col-head">{{ L('阶段观测', 'stage observations') }}</div>
          <div v-for="[k, v] in stages" :key="k" class="rtl-row mono">
            <span class="rtl-k">{{ k }}</span>
            <span class="rtl-v">{{ (v as any).count ?? (v as any).calls ?? '—' }}×</span>
            <span class="rtl-v">{{ (v as any).inclusive_ms ?? '—' }} ms</span>
          </div>
          <div v-if="!stages.length" class="rtl-none">{{ L('该运行没有阶段观测', 'no stage observations for this run') }}</div>
        </div>
        <div class="rtl-col" data-testid="runtime-honest-notes">
          <div class="rtl-col-head">{{ L('如实说明', 'honest notes') }}</div>
          <div class="rtl-note">• {{ L('运行时数据覆盖是选择性探针：PARTIAL，不代表全部内存访问。',
                                   'runtime data coverage is selective probes: PARTIAL, not all memory access.') }}</div>
          <div class="rtl-note">• {{ L('插桩会放大绝对时间（PERF-CAUSE0: 5.14×）。',
                                   'instrumentation inflates absolute time (PERF-CAUSE0: 5.14×).') }}</div>
          <div class="rtl-note">• {{ L('没有观测 ≠ 没有发生。', 'not observed ≠ did not happen.') }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rtl { display: flex; flex-direction: column; height: 100%; min-height: 0; font-size: 11px; }
.rtl-head { display: flex; gap: 6px; align-items: center; padding: 4px 8px; border-bottom: 1px solid var(--border); }
.rtl-sel { font-size: 10.5px; border: 1px solid var(--border-strong); border-radius: 4px; background: var(--surface); color: var(--text-primary); }
.rtl-meta { font-size: 9.5px; color: var(--text-muted); }
.rtl-spacer { flex: 1; }
.rtl-btn { border: 1px solid var(--border-strong); background: var(--panel); border-radius: 4px; font-size: 10px; padding: 1px 6px; cursor: pointer; color: var(--accent); }
.rtl-body { flex: 1; min-height: 0; overflow: auto; padding: 4px 8px; }
.rtl-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
.rtl-col-head { font-size: 10px; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted); margin-bottom: 2px; }
.rtl-row { display: grid; grid-template-columns: 1fr auto auto; gap: 6px; font-size: 10px; border-bottom: 1px dotted var(--border); padding: 1px 0; }
.rtl-k { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rtl-v { color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.rtl-none, .rtl-note { font-size: 9.5px; color: var(--text-muted); line-height: 1.6; }
.mono { font-family: var(--mono); }
</style>
