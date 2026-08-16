import { SEGMENT_COLOR, SEGMENT_LABEL, formatDuration, type Segment } from '@/lib/format'
import type { SegmentSummary } from '@/types'

/**
 * The signature element (§5): a proportional swim/bike/run time bar, reused on the
 * timeline card and the detail view. It encodes real pacing balance rather than
 * decorating the card.
 *
 * Two rules from the plan shape the rendering:
 * - Transitions are real segments but visually subordinate — neutral hairline
 *   slivers, never competing with the three sports.
 * - A derived transition is an inference from two timestamps, not a measurement
 *   (§3 Grouping), so it renders hatched. It must never be mistakable for
 *   recorded data.
 */

const MIN_PERCENT = 1.2 // keeps a 7-second T2 visible instead of collapsing to nothing

export function SegmentBar({
  segments,
  source = 'recorded',
  height = 'h-2',
}: {
  segments: SegmentSummary[]
  source?: 'recorded' | 'official'
  height?: string
}) {
  const timeOf = (s: SegmentSummary) =>
    (source === 'official' ? s.official_time_s : s.recorded_time_s) ?? 0

  const present = segments.filter((s) => timeOf(s) > 0)
  const total = present.reduce((sum, s) => sum + timeOf(s), 0)

  if (total === 0) {
    return (
      <div className={`${height} w-full rounded-full bg-hairline`} aria-hidden="true" />
    )
  }

  // Slivers are widened to a visible floor, so the remaining width is shared out
  // among the segments that are already above it — otherwise the bar overflows.
  const raw = present.map((s) => ({ segment: s, percent: (timeOf(s) / total) * 100 }))
  const belowFloor = raw.filter((r) => r.percent < MIN_PERCENT)
  const borrowed = belowFloor.reduce((sum, r) => sum + (MIN_PERCENT - r.percent), 0)
  const aboveTotal = raw
    .filter((r) => r.percent >= MIN_PERCENT)
    .reduce((sum, r) => sum + r.percent, 0)

  return (
    <div
      className={`${height} flex w-full overflow-hidden rounded-full`}
      role="img"
      aria-label={present
        .map((s) => `${SEGMENT_LABEL[s.segment as Segment]} ${formatDuration(timeOf(s))}`)
        .join(', ')}
    >
      {raw.map(({ segment, percent }) => {
        const width =
          percent < MIN_PERCENT
            ? MIN_PERCENT
            : aboveTotal > 0
              ? percent - (percent / aboveTotal) * borrowed
              : percent
        const key = segment.segment as Segment
        const color = SEGMENT_COLOR[key]
        return (
          <div
            key={key}
            title={`${SEGMENT_LABEL[key]} · ${formatDuration(timeOf(segment))}${
              segment.is_derived ? ' · derived from the gap between legs' : ''
            }`}
            style={{
              width: `${width}%`,
              background: segment.is_derived
                ? `repeating-linear-gradient(45deg, ${color}, ${color} 2px, transparent 2px, transparent 4px)`
                : color,
            }}
          />
        )
      })}
    </div>
  )
}
