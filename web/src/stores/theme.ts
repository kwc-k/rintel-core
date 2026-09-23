// THEME0 store: Appearance System/Light/Dark with persistence and live
// system listening.  Applying is just `data-theme` on <html> + the
// color-scheme hint — every component theme comes from CSS variables.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  applyThemeToDom,
  loadStoredMode,
  resolveTheme,
  storeMode,
  systemPrefersDark,
  watchSystemScheme,
  type ThemeMode,
} from '../lib/theme'

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(loadStoredMode())
  const systemDark = ref(systemPrefersDark())
  const resolved = computed(() => resolveTheme(mode.value, systemDark.value))
  let unsub: (() => void) | null = null

  function apply(): void {
    applyThemeToDom(resolved.value)
    // notify deep consumers (Monaco editor theme, X6 canvas redraw)
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('rintel:theme', {
        detail: { mode: mode.value, resolved: resolved.value },
      }))
    }
  }

  function setMode(next: ThemeMode): void {
    mode.value = next
    storeMode(next)
    apply()
  }

  function init(): void {
    apply()
    if (!unsub) {
      unsub = watchSystemScheme((dark) => {
        systemDark.value = dark
        apply()
      })
    }
  }

  return { mode, systemDark, resolved, setMode, init }
})
