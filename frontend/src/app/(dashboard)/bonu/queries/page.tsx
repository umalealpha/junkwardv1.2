'use client'

/**
 * /bonu/queries — the letters. This is the screen that actually recovers money.
 *
 * CFO 2026-08-03, recommendation 4: *"Turns a finding into a written query with a reply
 * date, and chases it. Money only comes back when somebody asks."*
 *
 * Design intent: the chase list is at the TOP, because it is the only part with a deadline
 * on it. Then what has been recovered, because that is the number that says whether the
 * whole exercise is worth running. The letters themselves are last — they are reference.
 *
 * Sending is deliberately NOT automatic. The letter is here to copy and send, and this
 * screen records what happened, so the recovery figure is real rather than hopeful.
 *
 * Backend: /api/v1/bonu/queries/ · /api/v1/bonu/queries/<id>/
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { AlarmClock, Check, Copy, FilePlus2, Loader2, Send } from 'lucide-react'
import {
  AMBER, BonuTabs, GREEN, LINE, NAVY, Note, ORANGE, RED, Stat, Table, money, money2, td, tdNum, trBorder,
} from '../_shared'

interface Letter {
  id: string; reference: string; firm: string; subject: string
  status: string; status_label: string
  amount_queried: number; amount_conceded: number
  sent_on: string; reply_due_on: string; replied_on: string
  overdue: boolean; chased_count: number; findings: number; body: string
}
interface Chase {
  query_id: string; reference: string; firm: string; amount_queried: number
  days_overdue: number; chased_count: number; escalate_to_cfo: boolean; line: string
}
interface Data {
  queries: Letter[]
  chase_today: Chase[]
  recovery: {
    letters: number; open: number; amount_queried: number; amount_recovered: number
    recovery_rate: number | null; answered: number; still_waiting: number
  }
  unqueried_findings: number
}

const STATUS_TONE: Record<string, string> = {
  draft: '#6B7280', sent: AMBER, replied: NAVY, conceded: GREEN, rejected: RED, withdrawn: '#6B7280',
}

export default function BonuQueriesPage() {
  const [d, setD] = useState<Data | null>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [openId, setOpenId] = useState('')
  const [conceded, setConceded] = useState<Record<string, string>>({})

  const [err, setErr] = useState('')

  const load = useCallback(() => {
    apiFetch<Data>('/bonu/queries/')
      .then((x) => { setD(x); setErr('') })
      // Swallowing this left the page spinning for ever with nothing to read — say what
      // happened instead.
      .catch(() => setErr('Could not load the letters.'))
  }, [])
  useEffect(load, [load])

  const draftAll = async () => {
    setBusy('draft'); setMsg('')
    try {
      const out = await apiFetch<{ ok: boolean; message?: string; detail?: string }>(
        '/bonu/queries/', { method: 'POST', body: JSON.stringify({}) })
      setMsg(out.message || out.detail || '')
      load()
    } finally { setBusy('') }
  }

  const act = async (id: string, action: string) => {
    setBusy(id + action); setMsg('')
    try {
      const body: Record<string, unknown> = { action }
      if (action === 'conceded') body.amount_conceded = Number(conceded[id] || 0)
      await apiFetch(`/bonu/queries/${id}/`, { method: 'POST', body: JSON.stringify(body) })
      load()
    } finally { setBusy('') }
  }

  if (!d) {
    return (
      <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
        <TopBar title="BONU — queries" />
        <div className="mx-auto max-w-[1400px] px-6 py-5">
          <BonuTabs active="/bonu/queries" />
          <div className="mt-5">
            {err ? (
              <Note tone="warn" title="Not loaded">{err}</Note>
            ) : (
              <div className="flex items-center gap-2 text-[13px]" style={{ color: '#6B7280' }}>
                <Loader2 className="h-4 w-4 animate-spin" style={{ color: ORANGE }} />
                Loading the letters…
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  const r = d.recovery

  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — queries" />
      <div className="mx-auto max-w-[1400px] px-6 py-5">
        <BonuTabs active="/bonu/queries" />

        {msg ? <div className="mt-4"><Note tone="good">{msg}</Note></div> : null}

        {/* CHASE FIRST — it is the only thing here with a deadline. */}
        {d.chase_today.length ? (
          <Card className="mt-5" style={{ borderColor: '#F3D6D6' }}>
            <CardContent className="p-4">
              <div className="mb-3 flex items-center gap-2 text-[14px] font-bold" style={{ color: RED }}>
                <AlarmClock className="h-4 w-4" />
                Chase today — {d.chase_today.length} firm(s) past the reply date
              </div>
              <div className="space-y-2">
                {d.chase_today.map((c) => (
                  <div
                    key={c.query_id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-lg px-3 py-2.5"
                    style={{ background: '#FEF7F7', border: '1px solid #F3D6D6' }}
                  >
                    <div className="text-[13px]" style={{ color: '#374151' }}>
                      <b style={{ color: NAVY }}>{c.firm}</b> — {c.reference},{' '}
                      {money2(c.amount_queried)} held, <b>{c.days_overdue} day(s) late</b>
                      {c.escalate_to_cfo ? (
                        <span className="ml-2 rounded px-1.5 py-0.5 text-[11px] font-bold text-white" style={{ background: RED }}>
                          escalate to the CFO
                        </span>
                      ) : null}
                    </div>
                    <button
                      onClick={() => act(c.query_id, 'chased')}
                      disabled={busy === c.query_id + 'chased'}
                      className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-semibold text-white transition-transform duration-150 hover:-translate-y-px"
                      style={{ background: NAVY }}
                    >
                      {busy === c.query_id + 'chased'
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <Send className="h-3.5 w-3.5" style={{ color: ORANGE }} />}
                      Chased (#{c.chased_count + 1})
                    </button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ) : null}

        {/* DID ASKING WORK — the number that justifies the exercise. */}
        <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-5">
          <Card><CardContent className="p-4">
            <Stat label="Letters" value={String(r.letters)} sub={`${r.open} still open`} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Money queried" value={money(r.amount_queried)} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat label="Money back" value={money(r.amount_recovered)} tone={GREEN} />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat
              label="Recovery rate"
              value={r.recovery_rate == null ? '—' : `${r.recovery_rate}%`}
              tone={NAVY}
              sub="of what we queried"
            />
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <Stat
              label="Findings not asked about"
              value={String(d.unqueried_findings)}
              tone={d.unqueried_findings ? AMBER : GREEN}
            />
          </CardContent></Card>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="max-w-2xl text-[13px]" style={{ color: '#6B7280' }}>
            One letter per firm, grouping its open findings — nobody answers eleven separate
            emails. Each item is a question with the amount next to it, and the letter carries a
            reply date, because an undated query is a query that gets filed.
          </div>
          <button
            onClick={draftAll}
            disabled={busy === 'draft' || !d.unqueried_findings}
            className="flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white transition-transform duration-150 hover:-translate-y-px disabled:opacity-50"
            style={{ background: NAVY }}
          >
            {busy === 'draft'
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <FilePlus2 className="h-4 w-4" style={{ color: ORANGE }} />}
            Draft letters from open findings
          </button>
        </div>

        <Card className="mt-4">
          <CardContent className="p-0">
            <Table head={['Reference', 'Firm', 'Items', 'Queried', 'Given back', 'Status', 'Reply by', 'Action']}>
              {d.queries.map((q) => (
                <tr key={q.id} style={trBorder}>
                  <td className={td}>
                    <button
                      onClick={() => setOpenId(openId === q.id ? '' : q.id)}
                      className="font-semibold underline-offset-2 hover:underline"
                      style={{ color: NAVY }}
                    >
                      {q.reference}
                    </button>
                  </td>
                  <td className={td}>{q.firm}</td>
                  <td className={tdNum}>{q.findings}</td>
                  <td className={tdNum}>{money2(q.amount_queried)}</td>
                  <td className={tdNum} style={{ color: q.amount_conceded ? GREEN : '#374151' }}>
                    {money2(q.amount_conceded)}
                  </td>
                  <td className={td}>
                    <span
                      className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
                      style={{ background: '#F3F4F6', color: STATUS_TONE[q.status] || '#374151' }}
                    >
                      {q.status_label}
                    </span>
                  </td>
                  <td className={td} style={{ color: q.overdue ? RED : '#374151' }}>
                    {q.reply_due_on || '—'}{q.overdue ? ' · late' : ''}
                  </td>
                  <td className={td}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {q.status === 'draft' ? (
                        <SmallBtn onClick={() => act(q.id, 'sent')} busy={busy === q.id + 'sent'}>
                          Mark sent
                        </SmallBtn>
                      ) : null}
                      {q.status === 'sent' ? (
                        <>
                          <input
                            value={conceded[q.id] || ''}
                            onChange={(e) => setConceded({ ...conceded, [q.id]: e.target.value })}
                            placeholder="amount"
                            className="w-[84px] rounded px-1.5 py-1 text-right text-[12px]"
                            style={{ border: `1px solid ${LINE}` }}
                          />
                          <SmallBtn onClick={() => act(q.id, 'conceded')} busy={busy === q.id + 'conceded'} tone={GREEN}>
                            Credited
                          </SmallBtn>
                          <SmallBtn onClick={() => act(q.id, 'rejected')} busy={busy === q.id + 'rejected'} tone={RED}>
                            Stood firm
                          </SmallBtn>
                        </>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
              {!d.queries.length ? (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-[13px]" style={{ color: '#6B7280' }}>
                  No letters yet. {d.unqueried_findings
                    ? `There are ${d.unqueried_findings} finding(s) nobody has asked about — draft the letters above.`
                    : 'There are no open findings to query.'}
                </td></tr>
              ) : null}
            </Table>
          </CardContent>
        </Card>

        {openId ? (() => {
          const q = d.queries.find((x) => x.id === openId)
          if (!q) return null
          return (
            <Card className="mt-4">
              <CardContent className="p-4">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide" style={{ color: '#6B7280' }}>
                      {q.reference} · to {q.firm}
                    </div>
                    <div className="text-[14px] font-bold" style={{ color: NAVY }}>{q.subject}</div>
                  </div>
                  <button
                    onClick={() => navigator.clipboard?.writeText(q.body)}
                    className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px]"
                    style={{ border: `1px solid ${LINE}`, color: NAVY }}
                  >
                    <Copy className="h-3.5 w-3.5" /> Copy the letter
                  </button>
                </div>
                <pre
                  className="whitespace-pre-wrap rounded-lg p-4 text-[13px] leading-relaxed"
                  style={{ background: '#F8FAFC', border: `1px solid ${LINE}`, color: '#374151' }}
                >
                  {q.body}
                </pre>
                <div className="mt-2 text-[12px]" style={{ color: '#6B7280' }}>
                  These are questions, not accusations — a firm that gets a clear question usually
                  answers it, and often credits the line. Send it from your own mailbox, then mark
                  it sent here.
                </div>
              </CardContent>
            </Card>
          )
        })() : null}
      </div>
    </div>
  )
}

function SmallBtn({
  onClick, busy, tone, children,
}: { onClick: () => void; busy?: boolean; tone?: string; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-[12px] font-semibold transition-colors duration-150 hover:bg-slate-100 disabled:opacity-50"
      style={{ border: `1px solid ${LINE}`, color: tone || NAVY }}
    >
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
      {children}
    </button>
  )
}
