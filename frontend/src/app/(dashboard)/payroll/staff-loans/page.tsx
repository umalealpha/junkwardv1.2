'use client'
/**
 * /payroll/staff-loans — the Financial Controller enters each staff member's
 * loan balance as at a month; Omni deducts it from then on (CFO 2026-08-28).
 * Human-entered opening balances (net, flat interest already included) → the
 * existing monthly loan engine does the rest. Omni never moves money.
 */
import { useEffect, useState, useCallback } from 'react'
import { apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Plus, Trash2, Save, Loader2, CheckCircle2 } from 'lucide-react'

interface Period { id: string; period_name: string; status?: string }
interface Loan {
  id: string; employee: string; employee_number: string
  principal: string; outstanding: string; monthly_instalment: string
  term_months: number; start_period: string | null
}
interface Row { employee_number: string; net_balance: string; monthly_deduction: string }

const money = (v: string) => `BWP ${Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function StaffLoansPage() {
  const [periods, setPeriods] = useState<Period[]>([])
  const [startId, setStartId] = useState('')
  const [rows, setRows] = useState<Row[]>([{ employee_number: '', net_balance: '', monthly_deduction: '' }])
  const [loans, setLoans] = useState<Loan[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const loadLoans = useCallback(async () => {
    try {
      const d = await apiFetch<{ loans: Loan[] }>('/payroll/staff-loans/')
      setLoans(d.loans || [])
    } catch { /* payroll-authority gate handled by the page shell */ }
  }, [])

  useEffect(() => {
    apiFetch<{ results?: Period[] } | Period[]>('/payroll-periods/')
      .then(d => {
        const list = Array.isArray(d) ? d : (d.results || [])
        setPeriods(list)
        const open = list.find(p => p.status === 'open') || list[0]
        if (open) setStartId(open.id)
      }).catch(() => {})
    loadLoans()
  }, [loadLoans])

  const setRow = (i: number, k: keyof Row, v: string) =>
    setRows(rs => rs.map((r, j) => j === i ? { ...r, [k]: v } : r))
  const addRow = () => setRows(rs => [...rs, { employee_number: '', net_balance: '', monthly_deduction: '' }])
  const delRow = (i: number) => setRows(rs => rs.filter((_, j) => j !== i))

  async function save() {
    const clean = rows.filter(r => r.employee_number.trim() && r.net_balance.trim() && r.monthly_deduction.trim())
    if (!startId) { setMsg({ ok: false, text: 'Pick the month deductions start from.' }); return }
    if (!clean.length) { setMsg({ ok: false, text: 'Enter at least one employee balance.' }); return }
    setBusy(true); setMsg(null)
    try {
      const d = await apiFetch<{ created_count: number; skipped_count: number; skipped: { ref?: string; employee?: string; reason: string }[] }>(
        '/payroll/staff-loans/opening-balance/',
        { method: 'POST', body: JSON.stringify({ start_period_id: startId, rows: clean }) })
      const skips = (d.skipped || []).map(s => `${s.employee || s.ref || '?'}: ${s.reason}`).join('; ')
      setMsg({ ok: d.created_count > 0, text: `Saved ${d.created_count} balance(s).${d.skipped_count ? ` Skipped ${d.skipped_count} — ${skips}` : ''}` })
      if (d.created_count > 0) setRows([{ employee_number: '', net_balance: '', monthly_deduction: '' }])
      loadLoans()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : 'Save failed' })
    } finally { setBusy(false) }
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <TopBar title="Staff loan balances" />
      <div className="max-w-5xl mx-auto p-4 sm:p-6 space-y-6">
        <div className="rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-5 space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Enter the balance as at a month</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">Type each person&apos;s NET balance (interest already included) and the monthly deduction. Omni deducts it from the chosen month until it clears. It never pays anyone — this only sets up the deduction.</p>
          </div>
          <div className="flex items-center gap-3">
            <label className="text-sm text-gray-600 dark:text-gray-300">Deduct from</label>
            <select value={startId} onChange={e => setStartId(e.target.value)}
                    className="rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100">
              <option value="">— pick month —</option>
              {periods.map(p => <option key={p.id} value={p.id}>{p.period_name}</option>)}
            </select>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-gray-500 dark:text-gray-400 text-left">
                <th className="pb-2 pr-3">Employee number</th><th className="pb-2 pr-3">Net balance</th><th className="pb-2 pr-3">Monthly deduction</th><th></th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td className="py-1 pr-3"><input value={r.employee_number} onChange={e => setRow(i, 'employee_number', e.target.value)} placeholder="e.g. ADI-0241" className="w-40 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 text-gray-900 dark:text-gray-100" /></td>
                    <td className="py-1 pr-3"><input value={r.net_balance} onChange={e => setRow(i, 'net_balance', e.target.value)} placeholder="0.00" inputMode="decimal" className="w-32 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 tabular-nums text-gray-900 dark:text-gray-100" /></td>
                    <td className="py-1 pr-3"><input value={r.monthly_deduction} onChange={e => setRow(i, 'monthly_deduction', e.target.value)} placeholder="0.00" inputMode="decimal" className="w-32 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 tabular-nums text-gray-900 dark:text-gray-100" /></td>
                    <td className="py-1">{rows.length > 1 && <button onClick={() => delRow(i)} className="text-gray-400 hover:text-red-600"><Trash2 className="w-4 h-4" /></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={addRow} className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-700 dark:text-gray-200"><Plus className="w-4 h-4" /> Add employee</button>
            <div className="flex-1" />
            <button onClick={save} disabled={busy} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white disabled:opacity-50" style={{ background: '#0D1B2A' }}>
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}{busy ? 'Saving…' : 'Save balances'}
            </button>
          </div>
          {msg && <div className={`rounded-lg p-3 text-sm ${msg.ok ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300' : 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300'}`}>{msg.text}</div>}
        </div>

        <div className="rounded-2xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-5 space-y-3">
          <h3 className="font-semibold text-gray-900 dark:text-gray-100 inline-flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4" style={{ color: '#F4A623' }} /> Current loan balances ({loans.length})</h3>
          {loans.length === 0 ? <p className="text-sm text-gray-500 dark:text-gray-400">No active staff-loan balances yet.</p> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="text-gray-500 dark:text-gray-400 text-left">
                  <th className="pb-2 pr-3">Employee</th><th className="pb-2 pr-3 text-right">Outstanding</th><th className="pb-2 pr-3 text-right">Monthly</th><th className="pb-2 pr-3 text-right">Months left</th><th className="pb-2">From</th>
                </tr></thead>
                <tbody>
                  {loans.map(l => (
                    <tr key={l.id} className="border-t border-gray-100 dark:border-gray-800">
                      <td className="py-2 pr-3 text-gray-900 dark:text-gray-100">{l.employee}{l.employee_number ? ` · ${l.employee_number}` : ''}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-gray-900 dark:text-gray-100">{money(l.outstanding)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-gray-700 dark:text-gray-300">{money(l.monthly_instalment)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-gray-700 dark:text-gray-300">{l.term_months}</td>
                      <td className="py-2 text-gray-500 dark:text-gray-400">{l.start_period || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
