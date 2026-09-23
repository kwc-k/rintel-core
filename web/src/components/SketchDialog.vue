<script setup lang="ts">
// TOPO-EDITOR-UX0 §21: Agent 草图模式 — user types a natural-language
// description; the agent returns a Topology Proposal; Preview → Apply to
// Design creates nodes only (never source code).
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDesignStore } from '../stores/design'

const design = useDesignStore()
const { t } = useI18n()
const prompt = ref('')
const err = ref('')

function run(): void {
  err.value = ''
  if (!prompt.value.trim()) { err.value = t('common.error'); return }
  design.sketchPrompt = prompt.value
  design.agentSketch()
  if (design.proposal) design.showSketch = false
  else err.value = '未识别到拓扑阶段（试试：数据采集/分析/缓存/通知）'
}
</script>

<template>
  <div class="sk-overlay" @click.self="design.showSketch = false">
    <div class="sk-dialog" role="dialog" :aria-label="t('canvas.agentSketch')" data-testid="sketch-dialog">
      <header class="sk-head">{{ t('canvas.agentSketch') }}</header>
      <textarea
        v-model="prompt"
        class="sk-input"
        rows="4"
        :placeholder="'我想要做一个股票分析服务：数据采集后分析，结果做缓存，最后发送通知。'"
        data-testid="sketch-prompt"
      ></textarea>
      <div v-if="err" class="sk-err">{{ err }}</div>
      <div class="sk-actions">
        <button class="sk-btn primary" data-testid="sketch-run" @click="run">{{ t('agent.run') }}</button>
        <button class="sk-btn" @click="design.showSketch = false">{{ t('common.cancel') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sk-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 940;
  display: flex;
  align-items: center;
  justify-content: center;
}
.sk-dialog {
  width: 420px;
  background: var(--panel-bg);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--text-primary);
}
.sk-head { font-weight: 700; font-size: 13px; }
.sk-input {
  border: 1px solid var(--border-strong);
  border-radius: 5px;
  padding: 6px 8px;
  font-size: 12px;
  background: var(--panel-bg);
  color: var(--text-primary);
  resize: vertical;
}
.sk-err { color: var(--status-error); font-size: 11px; }
.sk-actions { display: flex; gap: 6px; justify-content: flex-end; }
.sk-btn {
  border: 1px solid var(--border-strong);
  background: var(--panel-bg);
  color: var(--text-primary);
  border-radius: 5px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
}
.sk-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
</style>
