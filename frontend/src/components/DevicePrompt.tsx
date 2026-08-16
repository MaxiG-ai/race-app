import { useState } from 'react'
import { ApiError } from '@/api/client'
import { useCreateGearDevice, useGearList } from '@/api/gear'
import type { UnknownDeviceRead } from '@/types'

/**
 * "New sensor seen — which gear is this?" (§3 Gear detection step 3, §5 Components).
 * Mapping backfills every past activity carrying this serial — the response doesn't
 * report a count (see `useCreateGearDevice`), so this says what happened without
 * citing a number the API doesn't give us.
 */
export function DevicePrompt({ device }: { device: UnknownDeviceRead }) {
  const { data: gearList } = useGearList()
  const createDevice = useCreateGearDevice()
  const [gearId, setGearId] = useState<number | ''>('')
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleAssign() {
    if (gearId === '') return
    setError(null)
    setDone(false)
    try {
      await createDevice.mutateAsync({
        gearId,
        manufacturer: device.manufacturer,
        serial_number: device.serial_number,
      })
      setDone(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not map this device.')
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded border border-hairline p-3 text-sm">
      <span>
        New device seen: <code className="text-xs">{device.manufacturer}</code>{' '}
        <span className="tnum">{device.serial_number}</span>
        {device.activity_count > 0 &&
          ` · seen in ${device.activity_count} ${device.activity_count === 1 ? 'activity' : 'activities'}`}{' '}
        — which gear is this?
      </span>
      <select
        aria-label={`Gear for device ${device.serial_number}`}
        value={gearId}
        onChange={(e) => {
          setGearId(e.target.value === '' ? '' : Number(e.target.value))
          setDone(false)
        }}
        className="rounded border border-hairline bg-bg px-2 py-1 text-sm"
      >
        <option value="">Choose gear…</option>
        {(gearList ?? []).map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={handleAssign}
        disabled={gearId === '' || createDevice.isPending}
        className="rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5 disabled:opacity-50"
      >
        {createDevice.isPending ? 'Mapping…' : 'Map device'}
      </button>
      {done && (
        <span className="text-xs text-ink/50">
          Mapped — past activities recording this serial have been updated.
        </span>
      )}
      {error && <span className="text-xs text-run">{error}</span>}
    </div>
  )
}
