import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { RaceTrackResponse } from '@/types'

/**
 * `GET /races/{id}/track` — GPS polylines, one per segment (§4 Payload note: this is
 * the only endpoint that ever serialises `gps_track`, and it is simplified by
 * default). Derived transitions carry no track and are simply absent from the
 * response rather than an empty polyline (§3 Grouping).
 *
 * Precision defaults to `'low'` here — the detail page's map only needs a shape to
 * look at, not the full multi-MB stream; callers that need more can pass a higher
 * precision explicitly.
 */
export type TrackPrecision = 'low' | 'medium' | 'high' | 'full'

export const trackKeys = {
  detail: (raceId: number, precision: TrackPrecision) =>
    ['races', 'track', raceId, precision] as const,
}

export function useRaceTrack(raceId: number, precision: TrackPrecision = 'low') {
  return useQuery({
    queryKey: trackKeys.detail(raceId, precision),
    queryFn: () =>
      apiFetch<RaceTrackResponse>(`/races/${raceId}/track`, { query: { precision } }),
    enabled: Number.isFinite(raceId),
  })
}
