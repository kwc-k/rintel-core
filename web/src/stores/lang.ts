// TOPO-EDITOR-UX0 §13: language store (system / 中文 / English).
import { defineStore } from 'pinia'
import {
  LANG_MODES, LANG_STORAGE_KEY, detectLang, loadStoredLang, resolveLang,
  storeLang, watchSystemLang, applyLangToDom,
  type LangMode,
} from '../lib/lang'

export const useLangStore = defineStore('lang', {
  state: () => ({
    mode: LANG_MODES[0] as LangMode,
    detected: 'zh-CN' as 'zh-CN' | 'en-US',
    initialized: false,
  }),
  getters: {
    resolved(state): 'zh-CN' | 'en-US' {
      return resolveLang(state.mode, state.detected)
    },
    isZh(state): boolean {
      return resolveLang(state.mode, state.detected) === 'zh-CN'
    },
  },
  actions: {
    init(): void {
      if (this.initialized) return
      this.mode = loadStoredLang()
      this.detected = detectLang()
      applyLangToDom(this.resolved)
      watchSystemLang((d) => {
        this.detected = d
        applyLangToDom(this.resolved)
      })
      this.initialized = true
    },
    setMode(mode: LangMode): void {
      this.mode = mode
      storeLang(mode)
      applyLangToDom(this.resolved)
    },
  },
})
