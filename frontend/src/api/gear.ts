import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import type { GearCreate, GearListItem, GearRead, GearUpdate, UnknownDeviceRead } from '@/types'
import type { components } from '@/types/api'

/**
 * Gear API (§3 "Gear detection", §2 `Gear`/`GearDevice`).
 *
 * `GearPicker` reads and writes `RecordedActivity` via `PATCH /activities/{id}` — but
 * that endpoint's natural home, `src/api/activities.ts`, is owned by another agent in
 * this pass. Rather than touch that file, the two hooks it needs
 * (`useActivityDetail`, `useAssignGear`) live here, next to `GearPicker`, their only
 * caller.
 */

type GearDeviceRead = components['schemas']['GearDeviceRead']
type RecordedActivityDetail = components['schemas']['RecordedActivityDetail']
type RecordedActivityUpdate = components['schemas']['RecordedActivityUpdate']

export const gearKeys = {
  all: ['gear'] as const,
  list: (includeRetired: boolean) => ['gear', 'list', includeRetired] as const,
  unknownDevices: ['gear', 'unknown-devices'] as const,
  activity: (activityId: number) => ['gear', 'activity', activityId] as const,
}

export function useGearList(includeRetired = false) {
  return useQuery({
    queryKey: gearKeys.list(includeRetired),
    queryFn: () =>
      apiFetch<GearListItem[]>('/gear', { query: { include_retired: includeRetired } }),
  })
}

export function useCreateGear() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: GearCreate) => apiFetch<GearRead>('/gear', { method: 'POST', body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: gearKeys.all }),
  })
}

export function useUpdateGear(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: GearUpdate) => apiFetch<GearRead>(`/gear/${id}`, { method: 'PATCH', body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: gearKeys.all }),
  })
}

export function useUnknownDevices() {
  return useQuery({
    queryKey: gearKeys.unknownDevices,
    queryFn: () => apiFetch<UnknownDeviceRead[]>('/gear/unknown-devices'),
  })
}

/**
 * Maps a serial to a gear item. The API backfills every past activity carrying that
 * serial (§3 step 3) but — per the generated schema — the response is just the created
 * `GearDeviceRead`, with no count of how many activities were touched. Callers should
 * say the backfill happened without citing a number the API doesn't actually return.
 */
export function useCreateGearDevice() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      gearId,
      ...body
    }: {
      gearId: number
      manufacturer: string
      serial_number: string
      label?: string | null
    }) => apiFetch<GearDeviceRead>(`/gear/${gearId}/devices`, { method: 'POST', body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: gearKeys.all })
      queryClient.invalidateQueries({ queryKey: gearKeys.unknownDevices })
    },
  })
}

export function useActivityDetail(activityId: number) {
  return useQuery({
    queryKey: gearKeys.activity(activityId),
    queryFn: () => apiFetch<RecordedActivityDetail>(`/activities/${activityId}`),
    enabled: Number.isFinite(activityId),
  })
}

export function useAssignGear(activityId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (gearId: number | null) =>
      apiFetch<RecordedActivityDetail>(`/activities/${activityId}`, {
        method: 'PATCH',
        body: { gear_id: gearId } satisfies RecordedActivityUpdate,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: gearKeys.activity(activityId) })
      queryClient.invalidateQueries({ queryKey: gearKeys.all })
    },
  })
}
