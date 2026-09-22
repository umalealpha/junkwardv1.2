'use client'

/**
 * /bank-proofs — FNB proofs of payment, and the money Omni never saw.
 *
 * CFO 2026-08-26: "these are the payment proofs that come into my email — can we
 * pick them up and put them inside the relevant payment request?"
 *
 * Measured over a month of real mail before this was built: 48 payments, BWP
 * 2,585,588.37, and NOT ONE email carried an attachment — the body text is the
 * proof. Only 3 matched a payment request; 15 of the 17 carrying a claim number
 * matched a claim. So the page leads with the queue, not the filed list: the
 * interesting number is what has no home in Omni at all.
 *
 * The AI proposes; nothing here files itself. Confirm is a person's click.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Landmark, FileText, Check, Undo2, AlertTriangle } from 'lucide-react'

const NAVY = '#0D1B2A', ORANGE = '#F4A623', GREEN = '#2E9E5B', RED = '#D14343'

type Proof = {
  id: string; reference: string; amount: number; bank_status: string; paid: boolean
  received_at: string; state: string; state_display: string; is_filed: boolean
  claim_number: string; claim_matched: boolean; payment_request_ref: string
  proposed_kind: string; proposed_ref: string; proposed_reason: string
  has_pdf: boolean; confirmed_by: string; review_note: string; body_text: string
}

const num = (v: number) =>
  new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    .format(Number(v) || 0)

const TABS = [
  { key: 'proposed',      label: 'Needs a decision' },
  { key: 'unfiled',       label: 'No record in Omni' },
  { key: 'filed_claim',   label: 'Filed to a claim' },
  { key: 'filed_request', label: 'Filed to a request' },
  { key: '',              label: 'Everything' },
] as const

export default function BankProofsPage() {
  const [tab, setTab] = useState<string>('proposed')
  const [rows, setRows] = useState<Proof[] | null>(null)
  const [summary, setSummary] = useState<any>(null)
  const [err, setErr] = useState('')

  const load = useCallback(async (state: string) => {
    setRows(null)
    const { apiFetch } = await import('@/lib/api')
    try {
      const q = state ? `?state=${state}` : ''
      const d = await apiFetch<any>(`/fnb/proofs/${q}`)
      setRows(d.proofs); setSummary(d.summary); setErr('')
    } catch (e: any) { setErr(e?.message || 'Could not load the register.'); setRows([]) }
  }, [])

  useEffect(() => { load(tab) }, [tab, load])

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <div className="max-w-6xl mx-auto px-4 py-6">
        <div className="flex items-center gap-2.5 mb-1">
          <Landmark size={20} style={{ color: ORANGE }} />
          <h1 className="text-xl font-semibold" style={{ color: NAVY }}>
            Bank proofs of payment
          </h1>
        </div>
        <p className="text-sm text-slate-500 mb-5 max-w-2xl">
          Every payment confirmation First National Bank emails is captured and filed
          against the claim or the payment request it belongs to. FNB sends no
          attachment, so the confirmation text itself is kept as the proof.
        </p>

        {summary && (
          <div className="rounded-xl p-4 mb-4" style={{ background: NAVY }}>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              {[
                ['Filed to a claim', summary.filed_claim, GREEN],
                ['Filed to a request', summary.filed_request, GREEN],
                ['Awaiting a decision', summary.proposed, ORANGE],
                ['No record in Omni', summary.unfiled, RED],
              ].map(([label, v, c]: any) => (
                <div key={label}>
                  <p className="text-2xl font-semibold tabular-nums" style={{ color: c }}>{v}</p>
                  <p className="text-[11px] text-white/60">{label}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 pt-3 border-t border-white/10 flex flex-wrap gap-x-8 gap-y-1">
              <p className="text-xs text-white/70">
                Confirmed paid: <span className="text-white font-semibold tabular-nums">
                  BWP {num(summary.paid_value)}</span> across {summary.paid}
              </p>
              <p className="text-xs text-white/70">
                With nowhere to file it: <span className="font-semibold tabular-nums"
                  style={{ color: ORANGE }}>BWP {num(summary.unfiled_value)}</span>
              </p>
            </div>
          </div>
        )}

        <div className="flex gap-1.5 mb-4 flex-wrap">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)}
                    className="text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
                    style={tab === t.key
                      ? { background: NAVY, color: 'white' }
                      : { background: 'white', color: '#475569', border: '1px solid #e2e8f0' }}>
              {t.label}
            </button>
          ))}
        </div>

        {err && <p className="text-sm mb-3" style={{ color: RED }}>{err}</p>}
        {!rows && <p className="text-sm text-slate-400">Loading…</p>}
        {rows?.length === 0 && (
          <div className="rounded-xl border border-slate-200 bg-white p-8 text-center">
            <p className="text-sm text-slate-500">Nothing here.</p>
          </div>
        )}

        <div className="space-y-2">
          {rows?.map((p) => <ProofRow key={p.id} p={p} onDone={() => load(tab)} />)}
        </div>
      </div>
    </div>
  )
}

function ProofRow({ p, onDone }: { p: Proof; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const act = async (accept: boolean) => {
    setBusy(true); setErr('')
    const { apiFetch } = await import('@/lib/api')
    try {
      await apiFetch(`/fnb/proofs/${p.id}/confirm/`, {
        method: 'POST', body: JSON.stringify({ accept, note }),
        headers: { 'Content-Type': 'application/json' },
      })
      onDone()
    } catch (e: any) { setErr(e?.message || 'Could not save.') }
    setBusy(false)
  }

  const download = async () => {
    const { downloadFnbProofPdf } = await import('@/lib/api')
    try { await downloadFnbProofPdf(p.id, p.reference) }
    catch (e: any) { setErr(e?.message || 'Download failed.') }
  }

  const badge = p.state === 'proposed' ? { bg: '#FDF3E3', fg: ORANGE }
    : p.is_filed ? { bg: '#E8F5EE', fg: GREEN }
    : p.state === 'dismissed' ? { bg: '#EEF2F6', fg: '#64748B' }
    : { bg: '#FBE9E9', fg: RED }

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button onClick={() => setOpen((v) => !v)}
              className="w-full text-left px-3 py-2.5 flex items-start gap-3 hover:bg-slate-50 transition-colors">
        <span className="flex-1 min-w-0">
          <span className="block text-sm truncate" style={{ color: NAVY }}>{p.reference}</span>
          <span className="block text-[11px] text-slate-400 mt-0.5">
            {new Date(p.received_at).toLocaleDateString('en-GB')}
            {p.claim_number && ` · claim ${p.claim_number}${p.claim_matched ? '' : ' (not found)'}`}
            {p.payment_request_ref && ` · ${p.payment_request_ref}`}
          </span>
        </span>
        <span className="text-sm font-semibold tabular-nums shrink-0" style={{ color: NAVY }}>
          {num(p.amount)}
        </span>
        <span className="text-[10px] px-2 py-0.5 rounded-full shrink-0 whitespace-nowrap"
              style={{ background: badge.bg, color: badge.fg }}>{p.state_display}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-slate-100 space-y-3">
          <pre className="text-[11px] text-slate-600 whitespace-pre-wrap font-sans
                          bg-slate-50 rounded-lg p-2.5 leading-relaxed">{p.body_text}</pre>

          {p.state === 'proposed' && (
            <div className="rounded-lg p-2.5" style={{ background: '#FDF3E3' }}>
              <p className="text-xs font-medium mb-1" style={{ color: NAVY }}>
                <AlertTriangle size={12} className="inline mr-1" style={{ color: ORANGE }} />
                Suggested: file against {p.proposed_kind === 'claim' ? 'claim' : 'payment request'}
                {' '}<b>{p.proposed_ref}</b>
              </p>
              <p className="text-[11px] text-slate-600">{p.proposed_reason}</p>
              <p className="text-[10px] text-slate-500 mt-1.5">
                This is a suggestion only. Nothing is filed until you confirm it.
              </p>
            </div>
          )}

          {p.confirmed_by && (
            <p className="text-[10px] text-slate-400">
              {p.confirmed_by}{p.review_note ? ` — "${p.review_note}"` : ''}
            </p>
          )}

          {err && <p className="text-[11px]" style={{ color: RED }}>{err}</p>}

          <div className="flex flex-wrap items-center gap-2">
            {p.has_pdf && (
              <button onClick={download}
                      className="text-xs font-medium px-3 py-1.5 rounded-lg flex items-center gap-1.5"
                      style={{ background: 'white', color: NAVY, border: '1px solid #e2e8f0' }}>
                <FileText size={12} /> Proof PDF
              </button>
            )}
            {p.state === 'proposed' && (
              <>
                <input value={note} onChange={(e) => setNote(e.target.value)}
                       placeholder="Note (required to reject)"
                       className="text-xs rounded-lg border border-slate-200 px-2 py-1.5 flex-1 min-w-[150px]" />
                <button disabled={busy} onClick={() => act(true)}
                        className="text-xs font-medium px-3 py-1.5 rounded-lg flex items-center gap-1 disabled:opacity-40"
                        style={{ background: GREEN, color: 'white' }}>
                  <Check size={12} /> File it here
                </button>
                <button disabled={busy || !note.trim()} onClick={() => act(false)}
                        title={!note.trim() ? 'Say why it is wrong' : ''}
                        className="text-xs font-medium px-3 py-1.5 rounded-lg flex items-center gap-1 disabled:opacity-40"
                        style={{ background: '#FBE9E9', color: RED }}>
                  <Undo2 size={12} /> Wrong match
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
