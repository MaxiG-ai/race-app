import { useState, type FormEvent } from 'react'
import { ApiError } from '@/api/client'
import { useCreateGear, useGearList, useUpdateGear } from '@/api/gear'
import { formatDistance } from '@/lib/format'
import type { GearKind, GearListItem } from '@/types'

const GEAR_KIND_LABEL: Record<GearKind, string> = {
  bike: 'Bike',
  shoes: 'Shoes',
  wetsuit: 'Wetsuit',
  other: 'Other',
}
const GEAR_KINDS: GearKind[] = ['bike', 'shoes', 'wetsuit', 'other']

/**
 * Gear inventory with accumulated distance; retire/rename (§5 Components). Retired
 * gear is hidden by default (`include_retired=true` brings it back) but kept in
 * history rather than deleted.
 */
export function GearList() {
  const [includeRetired, setIncludeRetired] = useState(false)
  const { data, isLoading, isError } = useGearList(includeRetired)
  const createGear = useCreateGear()

  const [showAdd, setShowAdd] = useState(false)
  const [name, setName] = useState('')
  const [kind, setKind] = useState<GearKind>('bike')
  const [addError, setAddError] = useState<string | null>(null)

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setAddError(null)
    try {
      await createGear.mutateAsync({ kind, name: name.trim(), distance_offset_m: 0 })
      setName('')
      setShowAdd(false)
    } catch (err) {
      setAddError(err instanceof ApiError ? err.message : 'Could not add gear.')
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">Gear</h2>
        <label className="flex items-center gap-2 text-xs text-ink/60">
          <input
            type="checkbox"
            checked={includeRetired}
            onChange={(e) => setIncludeRetired(e.target.checked)}
          />
          Show retired
        </label>
      </div>

      {isLoading && <p className="text-sm text-ink/50">Loading gear…</p>}
      {isError && <p className="text-sm text-run">Could not load gear.</p>}
      {data && data.length === 0 && <p className="text-sm text-ink/50">No gear tracked yet.</p>}

      <ul className="space-y-2">
        {(data ?? []).map((item) => (
          <GearRow key={item.id} item={item} />
        ))}
      </ul>

      {showAdd ? (
        <form
          onSubmit={handleAdd}
          className="flex flex-wrap items-end gap-2 rounded border border-hairline p-3"
        >
          <div>
            <label htmlFor="gear-name" className="mb-1 block text-xs text-ink/70">
              Name
            </label>
            <input
              id="gear-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="rounded border border-hairline bg-bg px-2 py-1 text-sm"
            />
          </div>
          <div>
            <label htmlFor="gear-kind" className="mb-1 block text-xs text-ink/70">
              Kind
            </label>
            <select
              id="gear-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as GearKind)}
              className="rounded border border-hairline bg-bg px-2 py-1 text-sm"
            >
              {GEAR_KINDS.map((k) => (
                <option key={k} value={k}>
                  {GEAR_KIND_LABEL[k]}
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            disabled={createGear.isPending || !name.trim()}
            className="rounded bg-ink px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50"
          >
            {createGear.isPending ? 'Adding…' : 'Add'}
          </button>
          <button
            type="button"
            onClick={() => setShowAdd(false)}
            className="text-sm text-ink/50 hover:text-ink"
          >
            Cancel
          </button>
          {addError && <p className="w-full text-xs text-run">{addError}</p>}
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setShowAdd(true)}
          className="rounded border border-hairline px-3 py-1.5 text-sm hover:bg-ink/5"
        >
          + Add gear
        </button>
      )}
    </section>
  )
}

function GearRow({ item }: { item: GearListItem }) {
  const updateGear = useUpdateGear(item.id)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(item.name)
  const [error, setError] = useState<string | null>(null)

  const isRetired = item.retired_at != null

  async function saveName() {
    if (!name.trim() || name === item.name) {
      setName(item.name)
      setEditing(false)
      return
    }
    setError(null)
    try {
      await updateGear.mutateAsync({ name: name.trim() })
      setEditing(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not rename gear.')
    }
  }

  async function toggleRetired() {
    setError(null)
    try {
      await updateGear.mutateAsync({ retired_at: isRetired ? null : new Date().toISOString() })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update gear.')
    }
  }

  return (
    <li className="flex items-center justify-between gap-3 rounded border border-hairline p-3 text-sm">
      <div className="min-w-0 flex-1">
        {editing ? (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={saveName}
            onKeyDown={(e) => e.key === 'Enter' && saveName()}
            autoFocus
            className="rounded border border-hairline bg-bg px-2 py-1 text-sm"
          />
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="truncate text-left hover:underline"
          >
            {item.name}
          </button>
        )}
        <p className="text-xs text-ink/50">
          {GEAR_KIND_LABEL[item.kind]}
          {item.brand ? ` · ${item.brand}` : ''}
          {item.model ? ` ${item.model}` : ''} ·{' '}
          <span className="tnum">{formatDistance(item.total_distance_m)}</span>
          {isRetired ? ' · retired' : ''}
        </p>
        {error && <p className="text-xs text-run">{error}</p>}
      </div>
      <button
        type="button"
        onClick={toggleRetired}
        disabled={updateGear.isPending}
        className="shrink-0 rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5 disabled:opacity-50"
      >
        {isRetired ? 'Restore' : 'Retire'}
      </button>
    </li>
  )
}
