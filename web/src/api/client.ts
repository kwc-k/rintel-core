// Typed fetch wrapper with the unified error envelope (SPEC-P1 §7 /
// S2-WEB-CONTRACT §2). Any network failure (fetch reject, or a non-2xx
// response without an error envelope) maps to ApiError('network', ...).

export interface ApiErrorPayload {
  code: string
  message: string
  details: unknown
}

export class ApiError extends Error {
  readonly code: string
  readonly details: unknown
  readonly status: number

  constructor(code: string, message: string, details?: unknown, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = details
    this.status = status
  }

  get isNetwork(): boolean {
    return this.code === 'network'
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError
}

const BASE = '/api/v1'

// A single global connectivity hook so the offline banner can react to any
// API call without the client importing Pinia (avoids a module cycle).
type ConnectivityListener = (online: boolean) => void
let connectivityListener: ConnectivityListener | null = null

export function onConnectivityChange(listener: ConnectivityListener): void {
  connectivityListener = listener
}

function notifyConnectivity(online: boolean): void {
  connectivityListener?.(online)
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
  }
  return parts.length ? `?${parts.join('&')}` : ''
}

async function parseError(res: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = `HTTP ${res.status}`
  let details: unknown
  try {
    const body = (await res.json()) as { error?: Partial<ApiErrorPayload> }
    if (body && body.error && typeof body.error === 'object') {
      code = body.error.code ?? code
      message = body.error.message ?? message
      details = body.error.details
    } else {
      // Non-2xx without the unified envelope → treated as a network failure.
      code = 'network'
      message = `unexpected response (HTTP ${res.status})`
    }
  } catch {
    // Non-JSON error body → treated as a network failure.
    code = 'network'
    message = `unreachable response (HTTP ${res.status})`
  }
  return new ApiError(code, message, details, res.status)
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, init)
  } catch (err) {
    notifyConnectivity(false)
    const reason = err instanceof Error ? err.message : String(err)
    throw new ApiError('network', `network request failed: ${reason}`, undefined, 0)
  }
  if (!res.ok) {
    const err = await parseError(res)
    notifyConnectivity(!err.isNetwork)
    throw err
  }
  notifyConnectivity(true)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export async function apiGet<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'GET', headers: { Accept: 'application/json' } })
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export async function apiDelete<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: 'DELETE', headers: { Accept: 'application/json' } })
}
