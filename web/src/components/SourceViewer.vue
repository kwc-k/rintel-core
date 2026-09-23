<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type * as Monaco from 'monaco-editor'
import { useSourceStore } from '../stores/source'
import { useThemeStore } from '../stores/theme'
import { useWorkspaceStore } from '../stores/workspace'

const props = defineProps<{ repoId: string | null }>()

const source = useSourceStore()
const workspace = useWorkspaceStore()
const theme = useThemeStore()

// THEME0: Monaco keeps following the resolved appearance.
function monacoTheme(): 'vs' | 'vs-dark' {
  return theme.resolved === 'dark' ? 'vs-dark' : 'vs'
}

const container = ref<HTMLDivElement | null>(null)
const editorFailed = ref(false)

let editor: Monaco.editor.IStandaloneCodeEditor | null = null

const error = computed(() => source.error)
const drift = computed(() => source.current?.drift === true)
const content = computed(() => source.current?.content ?? '')

async function mountEditor(): Promise<void> {
  await nextTick()
  if (!container.value || !source.current) return
  editorFailed.value = false
  try {
    const { loadMonaco } = await import('../lib/monaco')
    const monaco = await loadMonaco()
    if (editor) {
      const model = monaco.editor.createModel(source.current.content, source.current.language ?? 'plaintext')
      editor.setModel(model)
    } else {
      editor = monaco.editor.create(container.value, {
        value: source.current.content,
        language: source.current.language ?? 'plaintext',
        readOnly: true,
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        lineNumbersMinChars: 3,
        theme: monacoTheme(),
      })
    }
    monaco.editor.setTheme(monacoTheme())
    const line = source.openLine
    // SOURCE-SPAN-COL0 §21: exact range highlight when the span is EXACT
    const sp = source.openSpan
    if (sp && sp.start_line && sp.start_column) {
      const range = {
        startLineNumber: sp.start_line,
        startColumn: sp.start_column,
        endLineNumber: sp.end_line ?? sp.start_line,
        endColumn: sp.end_column ?? sp.start_column,
      }
      editor.setSelection(range)
      editor.revealRangeInCenter(range)
    } else if (line) {
      editor.revealLineInCenter(line)
    }
  } catch {
    // Monaco could not mount (e.g. headless test env). Show a plain-text
    // fallback so the drift/error states remain observable without Monaco.
    editor = null
    editorFailed.value = true
  }
}

const spanLabel = computed(() => {
  const sp = source.openSpan
  if (!sp || !sp.start_column) return source.openLine ? `${source.openPath}:${source.openLine}` : ''
  const head = `${source.openPath}:${sp.start_line}:${sp.start_column}`
  const tail = sp.end_column ? `-${sp.end_line && sp.end_line !== sp.start_line ? `${sp.end_line}:` : ''}${sp.end_column}` : ''
  return head + tail
})

function onThemeEvent(): void {
  if (editor) {
    // lazy loader keeps Monaco out of the test transform graph
    import('../lib/monaco').then(({ loadMonaco }) => loadMonaco().then((m) => {
      m.editor.setTheme(monacoTheme())
    })).catch(() => {})
  }
}
window.addEventListener('rintel:theme', onThemeEvent)

watch(
  () => [source.current, source.error] as const,
  async () => {
    if (source.error) {
      editor?.dispose()
      editor = null
      editorFailed.value = false
      return
    }
    if (source.current) await mountEditor()
  },
)

onBeforeUnmount(() => {
  window.removeEventListener('rintel:theme', onThemeEvent)
  editor?.dispose()
  editor = null
})

function locateInTree(): void {
  const path = source.openPath
  if (!path || !props.repoId) return
  workspace.expandAncestors(path)
  workspace.setFocus({ plane: 'evidence', entityType: 'file', id: path })
  source.clearError()
}
</script>

<template>
  <div class="source-viewer" :data-open-line="source.openLine"
       :data-open-span="source.openSpan ? spanLabel : null"
       :data-open-column="source.openSpan?.start_column ?? null">
    <div class="sv-header">
      <span class="mono path">{{ source.openPath ?? '未打开文件' }}</span>
      <span v-if="source.loading" class="spinner" aria-label="加载中"></span>
      <button v-if="source.openPath" type="button" class="ghost" @click="source.reset()">关闭</button>
    </div>

    <div v-if="error" class="sv-error">
      <div class="err-title">无法打开源码</div>
      <div v-if="error === 'source_unavailable'" class="muted">
        源码不可用：该文件在当前 snapshot 不存在，或历史源码无法恢复（不回退当前源码）。
      </div>
      <div v-else class="muted">加载失败：{{ error }}</div>
      <button type="button" class="primary" @click="locateInTree">在树中定位</button>
    </div>

    <div v-else-if="drift" class="drift-banner" role="alert">
      ⚠ 工作区源码与索引快照不一致（drift）
    </div>

    <div v-else-if="!source.current && !source.loading" class="empty">选择文件或符号查看源码</div>

    <div ref="container" v-show="source.current && !error && !editorFailed" class="sv-editor"></div>
    <pre v-if="editorFailed && source.current && !error" class="fallback mono">{{ content }}</pre>
  </div>
</template>

<style scoped>
.source-viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--panel);
}
.sv-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  flex-shrink: 0;
}
.path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.sv-editor {
  flex: 1;
  min-height: 0;
}
.fallback {
  flex: 1;
  margin: 0;
  padding: 10px;
  overflow: auto;
  font-size: 12px;
}
.sv-error {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  text-align: center;
}
.err-title {
  font-weight: 600;
}
.drift-banner {
  background: var(--warn-bg);
  color: var(--warn);
  border-bottom: 1px solid #fde68a;
  padding: 6px 10px;
  font-size: 12px;
  flex-shrink: 0;
}
.empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
