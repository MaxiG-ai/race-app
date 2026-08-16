import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'

/**
 * Auth API (§4). Sessions live in HttpOnly cookies set by the backend — there is
 * no token here to store or attach by hand, `apiFetch` is already credentialed.
 */
export interface User {
  id: number
  email: string
  display_name: string | null
  is_active: boolean
  created_at: string
}

export interface LoginPayload {
  email: string
  password: string
}

export function login(payload: LoginPayload): Promise<User> {
  return apiFetch<User>('/auth/login', { method: 'POST', body: payload })
}

export function logout(): Promise<void> {
  return apiFetch<void>('/auth/logout', { method: 'POST' })
}

export function fetchMe(): Promise<User> {
  return apiFetch<User>('/auth/me')
}

export const meQueryKey = ['auth', 'me'] as const

/**
 * The frontend's boot-time login check (§4/§5): `RequireAuth` renders a
 * placeholder while this loads and redirects to `/login` on 401. `retry: false`
 * so a logged-out session resolves immediately instead of retrying a 401 into a
 * slow spinner.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: fetchMe,
    retry: false,
  })
}

/** Invalidates the cached `/auth/me` result — call after login/logout. */
export function useInvalidateCurrentUser() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: meQueryKey })
}
