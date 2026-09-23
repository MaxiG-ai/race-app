import { zodResolver } from '@hookform/resolvers/zod'
import { useState } from 'react'
import { useForm, type UseFormRegister } from 'react-hook-form'
import { z } from 'zod'
import { ApiError } from '@/api/client'
import { useUpsertOfficialResult } from '@/api/races'
import { formatDuration } from '@/lib/format'
import type { OfficialResultRead, OfficialResultUpsert } from '@/types'

/**
 * Accepts "ss", "mm:ss" or "h:mm:ss" and folds it to whole seconds — no one enters a
 * race time in raw seconds, but the API stores integer seconds (§2 `OfficialResult`).
 * Mirrors `formatDuration`'s output shape so a saved value round-trips through the
 * form unchanged.
 */
function parseDurationInput(raw: string): number | null {
  const trimmed = raw.trim()
  if (trimmed === '') return null
  const parts = trimmed.split(':')
  if (parts.length > 3 || parts.some((p) => p === '' || !/^\d+$/.test(p))) return null
  return parts.reduce((acc, part) => acc * 60 + Number(part), 0)
}

function isValidDuration(raw: string): boolean {
  return raw.trim() === '' || parseDurationInput(raw) !== null
}

function isValidPlace(raw: string): boolean {
  return raw.trim() === '' || /^\d+$/.test(raw.trim())
}

/** The five legs, in course order — their sum is the total. */
const SPLIT_FIELDS = ['swim_time', 't1_time', 'bike_time', 't2_time', 'run_time'] as const
const SPLIT_LABELS: Record<(typeof SPLIT_FIELDS)[number], string> = {
  swim_time: 'swim',
  t1_time: 'T1',
  bike_time: 'bike',
  t2_time: 'T2',
  run_time: 'run',
}

/** Seconds summed from the splits, or null if any is blank or unparseable. */
function totalFromSplits(values: Partial<ResultFormValues>): number | null {
  let sum = 0
  for (const field of SPLIT_FIELDS) {
    const seconds = parseDurationInput(values[field] ?? '')
    if (seconds == null) return null
    sum += seconds
  }
  return sum
}

function missingSplits(values: Partial<ResultFormValues>): string[] {
  return SPLIT_FIELDS.filter((f) => parseDurationInput(values[f] ?? '') == null).map(
    (f) => SPLIT_LABELS[f],
  )
}

const timeField = z.string().refine(isValidDuration, 'Use mm:ss or h:mm:ss.')
const placeField = z.string().refine(isValidPlace, 'Enter a whole number.')

const resultSchema = z
  .object({
    total_time: z.string().refine(isValidDuration, 'Use mm:ss or h:mm:ss.'),
    swim_time: timeField,
    t1_time: timeField,
    bike_time: timeField,
    t2_time: timeField,
    run_time: timeField,
    overall_place: placeField,
    age_group_place: placeField,
  })
  // The total is required, but never something the user should have to add up: a
  // complete set of splits stands in for it. Only when neither is present do we ask.
  .superRefine((values, ctx) => {
    if (values.total_time.trim() !== '') return
    if (totalFromSplits(values) != null) return
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['total_time'],
      message: `Enter the total time, or fill in every split so it can be calculated. Still missing: ${missingSplits(values).join(', ')}.`,
    })
  })

type ResultFormValues = z.infer<typeof resultSchema>

function fieldValue(seconds: number | null | undefined): string {
  return seconds == null ? '' : formatDuration(seconds)
}

/**
 * Manually entered official times and placings (§2 `OfficialResult`, §5 Components).
 * T1/T2 are nullable because they're often not separately timed — left blank rather
 * than forced to zero. Placings are optional.
 */
export function OfficialResultForm({
  raceId,
  result,
}: {
  raceId: number
  result?: OfficialResultRead | null
}) {
  const upsertResult = useUpsertOfficialResult(raceId)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<ResultFormValues>({
    resolver: zodResolver(resultSchema),
    defaultValues: {
      total_time: result ? formatDuration(result.total_time) : '',
      swim_time: fieldValue(result?.swim_time),
      t1_time: fieldValue(result?.t1_time),
      bike_time: fieldValue(result?.bike_time),
      t2_time: fieldValue(result?.t2_time),
      run_time: fieldValue(result?.run_time),
      overall_place: result?.overall_place != null ? String(result.overall_place) : '',
      age_group_place: result?.age_group_place != null ? String(result.age_group_place) : '',
    },
  })

  // Live preview of what the server will store when the total is left blank, so the
  // calculation is visible before saving rather than a surprise afterwards.
  const watched = watch()
  const typedTotal = (watched.total_time ?? '').trim()
  const derivedTotal = typedTotal === '' ? totalFromSplits(watched) : null
  const stillMissing = typedTotal === '' && derivedTotal == null ? missingSplits(watched) : []

  const onSubmit = async (values: ResultFormValues) => {
    setSubmitError(null)
    setSaved(false)
    const body: OfficialResultUpsert = {
      total_time: parseDurationInput(values.total_time),
      swim_time: parseDurationInput(values.swim_time),
      t1_time: parseDurationInput(values.t1_time),
      bike_time: parseDurationInput(values.bike_time),
      t2_time: parseDurationInput(values.t2_time),
      run_time: parseDurationInput(values.run_time),
      overall_place: values.overall_place.trim() === '' ? null : Number(values.overall_place),
      age_group_place:
        values.age_group_place.trim() === '' ? null : Number(values.age_group_place),
    }
    try {
      await upsertResult.mutateAsync(body)
      setSaved(true)
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Could not save the official result.')
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
      <h3 className="text-sm font-semibold text-ink">Official result</h3>

      <div className="flex flex-wrap gap-4">
        <TimeInput
          name="total_time"
          label="Total time"
          register={register}
          error={errors.total_time?.message}
          hint={
            derivedTotal != null
              ? `Calculated from splits: ${formatDuration(derivedTotal)}`
              : stillMissing.length > 0
                ? `Enter it, or fill in every split — still missing: ${stillMissing.join(', ')}.`
                : undefined
          }
        />
        <TimeInput name="swim_time" label="Swim" register={register} error={errors.swim_time?.message} />
        <TimeInput name="t1_time" label="T1" register={register} error={errors.t1_time?.message} />
        <TimeInput name="bike_time" label="Bike" register={register} error={errors.bike_time?.message} />
        <TimeInput name="t2_time" label="T2" register={register} error={errors.t2_time?.message} />
        <TimeInput name="run_time" label="Run" register={register} error={errors.run_time?.message} />
      </div>

      <div className="flex flex-wrap gap-4">
        <div>
          <label htmlFor="overall_place" className="mb-1 block text-xs text-ink/70">
            Overall place
          </label>
          <input
            id="overall_place"
            type="text"
            inputMode="numeric"
            className="w-20 rounded border border-hairline bg-bg px-2 py-1 text-sm tnum"
            {...register('overall_place')}
          />
          {errors.overall_place && (
            <p className="mt-1 text-xs text-run">{errors.overall_place.message}</p>
          )}
        </div>
        <div>
          <label htmlFor="age_group_place" className="mb-1 block text-xs text-ink/70">
            Age group place
          </label>
          <input
            id="age_group_place"
            type="text"
            inputMode="numeric"
            className="w-20 rounded border border-hairline bg-bg px-2 py-1 text-sm tnum"
            {...register('age_group_place')}
          />
          {errors.age_group_place && (
            <p className="mt-1 text-xs text-run">{errors.age_group_place.message}</p>
          )}
        </div>
      </div>

      {submitError && <p className="text-sm text-run">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded bg-ink px-3 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : 'Save official result'}
      </button>
      {saved && !isSubmitting && <span className="ml-3 text-xs text-ink/50">Saved.</span>}
    </form>
  )
}

function TimeInput({
  name,
  label,
  register,
  error,
  hint,
}: {
  name: keyof ResultFormValues
  label: string
  register: UseFormRegister<ResultFormValues>
  error?: string
  /** Shown only when there is no error — guidance, not a failure. */
  hint?: string
}) {
  return (
    <div>
      <label htmlFor={name} className="mb-1 block text-xs text-ink/70">
        {label}
      </label>
      <input
        id={name}
        type="text"
        inputMode="numeric"
        placeholder="h:mm:ss"
        className="w-28 rounded border border-hairline bg-bg px-2 py-1 text-sm tnum"
        {...register(name)}
      />
      {error ? (
        <p className="mt-1 text-xs text-run">{error}</p>
      ) : (
        hint && <p className="mt-1 max-w-[16rem] text-xs text-ink/50">{hint}</p>
      )}
    </div>
  )
}
