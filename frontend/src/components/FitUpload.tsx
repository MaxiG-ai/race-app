import { useRef, useState } from 'react'
import { ApiError } from '@/api/client'
import { useUploadFiles } from '@/api/uploads'
import type { UploadResult } from '@/types'

/**
 * Multi-file upload with per-file parse feedback (§5 Components). Drag/drop is an
 * enhancement layered on top of a plain, always-functional, keyboard-usable file
 * input — never the only path to upload.
 *
 * `duplicate` is not a footnote: Strava's "export original" returns the uploaded file,
 * not a per-activity file, so three selected files can legitimately dedupe to one
 * distinct upload (§3 "What Strava exports actually look like"). The UI says so rather
 * than looking like it silently dropped a file the user deliberately chose. `failed`
 * keeps its error message on screen rather than disappearing.
 */

function outcomeMessage(result: UploadResult): string {
  switch (result.outcome) {
    case 'created':
      return result.race ? `Imported — added to "${result.race.name}".` : 'Imported.'
    case 'duplicate':
      return 'Identical to an upload you already have.'
    case 'failed':
      return result.message ?? 'Could not parse this file.'
    case 'rejected':
      return result.message ?? 'Rejected before parsing.'
  }
}

const OUTCOME_STYLE: Record<UploadResult['outcome'], string> = {
  created: 'text-ink',
  duplicate: 'text-ink/60',
  failed: 'text-run',
  rejected: 'text-run',
}

export function FitUpload() {
  const uploadFiles = useUploadFiles()
  const [results, setResults] = useState<UploadResult[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function submitFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return
    setError(null)
    uploadFiles.mutate(Array.from(fileList), {
      onSuccess: (data) => setResults((prev) => [...data, ...prev]),
      onError: (err) => {
        setError(err instanceof ApiError ? err.message : 'Upload failed. Please try again.')
      },
    })
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setIsDragOver(true)
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setIsDragOver(false)
          submitFiles(e.dataTransfer.files)
        }}
        className={`rounded border border-dashed p-6 text-center transition-colors ${
          isDragOver ? 'border-ink/40 bg-ink/5' : 'border-hairline'
        }`}
      >
        <p className="mb-3 text-sm text-ink/70">Drop FIT files here, or choose them below.</p>
        <label htmlFor="fit-files" className="sr-only">
          FIT files to upload
        </label>
        <input
          id="fit-files"
          ref={inputRef}
          type="file"
          accept=".fit"
          multiple
          onChange={(e) => submitFiles(e.target.files)}
          className="mx-auto block text-sm text-ink/70 file:mr-3 file:rounded file:border file:border-hairline file:bg-bg file:px-3 file:py-1.5 file:text-sm file:text-ink"
        />
      </div>

      {uploadFiles.isPending && <p className="text-sm text-ink/50">Uploading…</p>}
      {error && <p className="text-sm text-run">{error}</p>}

      {results.length > 0 && (
        <ul className="space-y-1 text-sm">
          {results.map((result, i) => (
            <li key={`${result.filename}-${i}`} className={OUTCOME_STYLE[result.outcome]}>
              <span className="font-medium">{result.filename}</span> — {outcomeMessage(result)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
