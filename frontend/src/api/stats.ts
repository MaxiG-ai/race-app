import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { StatsSummary } from '@/types'

export const statsKeys = {
  summary: ['stats', 'summary'] as const,
}

/** Aggregate strip above the timeline (§5) — race count, total distance, total elevation. */
export function useStatsSummary() {
  return useQuery({
    queryKey: statsKeys.summary,
    queryFn: () => apiFetch<StatsSummary>('/stats/summary'),
  })
}
