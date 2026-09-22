'use client'

/**
 * /compliance/nbfira/annual — NBFIRA Annual Return (Phase 3 + Phase 4).
 *
 * Three schedules: AFS (audited statements), IMF Assets (Investment
 * Management Form — admissible assets), IMF Liabilities (technical
 * provisions + L+E). Annual return is due 90 days after FY end per
 * Insurance Industry Regulations 2019 reg 31.
 *
 * Pick FY (FYxxxx), generate from GL, edit values on draft, walk the
 * workflow review → approve → lock → submit, then export the XLSX
 * for upload to the NBFIRA portal.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  ChevronLeft, RefreshCw, Loader2, Plus, AlertCircle, Info,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { authedHrisFetch } from '../../../hris/_shared'
import {
  AuditLogPanel, ScheduleTable, WorkflowBar,
  type ReturnDetail, type ReturnLine, type ReturnSummary,
} from '../_workflow'

const SCHEDULE_TABS: { code: string; title: string }[] = [
  { code: 'AFS',     title: 'AFS — Audited Statements' },
  { code: 'IMF_A',   title: 'IMF Assets' },
  { code: 'IMF_L',   title: 'IMF Liabilities + Equity' },
]

function fyToday(): string {
  const t = new Date()
  const y = (t.getMonth() + 1) >= 7 ? t.getFullYear() + 1 : t.getFullYear()
  return `FY${y}`
}

export default function NBFIRAAnnualPage() {
  const { theme } = useTheme()
  const { selectedId: companyId } = useCompany()

  const [list, setList]           = useState<ReturnSummary[]>([])
  const [activeId, setActiveId]   = useState<string | null>(null)
  const [activeRet, setActiveRet] = useState<ReturnDetail | null>(null)
  const [tab, setTab]             = useState<string>('AFS')
  const [loading, setLoading]     = useState(false)
  const [busy, setBusy]           = useState<null | 'create' | 'regenerate'>(null)
  const [err, setErr]             = useState<string | null>(null)
  const [period, setPeriod]       = useState<string>(fyToday())
  const [auditOpen, setAuditOpen] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      const params = new URLSearchParams()
      params.set('type', 'annual')
      if (companyId) params.set('company', companyId)
      const r = await authedHrisFetch(`/api/v1/nbfira/returns/?${params}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      setList(data.results || [])
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load')
    } finally { setLoading(false) }
  }, [companyId])

  useEffect(() => { reload() }, [reload])

  useEffect(() => {
    if (!activeId) { setActiveRet(null); return }
    let cancelled = false
    setLoading(true); setErr(null)
    authedHrisFetch(`/api/v1/nbfira/returns/${activeId}/`)
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const data = await r.json()
        if (!cancelled) setActiveRet(data)
      })
      .catch(e => !cancelled && setErr(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [activeId])

  async function onCreate() {
    setBusy('create'); setErr(null)
    try {
      const r = await authedHrisFetch('/api/v1/nbfira/returns/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'annual', period_label: period, company: companyId || undefined }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      setActiveId(data.id)
      await reload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Create failed')
    } finally { setBusy(null) }
  }

  async function onRegenerate() {
    if (!activeId) return
    setBusy('regenerate'); setErr(null)
    try {
      const r = await authedHrisFetch(`/api/v1/nbfira/returns/${activeId}/regenerate/`, { method: 'POST' })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      setActiveRet(data)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Regenerate failed')
    } finally { setBusy(null) }
  }

  async function onEditLine(line: ReturnLine, newValue: string) {
    if (!activeId || !activeRet) return
    setErr(null)
    try {
      const r = await authedHrisFetch(`/api/v1/nbfira/returns/${activeId}/lines/${line.id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: newValue }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      // refresh detail
      const det = await authedHrisFetch(`/api/v1/nbfira/returns/${activeId}/`)
      if (det.ok) setActiveRet(await det.json())
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Edit failed')
    }
  }

  const linesForTab = useMemo(() => {
    if (!activeRet) return []
    return activeRet.lines_by_schedule[tab] || []
  }, [activeRet, tab])

  const editable = !!activeRet
    && ['draft', 'reopened', 'rejected'].includes(activeRet.status)

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="NBFIRA — Annual Return"
        breadcrumbs={[
          { label: 'Compliance' },
          { label: 'NBFIRA', href: '/compliance/nbfira' },
          { label: 'Annual' },
        ]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/compliance/nbfira"
              className="inline-flex items-center gap-1.5 text-sm font-medium"
              style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back
        </Link>

        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h3 className="font-semibold mb-3" style={{ color: theme.text }}>
            Annual Returns
          </h3>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                Existing return
              </label>
              <select value={activeId || ''} onChange={e => setActiveId(e.target.value || null)}
                      className="px-3 py-2 rounded-lg text-sm outline-none min-w-[220px]"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                <option value="">— select —</option>
                {list.map(r => (
                  <option key={r.id} value={r.id}>
                    {r.period_label} · {r.status} {r.company ? `· ${r.company}` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                New FY
              </label>
              <input value={period} onChange={e => setPeriod(e.target.value)}
                     placeholder="FY2026"
                     className="px-3 py-2 rounded-lg text-sm outline-none w-32 font-mono"
                     style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            </div>
            <button type="button" onClick={onCreate} disabled={busy !== null}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              {busy === 'create' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              {busy === 'create' ? 'Generating…' : 'Generate from GL'}
            </button>
            <button type="button" onClick={onRegenerate} disabled={!activeId || busy !== null}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                    style={{ background: theme.navy, color: '#fff' }}>
              {busy === 'regenerate' ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {busy === 'regenerate' ? 'Regenerating…' : 'Regenerate'}
            </button>
          </div>
          <p className="text-[11px] mt-3" style={{ color: theme.t2 }}>
            Period format <code>FY</code><code>YYYY</code> — ADIC FY = 1 Jul → 30 Jun. <strong>FY2026</strong> = 2025-07-01 to 2026-06-30.
            Annual return due 90 days after FY-end (Insurance Industry Regulations 2019, reg 31).
            Values in <strong>P'000</strong>.
          </p>
        </div>

        {err && (
          <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
               style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{err}</div>
          </div>
        )}

        {activeRet && (
          <div className="rounded-2xl p-5 space-y-3"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-baseline justify-between gap-3">
              <div>
                <h3 className="font-semibold" style={{ color: theme.text }}>
                  {activeRet.period_label} · {activeRet.status.toUpperCase()}
                </h3>
                <p className="text-xs" style={{ color: theme.t2 }}>
                  {activeRet.period_start} → {activeRet.period_end}
                  {activeRet.company ? ` · ${activeRet.company}` : ''}
                </p>
              </div>
              <span className="text-[11px]" style={{ color: theme.t3 }}>
                <Info className="w-3 h-3 inline mr-1" />
                Values in P'000.
              </span>
            </div>

            <WorkflowBar ret={activeRet}
                         onMutate={setActiveRet}
                         onOpenAudit={() => setAuditOpen(true)} />

            <div className="flex flex-wrap gap-2 mb-4 border-b" style={{ borderColor: theme.cardBdr }}>
              {SCHEDULE_TABS.map(t => (
                <button key={t.code} type="button" onClick={() => setTab(t.code)}
                        className="px-3 py-2 text-sm font-semibold transition-all"
                        style={{
                          color:     tab === t.code ? theme.orange : theme.t2,
                          borderBottom: `2px solid ${tab === t.code ? theme.orange : 'transparent'}`,
                          marginBottom: -1,
                        }}>
                  {t.title}
                </button>
              ))}
            </div>

            {loading && (
              <div className="py-8 text-center text-sm" style={{ color: theme.t2 }}>
                <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> loading…
              </div>
            )}

            {!loading && (
              <ScheduleTable lines={linesForTab} editable={editable} onEdit={onEditLine} />
            )}
          </div>
        )}
      </main>

      <AuditLogPanel returnId={activeId} open={auditOpen} onClose={() => setAuditOpen(false)} />
    </div>
  )
}
