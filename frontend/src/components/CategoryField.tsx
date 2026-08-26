import { useCategories } from '@/api/categories'
import { useUpdateRace } from '@/api/races'
import type { RaceDetail } from '@/types'

/**
 * The race's distance category, with where it came from (§9).
 *
 * Not a plain picker: the category is normally *inferred* from the measured distances,
 * so the field's job is to show the resolved value, say whether it was inferred or
 * chosen, and let the user override — and get back. When an override disagrees with the
 * inference, both are shown, because that disagreement is usually the first sign a
 * distance is wrong rather than a category being mislabelled.
 */
export function CategoryField({ race }: { race: RaceDetail }) {
  const { data: categories } = useCategories()
  const updateRace = useUpdateRace(race.id)

  const source = race.classification_source
  const assigned = race.category
  const inferred = race.auto_category
  const disagrees = source === 'manual' && inferred != null && inferred.id !== assigned?.id

  const onPick = (value: string) => {
    updateRace.mutate({ race_category_id: value === '' ? null : Number(value) })
  }

  return (
    <div>
      <label htmlFor="race-category" className="mb-1 block text-xs text-ink/70">
        Category
      </label>
      <select
        id="race-category"
        value={assigned?.id ?? ''}
        onChange={(event) => onPick(event.target.value)}
        disabled={updateRace.isPending || !categories}
        className="rounded border border-hairline bg-bg px-2 py-1.5 text-sm disabled:opacity-50"
      >
        <option value="">Unclassified</option>
        {categories?.map((category) => (
          <option key={category.id} value={category.id}>
            {category.label}
          </option>
        ))}
      </select>

      <p className="mt-1 max-w-[22rem] text-xs text-ink/50">{provenance(race)}</p>

      {disagrees && (
        <p className="mt-1 max-w-[22rem] text-xs text-run">
          The measured distances suggest {inferred.label}.
        </p>
      )}

      {source === 'manual' && (
        <button
          type="button"
          onClick={() => onPick('')}
          className="mt-1 text-xs text-ink/60 underline underline-offset-2 hover:text-ink"
        >
          Use the calculated category
        </button>
      )}
    </div>
  )
}

/** One line explaining why the field reads the way it does. */
function provenance(race: RaceDetail): string {
  if (race.classification_source === 'manual') return 'Set by you.'
  if (race.classification_source === 'auto') return 'Calculated from the measured distances.'

  const detail = race.classification_detail as
    | { nearest?: string; worst_segment?: string; worst_delta_pct?: number }
    | null
    | undefined

  // An unclassified race with a reason is far more useful than a blank field: it names
  // the discipline that fell outside and by how much, which is normally the actual bug.
  if (detail?.nearest && detail.worst_segment && detail.worst_delta_pct != null) {
    const direction = detail.worst_delta_pct < 0 ? 'short of' : 'over'
    const magnitude = Math.abs(detail.worst_delta_pct).toFixed(1)
    return `No band matched — closest is ${detail.nearest}, but the ${detail.worst_segment} is ${magnitude}% ${direction} it.`
  }
  return 'Not calculated yet — needs recorded distances for swim, bike and run.'
}
