/**
 * Formatting helpers. The backend stores durations as integer seconds, distances in
 * metres and never stores pace — everything human-readable is derived here.
 */

/** 3661 → "1:01:01", 185 → "3:05". Transitions are short, races are not. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`
}

/** Signed delta for official-vs-recorded comparison: -12 → "−0:12". */
export function formatDelta(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  const sign = seconds < 0 ? '−' : '+'
  return `${sign}${formatDuration(Math.abs(seconds))}`
}

/** Pace in min/km, derived from distance ÷ time (§3: never read the FIT avg_speed field). */
export function formatPace(distanceM: number | null | undefined, seconds: number | null | undefined): string {
  if (!distanceM || !seconds) return '—'
  const secPerKm = seconds / (distanceM / 1000)
  return `${formatDuration(secPerKm)}/km`
}

/** Swim pace conventionally reads per 100 m. */
export function formatSwimPace(distanceM: number | null | undefined, seconds: number | null | undefined): string {
  if (!distanceM || !seconds) return '—'
  return `${formatDuration(seconds / (distanceM / 100))}/100m`
}

export function formatSpeed(distanceM: number | null | undefined, seconds: number | null | undefined): string {
  if (!distanceM || !seconds) return '—'
  return `${((distanceM / 1000 / seconds) * 3600).toFixed(1)} km/h`
}

/** 33746 → "33.7 km"; below a kilometre stays in metres (transitions are 20–550 m). */
export function formatDistance(metres: number | null | undefined): string {
  if (metres == null) return '—'
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(1)} km`
}

export function formatElevation(metres: number | null | undefined): string {
  return metres == null ? '—' : `${Math.round(metres)} m`
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

export function formatNumber(value: number | null | undefined, unit = '', digits = 0): string {
  if (value == null) return '—'
  return `${value.toFixed(digits)}${unit}`
}

export const SEGMENT_ORDER = ['swim', 't1', 'bike', 't2', 'run'] as const
export type Segment = (typeof SEGMENT_ORDER)[number]

export const SEGMENT_LABEL: Record<Segment, string> = {
  swim: 'Swim',
  t1: 'T1',
  bike: 'Bike',
  t2: 'T2',
  run: 'Run',
}

/** Per-segment accent colours (§5). Transitions are neutral, visually subordinate. */
export const SEGMENT_COLOR: Record<Segment, string> = {
  swim: 'var(--color-swim)',
  t1: 'var(--color-transition)',
  bike: 'var(--color-bike)',
  t2: 'var(--color-transition)',
  run: 'var(--color-run)',
}

export const RACE_TYPE_LABEL: Record<string, string> = {
  sprint: 'Sprint',
  olympic: 'Olympic',
  half_iron: 'Half Iron',
  full_iron: 'Full Iron',
  custom: 'Custom',
}
