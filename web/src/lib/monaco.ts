// Lazy Monaco loader. `monaco-editor` is only fetched on first use via a
// dynamic import, so first paint never blocks on it (S2-WEB-CONTRACT §5).
// The `?worker` and CSS imports live in this module, which SourceViewer
// itself lazy-imports, keeping the whole Monaco footprint out of the boot
// bundle.

import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import 'monaco-editor/min/vs/editor/editor.main.css'

export type Monaco = typeof import('monaco-editor')

let monacoPromise: Promise<Monaco> | null = null

export function loadMonaco(): Promise<Monaco> {
  if (!monacoPromise) {
    monacoPromise = import('monaco-editor').then((monaco) => {
      const w = self as unknown as { MonacoEnvironment?: { getWorker: () => Worker } }
      w.MonacoEnvironment = { getWorker: () => new editorWorker() }
      return monaco
    })
  }
  return monacoPromise
}
