<script setup lang="ts">
// TOPO-EDITOR-UX0 §7-§9/§21: Agent proposal preview dialog.
// Every agent result must be previewed and explicitly accepted here before
// the store writes anything to TO-BE Design (§9/§26 — no silent apply).
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFlowStore } from '../stores/flow'
import type { AgentProposal } from '../domain/flow'

const props = defineProps<{
  proposal: AgentProposal
  /** resolved placeholders: '__cache__' etc. already concrete */
  editableName?: boolean
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'applied'): void
}>()

const { t } = useI18n()
const flow = useFlowStore()
const applying = ref(false)
const acceptName = ref(props.proposal.suggestions?.[0]?.primary ?? '')

const isNaming = computed(() => props.proposal.intent === 'naming')
const isAnnotation = computed(() => props.proposal.intent === 'annotation')
const isPatch = computed(() => !!props.proposal.patch)

function opLabel(op: string): string {
  const map: Record<string, string> = {
    addBlock: t('agent.patchAddBlock', { kind: '…' }),
    removeBlock: '−',
    addPort: t('agent.patchAddPort'),
    removePort: '−',
    addNet: t('agent.patchAddNet'),
    removeNet: t('agent.patchRemoveNet'),
    updateBlock: '♻',
    updateNet: '♻',
  }
  return map[op] ?? op
}

async function accept(): Promise<void> {
  applying.value = true
  try {
    const p = props.proposal
    if (isNaming.value && p.suggestions?.length) {
      // 命名：写入所选 block 的 Design Annotation display_name
      const targetId = p.affectedDesignIds[0]
      if (targetId) {
        const current = flow.annotationOf(targetId)
        await flow.saveBlockAnnotation(targetId, {
          ...current, displayName: acceptName.value,
        })
      }
      emit('applied')
      return
    }
    if (isAnnotation.value && p.annotation) {
      const targetId = p.affectedDesignIds[0]
      if (targetId) {
        await flow.saveBlockAnnotation(targetId, {
          ...flow.annotationOf(targetId),
          ...p.annotation,
        })
      }
      emit('applied')
      return
    }
    if (isPatch.value && p.patch) {
      const ok = await flow.applyAgentPatch(
        {
          agentActionId: p.agentActionId,
          intent: p.intent,
          prompt: p.prompt ?? null,
          affectedDesignIds: p.affectedDesignIds,
          before: { ops: p.patch.ops.length },
        },
        p.patch.ops as never,
      )
      if (ok) emit('applied')
      return
    }
    emit('close')
  } finally {
    applying.value = false
  }
}

function isPlaceholderPort(name: string | undefined): boolean {
  return name === '__cache__' || name === '__cachein__' || name === '__cacheout__'
}

onMounted(() => {
  if (props.editableName && acceptName.value) acceptName.value = acceptName.value
})
</script>

<template>
  <div class="ap-overlay" @click.self="emit('close')">
    <div class="ap-dialog" role="dialog" :aria-label="t('agent.previewTitle', { intent: proposal.intent })" data-testid="agent-preview">
      <header class="ap-head">{{ t('agent.previewTitle', { intent: proposal.intent }) }}</header>
      <div class="ap-hint">{{ t('agent.previewHint') }}</div>
      <div class="ap-summary">{{ proposal.summary }}</div>

      <!-- naming -->
      <template v-if="isNaming && proposal.suggestions?.length">
        <div class="ap-section">{{ t('agent.nameTitle') }}</div>
        <div v-for="(s, i) in proposal.suggestions" :key="i" class="ap-name-row" :class="{ primary: i === 0 }">
          <span class="ap-tag">{{ i === 0 ? t('agent.namePrimary') : t('agent.nameAlternates') }}</span>
          <span class="ap-name mono">{{ s.primary }}</span>
          <span class="ap-muted">{{ s.reason }}</span>
        </div>
        <div class="ap-section">{{ t('common.name') }}</div>
        <input v-model="acceptName" class="ap-input mono" data-testid="agent-name-input" />
        <div v-if="proposal.suggestions[0].alternates.length" class="ap-alts">
          <button
            v-for="alt in proposal.suggestions[0].alternates" :key="alt"
            class="ap-alt" @click="acceptName = alt"
          >{{ alt }}</button>
        </div>
        <div class="ap-actions">
          <button class="ap-btn primary" data-testid="agent-accept" :disabled="applying" @click="accept">
            {{ t('agent.acceptAndApply') }}
          </button>
          <button class="ap-btn" @click="emit('close')">{{ t('common.cancel') }}</button>
        </div>
      </template>

      <!-- annotation -->
      <template v-else-if="isAnnotation && proposal.annotation">
        <div class="ap-section">{{ t('agent.annotationIntro') }}</div>
        <dl class="ap-ann">
          <template v-for="(v, k) in proposal.annotation" :key="k">
            <dt class="ap-ann-k">{{ k }}</dt>
            <dd class="ap-ann-v">{{ v }}</dd>
          </template>
        </dl>
        <div class="ap-actions">
          <button class="ap-btn primary" data-testid="agent-accept" :disabled="applying" @click="accept">
            {{ t('agent.acceptAndApply') }}
          </button>
          <button class="ap-btn" @click="emit('close')">{{ t('common.cancel') }}</button>
        </div>
      </template>

      <!-- patch / sketch -->
      <template v-else-if="isPatch && proposal.patch">
        <div class="ap-section">{{ t('agent.patchIntro') }}</div>
        <div class="ap-patch-desc">{{ proposal.patch.description }}</div>
        <ul class="ap-ops" data-testid="agent-patch-ops">
          <li v-for="(op, i) in proposal.patch.ops" :key="i" class="ap-op" :class="'op-' + op.op">
            <span class="ap-op-sign">{{ op.op.startsWith('add') || op.op.startsWith('removeNet') ? '±' : '→' }}</span>
            <span class="ap-op-text">
              <template v-if="op.op === 'addBlock'">{{ t('agent.patchAddBlock', { kind: op.block?.kind }) }} → {{ op.block?.name }}</template>
              <template v-else-if="op.op === 'addPort'">{{ t('agent.patchAddPort') }}: {{ op.port?.name }} ({{ op.port?.semanticKind }}/{{ op.port?.direction }})</template>
              <template v-else-if="op.op === 'addNet'">
                {{ op.net?.sourcePortId }} → {{ op.net?.targetPortId }} <span class="ap-muted">({{ op.net?.kind }})</span>
              </template>
              <template v-else-if="op.op === 'removeNet'">{{ opLabel(op.op) }}</template>
              <template v-else>{{ opLabel(op.op) }}</template>
            </span>
          </li>
        </ul>
        <div class="ap-actions">
          <button class="ap-btn primary" data-testid="agent-accept" :disabled="applying" @click="accept">
            {{ applying ? '…' : t('agent.applyToDesign') }}
          </button>
          <button class="ap-btn" @click="emit('close')">{{ t('common.cancel') }}</button>
        </div>
      </template>

      <!-- explain (read-only) -->
      <template v-else>
        <pre class="ap-explain mono">{{ proposal.explanation ?? proposal.summary }}</pre>
        <div class="ap-actions">
          <button class="ap-btn" @click="emit('close')">{{ t('common.close') }}</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.ap-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 950;
  display: flex;
  align-items: center;
  justify-content: center;
}
.ap-dialog {
  width: 480px;
  max-height: 80vh;
  overflow-y: auto;
  background: var(--panel-bg);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--text-primary);
}
.ap-head { font-weight: 700; font-size: 14px; }
.ap-hint { font-size: 10px; color: var(--text-secondary); background: var(--sugg-bg); border: 1px solid var(--sugg-border); border-radius: 5px; padding: 5px 8px; }
.ap-summary { font-size: 12px; }
.ap-section { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-secondary); margin-top: 6px; }
.ap-name-row { display: flex; gap: 6px; align-items: baseline; font-size: 12px; }
.ap-name-row.primary .ap-name { color: var(--accent); font-weight: 700; }
.ap-tag { font-size: 9px; border: 1px solid var(--border-strong); border-radius: 3px; padding: 0 4px; color: var(--text-secondary); }
.ap-name { font-weight: 600; }
.ap-muted { font-size: 10px; color: var(--text-secondary); }
.ap-input { border: 1px solid var(--border-strong); border-radius: 5px; padding: 5px 8px; font-size: 12px; background: var(--panel-bg); color: var(--text-primary); }
.ap-alts { display: flex; gap: 5px; flex-wrap: wrap; }
.ap-alt { font-size: 10px; border: 1px dashed var(--border-strong); background: none; color: var(--text-primary); border-radius: 4px; padding: 2px 7px; cursor: pointer; }
.ap-ann { display: grid; grid-template-columns: 110px 1fr; gap: 3px 8px; margin: 0; font-size: 11px; }
.ap-ann-k { color: var(--text-secondary); }
.ap-ann-v { color: var(--text-primary); }
.ap-patch-desc { font-size: 11px; color: var(--text-secondary); background: var(--surface); border: 1px solid var(--border); border-radius: 5px; padding: 6px 8px; }
.ap-ops { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 3px; font-size: 11px; }
.ap-op { display: flex; gap: 6px; align-items: baseline; }
.ap-op-sign { font-family: var(--mono); color: var(--accent); }
.ap-op.op-removeNet .ap-op-text { color: var(--status-error); }
.ap-op-text { word-break: break-all; }
.ap-explain { white-space: pre-wrap; background: var(--surface); border: 1px solid var(--border); border-radius: 5px; padding: 8px; font-size: 11px; margin: 0; color: var(--text-primary); }
.ap-actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px; }
.ap-btn { border: 1px solid var(--border-strong); background: var(--panel-bg); color: var(--text-primary); border-radius: 5px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
.ap-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.ap-btn:disabled { opacity: 0.5; }
</style>
