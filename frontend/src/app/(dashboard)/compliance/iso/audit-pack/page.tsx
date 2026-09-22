'use client'

/**
 * /compliance/iso/audit-pack — auditor evidence pack.
 *
 * One-click download of the full ISO 27001 evidence ZIP plus quick stats:
 *   - SoA coverage (implemented / partial / planned / excluded)
 *   - Open findings by severity
 *   - Open CAPAs
 *   - Last internal audit + management review
 *
 * Hand the URL to the external auditor; they get every CSV they need.
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch, API_BASE, getToken, apiFetchBinary } from '@/lib/api'
import { Download, Package, FileBadge, AlertTriangle, ClipboardCheck } from 'lucide-react'
import { localYmd } from '@/lib/utils'

interface SoASummary {
  total: number; applicable: number; excluded: number
  by_status: Record<string, number>
  by_domain: Record<string, number>
}

interface Commandments {
  overall_score: number
  latest_run: { finished_at: string | null; actor: string; findings_created: number } | null
  commandments: Array<{ number: number; title: string; status_label: string;
    open_count: number; critical_count: number; high_count: number }>
}

export default function AuditPackPage() {
  const [soa, setSoA] = useState<SoASummary | null>(null)
  const [cmds, setCmds] = useState<Commandments | null>(null)
  const [risks, setRisks] = useState<number>(0)
  const [capas, setCAPAs] = useState<number>(0)
  const [policies, setPolicies] = useState<number>(0)
  const [evidence, setEvidence] = useState<number>(0)
  const [ia, setIA] = useState<number>(0)
  const [mr, setMR] = useState<number>(0)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      const [s, c, r, ca, p, e, i, m] = await Promise.all([
        apiFetch<SoASummary>('/iso/soa-summary/'),
        apiFetch<Commandments>('/iso/commandments/'),
        apiFetch<any>('/iso/risks/?page_size=1'),
        apiFetch<any>('/iso/capas/?page_size=1'),
        apiFetch<any>('/iso/policies/?page_size=1'),
        apiFetch<any>('/iso/evidence/?page_size=1'),
        apiFetch<any>('/iso/internal-audits/?page_size=1'),
        apiFetch<any>('/iso/management-reviews/?page_size=1'),
      ])
      setSoA(s); setCmds(c)
      setRisks(r?.count ?? (Array.isArray(r) ? r.length : 0))
      setCAPAs(ca?.count ?? (Array.isArray(ca) ? ca.length : 0))
      setPolicies(p?.count ?? (Array.isArray(p) ? p.length : 0))
      setEvidence(e?.count ?? (Array.isArray(e) ? e.length : 0))
      setIA(i?.count ?? (Array.isArray(i) ? i.length : 0))
      setMR(m?.count ?? (Array.isArray(m) ? m.length : 0))
    } catch (e: any) {
      setErr(e?.message || 'Load failed')
    } finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const downloadPack = async () => {
    // FE-SWEEP swarm 2026-06-08 #18: was sending "Token __sso__" for SSO
    // users → 401 download. apiFetchBinary handles Bearer/Token + sentinel skip.
    const res = await apiFetchBinary(`${API_BASE}/iso/auditor-pack/`)
    if (!res.ok) {
      setErr(`Download failed: ${res.status}`)
      return
    }
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `iso27001-audit-pack-${localYmd(new Date())}.zip`
    a.click()
  }

  const cmdCritical = cmds?.commandments.reduce((s, c) => s + c.critical_count, 0) ?? 0
  const cmdHigh     = cmds?.commandments.reduce((s, c) => s + c.high_count, 0) ?? 0
  const cmdOpen     = cmds?.commandments.reduce((s, c) => s + c.open_count, 0) ?? 0

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-3">
              <Package size={28} style={{ color: '#0D1B2A' }} />
              <h1 className="text-2xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
                Auditor Evidence Pack
              </h1>
            </div>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              Single-click ZIP with every CSV an external ISO/IEC 27001:2022 auditor expects to
              receive: Statement of Applicability, Risk Register, Findings, CAPA, Policies,
              Evidence, Internal Audits, Management Reviews, Run History.
            </p>
          </div>
          <button onClick={downloadPack}
            className="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium text-white shadow-sm"
            style={{ background: '#0D1B2A' }}>
            <Download size={16} /> Download Auditor Pack (.zip)
          </button>
        </div>

        {err && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {err}
          </div>
        )}

        <div className="mb-6 grid gap-4 md:grid-cols-4">
          <Stat icon={FileBadge} label="Annex A controls (SoA)" value={soa?.total ?? 0}
            sub={soa ? `${soa.applicable} applicable · ${soa.excluded} excluded` : 'loading'} />
          <Stat icon={AlertTriangle} label="Open audit findings" value={cmdOpen}
            sub={`${cmdCritical} critical · ${cmdHigh} high`}
            tone={cmdCritical > 0 ? 'red' : cmdHigh > 0 ? 'amber' : 'green'} />
          <Stat icon={ClipboardCheck} label="Open CAPAs" value={capas} />
          <Stat icon={FileBadge} label="Posture score" value={`${cmds?.overall_score ?? 0}%`}
            tone={(cmds?.overall_score ?? 0) >= 85 ? 'green' : (cmds?.overall_score ?? 0) >= 65 ? 'amber' : 'red'} />
        </div>

        <Card className="mb-6 border border-slate-200">
          <CardContent className="py-5">
            <h2 className="mb-3 font-serif text-lg font-semibold" style={{ color: '#0D1B2A' }}>
              SoA implementation status
            </h2>
            <div className="grid grid-cols-5 gap-3 text-sm">
              <StatusBar label="Implemented" n={soa?.by_status?.implemented ?? 0} total={soa?.total ?? 1} tone="bg-emerald-500" />
              <StatusBar label="Partial"     n={soa?.by_status?.partial     ?? 0} total={soa?.total ?? 1} tone="bg-amber-500" />
              <StatusBar label="Planned"     n={soa?.by_status?.planned     ?? 0} total={soa?.total ?? 1} tone="bg-sky-500" />
              <StatusBar label="Not impl."   n={soa?.by_status?.not_implemented ?? 0} total={soa?.total ?? 1} tone="bg-red-500" />
              <StatusBar label="Excluded"    n={soa?.by_status?.excluded    ?? 0} total={soa?.total ?? 1} tone="bg-slate-500" />
            </div>
          </CardContent>
        </Card>

        <Card className="border border-slate-200">
          <CardContent className="py-5">
            <h2 className="mb-3 font-serif text-lg font-semibold" style={{ color: '#0D1B2A' }}>
              Pack contents
            </h2>
            <ul className="space-y-1 text-sm">
              {[
                { f: '01_statement_of_applicability.csv', n: soa?.total ?? 0, l: 'rows' },
                { f: '02_risk_register.csv',              n: risks, l: 'risks' },
                { f: '03_commandments_scorecard.csv',     n: 10, l: 'commandments' },
                { f: '04_findings.csv',                   n: cmdOpen, l: 'open + historical findings' },
                { f: '05_capa_register.csv',              n: capas, l: 'CAPAs' },
                { f: '06_policies.csv',                   n: policies, l: 'policies' },
                { f: '07_evidence_register.csv',          n: evidence, l: 'evidence rows' },
                { f: '08_internal_audits.csv',            n: ia, l: 'internal audits' },
                { f: '09_management_reviews.csv',         n: mr, l: 'management reviews' },
                { f: '10_audit_runs.csv',                 n: 0, l: 'scan history' },
                { f: 'README.txt',                        n: '', l: 'cover sheet' },
              ].map((row) => (
                <li key={row.f} className="flex items-center justify-between border-b border-slate-100 py-1.5">
                  <span className="font-mono text-xs text-slate-700">{row.f}</span>
                  <span className="text-xs text-slate-500">
                    {row.n !== '' && <span className="font-mono">{row.n}</span>} {row.l}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </main>
    </div>
  )
}

function Stat({ icon: Icon, label, value, sub, tone }: {
  icon: any; label: string; value: any; sub?: string; tone?: 'green'|'amber'|'red'
}) {
  const ring = tone === 'red' ? 'border-red-300 bg-red-50'
            : tone === 'amber' ? 'border-amber-300 bg-amber-50'
            : tone === 'green' ? 'border-emerald-300 bg-emerald-50'
            : 'border-slate-200 bg-white'
  return (
    <div className={`rounded-md border ${ring} p-4`}>
      <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wide text-slate-500">
        <Icon size={14} />
        {label}
      </div>
      <div className="font-serif text-2xl font-semibold" style={{ color: '#0D1B2A' }}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

function StatusBar({ label, n, total, tone }:
  { label: string; n: number; total: number; tone: string }) {
  const pct = total ? Math.round((n / total) * 100) : 0
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-slate-500">{label}</span>
        <span className="font-mono font-semibold" style={{ color: '#0D1B2A' }}>{n}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 text-[10px] text-slate-400">{pct}%</div>
    </div>
  )
}
