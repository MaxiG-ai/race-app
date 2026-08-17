import { useState, type FormEvent } from 'react'
import { ApiError } from '@/api/client'
import { useCreateGear, useGearList, useUpdateGear } from '@/api/gear'
import { formatDistance } from '@/lib/format'
import type { GearKind, GearListItem, GearUpdate } from '@/types'

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

const FOCUS_RING =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink'
const FIELD_CLASS = `w-full rounded border border-hairline bg-bg px-2 py-1 text-sm text-ink ${FOCUS_RING}`
const LABEL_CLASS = 'mb-1 block text-xs text-ink/70'

type GearFormState = {
  kind: GearKind
  name: string
  brand: string
  model: string
  purchasedAt: string
  /** Displayed in km (§2: metres is not how anyone thinks about shoe mileage). */
  distanceOffsetKm: string
  notes: string
  retired: boolean
}

function toFormState(item: GearListItem): GearFormState {
  return {
    kind: item.kind,
    name: item.name,
    brand: item.brand ?? '',
    model: item.model ?? '',
    purchasedAt: item.purchased_at ?? '',
    distanceOffsetKm: String(item.distance_offset_m / 1000),
    notes: item.notes ?? '',
    retired: item.retired_at != null,
  }
}

/** Empty string means "clear the field" (→ null), never "set to empty". */
function diffNullable(formValue: string, original: string | null): string | null | undefined {
  const normalized = formValue.trim() === '' ? null : formValue.trim()
  return normalized === original ? undefined : normalized
}

function GearRow({ item }: { item: GearListItem }) {
  const updateGear = useUpdateGear(item.id)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<GearFormState>(() => toFormState(item))
  const [error, setError] = useState<string | null>(null)

  const isRetired = item.retired_at != null
  const fieldId = (field: string) => `gear-${item.id}-${field}`

  function startEdit() {
    setForm(toFormState(item))
    setError(null)
    setEditing(true)
  }

  function cancelEdit() {
    setForm(toFormState(item))
    setError(null)
    setEditing(false)
  }

  async function toggleRetired() {
    setError(null)
    try {
      // retired_at is a plain date (§2), not a timestamp.
      await updateGear.mutateAsync({
        retired_at: isRetired ? null : new Date().toISOString().slice(0, 10),
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update gear.')
    }
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault()
    const trimmedName = form.name.trim()
    if (!trimmedName) {
      setError('Name is required.')
      return
    }
    const km = form.distanceOffsetKm.trim() === '' ? 0 : Number(form.distanceOffsetKm)
    if (!Number.isFinite(km) || km < 0) {
      setError('Distance before tracking started must be a non-negative number of km.')
      return
    }
    const offsetM = km * 1000

    // Only touch retired_at if the checkbox actually flipped relative to the
    // stored state — otherwise leave it out so an unrelated field edit can
    // never clear or overwrite the existing retirement date (§2).
    let retiredAt: string | null | undefined
    if (form.retired && item.retired_at == null) {
      retiredAt = new Date().toISOString().slice(0, 10)
    } else if (!form.retired && item.retired_at != null) {
      retiredAt = null
    }

    const body: GearUpdate = {
      kind: form.kind !== item.kind ? form.kind : undefined,
      name: trimmedName !== item.name ? trimmedName : undefined,
      brand: diffNullable(form.brand, item.brand),
      model: diffNullable(form.model, item.model),
      purchased_at: diffNullable(form.purchasedAt, item.purchased_at),
      notes: diffNullable(form.notes, item.notes),
      distance_offset_m: Math.abs(offsetM - item.distance_offset_m) > 1e-6 ? offsetM : undefined,
      retired_at: retiredAt,
    }

    setError(null)
    try {
      await updateGear.mutateAsync(body)
      setEditing(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update gear.')
    }
  }

  return (
    <li className="rounded border border-hairline p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={editing ? cancelEdit : startEdit}
            className={`truncate text-left hover:underline ${FOCUS_RING}`}
          >
            {item.name}
          </button>
          <p className="text-xs text-ink/50">
            {GEAR_KIND_LABEL[item.kind]}
            {item.brand ? ` · ${item.brand}` : ''}
            {item.model ? ` ${item.model}` : ''} ·{' '}
            <span className="tnum">{formatDistance(item.total_distance_m)}</span>
            {isRetired ? ' · retired' : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={editing ? cancelEdit : startEdit}
            className={`rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5 ${FOCUS_RING}`}
          >
            {editing ? 'Close' : 'Edit'}
          </button>
          {!editing && (
            <button
              type="button"
              onClick={toggleRetired}
              disabled={updateGear.isPending}
              className={`rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5 disabled:opacity-50 ${FOCUS_RING}`}
            >
              {isRetired ? 'Restore' : 'Retire'}
            </button>
          )}
        </div>
      </div>

      {!editing && error && <p className="mt-2 text-xs text-run">{error}</p>}

      {editing && (
        <form onSubmit={handleSave} className="mt-3 space-y-3 border-t border-hairline pt-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={fieldId('name')} className={LABEL_CLASS}>
                Name
              </label>
              <input
                id={fieldId('name')}
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                required
                className={FIELD_CLASS}
              />
            </div>
            <div>
              <label htmlFor={fieldId('kind')} className={LABEL_CLASS}>
                Kind
              </label>
              <select
                id={fieldId('kind')}
                value={form.kind}
                onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value as GearKind }))}
                className={FIELD_CLASS}
              >
                {GEAR_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {GEAR_KIND_LABEL[k]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={fieldId('brand')} className={LABEL_CLASS}>
                Brand
              </label>
              <input
                id={fieldId('brand')}
                value={form.brand}
                onChange={(e) => setForm((f) => ({ ...f, brand: e.target.value }))}
                className={FIELD_CLASS}
              />
            </div>
            <div>
              <label htmlFor={fieldId('model')} className={LABEL_CLASS}>
                Model
              </label>
              <input
                id={fieldId('model')}
                value={form.model}
                onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                className={FIELD_CLASS}
              />
            </div>
            <div>
              <label htmlFor={fieldId('purchased')} className={LABEL_CLASS}>
                Purchased
              </label>
              <input
                id={fieldId('purchased')}
                type="date"
                value={form.purchasedAt}
                onChange={(e) => setForm((f) => ({ ...f, purchasedAt: e.target.value }))}
                className={FIELD_CLASS}
              />
            </div>
            <div>
              <label htmlFor={fieldId('offset')} className={LABEL_CLASS}>
                Distance before tracking started (km)
              </label>
              <input
                id={fieldId('offset')}
                type="number"
                min="0"
                step="0.001"
                inputMode="decimal"
                value={form.distanceOffsetKm}
                onChange={(e) => setForm((f) => ({ ...f, distanceOffsetKm: e.target.value }))}
                className={`tnum ${FIELD_CLASS}`}
              />
            </div>
          </div>
          <div>
            <label htmlFor={fieldId('notes')} className={LABEL_CLASS}>
              Notes
            </label>
            <textarea
              id={fieldId('notes')}
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              rows={2}
              className={FIELD_CLASS}
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-ink/70">
            <input
              type="checkbox"
              checked={form.retired}
              onChange={(e) => setForm((f) => ({ ...f, retired: e.target.checked }))}
              className={FOCUS_RING}
            />
            Retired
          </label>

          {error && <p className="text-xs text-run">{error}</p>}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={updateGear.isPending || !form.name.trim()}
              className={`rounded bg-ink px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50 ${FOCUS_RING}`}
            >
              {updateGear.isPending ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={cancelEdit}
              className={`text-sm text-ink/50 hover:text-ink ${FOCUS_RING}`}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </li>
  )
}
