'use client'

/**
 * The shared notebook — CFO 2026-07-25.
 *
 * "why do not we create a secret vault in omni, a clean text base notebook
 *  which you can read quickly than a book in one drive"
 *
 * One big text box on purpose. The moment this becomes fields and forms it
 * stops being something the CFO can correct in ten seconds, and it goes stale —
 * which is the only failure mode that matters here. Claude reads the same text
 * from /api/v1/notebook/raw/ (plain text, ~0.2s) before its first reply in every
 * session, on both machines.
 */
import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

type Notebook = {
  slug: string
  title: string
  body: string
  updated_at: string | null
  updated_by: string | null
}

export default function NotebookPage() {
  const [body, setBody] = useState('')
  const [saved, setSaved] = useState('')
  const [meta, setMeta] = useState<Notebook | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch<Notebook>('/api/v1/notebook/')
      setMeta(data)
      setBody(data.body || '')
      setSaved(data.body || '')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open the notebook.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const dirty = body !== saved

  async function save() {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await apiFetch('/api/v1/notebook/', {
        method: 'PUT',
        body: JSON.stringify({ body }),
      })
      setSaved(body)
      setNote('Saved. Claude will read this at the start of its next session.')
      void load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-2xl font-semibold" style={{ color: '#0D1B2A' }}>
        Notebook
      </h1>
      <p className="mt-1 text-sm text-gray-600">
        The shared page between you and Claude. Claude reads this before it says
        anything, in every session, on both machines. If this page disagrees with
        Omni&apos;s own data, this page wins.
      </p>
      <p className="mt-1 text-xs text-gray-500">
        Plain text. No passwords, keys or bank details — those belong in the
        Secrets Vault.
      </p>

      {loading ? (
        <p className="mt-6 text-sm text-gray-500">Opening…</p>
      ) : (
        <>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            spellCheck={false}
            className="mt-4 w-full h-[60vh] rounded border border-gray-300 p-3
                       font-mono text-sm leading-relaxed focus:outline-none
                       focus:ring-2"
            style={{ borderColor: dirty ? '#F4A623' : undefined }}
          />

          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={() => void save()}
              disabled={busy || !dirty}
              className="rounded px-4 py-2 text-white text-sm font-medium
                         disabled:opacity-40"
              style={{ background: '#0D1B2A' }}
            >
              {busy ? 'Saving…' : 'Save'}
            </button>
            {dirty && (
              <span className="text-sm" style={{ color: '#F4A623' }}>
                Unsaved changes
              </span>
            )}
            <span className="ml-auto text-xs text-gray-500">
              {body.length.toLocaleString()} characters
              {meta?.updated_at
                ? ` · last saved ${new Date(meta.updated_at).toLocaleString()}`
                : ''}
              {meta?.updated_by ? ` by ${meta.updated_by}` : ''}
            </span>
          </div>
        </>
      )}

      {note && <p className="mt-3 text-sm text-green-700">{note}</p>}
      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}
    </div>
  )
}
