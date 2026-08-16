import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './client'

/**
 * A 401 anywhere means the session lapsed; the router's auth gate picks this up from
 * `/auth/me` failing, so queries must not retry a 401 into a spinner that never resolves.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
        return failureCount < 2
      },
    },
  },
})
