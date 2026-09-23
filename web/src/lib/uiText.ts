// UI-text accessor for components rendered OUTSIDE the Vue app scope
// (@antv/x6-vue-shape nodes: TopoNode, FlowBlockNode).  They cannot call
// useI18n() — that throws "Need to install with `app.use`".  This helper
// resolves the SAME catalogs from the same stored mode, so X6 nodes follow
// the global Language setting.
import { detectLang, loadStoredLang, resolveLang } from './lang'
import zhCN from '../i18n/zh-CN'
import enUS from '../i18n/en-US'

type Catalog = Record<string, unknown>

function catalog(): Catalog {
  const resolved = resolveLang(loadStoredLang(), detectLang())
  return (resolved === 'zh-CN' ? zhCN : enUS) as unknown as Catalog
}

/** True when the resolved UI language is Chinese (same rule as i18n). */
export function uiIsZh(): boolean {
  return resolveLang(loadStoredLang(), detectLang()) === 'zh-CN'
}

/** Look up a dotted key in the message catalogs; returns `key` on miss. */
export function uiText(key: string): string {
  let cur: unknown = catalog()
  for (const part of key.split('.')) {
    if (cur && typeof cur === 'object') cur = (cur as Record<string, unknown>)[part]
    else { cur = undefined; break }
  }
  return typeof cur === 'string' ? cur : key
}
