import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { ApiError } from '@/api/client'
import { useRefreshWeather, useUpdateRace } from '@/api/races'
import { formatNumber } from '@/lib/format'
import type { RaceDetail, RaceUpdate } from '@/types'

function isBlankOrNumeric(v: string) {
  return v.trim() === '' || !Number.isNaN(Number(v))
}

const raceFormSchema = z.object({
  name: z.string().trim().min(1, 'Enter a race name.'),
  date: z.string().min(1, 'Enter a date.'),
  location_name: z.string(),
  location_lat: z.string().refine(isBlankOrNumeric, 'Enter a valid latitude.'),
  location_lng: z.string().refine(isBlankOrNumeric, 'Enter a valid longitude.'),
  notes: z.string(),
})

type RaceFormValues = z.infer<typeof raceFormSchema>

/**
 * Manual entry of race metadata (§5 Components). Editing location does *not*
 * re-trigger weather enrichment — `PATCH /races/{id}` leaves `weather_*` exactly as it
 * was, now potentially stale — so this offers `POST /races/{id}/weather/refresh`
 * explicitly rather than letting a corrected location carry silently stale weather.
 */
export function RaceForm({ race }: { race: RaceDetail }) {
  const updateRace = useUpdateRace(race.id)
  const refreshWeather = useRefreshWeather(race.id)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RaceFormValues>({
    resolver: zodResolver(raceFormSchema),
    defaultValues: {
      name: race.name,
      date: race.date,
      location_name: race.location_name ?? '',
      location_lat: race.location_lat != null ? String(race.location_lat) : '',
      location_lng: race.location_lng != null ? String(race.location_lng) : '',
      notes: race.notes ?? '',
    },
  })

  const onSubmit = async (values: RaceFormValues) => {
    setSubmitError(null)
    setSaved(false)
    const body: RaceUpdate = {
      name: values.name.trim(),
      date: values.date,
      location_name: values.location_name.trim() === '' ? null : values.location_name.trim(),
      location_lat: values.location_lat.trim() === '' ? null : Number(values.location_lat),
      location_lng: values.location_lng.trim() === '' ? null : Number(values.location_lng),
      notes: values.notes.trim() === '' ? null : values.notes,
    }
    try {
      await updateRace.mutateAsync(body)
      setSaved(true)
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Could not save the race.')
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
      <h3 className="text-sm font-semibold text-ink">Race details</h3>

      <div>
        <label htmlFor="race-name" className="mb-1 block text-xs text-ink/70">
          Name
        </label>
        <input
          id="race-name"
          className="w-full rounded border border-hairline bg-bg px-2 py-1.5 text-sm"
          {...register('name')}
        />
        {errors.name && <p className="mt-1 text-xs text-run">{errors.name.message}</p>}
      </div>

      <div className="flex flex-wrap gap-4">
        <div>
          <label htmlFor="race-date" className="mb-1 block text-xs text-ink/70">
            Date
          </label>
          <input
            id="race-date"
            type="date"
            className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm tnum"
            {...register('date')}
          />
          {errors.date && <p className="mt-1 text-xs text-run">{errors.date.message}</p>}
        </div>

      </div>

      <div>
        <label htmlFor="race-location-name" className="mb-1 block text-xs text-ink/70">
          Location
        </label>
        <input
          id="race-location-name"
          placeholder="e.g. Stutensee, Germany"
          className="w-full rounded border border-hairline bg-bg px-2 py-1.5 text-sm"
          {...register('location_name')}
        />
      </div>

      <div className="flex flex-wrap gap-4">
        <div>
          <label htmlFor="race-lat" className="mb-1 block text-xs text-ink/70">
            Latitude
          </label>
          <input
            id="race-lat"
            type="text"
            inputMode="decimal"
            className="w-32 rounded border border-hairline bg-bg px-2 py-1.5 text-sm tnum"
            {...register('location_lat')}
          />
          {errors.location_lat && (
            <p className="mt-1 text-xs text-run">{errors.location_lat.message}</p>
          )}
        </div>
        <div>
          <label htmlFor="race-lng" className="mb-1 block text-xs text-ink/70">
            Longitude
          </label>
          <input
            id="race-lng"
            type="text"
            inputMode="decimal"
            className="w-32 rounded border border-hairline bg-bg px-2 py-1.5 text-sm tnum"
            {...register('location_lng')}
          />
          {errors.location_lng && (
            <p className="mt-1 text-xs text-run">{errors.location_lng.message}</p>
          )}
        </div>
      </div>

      <div className="rounded border border-hairline p-3 text-sm">
        <p className="text-ink/70">
          Weather: {race.weather_conditions ?? '—'}
          {race.weather_temp_c != null ? `, ${formatNumber(race.weather_temp_c, '°C', 1)}` : ''}
          {race.weather_humidity_pct != null
            ? `, ${formatNumber(race.weather_humidity_pct, '% humidity', 0)}`
            : ''}
        </p>
        <p className="mt-1 text-xs text-ink/50">
          Editing location does not automatically refresh weather.
        </p>
        <button
          type="button"
          onClick={() => refreshWeather.mutate(undefined)}
          disabled={refreshWeather.isPending}
          className="mt-2 rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5 disabled:opacity-50"
        >
          {refreshWeather.isPending ? 'Refreshing…' : 'Refresh weather'}
        </button>
        {refreshWeather.isError && (
          <p className="mt-1 text-xs text-run">Could not refresh weather.</p>
        )}
        {refreshWeather.isSuccess && (
          <p className="mt-1 text-xs text-ink/50">Weather refreshed.</p>
        )}
      </div>

      <div>
        <label htmlFor="race-notes" className="mb-1 block text-xs text-ink/70">
          Notes
        </label>
        <textarea
          id="race-notes"
          rows={3}
          className="w-full rounded border border-hairline bg-bg px-2 py-1.5 text-sm"
          {...register('notes')}
        />
      </div>

      {submitError && <p className="text-sm text-run">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded bg-ink px-3 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : 'Save race'}
      </button>
      {saved && !isSubmitting && <span className="ml-3 text-xs text-ink/50">Saved.</span>}
    </form>
  )
}
