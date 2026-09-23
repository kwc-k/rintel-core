import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiError, isApiError } from '../api/client'

export type ToastKind = 'info' | 'success' | 'error'

export interface Toast {
  id: number
  kind: ToastKind
  message: string
}

let nextToastId = 1

export const useToastStore = defineStore('toast', () => {
  const toasts = ref<Toast[]>([])
  const offline = ref(false)

  function push(message: string, kind: ToastKind = 'info', timeoutMs = 4000): number {
    const id = nextToastId++
    toasts.value.push({ id, kind, message })
    if (timeoutMs > 0) {
      setTimeout(() => dismiss(id), timeoutMs)
    }
    return id
  }

  function dismiss(id: number): void {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  function setOffline(value: boolean): void {
    offline.value = value
  }

  /**
   * Route an API error to the right UI affordance. Network failures flip the
   * global offline banner; other errors become an error toast.
   */
  function reportError(err: unknown): void {
    if (isApiError(err) && err.isNetwork) {
      offline.value = true
      return
    }
    const message = err instanceof Error ? err.message : String(err)
    push(message, 'error')
  }

  function reportSuccess(message: string): void {
    push(message, 'success')
  }

  return { toasts, offline, push, dismiss, setOffline, reportError, reportSuccess }
})
