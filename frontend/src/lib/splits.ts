import { SEGMENT_ORDER, type Segment } from '@/lib/format'
import type { OfficialResultRead, RecordedActivityRead, SegmentSummary } from '@/types'

/**
 * The official-vs-recorded join, kept out of the component file so both the table
 * and the shared `SegmentBar` import it from one place — the summary strip atop
 * the detail page and the table below it must never disagree about which recording
 * counts for a segment.
 */

const OFFICIAL_FIELD = {
  swim: 'swim_time',
  t1: 't1_time',
  bike: 'bike_time',
  t2: 't2_time',
  run: 'run_time',
} as const satisfies Record<Segment, keyof OfficialResultRead>

export interface SegmentRow {
  segment: Segment
  officialSeconds: number | null
  /** The `is_primary` recording for this segment, or the sole one if none is flagged. */
  activity: RecordedActivityRead | null
  deltaSeconds: number | null
}

/**
 * Joins official result + recorded activities per segment. Exported so `RaceDetail`
 * can feed the exact same join into the shared `SegmentBar` — the summary strip
 * atop the page and this table must never disagree about which recording counts.
 */
export function buildSegmentRows(
  official: OfficialResultRead | null | undefined,
  activities: RecordedActivityRead[],
): SegmentRow[] {
  return SEGMENT_ORDER.map((segment) => {
    const activity =
      activities.find((a) => a.segment === segment && a.is_primary) ??
      activities.find((a) => a.segment === segment) ??
      null
    const officialSeconds = official?.[OFFICIAL_FIELD[segment]] ?? null
    const deltaSeconds =
      activity && officialSeconds != null ? activity.elapsed_time - officialSeconds : null
    return { segment, officialSeconds, activity, deltaSeconds }
  })
}

export function toSegmentSummaries(rows: SegmentRow[]): SegmentSummary[] {
  return rows.map((r) => ({
    segment: r.segment,
    official_time_s: r.officialSeconds,
    recorded_time_s: r.activity?.elapsed_time ?? null,
    recorded_distance_m: r.activity?.distance_m ?? null,
    is_derived: r.activity?.is_derived ?? null,
  }))
}
