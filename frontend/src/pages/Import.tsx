import { FitUpload } from '@/components/FitUpload'
import { GroupingTray } from '@/components/GroupingTray'

/**
 * §5 IA: multi-file upload plus the ungrouped tray where single-sport activities are
 * grouped into races (§3). `FitUpload` and `GroupingTray` talk to each other only
 * through the shared `uploads` query cache — a successful upload invalidates the tray,
 * which refetches on its own.
 */
export default function Import() {
  return (
    <div className="space-y-10">
      <h1 className="text-lg font-semibold text-ink">Import</h1>
      <FitUpload />
      <GroupingTray />
    </div>
  )
}
