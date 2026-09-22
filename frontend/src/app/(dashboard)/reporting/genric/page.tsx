'use client'

/**
 * GENRIC Monthly Reporting Pack — ONE button.
 *
 * Pick a month, attach the Graphite all-policy export (and last month's), press
 * Generate. Out comes the 13-report regulatory pack, the reinsurance submission
 * with the GENRIC invoice, and the cancellations report.
 *
 * Today this is a full day of manual work in Excel.
 *
 * Two things this screen is deliberate about:
 *  - a report that could NOT be produced is shown in red, with the reason, and
 *    is never allowed to look like a clean nil return; and
 *  - any open CFO question sits at the top of the page until it is answered,
 *    because a report is blocked on it. The pay window before cancellation was
 *    answered on 14 Sep 2026 (30 days) and now shows as a green banner stating
 *    the number in force — an answered question still has to be readable, or
 *    nobody can tell which number produced the cancellation list. GENRIC's bank
 *    details are still open and still red.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { getToken } from '@/lib/api'

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? ''
const API = `${BASE_URL}/api/v1`

async function authHeader(): Promise<Record<string, string>> {
  const h: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const b = await acquireApiToken()
      if (b) h['Authorization'] = `Bearer ${b}`
    }
  } catch { /* fall through */ }
  if (!h['Authorization']) { const t = getToken(); if (t) h['Authorization'] = `Token ${t}` }
  return h
}

interface ReportCard {
  key: string
  title: string
  nmi: string
  status: 'ok' | 'nil_no_activity' | 'nil_no_source' | 'blocked'
  row_count: number
  columns: string[]
  kpis: [string, string][]
  notes: string[]
  reconciled_against: string
}

interface OpenQuestion { for: string; blocks: string; question: string; setting: string }
/** The answered pay window. null only if somebody has blanked the setting,
 *  in which case it is back on open_questions as a red banner instead. */
interface PayWindow { setting: string; days: number; plain: string }
interface PackConfig { entity: string; open_questions: OpenQuestion[]; pay_window: PayWindow | null }

interface PackResponse {
  run_id: string
  period: string
  status: string
  reports: ReportCard[]
  manifest: Record<string, unknown>
}

const STATUS_STYLE: Record<ReportCard['status'], { label: string; bg: string; fg: string }> = {
  ok: { label: 'Produced', bg: '#E8F5EC', fg: '#166534' },
  nil_no_activity: { label: 'Nil return — checked', bg: '#F3F4F6', fg: '#4B5563' },
  nil_no_source: { label: 'NOT produced — no source', bg: '#FEE2E2', fg: '#991B1B' },
  blocked: { label: 'BLOCKED', bg: '#FEE2E2', fg: '#991B1B' },
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']

export default function GenricPackPage() {
  // Default to the month just ended. In January that is December of LAST year
  // — decrement the year too, or the page opens on a December twelve months in
  // the future and generates a pack for a period that has not happened.
  const now = new Date()
  const lastMonth = now.getMonth() === 0 ? 12 : now.getMonth()
  const [year, setYear] = useState(
    now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear())
  const [month, setMonth] = useState(lastMonth)
  const [invoiceNumber, setInvoiceNumber] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pack, setPack] = useState<PackResponse | null>(null)
  const [config, setConfig] = useState<PackConfig | null>(null)

  const currentFile = useRef<HTMLInputElement>(null)
  const priorFile = useRef<HTMLInputElement>(null)

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/genric/config/`, { headers: await authHeader() })
        if (r.ok) setConfig(await r.json())
      } catch { /* the page still works without it */ }
    })()
  }, [])

  const generate = useCallback(async () => {
    setBusy(true); setError(''); setPack(null)
    try {
      const form = new FormData()
      form.append('year', String(year))
      form.append('month', String(month))
      if (invoiceNumber.trim()) form.append('invoice_number', invoiceNumber.trim())
      const cur = currentFile.current?.files?.[0]
      const pri = priorFile.current?.files?.[0]
      if (cur) form.append('policy_export', cur)
      if (pri) form.append('prior_policy_export', pri)

      const res = await fetch(`${API}/genric/generate/`, {
        method: 'POST', headers: await authHeader(), body: form,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.error || `Generate failed (${res.status})`)
      }
      setPack(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Generate failed.')
    } finally {
      setBusy(false)
    }
  }, [year, month, invoiceNumber])

  const download = useCallback(async (what: 'xlsx' | 'invoice.pdf') => {
    if (!pack) return
    const form = new FormData()
    const cur = currentFile.current?.files?.[0]
    const pri = priorFile.current?.files?.[0]
    if (cur) form.append('policy_export', cur)
    if (pri) form.append('prior_policy_export', pri)
    const res = await fetch(`${API}/genric/runs/${pack.run_id}/${what}/`, {
      method: 'POST', headers: await authHeader(), body: form,
    })
    if (!res.ok) { setError(`Export failed (${res.status})`); return }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = what === 'xlsx'
      ? `GENRIC-Pack-${year}-${String(month).padStart(2, '0')}.xlsx`
      : 'GENRIC-invoice.pdf'
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  }, [pack, year, month])

  const openQuestions = config?.open_questions ?? []
  const payWindow = config?.pay_window ?? null

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1180, margin: '0 auto' }}>
      <h1 style={{ fontSize: 26, fontWeight: 700, color: '#1D3270', margin: 0 }}>
        GENRIC Monthly Reporting Pack
      </h1>
      <p style={{ color: '#6B7280', marginTop: 6, fontSize: 14 }}>
        {config?.entity ?? 'Alpha Direct South Africa (Third Party Motor)'} · one
        button, one month. The 13-report regulatory pack, the reinsurance
        submission with the GENRIC invoice, and the cancellations report.
      </p>

      {payWindow && (
        <div style={{
          marginTop: 16, padding: '14px 16px', borderRadius: 10,
          background: '#F0FDF4', border: '1px solid #86EFAC',
        }}>
          <div style={{ fontWeight: 700, color: '#166534', fontSize: 14 }}>
            Pay window in force — {payWindow.plain}
          </div>
          <div style={{ marginTop: 6, color: '#14532D', fontSize: 14 }}>
            The cancellations report recommends a policy only once it has been
            unpaid for more than {payWindow.days} days, counted from its failed
            collection to the last day of the reporting month. A policy at
            exactly {payWindow.days} days is still inside the window and is not
            recommended.
          </div>
          <div style={{ marginTop: 6, color: '#166534', fontSize: 12 }}>
            Changed on screen, with no deploy: Omni Admin → GENRIC Settings →{' '}
            {payWindow.setting}. Admin access is the CFO&apos;s today, so ask him
            to change it. Blank it and the report goes back to blocked rather
            than quietly picking a number.
          </div>
        </div>
      )}

      {openQuestions.map(q => (
        <div key={q.setting} style={{
          marginTop: 16, padding: '14px 16px', borderRadius: 10,
          background: '#FEF2F2', border: '1px solid #FCA5A5',
        }}>
          <div style={{ fontWeight: 700, color: '#991B1B', fontSize: 14 }}>
            One question is still open — {q.blocks} cannot be finished
          </div>
          <div style={{ marginTop: 6, color: '#7F1D1D', fontSize: 14 }}>{q.question}</div>
          <div style={{ marginTop: 6, color: '#991B1B', fontSize: 12 }}>
            Nothing was guessed. Everything else in the pack still generates.
          </div>
        </div>
      ))}

      <div style={{
        marginTop: 20, padding: 18, borderRadius: 12, background: '#FFFFFF',
        border: '1px solid #E5E7EB', display: 'grid', gap: 14,
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
      }}>
        <label style={{ display: 'grid', gap: 6, fontSize: 13, color: '#374151' }}>
          Reporting month
          <select value={month} onChange={e => setMonth(Number(e.target.value))}
                  style={inputStyle}>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
        </label>
        <label style={{ display: 'grid', gap: 6, fontSize: 13, color: '#374151' }}>
          Year
          <input type="number" value={year} onChange={e => setYear(Number(e.target.value))}
                 style={inputStyle} />
        </label>
        <label style={{ display: 'grid', gap: 6, fontSize: 13, color: '#374151' }}>
          Invoice number (confirm, do not guess)
          <input placeholder="GENRIC-1-025" value={invoiceNumber}
                 onChange={e => setInvoiceNumber(e.target.value)} style={inputStyle} />
        </label>
        <label style={{ display: 'grid', gap: 6, fontSize: 13, color: '#374151' }}>
          Graphite all-policy export
          <input type="file" accept=".csv" ref={currentFile} style={inputStyle} />
        </label>
        <label style={{ display: 'grid', gap: 6, fontSize: 13, color: '#374151' }}>
          Prior-month Graphite export
          <input type="file" accept=".csv" ref={priorFile} style={inputStyle} />
        </label>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button onClick={generate} disabled={busy} style={{
            width: '100%', padding: '11px 18px', borderRadius: 9, border: 'none',
            background: busy ? '#9CA3AF' : '#F47C20', color: '#fff',
            fontWeight: 700, fontSize: 15, cursor: busy ? 'default' : 'pointer',
            transition: 'background .18s cubic-bezier(.32,.72,0,1)',
          }}>
            {busy ? 'Generating…' : 'Generate GENRIC Pack'}
          </button>
        </div>
      </div>

      <p style={{ marginTop: 10, fontSize: 12, color: '#6B7280' }}>
        The FNB statement is not attached — it is read from the bank statements
        already imported into Omni for account 63104367974.
      </p>

      {error && (
        <div style={{
          marginTop: 16, padding: '12px 14px', borderRadius: 9,
          background: '#FEF2F2', border: '1px solid #FCA5A5', color: '#991B1B',
        }}>{error}</div>
      )}

      {pack && (
        <>
          <div style={{
            marginTop: 24, display: 'flex', alignItems: 'center', gap: 12,
            flexWrap: 'wrap',
          }}>
            <h2 style={{ fontSize: 19, fontWeight: 700, color: '#1D3270', margin: 0 }}>
              {pack.period}
            </h2>
            <button onClick={() => download('xlsx')} style={secondaryBtn}>
              Export Excel (live formulas)
            </button>
            <button onClick={() => download('invoice.pdf')} style={secondaryBtn}>
              Invoice PDF
            </button>
          </div>

          <div style={{
            marginTop: 16, display: 'grid', gap: 14,
            gridTemplateColumns: 'repeat(auto-fill, minmax(330px, 1fr))',
          }}>
            {pack.reports.map(r => {
              const s = STATUS_STYLE[r.status]
              return (
                <div key={r.key} style={{
                  padding: 16, borderRadius: 12, background: '#fff',
                  border: `1px solid ${r.status === 'ok' || r.status === 'nil_no_activity'
                    ? '#E5E7EB' : '#FCA5A5'}`,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <div style={{ fontWeight: 700, color: '#1D3270', fontSize: 15 }}>
                      {r.title}{r.nmi ? ` · ${r.nmi}` : ''}
                    </div>
                    <span style={{
                      background: s.bg, color: s.fg, fontSize: 11, fontWeight: 700,
                      padding: '3px 8px', borderRadius: 999, whiteSpace: 'nowrap',
                      height: 'fit-content',
                    }}>{s.label}</span>
                  </div>

                  {r.kpis.length > 0 && (
                    <div style={{ marginTop: 10, display: 'grid', gap: 4 }}>
                      {r.kpis.slice(0, 5).map(([k, v]) => (
                        <div key={k} style={{
                          display: 'flex', justifyContent: 'space-between',
                          fontSize: 13, gap: 10,
                        }}>
                          <span style={{ color: '#6B7280' }}>{k}</span>
                          <span style={{ color: '#111827', fontWeight: 600 }}>{v}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {r.row_count > 0 && (
                    <div style={{ marginTop: 8, fontSize: 12, color: '#6B7280' }}>
                      {r.row_count} row{r.row_count === 1 ? '' : 's'}
                    </div>
                  )}

                  {(r.status === 'blocked' || r.status === 'nil_no_source') && r.notes[0] && (
                    <div style={{
                      marginTop: 10, fontSize: 12, color: '#991B1B', lineHeight: 1.5,
                    }}>{r.notes[0]}</div>
                  )}

                  {r.reconciled_against && (
                    <div style={{
                      marginTop: 10, fontSize: 11, color: '#6B7280',
                      lineHeight: 1.5, fontStyle: 'italic',
                    }}>
                      Reconciled against: {r.reconciled_against}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  padding: '9px 11px', borderRadius: 8, border: '1px solid #D1D5DB',
  fontSize: 14, background: '#fff',
}

const secondaryBtn: React.CSSProperties = {
  padding: '8px 14px', borderRadius: 8, border: '1px solid #1D3270',
  background: '#fff', color: '#1D3270', fontWeight: 600, fontSize: 13,
  cursor: 'pointer',
}
