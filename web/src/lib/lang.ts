// TOPO-EDITOR-UX0 §13-§15: global language setting.
// Mode: system follows navigator.language (zh* → zh-CN); manual 中文/English
// persists until System is re-picked.  Default 中文 per spec §13 (or keep
// an existing stored config).
import { createI18n, type I18n } from 'vue-i18n'
import zhCN from '../i18n/zh-CN'
import enUS from '../i18n/en-US'

export type LangMode = 'system' | 'zh-CN' | 'en-US'
export const LANG_MODES: LangMode[] = ['system', 'zh-CN', 'en-US']
export const LANG_STORAGE_KEY = 'rintel.lang'

export const messages = {
  'zh-CN': zhCN,
  'en-US': enUS,
}

export function detectLang(): 'zh-CN' | 'en-US' {
  if (typeof navigator === 'undefined') return 'zh-CN'
  return (navigator.language || 'zh-CN').toLowerCase().startsWith('zh')
    ? 'zh-CN' : 'en-US'
}

export function loadStoredLang(storage?: Storage): LangMode {
  try {
    const raw = (storage ?? (typeof localStorage !== 'undefined' ? localStorage : null))
      ?.getItem(LANG_STORAGE_KEY)
    if (raw === 'system' || raw === 'zh-CN' || raw === 'en-US') return raw
  } catch { /* ignore */ }
  // TOPO-EDITOR-UX0 §13: default 中文 (system picked explicitly to follow OS).
  return 'zh-CN'
}

export function storeLang(mode: LangMode, storage?: Storage): void {
  try {
    (storage ?? (typeof localStorage !== 'undefined' ? localStorage : null))
      ?.setItem(LANG_STORAGE_KEY, mode)
  } catch { /* ignore */ }
}

export function resolveLang(mode: LangMode, detected: 'zh-CN' | 'en-US'): 'zh-CN' | 'en-US' {
  if (mode === 'zh-CN') return 'zh-CN'
  if (mode === 'en-US') return 'en-US'
  return detected
}

let i18n: I18n | null = null

export function setupI18n(): I18n {
  const mode = loadStoredLang()
  const locale = resolveLang(mode, detectLang())
  i18n = createI18n({
    legacy: false,
    globalInjection: true,
    locale,
    fallbackLocale: 'en-US',
    messages,
  })
  return i18n
}

export function getI18n(): I18n {
  if (!i18n) return setupI18n()
  return i18n
}

/** Apply a resolved language to the active i18n instance + <html lang>. */
export function applyLangToDom(resolved: 'zh-CN' | 'en-US'): void {
  const i = getI18n()
  const inst = (i.global as { locale: { value: string } })
  inst.locale.value = resolved
  document.documentElement.setAttribute('lang', resolved)
}

/** Subscribe to system language changes; returns unsubscribe. */
export function watchSystemLang(onChange: (detected: 'zh-CN' | 'en-US') => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const mql = window.matchMedia('(language: zh-CN)')
  // Navigation API has no reliable language change event across browsers;
  // poll sparingly while a tab is alive.  Users rarely flip OS language
  // mid-session — a 15s poll is a pragmatic live-follow.
  const handler = () => onChange(detectLang())
  const id = window.setInterval(handler, 15_000)
  mql.addEventListener?.('change', handler)
  return () => {
    window.clearInterval(id)
    mql.removeEventListener?.('change', handler)
  }
}
