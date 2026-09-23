import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useRace, useRefreshWeather } from '@/api/races'
import { CourseMap } from '@/components/CourseMap'
import { FitUpload } from '@/components/FitUpload'
import { RecordingSwitch } from '@/components/RecordingSwitch'
import { GearPicker } from '@/components/GearPicker'
import { SegmentBar } from '@/components/SegmentBar'
import { SplitsChart } from '@/components/SplitsChart'
import { SplitsComparison } from '@/components/SplitsComparison'
import { buildSegmentRows, toSegmentSummaries } from '@/lib/splits'
import {
  SEGMENT_LABEL,
  SEGMENT_ORDER,
  formatDate,
  type Segment,
} from '@/lib/format'

const buttonClass =
  'rounded-md border border-hairline px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink'

export default function RaceDetail() {
  const params = useParams<{ id: string }>()
  const raceId = Number(params.id)
  const query = useRace(raceId)
  const refreshWeather = useRefreshWeather(raceId)

  if (!Number.isFinite(raceId)) {
    return (
      <StatusPanel>
        <p className="text-sm text-ink/70">This race link looks broken.</p>
        <Link to="/" className={`mt-3 inline-block ${buttonClass}`}>
          Back to timeline
        </Link>
      </StatusPanel>
    )
  }

  if (query.isPending) {
    return <RaceDetailSkeleton />
  }

  if (query.isError) {
    // Someone else's race id returns 404 by design (§4 Tenancy) — the API never
    // confirms whether it exists, so this reads the same as "not found".
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <StatusPanel>
          <p className="text-sm text-ink/70">This race doesn&rsquo;t exist, or isn&rsquo;t yours.</p>
          <Link to="/" className={`mt-3 inline-block ${buttonClass}`}>
            Back to timeline
          </Link>
        </StatusPanel>
      )
    }
    return (
      <StatusPanel>
        <p className="text-sm text-ink/70">
          {query.error instanceof ApiError
            ? query.error.message
            : 'Something went wrong loading this race.'}
        </p>
        <button type="button" onClick={() => query.refetch()} className={`mt-3 ${buttonClass}`}>
          Retry
        </button>
      </StatusPanel>
    )
  }

  const race = query.data

  const segmentRows = buildSegmentRows(race.official_result, race.recorded_activities)
  const segmentSummaries = toSegmentSummaries(segmentRows)

  // Only real recordings drive the pacing chart's tabs — a derived transition
  // carries no laps by construction (§3 Grouping), so offering it as a tab would
  // just be a permanent "no lap data" dead end.
  const presentSegments: Segment[] = SEGMENT_ORDER.filter((segment) =>
    race.recorded_activities.some((a) => a.segment === segment && !a.is_derived),
  )

  // A `RecordingSwitch` only makes sense where a segment actually has more than
  // one recording (§2 `is_primary`) — most segments won't.
  const recordingsBySegment = SEGMENT_ORDER.map((segment) => ({
    segment,
    activities: race.recorded_activities.filter((a) => a.segment === segment),
  })).filter((g) => g.activities.length > 1)

  // Derived transitions were never recorded — they carry only a duration (§3
  // Grouping), so there is no device and nothing to attach gear to.
  const primaryActivities = race.recorded_activities
    .filter((a) => a.is_primary && !a.is_derived)
    .sort(
      (a, b) =>
        SEGMENT_ORDER.indexOf(a.segment as Segment) - SEGMENT_ORDER.indexOf(b.segment as Segment),
    )

  const fuelings = [...race.fuelings].sort(
    (a, b) => SEGMENT_ORDER.indexOf(a.segment as Segment) - SEGMENT_ORDER.indexOf(b.segment as Segment),
  )
  const hasAnyCarbs = fuelings.some((f) => f.carbs_g != null)
  // Total carbs is derived by summing rows, never stored (§2).
  const totalCarbs = fuelings.reduce((sum, f) => sum + (f.carbs_g ?? 0), 0)

  const fallbackCenter =
    race.location_lat != null && race.location_lng != null
      ? { lat: race.location_lat, lng: race.location_lng }
      : null

  return (
    <div className="flex flex-col gap-10">
      <header>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-ink">{race.name}</h1>
            <p className="mt-1 text-sm text-ink/60">
              {formatDate(race.date)} &middot; {race.category?.label ?? 'Unclassified'}
              {race.location_name ? ` · ${race.location_name}` : ''}
            </p>
          </div>
          <Link to={`/races/${race.id}/edit`} className={buttonClass}>
            Edit
          </Link>
        </div>
        <div className="mt-4">
          <SegmentBar segments={segmentSummaries} source="recorded" height="h-2.5" />
        </div>
        {race.notes && <p className="mt-4 whitespace-pre-wrap text-sm text-ink/70">{race.notes}</p>}
      </header>

      <CourseMap raceId={race.id} fallbackCenter={fallbackCenter} />

      <section aria-labelledby="add-recording-heading">
        <h2 id="add-recording-heading" className="text-sm font-semibold text-ink">
          Add a recording
        </h2>
        <p className="mt-1 text-sm text-ink/60">
          Backfill a leg you forgot to upload, or add a second recording of one you
          already have — e.g. a bike computer with power alongside a watch. Both
          recordings are kept; use &ldquo;Multiple recordings&rdquo; below to pick which
          one counts.
        </p>
        <div className="mt-3">
          <FitUpload raceId={race.id} />
        </div>
      </section>

      <SplitsComparison official={race.official_result} activities={race.recorded_activities} />

      {recordingsBySegment.length > 0 && (
        <section aria-labelledby="recordings-heading">
          <h2 id="recordings-heading" className="text-sm font-semibold text-ink">
            Multiple recordings
          </h2>
          <div className="mt-3 flex flex-col gap-3">
            {recordingsBySegment.map((group) => (
              <RecordingSwitch
                key={group.segment}
                raceId={race.id}
                segment={group.segment}
                activities={group.activities}
              />
            ))}
          </div>
        </section>
      )}

      <SplitsChart raceId={race.id} segments={presentSegments} />

      {primaryActivities.length > 0 && (
        <section aria-labelledby="gear-heading">
          <h2 id="gear-heading" className="text-sm font-semibold text-ink">
            Gear
          </h2>
          {/* Per segment, not per race (§3): mileage per bike and per pair of shoes
              is the payoff, so gear hangs off the recording. Only the primary
              recording of each segment gets a picker — assigning gear to a
              recording that doesn't count for the leg would not accrue distance. */}
          <div className="mt-3 flex flex-col gap-3">
            {primaryActivities.map((activity) => (
              <GearPicker
                key={activity.id}
                activityId={activity.id}
                segment={activity.segment as Segment}
              />
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="weather-heading">
        <div className="flex items-baseline justify-between gap-2">
          <h2 id="weather-heading" className="text-sm font-semibold text-ink">
            Weather
          </h2>
          <button
            type="button"
            onClick={() => refreshWeather.mutate(undefined)}
            disabled={refreshWeather.isPending}
            className="text-xs text-ink/50 underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:opacity-40"
          >
            {refreshWeather.isPending ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink/70">
          <span className="tnum">
            {race.weather_temp_c != null ? (
              `${Math.round(race.weather_temp_c)}°C`
            ) : (
              <span className="text-ink/40">Temp —</span>
            )}
          </span>
          <span className="tnum">
            {race.weather_humidity_pct != null ? (
              `${Math.round(race.weather_humidity_pct)}% humidity`
            ) : (
              <span className="text-ink/40">Humidity —</span>
            )}
          </span>
          <span>{race.weather_conditions ?? <span className="text-ink/40">Conditions —</span>}</span>
        </p>
      </section>

      <section aria-labelledby="fueling-heading">
        <h2 id="fueling-heading" className="text-sm font-semibold text-ink">
          Fueling
        </h2>
        {fuelings.length === 0 ? (
          <p className="mt-3 text-sm text-ink/50">No fueling recorded for this race.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[420px] border-collapse text-sm">
              <caption className="sr-only">Carbohydrate intake and products per segment.</caption>
              <thead>
                <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-ink/50">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Segment
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Carbs
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Products
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Notes
                  </th>
                </tr>
              </thead>
              <tbody>
                {fuelings.map((f) => (
                  <tr key={f.id} className="border-b border-hairline/60 align-top last:border-0">
                    <th scope="row" className="py-2 pr-3 text-left font-normal">
                      {SEGMENT_LABEL[f.segment as Segment]}
                    </th>
                    <td className="tnum py-2 pr-3">
                      {f.carbs_g != null ? `${f.carbs_g} g` : <span className="text-ink/40">—</span>}
                    </td>
                    <td className="py-2 pr-3">
                      {f.products && f.products.length > 0 ? (
                        f.products
                          .map((p) => `${p.name}${p.quantity != null ? ` ×${p.quantity}` : ''}`)
                          .join(', ')
                      ) : (
                        <span className="text-ink/40">—</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-ink/70">
                      {f.notes ?? <span className="text-ink/40">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
              {hasAnyCarbs && (
                <tfoot>
                  <tr className="border-t border-hairline font-medium">
                    <th scope="row" className="py-2 pr-3 text-left font-medium">
                      Total
                    </th>
                    <td
                      className="tnum py-2 pr-3"
                      title="Derived by summing the rows above — not itself stored."
                    >
                      {totalCarbs} g
                    </td>
                    <td />
                    <td />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function StatusPanel({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-hairline px-4 py-10 text-center">{children}</div>
}

function RaceDetailSkeleton() {
  return (
    <div aria-hidden="true" className="flex animate-pulse flex-col gap-6">
      <div className="h-6 w-1/2 rounded bg-hairline/40" />
      <div className="h-2.5 w-full rounded-full bg-hairline/40" />
      <div className="h-96 w-full rounded-lg bg-hairline/20" />
      <div className="h-40 w-full rounded-lg bg-hairline/20" />
    </div>
  )
}
