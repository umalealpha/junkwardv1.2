'use client'

import { useEffect, useState, useMemo, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getInvoices,
  getArAging,
  getApAging,
  getToken,
} from '@/lib/api'
import type {
  Invoice,
  AgingReport,
  AgingCustomer,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { cn, exportToCsv, formatAmount, formatCurrency, localYmd, parseAmount } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import {
  AlertTriangle,
  AlertCircle,
  Info,
  Clock,
  FileText,
  Download,
  RefreshCw,
} from 'lucide-react'

// ---- Types ----------------------------------------------------------------

type Severity = 'critical' | 'warning' | 'info'

interface ExceptionItem {
  id: string
  severity: Severity
  category: string
  description: string
  reference: string
  amount: number
  daysOverdue: number
  contactName: string
  href: string
}

// ---- Helpers ---------------------------------------------------------------

function daysBetween(dateStr: string): number {
  if (!dateStr) return 0
  const d = new Date(dateStr + 'T00:00:00')
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  return Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24))
}

function daysSinceCreated(createdAt: string): number {
  if (!createdAt) return 0
  const d = new Date(createdAt)
  const now = new Date()
  return Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24))
}

function severityOrder(s: Severity): number {
  switch (s) {
    case 'critical': return 0
    case 'warning': return 1
    case 'info': return 2
  }
}

// ---- Build exceptions from API data ----------------------------------------

function buildExceptions(
  overdueInvoices: Invoice[],
  draftInvoices: Invoice[],
  arAging: AgingReport | null,
  apAging: AgingReport | null,
): ExceptionItem[] {
  const items: ExceptionItem[] = []

  // --- Overdue invoices ---
  overdueInvoices.forEach((inv) => {
    const overdueDays = daysBetween(inv.due_date)
    const balance = parseAmount(inv.balance_due)

    if (overdueDays > 90) {
      items.push({
        id: `overdue-${inv.id}`,
        severity: 'critical',
        category: 'Overdue Invoice (>90 days)',
        description: `${inv.invoice_number} overdue by ${overdueDays} days`,
        reference: inv.invoice_number,
        amount: balance,
        daysOverdue: overdueDays,
        contactName: inv.contact_name,
        href: `/invoices/${inv.id}`,
      })
    } else if (overdueDays > 30) {
      items.push({
        id: `overdue-${inv.id}`,
        severity: 'warning',
        category: 'Overdue Invoice (31-90 days)',
        description: `${inv.invoice_number} overdue by ${overdueDays} days`,
        reference: inv.invoice_number,
        amount: balance,
        daysOverdue: overdueDays,
        contactName: inv.contact_name,
        href: `/invoices/${inv.id}`,
      })
    } else if (overdueDays > 0) {
      items.push({
        id: `overdue-${inv.id}`,
        severity: 'info',
        category: 'Recently Overdue',
        description: `${inv.invoice_number} overdue by ${overdueDays} days`,
        reference: inv.invoice_number,
        amount: balance,
        daysOverdue: overdueDays,
        contactName: inv.contact_name,
        href: `/invoices/${inv.id}`,
      })
    }
  })

  // --- Large unallocated amounts (overdue + balance > 50,000 BWP) ---
  overdueInvoices.forEach((inv) => {
    const balance = parseAmount(inv.balance_due)
    const overdueDays = daysBetween(inv.due_date)
    if (balance >= 50000 && overdueDays > 0) {
      // Avoid duplicate — only add if not already critical from aging
      const alreadyCritical = items.some(
        (it) => it.id === `overdue-${inv.id}` && it.severity === 'critical',
      )
      if (!alreadyCritical) {
        items.push({
          id: `large-${inv.id}`,
          severity: 'critical',
          category: 'Large Unallocated Amount',
          description: `${inv.invoice_number} has ${formatCurrency(balance)} outstanding`,
          reference: inv.invoice_number,
          amount: balance,
          daysOverdue: overdueDays,
          contactName: inv.contact_name,
          href: `/invoices/${inv.id}`,
        })
      }
    }
  })

  // --- Draft invoices stuck > 7 days ---
  draftInvoices.forEach((inv) => {
    const age = daysSinceCreated(inv.created_at)
    if (age > 7) {
      items.push({
        id: `draft-${inv.id}`,
        severity: 'warning',
        category: 'Stuck Draft Invoice',
        description: `${inv.invoice_number} in draft for ${age} days`,
        reference: inv.invoice_number,
        amount: parseAmount(inv.total_amount),
        daysOverdue: age,
        contactName: inv.contact_name,
        href: `/invoices/${inv.id}`,
      })
    }
  })

  // --- AR Aging > 90 days ---
  const processAging = (
    aging: AgingReport | null,
    type: 'AR' | 'AP',
  ) => {
    if (!aging) return
    aging.customers.forEach((customer: AgingCustomer) => {
      customer.invoices.forEach((inv) => {
        const bucket = inv.age_bucket?.toLowerCase()
        if (bucket === '91-120' || bucket === '91_120' || bucket === 'over_120' || bucket === 'over120') {
          const balance = parseAmount(inv.balance_due)
          // Skip if we already captured it from overdue invoices
          const already = items.some((it) => it.reference === inv.invoice_number && it.severity === 'critical')
          if (!already) {
            items.push({
              id: `${type.toLowerCase()}-aging-${inv.invoice_number}`,
              severity: 'critical',
              category: `${type} Aged >90 Days`,
              description: `${inv.invoice_number} — ${customer.contact_name}: ${inv.days_past_due} days past due`,
              reference: inv.invoice_number,
              amount: balance,
              daysOverdue: inv.days_past_due,
              contactName: customer.contact_name,
              href: type === 'AR' ? '/reports/ar-aging' : '/reports/ap-aging',
            })
          }
        }
      })
    })
  }

  processAging(arAging, 'AR')
  processAging(apAging, 'AP')

  // --- Invoices approaching due date (within 7 days, not yet overdue) ---
  // We check draft + non-overdue posted invoices. For simplicity, check overdue list
  // items with negative overdue days (i.e., still upcoming). The API might not return
  // these, so we also scan drafts that have a due date coming up.
  draftInvoices.forEach((inv) => {
    const daysUntilDue = -daysBetween(inv.due_date)
    if (daysUntilDue > 0 && daysUntilDue <= 7) {
      items.push({
        id: `approaching-${inv.id}`,
        severity: 'info',
        category: 'Approaching Due Date',
        description: `${inv.invoice_number} due in ${daysUntilDue} days (still in draft!)`,
        reference: inv.invoice_number,
        amount: parseAmount(inv.total_amount),
        daysOverdue: 0,
        contactName: inv.contact_name,
        href: `/invoices/${inv.id}`,
      })
    }
  })

  // Sort: critical first, then warning, then info; within each, by amount descending
  items.sort((a, b) => {
    const so = severityOrder(a.severity) - severityOrder(b.severity)
    if (so !== 0) return so
    return b.amount - a.amount
  })

  return items
}

// ---- Severity icon + color helpers -----------------------------------------

function SeverityIcon({
  severity,
  theme,
}: {
  severity: Severity
  theme: ReturnType<typeof useTheme>['theme']
}) {
  switch (severity) {
    case 'critical':
      return <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: theme.er }} />
    case 'warning':
      return <AlertCircle className="w-4 h-4 flex-shrink-0" style={{ color: theme.wr }} />
    case 'info':
      return <Info className="w-4 h-4 flex-shrink-0" style={{ color: theme.inf }} />
  }
}

function severityBg(severity: Severity, theme: ReturnType<typeof useTheme>['theme']) {
  switch (severity) {
    case 'critical':
      return { background: theme.erB, color: theme.er, border: `1px solid ${theme.er}33` }
    case 'warning':
      return { background: theme.wrB, color: theme.wr, border: `1px solid ${theme.wr}33` }
    case 'info':
      return { background: theme.inB, color: theme.inf, border: `1px solid ${theme.inf}33` }
  }
}

function severityLabel(severity: Severity): string {
  switch (severity) {
    case 'critical': return 'Critical'
    case 'warning': return 'Warning'
    case 'info': return 'Info'
  }
}

// ---- Component -------------------------------------------------------------

export default function ExceptionReportPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)
  const { selectedId: companyId, selected: selectedCompany } = useCompany()

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [overdueInvoices, setOverdueInvoices] = useState<Invoice[]>([])
  const [draftInvoices, setDraftInvoices] = useState<Invoice[]>([])
  const [arAging, setArAging] = useState<AgingReport | null>(null)
  const [apAging, setApAging] = useState<AgingReport | null>(null)
  const [filterSeverity, setFilterSeverity] = useState<Severity | 'all'>('all')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    handleRefresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId])

  const handleRefresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const invParams: Record<string, string> = { page_size: '200' }
      if (companyId) invParams.company = companyId
      const [overdueRes, draftRes, arRes, apRes] = await Promise.allSettled([
        getInvoices({ ...invParams, status: 'overdue' }),
        getInvoices({ ...invParams, status: 'draft' }),
        getArAging(undefined, companyId),
        getApAging(undefined, companyId),
      ])

      setOverdueInvoices(
        overdueRes.status === 'fulfilled' ? overdueRes.value.results : [],
      )
      setDraftInvoices(
        draftRes.status === 'fulfilled' ? draftRes.value.results : [],
      )
      setArAging(arRes.status === 'fulfilled' ? arRes.value : null)
      setApAging(apRes.status === 'fulfilled' ? apRes.value : null)
      setLoaded(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load exception data')
    } finally {
      setLoading(false)
    }
  }, [companyId])

  const exceptions = useMemo(
    () => buildExceptions(overdueInvoices, draftInvoices, arAging, apAging),
    [overdueInvoices, draftInvoices, arAging, apAging],
  )

  const counts = useMemo(() => {
    const c = { critical: 0, warning: 0, info: 0 }
    exceptions.forEach((e) => c[e.severity]++)
    return c
  }, [exceptions])

  const filtered = useMemo(() => {
    if (filterSeverity === 'all') return exceptions
    return exceptions.filter((e) => e.severity === filterSeverity)
  }, [exceptions, filterSeverity])

  const handleExport = () => {
    if (filtered.length === 0) return
    const rows = filtered.map((e) => ({
      Severity: severityLabel(e.severity),
      Category: e.category,
      Reference: e.reference,
      Contact: e.contactName,
      Amount: e.amount.toFixed(2),
      'Days Overdue': e.daysOverdue,
      Description: e.description,
    }))
    exportToCsv(rows, `exception_report_${localYmd(new Date())}`)
  }

  const handleRowClick = (href: string) => {
    router.push(href)
  }

  // ---- Render ----------------------------------------------------------------

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Exception Report"
        breadcrumbs={[
          { label: 'Reports', href: '/reports' },
          { label: 'Exception Report' },
        ]}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              onClick={handleRefresh}
              loading={loading}
            >
              Refresh
            </Button>
            {loaded && filtered.length > 0 && (
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Download className="w-3.5 h-3.5" />}
                onClick={handleExport}
              >
                Export CSV
              </Button>
            )}
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Error */}
        {error && (
          <div
            className="rounded-xl p-3 flex items-center gap-2"
            style={{ background: theme.erB, border: `1px solid ${theme.er}33` }}
          >
            <AlertCircle className="w-4 h-4" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>
              {error}
            </p>
          </div>
        )}

        {/* Summary Bar */}
        {loaded && !loading && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {/* Critical */}
            <button
              onClick={() =>
                setFilterSeverity(filterSeverity === 'critical' ? 'all' : 'critical')
              }
              className={cn(
                'rounded-xl p-4 text-center transition-all cursor-pointer',
                filterSeverity === 'critical' ? 'ring-2' : '',
              )}
              style={{
                background: theme.erB,
                border: `1px solid ${theme.er}33`,
                ...(filterSeverity === 'critical' ? { ringColor: theme.er } : {}),
              }}
            >
              <div className="flex items-center justify-center gap-2 mb-1">
                <AlertTriangle className="w-5 h-5" style={{ color: theme.er }} />
                <span
                  className="text-xs font-semibold uppercase tracking-wider"
                  style={{ color: theme.er }}
                >
                  Critical
                </span>
              </div>
              <p
                className="text-2xl font-bold font-mono-nums"
                style={{ color: theme.er }}
              >
                {counts.critical}
              </p>
            </button>

            {/* Warning */}
            <button
              onClick={() =>
                setFilterSeverity(filterSeverity === 'warning' ? 'all' : 'warning')
              }
              className={cn(
                'rounded-xl p-4 text-center transition-all cursor-pointer',
                filterSeverity === 'warning' ? 'ring-2' : '',
              )}
              style={{
                background: theme.wrB,
                border: `1px solid ${theme.wr}33`,
                ...(filterSeverity === 'warning' ? { ringColor: theme.wr } : {}),
              }}
            >
              <div className="flex items-center justify-center gap-2 mb-1">
                <AlertCircle className="w-5 h-5" style={{ color: theme.wr }} />
                <span
                  className="text-xs font-semibold uppercase tracking-wider"
                  style={{ color: theme.wr }}
                >
                  Warnings
                </span>
              </div>
              <p
                className="text-2xl font-bold font-mono-nums"
                style={{ color: theme.wr }}
              >
                {counts.warning}
              </p>
            </button>

            {/* Info */}
            <button
              onClick={() =>
                setFilterSeverity(filterSeverity === 'info' ? 'all' : 'info')
              }
              className={cn(
                'rounded-xl p-4 text-center transition-all cursor-pointer',
                filterSeverity === 'info' ? 'ring-2' : '',
              )}
              style={{
                background: theme.inB,
                border: `1px solid ${theme.inf}33`,
                ...(filterSeverity === 'info' ? { ringColor: theme.inf } : {}),
              }}
            >
              <div className="flex items-center justify-center gap-2 mb-1">
                <Info className="w-5 h-5" style={{ color: theme.inf }} />
                <span
                  className="text-xs font-semibold uppercase tracking-wider"
                  style={{ color: theme.inf }}
                >
                  Info
                </span>
              </div>
              <p
                className="text-2xl font-bold font-mono-nums"
                style={{ color: theme.inf }}
              >
                {counts.info}
              </p>
            </button>
          </div>
        )}

        {/* Active filter indicator */}
        {filterSeverity !== 'all' && (
          <div
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm"
            style={{ background: theme.g100, color: theme.t2 }}
          >
            <span>
              Showing <strong style={{ color: theme.text }}>{severityLabel(filterSeverity)}</strong> items only
            </span>
            <button
              onClick={() => setFilterSeverity('all')}
              className="ml-auto text-xs font-medium px-2 py-1 rounded-md transition-colors"
              style={{ background: theme.g200, color: theme.text }}
              onMouseEnter={(e) => (e.currentTarget.style.background = theme.orange)}
              onMouseLeave={(e) => (e.currentTarget.style.background = theme.g200)}
            >
              Show All
            </button>
          </div>
        )}

        {/* Exceptions Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={8} cols={7} />
            ) : !loaded ? (
              <div className="py-12 text-center">
                <Clock className="w-8 h-8 mx-auto mb-2" style={{ color: theme.g200 }} />
                <p className="text-sm" style={{ color: theme.t3 }}>
                  Loading exception data...
                </p>
              </div>
            ) : filtered.length === 0 ? (
              <div className="py-12 text-center">
                <FileText className="w-8 h-8 mx-auto mb-2" style={{ color: theme.g200 }} />
                <p className="text-sm" style={{ color: theme.t3 }}>
                  {filterSeverity !== 'all'
                    ? `No ${severityLabel(filterSeverity).toLowerCase()} exceptions found`
                    : 'No exceptions found — everything looks great!'}
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead
                    className="border-b-2"
                    style={{ background: theme.g100, borderColor: theme.g200 }}
                  >
                    <tr>
                      <th
                        className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
                        style={{ color: theme.g700 }}
                      >
                        Severity
                      </th>
                      <th
                        className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
                        style={{ color: theme.g700 }}
                      >
                        Category
                      </th>
                      <th
                        className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
                        style={{ color: theme.g700 }}
                      >
                        Reference
                      </th>
                      <th
                        className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider hidden md:table-cell"
                        style={{ color: theme.g700 }}
                      >
                        Contact
                      </th>
                      <th
                        className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider"
                        style={{ color: theme.g700 }}
                      >
                        Amount
                      </th>
                      <th
                        className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider hidden md:table-cell"
                        style={{ color: theme.g700 }}
                      >
                        Days
                      </th>
                      <th
                        className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider hidden lg:table-cell"
                        style={{ color: theme.g700 }}
                      >
                        Description
                      </th>
                    </tr>
                  </thead>
                  <tbody
                    className="divide-y"
                    style={{
                      background: theme.card,
                    }}
                  >
                    {filtered.map((item) => {
                      const sev = severityBg(item.severity, theme)
                      return (
                        <tr
                          key={item.id}
                          className="cursor-pointer transition-colors"
                          style={{ borderColor: theme.g200 }}
                          onClick={() => handleRowClick(item.href)}
                          onMouseEnter={(e) =>
                            (e.currentTarget.style.background = theme.oL)
                          }
                          onMouseLeave={(e) =>
                            (e.currentTarget.style.background = 'transparent')
                          }
                        >
                          {/* Severity badge */}
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-full"
                              style={sev}
                            >
                              <SeverityIcon severity={item.severity} theme={theme} />
                              {severityLabel(item.severity)}
                            </span>
                          </td>

                          {/* Category */}
                          <td
                            className="px-4 py-2.5 font-medium text-xs"
                            style={{ color: theme.text }}
                          >
                            {item.category}
                          </td>

                          {/* Reference */}
                          <td className="px-4 py-2.5">
                            <span
                              className="font-mono text-xs"
                              style={{ color: theme.orange }}
                            >
                              {item.reference}
                            </span>
                          </td>

                          {/* Contact */}
                          <td
                            className="px-4 py-2.5 max-w-[140px] truncate hidden md:table-cell"
                            style={{ color: theme.t2 }}
                          >
                            {item.contactName}
                          </td>

                          {/* Amount */}
                          <td className="px-4 py-2.5 text-right font-mono-nums font-semibold">
                            <span style={{ color: theme.orange }}>
                              {fmt(item.amount)}
                            </span>
                          </td>

                          {/* Days */}
                          <td className="px-4 py-2.5 text-right hidden md:table-cell">
                            <span
                              className="text-xs font-medium"
                              style={{
                                color:
                                  item.daysOverdue > 90
                                    ? theme.er
                                    : item.daysOverdue > 30
                                      ? theme.wr
                                      : theme.t2,
                              }}
                            >
                              {item.daysOverdue > 0 ? `${item.daysOverdue}d` : '--'}
                            </span>
                          </td>

                          {/* Description */}
                          <td
                            className="px-4 py-2.5 text-xs max-w-[260px] truncate hidden lg:table-cell"
                            style={{ color: theme.t3 }}
                          >
                            {item.description}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>

                  {/* Footer totals */}
                  {filtered.length > 0 && (
                    <tfoot
                      className="border-t"
                      style={{ background: theme.g50, borderColor: theme.g200 }}
                    >
                      <tr>
                        <td
                          colSpan={4}
                          className="px-4 py-3 text-xs font-bold uppercase"
                          style={{ color: theme.t2 }}
                        >
                          {filtered.length} Exception{filtered.length !== 1 ? 's' : ''} &mdash; Total Exposure
                        </td>
                        <td
                          className="px-4 py-3 text-right font-bold font-mono-nums"
                          style={{ color: theme.orange }}
                        >
                          {fmt(
                            filtered.reduce((sum, e) => sum + e.amount, 0),
                          )}
                        </td>
                        <td colSpan={2} />
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
