'use client'

/**
 * /hris/payroll-setup — Payslip Components → GL account mapping.
 *
 * Built 2026-06-09 after Oprah flagged that the Payslip Components list wasn't
 * reachable from the HRIS menu, so Team A couldn't do the payroll GL mapping.
 * The backend already exposes /api/v1/payslip-components/ (list open, write =
 * approver). This page surfaces every component, shows which have NO posting
 * account (the blockers), and lets an approver set the GL code with a picker
 * of live accounts. Sits under /hris so it inherits the unlock gate + menu.
 */
import { useEffect, useMemo, useState } from 'react'
import { listPayslipComponents, updatePayslipComponent, getAllAccounts } from '@/lib/api'
import type { PayslipComponentRow, Account } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Wallet, RefreshCw, CheckCircle2, AlertTriangle, AlertCircle } from 'lucide-react'

const KIND_HINT: Record<string, string> = {
  earning:             'Earning → expense account (e.g. 110010 Salaries & Wages)',
  earning_non_taxable: 'Non-taxable earning → expense account',
  employee_deduction:  'Deduction → liability / payable account',
  employee_pretax:     'Pre-tax deduction → liability / payable account',
  company_contribution:'Employer cost → expense account (auto-balances to 2151)',
  tax:                 'PAYE → liability account (2160 PAYE Payable)',
}

export default function PayrollSetupPage() {
  const [rows, setRows] = useState<PayslipComponentRow[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<Record<string, 'saving' | 'ok' | string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true); setError(null)
    try {
      const [comps, accs] = await Promise.all([
        listPayslipComponents(),
        // ALL active accounts — paginate past the 100-row cap (bug c369818a)
        getAllAccounts({ ordering: 'code' }).catch(() => [] as Account[]),
      ])
      setRows(comps)
      setAccounts(accs.filter(a => a.is_active))
      setDraft(Object.fromEntries(comps.map(c => [c.id, c.posting_account_code || ''])))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load payslip components')
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  // components that actually post a JE line and still have no GL account
  const journalisable = (k: string) => !k.startsWith('computed')
  const unmapped = rows.filter(r => journalisable(r.kind) && !r.posting_account_code).length

  const acctName = (code: string) => accounts.find(a => a.code === code)?.name || ''

  async function save(row: PayslipComponentRow) {
    const code = (draft[row.id] || '').trim()
    setSaving(s => ({ ...s, [row.id]: 'saving' }))
    try {
      await updatePayslipComponent(row.id, { posting_account_code: code })
      setRows(rs => rs.map(r => r.id === row.id ? { ...r, posting_account_code: code } : r))
      setSaving(s => ({ ...s, [row.id]: 'ok' }))
      setTimeout(() => setSaving(s => { const n = { ...s }; delete n[row.id]; return n }), 1500)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      setSaving(s => ({ ...s, [row.id]: msg.includes('403') || msg.toLowerCase().includes('approver') ? 'Needs approver role (CFO/FM)' : msg }))
    }
  }

  const grouped = useMemo(() => {
    const order = ['earning', 'earning_non_taxable', 'employee_pretax', 'employee_deduction', 'tax', 'company_contribution', 'computed_gross', 'computed_net', 'computed_ctc']
    const by: Record<string, PayslipComponentRow[]> = {}
    for (const r of rows) (by[r.kind] ||= []).push(r)
    return order.filter(k => by[k]?.length).map(k => ({ kind: k, label: by[k][0].kind_display, items: by[k] }))
  }, [rows])

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      <TopBar />
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-lg bg-[#0D1B2A] flex items-center justify-center">
            <Wallet className="w-5 h-5 text-[#F4A623]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">Payroll · GL Mapping</h1>
            <p className="text-sm text-[#6B7280]">Give every payslip line a GL account so payroll can post.</p>
          </div>
          <Button variant="outline" className="ml-auto" onClick={load} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>

        {/* progress banner */}
        <div className="mt-3 rounded-lg px-4 py-3 text-sm flex items-center gap-2"
             style={{ background: unmapped ? '#FFFBEB' : '#ECFDF5', border: `1px solid ${unmapped ? '#FDE68A' : '#A7F3D0'}` }}>
          {unmapped
            ? <><AlertTriangle className="w-4 h-4 text-[#B45309]" /><span className="text-[#92400E]"><b>{unmapped}</b> payslip component{unmapped === 1 ? '' : 's'} still have no GL account. Payroll with those lines can&apos;t post until they&apos;re mapped.</span></>
            : <><CheckCircle2 className="w-4 h-4 text-[#059669]" /><span className="text-[#065F46]">All journalisable components are mapped. Payroll can post.</span></>}
        </div>

        {error && <div className="mt-3 text-sm text-[#B91C1C] flex items-center gap-2"><AlertCircle className="w-4 h-4" />{error}</div>}

        {/* GL accounts datalist for the picker */}
        <datalist id="gl-accounts">
          {accounts.map(a => <option key={a.id} value={a.code}>{a.code} — {a.name}</option>)}
        </datalist>

        {loading ? (
          <p className="mt-6 text-sm text-[#6B7280]">Loading…</p>
        ) : grouped.map(g => (
          <Card key={g.kind} className="mt-4">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">{g.label}
                <span className="text-xs font-normal text-[#9CA3AF]">— {KIND_HINT[g.kind] || 'no GL line (subtotal)'}</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-1">
                {g.items.map(row => {
                  const isComputed = row.kind.startsWith('computed')
                  const needs = !isComputed && !row.posting_account_code
                  const st = saving[row.id]
                  return (
                    <div key={row.id} className="flex items-center gap-3 py-1.5 border-b border-[#F3F4F6] last:border-0">
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: isComputed ? '#D1D5DB' : (row.posting_account_code ? '#10B981' : '#EF4444') }} />
                      <div className="w-56 flex-shrink-0">
                        <div className="text-sm font-medium text-[#0D1B2A]">{row.name}</div>
                        <div className="text-xs text-[#9CA3AF] font-mono">{row.code}</div>
                      </div>
                      {isComputed ? (
                        <div className="flex-1 text-xs text-[#9CA3AF]">Subtotal — no GL posting</div>
                      ) : (
                        <>
                          <input
                            list="gl-accounts"
                            className={`flex-1 h-9 px-3 border rounded text-sm ${needs ? 'border-[#FCA5A5] bg-[#FEF2F2]' : 'border-[#D1D5DB]'}`}
                            placeholder="GL account code (type or pick)"
                            value={draft[row.id] ?? ''}
                            onChange={e => setDraft(d => ({ ...d, [row.id]: e.target.value }))}
                          />
                          <div className="w-44 text-xs text-[#6B7280] truncate">{
                            // client lookup (full CoA) for live typing; fall back to the
                            // server-resolved saved name if the list failed to load (bug c369818a)
                            acctName(draft[row.id] || '')
                            || ((draft[row.id] || '') === (row.posting_account_code || '') ? (row.posting_account_name || '') : '')
                          }</div>
                          <Button size="sm" variant={needs ? 'primary' : 'outline'}
                                  disabled={st === 'saving' || (draft[row.id] || '') === (row.posting_account_code || '')}
                                  onClick={() => save(row)}>
                            {st === 'saving' ? 'Saving…' : st === 'ok' ? <CheckCircle2 className="w-4 h-4 text-[#059669]" /> : 'Save'}
                          </Button>
                          {st && st !== 'saving' && st !== 'ok' && <span className="text-xs text-[#B91C1C]">{st}</span>}
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
