import { ApiError } from '@/api/client'
import { useSetPrimaryActivity } from '@/api/activities'
import { SEGMENT_LABEL, formatDistance, formatDuration, type Segment } from '@/lib/format'
import type { RecordedActivityRead } from '@/types'

/**
 * Shown only when a segment has more than one recording (§2 `is_primary`) — the
 * same leg caught by two devices, e.g. a watch and a power-meter-equipped head
 * unit. Assembly is per segment, never per metric (§3): picking a recording here
 * swaps *every* number shown for this leg (time, distance, power, HR, cadence),
 * never just the field that happens to differ between devices.
 */
export function RecordingSwitch({
  raceId,
  segment,
  activities,
}: {
  raceId: number
  segment: Segment
  activities: RecordedActivityRead[]
}) {
  const mutation = useSetPrimaryActivity(raceId)

  if (activities.length < 2) return null

  const byRecency = [...activities].sort((a, b) => (a.is_primary === b.is_primary ? 0 : a.is_primary ? -1 : 1))

  return (
    <fieldset className="rounded-lg border border-hairline p-3">
      <legend className="px-1 text-xs font-medium uppercase tracking-wide text-ink/50">
        {SEGMENT_LABEL[segment]} — {activities.length} recordings
      </legend>
      <p className="mb-2 text-xs text-ink/50">
        Picking a recording replaces every number shown for this leg — time, distance,
        power, heart rate — not just the metric that differs between devices.
      </p>
      <div className="flex flex-col gap-2">
        {byRecency.map((activity) => {
          const inputId = `recording-${activity.id}`
          const details = [
            formatDistance(activity.distance_m),
            formatDuration(activity.elapsed_time),
            activity.avg_power != null ? `${Math.round(activity.avg_power)} W` : null,
            activity.avg_hr != null ? `${Math.round(activity.avg_hr)} bpm` : null,
          ]
            .filter((d): d is string => d != null)
            .join(' · ')

          return (
            <label
              key={activity.id}
              htmlFor={inputId}
              className="flex cursor-pointer items-start gap-2 text-sm"
            >
              <input
                type="radio"
                id={inputId}
                name={`primary-recording-${segment}-${raceId}`}
                checked={activity.is_primary}
                disabled={mutation.isPending}
                onChange={() => mutation.mutate({ activityId: activity.id, isPrimary: true })}
                className="mt-1"
              />
              <span>
                <span className="font-medium text-ink">
                  {activity.device_key ?? 'Unknown device'}
                </span>
                {activity.is_primary && (
                  <span className="ml-1.5 rounded border border-hairline px-1 text-[10px] uppercase tracking-wide text-ink/50">
                    primary
                  </span>
                )}
                <span className="tnum block text-ink/50">{details}</span>
              </span>
            </label>
          )
        })}
      </div>
      {mutation.isError && (
        <p role="alert" className="mt-2 text-xs font-medium text-ink">
          {mutation.error instanceof ApiError
            ? mutation.error.message
            : 'Could not switch the primary recording. Try again.'}
        </p>
      )}
    </fieldset>
  )
}
