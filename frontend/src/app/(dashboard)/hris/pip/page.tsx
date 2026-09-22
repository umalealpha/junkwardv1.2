'use client'

/**
 * /hris/pip — CFO-directed Performance Improvement Plan.
 *
 * Shows the directed PIP(s) the caller may see (subject + named viewers + HR).
 * The subject gets a box to explain the issue (the ELRA "opportunity to
 * respond"); everyone else sees it read-only. CFO directive 2026-07-15.
 *
 * Backend: hris/pip_views.py (independent of the dormant ELRA gate).
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, ClipboardList, Loader2, Send, Check, AlertTriangle, Lock,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisSelfService } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface PIP {
  id: string
  title: string
  employee_name: string
  reason: string
  objectives: string
  support_plan: string
  start_date: string
  review_date: string
  end_date: string
  status: string
  status_label: string
  outcome: string
  opened_by: string
  employee_explanation: string
  employee_explanation_at: string | null
  is_subject: boolean
  can_explain: boolean
  viewers: string[]
  created_at: string
}

function fmt(d: string | null): string {
  if (!d) return '—'
  try { return new Date(d).toLocaleDateString('en-BW', { day: '2-digit', month: 'short', year: 'numeric' }) }
  catch { return d }
}

export default function HrisPipPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const selfService = useHrisSelfService()
  const canView = allowed === true || selfService === true
  const accessDenied = allowed === false && selfService === false

  const [pips, setPips] = useState<PIP[]>([])
  const [loading, setLoading] = useState(true)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busyId, setBusyId] = useState<string | null>(null)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null)

  const flash = (kind: 'ok' | 'err', msg: string) => { setToast({ kind, msg }); setTimeout(() => setToast(null), 4500) }

  const load = useCallback(() => {
    setLoading(true)
    authedHrisFetch('/hris/api/pips/directed/')
      .then(async r => (r.ok ? r.json() : { pips: [] }))
      .then(d => setPips(Array.isArray(d.pips) ? d.pips : []))
      .catch(() => setPips([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { if (accessDenied) router.replace('/dashboard') }, [accessDenied, router])
  useEffect(() => { if (canView) load() }, [canView, load])

  const submitExplanation = async (id: string) => {
    const explanation = (drafts[id] || '').trim()
    if (!explanation) { flash('err', 'Please write your explanation first.'); return }
    setBusyId(id)
    try {
      const r = await authedHrisFetch(`/hris/api/pips/directed/${id}/explain/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ explanation }),
      })
      if (r.ok) { flash('ok', 'Your explanation has been recorded.'); load() }
      else { const e = await r.json().catch(() => ({})); flash('err', e.detail || 'Could not save.') }
    } catch { flash('err', 'Could not save.') }
    finally { setBusyId(null) }
  }

  if (!canView) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Performance Improvement Plan" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'PIP' }]} />
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
            {toast.kind === 'ok' ? <Check className="w-4 h-4 mt-0.5" /> : <AlertTriangle className="w-4 h-4 mt-0.5" />}
            <div>{toast.msg}</div>
          </div>
        )}

        {loading ? (
          <div className="py-10 flex justify-center"><Loader2 className="w-6 h-6 animate-spin" style={{ color: theme.t2 }} /></div>
        ) : pips.length === 0 ? (
          <div className="rounded-2xl p-8 text-center" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <ClipboardList className="w-10 h-10 mx-auto mb-3" style={{ color: theme.t2 }} />
            <p className="text-sm" style={{ color: theme.t2 }}>No performance improvement plan on record.</p>
          </div>
        ) : pips.map(p => (
          <div key={p.id} className="rounded-2xl overflow-hidden" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            {/* header */}
            <div className="p-5" style={{ background: `linear-gradient(135deg, ${theme.navy}, ${theme.navy}e6)`, color: '#fff' }}>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[11px] uppercase tracking-widest opacity-60 font-semibold">Performance Improvement Plan</div>
                  <h2 className="font-display text-xl font-bold mt-1">{p.title}</h2>
                  <p className="text-xs opacity-70 mt-1">
                    {p.employee_name} · opened by {p.opened_by} · {fmt(p.start_date)}
                    {p.review_date ? ` · review by ${fmt(p.review_date)}` : ''}
                  </p>
                </div>
                <span className="text-[11px] px-2 py-1 rounded-full font-semibold whitespace-nowrap"
                      style={{ background: '#ffffff22', border: '1px solid #ffffff33' }}>{p.status_label}</span>
              </div>
            </div>

            <div className="p-5 space-y-4">
              <Section title="The issue" body={p.reason} theme={theme} />
              <Section title="Required improvement" body={p.objectives} theme={theme} />
              <Section title="Support provided" body={p.support_plan} theme={theme} />

              {/* Explanation */}
              <div className="rounded-xl p-4" style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}` }}>
                <div className="flex items-center gap-2 mb-2">
                  <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
                    Employee explanation — why the payments were delayed
                  </h3>
                  {!p.is_subject && <Lock className="w-3.5 h-3.5" style={{ color: theme.t2 }} />}
                </div>

                {p.employee_explanation && (
                  <div className="text-sm whitespace-pre-wrap mb-3 rounded-lg p-3"
                       style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                    {p.employee_explanation}
                    <div className="text-[11px] mt-2" style={{ color: theme.t2 }}>
                      Recorded {p.employee_explanation_at ? fmt(p.employee_explanation_at) : ''}
                    </div>
                  </div>
                )}

                {p.can_explain ? (
                  <div>
                    <textarea
                      value={drafts[p.id] ?? ''}
                      onChange={e => setDrafts(d => ({ ...d, [p.id]: e.target.value }))}
                      placeholder={p.employee_explanation ? 'Update your explanation…' : 'Explain, in your own words, why these payments were delayed…'}
                      rows={5} maxLength={8000}
                      className="w-full px-3 py-2 rounded-lg text-sm focus:outline-none resize-y"
                      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                    <button onClick={() => submitExplanation(p.id)} disabled={busyId === p.id}
                            className="mt-2 inline-flex items-center gap-2 h-9 px-4 rounded-lg text-sm font-semibold text-white disabled:opacity-50"
                            style={{ background: '#F07F00' }}>
                      {busyId === p.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                      {p.employee_explanation ? 'Update explanation' : 'Submit explanation'}
                    </button>
                  </div>
                ) : !p.employee_explanation ? (
                  <p className="text-sm italic" style={{ color: theme.t2 }}>
                    Awaiting {p.employee_name}&rsquo;s explanation.
                  </p>
                ) : null}
              </div>

              {p.outcome && <Section title="Outcome" body={p.outcome} theme={theme} />}
            </div>
          </div>
        ))}
      </main>
    </div>
  )
}

function Section({ title, body, theme }: { title: string; body: string; theme: any }) {
  if (!body) return null
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-wider font-semibold mb-1" style={{ color: theme.t2 }}>{title}</h3>
      <p className="text-sm whitespace-pre-wrap" style={{ color: theme.text }}>{body}</p>
    </div>
  )
}
