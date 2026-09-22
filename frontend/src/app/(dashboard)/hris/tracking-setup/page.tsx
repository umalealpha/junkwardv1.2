'use client'

/**
 * /hris/tracking-setup — "Who tracks" control panel (CFO 2026-07-14).
 * The CFO / Arun / Arjun confirm each Time Doctor account ↔ staff match ONCE
 * (remembered permanently) and click Track / Don't-track per person. This is
 * what the whole Workforce Brief reports on, so it kills the false-flag problem
 * at the source. Alpha Direct house style — navy #0D1B2A + orange #F4A623.
 */
import { useEffect, useMemo, useState } from 'react'
import { getTrackingSetup, setTracking, confirmTdMatch } from '@/lib/api'
import type { TrackingSetup, TrackingRow, UnmatchedTd } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Users, CheckCircle2, AlertCircle, Search } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = 'Georgia, "Book Antiqua", serif'

export default function TrackingSetupPage() {
  const [data, setData] = useState<TrackingSetup | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  async function load() {
    try { setData(await getTrackingSetup()) }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to load — this page is for the CFO, Arun, Arjun and the HR team.') }
  }
  useEffect(() => { load() }, [])

  async function toggle(row: TrackingRow) {
    setBusy(row.employee_id)
    try {
      const r = await setTracking(row.employee_id, !row.expected)
      setData(d => d ? { ...d, items: d.items.map(i => i.employee_id === row.employee_id
        ? { ...i, expected: r.expected_to_track, source: 'directive' } : i) } : d)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save') }
    finally { setBusy(null) }
  }

  async function link(row: TrackingRow, td: UnmatchedTd) {
    setBusy(row.employee_id)
    try {
      await confirmTdMatch(row.employee_id, td.td_user_id, td.td_name)
      setData(d => d ? {
        ...d,
        items: d.items.map(i => i.employee_id === row.employee_id
          ? { ...i, matched_td: td.td_name, td_user_id: td.td_user_id, confirmed: true } : i),
        unmatched_td: d.unmatched_td.filter(u => u.td_user_id !== td.td_user_id),
      } : d)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not link') }
    finally { setBusy(null) }
  }

  async function confirm(row: TrackingRow) {
    if (!row.td_user_id) return
    setBusy(row.employee_id)
    try {
      await confirmTdMatch(row.employee_id, row.td_user_id, row.matched_td || undefined)
      setData(d => d ? { ...d, items: d.items.map(i => i.employee_id === row.employee_id ? { ...i, confirmed: true } : i) } : d)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not confirm') }
    finally { setBusy(null) }
  }

  const items = data?.items ?? []
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase()
    return n ? items.filter(i => (i.name + i.department + i.job_title).toLowerCase().includes(n)) : items
  }, [items, q])
  const tracked = items.filter(i => i.expected).length

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      <TopBar title="Who tracks" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Time & Pay', href: '/hris/time-doctor' }, { label: 'Who tracks' }]} />
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ background: NAVY }}>
            <Users className="w-5 h-5" style={{ color: ORANGE }} />
          </div>
          <div>
            <h1 className="text-2xl font-bold" style={{ color: NAVY, fontFamily: SERIF }}>Who tracks time</h1>
            <p className="text-sm text-[#6B7280]">Confirm each person&rsquo;s Time Doctor match once, and set who is expected to track. This is what the daily brief reports on.</p>
          </div>
        </div>

        {error && <div className="mb-3 text-sm text-[#B91C1C] flex items-center gap-2"><AlertCircle className="w-4 h-4" />{error}</div>}
        {data && !data.configured && (
          <div className="mb-3 text-sm rounded-lg px-4 py-3" style={{ background: '#FFF7E8', border: '1px solid #F3E4C4', color: NAVY }}>
            Time Doctor isn&rsquo;t connected yet, so matches can&rsquo;t be shown — you can still set who is expected to track.
          </div>
        )}

        <div className="flex items-center gap-2 mb-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-[#9CA3AF]" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name / department"
              className="w-full rounded-lg border pl-9 pr-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
          </div>
          <div className="ml-auto text-sm text-[#6B7280]">{tracked} of {items.length} expected to track</div>
        </div>

        <Card>
          <CardContent className="p-0">
            <div className="divide-y" style={{ borderColor: '#EEF0F3' }}>
              {shown.map(row => (
                <div key={row.employee_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <div className="flex-1 min-w-[200px]">
                    <div className="font-semibold text-sm" style={{ color: NAVY }}>{row.name}</div>
                    <div className="text-xs text-[#6B7280]">{row.job_title || '—'}{row.department ? ` · ${row.department}` : ''}</div>
                  </div>

                  <div className="min-w-[190px] text-xs">
                    {row.matched_td ? (
                      <span className="inline-flex items-center gap-1" style={{ color: row.confirmed ? '#059669' : '#B45309' }}>
                        {row.confirmed ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                        TD: {row.matched_td}{row.confirmed ? ' (confirmed)' : ' (auto)'}
                      </span>
                    ) : (
                      <span className="text-[#9CA3AF]">No Time Doctor account matched</span>
                    )}
                    {row.matched_td && !row.confirmed && (
                      <button onClick={() => confirm(row)} disabled={busy === row.employee_id}
                        className="ml-2 underline" style={{ color: NAVY }}>Confirm</button>
                    )}
                    {!row.matched_td && data && data.unmatched_td.length > 0 && (
                      <select disabled={busy === row.employee_id} defaultValue=""
                        onChange={e => { const td = data.unmatched_td.find(u => u.td_user_id === e.target.value); if (td) link(row, td) }}
                        className="ml-2 rounded border px-1 py-0.5 text-xs" style={{ borderColor: '#E5E7EB' }}>
                        <option value="" disabled>Link a TD account…</option>
                        {data.unmatched_td.map(u => <option key={u.td_user_id} value={u.td_user_id}>{u.td_name || u.td_user_id}</option>)}
                      </select>
                    )}
                  </div>

                  {/* HR (CFO 2026-09-05) may open this panel and confirm links, but the
                      track / don't-track switch stays with the CFO, Arun and Arjun. */}
                  <button onClick={() => toggle(row)}
                    disabled={busy === row.employee_id || data?.can_toggle === false}
                    title={data?.can_toggle === false ? 'Only the CFO, Arun or Arjun can change who tracks' : undefined}
                    className="px-3 py-1.5 rounded-full text-xs font-semibold transition-colors disabled:cursor-not-allowed"
                    style={row.expected
                      ? { background: '#ECFDF5', color: '#059669', border: '1px solid #C7EBD9' }
                      : { background: '#F3F4F6', color: '#6B7280', border: '1px solid #E5E7EB' }}>
                    {row.expected ? '✓ Tracks' : 'Does not track'}
                  </button>
                </div>
              ))}
              {shown.length === 0 && <div className="px-4 py-8 text-center text-sm text-[#6B7280]">No matching staff.</div>}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
