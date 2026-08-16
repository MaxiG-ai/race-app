import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useRaceLaps } from '@/api/laps'
import {
  SEGMENT_COLOR,
  SEGMENT_LABEL,
  formatDistance,
  formatDuration,
  formatPace,
  formatSwimPace,
  type Segment,
} from '@/lib/format'
import type { LapSplitRead } from '@/types'

/**
 * Recharts pacing view, filterable by sport (§5). Two rules from §3 Traps shape
 * everything here:
 *
 * - Traps 2/7: power presence is a device property, not a sport property, and lap
 *   counts vary by vendor (an Apple swim is one lap, a Garmin swim is four). So
 *   which series render, and whether laps render as a line at all, is decided by
 *   inspecting the fetched laps — never hardcoded per sport.
 * - Trap 1: laps carry pacing shape only. Segment totals are never derived by
 *   summing them here; that number lives on `RecordedActivityRead.elapsed_time`
 *   and is rendered by `SplitsComparison`.
 */

interface LapPoint {
  lapIndex: number
  distance_m: number | null
  pace: number | null
  hr: number | null
  power: number | null
  normalizedPower: number | null
  cadence: number | null
}

function toLapPoints(laps: LapSplitRead[], segment: Segment): LapPoint[] {
  const unitMetres = segment === 'swim' ? 100 : 1000
  return [...laps]
    .sort((a, b) => a.lap_index - b.lap_index)
    .map((lap) => ({
      lapIndex: lap.lap_index,
      distance_m: lap.distance_m,
      // Pace derived from distance ÷ time, never the FIT avg_speed field (§3).
      pace:
        lap.distance_m && lap.total_time ? lap.total_time / (lap.distance_m / unitMetres) : null,
      hr: lap.avg_hr,
      power: lap.avg_power,
      normalizedPower: lap.normalized_power,
      cadence: lap.cadence_avg,
    }))
}

export function SplitsChart({ raceId, segments }: { raceId: number; segments: Segment[] }) {
  const [selected, setSelected] = useState<Segment | null>(segments[0] ?? null)
  const [view, setView] = useState<'chart' | 'table'>('chart')

  // The set of recorded segments can change under us (e.g. a RecordingSwitch flip
  // elsewhere invalidates the race and a segment that had no primary now does).
  // Re-anchor the selection rather than pointing at a tab that no longer exists.
  useEffect(() => {
    setSelected((current) => (current && segments.includes(current) ? current : (segments[0] ?? null)))
  }, [segments])

  // Hooks run unconditionally on every render — the "no segments" early return
  // happens below, after `useRaceLaps` has already been called with `undefined`
  // (which the hook treats as disabled).
  const activeSegment = selected && segments.includes(selected) ? selected : (segments[0] ?? null)
  const lapsQuery = useRaceLaps(raceId, activeSegment ?? undefined)
  const laps = lapsQuery.data
  const canToggleView = laps != null && laps.length > 1

  if (segments.length === 0 || activeSegment === null) {
    return (
      <section aria-labelledby="pacing-heading">
        <h2 id="pacing-heading" className="text-sm font-semibold text-ink">
          Pacing
        </h2>
        <p className="mt-3 text-sm text-ink/50">
          No recorded activity data to chart for this race.
        </p>
      </section>
    )
  }

  return (
    <section aria-labelledby="pacing-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="pacing-heading" className="text-sm font-semibold text-ink">
          Pacing
        </h2>
        {canToggleView && (
          <button
            type="button"
            onClick={() => setView((v) => (v === 'chart' ? 'table' : 'chart'))}
            className="rounded-md border border-hairline px-2 py-1 text-xs text-ink/70 transition-colors hover:border-ink/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
          >
            {view === 'chart' ? 'Show as table' : 'Show as chart'}
          </button>
        )}
      </div>

      <div
        role="tablist"
        aria-label="Segment"
        className="mt-3 flex flex-wrap gap-1 border-b border-hairline"
      >
        {segments.map((segment) => {
          const isActive = segment === activeSegment
          return (
            <button
              key={segment}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setSelected(segment)}
              className={`border-b-2 px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink ${
                isActive ? 'text-ink' : 'border-transparent text-ink/40 hover:text-ink/70'
              }`}
              style={isActive ? { borderColor: SEGMENT_COLOR[segment] } : undefined}
            >
              {SEGMENT_LABEL[segment]}
            </button>
          )
        })}
      </div>

      <div className="mt-4">
        {lapsQuery.isPending ? (
          <p className="text-sm text-ink/50">Loading laps…</p>
        ) : lapsQuery.isError ? (
          <p className="text-sm text-ink/50">Could not load lap data for this segment.</p>
        ) : laps!.length === 0 ? (
          <p className="text-sm text-ink/50">
            No lap data recorded for {SEGMENT_LABEL[activeSegment].toLowerCase()}.
          </p>
        ) : laps!.length === 1 ? (
          <SummaryTile segment={activeSegment} lap={laps![0]!} />
        ) : view === 'table' ? (
          <LapTable segment={activeSegment} laps={laps!} />
        ) : (
          <PacingCharts segment={activeSegment} laps={laps!} />
        )}
      </div>
    </section>
  )
}

interface SeriesSpec {
  key: keyof LapPoint
  label: string
  color: string
  dashed?: boolean
}

function MetricPanel({
  title,
  data,
  series,
  valueFormatter,
  sparse,
}: {
  title: string
  data: LapPoint[]
  series: SeriesSpec[]
  valueFormatter: (value: number) => string
  sparse: boolean
}) {
  return (
    <div>
      <h3 className="text-xs font-medium uppercase tracking-wide text-ink/50">{title}</h3>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-hairline)" vertical={false} />
          <XAxis
            dataKey="lapIndex"
            tickLine={false}
            axisLine={{ stroke: 'var(--color-hairline)' }}
            tick={{ fontSize: 11, fill: 'var(--color-ink)' }}
          />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={46}
            tick={{ fontSize: 11, fill: 'var(--color-ink)' }}
            tickFormatter={(v: number) => valueFormatter(v)}
            domain={['auto', 'auto']}
          />
          <Tooltip
            cursor={{ stroke: 'var(--color-hairline)' }}
            formatter={(value) => (typeof value === 'number' ? valueFormatter(value) : '—')}
            labelFormatter={(label) => `Lap ${label}`}
            contentStyle={{ fontSize: 12, borderColor: 'var(--color-hairline)' }}
          />
          {series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
          {series.map((s) => (
            <Line
              key={String(s.key)}
              name={s.label}
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              strokeDasharray={s.dashed ? '4 3' : undefined}
              dot={sparse ? { r: 4, strokeWidth: 0, fill: s.color } : false}
              activeDot={{ r: 5 }}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function PacingCharts({ segment, laps }: { segment: Segment; laps: LapSplitRead[] }) {
  const points = toLapPoints(laps, segment)
  const color = SEGMENT_COLOR[segment]
  const sparse = points.length <= 8
  const paceUnit = segment === 'swim' ? 'min/100 m' : 'min/km'

  const hasPace = points.some((p) => p.pace != null)
  const hasHr = points.some((p) => p.hr != null)
  const hasPower = points.some((p) => p.power != null)
  const hasNormalizedPower = points.some((p) => p.normalizedPower != null)
  const hasCadence = points.some((p) => p.cadence != null)

  if (!hasPace && !hasHr && !hasPower && !hasCadence) {
    return (
      <p className="text-sm text-ink/50">
        These laps carry no pace, heart rate, power or cadence data.
      </p>
    )
  }

  const powerSeries: SeriesSpec[] = [{ key: 'power', label: 'Avg power', color }]
  if (hasNormalizedPower) {
    powerSeries.push({
      key: 'normalizedPower',
      label: 'Normalized power',
      color: 'var(--color-ink)',
      dashed: true,
    })
  }

  return (
    <div className="flex flex-col gap-6">
      {hasPace && (
        <MetricPanel
          title={`Pace (${paceUnit})`}
          data={points}
          series={[{ key: 'pace', label: 'Pace', color }]}
          valueFormatter={(v) => formatDuration(v)}
          sparse={sparse}
        />
      )}
      {hasHr && (
        <MetricPanel
          title="Heart rate (bpm)"
          data={points}
          series={[{ key: 'hr', label: 'Heart rate', color }]}
          valueFormatter={(v) => `${Math.round(v)}`}
          sparse={sparse}
        />
      )}
      {hasPower && (
        <MetricPanel
          title="Power (W)"
          data={points}
          series={powerSeries}
          valueFormatter={(v) => `${Math.round(v)}`}
          sparse={sparse}
        />
      )}
      {hasCadence && (
        <MetricPanel
          title="Cadence (rpm/spm)"
          data={points}
          series={[{ key: 'cadence', label: 'Cadence', color }]}
          valueFormatter={(v) => `${Math.round(v)}`}
          sparse={sparse}
        />
      )}
    </div>
  )
}

function SummaryTile({ segment, lap }: { segment: Segment; lap: LapSplitRead }) {
  const pace =
    lap.distance_m && lap.total_time
      ? segment === 'swim'
        ? formatSwimPace(lap.distance_m, lap.total_time)
        : formatPace(lap.distance_m, lap.total_time)
      : null

  const stats: { label: string; value: string }[] = [
    { label: 'Distance', value: formatDistance(lap.distance_m) },
    { label: 'Time', value: formatDuration(lap.total_time) },
  ]
  if (pace) stats.push({ label: 'Pace', value: pace })
  if (lap.avg_hr != null) stats.push({ label: 'Avg HR', value: `${Math.round(lap.avg_hr)} bpm` })
  if (lap.avg_power != null)
    stats.push({ label: 'Avg power', value: `${Math.round(lap.avg_power)} W` })
  if (lap.normalized_power != null)
    stats.push({ label: 'Normalized power', value: `${Math.round(lap.normalized_power)} W` })
  if (lap.cadence_avg != null)
    stats.push({ label: 'Cadence', value: `${Math.round(lap.cadence_avg)}` })

  return (
    <div>
      <p className="mb-2 text-xs text-ink/50">
        Recorded as a single lap for the whole segment — a summary, not a pacing line.
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-hairline p-3 sm:grid-cols-3">
        {stats.map((s) => (
          <div key={s.label}>
            <dt className="text-xs uppercase tracking-wide text-ink/50">{s.label}</dt>
            <dd className="tnum text-sm font-medium text-ink">{s.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function LapTable({ segment, laps }: { segment: Segment; laps: LapSplitRead[] }) {
  const sorted = [...laps].sort((a, b) => a.lap_index - b.lap_index)
  const hasPartial = sorted.some((l) => l.is_partial)

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] border-collapse text-sm">
        <caption className="sr-only">
          Lap-by-lap pacing data for {SEGMENT_LABEL[segment]}.
        </caption>
        <thead>
          <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-ink/50">
            <th scope="col" className="py-2 pr-3 font-medium">
              Lap
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Distance
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Time
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Pace
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              HR
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Power
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Cadence
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((lap) => (
            <tr key={lap.id} className="border-b border-hairline/60 last:border-0">
              <th scope="row" className="tnum py-2 pr-3 text-left font-normal">
                {lap.lap_index}
                {lap.is_partial ? ' *' : ''}
              </th>
              <td className="tnum py-2 pr-3">{formatDistance(lap.distance_m)}</td>
              <td className="tnum py-2 pr-3">{formatDuration(lap.total_time)}</td>
              <td className="tnum py-2 pr-3">
                {segment === 'swim'
                  ? formatSwimPace(lap.distance_m, lap.total_time)
                  : formatPace(lap.distance_m, lap.total_time)}
              </td>
              <td className="tnum py-2 pr-3">
                {lap.avg_hr != null ? Math.round(lap.avg_hr) : '—'}
              </td>
              <td className="tnum py-2 pr-3">
                {lap.avg_power != null ? Math.round(lap.avg_power) : '—'}
                {lap.normalized_power != null ? ` (NP ${Math.round(lap.normalized_power)})` : ''}
              </td>
              <td className="tnum py-2 pr-3">
                {lap.cadence_avg != null ? Math.round(lap.cadence_avg) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {hasPartial && <p className="mt-1 text-xs text-ink/40">* trailing partial lap</p>}
    </div>
  )
}
