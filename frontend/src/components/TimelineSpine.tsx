import { RaceCard } from './RaceCard'
import type { RaceListItem } from '@/types'

/**
 * Signature element (§5): a real left rail — one continuous line plus a marker dot
 * per race, aligned to each card — rather than a border faked per card. The line is
 * a single absolutely-positioned strip behind the list; each `<li>` positions its own
 * dot on top of it, so line and dots stay aligned regardless of card height.
 *
 * Dots are left neutral (ink outline, not sport-coloured) — colour is reserved for the
 * segment bar, so it isn't relied on as the only carrier of meaning here.
 */
export function TimelineSpine({ races }: { races: RaceListItem[] }) {
  return (
    <ol className="relative">
      <div
        aria-hidden="true"
        className="absolute left-[5px] top-3 bottom-3 w-px bg-hairline"
      />
      {races.map((race) => (
        <li key={race.id} className="relative pb-6 pl-7 last:pb-0">
          <span
            aria-hidden="true"
            className="absolute left-0 top-3 block h-[11px] w-[11px] rounded-full border-2 border-ink bg-bg"
          />
          <RaceCard race={race} />
        </li>
      ))}
    </ol>
  )
}
