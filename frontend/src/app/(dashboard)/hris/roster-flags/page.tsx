'use client'

/**
 * /hris/roster-flags — Unami's decision list (CFO 2026-07-26).
 *
 * A manager has said someone on their roster isn't theirs, or has left. The person
 * STAYS on that manager's roster until Unami decides — a manager must not be able
 * to make someone vanish from their own accountability with one click.
 *
 * Accepting records the decision. The actual reporting-line or status change then
 * goes through the normal dual-approved HR amendment, so it stays attributable.
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { AlertTriangle, CheckCircle2, ChevronLeft, Clock, XCircle } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { authedHrisFetch } from '../_shared'

interface Flag {
  id: string
  employee: string
  employee_id: string
  job_title: string
  current_manager: string | null
  kind: string
  kind_label: string
  note: string
  status: string
  status_label: string
  raised_by: string | null
  raised_at: string
  hr_note: string
}

export default function RosterFlagsPage() {
  const { theme } = useTheme()
  const [flags, setFlags] = useState<Flag[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await authedHrisFetch('/hris/api/roster-flags/')
      const j = await r.json()
      if (!r.ok) { setMsg({ ok: false, text: j.detail || 'Could not load.' }); return }
      setFlags(j.flags || [])
    } catch {
      setMsg({ ok: false, text: 'Could not reach omni.' })
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  async function decide(flag: Flag, action: 'action' | 'reject') {
    setBusy(flag.id); setMsg(null)
    try {
      const r = await authedHrisFetch(`/hris/api/roster-flags/${flag.id}/decide/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, hr_note: notes[flag.id] || '' }),
      })
      const j = await r.json()
      if (!r.ok) setMsg({ ok: false, text: String(j.detail || 'Could not save.') })
      else {
        setMsg({ ok: true, text: action === 'action'
          ? `Accepted for ${flag.employee}. Now make the change in Job Titles & Reporting Lines.`
          : `Not accepted for ${flag.employee}. The manager keeps them on their roster.` })
        load()
      }
    } catch {
      setMsg({ ok: false, text: 'Could not save — check your connection.' })
    } finally { setBusy(null) }
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inputStyle = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Roster Flags"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Roster Flags' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5 max-w-5xl">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        <div className="rounded-2xl p-5" style={card}>
          <h1 className="text-lg font-bold" style={{ color: theme.text }}>
            Managers saying someone isn&apos;t theirs
          </h1>
          <p className="text-sm mt-1" style={{ color: theme.t2 }}>
            The person stays on the manager&apos;s roster until you decide. Accepting
            records your decision — then make the actual change in{' '}
            <Link href="/hris/amendments" className="font-semibold" style={{ color: theme.orange }}>
              Job Titles &amp; Reporting Lines
            </Link>.
          </p>
        </div>

        {msg && (
          <p className="text-sm rounded-lg p-3 flex items-start gap-2"
            style={{ background: msg.ok ? '#F0FDF4' : '#FEF2F2', color: msg.ok ? '#15803d' : '#b91c1c' }}>
            {msg.ok ? <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
                    : <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />}
            {msg.text}
          </p>
        )}

        {loading && (
          <div className="rounded-2xl p-8 text-center" style={card}>
            <div className="w-6 h-6 mx-auto border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
          </div>
        )}

        {!loading && flags.length === 0 && (
          <div className="rounded-2xl p-10 text-center" style={card}>
            <CheckCircle2 className="w-8 h-8 mx-auto mb-3" style={{ color: '#15803d' }} />
            <p style={{ color: theme.text }}>Nothing waiting on you.</p>
          </div>
        )}

        {!loading && flags.map(f => (
          <div key={f.id} className="rounded-2xl p-5 space-y-3" style={card}>
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <p className="font-bold" style={{ color: theme.text }}>{f.employee}</p>
                <p className="text-xs" style={{ color: theme.t2 }}>
                  {f.job_title || 'no job title on record'}
                  {f.current_manager ? ` · recorded as reporting to ${f.current_manager}` : ' · no manager on record'}
                </p>
              </div>
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold"
                style={{ background: theme.oL, color: theme.orange }}>
                <Clock className="w-3 h-3" /> {f.kind_label}
              </span>
            </div>

            <div className="rounded-lg p-3 text-sm" style={{ background: theme.g100 }}>
              <p style={{ color: theme.text }}>
                <strong>{f.raised_by || 'A manager'}</strong> says: {f.note || '(no note)'}
              </p>
            </div>

            <textarea rows={2}
              value={notes[f.id] || ''}
              onChange={e => setNotes(n => ({ ...n, [f.id]: e.target.value }))}
              placeholder="Your decision note (required if you do not accept)"
              className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inputStyle} />

            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={busy === f.id}
                onClick={() => decide(f, 'action')}
                className="px-4 py-2 rounded-lg text-sm font-semibold inline-flex items-center gap-2 disabled:opacity-60"
                style={{ background: theme.orange, color: '#fff' }}>
                <CheckCircle2 className="w-4 h-4" /> Accept
              </button>
              <button type="button" disabled={busy === f.id}
                onClick={() => decide(f, 'reject')}
                className="px-4 py-2 rounded-lg text-sm font-semibold inline-flex items-center gap-2 disabled:opacity-60"
                style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
                <XCircle className="w-4 h-4" /> Not accepted
              </button>
            </div>
          </div>
        ))}
      </main>
    </div>
  )
}
