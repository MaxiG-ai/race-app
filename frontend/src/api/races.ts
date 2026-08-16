import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './client'
import type {
  OfficialResultUpsert,
  RaceCreate,
  RaceDetail,
  RaceFuelingUpsert,
  RaceListResponse,
  RaceRead,
  RaceUpdate,
} from '@/types'

export const raceKeys = {
  all: ['races'] as const,
  list: (page: { limit: number; offset: number }) => ['races', 'list', page] as const,
  detail: (id: number) => ['races', 'detail', id] as const,
}

export function useRaces(page: { limit: number; offset: number }) {
  return useQuery({
    queryKey: raceKeys.list(page),
    queryFn: () => apiFetch<RaceListResponse>('/races', { query: page }),
  })
}

export function useRace(id: number) {
  return useQuery({
    queryKey: raceKeys.detail(id),
    queryFn: () => apiFetch<RaceDetail>(`/races/${id}`),
    enabled: Number.isFinite(id),
  })
}

/**
 * Every race mutation invalidates the whole `races` key rather than patching the
 * cache: editing a race changes its timeline card too (name, type, segment totals),
 * and the list is small enough that a refetch is cheaper than keeping two
 * representations in sync by hand.
 */
function useRaceMutation<TArgs, TResult>(
  mutationFn: (args: TArgs) => Promise<TResult>,
  raceId?: number,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: raceKeys.all })
      if (raceId !== undefined) {
        queryClient.invalidateQueries({ queryKey: raceKeys.detail(raceId) })
      }
    },
  })
}

export function useCreateRace() {
  return useRaceMutation((body: RaceCreate) => apiFetch<RaceRead>('/races', { method: 'POST', body }))
}

export function useUpdateRace(id: number) {
  return useRaceMutation(
    (body: RaceUpdate) => apiFetch<RaceRead>(`/races/${id}`, { method: 'PATCH', body }),
    id,
  )
}

export function useDeleteRace() {
  return useRaceMutation((id: number) => apiFetch<void>(`/races/${id}`, { method: 'DELETE' }))
}

export function useUpsertOfficialResult(id: number) {
  return useRaceMutation(
    (body: OfficialResultUpsert) =>
      apiFetch(`/races/${id}/official-result`, { method: 'PUT', body }),
    id,
  )
}

export function useUpsertFueling(id: number) {
  return useRaceMutation(
    (body: RaceFuelingUpsert) => apiFetch(`/races/${id}/fueling`, { method: 'PUT', body }),
    id,
  )
}

export function useRefreshWeather(id: number) {
  return useRaceMutation(
    () => apiFetch(`/races/${id}/weather/refresh`, { method: 'POST' }),
    id,
  )
}
