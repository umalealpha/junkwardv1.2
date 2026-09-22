'use client'

/**
 * /hris/excuses — CFO "Excuses feed" (Leave & Productive-Hours Accountability,
 * CFO 2026-07-21). Read-only view over the explanations staff give for missed
 * productive hours: each person's reason in their own words, the watch-list
 * auto-flag ("power cut at home"), a most-used-words panel, and a per-person
 * filter to read one employee's whole run before a conversation. Deducts and
 * changes nothing — it is a report. Gated server-side to CFO / exec / HR.
 */
import { useEffect, useMemo, useState } from 'react'
import { getExcusesFeed } from '@/lib/api'
import type { ExcusesFeed, ExcuseRow } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { MessageSquareWarning, AlertTriangle, Filter, X, Search, Clock } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = 'Georgia, "Book Antiqua", serif'

const REASON_LABEL: Record<string, string> = {
  none: 'No reason given', on_leave: 'On leave', external_meeting: 'External meeting',
  client_visit: 'Client visit', other: 'Other',
}
const STATUS_LABEL: Record<string, string> = {
  explained: 'Awaiting manager', justified: 'Accepted', unjustified: 'Not explained',
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

export default function ExcusesFeedPage() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<ExcusesFeed | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [person, setPerson] = useState<string>('')      // profile_id filter
  const [personName, setPersonName] = useState<string>('')
  const [q, setQ] = useState('')                         // free-text search of explanations

  useEffect(() => {
    let alive = true
    setLoading(true); setErr('')
    getExcusesFeed(days, person || undefined)
      .then(d => { if (alive) { setData(d); setLoading(false) } })
      .catch((e: unknown) => { if (alive) { setErr(e instanceof Error ? e.message : 'Could not load the feed.'); setLoading(false) } })
    return () => { alive = false }
  }, [days, person])

  const rows = useMemo(() => {
    const items = data?.items ?? []
    if (!q.trim()) return items
    const needle = q.trim().toLowerCase()
    return items.filter(r =>
      (r.explanation || '').toLowerCase().includes(needle) ||
      (r.employee || '').toLowerCase().includes(needle))
  }, [data, q])

  const flagged = rows.filter(r => r.flagged)

  return (
    <>
      <TopBar title="Excuses feed" />
      <div className="p-6 space-y-6 max-w-[1200px]">

        {/* warm-up banner */}
        {data && !data.enforcement_active && (
          <div className="rounded-xl px-4 py-3 text-sm flex items-center gap-2"
               style={{ background: '#FFF7E6', color: '#8A5A00', border: '1px solid #F4A62333' }}>
            <Clock size={16} />
            Warm-up mode — the system is watching and collecting explanations, but <strong>no leave or pay is deducted</strong>.
            Enforcement starts {new Date(data.strict_from).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}.
          </div>
        )}

        {/* window selector */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs uppercase tracking-wide text-[#6B7280]">Window</span>
          {[7, 14, 30, 90].map(d => (
            <button key={d} onClick={() => setDays(d)}
              className="px-3 py-1 rounded-full text-sm transition-colors"
              style={days === d ? { background: NAVY, color: '#fff' } : { background: '#EEF0F3', color: NAVY }}>
              {d} days
            </button>
          ))}
          <div className="ml-auto relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#9CA3AF]" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search words or a name…"
              className="pl-8 pr-3 py-1.5 rounded-lg text-sm border border-[#E3E6EA] w-64 outline-none focus:border-[#F4A623]" />
          </div>
        </div>

        {loading && <div className="text-[#6B7280] text-sm">Loading…</div>}
        {err && <div className="text-red-600 text-sm">{err}</div>}

        {data && !loading && (
          <>
            {/* summary tiles */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Tile label="Explanations" value={String(data.total)} sub={`last ${data.window_days} days`} />
              <Tile label="Watch-list flags" value={String(data.flagged_count)} sub={'"power cut at home" &c.'} accent />
              <Tile label="Not explained" value={String((data.by_reason?.none ?? 0) + (data.items.filter(i => i.status === 'unjustified').length))} sub="short, no accepted reason" />
              <Tile label="People" value={String(data.top_people.length)} sub="with a short day" />
            </div>

            {/* person filter chip */}
            {person && (
              <div className="flex items-center gap-2 text-sm">
                <Filter size={14} className="text-[#6B7280]" />
                <span className="px-3 py-1 rounded-full inline-flex items-center gap-2" style={{ background: NAVY, color: '#fff' }}>
                  {personName || 'Selected person'}
                  <button onClick={() => { setPerson(''); setPersonName('') }} className="opacity-80 hover:opacity-100"><X size={13} /></button>
                </span>
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* left: side panels */}
              <div className="space-y-6">
                <Card>
                  <CardHeader className="pb-2"><CardTitle className="text-sm" style={{ color: NAVY }}>Most-used words</CardTitle></CardHeader>
                  <CardContent>
                    {data.top_words.length === 0 && <div className="text-xs text-[#9CA3AF]">No explanations yet.</div>}
                    <div className="flex flex-wrap gap-1.5">
                      {data.top_words.map(([w, n]) => (
                        <span key={w} className="px-2 py-0.5 rounded-full text-xs"
                          style={{ background: '#EEF0F3', color: NAVY, fontSize: `${Math.min(15, 10 + n)}px` }}>
                          {w} <span className="text-[#9CA3AF]">{n}</span>
                        </span>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2"><CardTitle className="text-sm" style={{ color: NAVY }}>Most short days</CardTitle></CardHeader>
                  <CardContent className="space-y-1">
                    {data.top_people.map(([name, n]) => (
                      <button key={name} onClick={() => { setPersonName(name); const r = data.items.find(i => i.employee === name); setPerson(r?.profile_id || '') }}
                        className="w-full flex items-center justify-between text-sm px-2 py-1 rounded-lg hover:bg-[#F5F6F8] transition-colors">
                        <span style={{ color: NAVY }}>{name}</span>
                        <span className="text-[#6B7280] text-xs">{n}</span>
                      </button>
                    ))}
                  </CardContent>
                </Card>
              </div>

              {/* right: the feed */}
              <div className="lg:col-span-2 space-y-3">
                {flagged.length > 0 && (
                  <div className="rounded-xl px-4 py-2 text-sm flex items-center gap-2"
                       style={{ background: '#FEECEC', color: '#B42318', border: '1px solid #F0433322' }}>
                    <AlertTriangle size={15} />
                    {flagged.length} flagged excuse{flagged.length > 1 ? 's' : ''} — the rule is <em>come to the office or find a way to work</em>.
                  </div>
                )}
                {rows.length === 0 && <div className="text-[#6B7280] text-sm">Nothing to show for this window.</div>}
                {rows.map(r => <ExcuseCard key={r.id} r={r} />)}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  )
}

function ExcuseCard({ r }: { r: ExcuseRow }) {
  const dt = new Date(r.work_date).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
  return (
    <Card style={r.flagged ? { borderColor: '#F04333', borderWidth: 1 } : undefined}>
      <CardContent className="pt-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="font-semibold" style={{ color: NAVY, fontFamily: SERIF }}>{r.employee || '—'}</div>
            <div className="text-xs text-[#6B7280]">{dt} · {REASON_LABEL[r.reason] || r.reason} · {STATUS_LABEL[r.status] || r.status}</div>
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold" style={{ color: ORANGE, fontFamily: SERIF }}>{r.shortfall_hours}h short</div>
            <div className="text-[10px] text-[#9CA3AF]">would be {r.would_be_leave_hours}h leave</div>
          </div>
        </div>
        {r.explanation
          ? <p className="text-sm mt-2 text-[#374151] whitespace-pre-wrap">&ldquo;{r.explanation}&rdquo;</p>
          : <p className="text-sm mt-2 text-[#9CA3AF] italic">No explanation given.</p>}
        {r.flagged && (
          <div className="mt-2 inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full" style={{ background: '#FEECEC', color: '#B42318' }}>
            <MessageSquareWarning size={12} /> flagged: {r.flag_terms.join(', ')}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
