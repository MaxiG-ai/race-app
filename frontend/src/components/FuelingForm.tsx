import { zodResolver } from '@hookform/resolvers/zod'
import { useMemo, useState } from 'react'
import { useFieldArray, useForm, useWatch, type Control, type UseFormRegister } from 'react-hook-form'
import { z } from 'zod'
import { ApiError } from '@/api/client'
import { useUpsertFueling } from '@/api/races'
import { SEGMENT_LABEL } from '@/lib/format'
import type { RaceFuelingRead, RaceFuelingUpsert, Segment } from '@/types'

const SEGMENT_VALUES = ['swim', 't1', 'bike', 't2', 'run'] as const

function isBlankOrNumeric(v: string) {
  return v.trim() === '' || !Number.isNaN(Number(v))
}

const productSchema = z.object({
  name: z.string().trim().min(1, 'Enter a product name.'),
  quantity: z.string().refine(isBlankOrNumeric, 'Enter a number.'),
})

const rowSchema = z.object({
  segment: z.enum(SEGMENT_VALUES),
  carbs_g: z.string().refine(isBlankOrNumeric, 'Enter a number.'),
  notes: z.string(),
  products: z.array(productSchema),
})

const fuelingFormSchema = z.object({ rows: z.array(rowSchema) })
type FuelingFormValues = z.infer<typeof fuelingFormSchema>
type FuelingRowValues = FuelingFormValues['rows'][number]

const DEFAULT_SEGMENTS: Segment[] = ['bike', 'run']

function rowFromRead(read: RaceFuelingRead): FuelingRowValues {
  return {
    segment: read.segment,
    carbs_g: read.carbs_g != null ? String(read.carbs_g) : '',
    notes: read.notes ?? '',
    products: (read.products ?? []).map((p) => ({
      name: p.name,
      quantity: p.quantity != null ? String(p.quantity) : '',
    })),
  }
}

function emptyRow(segment: Segment): FuelingRowValues {
  return { segment, carbs_g: '', notes: '', products: [] }
}

/**
 * Per-segment carbs, a product list (add/remove rows) and notes (§2 `RaceFueling`, §5
 * Components). `PUT /races/{id}/fueling` replaces the whole set: a segment omitted from
 * the payload is deleted, which is exactly what makes "remove this segment" work — a
 * removed row (via `useFieldArray`) is simply absent from the submitted array. The UI
 * defaults to bike + run but any of the five segments is reachable via "Add segment"
 * (§2: "ate a gel in T2" must not be blocked). Total carbs is only ever summed for
 * display here, never sent or stored.
 */
export function FuelingForm({ raceId, fuelings }: { raceId: number; fuelings: RaceFuelingRead[] }) {
  const upsertFueling = useUpsertFueling(raceId)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [segmentToAdd, setSegmentToAdd] = useState<Segment | ''>('')

  const defaultRows = useMemo<FuelingRowValues[]>(() => {
    const rows = fuelings.map(rowFromRead)
    const present = new Set(rows.map((r) => r.segment))
    for (const segment of DEFAULT_SEGMENTS) {
      if (!present.has(segment)) rows.push(emptyRow(segment))
    }
    return SEGMENT_VALUES.filter((s) => rows.some((r) => r.segment === s)).map(
      (s) => rows.find((r) => r.segment === s)!,
    )
    // `useForm`'s `defaultValues` is only read once at mount, so recomputing this on
    // a `fuelings` change (there shouldn't be one — this component owns its own
    // lifetime) is harmless; it just satisfies the exhaustive-deps lint.
  }, [fuelings])

  const {
    control,
    register,
    handleSubmit,
    formState: { isSubmitting },
  } = useForm<FuelingFormValues>({
    resolver: zodResolver(fuelingFormSchema),
    defaultValues: { rows: defaultRows },
  })

  const { fields, append, remove } = useFieldArray({ control, name: 'rows' })
  const watchedRows = useWatch({ control, name: 'rows' })

  const totalCarbs = (watchedRows ?? []).reduce((sum, row) => {
    if (!row?.carbs_g || row.carbs_g.trim() === '') return sum
    const n = Number(row.carbs_g)
    return sum + (Number.isNaN(n) ? 0 : n)
  }, 0)

  const usedSegments = new Set((watchedRows ?? []).map((r) => r?.segment))
  const availableSegments = SEGMENT_VALUES.filter((s) => !usedSegments.has(s))

  function addSegmentRow() {
    if (!segmentToAdd) return
    append(emptyRow(segmentToAdd))
    setSegmentToAdd('')
  }

  const onSubmit = async (values: FuelingFormValues) => {
    setSubmitError(null)
    setSaved(false)
    const body: RaceFuelingUpsert = {
      fuelings: values.rows.map((row) => ({
        segment: row.segment,
        carbs_g: row.carbs_g.trim() === '' ? null : Number(row.carbs_g),
        products: row.products.length
          ? row.products.map((p) => ({
              name: p.name.trim(),
              quantity: p.quantity.trim() === '' ? null : Number(p.quantity),
            }))
          : null,
        notes: row.notes.trim() === '' ? null : row.notes,
      })),
    }
    try {
      await upsertFueling.mutateAsync(body)
      setSaved(true)
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Could not save fueling.')
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-ink">Fueling</h3>
        <p className="tnum text-sm text-ink/60">Total {totalCarbs}g carbs</p>
      </div>

      {fields.length === 0 && <p className="text-sm text-ink/50">No segments added yet.</p>}

      <div className="space-y-4">
        {fields.map((field, index) => (
          <FuelingRow
            key={field.id}
            control={control}
            register={register}
            index={index}
            segment={field.segment}
            onRemove={() => remove(index)}
          />
        ))}
      </div>

      {availableSegments.length > 0 && (
        <div className="flex items-center gap-2 text-sm">
          <select
            aria-label="Add fueling for segment"
            value={segmentToAdd}
            onChange={(e) => setSegmentToAdd(e.target.value as Segment)}
            className="rounded border border-hairline bg-bg px-2 py-1"
          >
            <option value="">Add segment…</option>
            {availableSegments.map((s) => (
              <option key={s} value={s}>
                {SEGMENT_LABEL[s]}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={addSegmentRow}
            disabled={!segmentToAdd}
            className="rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5 disabled:opacity-50"
          >
            Add
          </button>
        </div>
      )}

      {submitError && <p className="text-sm text-run">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded bg-ink px-3 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : 'Save fueling'}
      </button>
      {saved && !isSubmitting && <span className="ml-3 text-xs text-ink/50">Saved.</span>}
    </form>
  )
}

function FuelingRow({
  control,
  register,
  index,
  segment,
  onRemove,
}: {
  control: Control<FuelingFormValues>
  register: UseFormRegister<FuelingFormValues>
  index: number
  segment: Segment
  onRemove: () => void
}) {
  const { fields, append, remove } = useFieldArray({ control, name: `rows.${index}.products` })

  return (
    <div className="space-y-2 rounded border border-hairline p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{SEGMENT_LABEL[segment]}</span>
        <button type="button" onClick={onRemove} className="text-xs text-ink/50 hover:text-run">
          Remove segment
        </button>
      </div>

      <div>
        <label htmlFor={`carbs-${index}`} className="mb-1 block text-xs text-ink/70">
          Carbs (g)
        </label>
        <input
          id={`carbs-${index}`}
          type="text"
          inputMode="decimal"
          className="w-24 rounded border border-hairline bg-bg px-2 py-1 text-sm tnum"
          {...register(`rows.${index}.carbs_g`)}
        />
      </div>

      <div className="space-y-1">
        <span className="block text-xs text-ink/70">Products</span>
        {fields.map((field, productIndex) => (
          <div key={field.id} className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Product name"
              aria-label="Product name"
              className="flex-1 rounded border border-hairline bg-bg px-2 py-1 text-sm"
              {...register(`rows.${index}.products.${productIndex}.name`)}
            />
            <input
              type="text"
              inputMode="decimal"
              placeholder="Qty"
              aria-label="Quantity"
              className="w-16 rounded border border-hairline bg-bg px-2 py-1 text-sm tnum"
              {...register(`rows.${index}.products.${productIndex}.quantity`)}
            />
            <button
              type="button"
              onClick={() => remove(productIndex)}
              className="text-xs text-ink/50 hover:text-run"
            >
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => append({ name: '', quantity: '' })}
          className="text-xs text-ink/60 hover:text-ink"
        >
          + Add product
        </button>
      </div>

      <div>
        <label htmlFor={`notes-${index}`} className="mb-1 block text-xs text-ink/70">
          Notes
        </label>
        <textarea
          id={`notes-${index}`}
          rows={2}
          className="w-full rounded border border-hairline bg-bg px-2 py-1 text-sm"
          {...register(`rows.${index}.notes`)}
        />
      </div>
    </div>
  )
}
