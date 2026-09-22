'use client'

/**
 * /hris/letters — staff letters (employment confirmation) + document bank.
 *
 * Oprah Mogomotsi document-bank request + CFO 2026-07-15:
 *   • Any staff member can request a letter about themselves. omni auto-fills
 *     it from the payroll record ("To Whom It May Concern" — name, title,
 *     start date, employment confirmation).
 *   • Letterheads are signed only by managers: the request is issued as a
 *     branded-letterhead PDF ONLY after the person's manager (or HR) signs it
 *     off. Staff cannot issue their own letter.
 *   • The blank branded letterhead is downloadable here for any hand-written
 *     letter (the document-bank copy).
 *
 * Backend: hris/letter_views.py (self-scoped, self-service tier).
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, FileText, Download, Send, ShieldCheck, Loader2, Info,
  Check, X, Clock, PenLine,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisSelfService } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface LetterRow {
  id: string
  letter_type: string
  letter_type_label: string
  employee_name: string
  employee_number: string
  job_title: string
  addressee: string
  purpose: string
  status: 'pending' | 'issued' | 'declined'
  status_label: string
  reference: string
  signatory_name: string
  decline_reason: string
  requested_by: string
  decided_at: string | null
  created_at: string
  can_download: boolean
  pdf_url: string
}

interface LettersResponse {
  mine: LetterRow[]
  to_sign: LetterRow[]
  is_signer: boolean
  letter_types: { value: string; label: string }[]
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleDateString('en-BW', { day: '2-digit', month: 'short', year: 'numeric' }) }
  catch { return iso }
}

export default function HrisLettersPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const selfService = useHrisSelfService()
  const canView = allowed === true || selfService === true
  const accessDenied = allowed === false && selfService === false

  const [data, setData] = useState<LettersResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [purpose, setPurpose] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [declineFor, setDeclineFor] = useState<string | null>(null)
  const [declineReason, setDeclineReason] = useState('')
  const [extra, setExtra] = useState<Record<string, string>>({})
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null)

  const flash = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg }); setTimeout(() => setToast(null), 4500)
  }

  const load = useCallback(() => {
    setLoading(true)
    authedHrisFetch('/hris/api/letters/')
      .then(async r => (r.ok ? r.json() : null))
      .then((d: LettersResponse | null) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { if (accessDenied) router.replace('/dashboard') }, [accessDenied, router])
  useEffect(() => { if (canView) load() }, [canView, load])

  const request = async () => {
    setSubmitting(true)
    try {
      const r = await authedHrisFetch('/hris/api/letters/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ letter_type: 'employment_confirmation', purpose: purpose.trim() }),
      })
      if (r.ok) {
        setPurpose('')
        flash('ok', 'Request sent to your manager for sign-off.')
        load()
      } else {
        const e = await r.json().catch(() => ({}))
        flash('err', e.detail || 'Could not send the request.')
      }
    } catch { flash('err', 'Could not send the request.') }
    finally { setSubmitting(false) }
  }

  const decide = async (id: string, decision: 'approve' | 'decline', reason = '') => {
    setBusyId(id)
    try {
      const r = await authedHrisFetch(`/hris/api/letters/${id}/decide/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, reason, additional_details: (extra[id] || '').trim() }),
      })
      if (r.ok) {
        flash('ok', decision === 'approve' ? 'Signed off — letter issued.' : 'Request declined.')
        setDeclineFor(null); setDeclineReason(''); load()
      } else {
        const e = await r.json().catch(() => ({}))
        flash('err', e.detail || 'Could not complete that.')
      }
    } catch { flash('err', 'Could not complete that.') }
    finally { setBusyId(null) }
  }

  const download = async (url: string, filename: string) => {
    try {
      const r = await authedHrisFetch(url)
      if (!r.ok) { flash('err', 'Download failed.'); return }
      const blob = await r.blob()
      const u = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = u; a.download = filename; document.body.appendChild(a); a.click()
      a.remove(); URL.revokeObjectURL(u)
    } catch { flash('err', 'Download failed.') }
  }

  if (!canView) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const pendingMine = (data?.mine || []).some(l => l.status === 'pending')

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Letters" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Letters' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        {toast && (
          <div className="rounded-xl px-4 py-3 text-sm flex items-start gap-2"
               style={{
                 background: toast.kind === 'ok' ? '#10b98119' : '#ef444419',
                 color: toast.kind === 'ok' ? '#047857' : '#b91c1c',
                 border: `1px solid ${toast.kind === 'ok' ? '#6ee7b7' : '#fecaca'}`,
               }}>
            {toast.kind === 'ok' ? <Check className="w-4 h-4 mt-0.5" /> : <X className="w-4 h-4 mt-0.5" />}
            <div>{toast.msg}</div>
          </div>
        )}

        {/* Request hero */}
        <div className="rounded-2xl p-6"
             style={{ background: `linear-gradient(135deg, ${theme.navy}, ${theme.navy}e6)`, color: '#fff' }}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-[11px] uppercase tracking-widest opacity-60 font-semibold">Request a letter</div>
              <h2 className="font-display text-2xl font-bold mt-1 italic">Employment confirmation</h2>
              <p className="text-xs opacity-70 mt-1 max-w-lg">
                A “To Whom It May Concern” letter confirming your role and start date, on the
                Alpha Direct letterhead. Your manager signs it off before it is issued.
              </p>
            </div>
            <FileText className="w-9 h-9 opacity-30" />
          </div>
          <div className="mt-5 flex flex-col sm:flex-row gap-3 sm:items-end">
            <div className="flex-1">
              <label className="text-[10px] uppercase tracking-widest opacity-60 font-semibold">
                What is it for? (optional)
              </label>
              <input
                value={purpose} onChange={e => setPurpose(e.target.value)}
                placeholder="e.g. a home loan application"
                maxLength={200}
                className="mt-1 w-full h-10 px-3 rounded-lg text-sm text-white placeholder-white/50 focus:outline-none"
                style={{ background: '#ffffff1a', border: '1px solid #ffffff33' }}
              />
            </div>
            <button
              onClick={request} disabled={submitting || pendingMine}
              className="h-10 px-4 inline-flex items-center justify-center gap-2 rounded-lg text-sm font-semibold disabled:opacity-50 whitespace-nowrap"
              style={{ background: '#F07F00', color: '#fff' }}>
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              {pendingMine ? 'Request pending' : 'Request letter'}
            </button>
          </div>
          {pendingMine && (
            <p className="text-[11px] opacity-70 mt-2">
              You already have a request awaiting sign-off. It will appear below once issued.
            </p>
          )}
        </div>

        {/* Manager sign-off queue */}
        {data?.to_sign && data.to_sign.length > 0 && (
          <div className="rounded-2xl p-5" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2 mb-3">
              <PenLine className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold" style={{ color: theme.text }}>Awaiting your sign-off</h3>
              <span className="text-[11px] px-1.5 py-0.5 rounded-full font-semibold"
                    style={{ background: '#F07F0019', color: '#b45309' }}>{data.to_sign.length}</span>
            </div>
            <div className="space-y-3">
              {data.to_sign.map(l => (
                <div key={l.id} className="rounded-xl p-4" style={{ border: `1px solid ${theme.cardBdr}` }}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-semibold" style={{ color: theme.text }}>{l.employee_name}</div>
                      <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                        {l.job_title || '—'} · {l.letter_type_label}
                      </div>
                      <div className="text-xs mt-1" style={{ color: theme.t2 }}>
                        Purpose: {l.purpose || '—'} · requested {fmtDate(l.created_at)}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => decide(l.id, 'approve')} disabled={busyId === l.id}
                              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold text-white disabled:opacity-50"
                              style={{ background: '#059669' }}>
                        {busyId === l.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                        Sign off
                      </button>
                      <button onClick={() => { setDeclineFor(declineFor === l.id ? null : l.id); setDeclineReason('') }}
                              disabled={busyId === l.id}
                              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold disabled:opacity-50"
                              style={{ border: `1px solid ${theme.cardBdr}`, color: theme.t2 }}>
                        <X className="w-4 h-4" /> Decline
                      </button>
                    </div>
                  </div>
                  {declineFor === l.id && (
                    <div className="mt-3 flex flex-col sm:flex-row gap-2">
                      <input value={declineReason} onChange={e => setDeclineReason(e.target.value)}
                             placeholder="Reason (shared with the employee)" maxLength={300}
                             className="flex-1 h-9 px-3 rounded-lg text-sm focus:outline-none"
                             style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                      <button onClick={() => decide(l.id, 'decline', declineReason)} disabled={busyId === l.id}
                              className="h-9 px-3 rounded-lg text-sm font-semibold text-white disabled:opacity-50"
                              style={{ background: '#b91c1c' }}>
                        Confirm decline
                      </button>
                    </div>
                  )}
                  <div className="mt-3">
                    <label className="text-[11px] font-semibold" style={{ color: theme.t2 }}>
                      Extra details to add (optional)
                    </label>
                    <textarea
                      value={extra[l.id] || ''}
                      onChange={e => setExtra(prev => ({ ...prev, [l.id]: e.target.value }))}
                      placeholder="e.g. on a permanent contract; salary confidential on request. Aria phrases this into the letter."
                      maxLength={1000} rows={2}
                      className="mt-1 w-full px-3 py-2 rounded-lg text-sm focus:outline-none resize-y"
                      style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* My letters */}
        <div className="rounded-2xl p-5" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold mb-3" style={{ color: theme.text }}>My letters</h3>
          {loading ? (
            <div className="py-6 flex justify-center"><Loader2 className="w-5 h-5 animate-spin" style={{ color: theme.t2 }} /></div>
          ) : (data?.mine || []).length === 0 ? (
            <div className="py-8 text-center text-sm" style={{ color: theme.t2 }}>No letters yet.</div>
          ) : (
            <div className="space-y-2.5">
              {data!.mine.map(l => (
                <div key={l.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl p-3.5"
                     style={{ border: `1px solid ${theme.cardBdr}` }}>
                  <div className="min-w-0">
                    <div className="font-medium truncate" style={{ color: theme.text }}>
                      {l.letter_type_label}{l.reference ? ` · ${l.reference}` : ''}
                    </div>
                    <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                      Requested {fmtDate(l.created_at)}
                      {l.status === 'issued' && l.signatory_name ? ` · signed by ${l.signatory_name}` : ''}
                      {l.status === 'declined' && l.decline_reason ? ` · ${l.decline_reason}` : ''}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <StatusChip status={l.status} label={l.status_label} theme={theme} />
                    {l.can_download && (
                      <button onClick={() => download(l.pdf_url, `Employment-Confirmation-${(l.reference || l.id).replace(/\//g, '-')}.pdf`)}
                              className="inline-flex items-center gap-1 text-sm font-semibold" style={{ color: theme.orange }}>
                        <Download className="w-4 h-4" /> PDF
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Document bank — blank letterhead */}
        <div className="rounded-2xl p-5 flex flex-wrap items-center justify-between gap-3"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-start gap-3">
            <ShieldCheck className="w-5 h-5 mt-0.5" style={{ color: theme.navy }} />
            <div>
              <div className="font-semibold" style={{ color: theme.text }}>Blank Alpha Direct letterhead</div>
              <div className="text-xs mt-0.5 max-w-md" style={{ color: theme.t2 }}>
                The official branded template for any letter written by hand. Letters on the
                letterhead are signed only by a manager.
              </div>
            </div>
          </div>
          <button onClick={() => download('/hris/api/letters/letterhead/', 'Alpha Direct Letterhead.docx')}
                  className="h-9 px-4 inline-flex items-center gap-2 rounded-lg text-sm font-semibold"
                  style={{ background: theme.navy, color: '#fff' }}>
            <Download className="w-4 h-4" /> Download letterhead
          </button>
        </div>

        <p className="text-[11px] flex items-center gap-1.5" style={{ color: theme.t2 }}>
          <Info className="w-3.5 h-3.5" /> Issued letters carry a QR code so a bank or embassy can verify them with Alpha Direct.
        </p>
      </main>
    </div>
  )
}

function StatusChip({ status, label, theme }: { status: string; label: string; theme: any }) {
  const map: Record<string, { bg: string; fg: string; Icon: any }> = {
    pending:  { bg: '#f59e0b19', fg: '#b45309', Icon: Clock },
    issued:   { bg: '#10b98119', fg: '#047857', Icon: Check },
    declined: { bg: '#ef444419', fg: '#b91c1c', Icon: X },
  }
  const s = map[status] || map.pending
  const Icon = s.Icon
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider"
          style={{ background: s.bg, color: s.fg }}>
      <Icon className="w-3 h-3" /> {label}
    </span>
  )
}
