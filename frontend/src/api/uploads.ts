import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { raceKeys } from './races'
import { apiFetch } from './client'
import type { RaceGroupRequest, RaceRead, UploadResult, UploadTrayResponse } from '@/types'
import type { components } from '@/types/api'

type UploadStatus = components['schemas']['UploadStatus']

/**
 * Uploads / grouping tray API (§3 "Single-sport uploads and manual grouping").
 * `POST /uploads` is multipart and returns a *per-file* outcome — the tray must show
 * each file's fate individually, never collapse them into one batch result.
 */

export const uploadKeys = {
  all: ['uploads'] as const,
  tray: (status: UploadStatus) => ['uploads', 'tray', status] as const,
}

export function useUploadsTray(status: UploadStatus = 'parsed') {
  return useQuery({
    queryKey: uploadKeys.tray(status),
    queryFn: () => apiFetch<UploadTrayResponse>('/uploads', { query: { status } }),
  })
}

/**
 * Multipart upload of one or many files under the `files` field the API expects.
 *
 * An optional `raceId` attaches every uploaded file to that existing race
 * (`race_id` form field, §3 "Single-sport uploads and manual grouping" — backfilling
 * a forgotten leg, or a second recording of one already present) instead of letting
 * it land in the ungrouped tray or auto-group into a new race. Attaching changes the
 * race's activities/laps/track, so success also invalidates that race's detail query
 * (and the list, since segment totals on the timeline card can change too) — not just
 * the uploads tray.
 */
export function useUploadFiles(raceId?: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (files: File[]) => {
      const formData = new FormData()
      for (const file of files) formData.append('files', file)
      if (raceId !== undefined) formData.append('race_id', String(raceId))
      return apiFetch<UploadResult[]>('/uploads', { method: 'POST', body: formData })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: uploadKeys.all })
      if (raceId !== undefined) {
        queryClient.invalidateQueries({ queryKey: raceKeys.all })
        queryClient.invalidateQueries({ queryKey: raceKeys.detail(raceId) })
      }
    },
  })
}

export function useDeleteUpload() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => apiFetch<void>(`/uploads/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: uploadKeys.all }),
  })
}

/**
 * `POST /races/group` — creates the race from the user's confirmed selection.
 * Ambiguous detection (two runs in one group, an `other` sport) is rejected with an
 * error naming the ambiguity; callers catch that and retry with explicit
 * `segment_assignments` rather than this hook guessing at anything.
 */
export function useGroupUploads() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: RaceGroupRequest) =>
      apiFetch<RaceRead>('/races/group', { method: 'POST', body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: uploadKeys.all })
      queryClient.invalidateQueries({ queryKey: raceKeys.all })
    },
  })
}
