import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { LapSplitRead } from '@/types'
import type { Segment } from '@/lib/format'

/**
 * `GET /races/{id}/laps` — lap splits for the pacing chart, optionally filtered to
 * one segment. Only laps of the `is_primary` recording for that segment, so the
 * frontend never has to re-derive which device counts (§3 "assembly is per
 * segment"). Laps carry pacing *shape* only — never sum them to a segment total
 * (§3 Traps 1); segment totals live on `RecordedActivityRead.elapsed_time`.
 */
export const lapKeys = {
  bySport: (raceId: number, sport: Segment | undefined) =>
    ['races', 'laps', raceId, sport ?? 'all'] as const,
}

export function useRaceLaps(raceId: number, sport?: Segment) {
  return useQuery({
    queryKey: lapKeys.bySport(raceId, sport),
    queryFn: () =>
      apiFetch<LapSplitRead[]>(`/races/${raceId}/laps`, {
        query: sport ? { sport } : undefined,
      }),
    enabled: Number.isFinite(raceId) && sport !== undefined,
  })
}
