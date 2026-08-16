import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { useGroupUploads, useUploadsTray } from '@/api/uploads'
import { formatDistance, SEGMENT_LABEL, SEGMENT_ORDER } from '@/lib/format'
import type { RaceGroupRequest, Segment, UploadGroupProposal, UploadRead } from '@/types'
import { SegmentAssign } from './SegmentAssign'

function uploadSummary(upload: UploadRead): string {
  const parts: string[] = []
  if (upload.detected_sport) parts.push(upload.detected_sport)
  if (upload.detected_start_time) parts.push(new Date(upload.detected_start_time).toLocaleString())
  if (upload.detected_distance_m != null) parts.push(formatDistance(upload.detected_distance_m))
  if (upload.error) parts.push(`error: ${upload.error}`)
  return parts.join(' · ') || 'No preview available'
}

function proposalLabel(proposal: UploadGroupProposal): string {
  const present = new Set(Object.values(proposal.segments))
  return SEGMENT_ORDER.filter((s) => present.has(s))
    .map((s) => SEGMENT_LABEL[s])
    .join(' → ')
}

/**
 * The ungrouped tray (§3 "Single-sport uploads and manual grouping"). Proposals are
 * suggestions the user confirms — never auto-committed, since a Sunday brick session
 * looks identical to a race. Selecting a proposal only stages its uploads; creating the
 * race is always a separate, explicit confirm.
 */
export function GroupingTray() {
  const { data, isLoading, isError } = useUploadsTray('parsed')
  const groupUploads = useGroupUploads()
  const navigate = useNavigate()

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [assignments, setAssignments] = useState<Record<number, Segment>>({})
  const [needsAssignment, setNeedsAssignment] = useState(false)
  const [groupError, setGroupError] = useState<string | null>(null)

  const uploads = data?.uploads ?? []
  const proposals = data?.proposals ?? []

  function resetOutcome() {
    setGroupError(null)
    setNeedsAssignment(false)
  }

  function toggleUpload(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    resetOutcome()
  }

  function applyProposal(proposal: UploadGroupProposal) {
    setSelected(new Set(proposal.upload_ids))
    const prefill: Record<number, Segment> = {}
    for (const [id, segment] of Object.entries(proposal.segments)) prefill[Number(id)] = segment
    setAssignments(prefill)
    resetOutcome()
  }

  const selectedUploads = uploads.filter((u) => selected.has(u.id))
  const canConfirm =
    selected.size > 0 &&
    (!needsAssignment || selectedUploads.every((u) => assignments[u.id] !== undefined))

  async function confirmGroup() {
    if (!canConfirm) return
    setGroupError(null)
    const body: RaceGroupRequest = {
      upload_ids: Array.from(selected),
      segment_assignments: needsAssignment
        ? Object.fromEntries(Object.entries(assignments))
        : undefined,
    }
    try {
      const race = await groupUploads.mutateAsync(body)
      navigate(`/races/${race.id}`)
    } catch (err) {
      // §3: an ambiguous group (two runs in one group, an `other` sport) is rejected
      // with an error naming the ambiguity rather than guessed at. Catch it, surface
      // it, and let the user assign segments explicitly before retrying.
      setGroupError(err instanceof ApiError ? err.message : 'Could not create the race.')
      setNeedsAssignment(true)
    }
  }

  if (isLoading) return <p className="text-sm text-ink/50">Loading tray…</p>
  if (isError) return <p className="text-sm text-run">Could not load the upload tray.</p>

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-semibold text-ink">Ungrouped uploads</h2>

      {uploads.length === 0 ? (
        <p className="text-sm text-ink/50">Nothing waiting to be grouped.</p>
      ) : (
        <>
          {proposals.length > 0 && (
            <ul className="space-y-2">
              {proposals.map((proposal, i) => (
                <li
                  key={i}
                  className="flex items-center justify-between gap-3 rounded border border-hairline p-3 text-sm"
                >
                  <span>
                    Suggested: {proposalLabel(proposal)} ({proposal.upload_ids.length} files)
                  </span>
                  <button
                    type="button"
                    onClick={() => applyProposal(proposal)}
                    className="shrink-0 rounded border border-hairline px-2 py-1 text-xs hover:bg-ink/5"
                  >
                    Select this grouping
                  </button>
                </li>
              ))}
            </ul>
          )}

          <ul className="space-y-1">
            {uploads.map((upload) => (
              <li key={upload.id} className="flex items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  id={`upload-${upload.id}`}
                  checked={selected.has(upload.id)}
                  onChange={() => toggleUpload(upload.id)}
                  className="h-4 w-4 shrink-0"
                />
                <label htmlFor={`upload-${upload.id}`} className="min-w-0 flex-1 truncate">
                  {upload.filename ?? `Upload #${upload.id}`}
                  <span className="ml-2 text-ink/50">{uploadSummary(upload)}</span>
                </label>
              </li>
            ))}
          </ul>

          {needsAssignment && (
            <SegmentAssign uploads={selectedUploads} value={assignments} onChange={setAssignments} />
          )}

          {groupError && <p className="text-sm text-run">{groupError}</p>}

          <button
            type="button"
            onClick={confirmGroup}
            disabled={!canConfirm || groupUploads.isPending}
            className="rounded bg-ink px-3 py-2 text-sm font-medium text-bg transition-opacity disabled:opacity-50"
          >
            {groupUploads.isPending
              ? 'Creating race…'
              : needsAssignment
                ? 'Retry with these segments'
                : 'Create race from selected'}
          </button>
        </>
      )}
    </section>
  )
}
