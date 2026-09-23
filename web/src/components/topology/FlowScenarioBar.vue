<script setup lang="ts">
// FLOW-INFER0 §17/§19-§20: master flow controls — app, scenario isolation,
// shared core / unknown branches / pruned branches toggles, capability row.
import { useI18n } from 'vue-i18n'
import { useFlowTopologyStore } from '../../stores/flowTopology'
import { useTopoStore } from '../../stores/topo'

const { t } = useI18n()
const store = useFlowTopologyStore()
const topo = useTopoStore()

function onApp(next: string): void {
  store.setApp(next)
  store.loadSemantic()
}
function onScenario(id: string): void {
  store.setScenario(id === '__all__' ? null : id)
}
</script>

<template>
  <div class="flow-bar" data-testid="flow-bar">
    <select class="fb-select" :value="store.app" data-testid="flow-app" @change="onApp(($event.target as HTMLSelectElement).value)">
      <option v-for="a in store.apps" :key="a" :value="a">{{ a }}</option>
    </select>
    <!-- FLOW-SEMANTIC0 §11 granularity: Human / Region / Function -->
    <div class="fb-gran" data-testid="fs-gran" role="group" aria-label="granularity">
      <button type="button" :class="{ on: store.granularity === 'human' }" data-testid="fs-gran-human"
              @click="store.setGranularity('human')">Human</button>
      <button type="button" :class="{ on: store.granularity === 'region' }" data-testid="fs-gran-region"
              @click="store.setGranularity('region')">Region</button>
      <button type="button" :class="{ on: store.granularity === 'function' }" data-testid="fs-gran-function"
              @click="store.setGranularity('function')">Function</button>
    </div>
    <select
      class="fb-select" :value="store.scenarioId ?? '__all__'" data-testid="flow-scenario"
      @change="onScenario(($event.target as HTMLSelectElement).value)"
    >
      <option value="__all__">All Paths（全部路径）</option>
      <option v-for="s in store.scenarios" :key="s.scenario_id" :value="s.scenario_id">
        {{ s.name }}（{{ s.active_regions.length }} 区域）
      </option>
    </select>
    <label class="fb-check" data-testid="flow-isolate">
      <input type="checkbox" :checked="!!store.scenarioId" disabled />
      {{ t('wb.flow.isolate') }}
    </label>
    <label class="fb-check">
      <input type="checkbox" :checked="store.showSharedCore" data-testid="flow-shared"
             @change="store.showSharedCore = ($event.target as HTMLInputElement).checked" />
      {{ t('wb.flow.sharedCore') }}
    </label>
    <label class="fb-check">
      <input type="checkbox" :checked="store.showUnknownBranches" data-testid="flow-unknown"
             @change="store.showUnknownBranches = ($event.target as HTMLInputElement).checked" />
      {{ t('wb.flow.unknownBranches') }}
    </label>
    <label class="fb-check">
      <input type="checkbox" :checked="store.showPruned" data-testid="flow-pruned"
             @change="store.showPruned = ($event.target as HTMLInputElement).checked" />
      {{ t('wb.flow.showPruned') }}
    </label>
    <span class="fb-spacer"></span>
    <div class="fb-cap" data-testid="flow-capability">
      <template v-if="store.activeFlow">
        <span class="fb-cap-line">CALL {{ t('wb.flow.partial') }} · CONTROL {{ t('wb.flow.unknown') }} ·
          DATA PARTIAL（FAC C cross-procedural DATA = PARTIAL） · STATE {{ t('wb.flow.unknown') }} ·
          GUARD {{ t('wb.flow.partial') }}</span>
        <span class="fb-cap-line muted">coverage: PARTIAL（计算核心 faclib 在冻结 C lane 之外）· lane: {{ topo.lane ?? 'fac_c' }}</span>
      </template>
    </div>
  </div>
</template>

<style scoped>
.flow-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--panel);
  flex-wrap: wrap;
  font-size: 11px;
}
.fb-select {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text-primary);
}
.fb-check {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--text-secondary);
  font-size: 10.5px;
}
.fb-spacer { flex: 1; }
.fb-gran {
  display: inline-flex;
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  overflow: hidden;
}
.fb-gran button {
  border: none;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 10.5px;
  padding: 2px 8px;
  cursor: pointer;
}
.fb-gran button + button { border-left: 1px solid var(--border-strong); }
.fb-gran button.on { background: var(--accent-soft); color: var(--accent); font-weight: 700; }
.fb-cap {
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-size: 9.5px;
  color: var(--amber-text);
  max-width: 520px;
}
.fb-cap-line.muted { color: var(--text-muted); }
</style>
