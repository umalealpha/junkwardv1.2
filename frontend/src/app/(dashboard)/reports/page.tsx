'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  Scale,
  TrendingUp,
  BarChart3,
  Clock,
  Wallet,
  BookOpen,
  ChevronRight,
  AlertTriangle,
  Landmark,
  Calendar,
  Target,
  Briefcase,
  PieChart,
  Calculator,
  Boxes,
  Users,
  ShieldCheck,
} from 'lucide-react'

// ─── Report definitions ────────────────────────────────────────────────────────

interface ReportDef {
  id: string
  title: string
  description: string
  href: string
  icon: React.ComponentType<any>
  category: string
}

const REPORTS: ReportDef[] = [
  {
    id: 'renewals',
    title: 'Renewal Report',
    description: 'Policies coming up for renewal in a chosen month (from Graphite). Domestic and Commercial, downloadable as Excel or CSV.',
    href: '/reports/renewals',
    icon: Calendar,
    category: 'Management',
  },
  {
    id: 'trial-balance',
    title: 'Trial Balance',
    description: 'All account balances with opening balance, period movements, and closing balance.',
    href: '/reports/trial-balance',
    icon: Scale,
    category: 'Core',
  },
  {
    id: 'profit-loss',
    title: 'Profit and Loss Alpha Direct BW',
    description: 'Revenue vs expenses. Gross profit, operating expenses, and net profit or loss.',
    href: '/reports/profit-loss',
    icon: TrendingUp,
    category: 'Core',
  },
  {
    id: 'balance-sheet',
    title: 'Balance Sheet',
    description: 'Financial position at a point in time. Assets, liabilities and equity.',
    href: '/reports/balance-sheet',
    icon: BarChart3,
    category: 'Core',
  },
  {
    id: 'ar-aging',
    title: 'AR Aging',
    description: 'Outstanding customer receivables grouped by age: Current, 31-60, 61-90, 120+ days.',
    href: '/reports/ar-aging',
    icon: Clock,
    category: 'Receivables & Payables',
  },
  {
    id: 'ap-aging',
    title: 'AP Aging',
    description: 'Outstanding vendor payables grouped by age. Track overdue bills and manage cash flow.',
    href: '/reports/ap-aging',
    icon: Clock,
    category: 'Receivables & Payables',
  },
  {
    id: 'cash-position',
    title: 'Cash Position',
    description: 'Live bank account balances in BWP equivalent with currency breakdown.',
    href: '/reports/cash-position',
    icon: Wallet,
    category: 'Cash & Banking',
  },
  {
    id: 'bank-reconciliation',
    title: 'Bank Reconciliation',
    description: 'Import statements, match transactions, and resolve discrepancies.',
    href: '/banking',
    icon: Landmark,
    category: 'Cash & Banking',
  },
  {
    id: 'general-ledger',
    title: 'General Ledger',
    description: 'Detailed transaction history for any GL account with running balances.',
    href: '/reports/general-ledger',
    icon: BookOpen,
    category: 'Accounts',
  },
  {
    id: 'budget-setup',
    title: 'Budget Setup',
    description: 'Enter or upload budget figures by GL account and period. Feeds the Budget vs Actual report.',
    href: '/reports/budget-setup',
    icon: Calculator,
    category: 'Budgeting',
  },
  {
    id: 'budget-vs-actual',
    title: 'Budget vs Actual',
    description: 'Compare budgeted amounts to actual results by department with variance analysis.',
    href: '/reports/budget-vs-actual',
    icon: Target,
    category: 'Budgeting',
  },
  {
    id: 'expense-analysis',
    title: 'Expense Analysis',
    description: 'Detailed expense breakdown by category with period-over-period comparison and trends.',
    href: '/reports/expense-analysis',
    icon: PieChart,
    category: 'Budgeting',
  },
  {
    id: 'premium-claims-loss-ratio',
    title: 'Premium, Claims & Loss Ratio',
    description: 'The monthly premium and claims tabs and the loss ratio between them, built from the Premium Board, Month-on-Month and Claims As On Date exports.',
    href: '/reports/premium-claims-loss-ratio',
    icon: Briefcase,
    category: 'Management',
  },
  {
    id: 'management-pack',
    title: 'Management Pack',
    description: 'Monthly EXCO report: P&L, balance sheet, cash position, insurance KPIs, and budget comparison.',
    href: '/reports/management-pack',
    icon: Briefcase,
    category: 'Management',
  },
  {
    id: 'audit-pack',
    title: 'Audit Pack',
    description: 'Auditor-ready bundle: trial balance, P&L, balance sheet, full posted-JE listing for the period, plus reviewer sign-off page. Single-PDF download.',
    href: '/reports/audit-pack',
    icon: Briefcase,
    category: 'Management',
  },
  {
    id: 'asset-register',
    title: 'Asset Register',
    description: 'Fixed assets with cost, accumulated depreciation, and net book value as at any date — by category and balance-sheet tie-out.',
    href: '/reports/asset-register',
    icon: Boxes,
    category: 'Management',
  },
  {
    id: 'related-party',
    title: 'Related-Party Transactions',
    description: 'IAS 24 / NBFIRA disclosure — every line transacting with a related party, in window and fiscal-year-to-date, totalled per party.',
    href: '/reports/related-party-transactions',
    icon: Users,
    category: 'Compliance & Exceptions',
  },
  {
    id: 'regulatory-capital',
    title: 'Regulatory Capital (NBFIRA)',
    description: 'Live capital adequacy check — Available vs Required capital, CAR with traffic-light status, snapshots for the audit trail, editable parameters.',
    href: '/reports/regulatory-capital',
    icon: ShieldCheck,
    category: 'Compliance & Exceptions',
  },
  {
    id: 'vat-return',
    title: 'VAT Return',
    description: 'Prepare bi-monthly BURS VAT return: output VAT, credit notes, input VAT, and net payable.',
    href: '/reports/vat-return',
    icon: Calculator,
    category: 'Compliance & Exceptions',
  },
  {
    id: 'exception-report',
    title: 'Exception Report',
    description: 'Flagged items needing attention: overdue invoices, stuck drafts, unmatched bank lines.',
    href: '/reports/exceptions',
    icon: AlertTriangle,
    category: 'Compliance & Exceptions',
  },
  {
    id: 'tax-calendar',
    title: 'Tax & Regulatory Calendar',
    description: 'BURS and NBFIRA filing deadlines: VAT, PAYE, WHT, CIT, solvency returns, premium reports.',
    href: '/tax-calendar',
    icon: Calendar,
    category: 'Compliance & Exceptions',
  },
]

const CATEGORIES = ['Core', 'Receivables & Payables', 'Cash & Banking', 'Accounts', 'Budgeting', 'Management', 'Compliance & Exceptions']

// ─── Reports Hub Page ─────────────────────────────────────────────────────────

export default function ReportsPage() {
  const router = useRouter()
  const { theme } = useTheme()

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login') }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Reports" />

      <div className="flex-1 p-6" style={{ maxWidth: 1200 }}>
        <div className="mb-6">
          <h2 className="text-lg font-semibold" style={{ color: theme.navy }}>
            Financial Reports
          </h2>
          <p className="text-sm mt-1" style={{ color: theme.t2 }}>
            Generate and export comprehensive financial reports for Alpha Direct Insurance.
          </p>
        </div>

        {CATEGORIES.map((category) => {
          const categoryReports = REPORTS.filter((r) => r.category === category)
          if (categoryReports.length === 0) return null
          return (
            <div key={category} className="mb-8">
              <h3
                className="text-xs font-semibold uppercase tracking-wider mb-3"
                style={{ color: theme.t3 }}
              >
                {category}
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {categoryReports.map((report) => {
                  const Icon = report.icon
                  return (
                    <Link key={report.id} href={report.href}>
                      <div
                        className="rounded-lg p-5 transition-all duration-150 cursor-pointer group"
                        style={{
                          background: theme.card,
                          border: `1px solid ${theme.cardBdr}`,
                          boxShadow: theme.cardSh,
                        }}
                        onMouseEnter={e => {
                          e.currentTarget.style.borderColor = theme.orange
                          e.currentTarget.style.boxShadow = `0 4px 12px ${theme.orange}20`
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.borderColor = theme.cardBdr
                          e.currentTarget.style.boxShadow = theme.cardSh
                        }}
                      >
                        <div className="flex items-start gap-4">
                          <div
                            className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
                            style={{ background: theme.oL }}
                          >
                            <Icon className="w-5 h-5" style={{ color: theme.orange }} strokeWidth={1.5} />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between">
                              <h4
                                className="text-sm font-semibold transition-colors"
                                style={{ color: theme.text }}
                              >
                                {report.title}
                              </h4>
                              <ChevronRight
                                className="w-4 h-4 flex-shrink-0 transition-colors"
                                style={{ color: theme.t3 }}
                              />
                            </div>
                            <p className="text-xs mt-1.5 leading-relaxed" style={{ color: theme.t2 }}>
                              {report.description}
                            </p>
                          </div>
                        </div>
                      </div>
                    </Link>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
