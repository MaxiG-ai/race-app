import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useCurrentUser } from '@/api/auth'

/**
 * Auth gate wrapped around every route except `/login` (see App.tsx). While the
 * boot-time `/auth/me` query is in flight, renders a quiet placeholder rather
 * than flashing the guarded content. On 401 it redirects to `/login`, preserving
 * the attempted location so Login can send the user back afterwards.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { data: user, isLoading, isError } = useCurrentUser()

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <span className="text-sm text-ink/40">Loading…</span>
      </div>
    )
  }

  if (isError || !user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <>{children}</>
}
