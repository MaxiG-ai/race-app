import { useParams } from 'react-router-dom'
import { useRace } from '@/api/races'
import { FuelingForm } from '@/components/FuelingForm'
import { OfficialResultForm } from '@/components/OfficialResultForm'
import { RaceForm } from '@/components/RaceForm'

/**
 * Manual entry (§5 IA "Race edit"): race metadata, official result, fueling. Gear
 * assignment (`GearPicker`) is composed on the race detail page instead — see that
 * component's own docstring.
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
      <hr className="border-hairline" />
      <OfficialResultForm raceId={race.id} result={race.official_result} />
      <hr className="border-hairline" />
      <FuelingForm raceId={race.id} fuelings={race.fuelings} />
    </div>
  )
}
