import { SEGMENT_LABEL, SEGMENT_ORDER } from '@/lib/format'
import type { Segment, UploadRead } from '@/types'

export interface SegmentAssignProps {
  uploads: UploadRead[]
  value: Record<number, Segment>
  onChange: (next: Record<number, Segment>) => void
}

/**
 * Shown when `POST /races/group` rejects a grouping as ambiguous (§3: two runs in one
 * group, or an `other` sport) — the API names the ambiguity rather than guessing, so
 * this lets the user assign each upload's leg by hand so the caller can retry the same
 * request with explicit `segment_assignments`.
 */
export function SegmentAssign({ uploads, value, onChange }: SegmentAssignProps) {
  function setSegment(uploadId: number, segment: Segment) {
    onChange({ ...value, [uploadId]: segment })
  }

  return (
    <div className="space-y-2 rounded border border-hairline p-3">
      <p className="text-sm text-ink/70">
        Segment detection was ambiguous for this group — assign each upload&apos;s leg by
        hand, then retry.
      </p>
      <ul className="space-y-2">
        {uploads.map((upload) => (
          <li key={upload.id} className="flex items-center justify-between gap-3 text-sm">
            <span className="truncate">{upload.filename ?? `Upload #${upload.id}`}</span>
            <select
              aria-label={`Segment for ${upload.filename ?? `upload #${upload.id}`}`}
              value={value[upload.id] ?? ''}
              onChange={(e) => setSegment(upload.id, e.target.value as Segment)}
              className="rounded border border-hairline bg-bg px-2 py-1 text-sm"
            >
              <option value="" disabled>
                Choose…
              </option>
              {SEGMENT_ORDER.map((segment) => (
                <option key={segment} value={segment}>
                  {SEGMENT_LABEL[segment]}
                </option>
              ))}
            </select>
          </li>
        ))}
      </ul>
    </div>
  )
}
