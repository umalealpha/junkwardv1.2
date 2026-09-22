'use client'

/**
 * /accounts — MA-format Chart of Accounts tree (CFO directive 2026-05-24).
 *
 * The CoA page becomes the single-source-of-truth viewer: top-level
 * sections mirror the MA workbook exactly (Current Assets, Non-Current
 * Assets, Current Liabilities, Non-current Liabilities, Equity, plus
 * P&L sections), middle level = MA line label (e.g. "Trade Receivables"),
 * leaf = the GL accounts with their running balance.
 *
 * Each section is collapsible. Subtotal sits in the section header so the
 * tree reads like a small balance sheet on its own. Click an account row
 * to drill into the General Ledger for that account.
 *
 * Dashboard tiles will deep-link here via ?expand=<section_id> (e.g.
 * `/accounts?expand=current_assets` opens with Current Assets open).
 */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { ChevronDown, ChevronRight, AlertCircle, Loader2, FileQuestion } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { getCoaMaTree, type CoaMaTree, type CoaMaSection, type CoaMaLine } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { formatAmount } from '@/lib/utils'
import { FyPresetChips } from '@/components/finance/FyPresetChips'

const SECTION_TONE: Record<string, { bar: string; chip: string }> = {
  asset:     { bar: '#2563EB', chip: 'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]' },
  liability: { bar: '#CC6C00', chip: 'bg-[#FFF7ED] text-[#9A3412] border-[#FED7AA]' },
  equity:    { bar: '#7C3AED', chip: 'bg-[#F5F3FF] text-[#6D28D9] border-[#DDD6FE]' },
  pl:        { bar: '#059669', chip: 'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]' },
}

export default function MaTreeView() {
  const search = useSearchParams()
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId } = useCompany()
  const fmt = (n: string | number) => formatAmount(n, 'BWP', mode)

  const [data, setData]   = useState<CoaMaTree | null>(null)
  const [err, setErr]     = useState<string | null>(null)
  const [loading, setLoad]= useState(true)
  const [open, setOpen]   = useState<Set<string>>(new Set())
  const [openLine, setOpenLine] = useState<Set<string>>(new Set())
  const [period, setPeriod] = useState<{ from?: string; to?: string }>({})

  // Honour `?expand=<section_id>` so tiles can deep-link in.
  useEffect(() => {
    const ex = search?.get('expand')
    if (ex) setOpen(new Set(ex.split(',')))
  }, [search])

  useEffect(() => {
    let cancelled = false
    setLoad(true); setErr(null)
    getCoaMaTree(period.to, companyId, period.from)
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => !cancelled && setErr(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => !cancelled && setLoad(false))
    return () => { cancelled = true }
  }, [companyId, period.from, period.to])

  function toggleSection(id: string) {
    setOpen(p => {
      const n = new Set(p)
      if (n.has(id)) n.delete(id); else n.add(id)
      return n
    })
  }
  function toggleLine(key: string) {
    setOpenLine(p => {
      const n = new Set(p)
      if (n.has(key)) n.delete(key); else n.add(key)
      return n
    })
  }

  if (loading) {
    return <div className="p-8 text-sm text-[#6B7280] flex items-center gap-2">
      <Loader2 className="w-4 h-4 animate-spin" /> Loading MA tree…
    </div>
  }
  if (err) {
    return <div className="m-6 rounded-xl p-4 bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B] flex items-center gap-2">
      <AlertCircle className="w-5 h-5" /> {err}
    </div>
  }
  if (!data) return null

  const renderSection = (sec: CoaMaSection) => {
    const isOpen = open.has(sec.id)
    const tone = SECTION_TONE[sec.side] || SECTION_TONE.asset
    return (
      <div key={sec.id} className="rounded-xl bg-white border border-[#E5E7EB] overflow-hidden">
        <button
          type="button"
          onClick={() => toggleSection(sec.id)}
          className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[#F9FAFB] transition-colors"
        >
          <span className="w-1.5 h-8 rounded-full" style={{ background: tone.bar }} />
          {isOpen ? <ChevronDown className="w-4 h-4 text-[#6B7280]" /> : <ChevronRight className="w-4 h-4 text-[#6B7280]" />}
          <span className="flex-1 text-left font-semibold text-[#0D1B2A]">{sec.label}</span>
          <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${tone.chip}`}>
            {sec.side}
          </span>
          <span className="font-mono tabular-nums text-[#0D1B2A] font-semibold">{fmt(sec.subtotal_bwp)}</span>
        </button>
        {isOpen && (
          <div className="border-t border-[#F3F4F6] divide-y divide-[#F3F4F6]">
            {sec.lines.length === 0 && (
              <div className="px-12 py-3 text-xs text-[#9CA3AF] italic">No accounts in this section.</div>
            )}
            {sec.lines.map(line => {
              const lineKey = `${sec.id}::${line.label}`
              const lineOpen = openLine.has(lineKey)
              const noChildren = line.accounts.length === 0
              return (
                <div key={lineKey}>
                  <button
                    type="button"
                    onClick={() => !noChildren && toggleLine(lineKey)}
                    className={`w-full flex items-center gap-3 pl-12 pr-4 py-2 ${noChildren ? 'cursor-default' : 'hover:bg-[#F9FAFB]'} transition-colors`}
                  >
                    {noChildren
                      ? <span className="w-4 h-4" />
                      : (lineOpen ? <ChevronDown className="w-3.5 h-3.5 text-[#9CA3AF]" /> : <ChevronRight className="w-3.5 h-3.5 text-[#9CA3AF]" />)
                    }
                    <span className="flex-1 text-left text-sm text-[#374151]">{line.label}</span>
                    <span className="text-xs text-[#9CA3AF]">{line.accounts.length} acct{line.accounts.length === 1 ? '' : 's'}</span>
                    <span className="font-mono tabular-nums text-[#0D1B2A] text-sm w-32 text-right">{fmt(line.subtotal_bwp)}</span>
                  </button>
                  {lineOpen && !noChildren && (
                    <div className="bg-[#FAFBFC] divide-y divide-[#F3F4F6]">
                      {line.accounts.map(acc => (
                        <Link
                          key={acc.code}
                          href={`/reports/general-ledger?account=${encodeURIComponent(acc.code)}${companyId ? `&company=${companyId}` : ''}`}
                          className="flex items-center gap-3 pl-20 pr-4 py-1.5 hover:bg-[#F3F4F6] transition-colors"
                        >
                          <span className="font-mono text-xs text-[#6B7280] w-20">{acc.code}</span>
                          <span className="flex-1 text-sm text-[#374151]">{acc.name}</span>
                          <span className="text-[10px] text-[#9CA3AF] font-mono">{acc.sub_type}</span>
                          <span className="font-mono tabular-nums text-sm text-[#0D1B2A] w-32 text-right">{fmt(acc.balance_bwp)}</span>
                        </Link>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="p-6 space-y-4">
      <Card>
        <CardContent className="p-3">
          <FyPresetChips
            activeFrom={data.from_date}
            activeTo={data.as_of}
            onApply={(from, to) => setPeriod({ from, to })}
          />
        </CardContent>
      </Card>
      <Card className="bg-[#F0F9FF] border-[#BAE6FD]">
        <CardContent className="p-3 text-xs text-[#075985] flex items-center gap-2">
          <span className="font-semibold">MA-format view.</span>
          <span>Tree mirrors the CFO MA workbook. <strong>BS</strong> is cumulative as of {data.as_of}. <strong>P&amp;L</strong> is activity from {data.from_date} → {data.as_of}. Click an account to open its GL detail.</span>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[#6B7280] px-1">Balance Sheet</h3>
        {data.bs.map(renderSection)}
      </div>

      <div className="space-y-3 pt-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[#6B7280] px-1">Profit &amp; Loss</h3>
        {data.pl.map(renderSection)}
      </div>

      {data.unmapped.length > 0 && (
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              <FileQuestion className="w-4 h-4 text-[#F59E0B]" />
              <h3 className="font-semibold text-[#0D1B2A]">Unmapped accounts <span className="text-xs font-normal text-[#9CA3AF]">({data.unmapped.length}) — Finance must set fs_line_item via admin so they show up in the tree.</span></h3>
            </div>
            <div className="divide-y divide-[#F3F4F6] max-h-72 overflow-y-auto">
              {data.unmapped.map(a => (
                <Link
                  key={a.code}
                  href={`/reports/general-ledger?account=${encodeURIComponent(a.code)}${companyId ? `&company=${companyId}` : ''}`}
                  className="flex items-center gap-3 px-2 py-1.5 hover:bg-[#F9FAFB]"
                >
                  <span className="font-mono text-xs text-[#6B7280] w-20">{a.code}</span>
                  <span className="flex-1 text-sm text-[#374151]">{a.name}</span>
                  <span className="text-[10px] text-[#9CA3AF] font-mono">{a.account_type} · {a.sub_type}</span>
                  <span className="font-mono tabular-nums text-sm text-[#0D1B2A] w-32 text-right">{fmt(a.balance_bwp)}</span>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
