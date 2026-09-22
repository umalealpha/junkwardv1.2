'use client'

/**
 * CommandPalette — global Cmd-K / Ctrl-K quick navigator.
 *
 * Built 2026-06-03 (CFO "Ferrari not Mazda" UX directive). Press ⌘K (Mac)
 * or Ctrl-K (Win) — or "/" when not typing in a field — to open a fuzzy
 * search over every destination + quick action in omni, hit Enter to jump.
 *
 * Pure additive overlay: it only calls router.push() to existing routes.
 * No route definitions, no API, no layout changes. Mounted once in the
 * dashboard layout.
 */

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { Search, CornerDownLeft, ArrowUp, ArrowDown } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { ADIPL_ONLY_HREFS, isAdiplPlanVisible, isCompanyResolving } from '@/lib/entityScope'

export interface Dest {
  label: string
  href: string
  group: string
  kw?: string   // extra keywords for fuzzy match
}

// Complete index of every user-facing destination (audited against
// frontend/src/app/(dashboard)/**/page.tsx + the sidebar on 2026-07-05 after
// the CFO couldn't find Bug Reports). If you ADD A PAGE, ADD IT HERE —
// searchability is part of shipping the page.
// Group labels agree with the Sidebar rail domains (M-NAV-DOMAINS, CFO
// be335dfe + 3f2b2411, 19-Sep-2026) for every route the CFO explicitly
// relocated — see lib/navModules.ts MOVED_ITEM_DOMAIN / RAIL_DOMAINS, and the
// navDomains test suite, which checks this file against that map. Routes the
// CFO left silent keep their existing, finer-grained palette group (e.g.
// "Banking", "Reinsurance") — that is more useful for search than collapsing
// everything to 9 names, and does not disagree with the sidebar since those
// groups were never claimed by a different domain to begin with.
export const DESTS: Dest[] = [
  // Core
  { label: 'Dashboard', href: '/dashboard', group: 'Home', kw: 'home kpi overview' },
  { label: 'Underwriting — Document Generator', href: '/underwriting', group: 'Underwriting', kw: 'cover note wca certificate worker compensation underwriting pdf certificate generator' },
  { label: 'Underwriting — Quotations (Quote Builder)', href: '/underwriting/quotes', group: 'Underwriting', kw: 'quote quotes quotation quotations quote builder underwriting premium proposal risk' },
  // CFO Dashboard merged into /dashboard and CFO Snapshot retired (CFO 2026-08-22)
  // — both removed from the palette so Ctrl-K no longer advertises them.
  { label: 'Security Posture', href: '/compliance/security', group: 'Compliance', kw: 'security risk vulnerability findings penetration test hardening breach posture ceo coo cfo remediation' },
  { label: 'Self-Explaining Dashboard (Frozen Drift)', href: '/frozen-drift', group: 'Core', kw: 'frozen figure drift narrator why numbers moved gwp pat total assets cash variance explain journal entries locked' },
  { label: 'Premium Lapse Early-Warning', href: '/premium-lapse', group: 'Core', kw: 'premium lapse collections failed debit shortfall gwp at risk policies churn cancel arrears recurring payment' },
  { label: 'Cost-per-Hour League', href: '/cost-per-hour', group: 'People', kw: 'cost per hour productive payroll time doctor department league efficiency salary productivity expensive cfo exco hr' },
  { label: 'Adoption Scoreboard', href: '/adoption', group: 'People', kw: 'usage adoption who using excel resistance' },
  { label: 'Graphite Aware', href: '/aware', group: 'Data & AI', kw: 'ai ask nl sql claims payability kyc' },
  { label: 'Smart Entry / Quick Entry', href: '/quick-entry', group: 'Finance', kw: 'journal manual upload tb je expense payment' },
  { label: 'Tasks', href: '/tasks', group: 'My Work' },
  { label: 'My League', href: '/my-league', group: 'My Work', kw: 'my league score rank reward incentive points task confirm waiting bwp fortnight standing' },
  { label: 'Task Dashboard', href: '/task-dashboard', group: 'My Work', kw: 'tasks oversight completion feedback team overdue planning' },
  { label: 'WhatsApp Reminders', href: '/whatsapp', group: 'Core', kw: 'whatsapp reminder message staff managers phone deadline overdue send cfo' },
  { label: 'Refunds', href: '/refunds', group: 'Finance', kw: 'refund reimbursement expense claim starlink travel invoice payment' },
  // BONU legal benefit. Keywords carry the words a person actually types — "divorce",
  // "lawyer", "retainer", the firm names — not just the module name.
  { label: 'BONU Overview', href: '/bonu', group: 'Core', kw: 'bonu legal benefit lawyer law firm attorney premium claims loss ratio revenue divorce conveyancing criminal' },
  { label: 'BONU — Bills to Check', href: '/bonu/inbox', group: 'Core', kw: 'bonu bill invoice upload law firm lawyer capture confirm ocr scan excel duplicate accountant waiting' },
  { label: 'BONU — Law Firm Panel', href: '/bonu/panel', group: 'Core', kw: 'bonu panel league table law firm lawyer rate tariff cost per matter concede query slow quiet retainer jeremiah taldi divorce typical unbilled' },
  { label: 'BONU — Fee Queries', href: '/bonu/queries', group: 'Core', kw: 'bonu query letter law firm lawyer overbilling chase reply recover credit dispute fee question' },
  { label: 'Staff Loans', href: '/hris/staff-loans', group: 'People', kw: 'staff loan vehicle loan car salary advance borrow credit blue book repayment hr instalment' },
  { label: 'Leave Encashment', href: '/hris/leave-encashment', group: 'People', kw: 'leave encashment cash out payout provision basic 22 daily rate leave pay' },
  { label: 'Disciplinary', href: '/hris/disciplinary', group: 'People', kw: 'discipline warning misconduct hr sanction evidence attachment' },
  { label: 'Incentives', href: '/hris/incentives', group: 'People', kw: 'incentive incentives staff reward bonus commission motor claims support member manager request cfo hr sign payroll recurring amend' },
  { label: 'Pulse Check', href: '/hris/pulse', group: 'People', kw: 'pulse mood engagement survey happiness sentiment' },
  { label: 'Flight-Risk Radar', href: '/hris/flight-risk', group: 'People', kw: 'flight risk attrition resignation retention leaver' },
  { label: 'Workforce Command Center', href: '/hris/command-center', group: 'People', kw: 'workforce command center monthly time doctor excuses salary at risk' },
  { label: 'Skills & Gaps', href: '/hris/skills', group: 'People', kw: 'skills gaps matrix competency training' },
  { label: 'Letters', href: '/hris/letters', group: 'People', kw: 'letter offer confirmation employment reference hr document' },
  { label: 'Career Track', href: '/hris/career-track', group: 'People', kw: 'career track promotion readiness development path' },
  { label: 'OKR Alignment', href: '/hris/okr-tree', group: 'People', kw: 'okr objectives key results goals alignment tree' },
  { label: 'Development Dialogue (All Employees)', href: '/hris/talent-cockpit', group: 'People', kw: 'development dialogue dialog all employees everyone central 9 grid 9-grid nine grid nine box 9 box talent cockpit performance evaluation potential appraisal review ceo board' },
  { label: 'Manager Scorecard', href: '/hris/manager-scorecard', group: 'People', kw: 'manager scorecard leadership rating team' },
  { label: 'Spend Requests', href: '/spend-requests', group: 'Finance', kw: 'event golf party training travel advance budget approval spend deepseek broker birthday entertainment sponsorship' },
  { label: 'Payment Requests', href: '/payment-requests', group: 'Finance', kw: 'payment authorisation authorization pay approve cfo paye premium cession towing beneficiary payee bank transfer release funds' },
  { label: 'My Requests', href: '/my-requests', group: 'My Work', kw: 'my requests status track leave loan petty cash payment application submitted where is it progress pending whose desk' },
  { label: 'My Approvals', href: '/my-approvals', group: 'My Work', kw: 'approvals approve inbox pending sign off signoff refunds spend purchase order queue awaiting' },
  { label: 'Reconciliation Hub', href: '/reconciliation', group: 'Core', kw: 'recon match' },
  { label: 'TB / GL Upload', href: '/upload', group: 'Core', kw: 'trial balance import' },
  { label: 'CFO Upload', href: '/cfo-upload', group: 'Core', kw: 'workbook ma' },
  { label: 'Integrations', href: '/integrations', group: 'Core' },
  // Help & Support
  { label: 'Bug Reports (triage board)', href: '/bug-reports', group: 'Help', kw: 'bug bugs board status triage defect issue error fix' },
  { label: 'Report a Bug', href: '/report-bug', group: 'Help', kw: 'bug defect issue error problem broken submit' },
  { label: 'User Manual', href: '/help', group: 'Help', kw: 'help docs how to guide whats new' },
  { label: 'IT Help Desk', href: '/helpdesk/', group: 'Help', kw: 'support ticket' },
  { label: '10 Commandments', href: '/commandments', group: 'Help', kw: 'rules security' },
  // Accounting
  { label: 'Journal Entries', href: '/journal-entries', group: 'Accounting', kw: 'je posting' },
  { label: 'Recurring Journal Entries', href: '/settings/recurring-journal-entries', group: 'Accounting', kw: 'rje standing' },
  { label: 'Chart of Accounts', href: '/settings/chart-of-accounts', group: 'Accounting', kw: 'coa gl accounts' },
  { label: 'Accounts', href: '/accounts', group: 'Accounting' },
  { label: 'Invoices', href: '/invoices', group: 'Accounting', kw: 'customer bill' },
  { label: 'New Invoice', href: '/invoices/new', group: 'Accounting' },
  { label: 'Bills (Payables)', href: '/bills', group: 'Accounting', kw: 'vendor ap payable' },
  { label: 'Payables Home', href: '/payables', group: 'Accounting', kw: 'ap vendor' },
  { label: 'Vendors', href: '/vendors', group: 'Accounting', kw: 'supplier' },
  { label: 'Supplier Addresses', href: '/vendors/addresses', group: 'Accounting', kw: 'supplier vendor address edit upload update' },
  { label: 'UniCoin — Instant Insurance', href: '/unicoin', group: 'Accounting', kw: 'commission sales agent unicoin payout payslip instant insurance' },
  { label: 'Commissions', href: '/commissions', group: 'My Work', kw: 'commission agent independent bdu payout withholding monthly submission form' },
  { label: 'Contacts', href: '/contacts?type=customer', group: 'Accounting', kw: 'customer' },
  { label: 'Debtors Home', href: '/debtors', group: 'Accounting', kw: 'ar receivable' },
  { label: 'Equity / Cap Table', href: '/equity', group: 'Accounting', kw: 'shares capital' },
  { label: 'Fiscal Periods', href: '/fiscal-periods', group: 'Accounting' },
  { label: 'Period Management', href: '/periods', group: 'Accounting', kw: 'lock close' },
  { label: 'Period Close', href: '/period-close', group: 'Accounting' },
  { label: 'FX Revaluation', href: '/fx-revaluation', group: 'Accounting', kw: 'forex currency' },
  { label: 'AP Aging', href: '/payables/aging', group: 'Accounting' },
  { label: 'Supplier Reconciliation', href: '/payables/recon', group: 'Accounting' },
  // Procurement
  { label: 'Procurement Home', href: '/procurement', group: 'Procurement', kw: 'purchasing' },
  { label: 'Purchase Orders', href: '/purchase-orders', group: 'Procurement', kw: 'po' },
  { label: 'New Purchase Order', href: '/purchase-orders/new', group: 'Procurement', kw: 'po raise' },
  { label: 'Add Supplier / Repairer', href: '/contacts/new?type=vendor', group: 'Procurement', kw: 'new vendor create contact repairer supplier' },
  { label: 'Claims PO (Assessments)', href: '/claims-po', group: 'Procurement', kw: 'assessor purchase order' },
  // Petty cash
  { label: 'Petty Cash', href: '/petty-cash', group: 'Petty Cash', kw: 'voucher float tin' },
  { label: 'New Petty Cash Voucher', href: '/petty-cash/new', group: 'Petty Cash' },
  { label: 'Petty Cash Reimbursements', href: '/petty-cash/reimbursements', group: 'Petty Cash', kw: 'top up tin' },
  // Banking
  { label: 'Bank Reconciliation', href: '/banking', group: 'Banking', kw: 'banking recon' },
  { label: 'Bank Accounts', href: '/bank-accounts', group: 'Banking' },
  { label: 'Bank Feeds', href: '/bank-feeds', group: 'Banking' },
  { label: 'FNB Integration', href: '/banking/fnb', group: 'Banking' },
  { label: 'FNB Connect', href: '/fnb-connect', group: 'Banking' },
  { label: 'Voucher Clearing', href: '/banking/voucher-clearing', group: 'Banking', kw: 'clearing 2801' },
  { label: 'Corporate Governance — SOPs & Policies', href: '/sop-bank', group: 'Compliance', kw: 'sop sops policy policies procedures governance corporate standard operating procedure hr finance department iso 9001 manual document library' },
  { label: 'RealPay', href: '/banking/realpay', group: 'Banking', kw: 'collections debit order' },
  { label: 'RealPay — Botswana', href: '/banking/realpay/botswana', group: 'Banking' },
  { label: 'RealPay — South Africa', href: '/banking/realpay/south-africa', group: 'Banking' },
  { label: 'Debit Tracker', href: '/banking/realpay/collections/debit-tracker', group: 'Banking', kw: 'collections' },
  { label: 'Collections Dashboard', href: '/banking/realpay/collections/dashboard', group: 'Banking' },
  { label: 'Collections Analytics', href: '/banking/realpay/collections/analytics', group: 'Banking' },
  { label: 'Vendor Bank Accounts', href: '/vendor-banking', group: 'Banking', kw: 'vendor banking' },
  { label: 'Payments', href: '/payments?type=payment', group: 'Banking', kw: 'pay outbound' },
  { label: 'New Payment', href: '/payments/new', group: 'Banking' },
  { label: 'Receipts', href: '/payments?type=receipt', group: 'Banking' },
  { label: 'Payment Approvals', href: '/payments/approvals', group: 'Banking', kw: 'authorise outbound' },
  // Reports
  { label: 'Reports Home', href: '/reports', group: 'Reports' },
  { label: 'Trial Balance', href: '/reports/trial-balance', group: 'Reports', kw: 'tb' },
  { label: 'Profit & Loss', href: '/reports/profit-loss', group: 'Reports', kw: 'pl income statement' },
  { label: 'P&L by Entity', href: '/reports/entity-pl', group: 'Reports' },
  { label: 'Balance Sheet', href: '/reports/balance-sheet', group: 'Reports', kw: 'bs' },
  { label: 'Cash Flow', href: '/reports/cash-flow', group: 'Reports' },
  { label: 'General Ledger', href: '/reports/general-ledger', group: 'Reports', kw: 'gl extract' },
  { label: 'Management Pack', href: '/reports/management-pack', group: 'Reports', kw: 'ma board' },
  { label: 'Cash Position', href: '/reports/cash-position', group: 'Reports' },
  { label: 'AR Aging', href: '/reports/ar-aging', group: 'Reports' },
  { label: 'AP Aging Report', href: '/reports/ap-aging', group: 'Reports' },
  { label: 'Graphite Age Analysis', href: '/reports/age-analysis', group: 'Reports', kw: 'debtors age' },
  { label: 'VAT Return', href: '/reports/vat-return', group: 'Reports', kw: 'tax' },
  { label: 'Claims Payment Movement', href: '/reports/claims-payment-movement', group: 'Reports', kw: 'claims payment vat degross invoice aol cil bontle' },
  { label: 'Tax Pack (IAS 12)', href: '/reports/tax-reconciliation', group: 'Reports', kw: 'deferred tax' },
  { label: 'Consolidated', href: '/reports/consolidated', group: 'Reports', kw: 'group' },
  { label: 'Budget vs Actual', href: '/reports/budget-vs-actual', group: 'Reports' },
  { label: 'Budget Cockpit', href: '/budgets/simulator', group: 'Reports', kw: 'budget simulator cockpit fy27 forecast cashflow balance sheet bi prophix what-if' },
  { label: 'Budget Library', href: '/budgets', group: 'Reports', kw: 'budget pack fy27 library' },
  { label: '5-Year Plan Cockpit', href: '/budgets/five-year', group: 'Reports', kw: '5 year five plan cockpit insurtech fy30 valuation funding investor segment scenario' },
  { label: 'Budget Setup', href: '/reports/budget-setup', group: 'Reports', kw: 'budget upload csv' },
  { label: 'Expense Analysis', href: '/reports/expense-analysis', group: 'Reports' },
  { label: 'Audit Pack (IFRS)', href: '/reports/audit-pack', group: 'Reports' },
  { label: 'TB → BS Trace', href: '/reports/ma-trace', group: 'Reports', kw: 'trace mapping' },
  { label: 'Draft JE Triage', href: '/reports/draft-triage', group: 'Reports' },
  { label: 'AI Integrity Scan', href: '/reports/ai-integrity', group: 'Reports' },
  { label: 'Regulatory Capital', href: '/reports/regulatory-capital', group: 'Reports', kw: 'car solvency' },
  { label: 'Related-Party Transactions', href: '/reports/related-party-transactions', group: 'Reports', kw: 'rpt intercompany' },
  { label: 'Asset Movement', href: '/reports/asset-movement', group: 'Reports' },
  { label: 'Asset Register Report', href: '/reports/asset-register', group: 'Reports' },
  { label: 'Exceptions Report', href: '/reports/exceptions', group: 'Reports' },
  // Claims
  { label: 'Claims Register', href: '/claims/register', group: 'Claims', kw: 'graphite claims' },
  { label: 'Claims Recoveries', href: '/claims-recoveries', group: 'Claims' },
  { label: 'Subrogations', href: '/claims/subrogations', group: 'Claims', kw: 'recovery third party' },
  { label: 'Salvages (Claims)', href: '/claims/salvages', group: 'Claims' },
  { label: 'Claims Import', href: '/claims/import', group: 'Claims' },
  // Salvage yard
  { label: 'Salvage Yard', href: '/salvage', group: 'Salvage' },
  { label: 'Salvage Inventory', href: '/salvage/inventory', group: 'Salvage' },
  { label: 'Parts & Savings', href: '/salvage/parts', group: 'Salvage', kw: 'parts assessment savings suppliers veritas' },
  { label: 'Salvage Quotes', href: '/salvage/quotes', group: 'Salvage', kw: 'buyer' },
  { label: 'Salvage Sales', href: '/salvage/sales', group: 'Salvage' },
  { label: 'Salvage Approvals', href: '/salvage/approvals', group: 'Salvage' },
  // Reinsurance
  { label: 'Reinsurance Home', href: '/reinsurance', group: 'Reinsurance', kw: 'ri' },
  { label: 'Reinsurer Controls', href: '/reinsurance', group: 'Reinsurance', kw: 'counterparty security onboarding' },
  { label: 'FAC Risk Register', href: '/reinsurance/fac-risk', group: 'Reinsurance', kw: 'facultative exposure retained' },
  { label: 'Security Panel', href: '/reinsurance/security', group: 'Reinsurance', kw: 'rating concentration' },
  { label: 'Renewal 2026/27', href: '/reinsurance/renewal', group: 'Reinsurance' },
  { label: '11-Year Performance', href: '/reinsurance/history', group: 'Reinsurance' },
  { label: 'Reinsurance Treaties', href: '/reinsurance/treaties', group: 'Reinsurance' },
  { label: 'Reinsurance Cessions', href: '/reinsurance/cessions', group: 'Reinsurance' },
  { label: 'Reinsurance Recoveries', href: '/reinsurance/recoveries', group: 'Reinsurance' },
  { label: 'Reinsurers', href: '/reinsurance/reinsurers', group: 'Reinsurance' },
  { label: 'Bordereaux', href: '/reinsurance/bordereaux', group: 'Reinsurance' },
  // Health care
  { label: 'Health Overview', href: '/health/dashboard', group: 'Health', kw: 'healthcare medical' },
  { label: 'Health Quick Quote', href: '/health/quick-quote', group: 'Health', kw: 'insurance' },
  { label: 'Health Quotations', href: '/health/quotes', group: 'Health', kw: 'group quote' },
  { label: 'Health Revenue (Bordereaux)', href: '/healthcare/revenue', group: 'Health', kw: 'gwp' },
  { label: 'Health Claims (AFT)', href: '/healthcare/claims', group: 'Health' },
  { label: 'Health Treaty (IN / OUT)', href: '/healthcare/treaty', group: 'Health' },
  { label: 'Health Vendor Onboarding', href: '/health/vendor-onboarding', group: 'Health', kw: 'provider' },
  { label: 'ADH Dashboard', href: '/health/adh-dashboard', group: 'Health', kw: 'adh healthcare provider growth' },
  // Investments + assets
  { label: 'Investments Portfolio', href: '/investments', group: 'Investments' },
  { label: 'Investment Transactions', href: '/investments/transactions', group: 'Investments' },
  { label: 'Fixed Assets (Register)', href: '/assets', group: 'Assets', kw: 'ppe depreciation asset register' },
  { label: 'New Fixed Asset', href: '/assets/new', group: 'Assets', kw: 'capitalize' },
  { label: 'Assets — Import from Odoo', href: '/assets/import', group: 'Assets' },
  { label: 'Asset Sign-offs', href: '/assets/signoffs', group: 'Assets', kw: 'handover transfer' },
  { label: 'Company Fleet (Cartrack)', href: '/fleet', group: 'Assets', kw: 'cartrack vehicle car tracking gps fleet company cars location' },
  { label: 'Vehicle Register (pool cars)', href: '/vehicle-register', group: 'Assets', kw: 'vehicle register pool car checkout check in checkin logbook trip driver keys reception book' },
  // HRIS + payroll
  { label: 'HRIS Home', href: '/hris', group: 'People', kw: 'human resources staff' },
  { label: 'HR Inbox', href: '/hris/inbox', group: 'People', kw: 'approvals hr' },
  { label: 'Directory (People)', href: '/hris/directory', group: 'People', kw: 'employees staff' },
  { label: 'HR Documents', href: '/hris/documents', group: 'People', kw: 'vault files contracts' },
  { label: 'Recruitment (ATS)', href: '/recruitment', group: 'People', kw: 'ats hiring vacancy candidate cv job requisition talent match' },
  { label: 'Payroll (HRIS)', href: '/hris/payroll', group: 'People', kw: 'salary payslip amendments' },
  { label: 'Payroll Dashboard', href: '/payroll/dashboard', group: 'People' },
  { label: 'Run Payroll', href: '/payroll/run', group: 'People' },
  { label: 'Payroll Sign-off', href: '/payroll/sign-off', group: 'People', kw: 'approve sign off cfo payslip release' },
  { label: 'Payroll Register', href: '/payroll/payslips', group: 'People', kw: 'line export' },
  { label: 'Payroll Employees', href: '/payroll/employees', group: 'People' },
  { label: 'Payroll · GL Mapping', href: '/hris/payroll-setup', group: 'People', kw: 'setup accounts' },
  // CFO 2026-07-26: was "Payroll Amendments" with money-only keywords, so nobody
  // found it when they wanted to change a job title or a reporting line.
  { label: 'Job Titles & Reporting Lines', href: '/hris/amendments', group: 'People', kw: 'job title designation promote reports to reporting line manager line manager leave approver org chart amendment salary change hire terminate bonus' },
  { label: 'Payslips', href: '/hris/payslips', group: 'People' },
  { label: 'Leave Admin', href: '/hris/leave', group: 'People', kw: 'annual sick maternity' },
  { label: 'Leave Report', href: '/hris/leave/report', group: 'People' },
  { label: 'Performance Monitor', href: '/hris/performance', group: 'People', kw: 'check-in elra' },
  { label: 'Monthly Feedback', href: '/hris/monthly-feedback', group: 'People', kw: 'performance feedback target manager monthly' },
  { label: 'Company Cards', href: '/company-cards', group: 'Finance', kw: 'credit card statement receipt spend register missing' },
  { label: 'Time Doctor', href: '/hris/time-doctor', group: 'People', kw: 'attendance hours' },
  { label: 'My Daily Brief', href: '/hris/my-brief', group: 'People', kw: 'hours justify shortfall brief' },
  { label: 'Excuses feed', href: '/hris/excuses', group: 'People', kw: 'excuses reasons missed hours power cut watch list leave accountability' },
  { label: 'Leave Excuse Response', href: '/hris/leave-excuse', group: 'People', kw: 'leave excuse response productive hours low no power cut load shedding time doctor tracker it ticket not accepted aria auto reply' },
  { label: 'HRIS Rewards', href: '/hris/rewards', group: 'People' },
  { label: 'Transfers', href: '/hris/transfers', group: 'People', kw: 'employee move' },
  { label: 'ITW8 / Tax Certificates', href: '/hris/itw8', group: 'People', kw: 'paye burs' },
  { label: 'Bonus Simulation', href: '/hris/bonus-simulation', group: 'People' },
  { label: 'Alerts & Reminders', href: '/hris/alerts', group: 'People', kw: 'contract review' },
  { label: '9-Box Grid', href: '/hris/ninebox', group: 'People', kw: 'talent 9 grid 9-grid nine grid nine box performance potential hipo succession' },
  { label: 'Succession', href: '/hris/succession', group: 'People' },
  { label: 'Development Dialogue (mine)', href: '/hris/my-dialogue', group: 'People', kw: 'development dialogue dialog my own self assessment' },
  { label: 'Development Dialogue (my team)', href: '/hris/team-dialogues', group: 'People', kw: 'team dialogues development dialogue dialog manager sign off' },
  { label: 'Roster Flags (Unami decides)', href: '/hris/roster-flags', group: 'People',
    kw: 'not my report resigned left the company wrong team roster flag unami decide' },
  { label: 'Monthly Return (manager)', href: '/hris/monthly-return', group: 'People',
    kw: 'monthly return manager accountability feedback team hours overstaffed innovation fy27 sla declaration' },
  { label: 'Dev Plan (IDP)', href: '/hris/idp', group: 'People' },
  { label: 'AI Readiness', href: '/hris/ai-readiness', group: 'People' },
  { label: 'Career', href: '/hris/career', group: 'People' },
  { label: 'Assess', href: '/hris/assess', group: 'People', kw: 'assessment' },
  { label: 'My Profile', href: '/hris/profile', group: 'People', kw: 'self service ess' },
  { label: 'HR Analytics', href: '/hr-analytics', group: 'People' },
  // Rewards & Nexus
  { label: 'Alpha Rewards', href: '/rewards', group: 'Rewards', kw: 'points steps meal' },
  { label: 'Staff Rewards', href: '/staff-rewards', group: 'Rewards' },
  { label: 'Rewards Leaderboard', href: '/rewards/leaderboard', group: 'Rewards' },
  { label: 'Redeem Rewards', href: '/rewards/redeem', group: 'Rewards' },
  { label: 'Nexus Testers', href: '/rewards/nexus-testers', group: 'Rewards' },
  { label: 'Nexus Drive', href: '/nexus', group: 'Rewards', kw: 'telematics driving score webfleet' },
  { label: 'Thrive', href: '/thrive', group: 'Rewards', kw: 'wellness' },
  // Compliance
  { label: 'ISO 27001 — 10 Commandments', href: '/compliance/iso', group: 'Compliance', kw: 'security audit' },
  { label: 'ISO — Statement of Applicability', href: '/compliance/iso/soa', group: 'Compliance' },
  { label: 'ISO — Risk Register', href: '/compliance/iso/risks', group: 'Compliance' },
  { label: 'ISO — CAPA Register', href: '/compliance/iso/capas', group: 'Compliance' },
  { label: 'ISO — Policy Register', href: '/compliance/iso/policies', group: 'Compliance' },
  { label: 'ISO — Auditor Pack', href: '/compliance/iso/audit-pack', group: 'Compliance' },
  { label: 'Data Protection — DPIA Register', href: '/compliance/dpo', group: 'Compliance', kw: 'dpia dpo data protection impact assessment privacy special category health dpa' },
  { label: 'NBFIRA Dashboard', href: '/compliance/nbfira', group: 'Compliance', kw: 'regulator returns' },
  { label: 'NBFIRA Quarterly Returns', href: '/compliance/nbfira/quarterly', group: 'Compliance' },
  { label: 'NBFIRA Annual Returns', href: '/compliance/nbfira/annual', group: 'Compliance' },
  { label: 'Capital Adequacy / Solvency', href: '/compliance/nbfira/capital-adequacy', group: 'Compliance', kw: 'car' },
  { label: 'Prudential Limits Monitor', href: '/compliance/nbfira/prudential', group: 'Compliance' },
  { label: 'NBFIRA Submission History', href: '/compliance/nbfira/submissions', group: 'Compliance', kw: 'audit trail' },
  { label: 'NBFIRA Settings', href: '/compliance/nbfira/settings', group: 'Compliance' },
  { label: 'Approvals', href: '/approvals', group: 'Compliance' },
  { label: 'Anomalies', href: '/exceptions', group: 'Compliance' },
  { label: 'Payment exceptions (committee)', href: '/payment-requests?exceptions=1', group: 'Compliance' },
  { label: 'Audit Log', href: '/audit-log', group: 'Compliance' },
  { label: 'Tax Calendar', href: '/tax-calendar', group: 'Compliance' },
  { label: 'VAT Reconciliation', href: '/tax/vat-recon', group: 'Compliance', kw: 'vat return burs output input net payable refundable' },
  // Settings
  { label: 'Settings Home', href: '/settings', group: 'Settings' },
  { label: 'Users & Permissions', href: '/settings/users', group: 'Settings' },
  { label: 'Roles & Hierarchy', href: '/settings/roles', group: 'Settings' },
  { label: 'User-Entity Access', href: '/settings/user-access', group: 'Settings', kw: 'company access' },
  { label: 'Bulk Access', href: '/settings/bulk-access', group: 'Settings' },
  { label: 'Companies', href: '/settings/companies', group: 'Settings', kw: 'entities' },
  { label: 'API Keys', href: '/settings/api-keys', group: 'Settings' },
  { label: 'Secrets Vault', href: '/settings/secrets', group: 'Settings', kw: 'llm keys deepseek gemini' },
  { label: 'Frozen Controls', href: '/settings/frozen-controls', group: 'Settings', kw: 'adic governance freeze' },
  { label: 'Job Titles', href: '/settings/user-titles', group: 'Settings' },
  { label: 'Email IDs', href: '/settings/user-emails', group: 'Settings' },
  { label: 'FX Rates (BoB)', href: '/settings/fx-rates', group: 'Settings' },
  { label: 'Reinsurance Treaty Settings', href: '/settings/reinsurance-treaties', group: 'Settings' },
  { label: 'M365 Active Users', href: '/settings/m365-active-users', group: 'Settings' },
  { label: 'Digital Assistant', href: '/settings/digital-assistant', group: 'Settings', kw: 'aria telegram' },
]

/** Exported so the findability test scores with the REAL rule, not a copy. */
export function score(d: Dest, q: string): number {
  const hay = `${d.label} ${d.group} ${d.kw || ''}`.toLowerCase()
  const label = d.label.toLowerCase()
  if (label === q) return 100
  if (label.startsWith(q)) return 80
  if (label.includes(q)) return 60
  if (hay.includes(q)) return 40
  // subsequence (fuzzy) match
  let i = 0
  for (const ch of hay) { if (ch === q[i]) i++; if (i === q.length) return 20 }
  return -1
}

export function CommandPalette() {
  const router = useRouter()
  const { theme } = useTheme()
  const { selected: selectedCompany, selectedId: selectedCompanyId } = useCompany()
  // Same rule as the sidebar: unresolved must not read as allowed.
  const planVisible = !isCompanyResolving(selectedCompanyId, selectedCompany)
    && isAdiplPlanVisible(selectedCompany?.code)
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  // The AD Insurtech plan pages are hidden from the sidebar under other
  // entities; the palette must not keep offering them (bug 2026-08-06).
  const dests = useMemo(() => (
    planVisible
      ? DESTS
      : DESTS.filter((d) => !(ADIPL_ONLY_HREFS as readonly string[]).includes(d.href))
  ), [planVisible])

  const results = useMemo(() => {
    const query = q.trim().toLowerCase()
    if (!query) return dests.slice(0, 8)
    return dests
      .map((d) => ({ d, s: score(d, query) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 12)
      .map((x) => x.d)
  }, [q, dests])

  // Global hotkey: Cmd/Ctrl-K, or "/" outside inputs.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable
      if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
        e.preventDefault(); setOpen((o) => !o)
      } else if (e.key === '/' && !typing && !open) {
        e.preventDefault(); setOpen(true)
      } else if (e.key === 'Escape' && open) {
        setOpen(false)
      }
    }
    const onOpen = () => setOpen(true)
    window.addEventListener('keydown', onKey)
    window.addEventListener('omni:cmdk', onOpen as EventListener)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('omni:cmdk', onOpen as EventListener)
    }
  }, [open])

  useEffect(() => {
    if (open) { setQ(''); setHi(0); setTimeout(() => inputRef.current?.focus(), 0) }
  }, [open])

  useEffect(() => { setHi(0) }, [q])
  useEffect(() => {
    const el = listRef.current?.children[hi] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [hi])

  const go = useCallback((d: Dest) => {
    setOpen(false)
    router.push(d.href)
  }, [router])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((i) => Math.min(i + 1, results.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (results[hi]) go(results[hi]) }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-start justify-center px-4 pt-[12vh]"
      style={{
        background: 'rgba(8,11,18,0.55)',
        backdropFilter: 'blur(4px)',
        // Keyboard surface — instant feel: fade only, ≤120ms, no scale-in.
        animation: 'fadeIn 110ms ease-out',
      }}
      onMouseDown={() => setOpen(false)}
    >
      <div
        className="glass-card w-full max-w-xl overflow-hidden"
        style={{
          background: theme.card,
          border: `1px solid ${theme.orange}40`,
          boxShadow: `0 0 0 1px ${theme.orange}1f, 0 30px 70px -32px rgba(0,0,0,0.7)`,
          backdropFilter: 'blur(20px) saturate(1.2)',
          WebkitBackdropFilter: 'blur(20px) saturate(1.2)',
        }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Search row */}
        <div className="flex items-center gap-3 border-b px-4 py-3" style={{ borderColor: theme.g200 }}>
          <Search className="h-4 w-4 flex-shrink-0" style={{ color: theme.orange }} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search omni — jump to any page…"
            className="w-full bg-transparent text-sm focus:outline-none"
            style={{ color: theme.text, caretColor: theme.orange }}
          />
          <kbd className="rounded px-1.5 py-0.5 text-[10px] font-semibold"
               style={{ background: theme.g100, color: theme.t3 }}>ESC</kbd>
        </div>

        {/* Results — idle state shows quiet group headers (same headers as the
            nav); while typing the list stays rank-ordered with the group as a
            trailing chip per row, so scoring is never reshuffled. */}
        <ul ref={listRef} className="max-h-[50vh] overflow-y-auto py-2">
          {results.length === 0 && (
            <li className="px-4 py-6 text-center text-sm" style={{ color: theme.t3 }}>
              No match for “{q}”.
            </li>
          )}
          {results.map((d, i) => (
            <li key={d.href}>
              {q.trim() === '' && (i === 0 || results[i - 1].group !== d.group) && (
                <div
                  className="px-5 pt-3 pb-1 text-[10px] uppercase tracking-[0.18em] font-semibold select-none"
                  style={{ color: theme.t3 }}
                >
                  {d.group}
                </div>
              )}
              <div
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => { e.preventDefault(); go(d) }}
                className="mx-2 flex cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2"
                style={{
                  background: i === hi ? `${theme.orange}1a` : 'transparent',
                  boxShadow: i === hi ? `inset 2px 0 0 ${theme.orange}` : 'none',
                }}
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span className="truncate text-sm font-medium" style={{ color: theme.text }}>{d.label}</span>
                  <span className="flex-shrink-0 text-[10px] uppercase tracking-wide" style={{ color: theme.t3 }}>{d.group}</span>
                </div>
                {i === hi && <CornerDownLeft className="h-3.5 w-3.5 flex-shrink-0" style={{ color: theme.orange }} />}
              </div>
            </li>
          ))}
        </ul>

        {/* Footer hint */}
        <div className="flex items-center gap-4 border-t px-4 py-2 text-[11px]" style={{ borderColor: theme.g200, color: theme.t3 }}>
          <span className="flex items-center gap-1"><ArrowUp className="h-3 w-3" /><ArrowDown className="h-3 w-3" /> navigate</span>
          <span className="flex items-center gap-1"><CornerDownLeft className="h-3 w-3" /> open</span>
          <span className="ml-auto">⌘K / Ctrl-K anywhere</span>
        </div>
      </div>
    </div>
  )
}

export default CommandPalette
