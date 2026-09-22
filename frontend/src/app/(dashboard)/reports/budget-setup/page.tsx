'use client'

/**
 * /reports/budget-setup — enter or upload budget figures by GL account and
 * period. Feeds the Budget vs Actual report (bug 5ccd4c77, Oprah 2026-06-17).
 *
 * Two ways in:
 *   1. Manual grid — pick a period + department, type an amount per revenue /
 *      expense account, Save (POST /budgets/set-lines/).
 *   2. File upload — CSV/XLSX with columns account_code | amount [| notes]
 *      (POST /budgets/upload/). A one-click template is provided.
 *
 * Writes are Finance-only server-side; a non-Finance user sees a clear notice.
 */
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  Calculator, Download, UploadCloud, Save, Loader2, RefreshCw,
  CheckCircle2, AlertTriangle, ChevronLeft, Search, Lock, Unlock,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getFiscalPeriods, getAllAccounts, getBudgets, getBudget,
  setBudgetLines, uploadBudget, approveBudget, reviseBudget,
  type FiscalPeriod, type Account, type BudgetSaveResult,
} from '@/lib/api'

// Budget.Department choices — values MUST match the model (lowercase).
const DEPARTMENTS = [
  { value: 'master', label: 'Master (Company-wide)' },
  { value: 'finance', label: 'Finance' },
  { value: 'claims', label: 'Claims' },
  { value: 'underwriting', label: 'Underwriting' },
  { value: 'health', label: 'Health' },
  { value: 'bd', label: 'Business Development' },
  { value: 'operations', label: 'Operations' },
  { value: 'it', label: 'IT' },
]

export default function BudgetSetupPage() {
  const { theme } = useTheme()
  const [periods, setPeriods] = useState<FiscalPeriod[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [periodId, setPeriodId] = useState('')
  const [department, setDepartment] = useState('master')
  const [amounts, setAmounts] = useState<Record<string, string>>({})   // account id -> amount
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [locking, setLocking] = useState(false)
  const [budgetId, setBudgetId] = useState<string | null>(null)
  const [bstatus, setBstatus] = useState<string>('')          // '', draft, approved, revised
  const [approvedBy, setApprovedBy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const locked = bstatus === 'approved'

  // Load periods + the budgetable (revenue/expense) accounts once.
  useEffect(() => {
    Promise.all([
      getFiscalPeriods({ page_size: '200' }).then(r => r.results).catch(() => []),
      getAllAccounts({ ordering: 'code' }).catch(() => []),
    ]).then(([p, a]) => {
      setPeriods(p)
      setAccounts(a.filter(x => ['revenue', 'expense'].includes((x.account_type || '').toLowerCase())))
      if (p.length && !periodId) setPeriodId(p[0].id)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Load the existing budget for (period, department) into the grid.
  function loadExisting() {
    if (!periodId) return
    setLoading(true); setMsg(null); setErr(null)
    getBudgets({ fiscal_period: periodId, department })
      .then(async r => {
        const head = r.results[0]
        const next: Record<string, string> = {}
        if (head) {
          setBudgetId(head.id); setBstatus(head.status || 'draft')
          const detail = await getBudget(head.id)
          setApprovedBy(detail.approved_by_name || null)
          for (const l of detail.lines) {
            // match line back to an account id via code
            const acc = accounts.find(a => a.code === l.account_code)
            if (acc) next[acc.id] = l.amount
          }
        } else {
          setBudgetId(null); setBstatus(''); setApprovedBy(null)
        }
        setAmounts(next)
      })
      .catch(e => setErr(e instanceof Error ? e.message : 'Failed to load budget'))
      .finally(() => setLoading(false))
  }
  useEffect(() => { if (periodId && accounts.length) loadExisting() }, [periodId, department, accounts.length])  // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    const rows = q
      ? accounts.filter(a => a.code.toLowerCase().includes(q) || a.name.toLowerCase().includes(q))
      : accounts
    return {
      revenue: rows.filter(a => (a.account_type || '').toLowerCase() === 'revenue'),
      expense: rows.filter(a => (a.account_type || '').toLowerCase() === 'expense'),
    }
  }, [accounts, search])

  const total = useMemo(() => {
    let rev = 0, exp = 0
    for (const a of accounts) {
      const v = parseFloat(amounts[a.id] || '0') || 0
      if ((a.account_type || '').toLowerCase() === 'revenue') rev += v
      else exp += v
    }
    return { rev, exp }
  }, [accounts, amounts])

  function applyResult(res: BudgetSaveResult) {
    setMsg(res.message || `${res.applied} line(s) saved.`)
    if (res.errors?.length) setErr(`${res.errors.length} row(s) skipped — ${res.errors.slice(0, 3).map(e => e.error).join('; ')}`)
  }

  function save() {
    if (!periodId) return
    setSaving(true); setMsg(null); setErr(null)
    const lines = accounts
      .map(a => ({ account: a.id, amount: amounts[a.id] || '' }))
      .filter(l => l.amount.trim() !== '' && parseFloat(l.amount) !== 0)
    setBudgetLines({ fiscal_period: periodId, department, lines })
      .then(applyResult)
      .catch(e => setErr(e instanceof Error ? e.message : 'Save failed'))
      .finally(() => setSaving(false))
  }

  function onUpload(file: File | null) {
    if (!file || !periodId) return
    setUploading(true); setMsg(null); setErr(null)
    uploadBudget(periodId, department, file)
      .then(res => { applyResult(res); loadExisting() })
      .catch(e => setErr(e instanceof Error ? e.message : 'Upload failed'))
      .finally(() => setUploading(false))
  }

  function approveLock() {
    if (!budgetId) { setErr('Save the budget before approving.'); return }
    if (!confirm('Approve and LOCK this budget? After this no one can change the figures until it is explicitly revised (which is logged).')) return
    setLocking(true); setMsg(null); setErr(null)
    approveBudget(budgetId)
      .then(() => { setMsg('Budget approved and locked.'); loadExisting() })
      .catch(e => setErr(e instanceof Error ? e.message : 'Approve failed'))
      .finally(() => setLocking(false))
  }

  function reviseUnlock() {
    if (!budgetId) return
    if (!confirm('Unlock this approved budget for editing? This is recorded in the audit trail.')) return
    setLocking(true); setMsg(null); setErr(null)
    reviseBudget(budgetId)
      .then(() => { setMsg('Budget unlocked for revision.'); loadExisting() })
      .catch(e => setErr(e instanceof Error ? e.message : 'Revise failed'))
      .finally(() => setLocking(false))
  }

  function downloadTemplate() {
    const rows = [['account_code', 'amount', 'notes'],
      ...accounts.slice(0, 5).map(a => [a.code, '0', ''])]
    const csv = rows.map(r => r.join(',')).join('\n') + '\n'
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = 'budget_template.csv'; link.click()
    URL.revokeObjectURL(url)
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const input = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  const renderRows = (rows: Account[]) => rows.map(a => (
    <tr key={a.id} style={{ borderTop: `1px solid ${theme.cardBdr}55` }}>
      <td className="px-3 py-1.5 font-mono text-xs" style={{ color: theme.t2 }}>{a.code}</td>
      <td className="px-3 py-1.5 text-sm" style={{ color: theme.text }}>{a.name}</td>
      <td className="px-3 py-1.5 text-right">
        <input
          inputMode="decimal"
          value={amounts[a.id] ?? ''}
          disabled={locked}
          onChange={e => setAmounts(s => ({ ...s, [a.id]: e.target.value.replace(/[^0-9.\-]/g, '') }))}
          placeholder="0.00"
          className="w-32 px-2 py-1 rounded-md text-sm text-right outline-none tabular-nums disabled:opacity-50"
          style={input}
        />
      </td>
    </tr>
  ))

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Budget Setup"
              breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Budget Setup' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/reports/budget-vs-actual" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Budget vs Actual report
        </Link>

        {/* Controls */}
        <div className="rounded-2xl p-4 space-y-3" style={card}>
          <div className="flex items-center gap-2 flex-wrap">
            <Calculator className="w-4 h-4" style={{ color: theme.orange }} />
            <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Budget for period &amp; department</h3>
            {bstatus && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide"
                    style={locked
                      ? { background: '#05966922', color: '#059669' }
                      : { background: theme.g100, color: theme.t2 }}>
                {locked ? <Lock className="w-3 h-3" /> : null}
                {bstatus}{locked && approvedBy ? ` · by ${approvedBy}` : ''}
              </span>
            )}
            <div className="flex-1" />
            {locked ? (
              <button onClick={reviseUnlock} disabled={locking}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-50"
                      style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
                {locking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Unlock className="w-3.5 h-3.5" />}
                Revise (unlock)
              </button>
            ) : (
              <button onClick={approveLock} disabled={locking || !budgetId}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-40"
                      style={{ background: '#059669', color: '#fff' }}>
                {locking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Lock className="w-3.5 h-3.5" />}
                Approve &amp; lock
              </button>
            )}
          </div>
          {locked && (
            <div className="text-xs rounded-lg px-3 py-2" style={{ background: '#05966911', color: '#059669' }}>
              This budget is approved and locked — figures can&apos;t be changed. Click <b>Revise (unlock)</b> to edit; every unlock and change is recorded in the audit trail.
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Period</label>
              <select value={periodId} onChange={e => setPeriodId(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={input}>
                {periods.map(p => <option key={p.id} value={p.id}>{p.period_name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Department</label>
              <select value={department} onChange={e => setDepartment(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={input}>
                {DEPARTMENTS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </div>
            <div className="flex items-end">
              <button onClick={save} disabled={saving || loading || locked}
                      title={locked ? 'Budget is locked — Revise to edit' : ''}
                      className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50 w-full justify-center"
                      style={{ background: theme.orange, color: '#fff' }}>
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Save budget
              </button>
            </div>
          </div>
        </div>

        {/* Upload */}
        <div className="rounded-2xl p-4 space-y-2" style={card}>
          <div className="flex items-center gap-2">
            <UploadCloud className="w-4 h-4" style={{ color: theme.orange }} />
            <h4 className="font-semibold text-sm" style={{ color: theme.text }}>Or upload a budget file</h4>
          </div>
          <p className="text-xs" style={{ color: theme.t2 }}>
            CSV or Excel with columns <span className="font-mono">account_code · amount · notes</span>. Loads into the period &amp; department selected above.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={downloadTemplate}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              <Download className="w-3.5 h-3.5" /> Template
            </button>
            <input type="file" accept=".csv,.xlsx,.xlsm" disabled={locked || uploading}
                   onChange={e => onUpload(e.target.files?.[0] || null)}
                   className="text-xs disabled:opacity-50" style={{ color: theme.t2 }} />
            {uploading && <Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.orange }} />}
            {locked && <span className="text-xs" style={{ color: theme.t2 }}>(locked — revise to upload)</span>}
          </div>
        </div>

        {msg && (
          <div className="flex items-center gap-1.5 text-sm rounded-lg px-3 py-2"
               style={{ background: theme.g100, color: '#16a34a' }}>
            <CheckCircle2 className="w-4 h-4" /> {msg}
          </div>
        )}
        {err && (
          <div className="flex items-start gap-1.5 text-sm rounded-lg px-3 py-2"
               style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {err}
          </div>
        )}

        {/* Grid */}
        <div className="rounded-2xl overflow-hidden" style={card}>
          <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
            <div className="relative flex-1 max-w-xs">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: theme.t2 }} />
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Filter accounts…"
                     className="w-full pl-8 pr-3 py-1.5 rounded-lg text-sm outline-none" style={input} />
            </div>
            <button onClick={loadExisting} className="inline-flex items-center gap-1.5 text-xs font-medium" style={{ color: theme.t2 }}>
              <RefreshCw className="w-3.5 h-3.5" /> Reload
            </button>
            <div className="flex-1" />
            <span className="text-xs tabular-nums" style={{ color: theme.t2 }}>
              Revenue budget <b style={{ color: theme.text }}>{total.rev.toLocaleString()}</b> · Expense budget <b style={{ color: theme.text }}>{total.exp.toLocaleString()}</b>
            </span>
          </div>
          {loading ? (
            <div className="px-4 py-10 text-center text-sm" style={{ color: theme.t2 }}>
              <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> Loading…
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                    {['Code', 'Account', 'Budget amount'].map((h, i) => (
                      <th key={h} className={`px-3 py-2 text-[11px] uppercase tracking-wider font-semibold ${i === 2 ? 'text-right' : ''}`}
                          style={{ color: theme.t2 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr><td colSpan={3} className="px-3 py-1.5 text-[11px] uppercase tracking-wide font-semibold" style={{ background: theme.g100, color: theme.orange }}>Revenue</td></tr>
                  {renderRows(visible.revenue)}
                  <tr><td colSpan={3} className="px-3 py-1.5 text-[11px] uppercase tracking-wide font-semibold" style={{ background: theme.g100, color: theme.orange }}>Expenses</td></tr>
                  {renderRows(visible.expense)}
                  {accounts.length === 0 && (
                    <tr><td colSpan={3} className="px-3 py-8 text-center text-sm" style={{ color: theme.t2 }}>No revenue/expense accounts found.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <p className="text-[11px]" style={{ color: theme.t2 }}>
          Budgets are Finance-editable. Once saved they appear immediately in the
          Budget vs Actual report for the same period &amp; department.
        </p>
      </main>
    </div>
  )
}
