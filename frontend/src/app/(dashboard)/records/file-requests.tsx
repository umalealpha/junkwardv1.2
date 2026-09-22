'use client'

/**
 * Records file requests (Tshepo Maswabi 2026-08-11).
 *
 * RequestFileButton — per-row "Request" button + modal; staff ask for a physical
 * file, which sits pending until Human Capital / Records decides.
 * FileRequestsPanel — the trail: Human Capital / Records see pending requests to
 * approve or deny (with a mandatory reason); everyone sees their own requests.
 */
import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  approveRecordFileRequest, createRecordFileRequest, denyRecordFileRequest,
  listRecordFileRequests, type RecordFileRequestRow,
} from '@/lib/api'
import { Button } from '@/components/ui/button'
import { FileText, X, Check, Ban, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react'

export function RequestFileButton(
  { record, onDone }: { record: { id: string; reference: string; title: string }; onDone: () => void },
) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <FileText className="w-3.5 h-3.5 mr-1" /> Request
      </Button>
      {open && <RequestModal record={record} onClose={() => setOpen(false)} onSaved={onDone} />}
    </>
  )
}

function RequestModal(
  { record, onClose, onSaved }:
  { record: { id: string; reference: string; title: string }; onClose: () => void; onSaved: () => void },
) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  async function submit() {
    setErr(null)
    if (reason.trim().length < 5) { setErr('Please give a short reason for the request.'); return }
    setBusy(true)
    try { await createRecordFileRequest(record.id, reason.trim()); setDone(true); onSaved() }
    catch (e) { setErr(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Could not submit') }
    finally { setBusy(false) }
  }

  if (typeof document === 'undefined') return null
  return createPortal(
    <div className="fixed inset-0 z-[100] grid place-items-center p-4 bg-black/50" onMouseDown={onClose}>
      <div className="w-full max-w-md rounded-xl bg-white shadow-xl" onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <div className="flex items-center gap-2 font-semibold"><FileText className="w-4 h-4" /> Request this file</div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-4 h-4" /></button>
        </div>
        {done ? (
          <div className="p-6 text-center">
            <CheckCircle2 className="w-10 h-10 mx-auto text-green-600" />
            <p className="mt-3 font-semibold">Request sent</p>
            <p className="text-sm text-gray-500 mt-1">
              Human Capital / Records will approve or deny it. You can track it in the File requests list below.
            </p>
            <Button size="sm" className="mt-4" onClick={onClose}>Done</Button>
          </div>
        ) : (
          <div className="p-5 space-y-3">
            <div className="text-sm text-gray-600">
              <span className="font-mono">{record.reference}</span> — {record.title}
            </div>
            <label className="block text-sm">
              <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Reason</span>
              <textarea value={reason} onChange={e => setReason(e.target.value)} rows={3}
                placeholder="Why you need this file"
                className="w-full rounded-lg border px-3 py-2 text-sm outline-none focus:border-gray-400" />
            </label>
            {err && (
              <div className="rounded-lg p-3 text-sm flex items-center gap-2 bg-red-50 border border-red-200 text-red-800">
                <AlertCircle className="w-4 h-4 shrink-0" /> {err}
              </div>
            )}
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={submit} disabled={busy}>
                {busy ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <FileText className="w-3.5 h-3.5 mr-1" />}
                {busy ? 'Sending…' : 'Send request'}
              </Button>
              <Button size="sm" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

export function FileRequestsPanel({ reloadKey, onDone }: { reloadKey: number; onDone: () => void }) {
  const [mine, setMine] = useState<RecordFileRequestRow[] | null>(null)
  const [pending, setPending] = useState<RecordFileRequestRow[]>([])
  const [canApprove, setCanApprove] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [denying, setDenying] = useState<string | null>(null)
  const [note, setNote] = useState('')

  const load = useCallback(async () => {
    setErr(null)
    try {
      const m = await listRecordFileRequests('mine')
      setMine(m.results); setCanApprove(m.can_approve)
      if (m.can_approve) setPending((await listRecordFileRequests('to_approve')).results)
    } catch (e) { setErr(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed to load') }
  }, [])
  useEffect(() => { load() }, [load, reloadKey])

  async function approve(id: string) {
    setBusy(id); setErr(null)
    try { await approveRecordFileRequest(id); await load(); onDone() }
    catch (e) { setErr(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Approve failed') }
    finally { setBusy(null) }
  }
  async function deny(id: string) {
    if (note.trim().length < 3) { setErr('A reason is required to deny.'); return }
    setBusy(id); setErr(null)
    try { await denyRecordFileRequest(id, note.trim()); setDenying(null); setNote(''); await load() }
    catch (e) { setErr(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Deny failed') }
    finally { setBusy(null) }
  }

  if (mine !== null && mine.length === 0 && pending.length === 0) return null
  const pill = (s: string) =>
    s === 'approved' ? 'bg-green-100 text-green-800'
      : s === 'denied' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'

  return (
    <div className="rounded-xl border bg-white p-4 mt-4">
      <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">File requests</div>
      {err && (
        <div className="rounded-lg p-3 text-sm flex items-center gap-2 mb-3 bg-red-50 border border-red-200 text-red-800">
          <AlertCircle className="w-4 h-4 shrink-0" /> {err}
        </div>
      )}

      {canApprove && pending.length > 0 && (
        <div className="mb-4">
          <div className="text-[11px] font-semibold text-amber-700 mb-1">To approve ({pending.length})</div>
          <div className="space-y-2">
            {pending.map(r => (
              <div key={r.id} className="rounded-lg border p-3">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="text-sm">
                    <span className="font-mono">{r.record.reference}</span> — {r.record.title}
                    <div className="text-xs text-gray-500 mt-0.5">“{r.reason}” · by {r.requested_by || '—'}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button size="sm" disabled={busy !== null} onClick={() => approve(r.id)}>
                      <Check className="w-3.5 h-3.5 mr-1" /> Approve
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy !== null}
                      onClick={() => { setDenying(denying === r.id ? null : r.id); setNote('') }}>
                      <Ban className="w-3.5 h-3.5 mr-1" /> Deny
                    </Button>
                  </div>
                </div>
                {denying === r.id && (
                  <div className="mt-2 flex items-center gap-2">
                    <input value={note} onChange={e => setNote(e.target.value)} autoFocus
                      placeholder="Reason (required)"
                      className="flex-1 rounded-lg border px-3 py-1.5 text-sm outline-none focus:border-gray-400" />
                    <Button size="sm" variant="outline" disabled={busy !== null || note.trim().length < 3}
                      onClick={() => deny(r.id)}>Send back</Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {mine && mine.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-gray-500 mb-1">My requests</div>
          <div className="space-y-1.5">
            {mine.map(r => (
              <div key={r.id} className="flex items-center justify-between gap-3 text-sm border-b last:border-0 py-1.5">
                <div><span className="font-mono">{r.record.reference}</span> — {r.record.title}
                  {r.decision_note && <span className="text-xs text-gray-500"> · {r.decision_note}</span>}
                </div>
                <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${pill(r.status)}`}>
                  {r.status_display}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
