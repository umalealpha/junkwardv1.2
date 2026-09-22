'use client'
/**
 * /help — Omni User Manual.
 *
 * Single-file Next.js page. Branded for Alpha Direct (navy #0D1B2A,
 * orange #F4A623, Book-Antiqua serif headers, system sans body).
 * Audience: every omni user — Finance, HR, Procurement, EXCO.
 *
 * Lead chapter (Bank Reconciliation) is exhaustive; other modules
 * are step-tight. CFO directive 2026-06-08 — "create a fantastic
 * document, save somewhere everyone has access to".
 */
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { TopBar } from '@/components/layout/TopBar'
import { getWhatsNew, type WhatsNewEntry } from '@/lib/api'
import {
  BookOpen, Banknote, Users, HeartPulse, FileSpreadsheet,
  CreditCard, Building2, Calendar, Sparkles, ArrowRight,
  CheckCircle2, AlertTriangle, Info, Search, Layers, Rocket,
} from 'lucide-react'

// ─── Brand tokens ─────────────────────────────────────────────────
// Surface-aware: ORANGE/MUTE sit on the light (cream/paper) body, the *_ON_NAVY
// pair sits on the navy hero + What's-New band. #F4A623 fails on white (1.94:1)
// but clears 8.5:1 on navy; #B04E00 is the inverse. Same for the muted grays.
const NAVY    = '#0D1B2A'
const ORANGE  = '#B04E00'          // text-safe on light surfaces (5.3:1 on white)
const ORANGE_ON_NAVY = '#F4A623'   // brand orange, 8.5:1 on navy
const CREAM   = '#FAF7F2'
const PAPER   = '#FFFFFF'
const RULE    = '#E4E7EC'
const INK     = '#111827'
const SUB     = '#4B5563'
const MUTE    = '#6B7280'          // was #9CA3AF (2.53:1 on white)
const MUTE_ON_NAVY = '#9CA3AF'     // 5.5:1 on navy
const SERIF   = "'Book Antiqua', 'Palatino', Georgia, serif"

interface Chapter {
  id: string
  num: string
  title: string
  icon: typeof Banknote
  blurb: string
}

const CHAPTERS: Chapter[] = [
  { id: 'start',    num: '01', title: 'Getting Started',          icon: Sparkles,        blurb: 'Sign in, the company switcher, what each menu does.' },
  { id: 'bank',     num: '02', title: 'Bank Reconciliation',       icon: Banknote,        blurb: 'Upload statement → run matching → fix exceptions → reconcile.' },
  { id: 'je',       num: '03', title: 'Journal Entries',           icon: FileSpreadsheet, blurb: 'Raise a JE, submit for approval, post, reverse.' },
  { id: 'hris',     num: '04', title: 'HRIS Amendments',           icon: Users,           blurb: 'Propose an employee change. Approver applies it. Maker-checker.' },
  { id: 'hc',       num: '05', title: 'Health Care Smart Upload',  icon: HeartPulse,      blurb: 'Revenue (GWP), Claims (AFT) and Treaty (IN/OUT) bordereaux.' },
  { id: 'pay',      num: '06', title: 'Payments',                  icon: CreditCard,      blurb: 'New payment, approve, FNB / RealPay routing.' },
  { id: 'vend',     num: '07', title: 'Vendors & Contacts',        icon: Building2,       blurb: 'Create / upload vendors. Name validation, duplicates, banking.' },
  { id: 'period',   num: '08', title: 'Period Close',              icon: Calendar,        blurb: 'Lock a fiscal period. Override authority. Audit trail.' },
  { id: 'coa',      num: '09', title: 'Chart of Accounts',         icon: Layers,          blurb: 'Browse the CoA — MA-tree, flat list, drill into any account ledger.' },
]

export default function HelpPage() {
  const [q, setQ] = useState('')
  const filtered = CHAPTERS.filter(c =>
    !q || c.title.toLowerCase().includes(q.toLowerCase()) || c.blurb.toLowerCase().includes(q.toLowerCase()))

  // "What's New" — live feature log, refreshed nightly by the backend.
  const [news, setNews] = useState<WhatsNewEntry[]>([])
  const [newsUpdated, setNewsUpdated] = useState<string | null>(null)
  const [newsLoaded, setNewsLoaded] = useState(false)
  useEffect(() => {
    getWhatsNew()
      .then((r) => { setNews(r.entries || []); setNewsUpdated(r.last_updated) })
      .catch(() => { /* manual still works without the feed */ })
      .finally(() => setNewsLoaded(true))
  }, [])

  return (
    <div className="flex flex-col min-h-screen" style={{ background: CREAM, color: INK }}>
      <TopBar
        title="User Manual"
        breadcrumbs={[{ label: 'Home', href: '/dashboard' }, { label: 'User Manual' }]}
      />

      {/* ── Hero ───────────────────────────────────────────────────── */}
      <header
        style={{ background: NAVY, color: '#fff' }}
        className="px-6 md:px-12 py-10 md:py-14 border-b-4"
      >
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-3 mb-4">
            <span style={{ color: ORANGE_ON_NAVY, fontSize: 13, letterSpacing: '0.2em', fontWeight: 600 }}>
              ALPHA DIRECT · OMNI
            </span>
            <span style={{ color: '#cfd6e0', fontSize: 13 }}>v2026.06</span>
          </div>
          <h1
            style={{ fontFamily: SERIF, fontWeight: 700, fontSize: 'clamp(36px, 5vw, 56px)', lineHeight: 1.05, margin: 0 }}
          >
            The Omni <span style={{ color: ORANGE_ON_NAVY }}>User Manual</span>.
          </h1>
          <p style={{ color: '#cbd5e1', fontSize: 17, maxWidth: 720, marginTop: 18 }}>
            One source of truth for how to use every part of omni — written for the people who use it,
            not the people who built it. Start with <strong style={{ color: '#fff' }}>Bank Reconciliation</strong> if
            that&rsquo;s what brought you here, or pick any chapter below.
          </p>
          <div style={{ marginTop: 24, display: 'flex', alignItems: 'center', gap: 10, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10, padding: '10px 14px', maxWidth: 520 }}>
            <Search className="w-4 h-4" style={{ color: ORANGE_ON_NAVY }} />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search the manual (e.g. reconcile, payslip, vendor)…"
              style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', color: '#fff', fontSize: 14 }}
            />
            {q && <button onClick={() => setQ('')} style={{ color: '#cbd5e1', fontSize: 12 }}>clear</button>}
          </div>
        </div>
      </header>

      {/* ── What's New (live, auto-updated nightly) ────────────────── */}
      <section id="whatsnew" className="px-6 md:px-12 py-10" style={{ background: NAVY, color: '#fff', scrollMarginTop: 80 }}>
        <div className="max-w-5xl mx-auto">
          <div className="flex items-center gap-3 mb-1">
            <Rocket className="w-5 h-5" style={{ color: ORANGE_ON_NAVY }} />
            <h2 style={{ fontFamily: SERIF, fontWeight: 700, fontSize: 24, color: '#fff', margin: 0 }}>
              What&rsquo;s New
            </h2>
          </div>
          <p style={{ color: '#cbd5e1', fontSize: 14, margin: '0 0 20px 0' }}>
            Every feature we add lands here automatically — refreshed each night.
            {newsUpdated && <span style={{ color: MUTE_ON_NAVY }}> Last updated {newsUpdated}.</span>}
          </p>

          {newsLoaded && news.length === 0 && (
            <p style={{ color: '#cbd5e1', fontSize: 14 }}>
              New features will show up here as they ship. Check back soon.
            </p>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {news.slice(0, 12).map((e, i) => (
              <div key={i} style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 12, padding: 16 }}>
                <div className="flex items-center gap-2 mb-2" style={{ flexWrap: 'wrap' }}>
                  <span style={{ background: ORANGE_ON_NAVY, color: NAVY, borderRadius: 999, padding: '1px 9px', fontSize: 11, fontWeight: 700 }}>{e.area || 'General'}</span>
                  <span style={{ color: MUTE_ON_NAVY, fontSize: 12 }}>{e.date}</span>
                </div>
                <div style={{ fontFamily: SERIF, fontSize: 17, fontWeight: 700, color: '#fff', lineHeight: 1.25 }}>{e.title}</div>
                {e.summary && <div style={{ color: '#cbd5e1', fontSize: 13.5, marginTop: 6, lineHeight: 1.5 }}>{e.summary}</div>}
              </div>
            ))}
          </div>
          {news.length > 12 && (
            <p style={{ color: MUTE_ON_NAVY, fontSize: 12, marginTop: 14 }}>Showing the 12 most recent of {news.length}.</p>
          )}
        </div>
      </section>

      {/* ── Table of contents ──────────────────────────────────────── */}
      <section className="px-6 md:px-12 py-10" style={{ background: PAPER, borderBottom: `1px solid ${RULE}` }}>
        <div className="max-w-5xl mx-auto">
          <h2 style={{ fontFamily: SERIF, fontWeight: 700, fontSize: 24, color: NAVY, margin: '0 0 6px 0' }}>
            Chapters
          </h2>
          <p style={{ color: SUB, fontSize: 14, margin: '0 0 20px 0' }}>
            Nine modules. Click a card to jump to the section.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((c) => {
              const Ico = c.icon
              return (
                <a key={c.id} href={`#${c.id}`} className="block group" style={{
                  background: PAPER, border: `1px solid ${RULE}`, borderRadius: 12, padding: 18,
                  transition: 'border-color .15s, box-shadow .15s',
                }}>
                  <div className="flex items-start gap-3">
                    <div style={{ background: NAVY, color: ORANGE_ON_NAVY, width: 38, height: 38, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      <Ico className="w-5 h-5" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div style={{ fontSize: 11, letterSpacing: '0.18em', color: MUTE, fontWeight: 600 }}>CH. {c.num}</div>
                      <div style={{ fontFamily: SERIF, fontSize: 18, fontWeight: 700, color: NAVY, marginTop: 2 }}>{c.title}</div>
                      <div style={{ color: SUB, fontSize: 13, marginTop: 6, lineHeight: 1.45 }}>{c.blurb}</div>
                    </div>
                    <ArrowRight className="w-4 h-4 mt-2" style={{ color: ORANGE }} />
                  </div>
                </a>
              )
            })}
          </div>
          {filtered.length === 0 && (
            <p style={{ color: MUTE, fontSize: 14, marginTop: 12 }}>No chapter matches &ldquo;{q}&rdquo;.</p>
          )}
        </div>
      </section>

      {/* ── Chapter content ───────────────────────────────────────── */}
      {/* section, not <main> — the dashboard layout already provides the page's
          single <main> landmark; nesting a second one fails axe landmark rules */}
      <section className="px-6 md:px-12 py-12" style={{ background: CREAM }}>
        <div className="max-w-3xl mx-auto" style={{ background: PAPER, border: `1px solid ${RULE}`, borderRadius: 12, padding: '28px 32px' }}>

          {/* 01 — Getting Started */}
          <ChapterHeader num="01" title="Getting Started" id="start" />
          <p style={para}>
            omni is signed in via Microsoft (single sign-on). Click <Kbd>Sign in</Kbd> on the login screen
            and follow the prompt; the first time, choose your Alpha Direct account.
          </p>
          <Steps items={[
            <>Use the <Kbd>Company switcher</Kbd> at the top right to switch between ADIC, ADIL, ADSA, RSA, UNI, VCM, QIH and the other group entities. Every page on omni is scoped to the selected company.</>,
            <>The <Kbd>left sidebar</Kbd> groups features by team — Accounting, HRIS, Banking, Payables, Procurement, Health Care, Compliance.</>,
            <>The <Kbd>Dashboard</Kbd> shows your selected company&rsquo;s headline numbers (Cash, Revenue / GWP, AR, AP, EBITDA, PAT) plus an Action Center listing what needs your attention.</>,
            <>If you don&rsquo;t see a menu, you probably don&rsquo;t have rights for that module — talk to Finance Systems.</>,
          ]} />

          <Divider />

          {/* 02 — Bank Reconciliation (the deep chapter) */}
          <ChapterHeader num="02" title="Bank Reconciliation" id="bank" />
          <p style={para}>
            Bank reconciliation in omni follows four moves:
            <strong> upload the statement → run matching → match anything left over → confirm the result.</strong>
            Below is the full walk-through. If you do not remember a step, reading this end-to-end takes about 4 minutes.
          </p>

          <SubHeader>A · Open the Banking page</SubHeader>
          <Steps items={[
            <>From the sidebar, click <Kbd>Banking</Kbd>. You will see your bank accounts on the left and any uploaded statements on the right.</>,
            <>Confirm the company at the top right is the one whose statement you are about to reconcile (e.g. <strong>ADIC</strong>).</>,
            <>Each bank account shows a <em>Last reconciled</em> date. If it says <span style={{ color: '#B45309', fontWeight: 600 }}>Never reconciled</span>, this will be its first.</>,
          ]} />

          <SubHeader>B · Upload the bank statement</SubHeader>
          <Steps items={[
            <>Click <Kbd>Upload statement</Kbd>. Choose the bank account, the statement file (CSV or PDF as supplied by the bank), and pick the statement&rsquo;s opening and closing dates.</>,
            <>omni parses the lines and creates a <em>Bank Statement</em> record. You will see Opening / Closing balance, total Lines, and provisional Matched / Unmatched counts.</>,
            <>If parsing fails or the file is in an unexpected format, omni shows an explicit error — re-export the statement from FNB / RealPay in the documented format and try again.</>,
          ]} />

          <SubHeader>C · Run automatic matching</SubHeader>
          <p style={para}>
            On the loaded statement, click <Kbd>Run Matching</Kbd>. omni compares each statement line
            against unmatched omni payments and any rule you have set. After a few seconds you&rsquo;ll see a
            summary like &ldquo;<strong>Matched: 24</strong> · <strong>Unmatched: 6</strong>&rdquo;.
          </p>
          <Callout kind="info">
            <strong>What &ldquo;auto-matched&rdquo; means</strong> — omni found exactly one omni payment that matches a
            line by amount + date + reference. The auto-match shows a confidence percentage; anything below
            the threshold is left as <em>unmatched</em> for you to confirm manually.
          </Callout>

          <SubHeader>D · The Statement Lines table</SubHeader>
          <p style={para}>
            Each row in the lines table has six columns:
          </p>
          <Steps items={[
            <><strong>Date</strong> — the bank value-date.</>,
            <><strong>Description</strong> — the bank narration (e.g. &ldquo;EFT ACME LTD&rdquo;).</>,
            <><strong>Reference</strong> — the bank reference field, if present.</>,
            <><strong>Amount</strong> — positive = inflow, negative = outflow.</>,
            <><strong>Status</strong> — a coloured pill: <Pill color="#059669">matched</Pill> <Pill color="#2563EB">auto matched</Pill> <Pill color="#DC2626">unmatched</Pill> <Pill color="#7C3AED">manually matched</Pill>.</>,
            <><strong>Action</strong> — a <Kbd>Match</Kbd> button on any unmatched line.</>,
          ]} />

          <SubHeader>E · Match an unmatched line</SubHeader>
          <Steps items={[
            <>Click <Kbd>Match</Kbd> next to an unmatched line. A pop-up opens listing unmatched omni payments around the same date and amount.</>,
            <>Pick the right payment and click <Kbd>Confirm match</Kbd>. The line is now <Pill color="#7C3AED">manually matched</Pill> and the payment is linked to that statement line. Audit trail is automatic.</>,
            <>If <em>no candidate payment exists</em> (e.g. a bank fee, an interest debit, a deposit that was never raised in omni), click <Kbd>Create payment from line</Kbd> instead. omni opens a New Payment form with the date, amount and description pre-filled. Save, then come back and match it.</>,
            <>If the line is something you don&rsquo;t expect to match (e.g. a duplicate), use <Kbd>Ignore</Kbd>. You will be asked to add a one-line reason — that becomes part of the audit trail.</>,
          ]} />

          <SubHeader>F · Finishing the reconciliation</SubHeader>
          <Steps items={[
            <>When <em>every</em> line is no longer <Pill color="#DC2626">unmatched</Pill>, the statement is ready to close. The counter at the top should read <em>Unmatched: 0</em>.</>,
            <>omni updates the bank account&rsquo;s <em>Last reconciled</em> date and locks the statement against accidental edits.</>,
            <>The bank-rec sits in the audit pack: every match (who, when, against which payment) and every &ldquo;create from line&rdquo; or &ldquo;ignore&rdquo; is recorded.</>,
          ]} />

          <Callout kind="warn">
            <strong>If the opening + period movement ≠ closing balance</strong> on the statement, omni flags it
            in red — re-check that you uploaded the right month&rsquo;s file or that you haven&rsquo;t double-counted
            a line. Don&rsquo;t close the statement until that warning is gone.
          </Callout>

          <SubHeader>G · Tips</SubHeader>
          <Steps items={[
            <><strong>FNB direct feed</strong> — under Banking → FNB you can ingest statements via the live feed instead of CSV upload; same workflow afterwards.</>,
            <><strong>RealPay collections</strong> — Banking → RealPay shows a reconciled / variance badge that ties the imported file totals against omni&rsquo;s record automatically (Botswana + South Africa subsidiaries).</>,
            <><strong>Bulk recurring lines</strong> — set up a Bank Rec Rule (e.g. &ldquo;match anything with <em>BANK FEE</em> in description to GL account 612000 expense&rdquo;) so common lines auto-match next time.</>,
          ]} />

          <Divider />

          {/* 03 — Journal Entries */}
          <ChapterHeader num="03" title="Journal Entries" id="je" />
          <Steps items={[
            <>Sidebar &rarr; Accounting &rarr; <Kbd>Journal Entries</Kbd>. Click <Kbd>+ New</Kbd>.</>,
            <>Pick the date (must be on or before today — future-dated entries are blocked), description, journal type, and add lines (Account, Debit, Credit). The entry must balance to the cent before you can save.</>,
            <>Save as Draft, then click <Kbd>Submit for approval</Kbd>. The CFO or designated approver gets a notification, reviews, and Approves or Rejects.</>,
            <>Approved &rarr; the entry posts and gets a JE number (<code>JE-ADIC-2026-000123</code>). Once posted you can&rsquo;t edit it — only reverse it.</>,
            <><strong>To reverse</strong> a posted entry, open it and click <Kbd>Reverse</Kbd>. omni creates a mirror entry with sign-flipped amounts on the same accounts, linked to the original.</>,
          ]} />

          <Divider />

          {/* 04 — HRIS Amendments */}
          <ChapterHeader num="04" title="HRIS Amendments" id="hris" />
          <p style={para}>
            Employee data changes go through a <strong>maker-checker</strong>. The maker proposes, the
            approver applies — the live record doesn&rsquo;t change until approval.
          </p>
          <Steps items={[
            <>Sidebar &rarr; HRIS &rarr; <Kbd>Amendments</Kbd>. First time each day you&rsquo;ll be asked for the HRIS password (unlocks HRIS for 8 hours).</>,
            <>Under <em>Propose an amendment</em>, choose the employee, the field (e.g. Job Title), the new value, and a reason. Click <Kbd>Submit for approval</Kbd>. You&rsquo;ll see a confirmation — <em>the live record is unchanged until approved.</em></>,
            <>Your approver (Unami Butale by default; escalates to the CFO if Unami is the maker — segregation of duties) opens the same screen and clicks <Kbd>Approve</Kbd> or <Kbd>Reject</Kbd>.</>,
            <><strong>Approve</strong> &rarr; change applied + audited. <strong>Reject</strong> &rarr; nothing changes. Bank-detail edits are always flagged for HR/Finance review.</>,
          ]} />

          <Divider />

          {/* 05 — Health Care Smart Upload */}
          <ChapterHeader num="05" title="Health Care Smart Upload" id="hc" />
          <p style={para}>
            Three smart-upload screens, one per data type. omni parses your xlsx, computes totals,
            recognises member counts, and stores rows for the dashboard.
          </p>
          <Steps items={[
            <><strong>Revenue (Bordereaux)</strong> — Sidebar &rarr; Health Care &rarr; <Kbd>Revenue (Bordereaux)</Kbd>. Upload the monthly GWP Master xlsx. omni reads <code>TotalPremium Incl. VAT</code> (or fallback variants), counts beneficiaries.</>,
            <><strong>Claims (AFT)</strong> — Health Care &rarr; <Kbd>Claims (AFT)</Kbd>. Upload the weekly <code>ADI_AFT_PmtRun_*.xlsx</code>. omni sums <code>Charged Amount</code> + <code>Paid Amount</code> and counts unique Member Numbers.</>,
            <><strong>Treaty (IN / OUT)</strong> — Health Care &rarr; <Kbd>Treaty (IN / OUT)</Kbd>. Pick IN (received from broker) or OUT (sent to broker), upload the bordereaux. omni skips lookup/config sheets and picks the real Premium Register.</>,
            <>All three accept <Kbd>.xlsx, ≤25 MB</Kbd>. If a column name in your file doesn&rsquo;t match what the parser expects, totals will show 0 and the &ldquo;Recent uploads&rdquo; table reports it — reply to <em>omni Engineering</em> with the file attached.</>,
          ]} />

          <Divider />

          {/* 06 — Payments */}
          <ChapterHeader num="06" title="Payments" id="pay" />
          <Steps items={[
            <>Sidebar &rarr; Payables &rarr; <Kbd>Payments</Kbd>. Click <Kbd>+ New</Kbd>.</>,
            <>Pick direction (OUT = vendor, IN = customer), the contact, date, method (FNB EFT, RealPay debit, cheque, cash, etc.), amount in transaction currency.</>,
            <>Save. Status is <em>Draft</em>. Click <Kbd>Submit for approval</Kbd>. Approvers see it under Payment Approvals.</>,
            <>For FNB / RealPay, the routing layer takes the approved payment to the bank file — you don&rsquo;t key it twice.</>,
            <>The Payments page header now reads <Kbd>AMOUNT IN CURRENCY</Kbd> for the native-currency column.</>,
          ]} />

          <Divider />

          {/* 07 — Vendors */}
          <ChapterHeader num="07" title="Vendors & Contacts" id="vend" />
          <Steps items={[
            <>Sidebar &rarr; Payables &rarr; <Kbd>Vendors</Kbd>. Search by name, tax ID or registration number.</>,
            <>Click <Kbd>+ New Vendor</Kbd>. Name must be at least 2 characters (blank or single-character names are rejected at the API). Fill registration number, tax ID, contact details, banking — bank fields are audit-tracked.</>,
            <>For multiple vendors at once, use <Kbd>Upload vendors</Kbd> — upload an xlsx; Aria maps the columns; review the preview before commit.</>,
            <>If a vendor appears twice, use <Kbd>Merge duplicates</Kbd> (Finance role only).</>,
          ]} />

          <Divider />

          {/* 08 — Period Close */}
          <ChapterHeader num="08" title="Period Close" id="period" />
          <Steps items={[
            <>Sidebar &rarr; <Kbd>Period Management</Kbd>. Pick the fiscal period and the company.</>,
            <>Before closing: every JE in the period is <em>Posted</em>, every bank account is reconciled, AR / AP aging is reviewed, and the TB ticks.</>,
            <>Click <Kbd>Close period</Kbd>. omni locks the period — new JEs dated in it are rejected.</>,
            <>If you need to amend something after close, only the CFO can re-open. omni records the override and notes who, when, why.</>,
          ]} />

          <Divider />

          {/* 09 — Chart of Accounts */}
          <ChapterHeader num="09" title="Chart of Accounts" id="coa" />
          <p style={para}>
            omni&rsquo;s Chart of Accounts (CoA) is the master list of every GL account across the group &mdash;
            2,167 accounts at last count (738 ADIC, 146 QIH, the rest spread across the 9 other subsidiaries).
            There are <b>three views</b>, each for a different job.
          </p>

          <SubHeader>A &middot; Browse Accounts (the everyday view)</SubHeader>
          <Steps items={[
            <>Sidebar &rarr; Accounting &rarr; <Kbd>Browse Accounts</Kbd> (or go to <code>/accounts</code>).</>,
            <>Switch between <b>MA Format</b> (tree view that mirrors the CFO&rsquo;s Management Accounts workbook: Current Assets &rarr; Non-Current Assets &rarr; Current Liabilities &rarr; NCL &rarr; Equity &rarr; Net Earned Premium &rarr; Claims &rarr; etc.) and <b>Flat list (legacy)</b> (raw account-code list).</>,
            <>The header shows <em>BS cumulative as of [today]</em> and <em>P&amp;L activity for the current FY</em>. The company switcher at the top right scopes everything.</>,
            <>Click any account to open its <b>GL detail</b> &mdash; running balance, every journal line, drill into the JE.</>,
          ]} />

          <SubHeader>B &middot; Chart of Accounts admin (settings)</SubHeader>
          <Steps items={[
            <>Sidebar &rarr; Settings &rarr; <Kbd>Chart of Accounts</Kbd> (or go to <code>/settings/chart-of-accounts</code>).</>,
            <>This is where you add a new account, change an account&rsquo;s name / type / sub-type / fs_line_item classification, mark an account inactive, or set the FX revaluation flag.</>,
            <>Every edit is audit-trailed (who, when, before / after). New accounts auto-pick up the omni naming convention (e.g. <code>ADSA-630000</code> for a salary expense booked under ADSA).</>,
          ]} />

          <SubHeader>C &middot; CoA-as-MA-tree (single source of truth)</SubHeader>
          <Steps items={[
            <>Go to <code>/reports/coa-ma-tree</code> (also reachable from the Reports sidebar) &mdash; one viewer that everything in omni reads. If a number in a board pack differs from this tree, the board pack is the one that needs explaining.</>,
            <>Sign-aware sub-totals on each MA section line. P&amp;L sub-totals show period activity (not cumulative since inception &mdash; the P&amp;L resets at fiscal-year start).</>,
            <>Use this view for any reconciliation question that starts with &ldquo;why does this number look different on report X vs report Y?&rdquo; &mdash; both should resolve back to the tree.</>,
          ]} />

          <SubHeader>D &middot; Bulk CoA upload (CFO only)</SubHeader>
          <Steps items={[
            <>For a wholesale re-class (CFO only): use the <code>/admin/cfo-upload-coa/</code> endpoint with the supplied xlsx template. Lands as a draft for review before commit; never applies silently.</>,
          ]} />

          <Callout kind="info">
            <strong>Who can see the CoA?</strong> Anyone signed into omni can read all three views &mdash; no extra
            password or role gate. Edits in the Settings view are audit-tracked but not blocked. If you can&rsquo;t see
            an entity in the company switcher on the CoA page, you don&rsquo;t have that entity in your allowlist
            (different setting) &mdash; reply to omni Engineering.
          </Callout>

          <Divider />

          {/* ── Need help? ─────────────────────────────────────────── */}
          <h3 style={{ fontFamily: SERIF, fontWeight: 700, color: NAVY, fontSize: 22, marginTop: 36 }}>Need help?</h3>
          <p style={para}>
            Reply to any omni notification email, or write to <a href="mailto:omni@alphadirect.co.bw" style={{ color: ORANGE, fontWeight: 600 }}>omni@alphadirect.co.bw</a>. For an issue
            on a specific page, screenshot it &mdash; that&rsquo;s often quicker than describing it.
          </p>
          <p style={{ color: MUTE, fontSize: 12, marginTop: 24 }}>
            Document version <strong>2026.06</strong> · Built by Finance Systems · Updated as features land.
          </p>
        </div>
      </section>
    </div>
  )
}

// ─── Small helpers ─────────────────────────────────────────────────

const para: React.CSSProperties = { fontSize: 15, lineHeight: 1.65, color: '#1F2937', margin: '10px 0' }

function ChapterHeader({ num, title, id }: { num: string; title: string; id: string }) {
  return (
    <div id={id} style={{ scrollMarginTop: 80, marginTop: 8 }}>
      <div style={{ fontSize: 11, letterSpacing: '0.22em', color: ORANGE, fontWeight: 700 }}>CHAPTER · {num}</div>
      <h2 style={{ fontFamily: SERIF, fontWeight: 700, color: NAVY, fontSize: 30, margin: '4px 0 14px 0' }}>{title}</h2>
    </div>
  )
}

function SubHeader({ children }: { children: React.ReactNode }) {
  return <h3 style={{ fontFamily: SERIF, fontSize: 18, fontWeight: 700, color: NAVY, marginTop: 22, marginBottom: 6 }}>{children}</h3>
}

function Divider() { return <hr style={{ border: 'none', borderTop: `1px solid ${RULE}`, margin: '28px 0' }} /> }

function Kbd({ children }: { children: React.ReactNode }) {
  return <code style={{ background: '#F3F4F6', border: '1px solid #E5E7EB', borderRadius: 4, padding: '1px 6px', fontSize: 12.5, fontWeight: 600, color: NAVY }}>{children}</code>
}

function Steps({ items }: { items: React.ReactNode[] }) {
  return (
    <ol style={{ margin: '8px 0 12px 0', paddingLeft: 22 }}>
      {items.map((it, i) => <li key={i} style={{ marginBottom: 8, lineHeight: 1.55, color: '#1F2937', fontSize: 15 }}>{it}</li>)}
    </ol>
  )
}

function Pill({ color, children }: { color: string; children: React.ReactNode }) {
  // Text is a darkened variant of the pill colour — the raw badge colours
  // (#059669, #DC2626) sat under 4.5:1 against their own 8% tint background.
  const darkText: Record<string, string> = {
    '#059669': '#047857', '#DC2626': '#B91C1C', '#2563EB': '#1D4ED8', '#7C3AED': '#6D28D9',
  }
  return <span style={{ background: color + '15', color: darkText[color] || color, borderRadius: 999, padding: '1px 8px', fontSize: 11, fontWeight: 600 }}>{children}</span>
}

function Callout({ kind, children }: { kind: 'info' | 'warn'; children: React.ReactNode }) {
  const c = kind === 'warn' ? { bg: '#FEF3C7', border: '#F59E0B', icon: AlertTriangle, label: 'WATCH OUT' }
                            : { bg: '#ECFDF5', border: '#10B981', icon: Info,           label: 'NOTE' }
  const Ico = c.icon
  return (
    <div style={{ background: c.bg, border: `1px solid ${c.border}`, borderRadius: 8, padding: '10px 14px', margin: '12px 0', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <Ico className="w-4 h-4 mt-0.5" style={{ color: c.border, flexShrink: 0 }} />
      <div style={{ fontSize: 14, color: INK, lineHeight: 1.55 }}>{children}</div>
    </div>
  )
}
