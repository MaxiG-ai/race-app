import { useEffect, useState } from 'react'
import { ApiError } from '@/api/client'
import { useActivityDetail, useAssignGear, useGearList } from '@/api/gear'
import { SEGMENT_LABEL } from '@/lib/format'
import type { GearKind, Segment } from '@/types'

const SEGMENT_GEAR_KIND: Partial<Record<Segment, GearKind>> = {
  swim: 'wetsuit',
  bike: 'bike',
  run: 'shoes',
}

export interface GearPickerProps {
  /** `RecordedActivity.id` — the specific recording this picker assigns gear to. */
  activityId: number
  /** The activity's segment; used to filter/label gear options (§3 Gear detection). */
  segment: Segment
}

/**
 * Per-segment gear assignment (`PATCH /activities/{id}`, §5 Components). Self-contained
 * on purpose — the race detail page (owned elsewhere in this pass) only needs to mount
 * `<GearPicker activityId={...} segment={...} />` per recorded segment; everything else
 * (current assignment, the shoe suggestion, saving) is handled here.
 *
 * Shoes can't be auto-detected (§3: no sensor, and the Apple files list only the watch),
 * so `RecordedActivityDetail.suggested_gear_id` — the last-used pair — prefills the
 * `run` segment's picker when nothing is assigned yet. It's only a prefill: the user
 * still has to press Save, same as any other selection.
 */
export function GearPicker({ activityId, segment }: GearPickerProps) {
  const { data: activity, isLoading: activityLoading } = useActivityDetail(activityId)
  const { data: gearList, isLoading: gearLoading } = useGearList(true)
  const assignGear = useAssignGear(activityId)

  const [selected, setSelected] = useState<number | ''>('')
  const [saved, setSaved] = useState(false)

  const currentGearId = activity?.gear[0]?.gear_id ?? null

  useEffect(() => {
    if (!activity) return
    if (currentGearId != null) setSelected(currentGearId)
    else if (activity.suggested_gear_id != null) setSelected(activity.suggested_gear_id)
    else setSelected('')
  }, [activity, currentGearId])

  const kind = SEGMENT_GEAR_KIND[segment]
  const options = (gearList ?? []).filter(
    (g) => (!kind || g.kind === kind) && (g.retired_at == null || g.id === currentGearId),
  )

  const isSuggestionPrefill =
    currentGearId == null && activity?.suggested_gear_id != null && selected === activity.suggested_gear_id

  async function handleSave() {
    setSaved(false)
    try {
      await assignGear.mutateAsync(selected === '' ? null : selected)
      setSaved(true)
    } catch {
      // surfaced below via assignGear.isError
    }
  }

  if (activityLoading || gearLoading) {
    return <p className="text-sm text-ink/50">Loading gear…</p>
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor={`gear-picker-${activityId}`} className="text-sm text-ink/70">
        {SEGMENT_LABEL[segment]} gear
      </label>
      <select
        id={`gear-picker-${activityId}`}
        value={selected}
        onChange={(e) => {
          setSelected(e.target.value === '' ? '' : Number(e.target.value))
          setSaved(false)
        }}
        className="rounded border border-hairline bg-bg px-2 py-1 text-sm"
      >
        <option value="">— none —</option>
        {options.map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
            {g.retired_at ? ' (retired)' : ''}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={handleSave}
        disabled={assignGear.isPending}
        className="rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5 disabled:opacity-50"
      >
        {assignGear.isPending ? 'Saving…' : 'Save'}
      </button>
      {isSuggestionPrefill && (
        <span className="text-xs text-ink/40">suggested from last use</span>
      )}
      {saved && !assignGear.isPending && !assignGear.isError && (
        <span className="text-xs text-ink/50">Saved.</span>
      )}
      {assignGear.isError && (
        <span className="text-xs text-run">
          {assignGear.error instanceof ApiError ? assignGear.error.message : 'Could not save gear.'}
        </span>
      )}
    </div>
  )
}
