import { Link } from 'react-router-dom'
import { SegmentBar } from './SegmentBar'
import { RACE_TYPE_LABEL, formatDate, formatDuration } from '@/lib/format'
import type { RaceListItem } from '@/types'

/**
 * Timeline preview card (§5 Components). Both official and recorded totals are shown
 * when present — official-vs-recorded is the centerpiece of the app (§3 Traps 6), so
 * the timeline hints at it even before opening the detail view. Either total, or the
 * per-segment data behind the bar, can legitimately be missing: a race can have an
 * official result with no import yet, or an import with no official result entered.
 */
export function RaceCard({ race }: { race: RaceListItem }) {
  // Prefer the recorded shape for the bar — it's the one with real per-segment
  // granularity; official entry is usually just a handful of totals.
  const barSource = race.segments.some((s) => s.recorded_time_s != null) ? 'recorded' : 'official'

  const noTotals = race.total_official_time_s == null && race.total_recorded_time_s == null

  // `badges` already carries the race-type label today (backend derives it from
  // `race_type`), but that's an implementation detail, not a contract — guard against
  // it being duplicated, and fall back to deriving it ourselves if it's ever absent.
  // RACE_TYPE_LABEL keeps the fallback human-readable rather than a raw enum value.
  const typeLabel = RACE_TYPE_LABEL[race.race_type] ?? race.race_type
  const badges = race.badges.includes(typeLabel) ? race.badges : [typeLabel, ...race.badges]

  return (
    <Link
      to={`/races/${race.id}`}
      className="block rounded-lg border border-hairline bg-bg px-4 py-3 transition-colors hover:border-ink/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="truncate text-base font-medium text-ink">{race.name}</h2>
        <time dateTime={race.date} className="shrink-0 text-sm text-ink/50">
          {formatDate(race.date)}
        </time>
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {badges.map((badge) => (
          <span
            key={badge}
            className="rounded-full border border-hairline px-2 py-0.5 text-xs text-ink/70"
          >
            {badge}
          </span>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-sm">
        {race.total_official_time_s != null && (
          <span className="tnum text-ink">
            <span className="text-ink/50">Official </span>
            {formatDuration(race.total_official_time_s)}
          </span>
        )}
        {race.total_recorded_time_s != null && (
          <span className="tnum text-ink">
            <span className="text-ink/50">Recorded </span>
            {formatDuration(race.total_recorded_time_s)}
          </span>
        )}
        {noTotals && <span className="text-ink/40">No times yet</span>}
      </div>

      <div className="mt-3">
        <SegmentBar segments={race.segments} source={barSource} />
      </div>
    </Link>
  )
}
