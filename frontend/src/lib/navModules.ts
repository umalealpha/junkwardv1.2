/**
 * navModules — the ONE canonical definition of every logged-in nav
 * destination: which rail domain and section it renders under, its icon,
 * href and role gate. Sidebar.tsx (rail + expanded panel) calls
 * buildNavModules() with the caller's live permission probes; CommandPalette
 * groups agree with the domain labels exported here (RAIL_DOMAIN_LABELS).
 * Extracted out of Sidebar.tsx (M-NAV-DOMAINS, 19-Sep-2026, CFO be335dfe +
 * 3f2b2411) so the placement logic is plain data + a pure function — testable
 * without rendering React — instead of a second list that can drift from what
 * actually ships. See the reasoning block below topItems' old declaration
 * (now inlined above homeItems) for why each item sits where it does.
 */
import type { UserProfile } from '@/lib/api'
import { ADIPL_ONLY_HREFS } from '@/lib/entityScope'
import {
  LayoutDashboard,
  Zap,
  Users,
  FileText,
  Clock,
  Receipt,
  UserCheck,
  CreditCard,
  Banknote,
  Building2,
  BarChart3,
  Scale,
  TrendingUp,
  Layers,
  CircleDollarSign,
  BookOpen,
  FileSpreadsheet,
  AlertTriangle,
  Target,
  Landmark,
  Calendar,
  Settings,
  Sparkles,
  Bug,
  Briefcase,
  ClipboardCheck,
  ClipboardList,
  PieChart,
  Calculator,
  Boxes,
  Plus,
  Upload,
  Shield,
  ShieldCheck,
  ShieldAlert,
  UserCog,
  RotateCw,
  Coins,
  Package,
  Wallet,
  Globe,
  ArrowDownLeft,
  ArrowRightLeft,
  ArrowUpRight,
  Users as UsersIcon,
  UserPlus,
  Lock,
  Inbox,
  ListChecks,
  Mail,
  Eraser,
  Stethoscope,
  Grid3x3,
  GitBranch,
  Compass,
  Navigation,
  Sparkle,
  Gift,
  Trophy,
  Store,
  Database,
  HeartPulse,
  LifeBuoy,
  Route,
  MessageCircle,
  Gauge,
  Pencil,
  FileSignature,
  House,
  Settings2,
  BadgeCheck,
  BrainCircuit,
  UserRoundCheck,
  CalendarDays,
  Cog,
  type LucideIcon, Archive, Send,
  PackageCheck, Bot, MapPin, CalendarClock, Gavel, Activity, RefreshCw, GraduationCap,
  Rocket,
} from 'lucide-react'

// ─── Navigation structure ────────────────────────────────────────────────────

interface NavItem {
  label: string
  icon: LucideIcon
  href: string
  /** CFO directive 2026-05-21 menu consolidation. When true the item
   *  renders as a non-clickable sub-header inside an expanded group
   *  (e.g. "Reporting" inside Accounting). `href` is ignored. */
  header?: boolean
  /** When true, render as a plain <a> (full-page nav) instead of a Next
   *  <Link>. Used for same-origin apps served outside the Next.js app,
   *  e.g. /claims-po (the Claims PO tool behind Caddy). */
  external?: boolean
}

interface NavGroup {
  label: string
  icon: LucideIcon
  children?: NavItem[]
  /** If set, the group is a direct link (no dropdown). */
  href?: string
  /** If set with href, render as plain <a> instead of Next Link. Used for
   *  Django-served pages such as /hris/ that sit outside the Next.js app. */
  external?: boolean
}

// CFO directive 2026-08-22 declutter: the old flat 17-item list mixed
// dashboards, personal work, spend and support in one pile — later split into
// readable sections. CFO feature requests be335dfe + 3f2b2411 (19-Sep-2026,
// UI-NAV-OVERVIEW-GROUPING) went further: Overview itself was a catch-all
// mixing an executive home page with a GitHub release monitor, a platform
// automation switchboard, a people-performance control and an accounting
// entry tool. The single flat `topItems` list is gone; each destination now
// lives in its own domain array below, one array per rail icon, so the rail
// reads down to 9 named business domains (Home, My Work, Finance, People,
// Insurance, Operations, Risk & Assurance, Data & AI, System) instead of the
// 13 icons nobody could tell apart. Routes, hrefs, gates and behaviour are
// unchanged — this file only decides which rail icon and section an
// already-existing page renders under.
//
// Placement reasoning, one line per move (CFO explicit instruction followed
// where given; "silent" items placed where a finance/HR/claims/ops user would
// actually look, per 3f2b2411):
//   - My Omni, Dashboard, Intelligence Summary -> Home (CFO explicit).
//   - Tasks, My League, Task Dashboard, My Requests, My Approvals, My Equity,
//     Rooms, employee Commissions -> My Work (CFO explicit). Broker
//     Commission stays out — it is a finance register, not a personal page.
//   - Project Nexus (rewards/wellness) -> My Work, 2nd section. Not named by
//     the CFO, but it is the same kind of personal/self-service page as My
//     League and My Equity, so it reads naturally next to them rather than
//     keeping its own rail icon for 7 routes.
//   - Smart Entry, Payment Requests, Spend Requests, Company Cards, Refunds
//     -> Finance (CFO explicit).
//   - Accounting & Control (ledger/reporting/BONU/debtors/investments/fixed
//     assets/asset control), Banking, Payables, Broker Commission -> Finance.
//     Not named individually, but this is exactly what "Finance" means to a
//     finance user, and there is nowhere else on the 9-domain rail for
//     day-to-day bookkeeping to live. BONU stays in Finance too — it was
//     already grouped there for its billing/payment lines (Lawyer Payments,
//     Fee Queries), not its claims content.
//   - Forgiveness watch, Adoption -> People, new "Management Insight" section
//     (CFO explicit: "under management insight").
//   - Graphite Aware, Graphite Feeds -> Data & AI (CFO explicit). Labels kept
//     exactly as-is (no rename) per the CFO's hard rule on this batch.
//   - Build log, Gate board, Scheduled jobs -> System > Platform Operations
//     (CFO explicit).
//   - Records Register -> Operations > Corporate Services (CFO explicit).
//   - Procurement -> Operations. Not named individually, but procurement
//     (POs, GRNs, supplier onboarding) is corporate/operational work, not
//     bookkeeping — an ops user looks for it here, not under Finance.
//   - Underwriting, Claims Recoveries, Reinsurance, Health Care, Instant
//     Insurance, Salvage Yard -> Insurance. Not named individually, but they
//     are the policy/claims/reinsurance lifecycle — a claims or UW user would
//     never look for a quote generator or a salvage yard under "Finance".
//     Reinsurance specifically moves out of the old Accounting group for the
//     same reason: ceding a treaty is an insurance decision, even though its
//     GL entries are booked through Accounting.
//   - Data Protection, Compliance, Internal Audit -> Risk & Assurance, as
//     separate sections, gates unchanged (CFO explicit).
//   - Audience Feedback (single-reader confidential screen) -> System. Not
//     named by the CFO; it has no natural business domain and was already
//     filed as "hide its existence from everyone but the one reader" — System
//     is where the rest of that kind of narrow admin-tier utility lives.
//   - IT Help Desk -> System > Help & Support, alongside User Manual / Report
//     a Bug / Bug Reports — the rest of the same "Support" cluster it used to
//     sit next to inside Overview.
//
// WhatsApp Reminders stays in Settings → Communications (CFO-only tool, not a
// front-of-house menu item). CFO Snapshot retired from the menu — its figures
// are static and do NOT reconcile to the GL, so it was a trap; the /cfo-snapshot
// route still exists but is no longer surfaced.

// The ADH settlement runs screen — Health or Finance only (CFO 2026-09-13).
// Named so the nav entry and the visibility filter can never drift apart.
const ADH_SETTLEMENTS_HREF = '/health/adh-settlements'

// Home — the executive/personal front door (CFO be335dfe explicit list only:
// Build log / Gate board / Scheduled jobs / Graphite Aware / Graphite Feeds /
// Adoption / Smart Entry moved OUT to their own domains below — Overview is
// no longer a catch-all).
const homeItems: NavItem[] = [
  // My Omni — the personal employee home (default front door for every user).
  { label: 'My Omni',       icon: Sparkles,        href: '/my-omni' },
  { label: 'Dashboard',     icon: LayoutDashboard, href: '/dashboard' },
  // CFO Dashboard retired 2026-08-22 — its control tiles merged into /dashboard
  // (CfoControlsSection, finance/CFO only). /cfo now redirects to /dashboard.
  { label: 'Intelligence Summary', icon: BarChart3, href: '/intel-summary' },
  // Transformation Board — "First AI Insurance Company in Botswana" programme
  // (20-Sep-2026 → 20-Jan-2027). CEO/CFO/COO/Chief Human Capital Officer only
  // (transformation/permissions.py CanViewTransformationBoard); the page
  // itself renders the 403 for anyone else, same pattern as every other
  // exec screen here — the link stays visible, the API gates it.
  { label: 'Transformation Board', icon: Rocket, href: '/transformation' },
]

// My Work — personal / self-service pages (CFO be335dfe explicit list), plus
// Rooms and employee Commissions folded in from their old single-item rail
// icons (see reasoning block above).
const myWorkItems: NavItem[] = [
  { label: 'Tasks',         icon: Inbox,           href: '/tasks' },
  { label: 'My League',     icon: Trophy,          href: '/my-league' },
  { label: 'Task Dashboard', icon: ListChecks,     href: '/task-dashboard' },
  { label: 'My Requests',    icon: ListChecks,     href: '/my-requests' },
  { label: 'My Approvals',   icon: Inbox,          href: '/my-approvals' },
  { label: 'My Sign-offs',   icon: ClipboardCheck, href: '/my-signoffs' },
  { label: 'My Equity',      icon: PieChart,       href: '/my-equity' },
  // Rooms — CFO icon spec: Rooms=CalendarDays.
  { label: 'Book a room',    icon: CalendarDays,   href: '/rooms' },
  // The employee-facing register only — Broker Commission stays in Finance.
  { label: 'Commissions',    icon: Coins,          href: '/commissions' },
]

// Finance — self-service spend tools (CFO be335dfe explicit list). Accounting
// & Control / Banking / Payables / Broker Commission are appended in the
// Finance module below (see reasoning block above).
const financeSelfServiceItems: NavItem[] = [
  { label: 'Smart Entry',    icon: Zap,            href: '/quick-entry' },
  { label: 'Payment Requests', icon: Banknote,     href: '/payment-requests' },
  { label: 'Spend Requests', icon: Receipt,        href: '/spend-requests' },
  // Company cards (CFO 2026-08-07) — load the statement, see what has no
  // receipt, and keep the card register. Backend is finance-gated; a
  // non-finance user who lands here is told so.
  { label: 'Company Cards',  icon: CreditCard,     href: '/company-cards' },
  { label: 'Refunds',        icon: Receipt,        href: '/refunds' },
]

// Data & AI (CFO be335dfe explicit: Graphite Aware, Graphite Feeds). Icons
// per CFO spec: Graphite Aware=Bot, Graphite Feeds=Database.
const dataAiItems: NavItem[] = [
  { label: 'Graphite Aware',icon: Bot,             href: '/aware' },
  { label: 'Graphite Feeds', icon: Database,       href: '/graphite-feeds' },
]

// System > Platform Operations (CFO be335dfe explicit). CFO-only, same gate
// as before (isTheCfo === true) — built conditionally in the modules memo.
// Icons per CFO spec: Gate board=GitBranch, Build log=ListChecks,
// Scheduled jobs=CalendarClock.
const platformOpsItems: NavItem[] = [
  // The CFO's own build log. Gated below for everybody else — the API
  // and the page are both gated too, this only keeps it off other people's menu.
  { label: 'Build log',     icon: ListChecks,      href: '/cfo/build-log' },
  // Live gate board — every /cfo/ item here is filtered to the CFO below.
  { label: 'Gate board',    icon: GitBranch,       href: '/cfo/ci' },
  { label: 'Scheduled jobs', icon: CalendarClock,  href: '/cfo/jobs' },
]

// CFO directive 2026-07-02: the rewards / wellness / Nexus surfaces are one
// programme — "Project Nexus". They get their own module (icon-rail entry)
// instead of crowding the Overview top list. Ungated, exactly as before.
const nexusItems: NavItem[] = [
  { label: 'Alpha Rewards', icon: Gift,       href: '/rewards' },
  { label: 'Staff Rewards', icon: Sparkles,   href: '/staff-rewards' },
  { label: 'Leaderboard',   icon: Trophy,     href: '/rewards/leaderboard' },
  { label: 'Redeem',        icon: Store,      href: '/rewards/redeem' },
  { label: 'Nexus Drive',   icon: Compass,    href: '/nexus' },
  { label: 'Thrive',        icon: HeartPulse, href: '/thrive' },
  { label: 'Nexus Testers', icon: Trophy,     href: '/rewards/nexus-testers' },
]

// CFO directive 2026-05-21 menu consolidation. Old layout had 20+ top-level
// entries; new layout collapses Reporting, Debtors, Investments, Fixed Assets
// and Reinsurance under Accounting; Petty Cash moves into Banking; and
// Approvals / Exceptions / Tax Calendar / Audit Log move into Administration.
// Section sub-headers (header:true items) keep each sub-area readable inside
// the expanded group without needing a second-level dropdown.
const groups: NavGroup[] = [
  {
    label: 'Accounting',
    icon: BookOpen,
    children: [
      // Ledger
      { label: 'Ledger',             icon: BookOpen,        href: '#', header: true },
      { label: 'Browse Accounts',    icon: Layers,          href: '/accounts' },
      { label: 'Journal Entries',    icon: FileSpreadsheet, href: '/journal-entries' },
      { label: 'Recurring JEs',      icon: RotateCw,        href: '/settings/recurring-journal-entries' },
      { label: 'TB / GL Upload',     icon: Upload,          href: '/upload' },
      // Reporting
      { label: 'Reporting',          icon: BarChart3,       href: '#', header: true },
      { label: 'Trial Balance',      icon: Scale,           href: '/reports/trial-balance' },
      { label: 'Profit & Loss',      icon: TrendingUp,      href: '/reports/profit-loss' },
      { label: 'P&L by Entity',      icon: TrendingUp,      href: '/reports/entity-pl' },
      { label: 'Balance Sheet',      icon: Layers,          href: '/reports/balance-sheet' },
      { label: 'IFRS 16 Leases',     icon: Building2,       href: '/leases' },
      { label: 'Consolidated',       icon: Layers,          href: '/reports/consolidated' },
      { label: 'Equity / Cap Table', icon: PieChart,        href: '/equity' },
      { label: 'Cash Position',      icon: CircleDollarSign,href: '/reports/cash-position' },
      { label: 'General Ledger',     icon: BookOpen,        href: '/reports/general-ledger' },
      { label: 'Budget vs Actual',   icon: Target,          href: '/reports/budget-vs-actual' },
      { label: 'Budget Library',     icon: Wallet,          href: '/budgets' },
      { label: 'Budget Cockpit',     icon: Sparkles,        href: '/budgets/simulator' },
      { label: '5-Year Plan Cockpit', icon: TrendingUp,      href: '/budgets/five-year' },
      { label: 'IFRS 17 Cockpit',   icon: Scale,           href: '/ifrs17' },
      // Finance 2026-08-04: the archive behind the cockpit, kept next to it so the
      // plan and its source of record are in one place.
      { label: 'Strategic Plan Library', icon: BookOpen,     href: '/budgets/five-year/library' },
      { label: 'Expense Analysis',   icon: PieChart,        href: '/reports/expense-analysis' },
      { label: 'VAT Return',         icon: Calculator,      href: '/reports/vat-return' },
      // B6 (Bontle Tendani). Finance-only, but the gate is on the API
      // view — hiding this entry protects nothing on its own.
      { label: 'Claims Payment Movement', icon: Receipt,     href: '/reports/claims-payment-movement' },
      { label: 'Reverse-Charge VAT', icon: Globe,           href: '/reverse-charge' },
      { label: 'Management Pack',    icon: Briefcase,       href: '/reports/management-pack' },
      { label: 'Market Benchmark',   icon: BarChart3,       href: '/reports/peer-benchmark' },
      { label: 'Bank Proofs (FNB)', icon: Landmark,        href: '/bank-proofs' },
      { label: 'Reconciliation Hub', icon: Scale,           href: '/reconciliation' },
      { label: 'Audit Pack (IFRS)',  icon: ClipboardCheck,  href: '/reports/audit-pack' },
      { label: 'Tax Pack (IAS 12)',  icon: Calculator,      href: '/reports/tax-reconciliation' },
      { label: 'TB → BS Trace',      icon: AlertTriangle,   href: '/reports/ma-trace' },
      { label: 'Draft JE Triage',    icon: ClipboardCheck,  href: '/reports/draft-triage' },
      { label: 'AI Integrity Scan',  icon: Sparkles,        href: '/reports/ai-integrity' },
      { label: 'Cash Flow',          icon: TrendingUp,      href: '/reports/cash-flow' },
      { label: 'AP Aging',           icon: Clock,           href: '/reports/ap-aging' },
      { label: 'Regulatory Capital', icon: Scale,           href: '/reports/regulatory-capital' },
      { label: 'Related-Party Txns', icon: Users,           href: '/reports/related-party-transactions' },
      { label: 'Budget Setup',       icon: Target,          href: '/reports/budget-setup' },
      { label: 'Asset Movement',     icon: FileSpreadsheet, href: '/reports/asset-movement' },
      { label: 'Exceptions Report',  icon: AlertTriangle,   href: '/reports/exceptions' },
      // BONU legal benefit — a screen nobody can find is a screen nobody uses, so all
      // four go in the menu, not just the overview (CFO go-live 2026-08-03).
      { label: 'BONU legal',         icon: Scale,           href: '#', header: true },
      { label: 'BONU Overview',      icon: Scale,           href: '/bonu' },
      { label: 'Claim Intake',       icon: ClipboardList,   href: '/bonu/intake' },
      { label: 'Legal Office',       icon: Gavel,           href: '/bonu/legal' },
      { label: 'Bills to Check',     icon: FileText,        href: '/bonu/inbox' },
      { label: 'Law Firm Panel',     icon: TrendingUp,      href: '/bonu/panel' },
      { label: 'Fee Queries',        icon: AlertTriangle,   href: '/bonu/queries' },
      { label: 'Lawyer Payments',    icon: Banknote,        href: '/bonu/payments' },
      // Debtors
      { label: 'Debtors',            icon: Users,           href: '#', header: true },
      { label: 'Customer Invoices',  icon: FileText,        href: '/invoices' },
      { label: 'AR Aging',           icon: Clock,           href: '/reports/ar-aging' },
      { label: 'Graphite Age Analysis', icon: Clock,        href: '/reports/age-analysis' },
      { label: 'Receipts',           icon: Receipt,         href: '/payments?type=receipt' },
      { label: 'Customers',          icon: UserCheck,       href: '/contacts?type=customer' },
      { label: 'Debtors Home',       icon: Users,           href: '/debtors' },
      // Investments
      { label: 'Investments',        icon: TrendingUp,      href: '#', header: true },
      { label: 'Portfolio',          icon: BarChart3,       href: '/investments' },
      { label: 'Transactions',       icon: FileSpreadsheet, href: '/investments/transactions' },
      // Fixed Assets
      { label: 'Fixed Assets',       icon: Boxes,           href: '#', header: true },
      { label: 'Asset Register',     icon: Boxes,           href: '/assets' },
      { label: 'Company Fleet',      icon: Navigation,      href: '/fleet' },
      { label: 'Vehicle Register',   icon: ListChecks,      href: '/vehicle-register' },
      { label: 'New Asset',          icon: Plus,            href: '/assets/new' },
      { label: 'Import from Odoo',   icon: Upload,          href: '/assets/import' },
      { label: 'Register Report',    icon: FileSpreadsheet, href: '/reports/asset-register' },
      // Asset Control & Handover (CFO spec 2026-09-02)
      { label: 'Asset Control',      icon: ClipboardCheck,  href: '#', header: true },
      { label: 'Requisitions',       icon: ClipboardList,   href: '/assets/requisitions' },
      { label: 'Approvals Queue',    icon: Inbox,           href: '/assets/approvals' },
      { label: 'Spare Pool',         icon: Package,         href: '/assets/spare-pool' },
      { label: 'My Assets',          icon: UserCheck,       href: '/assets/my-assets' },
      { label: 'Count Reconciliation', icon: Scale,         href: '/assets/reconciliation' },
      // Reinsurance moved OUT to its own `reinsuranceItems` array below
      // (Insurance domain, M-NAV-DOMAINS 19-Sep-2026) — see the placement
      // reasoning block above the old topItems declaration.
    ],
  },
  // HRIS sits between Accounting and Banking per CFO ordering (position 5).
  // Actual entry is rendered conditionally below via hrisGroup + access probe.
  {
    label: 'Banking',
    icon: Landmark,
    children: [
      { label: 'Banks',               icon: Landmark, href: '#', header: true },
      { label: 'Bank Reconciliation', icon: Scale,    href: '/banking' },
      { label: 'Bank Accounts',       icon: Landmark, href: '/bank-accounts' },
      { label: 'FNB Integration',     icon: Banknote, href: '/banking/fnb' },
      { label: 'FX Payment Planning', icon: CalendarClock, href: '/banking/fx-planning' },
      { label: 'Voucher Clearing',    icon: Eraser,   href: '/banking/voucher-clearing' },
      { label: 'Bank Feeds',          icon: RotateCw, href: '/bank-feeds' },
      { label: 'Collections',         icon: ArrowDownLeft, href: '#', header: true },
      { label: 'RealPay',             icon: ArrowDownLeft, href: '/banking/realpay' },
      { label: 'Debit Tracker',       icon: AlertTriangle, href: '/banking/realpay/collections/debit-tracker' },
      { label: 'Collections Dashboard', icon: BarChart3,   href: '/banking/realpay/collections/dashboard' },
      { label: 'Collections Analytics', icon: TrendingUp,  href: '/banking/realpay/collections/analytics' },
      // Upload-and-reconcile against Graphite (Keetile Mokhendo, 2026-08-17).
      { label: 'Reconcile vs Policies', icon: ArrowRightLeft, href: '/banking/realpay/collections/recon' },
      // The six monitoring reports off the same handover note.
      { label: 'Finance Monitoring',  icon: ShieldAlert, href: '/banking/realpay/monitoring' },
      // The Monday 07:30 chase list, and the address list Finance keep for it
      // (CFO 2026-09-11, replacing a hand-built Outlook email).
      { label: 'Failed Debits',       icon: Mail,     href: '/banking/realpay/failed-debits' },
      // Petty Cash nested per CFO ask.
      { label: 'Petty Cash',          icon: Coins,    href: '#', header: true },
      { label: 'Vouchers',            icon: Receipt,  href: '/petty-cash' },
      { label: 'New Voucher',         icon: Plus,     href: '/petty-cash/new' },
      { label: 'Reimbursements',      icon: Coins,    href: '/petty-cash/reimbursements' },
      { label: 'Top up the Tin',      icon: Plus,     href: '/petty-cash/reimbursements/new' },
    ],
  },
  {
    label: 'Payables',
    icon: CreditCard,
    children: [
      { label: 'Vendor Bills',     icon: FileText,  href: '/bills' },
      { label: 'AP Aging',         icon: Clock,     href: '/payables/aging' },
      { label: 'Supplier Recon',   icon: ShieldCheck, href: '/payables/recon' },
      { label: 'Payments',         icon: Banknote,  href: '/payments?type=payment' },
      { label: 'Payment Approvals', icon: ShieldCheck, href: '/payments/approvals' },
      { label: 'Vendors',          icon: Building2, href: '/vendors' },
      { label: 'Supplier Addresses', icon: Building2, href: '/vendors/addresses' },
    ],
  },
  {
    label: 'Claims Recoveries',
    icon: Coins,
    children: [
      { label: 'Claims Register', icon: FileSpreadsheet, href: '/claims/register' },
      // Claims automation end to end (CFO plan 19-Sep-2026): AI reading, drafted
      // Agreements of Loss and repudiations waiting for a person, drafted POs.
      { label: 'Claims Automation', icon: Bot, href: '/claims-automation' },
      { label: 'Claim Forms',  icon: FileText, href: '/claims/forms' },
      { label: 'Subrogations', icon: Coins,   href: '/claims/subrogations' },
      { label: 'Salvages',     icon: Package, href: '/claims/salvages' },
    ],
  },
  {
    label: 'Underwriting',
    icon: FileText,
    children: [
      { label: 'Document Generator', icon: FileText, href: '/underwriting' },
      // One standard quote template, filled from plain English (CFO/EXCO
      // 2026-08-08). Sits beside the cover-note / WCA generator because it is
      // the same job, done by the same people, in the same place.
      { label: 'Quotations',         icon: FileText, href: '/underwriting/quotes' },
      // Who actually USES the two tools above, and who is still doing it by
      // hand (CFO Amendment 3, 2026-09-08). Per-underwriter AI readiness.
      { label: 'Tool Adoption',      icon: TrendingUp, href: '/underwriting/adoption' },
      // Who renews in a chosen month, read from Graphite (Finance, [B1]
      // 14-Sep-2026). Replaces the monthly export-and-clean by hand.
      { label: 'Renewals',           icon: RefreshCw, href: '/renewals' },
    ],
  },
  {
    label: 'Procurement',
    icon: Package,
    children: [
      { label: 'Procurement Home',    icon: Package,     href: '/procurement' },
      { label: 'Purchase Orders',     icon: FileText,    href: '/purchase-orders' },
      { label: 'Goods Received Notes', icon: ClipboardCheck, href: '/goods-received' },
      { label: 'New PO',              icon: Plus,        href: '/purchase-orders/new' },
      // Kao (Claims) 2026-07-13: claims must be able to add a repairer /
      // supplier that isn't on the vendor list yet. The create form lived
      // only behind Payables → Vendors, which claims never open — surface
      // it here where the PO work actually happens.
      { label: 'Add Supplier / Repairer', icon: Plus,    href: '/contacts/new?type=vendor' },
      // Claims PO (assessment PDF → draft repairer/parts POs in omni).
      // Phase-4 review screen — now a native Next.js page (was the old
      // Caddy-served Odoo tool, retired with the omni port).
      { label: 'Claims PO (Assessments)', icon: Upload,  href: '/claims-po' },
      // Claims-PO analytics dashboard (CFO 2026-07-07) — spend / excess /
      // turnaround / repairer-vs-parts over department=claims POs.
      { label: 'Claims PO — Analytics', icon: BarChart3, href: '/claims-po/analytics' },
      { label: 'Vendor Bank Accounts', icon: ShieldCheck, href: '/vendor-banking' },
      { label: 'FX Revaluation',      icon: Globe,       href: '/fx-revaluation' },
    ],
  },
  // Build Brief 1 (Data department, 2-Sep-2026): the Instant Insurance book, and
  // the RealPay-vs-ledger check added after ~22,500 collections went unrecorded.
  {
    label: 'Instant Insurance',
    icon: ShieldCheck,
    children: [
      { label: 'Book & collections', icon: BarChart3, href: '/instant-insurance' },
    ],
  },
  // CFO directive 2026-05-26 — Health Care quick-quote (revenue stream)
  {
    label: 'Health Care',
    icon: Stethoscope,
    children: [
      // CFO directive 2026-07-02 — Overview hub leads the section; it surfaces
      // the live bordereaux GWP + book + pipeline + claims in one place.
      { label: 'Overview', icon: LayoutDashboard, href: '/health/dashboard' },
      // Health Cover — the member-facing plan + benefits page (CFO 2026-08-07),
      // ported from Steven Diaz's design mockup.
      { label: 'Health Cover', icon: HeartPulse, href: '/health-care/cover' },
      { label: 'Provider Network', icon: MapPin,     href: '/health-care/providers' },
      { label: 'Quick Quote', icon: Calculator, href: '/health/quick-quote' },
      { label: 'Quotations', icon: FileText, href: '/health/quotes' },
      // CFO directive 2026-06-05 — Tlamelo's Smart-Upload trackers.
      { label: 'Revenue (Bordereaux)', icon: FileSpreadsheet, href: '/healthcare/revenue' },
      { label: 'Claims (AFT)',         icon: Receipt,         href: '/healthcare/claims' },
      { label: 'Treaty (IN / OUT)',    icon: ShieldCheck,     href: '/healthcare/treaty' },
      { label: 'Vendor Onboarding',    icon: HeartPulse,      href: '/health/vendor-onboarding' },
      { label: 'Service Providers',   icon: Stethoscope,     href: '/health/service-providers' },
      { label: 'Network Dashboard',   icon: LayoutDashboard, href: '/health/provider-dashboard' },
      { label: 'ADH Dashboard',       icon: Activity,        href: '/health/adh-dashboard' },
      { label: 'AFA Load File',       icon: Send,            href: '/health/afa-load-file' },
      { label: 'ADH Settlement Runs', icon: Banknote,        href: ADH_SETTLEMENTS_HREF },
    ],
  },
  // CFO directive 2026-05-22 — NBFIRA Compliance module. Structure +
  // navigation only at this stage; the underlying pages are not yet built.
  {
    label: 'Compliance',
    icon: ClipboardCheck,
    children: [
      // Corporate Governance — the company-wide SOP + Policy library, open to
      // ALL staff (CFO directive 2026-07-27, Unami's request). Moved here from
      // the Banking menu, where a "SOP Bank" + bank icon read as a bank account
      // and nobody could find it. Placed first so it is unmissable.
      { label: 'Corporate Governance', icon: ShieldCheck, href: '#', header: true },
      { label: 'SOPs & Policies',      icon: FileText,    href: '/sop-bank' },
      // CFO directive 2026-07-02 — two collapsible folders (header:true rows
      // become accordion section titles): NBFIRA returns, and the ISO 27001
      // register. The ISO items lose their "ISO — " prefix since the folder
      // header already says ISO 27001.
      { label: 'NBFIRA',                          icon: LayoutDashboard, href: '#', header: true },
      { label: 'NBFIRA Dashboard',                icon: LayoutDashboard, href: '/compliance/nbfira' },
      { label: 'Quarterly Returns',               icon: Calendar,        href: '/compliance/nbfira/quarterly' },
      { label: 'Annual Returns',                  icon: Calendar,        href: '/compliance/nbfira/annual' },
      { label: 'Capital Adequacy / Solvency',     icon: Scale,           href: '/compliance/nbfira/capital-adequacy' },
      { label: 'Prudential Limits Monitor',       icon: AlertTriangle,   href: '/compliance/nbfira/prudential' },
      { label: 'Submission History & Audit Trail', icon: FileSpreadsheet, href: '/compliance/nbfira/submissions' },
      { label: 'Settings',                        icon: Settings,        href: '/compliance/nbfira/settings' },
      // CFO directive 2026-06-01 — ISO 27001 audit register (auditor-grade).
      { label: 'ISO 27001',                       icon: ShieldCheck,     href: '#', header: true },
      { label: '10 Commandments',                 icon: ShieldCheck,     href: '/compliance/iso' },
      { label: 'Statement of Applicability',      icon: ShieldCheck,     href: '/compliance/iso/soa' },
      { label: 'Risk Register',                   icon: AlertTriangle,   href: '/compliance/iso/risks' },
      { label: 'CAPA Register',                   icon: ClipboardCheck,  href: '/compliance/iso/capas' },
      { label: 'Policy Register',                 icon: FileSpreadsheet, href: '/compliance/iso/policies' },
      { label: 'Auditor Pack',                    icon: FileSpreadsheet, href: '/compliance/iso/audit-pack' },
      // CFO directive 2026-08-13 — Data Protection Officer register: DPIAs
      // (Data Protection Impact Assessments) under the Botswana DPA.
      { label: 'Data Protection',                 icon: ShieldCheck,     href: '#', header: true },
      { label: 'DPIA Register',                   icon: ClipboardCheck,  href: '/compliance/dpo' },
      { label: 'Policy & Legal Library',          icon: FileSpreadsheet, href: '/compliance/policy-library' },
      { label: 'ROPA Registry',                   icon: ClipboardCheck,  href: '/compliance/ropa' },
      { label: 'ROPA Vendor Register',            icon: FileSpreadsheet, href: '/compliance/ropa/vendors' },
      { label: 'ROPA Field Detector',            icon: ShieldCheck,     href: '/compliance/ropa-fields' },
    ],
  },
]

// Reinsurance — moved OUT of the Accounting group into the Insurance domain
// (M-NAV-DOMAINS, 19-Sep-2026): ceding a treaty is an insurance decision, even
// though its GL entries post through Accounting — a claims/UW user looks for
// it under Insurance, not Finance. Same items, routes and icons as before.
const reinsuranceItems: NavItem[] = [
  { label: 'Reinsurer Controls', icon: ShieldCheck,     href: '/reinsurance' },
  { label: 'FAC Risk Register',  icon: Layers,          href: '/reinsurance/fac-risk' },
  { label: 'Security Panel',     icon: Gauge,           href: '/reinsurance/security' },
  { label: 'Renewal 2026/27',    icon: Sparkles,        href: '/reinsurance/renewal' },
  { label: '11-Year Performance', icon: BarChart3,      href: '/reinsurance/history' },
  { label: 'Treaties',           icon: FileText,        href: '/reinsurance/treaties' },
  { label: 'Cessions',           icon: ArrowDownLeft,   href: '/reinsurance/cessions' },
  { label: 'Recoveries',         icon: ArrowUpRight,    href: '/reinsurance/recoveries' },
  { label: 'Reinsurers',         icon: Building2,       href: '/reinsurance/reinsurers' },
  { label: 'Bordereaux',         icon: FileSpreadsheet, href: '/reinsurance/bordereaux' },
]

// Internal Audit — full audit-management module (spec: Internal Audit Module,
// GIAS Jan-2024). Its own top-level module, rendered ONLY for the audit
// function + the exec/board viewers (me.can_view_internal_audit). Independence:
// editing is gated separately in-page on can_edit_internal_audit.
const internalAuditGroup: NavGroup = {
  label: 'Internal Audit',
  icon: ShieldCheck,
  children: [
    { label: 'Dashboard',           icon: BarChart3,      href: '/internal-audit' },
    { label: 'Findings Register',   icon: AlertTriangle,  href: '/internal-audit/findings' },
    { label: 'Follow-up Tracking',  icon: ClipboardCheck, href: '/internal-audit/follow-up' },
  ],
}

// CFO directive 2026-05-21: Administration group renamed → Settings.
// Fiscal Periods + Period Close moved here from Accounting → Ledger
// (period management is configuration, not day-to-day bookkeeping).
// Standalone bottom "Settings" link removed as redundant.
// CFO directive 2026-06-02 — Period Management is a Finance Manager
// capability, not CFO/admin-only. Split out of `adminGroup` so it can be
// rendered behind `can_manage_periods` (CFO / FM / FC / admin / super).
const periodMgmtGroup: NavGroup = {
  label: 'Period Management',
  icon: Lock,
  children: [
    { label: 'Period Management',    icon: Lock,            href: '/periods' },
    { label: 'Fiscal Periods',       icon: Calendar,        href: '/fiscal-periods' },
    { label: 'Period Close',         icon: Lock,            href: '/period-close' },
  ],
}

const adminGroup: NavGroup = {
  label: 'Settings',
  icon: Settings,
  children: [
    { label: 'Workflow',             icon: ShieldCheck,     href: '#', header: true },
    { label: 'Approvals',            icon: ShieldCheck,     href: '/approvals' },
    // Renamed from "Exceptions" (5-Sep-2026): a committee member opened this list
    // looking for the PAYMENT exceptions board, which lives behind the Exceptions
    // button on Payment Requests. Two screens, one name — so this one changed.
    { label: 'Anomalies',            icon: AlertTriangle,   href: '/exceptions' },
    { label: 'Tax Calendar',         icon: Calendar,        href: '/tax-calendar' },
    // B2: the VAT return reconciled back to the ledger. Sits next to the Tax
    // Calendar because that is where the 25th-of-the-month VAT due date lives.
    // Access is enforced on the endpoint (CanViewFinancials), not by hiding this.
    { label: 'VAT Reconciliation',   icon: Calculator,      href: '/tax/vat-recon' },
    { label: 'Audit Log',            icon: FileSpreadsheet, href: '/audit-log' },
    { label: 'Manus Activity',       icon: Bot,             href: '/manus-activity' },
    // Communications (CFO 2026-08-22) — moved out of the Overview top list.
    { label: 'Communications',       icon: MessageCircle,   href: '#', header: true },
    { label: 'WhatsApp Reminders',   icon: MessageCircle,   href: '/whatsapp' },
    { label: 'Configuration',        icon: Shield,          href: '#', header: true },
    { label: 'FX Rates (BoB)',       icon: Globe,           href: '/settings/fx-rates' },
    { label: 'Chart of Accounts',    icon: BookOpen,        href: '/settings/chart-of-accounts' },
    { label: 'Reinsurance Treaties', icon: Shield,          href: '/settings/reinsurance-treaties' },
    { label: 'Users & Permissions',  icon: UserCog,         href: '/settings/users' },
    { label: 'Roles & Hierarchy',    icon: Shield,          href: '/settings/roles' },
    { label: 'User-Entity Access',   icon: ShieldCheck,     href: '/settings/user-access' },
    { label: 'API Keys',             icon: Lock,            href: '/settings/api-keys' },
    { label: 'Secrets Vault',        icon: ShieldCheck,     href: '/settings/secrets' },
    { label: 'Frozen Controls',      icon: Lock,            href: '/settings/frozen-controls' },
    { label: 'Job Titles',           icon: UserCog,         href: '/settings/user-titles' },
    { label: 'Email IDs',            icon: Mail,            href: '/settings/user-emails' },
    { label: 'M365 Active Users',    icon: Users,           href: '/settings/m365-active-users' },
    { label: 'Digital Assistant',    icon: Sparkles,        href: '/settings/digital-assistant' },
    { label: 'Integrations',         icon: RotateCw,        href: '/integrations' },
    { label: 'CFO Upload',           icon: Upload,          href: '/cfo-upload' },
    { label: 'Settings Home',        icon: Settings,        href: '/settings' },
  ],
}

// Salvage Yard — VCM + ADIC only, gated by /salvage/me-can-access/ probe.
const salvageGroup: NavGroup = {
  label: 'Salvage Yard',
  icon: Boxes,
  children: [
    { label: 'Dashboard',       icon: LayoutDashboard, href: '/salvage' },
    { label: 'Inventory',       icon: Package,         href: '/salvage/inventory' },
    // Veritas declares what they hold for a written-off vehicle; the Agreement
    // of Loss cannot be settled until they do (CFO 19-Sep-2026).
    { label: 'Possession',      icon: PackageCheck,    href: '/salvage/possession' },
    // CFO 2026-09-10 — the Parts & Assessments team's monthly workbooks.
    { label: 'Parts & Savings', icon: TrendingUp,      href: '/salvage/parts' },
  ],
}

// HRIS — restricted to a 5-person whitelist (Prathap, Arun, Kago, Pako,
// Unami) plus superusers / admins, gated by /admin/hris-access/ probe.
// CFO directive 2026-05-18: payroll details live here, so visibility
// is hidden, not just blocked at the page. Probe loading → hide group
// (avoids flash); explicit `true` → show.
const hrisGroup: NavGroup = {
  label: 'HRIS',
  icon: UsersIcon,
  children: [
    // CFO directive 2026-07-02: bulk the HRIS surfaces into collapsible folders
    // (header:true rows become accordion titles) — Payroll, HR, Talent — so the
    // panel isn't one long flat list. Visibility is gated server-side too — only
    // HR / HR Manager / CFO / Finance Manager / Financial Controller see data.
    { label: 'Payroll',           icon: Wallet,    href: '#',                    header: true },
    { label: 'Payroll Dashboard', icon: LayoutDashboard, href: '/payroll/dashboard' },
    { label: 'Payroll',           icon: Wallet,    href: '/hris/payroll' },
    { label: 'Payroll Register',  icon: FileText,  href: '/payroll/payslips' },
    { label: 'Payroll · GL Mapping', icon: GitBranch, href: '/hris/payroll-setup' },
    { label: 'Run Payroll',       icon: Zap,       href: '/payroll/run' },
    // CFO 2026-07-26: payroll now needs an explicit CFO sign-off per entity per
    // month before payslips reach staff. Menu entry added with the feature —
    // Kago could not find Development Dialogue because it shipped without one.
    { label: 'Payroll Sign-off',  icon: ShieldCheck, href: '/payroll/sign-off' },
    // Monthly pack + incentive/authority control check (CFO 2026-08-24).
    { label: 'Monthly Pack & Checks', icon: ClipboardCheck, href: '/payroll/monthly-pack' },
    // Group Payroll Report — every payroll company in one saved monthly report (CFO 19-Sep-2026).
    { label: 'Group Payroll Report', icon: Layers, href: '/payroll/group-report' },
    // Backlog release (CFO payroll.docx §5, 2026-09-16). The automatic feed can
    // only write into the current month now, so an earlier month's approved
    // incentives and commissions need a door a person can walk through — a
    // register with no menu entry is a register nobody opens.
    { label: 'Release Backlog',   icon: Inbox,     href: '/payroll/backlog' },
    { label: 'Staff Loans',       icon: Banknote,  href: '/payroll/staff-loans' },
    { label: 'Payroll Employees', icon: UsersIcon, href: '/payroll/employees' },
    { label: 'All Payslips',      icon: Receipt,   href: '/hris/payslips' },
    { label: 'HR',                icon: UsersIcon, href: '#',                    header: true },
    { label: 'HRIS Home',         icon: LayoutDashboard, href: '/hris' },
    { label: 'Inbox',             icon: Inbox,     href: '/hris/inbox' },
    { label: 'People',            icon: UsersIcon, href: '/hris/directory' },
    { label: 'Onboard Employee',  icon: UserPlus,  href: '/hris/onboard' },
    // Contract register + 2/4/6-month renewal reminders (HC 19-Sep-2026).
    { label: 'Contracts',         icon: FileSignature, href: '/hris/contracts' },
    // Joiners + leavers (HC / Unami 19-Sep-2026): 30-day pack, and the offboarding steps
    // Omni requires before it archives anyone.
    { label: 'New Joiners',       icon: UserPlus,  href: '/hris/joiners' },
    { label: 'Training Academy',  icon: GraduationCap, href: '/hris/training' },
    { label: 'Leavers',           icon: UsersIcon, href: '/hris/offboarding' },
    // CFO 2026-07-26: this page already did job titles + reporting lines, but it
    // was palette-only and named "Payroll Amendments", so it read as a money
    // screen and nobody found it (the CFO asked for it to be built from scratch).
    // Same page, honest label, now in the menu.
    { label: 'Job Titles & Reporting Lines', icon: Pencil, href: '/hris/amendments' },
    { label: 'Documents',         icon: FileText,  href: '/hris/documents' },
    { label: 'Letters',           icon: Mail,      href: '/hris/letters' },
    { label: 'Leave Admin',       icon: Calendar,  href: '/hris/leave' },
    // Disciplinary cases — manager/HR raise → HR review → CFO sign-off for
    // suspension / dismissal → issued. Confidential HR record (CFO 2026-07-22).
    { label: 'Disciplinary',      icon: ShieldAlert, href: '/hris/disciplinary' },
    // Staff Incentive Approval — manager request → CFO + HR sign → payroll (CFO 2026-07-13).
    { label: 'Incentives',        icon: Coins,     href: '/hris/incentives' },
    // Staff + vehicle loans — apply → CFO approve → employee signs → Finance
    // releases, payment auto-loads, HR notified (CFO 2026-09-15; was HR 2026-07-15).
    { label: 'Staff Loans',       icon: Banknote,  href: '/hris/staff-loans' },
    // Leave Encashment + leave-pay provision register — basic ÷ 22 × days
    // (CFO 2026-07-21). HR/Finance raise → CFO signs → payroll.
    { label: 'Leave Encashment',  icon: Banknote,  href: '/hris/leave-encashment' },
    // ELRA-2025 monthly performance monitor — all employees (CFO 2026-07-02).
    { label: 'Performance Monitor', icon: Target,  href: '/hris/performance' },
    // Monthly manager feedback — short note + target check per report (CFO 2026-07-20).
    { label: 'Monthly Feedback',  icon: ClipboardCheck, href: '/hris/monthly-feedback' },
    // New HRIS feature pack (CFO 2026-07-21 — "excite Unami"): pulse, flight-risk,
    // skills, OKR tree, manager scorecard. HR team signs off each one in-page.
    { label: 'Pulse Check',       icon: HeartPulse,    href: '/hris/pulse' },
    { label: 'Flight-Risk Radar', icon: AlertTriangle, href: '/hris/flight-risk' },
    { label: 'Workforce Command Center', icon: AlertTriangle, href: '/hris/command-center' },
    { label: 'Skills & Gaps',     icon: Grid3x3,       href: '/hris/skills' },
    { label: 'OKR Alignment',     icon: GitBranch,     href: '/hris/okr-tree' },
    { label: 'Manager Scorecard', icon: Gauge,         href: '/hris/manager-scorecard' },
    { label: 'Time Doctor',       icon: Clock,     href: '/hris/time-doctor' },
    // Screen-Integrity Monitor — frozen-screen / weight-on-a-key exceptions,
    // pulled from the nightly Time Doctor sweep (CFO 2026-09-06).
    { label: 'Screen Integrity',  icon: ShieldAlert, href: '/hris/screen-integrity' },
    { label: 'Excuses feed',      icon: MessageCircle, href: '/hris/excuses' },
    // Leave Excuse Response — low/no productive-hours people for a day + their
    // explanation + Aria + Rule A/B auto-verdict. Exec/HR only (CFO 2026-07-22).
    { label: 'Leave Excuse',      icon: ClipboardList, href: '/hris/leave-excuse' },
    { label: 'Rewards',           icon: Sparkles,  href: '/hris/rewards' },
    // Talent Management cluster (CFO directive 2026-05-26 — Unami TMS Orbit parity).
    { label: 'Talent Management', icon: Target,    href: '#',                    header: true },
    // Development Dialogue self-service — staff were emailed "Talent Management →
    // Development Dialogue" but only /hris/profile linked it (Kago 2026-07-18).
    // T8 (board dd515fa8): this was a bare "Development Dialogue" — the same words
    // as the all-employees link below, which is the collision the CFO flagged.
    // Renamed to disambiguate; the all-employees one keeps its name so the
    // 19-Sep findability the CFO's boss relies on is untouched.
    { label: 'My Development Dialogue', icon: ClipboardCheck, href: '/hris/my-dialogue' },
    { label: 'Team Dialogues',    icon: Users,     href: '/hris/team-dialogues' },
    // The CENTRAL 9-grid + every employee's Development Dialogue (the Talent
    // Cockpit). It had no sidebar link at all and the palette called it "Talent
    // Cockpit", so searching "development dialogue" never found it — CFO 19-Sep-2026:
    // "it is one of the areas my boss likes to look at".
    { label: 'Development Dialogue (All Employees)', icon: Grid3x3, href: '/hris/talent-cockpit' },
    // The SHORT monthly accountability return (CFO 2026-07-26). Sits next to the
    // Development Dialogue because the year's returns roll up INTO it — the DD is
    // the big annual conversation, this is the five-minute monthly one.
    { label: 'Monthly Return',    icon: ClipboardCheck, href: '/hris/monthly-return' },
    // Unami decides these: a manager said someone isn't theirs / has left
    // (CFO 2026-07-26). The person stays on the roster until she decides.
    { label: 'Roster Flags',      icon: AlertTriangle, href: '/hris/roster-flags' },
    // HR runs its own team, reminders and locks (CFO 19-Sep-2026).
    { label: 'HR Settings',       icon: Settings2, href: '/hris/settings' },
    { label: 'Recruitment',       icon: Briefcase, href: '/recruitment' },
    { label: 'Offer Letters',     icon: FileText,  href: '/recruitment/offers' },
    // Restricted to the five signatories — the page itself says so to anyone
    // else, but the link stays visible so a signatory can always find it
    // (CFO 2026-08-03: a page with no menu link is a page nobody uses).
    { label: 'Authority to Recruit', icon: FileSignature, href: '/recruitment/authorities' },
    // Position tiers + salary bands (Unami Hiring-SOP, CFO 2026-09-02). Restricted
    // like the authorities page — the page itself gates it; the link stays visible.
    { label: 'Tiers & Salary Bands', icon: Layers, href: '/recruitment/tiers' },
    { label: '9-Box Grid',        icon: Grid3x3,   href: '/hris/ninebox' },
    { label: 'Succession',        icon: GitBranch, href: '/hris/succession' },
    { label: 'Dev Plan (IDP)',    icon: Compass,   href: '/hris/idp' },
    // Career Tracks — promotion-readiness records (CFO directive 2026-07-13).
    { label: 'Career Track',      icon: Route,     href: '/hris/career-track' },
    { label: 'AI Readiness',      icon: Sparkle,   href: '/hris/ai-readiness' },
    { label: 'HR Analytics',      icon: BarChart3, href: '/hr-analytics' },
  ],
}

// Employee Self-Service — the narrow HRIS slice EVERY staff member gets, even
// when they're not on the 5-person HRIS whitelist (CFO directive 2026-06-16,
// Lakshmi Anand / ADRisk bug aec2f3ce). Strictly own-data surfaces: log own
// leave, view own payslips, view/update own profile. Payroll, People
// directory, compensation and talent stay in the whitelist-only hrisGroup.
const hrisSelfServiceItems: NavItem[] = [
  { label: 'My Leave',    icon: Calendar,  href: '/hris/leave' },
  { label: 'My Payslips', icon: Receipt,   href: '/hris/payslips' },
  { label: 'My Profile',  icon: UserPlus,  href: '/hris/profile' },
  // Moved out of the Overview top list (CFO 2026-07-21) — staff loans is a
  // self-service HRIS surface (own-data only; route already whitelisted in
  // hris/layout.tsx SELF_SERVICE_ROUTES).
  { label: 'Staff Loans', icon: Banknote,  href: '/hris/staff-loans' },
  // Leave Encashment is self-service too — any employee applies to cash out
  // their own leave (CFO 2026-07-21). Own-data on the apply path; the register
  // + approvals are backend-gated to CFO/HR/Finance.
  { label: 'Leave Encashment', icon: Banknote, href: '/hris/leave-encashment' },
  // My Daily Brief — the workforce brief is emailed to every employee and the
  // shortfall-justification page is own-data only, so it belongs in self-service
  // (CFO 2026-07-23; staff hit the "HRIS is restricted" wall without it).
  { label: 'My Daily Brief', icon: Clock, href: '/hris/my-brief' },
  // Request a Letter — own-data self-service (employment/salary confirmation etc.);
  // route already whitelisted in hris/layout.tsx SELF_SERVICE_ROUTES. Was reachable
  // by direct URL only, missing from this menu (bug 1b9d6052, Oprah 2026-07-25).
  { label: 'Request a Letter', icon: FileText, href: '/hris/letters' },
  // Incentives — every line manager submits their team's incentives here, but
  // the only menu entry lived in the whitelist-only hrisGroup above, so a
  // manager who is not on the HRIS whitelist had NO way to reach the page
  // (Unami / Sechele 2026-07-30). Route is already in hris/layout.tsx
  // SELF_SERVICE_ROUTES and the backend gates submit to managers and above.
  { label: 'Incentives', icon: Coins, href: '/hris/incentives' },
  // Monthly Feedback — every line manager owes their team a monthly note, but
  // the only menu entry lived in the whitelist-only hrisGroup, so a manager who
  // is not one of the five had NO way to reach the page (Bharath, CFO
  // 2026-08-07). Route is in hris/layout.tsx TEAM_ROUTES and the backend scopes
  // it to the caller's own reports.
  { label: 'Monthly Feedback', icon: ClipboardCheck, href: '/hris/monthly-feedback' },
  // Induction must live HERE, not only in hrisGroup: the people who have to sit
  // it are new joiners, who are never on the five-person HRIS whitelist. Same
  // mistake as Incentives and Monthly Feedback above — opening the route in
  // hris/layout.tsx SELF_SERVICE_ROUTES is only half of it; without a menu
  // entry there is no way in. The backend is IsAuthenticated and self-scoped.
  { label: 'Induction', icon: GraduationCap, href: '/hris/induction' },
]

// Help & support cluster — pinned at the bottom of the old sidebar, now the
// tail section of the System module so every user keeps the same links.
const helpItems: NavItem[] = [
  // User Manual — single canonical how-to (CFO directive 2026-06-08).
  // Lives at /help. Everyone has read access (no role gate).
  { label: 'User Manual',     icon: BookOpen,       href: '/help' },
  // Report a System Bug (CFO directive 2026-06-10) — staff bug channel
  // that emails excoboard@ so people stop emailing the CFO directly.
  { label: 'Report a Bug',    icon: Bug,            href: '/report-bug' },
  // Bug Reports status board — role-aware: users see their own + status,
  // triagers see all + can move status (emails the reporter).
  { label: 'Bug Reports',     icon: ClipboardCheck, href: '/bug-reports' },
  // 10 Commandments — culture easter egg
  { label: '10 Commandments', icon: Sparkles,       href: '/commandments' },
  // IT Help Desk — moved in from the old Overview "Support" section
  // (M-NAV-DOMAINS, 19-Sep-2026): the rest of that cluster (User Manual,
  // Report a Bug) already lived here, so this was a stray duplicate location.
  { label: 'IT Help Desk',    icon: LifeBuoy,       href: '/helpdesk/', external: true },
]

// ─── Two-tier derivation (redesign pack 02, 2026-06-13) ─────────────────────
// The flat groups above stay the single source of truth. The icon rail +
// contextual panel are DERIVED from them — nothing is duplicated, so a route
// added to a group automatically appears in the panel.

interface NavSection {
  title: string
  items: NavItem[]
}

interface NavModule {
  key: string
  label: string
  /** Short name printed under the icon on the rail. Defaults to `label`.
      CFO 2026-08-06: the rail used to be icons only — "even I do not know where
      the features are, I have to keep my mouse over those icons". */
  short?: string
  icon: LucideIcon
  sections: NavSection[]
}

/** Split a group's children into sections at its header:true separators. */
function sectionize(children: NavItem[], fallbackTitle: string): NavSection[] {
  const sections: NavSection[] = []
  let current: NavSection = { title: fallbackTitle, items: [] }
  for (const item of children) {
    if (item.header) {
      if (current.items.length > 0) sections.push(current)
      current = { title: item.label, items: [] }
    } else {
      current.items.push(item)
    }
  }
  if (current.items.length > 0) sections.push(current)
  return sections
}

// ─── Rail domains (CFO be335dfe explicit list) ───────────────────────────────

export type DomainKey =
  | 'home' | 'mywork' | 'finance' | 'people' | 'insurance'
  | 'operations' | 'risk' | 'data-ai' | 'system'

/** Rail icon labels, in CFO-specified order. CommandPalette imports this so
 *  its group chips read from the same 9 names — sidebar and search agree. */
export const RAIL_DOMAINS: { key: DomainKey; label: string }[] = [
  { key: 'home', label: 'Home' },
  { key: 'mywork', label: 'My Work' },
  { key: 'finance', label: 'Finance' },
  { key: 'people', label: 'People' },
  { key: 'insurance', label: 'Insurance' },
  { key: 'operations', label: 'Operations' },
  { key: 'risk', label: 'Risk & Assurance' },
  { key: 'data-ai', label: 'Data & AI' },
  { key: 'system', label: 'System' },
]

/** Ground truth for every item the CFO's be335dfe / 3f2b2411 batch explicitly
 *  named a destination for: href -> the rail domain it must render under.
 *  Sidebar.tsx places every one of these hrefs inside the matching domain's
 *  item arrays above; the navDomains test suite checks that placement against
 *  this map, and CommandPalette's group labels are checked against it too, so
 *  the rail and the command palette can never quietly drift apart on these
 *  routes. Items the CFO left silent are NOT listed here — their placement
 *  reasoning lives in the comment block above homeItems instead. */
export const MOVED_ITEM_DOMAIN: Record<string, DomainKey> = {
  '/my-omni': 'home',
  '/dashboard': 'home',
  '/intel-summary': 'home',
  '/transformation': 'home',
  '/tasks': 'mywork',
  '/my-league': 'mywork',
  '/task-dashboard': 'mywork',
  '/my-requests': 'mywork',
  '/my-approvals': 'mywork',
  '/my-signoffs': 'mywork',
  '/my-equity': 'mywork',
  '/rooms': 'mywork',
  '/commissions': 'mywork',
  '/quick-entry': 'finance',
  '/payment-requests': 'finance',
  '/spend-requests': 'finance',
  '/company-cards': 'finance',
  '/refunds': 'finance',
  '/cfo/forgiveness': 'people',
  '/adoption': 'people',
  '/aware': 'data-ai',
  '/graphite-feeds': 'data-ai',
  '/cfo/build-log': 'system',
  '/cfo/ci': 'system',
  '/cfo/jobs': 'system',
  '/records': 'operations',
}

// ─── Gates ────────────────────────────────────────────────────────────────

/** Every permission probe buildNavModules needs, exactly as Sidebar.tsx reads
 *  them from its hooks/state. Passed in explicitly (not read from a closure)
 *  so this function is pure and unit-testable without rendering React. */
export interface NavGates {
  isTheCfo?: boolean
  canSeeForgiveness?: boolean
  me: UserProfile | null
  canAccessHris?: boolean
  canSelfServeHris?: boolean
  canAccessSalvage?: boolean | null
  canAccessDpa?: boolean
  canAccessCompliance?: boolean
  canAccessSecurity?: boolean
  canReadSpeakerFeedback?: boolean
  planVisible: boolean
  canAccessAdhSettlements?: boolean
  canManageUnicoin?: boolean
}

export function buildNavModules(gates: NavGates): NavModule[] {
  const mods: NavModule[] = []

    // Home — CFO be335dfe explicit list only (see the reasoning block above
    // the old topItems declaration for every move in this file).
    mods.push({
      key: 'home',
      label: 'Home',
      short: 'Home',
      icon: House,
      sections: [{ title: 'Home', items: homeItems }],
    })

    // My Work — personal / self-service pages (CFO be335dfe), plus Project
    // Nexus (rewards/wellness) folded in as its own section.
    mods.push({
      key: 'mywork',
      label: 'My Work',
      short: 'My Work',
      icon: Briefcase,
      sections: [
        { title: 'My Work', items: myWorkItems },
        { title: 'Rewards & Wellness', items: nexusItems },
      ],
    })

    // UniCoin — Instant Insurance agent commissions, its own module for portal
    // managers (CFO 2026-07-27). Gated on the manager probe so only the portal
    // managers see it; everyone else never does.
    //
    // HIDDEN 2026-09-01 (CFO, bug bc371a49): the CFO does not need the UniCoin >
    // Agent Commissions nav item right now, so the module is suppressed from the
    // rail. The /unicoin page is untouched and still reachable by URL — this only
    // removes the sidebar entry. Restore by removing the `false &&` guard.
    if (false && gates.canManageUnicoin === true) {
      mods.push({
        key: 'unicoin',
        label: 'UniCoin',
        short: 'UniCoin',
        icon: Users,
        sections: [{ title: 'Instant Insurance', items: [
          { label: 'Agent Commissions', icon: Users, href: '/unicoin' },
        ] }],
      })
    }

    // Finance — Spend & Payments (CFO be335dfe) + Accounting & Control +
    // Banking + Payables + Broker Commission (see reasoning block above).
    // The 5-Year Plan pages belong to AD Insurtech. Under any other single
    // entity they are hidden rather than shown with irrelevant content.
    const acctChildren = (groups[0].children || []).filter(
      (i) => gates.planVisible || !(ADIPL_ONLY_HREFS as readonly string[]).includes(i.href))
    const financeSections: NavSection[] = [
      { title: 'Spend & Payments', items: financeSelfServiceItems },
      ...sectionize(acctChildren, 'Ledger'),
    ]
    if (gates.me?.can_manage_periods || gates.me?.can_administer_users) {
      financeSections.push({ title: 'Period Management', items: periodMgmtGroup.children || [] })
    }
    const bankingGroup = groups.find(g => g.label === 'Banking')
    const payablesGroup = groups.find(g => g.label === 'Payables')
    if (bankingGroup) financeSections.push(...sectionize(bankingGroup.children || [], 'Banking'))
    if (payablesGroup) financeSections.push(...sectionize(payablesGroup.children || [], 'Payables'))
    // Broker Commission register (Rose Mokgware / CFO 2026-09-08) — a finance
    // register, not a personal page; employee Commissions moved to My Work.
    // Since board item [C8] the page answers to the "Broker Commission - Full
    // Access" role, not the commission-stage roster; anyone else who lands
    // here sees the refusal, same as before.
    financeSections.push({ title: 'Broker Commission', items: [
      { label: 'Broker Commission', icon: Building2, href: '/commissions/brokers' },
    ] })
    mods.push({ key: 'finance', label: 'Finance', short: 'Finance', icon: CircleDollarSign, sections: financeSections })

    // People — HRIS, whitelist gated (CFO directive 2026-05-18) + M-PEOPLE
    // consolidation (19-Sep) + Management Insight (Forgiveness watch,
    // Adoption — CFO be335dfe explicit: "under management insight").
    const peopleSections: NavSection[] = []
    if (gates.canSelfServeHris === true) {
      peopleSections.push({ title: 'My HR', items: hrisSelfServiceItems })
    }
    if (gates.canAccessHris === true) {
      peopleSections.push(...sectionize(hrisGroup.children || [], 'HRIS'))
    }
    // Self-service module (own leave / payslips / profile). Every employee with
    // the view_self capability gets it — INCLUDING HRIS-whitelist / HR / admin
    // users, who are also employees with their own leave & payslips. Previously
    // an `else if` here hid My Profile / My Leave / My Payslips from anyone on
    // the admin tier (bug 5ae38c73, Oprah Mogomotsi finance_manager 2026-07-22).

    // Adoption is ungated, exactly as it always was on the old flat Overview
    // list; Forgiveness watch keeps its CFO / can_see_forgiveness gate. Pushed
    // unconditionally (not behind gates.canSelfServeHris/gates.canAccessHris) so Adoption
    // stays visible to everyone who could see it before this change.
    const managementInsightItems: NavItem[] = [
      { label: 'Adoption', icon: TrendingUp, href: '/adoption' },
    ]
    if (gates.isTheCfo === true || gates.canSeeForgiveness === true) {
      managementInsightItems.push({ label: 'Forgiveness watch', icon: UserRoundCheck, href: '/cfo/forgiveness' })
    }
    peopleSections.push({ title: 'Management Insight', items: managementInsightItems })
    if (peopleSections.length) {
      mods.push({ key: 'people', label: 'People', short: 'People', icon: UsersIcon, sections: peopleSections })
    }

    // Insurance — Underwriting, Claims Recoveries, Reinsurance, Health Care,
    // Instant Insurance, Salvage Yard (see reasoning block above).
    const insuranceSections: NavSection[] = []
    const underwritingGroup = groups.find(g => g.label === 'Underwriting')
    const claimsGroup = groups.find(g => g.label === 'Claims Recoveries')
    const instantInsuranceGroup = groups.find(g => g.label === 'Instant Insurance')
    const healthGroup = groups.find(g => g.label === 'Health Care')
    if (underwritingGroup) insuranceSections.push(...sectionize(underwritingGroup.children || [], 'Underwriting'))
    if (claimsGroup) insuranceSections.push(...sectionize(claimsGroup.children || [], 'Claims Recoveries'))
    insuranceSections.push({ title: 'Reinsurance', items: reinsuranceItems })
    if (instantInsuranceGroup) insuranceSections.push(...sectionize(instantInsuranceGroup.children || [], 'Instant Insurance'))
    if (healthGroup) {
      // ADH Settlement Runs is Health-or-Finance only; drop the entry rather
      // than offer a refusal (CFO 2026-09-13) — unchanged from before.
      const healthChildren = (healthGroup.children || []).filter(
        (i) => i.href !== ADH_SETTLEMENTS_HREF || gates.canAccessAdhSettlements === true)
      insuranceSections.push(...sectionize(healthChildren, 'Health Care'))
    }
    if (gates.canAccessSalvage) {
      insuranceSections.push({ title: 'Salvage Yard', items: salvageGroup.children || [] })
    }
    mods.push({ key: 'insurance', label: 'Insurance', short: 'Insurance', icon: Shield, sections: insuranceSections })

    // Operations — Procurement + Records Register > Corporate Services (CFO
    // be335dfe explicit for Records; Procurement placed here — see reasoning
    // block above — as the corporate/operational work left once Banking /
    // Payables moved to Finance and Claims / Underwriting / Health moved to
    // Insurance).
    const procurementGroup = groups.find(g => g.label === 'Procurement')
    const opsSections: NavSection[] = []
    if (procurementGroup) opsSections.push(...sectionize(procurementGroup.children || [], 'Procurement'))
    // Records Register — kept its own section, NOT under Fixed Assets (CFO
    // 2026-08-06: "do not put this under the fixed asset register, keep a
    // separate tab"). Paper files must never sit in the register that feeds
    // asset reporting.
    opsSections.push({ title: 'Corporate Services', items: [
      { label: 'Records Register', icon: Archive, href: '/records' },
    ] })
    mods.push({ key: 'operations', label: 'Operations', short: 'Operations', icon: Settings2, sections: opsSections })

    // Risk & Assurance — Data Protection, Compliance, Internal Audit
    // consolidated as separate sections, each keeping its own gate exactly as
    // before (CFO be335dfe explicit).
    const riskSections: NavSection[] = []
    // Data Protection — the DPO's own folder (+ C-suite / HR / Finance Mgr),
    // server-gated. All the DPA registers in one place (CFO 2026-07-20).
    if (gates.canAccessDpa === true) {
      riskSections.push({ title: 'Data Protection', items: [
        { label: 'Dashboard',         icon: ShieldCheck,    href: '/data-protection' },
        { label: 'Monthly Checklist', icon: ClipboardCheck, href: '/data-protection#checklist' },
        { label: 'Breach Register',   icon: Shield,         href: '/data-protection#breaches' },
        { label: 'Data Requests',     icon: Inbox,          href: '/data-protection#dsr' },
        { label: 'DPIA',              icon: ClipboardCheck, href: '/data-protection#dpia' },
        { label: 'ROPA (register)',   icon: FileText,       href: '/data-protection#ropa' },
        { label: 'Privacy Notices',   icon: FileText,       href: '/data-protection#notices' },
        { label: 'Audit Scorecard',   icon: BarChart3,      href: '/data-protection#scorecard' },
      ] })
    }
    const complianceGroup = groups.find(g => g.label === 'Compliance')
    if (complianceGroup) {
      // AML officer overview sits at the top for those allowed (CFO 2026-07-24).
      if (gates.canAccessCompliance === true) {
        riskSections.push({ title: 'AML / Compliance', items: [
          { label: 'Compliance Overview', icon: ClipboardCheck, href: '/compliance/overview' },
          // The four AML registers (CFO 2026-09-16). They existed as tables from
          // 2026-09-09 with no screen, so the only way in was Django admin —
          // which the AML officer cannot reach.
          { label: 'Sanctions Screening', icon: ShieldAlert,   href: '/compliance/aml/sanctions' },
          { label: 'Supplier Screening',  icon: Building2,     href: '/compliance/aml/suppliers' },
          { label: 'AML Training',        icon: GraduationCap, href: '/compliance/aml/training' },
          { label: 'Board Reports',       icon: FileText,      href: '/compliance/aml/board-reports' },
        ] })
      }
      // Security Posture — CEO / COO / CFO only.
      if (gates.canAccessSecurity === true) {
        riskSections.push({ title: 'Security', items: [
          { label: 'Security Posture', icon: Shield, href: '/compliance/security' },
        ] })
      }
      riskSections.push(...sectionize(complianceGroup.children || [], 'Compliance'))
    }
    // Internal Audit — visible only to CEO / COO / CFO / board + the audit function.
    if (gates.me?.can_view_internal_audit) {
      riskSections.push(...sectionize(internalAuditGroup.children || [], 'Internal Audit'))
    }
    if (riskSections.length) {
      mods.push({ key: 'risk', label: 'Risk & Assurance', short: 'Risk', icon: BadgeCheck, sections: riskSections })
    }

    // Data & AI — Graphite Aware, Graphite Feeds (CFO be335dfe explicit).
    mods.push({ key: 'data-ai', label: 'Data & AI', short: 'Data & AI', icon: BrainCircuit, sections: [
      { title: 'Data & AI', items: dataAiItems },
    ] })

    // System — Platform Operations (Build log / Gate board / Scheduled jobs,
    // CFO-only, be335dfe explicit) + Audience Feedback (confidential,
    // single-reader — placement reasoning above) + admin Settings
    // (admin-gated, unchanged) + the help cluster every user always had at
    // the bottom of the old sidebar.
    const sysSections: NavSection[] = []
    if (gates.isTheCfo === true) {
      sysSections.push({ title: 'Platform Operations', items: platformOpsItems })
    }
    // Audience Feedback — the speaker's confidential pre-session responses
    // (CFO 2026-08-03). Only the listed reader sees this; not even admins.
    if (gates.canReadSpeakerFeedback) {
      sysSections.push({ title: 'Confidential', items: [
        { label: 'Audience Feedback', icon: Lock, href: '/speaker-feedback' },
      ] })
    }
    if (gates.me?.can_administer_users || gates.me?.is_access_delegate) {
      sysSections.push(...sectionize(adminGroup.children || [], 'Settings'))
    }
    sysSections.push({ title: 'Help & Support', items: helpItems })
    mods.push({ key: 'system', label: 'System', short: 'System', icon: Cog, sections: sysSections })

  return mods
}

export type { NavItem, NavGroup, NavSection, NavModule }
export {
  ADH_SETTLEMENTS_HREF, homeItems, myWorkItems, financeSelfServiceItems,
  dataAiItems, platformOpsItems, nexusItems, groups, reinsuranceItems,
  internalAuditGroup, periodMgmtGroup, adminGroup, salvageGroup, hrisGroup,
  hrisSelfServiceItems, helpItems, sectionize,
}
