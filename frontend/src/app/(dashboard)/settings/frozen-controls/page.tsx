'use client'

/**
 * /settings/frozen-controls — Frozen-component governance (Internal Audit, 2026-06-23).
 *
 * Makes the ADIC freeze a system control: a registry of frozen components, a
 * CFO maker-checker gate for changes (file → approve/reject + mandatory
 * comment), and an immutable audit trail. Backend: core/frozen_views.py.
 */
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'

interface Component { key: string; label: string; description: string; is_frozen: boolean; pending: number }
interface Request {
  id: string; component_key: string; component_label: string; summary: string
  reason: string; board_impact: string; status: string; status_display: string
  requested_by: string; decided_by: string | null; decision_comment: string | null
  consumed: boolean; created_at: string; decided_at: string | null
}
interface CompResp { components: Component[]; message: string; can_file: boolean; can_decide: boolean }

const STATUS_COLOR: Record<string, string> = {
  pending: '#F4A623', approved: '#34d399', rejected: '#f87171',
}

export default function FrozenControlsPage() {
  const router = useRouter()
  const [comps, setComps] = useState<CompResp | null>(null)
  const [reqs, setReqs] = useState<Request[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // file-request form
  const [fKey, setFKey] = useState('')
  const [fSummary, setFSummary] = useState('')
  const [fReason, setFReason] = useState('')
  const [fImpact, setFImpact] = useState('')

  // decision comments keyed by request id
  const [comment, setComment] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([
        apiFetch<CompResp>('/frozen/components/'),
        apiFetch<{ requests: Request[] }>('/frozen/requests/'),
      ])
      setComps(c); setReqs(r.requests); setErr(null)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load') }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  async function file() {
    if (!fKey || !fSummary.trim() || !fReason.trim() || !fImpact.trim()) {
      setErr('Pick a component and fill in what is changing, why, and the board impact.'); return
    }
    setBusy(true); setErr(null)
    try {
      await apiFetch('/frozen/requests/', {
        method: 'POST',
        body: JSON.stringify({ component_key: fKey, summary: fSummary, reason: fReason, board_impact: fImpact }),
      })
      setFKey(''); setFSummary(''); setFReason(''); setFImpact('')
      await load()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not file request') }
    finally { setBusy(false) }
  }

  async function decide(id: string, decision: 'approve' | 'reject') {
    const c = (comment[id] || '').trim()
    if (!c) { setErr('A decision comment is mandatory.'); return }
    setBusy(true); setErr(null)
    try {
      await apiFetch(`/frozen/requests/${id}/decide/`, {
        method: 'POST', body: JSON.stringify({ decision, comment: c }),
      })
      setComment(p => ({ ...p, [id]: '' }))
      await load()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Decision failed') }
    finally { setBusy(false) }
  }

  const card = { background: 'linear-gradient(180deg,#13263d,#11233a)', borderColor: '#21384f', color: '#eaf1f8' }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Frozen Controls" breadcrumbs={[{ label: 'Settings' }, { label: 'Frozen Controls' }]} />
      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-4">
        {err && <div className="rounded-lg p-3 text-sm bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]">{err}</div>}

        {/* intro */}
        <div className="rounded-2xl p-5 border" style={card}>
          <div className="text-xs uppercase tracking-widest text-[#9bb3c9] font-semibold">CFO-locked components</div>
          <p className="text-sm mt-2 text-[#cdddee]">
            These items are frozen. Any change must go through a CFO maker-checker request — file what is
            changing, why, and the board impact; the CFO approves or rejects with a mandatory comment. Every
            attempt is logged immutably. <span className="text-[#F4A623]">{comps?.message}</span>
          </p>
        </div>

        {/* components */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {comps?.components.map(c => (
            <div key={c.key} className="rounded-2xl p-4 border" style={card}>
              <div className="flex items-center justify-between">
                <b className="text-sm">{c.label}</b>
                <span className="text-[10px] px-2 py-0.5 rounded-full font-bold"
                      style={{ background: 'rgba(244,166,35,.12)', border: '1px solid rgba(244,166,35,.35)', color: '#F4A623' }}>
                  {c.is_frozen ? 'FROZEN' : 'OPEN'}
                </span>
              </div>
              <p className="text-[12px] text-[#9bb3c9] mt-2">{c.description}</p>
              {c.pending > 0 && <p className="text-[11px] mt-2" style={{ color: '#F4A623' }}>{c.pending} pending request{c.pending > 1 ? 's' : ''}</p>}
            </div>
          ))}
        </div>

        {/* file a request */}
        {comps?.can_file && (
          <div className="rounded-2xl p-5 border" style={card}>
            <div className="text-xs uppercase tracking-widest text-[#9bb3c9] font-semibold">File a change request</div>
            <div className="grid grid-cols-1 gap-2 mt-3">
              <select value={fKey} onChange={e => setFKey(e.target.value)}
                      className="px-3 py-2 rounded-lg text-sm" style={{ background: '#0f2236', border: '1px solid #21384f', color: '#eaf1f8' }}>
                <option value="">— select frozen component —</option>
                {comps.components.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
              <input value={fSummary} onChange={e => setFSummary(e.target.value)} placeholder="What is changing (one line)"
                     className="px-3 py-2 rounded-lg text-sm" style={{ background: '#0f2236', border: '1px solid #21384f', color: '#eaf1f8' }} />
              <textarea value={fReason} onChange={e => setFReason(e.target.value)} placeholder="Why is this change needed?" rows={2}
                        className="px-3 py-2 rounded-lg text-sm" style={{ background: '#0f2236', border: '1px solid #21384f', color: '#eaf1f8' }} />
              <textarea value={fImpact} onChange={e => setFImpact(e.target.value)} placeholder="Impact on the board figures" rows={2}
                        className="px-3 py-2 rounded-lg text-sm" style={{ background: '#0f2236', border: '1px solid #21384f', color: '#eaf1f8' }} />
              <button onClick={file} disabled={busy}
                      className="px-4 py-2 rounded-lg text-sm font-semibold self-start disabled:opacity-50"
                      style={{ background: '#F4A623', color: '#11233a' }}>
                {busy ? 'Filing…' : 'File request'}
              </button>
            </div>
          </div>
        )}

        {/* requests + audit trail */}
        <div className="rounded-2xl p-5 border" style={card}>
          <div className="text-xs uppercase tracking-widest text-[#9bb3c9] font-semibold">Change requests &amp; trail</div>
          {reqs.length === 0 && <p className="text-sm text-[#9bb3c9] mt-3">No requests yet.</p>}
          <div className="mt-3 space-y-2">
            {reqs.map(r => (
              <div key={r.id} className="rounded-xl p-3 border" style={{ background: '#0f2236', borderColor: '#21384f' }}>
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <b className="text-sm">{r.component_label}: {r.summary}</b>
                  <span className="text-[10px] px-2 py-0.5 rounded-full font-bold uppercase"
                        style={{ background: `${STATUS_COLOR[r.status]}22`, color: STATUS_COLOR[r.status] }}>
                    {r.status_display}{r.consumed ? ' · used' : ''}
                  </span>
                </div>
                <p className="text-[12px] text-[#cdddee] mt-1"><b>Why:</b> {r.reason}</p>
                <p className="text-[12px] text-[#cdddee]"><b>Board impact:</b> {r.board_impact}</p>
                <p className="text-[11px] text-[#9bb3c9] mt-1">
                  filed by {r.requested_by}{r.decided_by ? ` · decided by ${r.decided_by}` : ''}
                  {r.decision_comment ? ` · "${r.decision_comment}"` : ''}
                </p>
                {comps?.can_decide && r.status === 'pending' && (
                  <div className="mt-2 flex flex-col gap-2">
                    <textarea value={comment[r.id] || ''} onChange={e => setComment(p => ({ ...p, [r.id]: e.target.value }))}
                              placeholder="Decision comment (mandatory)" rows={2}
                              className="px-3 py-2 rounded-lg text-sm" style={{ background: '#11233a', border: '1px solid #21384f', color: '#eaf1f8' }} />
                    <div className="flex gap-2">
                      <button onClick={() => decide(r.id, 'approve')} disabled={busy}
                              className="px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50"
                              style={{ background: '#34d399', color: '#06281f' }}>Approve</button>
                      <button onClick={() => decide(r.id, 'reject')} disabled={busy}
                              className="px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50"
                              style={{ background: '#f87171', color: '#2a0d0d' }}>Reject</button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
