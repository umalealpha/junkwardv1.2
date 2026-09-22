'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight as ChevronRightIcon,
  ChevronDown,
  Menu,
  Search,
  Eye,
  Sun,
  Sparkles,
  CloudSun,
  Briefcase,
  EyeOff,
  SlidersHorizontal,
  Building2,
  Check,
} from 'lucide-react'
import NotificationCenter, { PILL_SLOT_ID } from '@/components/layout/NotificationCenter'
import { cn } from '@/lib/utils'
import { useTheme } from '@/contexts/ThemeContext'
import { useNumberFormat, type NumberFormatMode } from '@/contexts/NumberFormatContext'
import { useQuote } from '@/contexts/QuoteContext'
import { useCompany } from '@/contexts/CompanyContext'
import { getQuotePageKey } from '@/lib/quotes'

// ─── Route → label mapping ──────────────────────────────────────────────────

const PAGE_LABELS: Record<string, string> = {
  '/health-care/cover': 'Health Cover',
  '/health-care/providers': 'Provider Network',
  '/dashboard': 'Dashboard',
  '/quick-entry': 'Smart Entry',
  '/invoices': 'Invoices',
  '/invoices/new': 'New Invoice',
  '/contacts': 'Contacts',
  '/payments': 'Payments',
  '/payments/new': 'New Payment',
  '/banking': 'Bank Reconciliation',
  '/reports': 'Reports',
  '/reports/trial-balance': 'Trial Balance',
  '/reports/profit-loss': 'Profit and Loss Alpha Direct BW',
  '/reports/balance-sheet': 'Balance Sheet',
  '/reports/cash-position': 'Cash Position',
  '/reports/general-ledger': 'General Ledger',
  '/reports/ar-aging': 'AR Aging',
  '/reports/ap-aging': 'AP Aging',
  '/reports/budget-vs-actual': 'Budget vs Actual',
  '/reports/vat-return': 'VAT Return',
  '/reports/claims-payment-movement': 'Claims Payment Movement',
  '/reports/expense-analysis': 'Expense Analysis',
  '/reports/management-pack': 'Management Pack',
  '/reports/audit-pack': 'Financial Statements for Auditors',
  '/petty-cash': 'Petty Cash Vouchers',
  '/petty-cash/new': 'New Petty Cash Voucher',
  '/petty-cash/reimbursements': 'Petty Cash Reimbursements',
  '/petty-cash/reimbursements/new': 'Top Up the Petty Cash Tin',
  '/reinsurance/treaties': 'Reinsurance Treaties',
  '/reinsurance/cessions': 'Reinsurance Cessions',
  '/reinsurance/recoveries': 'Reinsurance Recoveries',
  '/reinsurance/reinsurers': 'Reinsurers',
  '/reinsurance/bordereaux': 'Reinsurance Bordereaux',
  '/investments': 'Investment Portfolio',
  '/investments/transactions': 'Investment Transactions',
  '/bank-feeds': 'Automated Bank Feeds',
  '/reports/exceptions': 'Exception Report',
  '/journal-entries': 'Journal Entries',
  '/accounts': 'Chart of Accounts',
  '/tax-calendar': 'Tax & Regulatory Calendar',
  '/tax/vat-recon': 'VAT Reconciliation',
  '/settings': 'Settings',
  '/integrations': 'Integrations',
}

function getPageLabel(pathname: string): string {
  // Exact match first
  if (PAGE_LABELS[pathname]) return PAGE_LABELS[pathname]
  // Try stripping trailing segments for detail pages like /invoices/[id]
  const segments = pathname.split('/')
  while (segments.length > 1) {
    segments.pop()
    const parent = segments.join('/') || '/'
    if (PAGE_LABELS[parent]) return PAGE_LABELS[parent]
  }
  return 'Page'
}

// ─── Props ───────────────────────────────────────────────────────────────────

interface TopBarProps {
  /** Legacy props — still accepted for backward compatibility with existing pages */
  title?: string
  /** Optional sub-line under the title — accepted but currently rendered
   *  by the older pages that pass it; the new TopBar layout shows it as
   *  small muted text under the title. */
  subtitle?: string
  breadcrumbs?: { label: string; href?: string }[]
  actions?: React.ReactNode
  className?: string
  /** Sidebar collapse state */
  sidebarCollapsed?: boolean
  onSidebarToggle?: () => void
}

// ─── Component ───────────────────────────────────────────────────────────────

export function TopBar({
  title,
  actions,
  className,
  sidebarCollapsed,
  onSidebarToggle,
}: TopBarProps) {
  const pathname = usePathname()
  const { theme, themeKey, setTheme, reduceMotion, toggleReduceMotion } = useTheme()
  const { quote, refreshQuote } = useQuote()
  const { mode: numMode, setMode: setNumMode } = useNumberFormat()

  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [companyDropdownOpen, setCompanyDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const companyDropdownRef = useRef<HTMLDivElement>(null)
  const { companies, selectedId: companyId, setSelectedId: setCompanyId, selected: selectedCompany } = useCompany()

  // Refresh quote on pathname change
  useEffect(() => {
    refreshQuote(getQuotePageKey(pathname))
  }, [pathname, refreshQuote])

  // Close dropdowns on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
      if (companyDropdownRef.current && !companyDropdownRef.current.contains(e.target as Node)) {
        setCompanyDropdownOpen(false)
      }
    }
    if (dropdownOpen || companyDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [dropdownOpen, companyDropdownOpen])

  // Don't render on home page
  if (pathname === '/') return null

  const pageLabel = title || getPageLabel(pathname)

  return (
    <header
      className={cn('flex-shrink-0 sticky top-0', className)}
      style={{
        height: 56,
        background: theme.topbar,
        borderBottom: `1px solid ${theme.topbarBdr}`,
        zIndex: 50,
      }}
    >
      {/* Left padding bumped on mobile to clear the fixed hamburger drawer
          toggle (top-4 left-4, ~36px square in Sidebar.tsx). */}
      <div className="flex items-center justify-between h-full pl-14 pr-3 md:pl-6 md:pr-6">
        {/* Left side */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          {/* Sidebar collapse toggle */}
          {onSidebarToggle && (
            <button
              onClick={onSidebarToggle}
              className="hidden md:flex items-center justify-center w-8 h-8 rounded-lg transition-colors hover:bg-black/5"
              style={{ color: theme.t3 }}
              aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {sidebarCollapsed
                ? <Menu className="w-4 h-4" strokeWidth={1.5} />
                : <ChevronLeft className="w-4 h-4" strokeWidth={1.5} />
              }
            </button>
          )}

          {/* Page h1 — visually hidden; gives every dashboard page a level-one
              heading for screen readers without changing the layout. */}
          <h1 className="sr-only">{pageLabel}</h1>

          {/* Breadcrumb */}
          <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm min-w-0">
            <Link
              href="/dashboard"
              className="flex-shrink-0 hover:underline"
              style={{ color: theme.t3 }}
            >
              Home
            </Link>
            {pageLabel !== 'Dashboard' && (
              <>
                <ChevronRightIcon
                  className="w-3 h-3 flex-shrink-0"
                  style={{ color: theme.t3 }}
                />
                <span
                  className="font-medium truncate"
                  style={{ color: theme.text }}
                >
                  {pageLabel}
                </span>
              </>
            )}
          </nav>

          {/* Slot for the "N tasks pending" pill (NotificationCenter portals
              into it). It sits AFTER the breadcrumb as a real flex child, so
              the pill can never paint over the page title — the breadcrumb's
              own `min-w-0 truncate` gives way first if the header runs out of
              room. See the comment in NotificationCenter.tsx. */}
          <div id={PILL_SLOT_ID} className="flex flex-shrink-0 items-center" />

          {/* Funny quote removed 2026-05-20 per CFO directive — was
              cluttering every page header. QuoteContext is kept in case
              we re-add later, but no caller renders the string anymore. */}
        </div>

        {/* Right side */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Global quick-search (⌘K) — opens the CommandPalette overlay. */}
          <button
            onClick={() => window.dispatchEvent(new Event('omni:cmdk'))}
            aria-label="Search omni (Command-K)"
            title="Search — ⌘K / Ctrl-K"
            className="h-9 hidden sm:flex items-center gap-2 px-2.5 rounded-lg transition-colors text-xs"
            style={{ background: theme.g100, color: theme.g700, border: `1px solid ${theme.cardBdr}` }}
          >
            <Search className="w-3.5 h-3.5" />
            <span className="hidden md:inline">Search</span>
            <kbd className="hidden md:inline rounded px-1 py-0.5 text-[10px] font-semibold"
                 style={{ background: theme.card, color: theme.t3 }}>⌘K</kbd>
          </button>

          {/* Legacy actions slot */}
          {actions && <div className="flex items-center gap-2">{actions}</div>}

          {/* Company / Subsidiary picker */}
          {companies.length > 0 && (
            <div className="relative" ref={companyDropdownRef}>
              <button
                onClick={() => setCompanyDropdownOpen(prev => !prev)}
                className="h-9 flex items-center gap-1.5 px-2.5 rounded-lg transition-colors text-xs font-medium"
                style={{ color: theme.t2, border: `1px solid ${theme.cardBdr}` }}
                onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                aria-label="Select company"
                title="Filter all data by subsidiary"
              >
                <Building2 className="w-3.5 h-3.5" strokeWidth={2} />
                <span className="hidden sm:inline max-w-[140px] truncate">
                  {selectedCompany ? selectedCompany.code : 'All companies'}
                </span>
                <ChevronDown className="w-3 h-3" strokeWidth={2} />
              </button>

              {companyDropdownOpen && (
                <div
                  className="absolute right-0 py-1"
                  style={{
                    top: '110%',
                    minWidth: 240,
                    background: theme.card,
                    border: `1px solid ${theme.cardBdr}`,
                    borderRadius: 10,
                    boxShadow: theme.cardSh,
                    zIndex: 999,
                  }}
                >
                  <p className="px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wider" style={{ color: theme.t3 }}>
                    Subsidiary
                  </p>
                  <button
                    onClick={() => { setCompanyId(null); setCompanyDropdownOpen(false) }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                    style={{ color: theme.text }}
                    onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <span className="flex-1 text-left">All companies (consolidated)</span>
                    {!companyId && <Check className="w-3.5 h-3.5" style={{ color: theme.ok }} />}
                  </button>
                  <div className="my-1" style={{ borderTop: `1px solid ${theme.cardBdr}` }} />
                  {companies.map((c) => (
                    <button
                      key={c.id}
                      onClick={() => { setCompanyId(c.id); setCompanyDropdownOpen(false) }}
                      className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                      style={{ color: theme.text }}
                      onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                    >
                      <div className="flex-1 text-left">
                        <span className="font-mono font-medium">{c.code}</span>
                        <span className="text-[11px] block" style={{ color: theme.t3 }}>{c.name}</span>
                      </div>
                      {companyId === c.id && <Check className="w-3.5 h-3.5" style={{ color: theme.ok }} />}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* View options — chrome cleanup (redesign pack 02/05, 2026-06-13):
              the old separate "# 0.00" chip and eye icon are consolidated into
              ONE dropdown. Both functions stay fully reachable: theme switcher
              (Light / Fun / Heavenly), number format, reduce motion. */}
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => setDropdownOpen(prev => !prev)}
              className="w-9 h-9 flex items-center justify-center rounded-lg transition-colors"
              style={{ color: theme.t2 }}
              onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              aria-label="View settings"
              title="View settings — theme, number format, motion"
            >
              <SlidersHorizontal className="w-4 h-4" strokeWidth={1.5} />
            </button>

            {dropdownOpen && (
              <div
                className="absolute right-0 py-1"
                style={{
                  top: '110%',
                  minWidth: 220,
                  background: theme.card,
                  border: `1px solid ${theme.cardBdr}`,
                  borderRadius: 10,
                  boxShadow: theme.cardSh,
                  zIndex: 999,
                }}
              >
                {/* Header */}
                <p
                  className="px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wider"
                  style={{ color: theme.t3 }}
                >
                  View Mode
                </p>

                {/* Professional option (clean neutral light — CFO 2026-08-10) */}
                <button
                  onClick={() => { setTheme('professional'); setDropdownOpen(false) }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                  style={{ color: theme.text }}
                  onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <Briefcase className="w-4 h-4" style={{ color: '#4F6BED' }} strokeWidth={1.5} />
                  <span className="flex-1 text-left">Professional</span>
                  {themeKey === 'professional' && (
                    <span
                      className="text-[11px] font-medium px-1.5 py-0.5 rounded"
                      style={{ background: theme.okB, color: theme.ok }}
                    >
                      Active
                    </span>
                  )}
                </button>

                {/* Light option */}
                <button
                  onClick={() => { setTheme('light'); setDropdownOpen(false) }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                  style={{ color: theme.text }}
                  onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <Sun className="w-4 h-4" style={{ color: theme.t2 }} strokeWidth={1.5} />
                  <span className="flex-1 text-left">Light</span>
                  {themeKey === 'light' && (
                    <span
                      className="text-[11px] font-medium px-1.5 py-0.5 rounded"
                      style={{ background: theme.okB, color: theme.ok }}
                    >
                      Active
                    </span>
                  )}
                </button>

                {/* Fun Mode option */}
                <button
                  onClick={() => { setTheme('fun'); setDropdownOpen(false) }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                  style={{ color: theme.text }}
                  onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <Sparkles className="w-4 h-4" style={{ color: theme.t2 }} strokeWidth={1.5} />
                  <span className="flex-1 text-left">Fun Mode</span>
                  {themeKey === 'fun' && (
                    <span
                      className="text-[11px] font-medium px-1.5 py-0.5 rounded"
                      style={{ background: theme.okB, color: theme.ok }}
                    >
                      Active
                    </span>
                  )}
                </button>

                {/* Heavenly option (sky-blue, white glass) */}
                <button
                  onClick={() => { setTheme('heavenly'); setDropdownOpen(false) }}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                  style={{ color: theme.text }}
                  onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <CloudSun className="w-4 h-4" style={{ color: '#2E8BE6' }} strokeWidth={1.5} />
                  <span className="flex-1 text-left">Heavenly</span>
                  {themeKey === 'heavenly' && (
                    <span
                      className="text-[11px] font-medium px-1.5 py-0.5 rounded"
                      style={{ background: theme.okB, color: theme.ok }}
                    >
                      Active
                    </span>
                  )}
                </button>

                {/* Divider */}
                <div className="my-1" style={{ borderTop: `1px solid ${theme.cardBdr}` }} />

                {/* Number Format (folded in from the old "# 0.00" chip) */}
                <p
                  className="px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wider"
                  style={{ color: theme.t3 }}
                >
                  Number Format
                </p>
                {([
                  { key: 'full' as NumberFormatMode, label: 'Full Detail', example: 'BWP 4,900,000.00' },
                  { key: 'thousands' as NumberFormatMode, label: 'Thousands', example: 'BWP 4,900K' },
                  { key: 'millions' as NumberFormatMode, label: 'Millions', example: 'BWP 4.90M' },
                ]).map(opt => (
                  <button
                    key={opt.key}
                    onClick={() => { setNumMode(opt.key); setDropdownOpen(false) }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                    style={{ color: theme.text }}
                    onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <span className="flex-1 text-left font-medium">{opt.label}</span>
                    <span className="text-[11px]" style={{ color: theme.t3 }}>{opt.example}</span>
                    {numMode === opt.key && (
                      <span
                        className="text-[10px] font-medium px-1.5 py-0.5 rounded"
                        style={{ background: theme.okB, color: theme.ok }}
                      >
                        Active
                      </span>
                    )}
                  </button>
                ))}

                {/* Divider */}
                <div className="my-1" style={{ borderTop: `1px solid ${theme.cardBdr}` }} />

                {/* Reduce Motion toggle */}
                <button
                  onClick={toggleReduceMotion}
                  className="w-full flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                  style={{ color: theme.text }}
                  onMouseEnter={e => (e.currentTarget.style.background = theme.g100)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  {reduceMotion
                    ? <EyeOff className="w-4 h-4" style={{ color: theme.t2 }} strokeWidth={1.5} />
                    : <Eye className="w-4 h-4" style={{ color: theme.t2 }} strokeWidth={1.5} />
                  }
                  <span className="flex-1 text-left">Reduce Motion</span>
                  {reduceMotion && (
                    <span
                      className="text-[11px] font-medium px-1.5 py-0.5 rounded"
                      style={{ background: theme.wrB, color: theme.wr }}
                    >
                      On
                    </span>
                  )}
                </button>
              </div>
            )}
          </div>

          {/* Duplicate decorative "Search..." pill removed (redesign pack 02
              chrome cleanup) — the real ⌘K search button on the left of this
              cluster is the single search affordance. */}

          {/* CFO directive 2026-05-21 — bell now shows real pending count
              plus a sticky banner at the top of the page when items
              await approval. Component owns its own polling. */}
          <div style={{ color: theme.t2 }}>
            <NotificationCenter />
          </div>
        </div>
      </div>
    </header>
  )
}
