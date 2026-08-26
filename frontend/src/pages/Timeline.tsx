import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useCreateRace, useRaces } from '@/api/races'
import { ApiError } from '@/api/client'
import { StatStrip } from '@/components/StatStrip'
import { TimelineSpine } from '@/components/TimelineSpine'

const PAGE_SIZE = 10

export default function Timeline() {
  const [offset, setOffset] = useState(0)
  const query = useRaces({ limit: PAGE_SIZE, offset })

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-baseline justify-between gap-4">
        <StatStrip />
        <NewRaceButton />
      </div>

      {query.isPending ? (
        <TimelineSkeleton />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : query.data.items.length === 0 ? (
        offset === 0 ? (
          <EmptyState />
        ) : (
          // Ran past the last page (e.g. a race was deleted elsewhere) — step back
          // rather than show a dead end.
          <NoMorePage onBack={() => setOffset(Math.max(0, offset - PAGE_SIZE))} />
        )
      ) : (
        <>
          <TimelineSpine races={query.data.items} />
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            total={query.data.total}
            isFetching={query.isFetching}
            onChange={setOffset}
          />
        </>
      )}
    </div>
  )
}

function TimelineSkeleton() {
  return (
    <div aria-hidden="true" className="animate-pulse">
      <ol className="relative">
        <div className="absolute left-[5px] top-3 bottom-3 w-px bg-hairline" />
        {[0, 1, 2].map((i) => (
          <li key={i} className="relative pb-6 pl-7 last:pb-0">
            <span className="absolute left-0 top-3 block h-[11px] w-[11px] rounded-full border-2 border-hairline bg-bg" />
            <div className="h-[104px] rounded-lg border border-hairline bg-hairline/20" />
          </li>
        ))}
      </ol>
    </div>
  )
}

function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const message =
    error instanceof ApiError ? error.message : 'Something went wrong loading your races.'
  return (
    <div className="rounded-lg border border-hairline px-4 py-6 text-center">
      <p className="text-sm text-ink/70">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-md border border-hairline px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
      >
        Retry
      </button>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-hairline px-4 py-10 text-center">
      <p className="text-sm text-ink/70">No races yet.</p>
      <p className="mt-1 text-sm text-ink/50">
        Import a FIT file to start your timeline.
      </p>
      <Link
        to="/import"
        className="mt-4 inline-block rounded-md border border-hairline px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
      >
        Go to Import
      </Link>
    </div>
  )
}

function NoMorePage({ onBack }: { onBack: () => void }) {
  return (
    <div className="rounded-lg border border-hairline px-4 py-6 text-center">
      <p className="text-sm text-ink/70">Nothing on this page.</p>
      <button
        type="button"
        onClick={onBack}
        className="mt-3 rounded-md border border-hairline px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
      >
        Back
      </button>
    </div>
  )
}

function Pagination({
  offset,
  limit,
  total,
  isFetching,
  onChange,
}: {
  offset: number
  limit: number
  total: number
  isFetching: boolean
  onChange: (offset: number) => void
}) {
  if (total <= limit) return null

  const from = offset + 1
  const to = Math.min(offset + limit, total)
  const canPrev = offset > 0
  const canNext = offset + limit < total

  return (
    <nav
      aria-label="Timeline pagination"
      className="flex items-center justify-between border-t border-hairline pt-4 text-sm"
    >
      <span className="tnum text-ink/50">
        {from}–{to} of {total}
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={!canPrev}
          onClick={() => onChange(Math.max(0, offset - limit))}
          className="rounded-md border border-hairline px-3 py-1.5 text-ink transition-colors hover:border-ink/40 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
        >
          Previous
        </button>
        <button
          type="button"
          disabled={!canNext}
          onClick={() => onChange(offset + limit)}
          className="rounded-md border border-hairline px-3 py-1.5 text-ink transition-colors hover:border-ink/40 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
        >
          {isFetching ? 'Loading…' : 'Next'}
        </button>
      </div>
    </nav>
  )
}

/**
 * Not every race arrives as a file. A race can exist with no import at all — §2 is
 * explicit that fueling "must be recordable for a race with no import yet" — so
 * there has to be a path to a race that isn't an upload. This creates a stub and
 * drops straight into the edit form rather than adding a separate create page:
 * the fields are the same ones, and the IA (§5) lists no such page.
 */
function NewRaceButton() {
  const navigate = useNavigate()
  const createRace = useCreateRace()

  const handleClick = () => {
    const today = new Date()
    const date = [
      today.getFullYear(),
      String(today.getMonth() + 1).padStart(2, '0'),
      String(today.getDate()).padStart(2, '0'),
    ].join('-')
    createRace.mutate(
      { name: 'Untitled race', date },
      { onSuccess: (race) => navigate(`/races/${race.id}/edit`) },
    )
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={createRace.isPending}
      className="shrink-0 rounded-md border border-hairline px-3 py-1.5 text-sm text-ink transition-colors hover:border-ink/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink disabled:opacity-40"
    >
      {createRace.isPending ? 'Creating\u2026' : 'New race'}
    </button>
  )
}
