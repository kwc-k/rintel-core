<script setup lang="ts">
// TOPO-EDITOR-UX0 §6: Design Annotation editor (display_name / description /
// responsibility / notes / tags).  Pure design-plane; save goes through the
// flow store (recorded as a design transaction).
import { useI18n } from 'vue-i18n'
import { useDesignStore } from '../stores/design'

const design = useDesignStore()
const { t } = useI18n()
</script>

<template>
  <div class="ann-panel" data-testid="annotation-panel" role="dialog" :aria-label="t('annotation.title')">
    <header class="ann-head">{{ t('annotation.title') }}</header>
    <div class="ann-hint">{{ t('annotation.hint') }}</div>
    <label class="ann-field">
      <span class="ann-label">{{ t('annotation.displayName') }}</span>
      <input v-model="design.annotationDraft.displayName" class="ann-input" data-testid="ann-display-name" />
    </label>
    <label class="ann-field">
      <span class="ann-label">{{ t('annotation.description') }}</span>
      <textarea v-model="design.annotationDraft.description" rows="3" class="ann-input" data-testid="ann-description"></textarea>
    </label>
    <label class="ann-field">
      <span class="ann-label">{{ t('annotation.responsibility') }}</span>
      <textarea v-model="design.annotationDraft.responsibility" rows="2" class="ann-input" data-testid="ann-responsibility"></textarea>
    </label>
    <label class="ann-field">
      <span class="ann-label">{{ t('annotation.notes') }}</span>
      <textarea v-model="design.annotationDraft.notes" rows="2" class="ann-input" data-testid="ann-notes"></textarea>
    </label>
    <label class="ann-field">
      <span class="ann-label">{{ t('annotation.tags') }}</span>
      <input v-model="design.annotationDraft.tags" class="ann-input" data-testid="ann-tags" />
    </label>
    <div class="ann-actions">
      <button class="ann-btn primary" data-testid="ann-save" @click="design.saveAnnotation()">
        {{ t('annotation.save') }}
      </button>
      <button class="ann-btn" @click="design.closeAnnotation()">{{ t('common.cancel') }}</button>
    </div>
  </div>
</template>

<style scoped>
.ann-panel {
  position: fixed;
  right: 340px;
  top: 48px;
  width: 300px;
  background: var(--panel-bg);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: 0 10px 32px rgba(0, 0, 0, 0.2);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 800;
  color: var(--text-primary);
}
.ann-head { font-weight: 700; font-size: 13px; }
.ann-hint { font-size: 10px; color: var(--text-secondary); }
.ann-field { display: flex; flex-direction: column; gap: 3px; }
.ann-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-secondary); }
.ann-input {
  border: 1px solid var(--border-strong);
  border-radius: 4px;
  padding: 4px 7px;
  font-size: 11px;
  background: var(--panel-bg);
  color: var(--text-primary);
  resize: vertical;
}
.ann-actions { display: flex; gap: 6px; justify-content: flex-end; }
.ann-btn {
  border: 1px solid var(--border-strong);
  background: var(--panel-bg);
  color: var(--text-primary);
  border-radius: 4px;
  padding: 4px 9px;
  font-size: 11px;
  cursor: pointer;
}
.ann-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
</style>
