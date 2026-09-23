// THEME0 pure theme resolution (renderer-neutral, testable).
// Appearance: System (default) / Light / Dark.  System follows
// prefers-color-scheme; manual Light/Dark overrides until System is
// picked again. No backend / Evidence / Topology / Formal Core touched.
export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_MODES: ThemeMode[] = ['system', 'light', 'dark']
export const THEME_STORAGE_KEY = 'rintel.theme.mode'

export function systemPrefersDark(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

export function resolveTheme(mode: ThemeMode, systemDark: boolean): ResolvedTheme {
  if (mode === 'dark') return 'dark'
  if (mode === 'light') return 'light'
  return systemDark ? 'dark' : 'light'
}

export function loadStoredMode(storage?: Storage): ThemeMode {
  try {
    const raw = (storage ?? (typeof localStorage !== 'undefined' ? localStorage : null))
      ?.getItem(THEME_STORAGE_KEY)
    if (raw === 'system' || raw === 'light' || raw === 'dark') return raw
  } catch {
    /* ignore */
  }
  return 'system'
}

export function storeMode(mode: ThemeMode, storage?: Storage): void {
  try {
    (storage ?? (typeof localStorage !== 'undefined' ? localStorage : null))
      ?.setItem(THEME_STORAGE_KEY, mode)
  } catch {
    /* ignore */
  }
}

export function applyThemeToDom(resolved: ResolvedTheme, root?: HTMLElement): void {
  const el = root ?? document.documentElement
  el.setAttribute('data-theme', resolved)
  el.style.colorScheme = resolved
}

/** Subscribe to system scheme changes; returns an unsubscribe fn. */
export function watchSystemScheme(onChange: (dark: boolean) => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  const handler = (e: MediaQueryListEvent) => onChange(e.matches)
  mql.addEventListener?.('change', handler)
  return () => mql.removeEventListener?.('change', handler)
}
