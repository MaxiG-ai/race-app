/**
 * Thin fetch wrapper. Sessions are HttpOnly cookies (§4 Auth), so every request is
 * credentialed and there is no token to attach by hand.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export class ApiError extends Error {
  status: number
  body?: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & { body?: unknown; query?: Record<string, unknown> }

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, headers, ...rest } = options

  const url = new URL(`${BASE_URL}${path}`, window.location.origin)
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value))
  }

  const isFormData = body instanceof FormData
  const response = await fetch(url, {
    credentials: 'include',
    headers: {
      ...(isFormData || body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...headers,
    },
    ...(body !== undefined ? { body: isFormData ? body : JSON.stringify(body) } : {}),
    ...rest,
  })

  if (!response.ok) {
    const detail = await response.json().catch(() => undefined)
    throw new ApiError(
      response.status,
      (detail as { detail?: string })?.detail ?? `${response.status} ${response.statusText}`,
      detail,
    )
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
