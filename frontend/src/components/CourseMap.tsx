import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { LatLngBoundsExpression, LatLngTuple } from 'leaflet'
import { useRaceTrack } from '@/api/track'
import { SEGMENT_COLOR, SEGMENT_LABEL, type Segment } from '@/lib/format'
import type { SegmentTrack } from '@/types'

/**
 * react-leaflet wrapper over `GET /races/{id}/track` (§5). Two lifecycle rules
 * matter here more than anything about the data:
 *
 * - The container needs an explicit height (`h-96` below) — Leaflet silently
 *   renders a 0px map otherwise.
 * - `MapContainer` must not be re-created on every render. `react-leaflet` itself
 *   already guards its internal `L.Map` instance against re-init as long as the
 *   component doesn't unmount; `key={raceId}` forces a clean remount only when
 *   navigating to a *different* race, never on a data refetch for the same one
 *   (e.g. a `RecordingSwitch` flip). Viewport fitting is handled by a child
 *   (`FitBounds`) that calls `map.fitBounds` from an effect instead of touching
 *   `MapContainer`'s own `center`/`bounds` props after mount — those are
 *   read once, at construction, and are never revisited by react-leaflet itself.
 */

const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
const OSM_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'

function unionBounds(segments: SegmentTrack[]): LatLngBoundsExpression | null {
  const withBounds = segments.filter((s) => s.bounds != null)
  if (withBounds.length === 0) return null

  let n = -90
  let s = 90
  let e = -180
  let w = 180
  for (const seg of withBounds) {
    const b = seg.bounds!
    n = Math.max(n, b.n_lat)
    s = Math.min(s, b.s_lat)
    e = Math.max(e, b.e_lng)
    w = Math.min(w, b.w_lng)
  }
  return [
    [s, w],
    [n, e],
  ]
}

/** Re-fits the viewport whenever the bounds *content* changes, without ever
 * touching (or recreating) the `L.Map` instance itself. */
function FitBounds({ bounds }: { bounds: LatLngBoundsExpression | null }) {
  const map = useMap()
  const key = bounds ? JSON.stringify(bounds) : null

  useEffect(() => {
    if (bounds) map.fitBounds(bounds, { padding: [24, 24] })
    // `key` is a content-derived id for `bounds`; re-running only when the
    // actual coordinates change is the point (not on every parent render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return null
}

export function CourseMap({
  raceId,
  fallbackCenter,
}: {
  raceId: number
  fallbackCenter?: { lat: number; lng: number } | null
}) {
  const trackQuery = useRaceTrack(raceId, 'low')

  if (trackQuery.isPending) {
    return <MapPlaceholder>Loading course map…</MapPlaceholder>
  }
  if (trackQuery.isError) {
    return <MapPlaceholder>Map unavailable.</MapPlaceholder>
  }

  const segments = trackQuery.data.segments.filter((s) => s.points.length > 0)

  if (segments.length === 0) {
    // A race with no GPS at all (manually entered, or not yet imported) still
    // gets something sensible: the race location if we have it, or plain text.
    if (fallbackCenter) {
      const center: LatLngTuple = [fallbackCenter.lat, fallbackCenter.lng]
      return (
        <div>
          <MapContainer
            key={raceId}
            center={center}
            zoom={12}
            scrollWheelZoom={false}
            className="h-96 w-full rounded-lg"
          >
            <TileLayer attribution={OSM_ATTRIBUTION} url={OSM_URL} />
            <CircleMarker
              center={center}
              radius={8}
              pathOptions={{
                color: 'var(--color-ink)',
                fillColor: 'var(--color-ink)',
                fillOpacity: 0.5,
              }}
            >
              <Tooltip>Race location</Tooltip>
            </CircleMarker>
          </MapContainer>
          <p className="mt-2 text-xs text-ink/50">
            No GPS track recorded for this race — showing its location instead.
          </p>
        </div>
      )
    }
    return <MapPlaceholder>No GPS data available for this race.</MapPlaceholder>
  }

  const bounds = unionBounds(segments)
  const firstPoint = segments[0]!.points[0]!
  const initialCenter: LatLngTuple = fallbackCenter
    ? [fallbackCenter.lat, fallbackCenter.lng]
    : [firstPoint.lat, firstPoint.lng]

  return (
    <div>
      <MapContainer
        key={raceId}
        center={initialCenter}
        zoom={13}
        scrollWheelZoom={false}
        className="h-96 w-full rounded-lg"
      >
        <TileLayer attribution={OSM_ATTRIBUTION} url={OSM_URL} />
        <FitBounds bounds={bounds} />
        {segments.map((seg) => {
          const segment = seg.segment as Segment
          return (
            <Polyline
              key={seg.recorded_activity_id}
              positions={seg.points.map((p): LatLngTuple => [p.lat, p.lng])}
              pathOptions={{ color: SEGMENT_COLOR[segment], weight: 3 }}
            >
              <Tooltip sticky>{SEGMENT_LABEL[segment]}</Tooltip>
            </Polyline>
          )
        })}
      </MapContainer>
      <Legend segments={segments.map((s) => s.segment as Segment)} />
    </div>
  )
}

function Legend({ segments }: { segments: Segment[] }) {
  const unique = [...new Set(segments)]
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink/60">
      {unique.map((segment) => (
        <li key={segment} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-0.5 w-4 shrink-0 rounded-full"
            style={{ background: SEGMENT_COLOR[segment] }}
          />
          {SEGMENT_LABEL[segment]}
        </li>
      ))}
    </ul>
  )
}

function MapPlaceholder({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-96 w-full items-center justify-center rounded-lg border border-hairline text-sm text-ink/50">
      {children}
    </div>
  )
}
