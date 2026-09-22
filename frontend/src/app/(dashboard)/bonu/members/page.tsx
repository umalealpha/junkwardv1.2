'use client'

/**
 * /bonu/members — the membership roll, and the check before a claim is paid.
 *
 * CFO 2026-08-18: "We also need a place where I can put all the member details so we
 * only pay claims for members who pay premiums. It's very important."
 *
 * BONU has 9,000+ members and pays Alpha Direct two bulk payments a month, so paid-up
 * status cannot come from our cash — it comes from the union's own dated list. The whole
 * screen therefore leads with WHICH list is current, because every verdict hangs off it.
 *
 * Backend: /bonu/members/ (roll + summary) · /bonu/members/check/ (verdict)
 *          /bonu/members/upload/ (the union's list) · /bonu/members/<id>/ (hand fix)
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { Loader2, Search, Upload, ShieldCheck, ShieldAlert, ShieldQuestion } from 'lucide-react'
import { AMBER, BonuTabs, GREEN, LINE, NAVY, ORANGE, RED, Stat, Table, td, trBorder } from '../_shared'

interface Member {
  id: string; membership_no: string; full_name: string; station: string; district: string
  status: string; status_display: string; monthly_premium: string | null
  paid_up_to: string | null; joined_on: string | null; left_on: string | null
  on_current_list: boolean; last_seen_as_at: string | null; note: string
}
interface Summary {
  members_total: number; list_as_at: string | null; list_source: string
  on_current_list: number; not_on_current_list: number; by_status: Record<string, number>
}
interface Load {
  as_at: string; source_name: string; rows_seen: number; members_loaded: number
  is_current: boolean; loaded_by: string
}
interface RollResp { summary: Summary; total: number; page: number; page_size: number; rows: Member[]; loads: Load[] }
interface Verdict {
  verdict: 'allow' | 'refuse' | 'unknown'; reason: string; membership_no: string
  member: Member | null; list_as_at: string | null; on_date: string; spend_this_year: string | null
}

const VERDICT_LOOK = {
  allow: { colour: GREEN, Icon: ShieldCheck, word: 'May be paid' },
  refuse: { colour: RED, Icon: ShieldAlert, word: 'Do NOT pay' },
  unknown: { colour: AMBER, Icon: ShieldQuestion, word: 'Cannot tell' },
} as const

export default function BonuMembersPage() {
  const [data, setData] = useState<RollResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)

  const [checkRef, setCheckRef] = useState('')
  const [verdict, setVerdict] = useState<Verdict | null>(null)
  const [checking, setChecking] = useState(false)

  const [asAt, setAsAt] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ page: String(page) })
      if (q.trim()) params.set('q', q.trim())
      if (status) params.set('status', status)
      setData(await apiFetch<RollResp>(`/bonu/members/?${params}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the roll.')
    } finally {
      setLoading(false)
    }
  }, [q, status, page])

  useEffect(() => { void load() }, [load])

  const runCheck = async () => {
    if (!checkRef.trim()) return
    setChecking(true)
    setVerdict(null)
    try {
      setVerdict(await apiFetch<Verdict>(
        `/bonu/members/check/?membership_no=${encodeURIComponent(checkRef.trim())}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Check failed.')
    } finally {
      setChecking(false)
    }
  }

  const upload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) { setUploadMsg('Choose the union’s file first.'); return }
    if (!asAt) { setUploadMsg('Give the date the list speaks as of.'); return }
    setUploading(true)
    setUploadMsg('')
    try {
      const body = new FormData()
      body.append('file', file)
      body.append('as_at', asAt)
      const res = await apiFetch<{ members_loaded: number; rows_seen: number }>(
        '/bonu/members/upload/', { method: 'POST', body })
      setUploadMsg(`Loaded ${res.members_loaded.toLocaleString()} members from ${res.rows_seen.toLocaleString()} rows.`)
      if (fileRef.current) fileRef.current.value = ''
      await load()
    } catch (e) {
      setUploadMsg(e instanceof Error ? e.message : 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  const s = data?.summary
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <>
      <TopBar title="BONU — members" />
      <div className="space-y-4 p-4">
        <BonuTabs active="/bonu/members" />

        {/* Which list is current is the first thing to know: every verdict hangs off it. */}
        {s && !s.list_as_at && (
          <div className="rounded-md px-4 py-3 text-[13px]"
               style={{ background: '#FEF3C7', color: '#7C2D12' }}>
            <strong>No membership list has been loaded yet.</strong> Until the union&rsquo;s list is
            here, nothing can confirm that a member pays premiums, so every check returns
            &ldquo;cannot tell&rdquo; rather than a yes or a no.
          </div>
        )}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Members on the roll" value={(s?.members_total ?? 0).toLocaleString()} />
          <Stat label="On the current list" value={(s?.on_current_list ?? 0).toLocaleString()}
                tone={GREEN} />
          <Stat label="Dropped off the list" value={(s?.not_on_current_list ?? 0).toLocaleString()}
                tone={(s?.not_on_current_list ?? 0) > 0 ? RED : undefined}
                sub="Do not pay these without asking the union" />
          <Stat label="List dated" value={s?.list_as_at ?? "—"} sub={s?.list_source || ""} />
        </div>

        {/* The daily question, answered in one box. */}
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="text-[13px] font-semibold" style={{ color: NAVY }}>
              Before you pay a claim — check the member
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={checkRef}
                onChange={(e) => setCheckRef(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void runCheck() }}
                placeholder="Membership number"
                className="rounded border px-3 py-2 text-[13px]"
                style={{ borderColor: LINE, minWidth: 220 }}
              />
              <button
                onClick={() => void runCheck()}
                disabled={checking}
                className="flex items-center gap-2 rounded px-4 py-2 text-[13px] font-medium text-white disabled:opacity-50"
                style={{ background: NAVY }}
              >
                {checking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                Check
              </button>
            </div>
            {verdict && (() => {
              const look = VERDICT_LOOK[verdict.verdict]
              return (
                <div className="rounded-md border p-3" style={{ borderColor: look.colour }}>
                  <div className="flex items-center gap-2 text-[13px] font-semibold"
                       style={{ color: look.colour }}>
                    <look.Icon className="h-4 w-4" />
                    {look.word}
                    {verdict.member?.full_name ? ` — ${verdict.member.full_name}` : ''}
                  </div>
                  <div className="mt-1 text-[13px]" style={{ color: '#374151' }}>{verdict.reason}</div>
                  {verdict.spend_this_year && (
                    <div className="mt-1 text-[12px]" style={{ color: '#6B7280' }}>
                      Legal benefit used this year: P{Number(verdict.spend_this_year).toLocaleString()}
                    </div>
                  )}
                  <div className="mt-2 text-[11px]" style={{ color: '#6B7280' }}>
                    This is advice on the record, not a block. The decision to pay stays with the
                    person paying.
                  </div>
                </div>
              )
            })()}
          </CardContent>
        </Card>

        {/* Loading the union's monthly list. */}
        <Card>
          <CardContent className="space-y-2 p-4">
            <div className="text-[13px] font-semibold" style={{ color: NAVY }}>
              Load the union&rsquo;s membership list
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input aria-label="Membership list file to upload"
                     ref={fileRef} type="file" accept=".xlsx,.xlsm,.xls,.csv"
                     className="text-[13px]" />
              <label className="text-[12px]" style={{ color: '#6B7280' }}>
                List speaks as of
                <input type="date" value={asAt} onChange={(e) => setAsAt(e.target.value)}
                       className="ml-2 rounded border px-2 py-1 text-[13px]"
                       style={{ borderColor: LINE }} />
              </label>
              <button
                onClick={() => void upload()}
                disabled={uploading}
                className="flex items-center gap-2 rounded px-4 py-2 text-[13px] font-medium text-white disabled:opacity-50"
                style={{ background: ORANGE }}
              >
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                Load list
              </button>
            </div>
            <div className="text-[11px]" style={{ color: '#6B7280' }}>
              Members missing from a new list are kept, not deleted — &ldquo;was on July&rsquo;s list,
              not on August&rsquo;s&rdquo; is what makes a refusal provable.
            </div>
            {uploadMsg && <div className="text-[13px]" style={{ color: NAVY }}>{uploadMsg}</div>}
          </CardContent>
        </Card>

        {/* The roll itself. */}
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={q}
                onChange={(e) => { setQ(e.target.value); setPage(1) }}
                placeholder="Search name, number, station or district"
                className="flex-1 rounded border px-3 py-2 text-[13px]"
                style={{ borderColor: LINE, minWidth: 240 }}
              />
              <select aria-label="Filter by membership status"
                      value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}
                      className="rounded border px-2 py-2 text-[13px]" style={{ borderColor: LINE }}>
                <option value="">Every status</option>
                <option value="active">Active — paid up</option>
                <option value="arrears">In arrears</option>
                <option value="suspended">Suspended</option>
                <option value="resigned">Resigned</option>
                <option value="unknown">Status not stated</option>
              </select>
            </div>

            {error && <div className="text-[13px]" style={{ color: RED }}>{error}</div>}
            {loading ? (
              <div className="flex items-center gap-2 py-6 text-[13px]" style={{ color: '#6B7280' }}>
                <Loader2 className="h-4 w-4 animate-spin" /> Loading the roll…
              </div>
            ) : (
              <>
                <Table head={['Membership no', 'Name', 'Station', 'District', 'Status',
                              'Paid up to', 'On current list']}>
                  {(data?.rows ?? []).map((m) => (
                    <tr key={m.id} style={trBorder}>
                      <td className={td}>{m.membership_no}</td>
                      <td className={td}>{m.full_name || '—'}</td>
                      <td className={td}>{m.station || '—'}</td>
                      <td className={td}>{m.district || '—'}</td>
                      <td className={td} style={{
                        color: m.status === 'active' ? GREEN
                             : m.status === 'unknown' ? AMBER : RED,
                      }}>{m.status_display}</td>
                      <td className={td}>{m.paid_up_to ?? '—'}</td>
                      <td className={td} style={{ color: m.on_current_list ? GREEN : RED }}>
                        {m.on_current_list ? 'Yes' : `No — last seen ${m.last_seen_as_at ?? 'never'}`}
                      </td>
                    </tr>
                  ))}
                  {(data?.rows ?? []).length === 0 && (
                    <tr><td className={td} colSpan={7} style={{ color: '#6B7280' }}>
                      Nothing on the roll yet. Load the union&rsquo;s list above.
                    </td></tr>
                  )}
                </Table>
                {pages > 1 && (
                  <div className="flex items-center gap-3 text-[13px]">
                    <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}
                            className="rounded border px-3 py-1 disabled:opacity-40"
                            style={{ borderColor: LINE }}>Back</button>
                    <span style={{ color: '#6B7280' }}>
                      Page {page} of {pages} — {(data?.total ?? 0).toLocaleString()} members
                    </span>
                    <button onClick={() => setPage((p) => Math.min(pages, p + 1))}
                            disabled={page >= pages}
                            className="rounded border px-3 py-1 disabled:opacity-40"
                            style={{ borderColor: LINE }}>Next</button>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>

        {/* Which lists have been loaded — the evidence trail behind every verdict. */}
        {(data?.loads ?? []).length > 0 && (
          <Card>
            <CardContent className="p-4">
              <div className="mb-2 text-[13px] font-semibold" style={{ color: NAVY }}>
                Lists the union has sent
              </div>
              <Table head={['As at', 'File', 'Rows', 'Members loaded', 'Current', 'Loaded by']}>
                {(data?.loads ?? []).map((l) => (
                  <tr key={`${l.as_at}-${l.source_name}`} style={trBorder}>
                    <td className={td}>{l.as_at}</td>
                    <td className={td}>{l.source_name || '—'}</td>
                    <td className={td}>{l.rows_seen.toLocaleString()}</td>
                    <td className={td}>{l.members_loaded.toLocaleString()}</td>
                    <td className={td} style={{ color: l.is_current ? GREEN : '#6B7280' }}>
                      {l.is_current ? 'Yes' : 'Superseded'}
                    </td>
                    <td className={td}>{l.loaded_by || '—'}</td>
                  </tr>
                ))}
              </Table>
            </CardContent>
          </Card>
        )}
      </div>
    </>
  )
}
