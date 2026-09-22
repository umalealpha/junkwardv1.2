'use client'

/**
 * /banking/realpay/failed-debits — the weekly chase list, and who it goes to.
 *
 * CFO 2026-09-11. Every week an Accounts Assistant exported the failed debit
 * orders by hand, pasted them into a spreadsheet and emailed Underwriting and
 * the account handlers. Omni now sends that email itself, every Monday at
 * 07:30, and this is where Finance keep the address list — so adding somebody
 * is a job for Rose or Keetile, not for a developer.
 *
 * Read order is deliberate. The recipient list is FIRST, because that is the
 * thing somebody came to this page to change. The preview is second, so they
 * can see what those people will actually receive before Monday.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  addReportRecipient, getFailedDebitsPreview, listReportRecipients,
  setReportRecipientActive,
} from '@/lib/api'
import type {
  FailedDebitsPreview, ReportRecipientList, ReportRecipientRow,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertTriangle, CheckCircle2, Loader2, Mail, Plus, RefreshCw, Undo2, X,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const RED = '#B91C1C'
const AMBER = '#B45309'

const WEEKLY_SLUG = 'failed-debits-weekly'

function money(v: unknown): string {
  const n = Number(v ?? 0)
  if (!Number.isFinite(n)) return '—'
  return `P ${n.toLocaleString('en-BW', { minimumFractionDigits: 2,
                                          maximumFractionDigits: 2 })}`
}

export default function FailedDebitsPage() {
  const [slug, setSlug] = useState(WEEKLY_SLUG)
  const [list, setList] = useState<ReportRecipientList | null>(null)
  const [preview, setPreview] = useState<FailedDebitsPreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'to' | 'cc'>('to')

  const loadList = useCallback(async (which: string) => {
    setList(await listReportRecipients(which))
  }, [])

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [l, p] = await Promise.all([
        listReportRecipients(slug),
        getFailedDebitsPreview(),
      ])
      setList(l)
      setPreview(p)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load this page.')
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => { void loadAll() }, [loadAll])

  async function add() {
    if (!email.trim()) return
    setSaving(true)
    setError('')
    try {
      await addReportRecipient(slug, email.trim(), name.trim(), kind)
      setEmail('')
      setName('')
      await loadList(slug)
    } catch (e) {
      // The message from the server is the useful one ("… is not a valid email
      // address"), so it is shown as-is rather than replaced with a generic.
      setError(e instanceof Error ? e.message : 'Could not add that address.')
    } finally {
      setSaving(false)
    }
  }

  async function toggle(row: ReportRecipientRow) {
    setSaving(true)
    try {
      await setReportRecipientActive(row.id, !row.active)
      await loadList(slug)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not change that.')
    } finally {
      setSaving(false)
    }
  }

  const rows = list?.results ?? []
  const liveTo = rows.filter(r => r.active && r.kind === 'to')
  const nobodyAddressed = liveTo.length === 0

  return (
    <>
      <TopBar title="Failed debits" />
      <div className="p-6 space-y-6 max-w-6xl">

        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold" style={{ color: NAVY }}>
              Failed debit orders
            </h1>
            <p className="text-sm text-slate-600 mt-1 max-w-3xl">
              Omni emails this list every <strong>Monday at 07:30</strong>, read
              live from Graphite. Commercial and domestic policies only. It does
              not move money and it does not change a policy.
            </p>
          </div>
          <Button variant="outline" onClick={() => void loadAll()} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" />
                     : <RefreshCw className="h-4 w-4" />}
            <span className="ml-2">Refresh</span>
          </Button>
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-md border p-3 text-sm"
               style={{ borderColor: RED, color: RED, background: '#FEF2F2' }}>
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* ── Who it goes to ─────────────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2" style={{ color: NAVY }}>
              <Mail className="h-5 w-5" style={{ color: ORANGE }} />
              Who gets this email
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">

            {(list?.reports?.length ?? 0) > 1 && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-slate-600">Report:</span>
                <select
                  aria-label="Which report's recipient list to edit"
                  className="border rounded-md px-2 py-1.5 text-sm"
                  value={slug}
                  onChange={e => setSlug(e.target.value)}
                >
                  {list?.reports.map(r => (
                    <option key={r.slug} value={r.slug}>{r.label}</option>
                  ))}
                </select>
              </div>
            )}

            {nobodyAddressed && (
              // Said plainly, because an empty list is the one state where the
              // Monday job deliberately sends nothing at all.
              <div className="flex items-start gap-2 rounded-md border p-3 text-sm"
                   style={{ borderColor: AMBER, color: AMBER, background: '#FFFBEB' }}>
                <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                <span>
                  {slug === WEEKLY_SLUG ? (
                    <>Nobody is addressed on this list yet, so nothing will be
                    sent on Monday. Add at least one person under{' '}
                    <strong>Send to</strong>.</>
                  ) : (
                    <>Nobody is addressed on this list, so this report keeps
                    going to its original built-in recipients (Keetile&rsquo;s
                    UniCoin debtors group) until somebody is added here. Adding
                    a person under <strong>Send to</strong> takes over the list
                    completely.</>
                  )}
                </span>
              </div>
            )}

            <div className="flex flex-wrap items-end gap-2">
              <div className="flex flex-col gap-1">
                <label className="text-xs text-slate-600">Email address</label>
                <input
                  className="border rounded-md px-3 py-1.5 text-sm w-72"
                  placeholder="name@alphadirect.co.bw"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') void add() }}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-slate-600">Name (optional)</label>
                <input
                  className="border rounded-md px-3 py-1.5 text-sm w-48"
                  placeholder="Rose Mokgware"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') void add() }}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-slate-600">How</label>
                <select
                  aria-label="Send this person the email, or only copy them in"
                  className="border rounded-md px-2 py-1.5 text-sm"
                  value={kind}
                  onChange={e => setKind(e.target.value as 'to' | 'cc')}
                >
                  <option value="to">Send to</option>
                  <option value="cc">Copy in</option>
                </select>
              </div>
              <Button onClick={() => void add()} disabled={saving || !email.trim()}
                      style={{ background: NAVY }}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" />
                        : <Plus className="h-4 w-4" />}
                <span className="ml-2">Add</span>
              </Button>
            </div>

            {rows.length === 0 ? (
              <p className="text-sm text-slate-500">No addresses added yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-600 border-b">
                    <th className="py-2 font-medium">Email</th>
                    <th className="py-2 font-medium">Name</th>
                    <th className="py-2 font-medium">How</th>
                    <th className="py-2 font-medium">Added by</th>
                    <th className="py-2 font-medium text-right">Receiving</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.id} className="border-b last:border-0">
                      <td className={`py-2 ${r.active ? '' : 'text-slate-400 line-through'}`}>
                        {r.email}
                      </td>
                      <td className="py-2 text-slate-600">{r.name || '—'}</td>
                      <td className="py-2 text-slate-600">
                        {r.kind === 'to' ? 'Send to' : 'Copy in'}
                      </td>
                      <td className="py-2 text-slate-500">{r.added_by || '—'}</td>
                      <td className="py-2 text-right">
                        <Button variant="outline" size="sm" disabled={saving}
                                onClick={() => void toggle(r)}>
                          {r.active
                            ? <><X className="h-3.5 w-3.5" /><span className="ml-1.5">Stop</span></>
                            : <><Undo2 className="h-3.5 w-3.5" /><span className="ml-1.5">Restore</span></>}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="text-xs text-slate-500">
              Stopping somebody keeps the record that they used to receive it.
              Nobody is deleted.
            </p>
          </CardContent>
        </Card>

        {/* ── What they will receive ─────────────────────────────────────── */}
        <Card>
          <CardHeader>
            <CardTitle style={{ color: NAVY }}>What Monday&rsquo;s email will say</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin" /> Checking Graphite…
              </div>
            ) : !preview?.available ? (
              // An outage is shown as an outage. A page that renders an empty
              // table here would read as "a clean week".
              <div className="flex items-start gap-2 text-sm" style={{ color: AMBER }}>
                <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                <span>{preview?.detail || 'This could not be checked right now.'}</span>
              </div>
            ) : preview.count === 0 ? (
              <div className="flex items-center gap-2 text-sm" style={{ color: '#047857' }}>
                <CheckCircle2 className="h-4 w-4" />
                No failed debits in the last {preview.days} days.
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {[
                    ['Failed debits', String(preview.count)],
                    ['Not collected', money(preview.amount)],
                    ['On dead policies', String(preview.non_active ?? 0)],
                    ['Failed before', String(preview.repeat ?? 0)],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-md border p-3">
                      <div className="text-xs text-slate-500">{label}</div>
                      <div className="text-lg font-semibold" style={{ color: NAVY }}>
                        {value}
                      </div>
                    </div>
                  ))}
                </div>

                <div>
                  <div className="text-sm font-medium mb-2" style={{ color: NAVY }}>
                    Who has to chase what
                  </div>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-slate-600 border-b">
                        <th className="py-2 font-medium">Agent</th>
                        <th className="py-2 font-medium text-right">Failed</th>
                        <th className="py-2 font-medium text-right">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(preview.by_agent ?? []).map(a => (
                        <tr key={a.agent} className="border-b last:border-0">
                          <td className="py-2">{a.agent}</td>
                          <td className="py-2 text-right">{a.count}</td>
                          <td className="py-2 text-right">{money(a.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <p className="text-xs text-slate-500">
                  Covering {preview.start} to {preview.end}. The full list goes
                  out as a spreadsheet attached to the email.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  )
}
