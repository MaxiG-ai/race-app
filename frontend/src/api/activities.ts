import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import { raceKeys } from './races'
import type { RecordedActivityRead } from '@/types'
import type { components } from '@/types/api'

type RecordedActivityUpdate = components['schemas']['RecordedActivityUpdate']

/**
 * `PATCH /activities/{id}` — used here only to flip `is_primary` (§5
 * `RecordingSwitch`). Assembly is per segment, never per metric (§3): making one
 * recording primary changes every displayed number for that leg, so the only
 * sane invalidation is the whole race detail, not a field-level cache patch.
 */
export function useSetPrimaryActivity(raceId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ activityId, isPrimary }: { activityId: number; isPrimary: boolean }) =>
      apiFetch<RecordedActivityRead>(`/activities/${activityId}`, {
        method: 'PATCH',
        body: { is_primary: isPrimary } satisfies RecordedActivityUpdate,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: raceKeys.detail(raceId) })
      queryClient.invalidateQueries({ queryKey: raceKeys.all })
      // The track and lap-split endpoints only ever return the `is_primary`
      // recording per segment (§4), so a switch here changes what they'd return
      // too — not just the detail payload. Prefix match invalidates every
      // precision/sport variant cached for this race.
      queryClient.invalidateQueries({ queryKey: ['races', 'track', raceId] })
      queryClient.invalidateQueries({ queryKey: ['races', 'laps', raceId] })
    },
  })
}
