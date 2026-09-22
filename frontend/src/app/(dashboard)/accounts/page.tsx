'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getAccounts, getToken } from '@/lib/api'
import type { Account } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Select } from '@/components/ui/input'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, parseAmount, today, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { ChevronDown, ChevronRight, Search, BookOpen, AlertCircle } from 'lucide-react'
import MaTreeView from './_MaTreeView'
import { MappingCell } from './_MappingCell'

// ─── Account type config ──────────────────────────────────────────────────────

const ACCOUNT_TYPE_ORDER = ['asset', 'liability', 'equity', 'revenue', 'expense']
const ACCOUNT_TYPE_LABELS: Record<string, string> = {
  asset: 'Assets', liability: 'Liabilities', equity: 'Equity',
  revenue: 'Revenue', expense: 'Expenses',
}
const ACCOUNT_TYPE_COLORS: Record<string, string> = {
  asset:     'text-[#2563EB]',
  liability: 'text-[#CC6C00]',
  equity:    'text-[#7C3AED]',
  revenue:   'text-[#059669]',
  expense:   'text-[#DC2626]',
}
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const ACCOUNT_TYPE_BG: Record<string, string> = {
  asset:     'bg-[#EFF6FF] border-[#BFDBFE]',
  liability: 'bg-[#FFF7ED] border-[#FED7AA]',
  equity:    'bg-[#F5F3FF] border-[#DDD6FE]',
  revenue:   'bg-[#ECFDF5] border-[#A7F3D0]',
  expense:   'bg-[#FEF2F2] border-[#FEE2E2]',
}

// ─── Chart of Accounts page ───────────────────────────────────────────────────

export default function AccountsPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  // CFO directive 2026-05-25 (COA-002 + COA-003): flat-list balances
  // must follow the topbar company selector and an as-of date. Same
  // pattern P&L + BS already use.
  const { selectedId: companyId } = useCompany()
  const [asOfDate, setAsOfDate] = useState<string>(today())
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  // CFO directive 2026-05-24: default to MA-format tree (CoA-as-SSOT-viewer).
  // The legacy flat-list view is still available behind the tab toggle.
  const [view, setView] = useState<'ma' | 'flat'>('ma')

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = { page_size: '500', as_of: asOfDate }
      if (typeFilter) params.account_type = typeFilter
      if (search) params.search = search
      if (companyId) params.company = companyId
      const res = await getAccounts(params)
      setAccounts(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load accounts')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeFilter, companyId, asOfDate])

  useEffect(() => {
    const timer = setTimeout(() => load(), 400)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  const grouped = accounts.reduce<Record<string, Account[]>>((acc, account) => {
    const type = account.account_type?.toLowerCase() || 'other'
    if (!acc[type]) acc[type] = []
    acc[type].push(account)
    return acc
  }, {})

  const toggleCollapse = (type: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  const types = ACCOUNT_TYPE_ORDER.filter((t) => grouped[t]?.length > 0)

  const getBalanceColor = (account: Account) => {
    if (!account.balance) return 'text-[#9CA3AF]'  // no activity → dash
    const bal = parseAmount(account.balance)
    const type = account.account_type?.toLowerCase()
    if (bal === 0) return 'text-[#9CA3AF]'
    if (type === 'asset' || type === 'expense') return bal > 0 ? 'text-[#111827]' : 'text-[#DC2626]'
    if (type === 'liability' || type === 'equity' || type === 'revenue')
      return bal > 0 ? 'text-[#059669]' : 'text-[#DC2626]'
    return 'text-[#374151]'
  }

  // Empty balance ("") = no activity → show "—" not "BWP 0.00".
  const renderBalance = (account: Account) => (
    !account.balance ? '—' : fmt(account.balance, account.currency)
  )

  const groupTotal = (accs: Account[]) =>
    accs.reduce((sum, a) => sum + (a.balance ? parseAmount(a.balance) : 0), 0)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Chart of Accounts"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Chart of Accounts' }]}
      />

      {/* Page hero — rebuilt Omni screens (2026-09-02). */}
      <div style={{ padding: '20px 24px 12px', background: '#F7F8FA' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 10.5, fontWeight: 650, letterSpacing: '0.11em', textTransform: 'uppercase', color: '#3F58CC' }}>
          <span style={{ width: 5, height: 5, borderRadius: 99, background: '#4F6BED' }} />
          Accounting &amp; Control
        </div>
        <h1 style={{ margin: '6px 0 0', fontSize: 25, lineHeight: 1.15, letterSpacing: '-0.03em', fontWeight: 680, color: '#1A1D21' }}>Chart of Accounts</h1>
        <p style={{ margin: '6px 0 0', fontSize: 14, color: '#6B7280', maxWidth: '62ch' }}>The tree mirrors the CFO MA workbook. Click an account to open its GL detail.</p>
      </div>

      {/* View toggle — MA (canonical) vs legacy flat list */}
      <div className="px-6 pt-4 flex items-center gap-2 border-b border-[#E5E7EB] bg-white">
        <button type="button" onClick={() => setView('ma')}
                className="relative pb-2.5 px-1 text-sm font-semibold transition-colors"
                style={{ color: view === 'ma' ? '#3F58CC' : '#6B7280' }}>
          MA Format
          {view === 'ma' && <span className="absolute left-0 right-0 -bottom-px h-0.5 bg-[#4F6BED]" />}
        </button>
        <button type="button" onClick={() => setView('flat')}
                className="relative pb-2.5 px-1 text-sm font-semibold transition-colors"
                style={{ color: view === 'flat' ? '#3F58CC' : '#6B7280' }}>
          Flat list (legacy)
          {view === 'flat' && <span className="absolute left-0 right-0 -bottom-px h-0.5 bg-[#4F6BED]" />}
        </button>
      </div>

      {view === 'ma' && <MaTreeView />}
      {view === 'flat' && (
      <div className="flex-1 p-6 space-y-4">
        {/* Filters */}
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
            <input
              type="text"
              placeholder="Search accounts..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md pl-9 pr-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
            />
          </div>
          <Select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="w-full sm:w-48">
            <option value="">All Types</option>
            <option value="asset">Assets</option>
            <option value="liability">Liabilities</option>
            <option value="equity">Equity</option>
            <option value="revenue">Revenue</option>
            <option value="expense">Expenses</option>
          </Select>
          <label className="flex items-center gap-2 text-xs text-[#374151]">
            <span className="font-medium whitespace-nowrap">Balances as of</span>
            <input
              type="date"
              value={asOfDate}
              onChange={(e) => setAsOfDate(e.target.value)}
              className="h-10 bg-white border border-[#D1D5DB] rounded-md px-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
            />
          </label>
          {loading && (
            <span className="self-center text-xs text-[#6B7280]">Loading…</span>
          )}
        </div>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-[#DC2626] flex-shrink-0" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {loading ? (
          <Card>
            <CardContent className="p-0">
              <LoadingTable rows={12} cols={4} />
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {types.length === 0 ? (
              <Card>
                <CardContent className="py-16 text-center">
                  <BookOpen className="w-8 h-8 text-[#D1D5DB] mx-auto mb-3" />
                  <p className="text-[#6B7280] font-medium">No accounts found</p>
                </CardContent>
              </Card>
            ) : (
              types.map((type) => {
                const typeAccounts = grouped[type] || []
                const isCollapsedGroup = collapsed.has(type)
                const total = groupTotal(typeAccounts)
                const label = ACCOUNT_TYPE_LABELS[type] || type
                const color = ACCOUNT_TYPE_COLORS[type] || 'text-[#6B7280]'

                return (
                  <Card key={type} className="overflow-hidden">
                    {/* Group header */}
                    <button
                      onClick={() => toggleCollapse(type)}
                      className={cn(
                        'w-full flex items-center justify-between px-5 py-3.5',
                        'hover:bg-[#F9FAFB] transition-colors text-left',
                        !isCollapsedGroup ? 'border-b border-[#E5E7EB]' : ''
                      )}
                    >
                      <div className="flex items-center gap-3">
                        {isCollapsedGroup ? (
                          <ChevronRight className="w-4 h-4 text-[#9CA3AF]" />
                        ) : (
                          <ChevronDown className="w-4 h-4 text-[#9CA3AF]" />
                        )}
                        <span className={cn('font-semibold text-sm', color)}>{label}</span>
                        <span className="text-xs text-[#9CA3AF]">
                          {typeAccounts.length} account{typeAccounts.length !== 1 ? 's' : ''}
                        </span>
                      </div>
                      <span className={cn('text-sm font-bold font-mono-nums', color)}>
                        {fmt(total)}
                      </span>
                    </button>

                    {/* Account rows */}
                    {!isCollapsedGroup && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm border-collapse">
                          <thead className="bg-[#F3F4F6]">
                            <tr>
                              <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider w-24">Code</th>
                              <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Account Name</th>
                              <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden sm:table-cell">Sub-type</th>
                              <th className="px-4 py-2.5 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Currency</th>
                              <th className="px-4 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Balance</th>
                              <th className="px-4 py-2.5 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">MA Mapping</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[#E5E7EB] bg-white">
                            {typeAccounts.map((account) => (
                              <tr
                                key={account.id}
                                onClick={() => router.push(`/accounts/${account.id}`)}
                                className="table-row-alt hover:bg-[#FFF7ED] cursor-pointer transition-colors"
                              >
                                <td className="px-4 py-2.5 font-mono text-xs text-[#6B7280] font-medium">{account.code}</td>
                                <td className="px-4 py-2.5 text-[#111827] font-medium">
                                  {account.name}
                                  {account.is_bank_account && (
                                    <span className="ml-2 text-xs text-[#2563EB] bg-[#EFF6FF] px-1.5 py-0.5 rounded">Bank</span>
                                  )}
                                  {!account.is_active && (
                                    <span className="ml-2 text-xs text-[#9CA3AF] bg-[#F3F4F6] px-1.5 py-0.5 rounded">Inactive</span>
                                  )}
                                </td>
                                <td className="px-4 py-2.5 text-[#6B7280] text-xs capitalize hidden sm:table-cell">
                                  {account.sub_type?.replace(/_/g, ' ')}
                                </td>
                                <td className="px-4 py-2.5 text-[#6B7280] text-xs hidden md:table-cell">{account.currency}</td>
                                <td className={cn('px-4 py-2.5 text-right font-mono-nums font-medium', getBalanceColor(account))}>
                                  {renderBalance(account)}
                                </td>
                                <td className="px-4 py-2.5 text-right">
                                  <MappingCell
                                    account={account}
                                    onSaved={(next) =>
                                      setAccounts(prev => prev.map(a =>
                                        a.id === next.id ? { ...a, ...next } : a))
                                    }
                                  />
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </Card>
                )
              })
            )}
          </div>
        )}
      </div>
      )}
    </div>
  )
}
