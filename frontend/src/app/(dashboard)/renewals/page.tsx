'use client'

/**
 * [B1] Renewals — who renews in a chosen month, read live from Graphite.
 *
 * Replaces the monthly export-and-clean Finance does by hand. Backend:
 * /api/v1/graphite/renewals/?month=10 (+ &lob=, &include_quotes=, &download=csv|xlsx).
 *
 * The month is the only date input, deliberately. Finance settled on 14-Sep that
 * the year is NOT a filter — every policy renewing in October appears, whatever
 * year it started — so a year box would quietly shrink the list.
 *
 * Empty, loading, error and "Graphite is unreachable" are four different screens
 * here. A report that shows nothing and says nothing is the one that gets
 * believed when it is broken.
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch, apiFetchRaw, saveBlob } from '@/lib/api'
import { AlertTriangle, Download, Loader2, RefreshCw } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const LINE = '#E6EAF0'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December']

interface Row {
  policy_number: string
  insured_name: string
  broker_name: string
  agent_name: string
  product: string
  renewal_effective_date: string
  renewal_expiry_date: string
  next_renewal_date: string
  payment_frequency: string
  policy_status: string
  sum_insured: string
  renewal_premium: number | null
  inforce_premium: number | null
  no_issued_anniversary: boolean
}
interface Result {
  available: boolean
  reason?: string
  month: number
  rows: Row[]
  dropped: { policy_number: string; problem: string }[]
  flagged: { policy_number: string; problem: string }[]
  notes: string[]
  count: number
  first_renewal_count: number
}

const pula = (n: number | null) =>
  n === null || n === undefined
    ? '—'
    : `P${n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function RenewalsPage() {
  // Default to next month: that is the list a renewals clerk is working now.
  const [month, setMonth] = useState(() => (new Date().getMonth() + 1) % 12 + 1)
  const [lob, setLob] = useState('')
  const [includeQuotes, setIncludeQuotes] = useState(false)
  const [data, setData] = useState<Result | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const params = useCallback(() => {
    const q = new URLSearchParams({ month: String(month) })
    if (lob) q.set('lob', lob)
    if (includeQuotes) q.set('include_quotes', '1')
    return q.toString()
  }, [month, lob, includeQuotes])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await apiFetch<Result>(`/graphite/renewals/?${params()}`))
    } catch {
      setError('The renewal list could not be loaded. Nothing has changed — try again.')
      setData(null)
    }
    setLoading(false)
  }, [params])

  useEffect(() => { load() }, [load])

  const download = async (format: 'csv' | 'xlsx') => {
    const response = await apiFetchRaw(`/graphite/renewals/?${params()}&download=${format}`)
    if (!response.ok) { setError('The download failed. The list on screen is unchanged.'); return }
    saveBlob(await response.blob(), `renewals-${MONTHS[month - 1]}.${format}`)
  }

  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="Renewals" />
      <div className="mx-auto max-w-[1500px] px-6 py-5">

        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-gray-500">Renewal month</span>
            <select value={month} onChange={e => setMonth(Number(e.target.value))}
              className="rounded-md border px-3 py-1.5 text-sm" style={{ borderColor: LINE }}>
              {MONTHS.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}
            </select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-gray-500">Line of business</span>
            <select value={lob} onChange={e => setLob(e.target.value)}
              className="rounded-md border px-3 py-1.5 text-sm" style={{ borderColor: LINE }}>
              <option value="">Domestic and commercial</option>
              <option value="domestic">Domestic only</option>
              <option value="commercial">Commercial only</option>
            </select>
          </label>

          <label className="flex items-center gap-2 pb-1.5 text-sm text-gray-700">
            <input type="checkbox" checked={includeQuotes}
              onChange={e => setIncludeQuotes(e.target.checked)} />
            Include anniversaries still at quote
          </label>

          <div className="ml-auto flex gap-2">
            <button onClick={load} title="Reload"
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm"
              style={{ borderColor: LINE }}>
              <RefreshCw className="h-3.5 w-3.5" /> Reload
            </button>
            <button onClick={() => download('csv')} disabled={!data?.available}
              className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
              style={{ background: NAVY }}>
              <Download className="h-3.5 w-3.5" /> CSV
            </button>
            <button onClick={() => download('xlsx')} disabled={!data?.available}
              className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
              style={{ background: ORANGE }}>
              <Download className="h-3.5 w-3.5" /> Excel
            </button>
          </div>
        </div>

        <p className="mb-4 text-xs text-gray-500">
          Every policy renewing in {MONTHS[month - 1]}, whatever year it started.
          Domestic and commercial only — Instant is not included.
        </p>

        {loading && (
          <div className="flex items-center gap-2 py-10 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Reading Graphite…
          </div>
        )}

        {!loading && error && (
          <Card><CardContent className="flex items-start gap-2 py-5 text-sm text-red-700">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </CardContent></Card>
        )}

        {!loading && !error && data && !data.available && (
          <Card><CardContent className="flex items-start gap-2 py-5 text-sm text-amber-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {data.reason}
          </CardContent></Card>
        )}

        {!loading && !error && data?.available && (
          <>
            <div className="mb-3 text-sm text-gray-700">
              <strong>{data.count}</strong> {data.count === 1 ? 'policy renews' : 'policies renew'} in {MONTHS[month - 1]}
              {data.first_renewal_count > 0 && <> · <strong>{data.first_renewal_count}</strong> with no issued anniversary on record</>}
              {data.dropped.length > 0 && <> · <strong>{data.dropped.length}</strong> left off, listed below</>}
              {data.flagged.length > 0 && <> · <strong>{data.flagged.length}</strong> on the list but incomplete</>}
            </div>

            {data.notes.map(note => (
              <p key={note} className="mb-3 text-xs text-gray-500">{note}</p>
            ))}

            {data.count === 0 ? (
              <Card><CardContent className="py-8 text-center text-sm text-gray-500">
                No policy renews in {MONTHS[month - 1]}.
              </CardContent></Card>
            ) : (
              <div className="overflow-x-auto rounded-lg border bg-white" style={{ borderColor: LINE }}>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
                      {['Policy', 'Insured', 'Broker', 'Agent', 'Product', 'Next renewal',
                        'Expiry', 'Frequency', 'Status', 'Renewal premium', 'Inforce premium']
                        .map(h => <th key={h} className="whitespace-nowrap border-b px-3 py-2 font-medium" style={{ borderColor: LINE }}>{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map(row => (
                      <tr key={row.policy_number} className="border-b last:border-0 hover:bg-gray-50" style={{ borderColor: LINE }}>
                        <td className="whitespace-nowrap px-3 py-2 font-medium">
                          {row.policy_number}
                          {row.no_issued_anniversary && (
                            <span className="ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold text-white"
                              style={{ background: ORANGE }}
                              title="No issued anniversary invoice on record — the date comes from the policy's first invoice">
                              no anniversary yet
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2">{row.insured_name || '—'}</td>
                        <td className="px-3 py-2">{row.broker_name || '—'}</td>
                        <td className="px-3 py-2">{row.agent_name || '—'}</td>
                        <td className="px-3 py-2">{row.product || '—'}</td>
                        <td className="whitespace-nowrap px-3 py-2">{row.next_renewal_date || '—'}</td>
                        <td className="whitespace-nowrap px-3 py-2">{row.renewal_expiry_date || '—'}</td>
                        <td className="whitespace-nowrap px-3 py-2">{row.payment_frequency}</td>
                        <td className="whitespace-nowrap px-3 py-2">{row.policy_status}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-right">{pula(row.renewal_premium)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-right">{pula(row.inforce_premium)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {([['Left off the list, and why', data.dropped],
               ['On the list, but something is missing', data.flagged]] as const)
              .filter(([, items]) => items.length > 0)
              .map(([heading, items]) => (
                <div key={heading} className="mt-6">
                  <h2 className="mb-2 text-sm font-semibold" style={{ color: NAVY }}>{heading}</h2>
                  <div className="rounded-lg border bg-white" style={{ borderColor: LINE }}>
                    {items.map(e => (
                      <div key={e.policy_number + e.problem}
                        className="flex gap-3 border-b px-3 py-2 text-sm last:border-0" style={{ borderColor: LINE }}>
                        <span className="w-40 shrink-0 font-medium">{e.policy_number}</span>
                        <span className="text-gray-600">{e.problem}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
          </>
        )}
      </div>
    </div>
  )
}
