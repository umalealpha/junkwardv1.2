'use client'

/**
 * /hris/leave-excuse — Leave Excuse Response dashboard (CFO 2026-07-22).
 *
 * Exec/HR only (server-gated by hris.workforce_views._can_see_excuses). For a
 * chosen day it lists the tracking-eligible staff whose PRODUCTIVE hours were LOW
 * or NO, their own-words explanation, a status chip ("not accepted" in red for a
 * power cut / a tracker excuse with no IT ticket), and Aria's anonymised read of
 * the day. Read-only — the page never sends anything; the auto-responder is the
 * separate process_leave_excuses command behind the LEAVE_EXCUSE_AUTOSEND gate.
 *
 * On-screen we show only names, hours and explanations — no ID / bank / medical.
 */
import { useCallback, useEffect, useState } from 'react'
import { authedHrisFetch } from '../_shared'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ClipboardList, Sparkles, AlertTriangle, ShieldAlert } from 'lucide-react'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const SERIF = 'Georgia, "Book Antiqua", serif'

interface Row {
  employee: string
  profile_id?: string
  date: string
  productive_hours: number
  band?: string
  explained: boolean
  explanation: string
  reason: string
  status: string
  flagged: boolean
  flag_terms: string[]
  auto_action: string
  decided?: boolean
  review_note?: string
}
interface AriaPerson { n: number; summary: string; flag: string }
interface Aria {
  ok: boolean
  reason?: string
  items?: { people: AriaPerson[]; overall: string[] } | null
  text?: string
}
interface Resp {
  me: { name: string; email: string }
  date: string | null
  snapshot: boolean
  low_hours: number
  rows: Row[]
  aria: Aria
}

const REASON_LABEL: Record<string, string> = {
  none: '—', on_leave: 'On leave', external_meeting: 'External meeting',
  client_visit: 'Client visit', other: 'Other',
}

const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  'not accepted':   { bg: '#FEECEC', fg: '#B42318' },
  'no explanation': { bg: '#FEF3E2', fg: '#8A5A00' },
  'appealed':       { bg: '#FFF1E4', fg: '#B45309' },
  'explained':      { bg: '#EEF1F7', fg: NAVY },
  'on leave':       { bg: '#E7F6EC', fg: '#137333' },
  'accepted':       { bg: '#E7F6EC', fg: '#137333' },
  'rejected':       { bg: '#FEECEC', fg: '#B42318' },
}

function StatusChip({ status }: { status: string }) {
  const s = STATUS_STYLE[status] || { bg: '#EEF0F3', fg: NAVY }
  return (
    <span className="inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold whitespace-nowrap"
      style={{ background: s.bg, color: s.fg }}>
      {status}
    </span>
  )
}

function FlagChip({ flag }: { flag: string }) {
  const map: Record<string, string> = {
    weak: '#B42318', vague: '#8A5A00', repeated: '#B45309', ok: '#137333',
  }
  return (
    <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
      style={{ color: map[flag] || NAVY, background: '#F5F6F8' }}>
      {flag}
    </span>
  )
}

export default function LeaveExcusePage() {
  const [data, setData] = useState<Resp | null>(null)
  const [date, setDate] = useState('')          // '' = let the server pick the latest day
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [acting, setActing] = useState<string>('')   // "profileId:action" in flight
  const [toast, setToast] = useState('')

  const load = useCallback(async (d: string) => {
    setLoading(true); setErr('')
    try {
      const qs = d ? `?date=${encodeURIComponent(d)}` : ''
      const r = await authedHrisFetch(`/hris/api/leave-excuse/${qs}`)
      if (r.status === 403) throw new Error('Restricted to the CFO, exec and HR.')
      if (!r.ok) throw new Error(`Could not load (${r.status}).`)
      const json: Resp = await r.json()
      setData(json)
      if (!d && json.date) setDate(json.date)     // sync the picker to the latest day
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load the dashboard.')
    } finally {
      setLoading(false)
    }
  }, [])

  // Honour ?date=YYYY-MM-DD from the URL (e.g. a link to a specific Saturday);
  // otherwise let the server pick the latest tracked day.
  useEffect(() => {
    const q = typeof window !== 'undefined'
      ? (new URLSearchParams(window.location.search).get('date') || '').trim() : ''
    setDate(q)
    load(q)
  }, [load])

  const decide = async (profileId: string, d: string, action: 'accept' | 'reject_more' | 'reject_leave') => {
    let note = ''
    if (action !== 'accept') {
      note = (window.prompt(action === 'reject_leave'
        ? 'Reject and deduct this day as annual leave. Note (the employee sees it):'
        : 'Send back for a better explanation. What do you need from them?') || '').trim()
      if (!note) return
    }
    setActing(`${profileId}:${action}`)
    setToast('')
    try {
      const r = await authedHrisFetch('/hris/api/leave-excuse/decide/', {
        method: 'POST',
        body: JSON.stringify({ profile_id: profileId, date: d, action, note }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || `Failed (${r.status}).`)
      setToast(j.detail || 'Done.')
      await load(date)
    } catch (e) {
      setToast(e instanceof Error ? e.message : 'Could not save.')
    } finally { setActing('') }
  }

  const rows = data?.rows ?? []
  const notAccepted = rows.filter(r => r.status === 'not accepted').length
  const flagged = rows.filter(r => r.flagged).length
  const noExplanation = rows.filter(r => r.status === 'no explanation').length
  const aria = data?.aria

  return (
    <>
      <TopBar title="Leave Excuse Response" />
      <div className="p-6 space-y-6 max-w-[1200px]">

        <p className="text-sm text-[#6B7280] max-w-3xl">
          Tracking-eligible staff whose <strong>productive hours</strong> were low
          (&lt; {data?.low_hours ?? 4}h) or none for the day, with their own-words
          explanation. A power cut at home, or &ldquo;Time Doctor was down&rdquo;
          with no IT ticket, is <strong style={{ color: ORANGE }}>not accepted</strong>.
          Decide each one: <strong>Accept</strong>, send back for a
          <strong> better explanation</strong>, or <strong>reject &amp; deduct as leave</strong>.
        </p>
        {toast && (
          <div className="text-sm rounded-lg px-3 py-2" style={{ background: '#EEF1F7', color: NAVY }}>{toast}</div>
        )}

        {/* day picker */}
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-xs uppercase tracking-wide text-[#6B7280]">Day</label>
          <input type="date" value={date} onChange={e => setDate(e.target.value)}
            className="px-3 py-1.5 rounded-lg text-sm border border-[#E3E6EA] outline-none focus:border-[#F47C20]" />
          <button onClick={() => load(date)}
            className="px-3 py-1.5 rounded-lg text-sm font-semibold text-white"
            style={{ background: NAVY }}>
            View
          </button>
          {data && !data.snapshot && (
            <span className="text-xs text-[#8A5A00] inline-flex items-center gap-1">
              <AlertTriangle size={13} /> No Time Doctor snapshot for this day.
            </span>
          )}
        </div>

        {loading && <div className="text-[#6B7280] text-sm">Loading…</div>}
        {err && <div className="text-red-600 text-sm">{err}</div>}

        {data && !loading && !err && (
          <>
            {/* summary tiles */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Tile label="People flagged" value={String(rows.length)} sub="low / no productive hours" />
              <Tile label="Not accepted" value={String(notAccepted)} sub="power cut / no IT ticket" accent />
              <Tile label="No explanation" value={String(noExplanation)} sub="nothing said yet" />
              <Tile label="Watch-list flags" value={String(flagged)} sub={'"power cut at home" &c.'} />
            </div>

            {/* Aria analysis card */}
            <Card style={{ borderColor: ORANGE, borderTopWidth: 3 }}>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2" style={{ color: NAVY }}>
                  <Sparkles size={15} style={{ color: ORANGE }} /> Aria analysis
                </CardTitle>
              </CardHeader>
              <CardContent>
                {aria?.ok && aria.items ? (
                  <div className="space-y-3">
                    {aria.items.overall.length > 0 && (
                      <ul className="list-disc pl-5 space-y-1 text-sm text-[#374151]">
                        {aria.items.overall.map((b, i) => <li key={i}>{b}</li>)}
                      </ul>
                    )}
                    {aria.items.people.length > 0 && (
                      <div className="mt-2 space-y-1">
                        {aria.items.people.map(p => (
                          <div key={p.n} className="flex items-start gap-2 text-sm">
                            <span className="text-[#9CA3AF] w-14 shrink-0">Person {p.n}</span>
                            <FlagChip flag={p.flag} />
                            <span className="text-[#374151]">{p.summary}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-[#9CA3AF]">
                    {rows.length === 0
                      ? 'Nothing to analyse for this day.'
                      : `Aria could not run right now${aria?.reason ? ` (${aria.reason})` : ''}.`}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* the table */}
            <Card>
              <CardContent className="pt-4 overflow-x-auto">
                {rows.length === 0 ? (
                  <div className="text-[#6B7280] text-sm py-6 text-center">
                    No low or no-productive-hours staff for this day. Sharp sharp.
                  </div>
                ) : (
                  <table className="w-full text-sm border-collapse table-fixed">
                    <thead>
                      <tr className="text-left" style={{ color: '#fff' }}>
                        {['Name', 'Date', 'Productive hrs', 'Explained', 'Explanation', 'Status', 'Decision'].map(h => (
                          <th key={h} className="px-3 py-2 font-semibold" style={{ background: NAVY }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={r.profile_id || i} className="border-b border-[#EEF0F3] align-top">
                          <td className="px-3 py-2 font-semibold" style={{ color: NAVY, fontFamily: SERIF }}>
                            {r.employee || '—'}
                            {r.flagged && (
                              <span className="ml-2 inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded"
                                style={{ background: '#FEECEC', color: '#B42318' }}>
                                <ShieldAlert size={10} /> {r.flag_terms.join(', ')}
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-[#6B7280] whitespace-nowrap">
                            {new Date(r.date).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
                          </td>
                          <td className="px-3 py-2 whitespace-nowrap font-semibold"
                            style={{ color: r.band === 'no' ? '#B42318' : ORANGE, fontFamily: SERIF }}>
                            {r.productive_hours.toFixed(2)}h
                          </td>
                          <td className="px-3 py-2">{r.explained ? 'Yes' : 'No'}</td>
                          <td className="px-3 py-2 text-[#374151] max-w-[360px] break-words">
                            {r.explanation
                              ? <span className="whitespace-pre-wrap">&ldquo;{r.explanation}&rdquo;</span>
                              : <span className="text-[#9CA3AF] italic">{REASON_LABEL[r.reason] || 'No explanation'}</span>}
                            {r.auto_action && (
                              <div className="text-[11px] mt-1 font-semibold" style={{ color: '#B42318' }}>
                                auto: {r.auto_action}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2"><StatusChip status={r.status} /></td>
                          <td className="px-3 py-2 whitespace-nowrap w-[132px]">
                            {r.decided ? (
                              <span className="text-xs text-[#6B7280]">
                                {r.status === 'accepted' ? '✓ accepted' : '✕ rejected'}
                                {r.review_note ? <span className="block italic text-[#9CA3AF] max-w-[220px] truncate">{r.review_note}</span> : null}
                              </span>
                            ) : (
                              <div className="flex flex-col gap-1 items-stretch">
                                <button disabled={!!acting} onClick={() => decide(r.profile_id || '', r.date, 'accept')}
                                  className="w-full text-xs font-semibold px-2 py-1 rounded-md text-white disabled:opacity-50"
                                  style={{ background: '#137333' }}>Accept</button>
                                <button disabled={!!acting} onClick={() => decide(r.profile_id || '', r.date, 'reject_more')}
                                  className="w-full text-xs font-semibold px-2 py-1 rounded-md disabled:opacity-50"
                                  style={{ background: '#FFF1E4', color: '#B45309' }}>Need more</button>
                                <button disabled={!!acting} onClick={() => decide(r.profile_id || '', r.date, 'reject_leave')}
                                  className="w-full text-xs font-semibold px-2 py-1 rounded-md disabled:opacity-50"
                                  style={{ background: '#FEECEC', color: '#B42318' }}>Reject → leave</button>
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </>
  )
}

function Tile({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="text-[#6B7280] text-xs uppercase tracking-wide">{label}</div>
        <div className="text-2xl font-bold mt-1" style={{ color: accent ? ORANGE : NAVY, fontFamily: SERIF }}>{value}</div>
        {sub && <div className="text-xs text-[#9CA3AF] mt-0.5">{sub}</div>}
      </CardContent>
    </Card>
  )
}
