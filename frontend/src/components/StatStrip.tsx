import { useStatsSummary } from '@/api/stats'
import { formatDistance, formatElevation } from '@/lib/format'

/**
 * Thin, single-line aggregate above the timeline (§5). Deliberately not a dashboard —
 * timeline browsing is the primary interaction, so this stays quiet: no cards, no
 * icons competing for attention, just three numbers separated by hairline dots.
 */
export function StatStrip() {
  const { data, isLoading } = useStatsSummary()

  // Quiet on loading/error: this is a supporting line, not something worth a spinner
  // or an error banner of its own. The timeline below carries its own error state.
  if (isLoading || !data) {
    return <div className="h-5" aria-hidden="true" />
  }

  const races = `${data.race_count} ${data.race_count === 1 ? 'race' : 'races'}`

  return (
    <p className="flex flex-wrap items-baseline gap-x-2 text-sm text-ink/60">
      <span className="tnum">{races}</span>
      <span aria-hidden="true">·</span>
      <span className="tnum">{formatDistance(data.total_distance_m)}</span>
      <span aria-hidden="true">·</span>
      <span className="tnum">{formatElevation(data.total_elevation_gain_m)} gained</span>
    </p>
  )
}
