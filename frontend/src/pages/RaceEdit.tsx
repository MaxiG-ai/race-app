import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useDeleteRace, useRace } from '@/api/races'
import { FuelingForm } from '@/components/FuelingForm'
import { OfficialResultForm } from '@/components/OfficialResultForm'
import { CategoryField } from '@/components/CategoryField'
import { RaceForm } from '@/components/RaceForm'
import type { RaceDetail as RaceDetailType } from '@/types'

/**
 * Manual entry (§5 IA "Race edit"): race metadata, official result, fueling. Gear
 * assignment (`GearPicker`) is composed on the race detail page instead — see that
 * component's own docstring.
 *
 * Deleting the race lives here too, kept well below the save actions above (§4
 * `DELETE /races/{id}`) — destructive, so it must never sit next to "Save" where
 * muscle memory could hit it by mistake.
 */
export default function RaceEdit() {
  const { id } = useParams<{ id: string }>()
  const raceId = Number(id)
  const { data: race, isLoading, isError } = useRace(raceId)

  if (isLoading) return <p className="text-sm text-ink/50">Loading race…</p>
  if (isError || !race) return <p className="text-sm text-run">Could not load this race.</p>

  return (
    <div className="space-y-10">
      <div>
        <h1 className="mb-1 text-lg font-semibold text-ink">Edit race</h1>
        <p className="text-sm text-ink/50">{race.name}</p>
      </div>

      <RaceForm race={race} />

      <CategoryField race={race} />
      <hr className="border-hairline" />
      <OfficialResultForm raceId={race.id} result={race.official_result} />
      <hr className="border-hairline" />
      <FuelingForm raceId={race.id} fuelings={race.fuelings} />
      <hr className="border-hairline" />
      <DeleteRaceSection race={race} />
    </div>
  )
}

/**
 * A cascading, irreversible delete (race + official result + fueling + every
 * recorded activity + every lap split). Requires an explicit inline confirmation
 * step naming the race — never a bare button — and calls out that uploads survive
 * it (`Upload.race_id` goes to null, the FIT file itself is retained per §8) so
 * deleting a race for re-grouping doesn't read as losing the underlying recordings.
 */
function DeleteRaceSection({ race }: { race: RaceDetailType }) {
  const navigate = useNavigate()
  const deleteRace = useDeleteRace()
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDelete = () => {
    setError(null)
    deleteRace.mutate(race.id, {
      onSuccess: () => navigate('/'),
      onError: (err) => {
        setError(err instanceof ApiError ? err.message : 'Could not delete this race.')
      },
    })
  }

  return (
    <section
      aria-labelledby="delete-race-heading"
      className="rounded-lg border border-run/40 p-4"
    >
      <h2 id="delete-race-heading" className="text-sm font-semibold text-run">
        Delete this race
      </h2>
      <p className="mt-2 text-sm text-ink/70">
        Permanently deletes &ldquo;{race.name}&rdquo;, its official result, fueling, and
        every recorded activity and lap split. This cannot be undone.
      </p>
      <p className="mt-1 text-xs text-ink/50">
        The FIT files you uploaded for it are not deleted — they&rsquo;re unlinked from
        this race and stay available to re-group elsewhere.
      </p>

      {!confirming ? (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="mt-3 rounded border border-run/60 px-3 py-1.5 text-sm font-medium text-run transition-colors hover:bg-run/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-run"
        >
          Delete race&hellip;
        </button>
      ) : (
        <div
          role="alert"
          className="mt-3 rounded border border-run/40 bg-run/5 p-3"
        >
          <p className="text-sm font-medium text-ink">
            Delete &ldquo;{race.name}&rdquo; and all of its recorded data?
          </p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleteRace.isPending}
              className="rounded bg-run px-3 py-1.5 text-sm font-medium text-bg transition-opacity disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-run"
            >
              {deleteRace.isPending ? 'Deleting…' : `Yes, delete "${race.name}"`}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={deleteRace.isPending}
              className="rounded border border-hairline px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink/40 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-2 text-sm text-run">
          {error}
        </p>
      )}
    </section>
  )
}
