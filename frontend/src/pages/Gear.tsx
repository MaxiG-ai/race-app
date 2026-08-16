import { useUnknownDevices } from '@/api/gear'
import { DevicePrompt } from '@/components/DevicePrompt'
import { GearList } from '@/components/GearList'

/** §5 IA: inventory with accumulated distance, plus unmapped sensor serials awaiting a name. */
export default function Gear() {
  const { data: unknownDevices, isLoading, isError } = useUnknownDevices()

  return (
    <div className="space-y-10">
      <h1 className="text-lg font-semibold text-ink">Gear</h1>

      {!isLoading && !isError && unknownDevices && unknownDevices.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-ink">Unmapped sensors</h2>
          <div className="space-y-2">
            {unknownDevices.map((device) => (
              <DevicePrompt key={`${device.manufacturer}-${device.serial_number}`} device={device} />
            ))}
          </div>
        </section>
      )}

      <GearList />
    </div>
  )
}
