'use client'

/**
 * /reports/ai-integrity — Aria-backed integrity scanner.
 *
 * CFO directive 2026-05-18. Three scanners ship today:
 *   NARRATIVE_MISMATCH  — does the narrative match the accounts?
 *   VENDOR_GL_MISMATCH  — Eskom posted to telecoms? Microsoft to reinsurance?
 *   AMOUNT_ANOMALY      — round-number / duplicated / split patterns
 *
 * The scan is ON-DEMAND (POST). Each scanner = 1 Aria call so a
 * 7-day window typically costs ≤ 3 calls. Keep the window narrow.
 */
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  AlertCircle, AlertTriangle, CheckCircle2, Loader2,
  Sparkles, ChevronDown, ChevronRight,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { apiFetch, getToken } from '@/lib/api'
import { localYmd } from '@/lib/utils'

interface Finding {
  je_id:       string
  entry_date:  string
  severity:    'high' | 'medium' | 'low'
  category:    'NARRATIVE_MISMATCH' | 'VENDOR_GL_MISMATCH' | 'AMOUNT_ANOMALY'
  message:     string
  accounts:    string[]
  amount_bwp:  number
}

interface ScanResponse {
  from_date:   string
  to_date:     string
  company:     string
  sample_size: number
  findings:    Finding[]
  counts:      Record<string, number>
}

const CATEGORY_LABEL: Record<Finding['category'], string> = {
  NARRATIVE_MISMATCH: 'Narrative ↔ accounts',
  VENDOR_GL_MISMATCH: 'Vendor ↔ GL',
  AMOUNT_ANOMALY:     'Amount pattern',
}

const SEVERITY_COLOR: Record<Finding['severity'], string> = {
  high:   '#dc2626',
  medium: '#d97706',
  low:    '#2563eb',
}

function fmt(n: number): string {
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0)
}

function defaultRange(): { from: string; to: string } {
  const now = new Date()
  const seven = new Date(now.getTime() - 7 * 86400 * 1000)
  return { from: localYmd(seven), to: localYmd(now) }
}

export default function AiIntegrityPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { selectedId } = useCompany()
  const range = defaultRange()
  const [from, setFrom] = useState(range.from)
  const [to,   setTo]   = useState(range.to)
  const [scanners, setScanners] = useState<Set<string>>(new Set(['narrative', 'vendor', 'amount']))
  const [busy, setBusy] = useState(false)
  const [data, setData] = useState<ScanResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function runScan() {
    if (!getToken()) { router.replace('/login'); return }
    setBusy(true); setError(null); setData(null)
    try {
      const body = {
        from_date: from,
        to_date:   to,
        company:   selectedId || null,
        scanners:  Array.from(scanners),
      }
      const r = await apiFetch<ScanResponse>('/ai/integrity-scan/', {
        method: 'POST', body: JSON.stringify(body),
      })
      setData(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setBusy(false)
    }
  }

  function toggleScanner(s: string) {
    const next = new Set(scanners)
    if (next.has(s)) next.delete(s); else next.add(s)
    setScanners(next)
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="AI Integrity Scan" breadcrumbs={[{ label: 'Reporting' }, { label: 'AI Integrity' }]} />
      <div className="flex-1 p-6 space-y-5">
        <div>
          <h1 className="text-xl font-bold inline-flex items-center gap-2" style={{ color: theme.navy }}>
            <Sparkles className="w-5 h-5" style={{ color: theme.orange }} /> AI Integrity Scan
          </h1>
          <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
            Aria reads the posted journal entries in the window and flags narrative / vendor / amount
            anomalies a regex can't catch. Sample is capped at 200 entries per scan to keep the bill predictable.
          </p>
        </div>

        <div className="rounded-2xl p-4"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>From</label>
              <input type="date" value={from} onChange={e => setFrom(e.target.value)}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                     style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>To</label>
              <input type="date" value={to} onChange={e => setTo(e.target.value)}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                     style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            </div>
            <div className="md:col-span-2">
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Scanners</label>
              <div className="flex flex-wrap gap-2">
                {[
                  ['narrative', 'Narrative ↔ accounts'],
                  ['vendor',    'Vendor ↔ GL'],
                  ['amount',    'Amount pattern'],
                ].map(([id, label]) => (
                  <button key={id} type="button" onClick={() => toggleScanner(id)}
                          className="px-3 py-2 rounded-lg text-xs font-semibold"
                          style={{
                            background: scanners.has(id) ? theme.oL : theme.g100,
                            color:      scanners.has(id) ? theme.orange : theme.t2,
                            border:    `1px solid ${scanners.has(id) ? theme.orange + '55' : theme.cardBdr}`,
                          }}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-between">
            <p className="text-[11px]" style={{ color: theme.t2 }}>
              ≈ 1 Aria call per scanner. 7-day window recommended.
            </p>
            <button type="button" onClick={runScan} disabled={busy || scanners.size === 0}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {busy ? 'Scanning…' : 'Run scan'}
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg p-3 flex items-center gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30`, color: theme.er }}>
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}

        {data && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <KpiCard label="Sample" value={String(data.sample_size)} theme={theme} />
              <KpiCard label="Narrative" value={String(data.counts.NARRATIVE_MISMATCH || 0)} accent theme={theme} />
              <KpiCard label="Vendor ↔ GL" value={String(data.counts.VENDOR_GL_MISMATCH || 0)} accent theme={theme} />
              <KpiCard label="Amount" value={String(data.counts.AMOUNT_ANOMALY || 0)} accent theme={theme} />
            </div>

            {data.findings.length === 0 ? (
              <div className="rounded-2xl p-6 text-center"
                   style={{ background: theme.okB, border: `1px solid ${theme.ok}40` }}>
                <CheckCircle2 className="w-6 h-6 mx-auto mb-2" style={{ color: theme.ok }} />
                <p className="text-sm font-semibold" style={{ color: theme.ok }}>No anomalies flagged in this window.</p>
                <p className="text-xs mt-1" style={{ color: theme.t2 }}>
                  Aria reviewed {data.sample_size} entries.
                </p>
              </div>
            ) : (
              <div className="rounded-2xl"
                   style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
                {data.findings.map(f => (
                  <FindingRow key={f.je_id + f.category} f={f} theme={theme} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function KpiCard({ label, value, accent, theme }: { label: string; value: string; accent?: boolean; theme: any }) {
  return (
    <div className="rounded-2xl p-4"
         style={{ background: theme.card, border: `1px solid ${accent && value !== '0' ? theme.orange + '55' : theme.cardBdr}` }}>
      <div className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>{label}</div>
      <div className="text-2xl font-bold tabular-nums mt-1"
           style={{ color: accent && value !== '0' ? theme.orange : theme.text }}>
        {value}
      </div>
    </div>
  )
}

function FindingRow({ f, theme }: { f: Finding; theme: any }) {
  const [open, setOpen] = useState(false)
  return (
    <button type="button" onClick={() => setOpen(!open)}
            className="w-full text-left px-4 py-3 border-b"
            style={{ borderColor: theme.cardBdr }}>
      <div className="flex items-center gap-3">
        <span className="text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 rounded"
              style={{ background: SEVERITY_COLOR[f.severity] + '22', color: SEVERITY_COLOR[f.severity] }}>
          {f.severity}
        </span>
        <span className="text-xs font-semibold" style={{ color: theme.t2 }}>
          {CATEGORY_LABEL[f.category]}
        </span>
        <span className="text-xs font-mono" style={{ color: theme.t2 }}>{f.entry_date}</span>
        <span className="text-sm flex-1 truncate" style={{ color: theme.text }}>{f.message}</span>
        <span className="text-xs tabular-nums" style={{ color: theme.t2 }}>BWP {fmt(f.amount_bwp)}</span>
        {open ? <ChevronDown className="w-4 h-4" style={{ color: theme.t2 }} /> :
                <ChevronRight className="w-4 h-4" style={{ color: theme.t2 }} />}
      </div>
      {open && (
        <div className="mt-2 ml-12 text-xs space-y-1">
          <div>
            <span className="opacity-60" style={{ color: theme.t2 }}>JE:</span>{' '}
            <a href={`/journal-entries/${f.je_id}`} style={{ color: theme.orange }}>{f.je_id.slice(0, 8)}</a>
          </div>
          <div>
            <span className="opacity-60" style={{ color: theme.t2 }}>Accounts hit:</span>{' '}
            <span style={{ color: theme.text }}>{f.accounts.join(', ')}</span>
          </div>
        </div>
      )}
    </button>
  )
}
