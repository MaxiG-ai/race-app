import {
  SEGMENT_COLOR,
  SEGMENT_LABEL,
  formatDelta,
  formatDistance,
  formatDuration,
} from '@/lib/format'
import { buildSegmentRows } from '@/lib/splits'
import type { OfficialResultRead, RecordedActivityRead } from '@/types'

/**
 * The centerpiece view (§3 Traps 6): recorded transitions are not ground truth —
 * T1 ranges 105–552 m across the fixtures, one Apple T2 is 20 m / 7.7 s, essentially
 * a miss — which is precisely the argument for showing official and recorded side
 * by side rather than trusting either alone.
 */

export function SplitsComparison({
  official,
  activities,
}: {
  official: OfficialResultRead | null | undefined
  activities: RecordedActivityRead[]
}) {
  const rows = buildSegmentRows(official, activities)
  const officialTotal = official?.total_time ?? null
  const allSegmentsRecorded = rows.every((r) => r.activity)
  // Session totals, per segment — never a sum of laps (§3 Traps 1). A partial sum
  // (some legs unrecorded) is labelled as such rather than silently rendered as a
  // real total.
  const recordedTotal = allSegmentsRecorded
    ? rows.reduce((sum, r) => sum + (r.activity?.elapsed_time ?? 0), 0)
    : null
  const totalDelta =
    recordedTotal != null && officialTotal != null ? recordedTotal - officialTotal : null

  return (
    <section aria-labelledby="splits-heading">
      <h2 id="splits-heading" className="text-sm font-semibold text-ink">
        Official vs. recorded
      </h2>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[420px] border-collapse text-sm">
          <caption className="sr-only">
            Official result compared with recorded activity data, per segment, including
            transitions. Recordings marked &quot;derived&quot; are inferred from the gap
            between surrounding legs, not measured.
          </caption>
          <thead>
            <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-ink/50">
              <th scope="col" className="py-2 pr-3 font-medium">
                Segment
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                Official
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                Recorded
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                Δ
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                Distance
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.segment} className="border-b border-hairline/60 last:border-0">
                <th scope="row" className="py-2 pr-3 text-left font-normal">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      aria-hidden="true"
                      className="inline-block h-2 w-2 shrink-0 rounded-full"
                      style={{ background: SEGMENT_COLOR[row.segment] }}
                    />
                    {SEGMENT_LABEL[row.segment]}
                  </span>
                </th>
                <td className="tnum py-2 pr-3">
                  {row.officialSeconds != null ? (
                    formatDuration(row.officialSeconds)
                  ) : (
                    <span className="text-ink/40">not entered</span>
                  )}
                </td>
                <td className="tnum py-2 pr-3">
                  {row.activity ? (
                    <span className="inline-flex items-center gap-1.5">
                      <span>{formatDuration(row.activity.elapsed_time)}</span>
                      {row.activity.is_derived && (
                        <span
                          title="Derived: an inference from the gap between the surrounding legs, not a measurement. No distance or GPS for this leg."
                          className="rounded border border-hairline px-1 text-[10px] normal-case leading-4 tracking-wide text-ink/50"
                        >
                          derived
                        </span>
                      )}
                    </span>
                  ) : (
                    <span className="text-ink/40">not recorded</span>
                  )}
                </td>
                <td className="tnum py-2 pr-3">
                  {row.deltaSeconds != null ? (
                    formatDelta(row.deltaSeconds)
                  ) : (
                    <span className="text-ink/40">—</span>
                  )}
                </td>
                <td className="tnum py-2 pr-3">
                  {row.activity?.distance_m != null ? (
                    formatDistance(row.activity.distance_m)
                  ) : (
                    <span className="text-ink/40">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-hairline text-sm font-medium">
              <th scope="row" className="py-2 pr-3 text-left font-medium">
                Total
              </th>
              <td className="tnum py-2 pr-3">
                {officialTotal != null ? (
                  formatDuration(officialTotal)
                ) : (
                  <span className="font-normal text-ink/40">—</span>
                )}
              </td>
              <td className="tnum py-2 pr-3">
                {recordedTotal != null ? (
                  formatDuration(recordedTotal)
                ) : (
                  <span
                    className="font-normal text-ink/40"
                    title="Not every segment has a recording yet — this total would understate the race."
                  >
                    partial
                  </span>
                )}
              </td>
              <td className="tnum py-2 pr-3">
                {totalDelta != null ? (
                  formatDelta(totalDelta)
                ) : (
                  <span className="font-normal text-ink/40">—</span>
                )}
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  )
}
