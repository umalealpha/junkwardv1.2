'use client'

/**
 * /compliance/nbfira — NBFIRA Dashboard.
 *
 * Built 2026-05-22 (CFO directive). Single landing pane summarising
 * regulatory health for the internal auditor:
 *   - Next quarterly + annual filing deadlines
 *   - Latest solvency-margin reading (live from /regulatory/capital-check/)
 *   - Prudential-limit traffic-light overview
 *   - Quick links into each sub-module
 *
 * No data is filed from this page — it reads from existing reports and
 * regulatory endpoints. Internal audit can review and mark up findings.
 */

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import {
  ArrowRight, Calendar, ClipboardCheck, AlertTriangle, Scale,
  ShieldCheck, FileSpreadsheet, Settings,
} from 'lucide-react'
import {
  fyQuarters, annualReturnDue, daysUntil, statusForDays,
  PRUDENTIAL_LIMITS, TARGET_SCR_RATIO, FLOOR_SCR_RATIO,
} from './_lib'

export default function NbfiraDashboardPage() {
  const today = new Date()
  const fy = (today.getMonth() + 1) >= 7 ? today.getFullYear() + 1 : today.getFullYear()
  const quarters = fyQuarters(fy)
  const annualDue = annualReturnDue(fy)

  // Next-due quarter = first quarter whose due-date is still in the future
  // (or the most recently overdue one).
  const sortedByDue = [...quarters].sort((a, b) =>
    Math.abs(daysUntil(a.due)) - Math.abs(daysUntil(b.due)),
  )
  const nextQuarter = sortedByDue[0]

  /* Live solvency reading. The capital-check endpoint returns
     { available_capital, required_capital, ratio, ... } when data is set;
     until populated we render dashes rather than fake numbers. */
  const { selectedId: companyId } = useCompany()
  const [scr, setScr] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    // OMNI-QA-008 (Lakshmi QA 2026-06-09): dashboard tile showed "—" while the
    // /capital-adequacy detail page showed 376.6%. Two reasons: (1) the detail
    // page passes ?company=<id> and this tile did not, so the endpoint returned
    // empty; (2) the detail page falls back to available_capital ÷ scr when the
    // server omits a ratio key, and this tile had no fallback. Mirror both.
    apiFetch<any>(`/regulatory/capital-check/${companyId ? `?company=${companyId}` : ''}`)
      .then((r) => setScr(r))
      .catch(() => setScr(null))
      .finally(() => setLoading(false))
  }, [companyId])

  // OMNI-QA-008's fix was to copy the detail page's available ÷ required
  // fallback onto this tile. That made both places agree — and both wrong: the
  // fallback publishes a ratio the server deliberately withheld. Honour the
  // server's own status first.
  const underRevision = scr?.status === 'under_revision'
  const _avail = Number(scr?.available_capital ?? scr?.available ?? NaN)
  const _scr   = Number(scr?.required_capital ?? scr?.scr ?? NaN)
  const ratio = underRevision ? null : (
    scr?.capital_adequacy_ratio != null ? Number(scr.capital_adequacy_ratio)
      : scr?.ratio ?? scr?.solvency_ratio ??
        (isFinite(_avail) && isFinite(_scr) && _scr ? _avail / _scr : null))
  const ratioColour =
    ratio == null         ? 'text-[#6B7280]' :
    ratio >= TARGET_SCR_RATIO ? 'text-[#047857]' :
    ratio >= FLOOR_SCR_RATIO  ? 'text-[#92400E]' :
                                'text-[#B91C1C]'

  const cards = [
    { href: '/compliance/nbfira/quarterly',         label: 'Quarterly Returns',         icon: Calendar,         desc: 'Q1-Q4 prudential returns + management metrics per quarter' },
    { href: '/compliance/nbfira/annual',            label: 'Annual Returns',            icon: Calendar,         desc: 'Audited FY returns, board attestation, audit pack link' },
    { href: '/compliance/nbfira/capital-adequacy',  label: 'Capital Adequacy / Solvency', icon: Scale,         desc: 'MCR vs Available Capital, SCR ratio, components' },
    { href: '/compliance/nbfira/prudential',        label: 'Prudential Limits Monitor', icon: AlertTriangle,    desc: 'Investment, concentration, liquidity exposure traffic-light' },
    { href: '/compliance/nbfira/submissions',       label: 'Submission History',        icon: FileSpreadsheet,  desc: 'Audit trail of every NBFIRA filing + acknowledgement refs' },
    { href: '/compliance/nbfira/settings',          label: 'Settings',                  icon: Settings,         desc: 'Reminder days, board attestation defaults, contacts' },
  ]

  const nextQStatus = statusForDays(daysUntil(nextQuarter.due))
  const annualStatus = statusForDays(daysUntil(annualDue))

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="NBFIRA Compliance"
        subtitle="Botswana Insurance Industry Act 2015 + Regulations 2019"
        breadcrumbs={[{ label: 'Compliance' }, { label: 'NBFIRA Dashboard' }]}
      />
      <div className="flex-1 p-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card>
            <CardContent className="p-5">
              <div className="text-xs uppercase tracking-wider text-[#6B7280]">Next quarterly return</div>
              <div className="text-2xl font-semibold text-[#0D1B2A] mt-1">{nextQuarter.label}</div>
              <div className="text-sm text-[#374151] mt-1">
                Period {nextQuarter.start} → {nextQuarter.end}
              </div>
              <div className={`inline-block mt-3 px-2 py-0.5 rounded-full text-xs font-semibold border ${nextQStatus.cls}`}>
                Due {nextQuarter.due} · {nextQStatus.label}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <div className="text-xs uppercase tracking-wider text-[#6B7280]">Annual return</div>
              <div className="text-2xl font-semibold text-[#0D1B2A] mt-1">FY{String(fy).slice(-2)}</div>
              <div className="text-sm text-[#374151] mt-1">
                Period {fy - 1}-07-01 → {fy}-06-30
              </div>
              <div className={`inline-block mt-3 px-2 py-0.5 rounded-full text-xs font-semibold border ${annualStatus.cls}`}>
                Due {annualDue} · {annualStatus.label}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <div className="text-xs uppercase tracking-wider text-[#6B7280]">Solvency ratio</div>
              <div className={`text-2xl font-semibold mt-1 ${ratioColour}`}>
                {loading ? '...' : ratio == null ? '—' : (ratio * 100).toFixed(0) + '%'}
              </div>
              <div className="text-sm text-[#374151] mt-1">
                {underRevision
                  ? 'Under revision — not for regulatory or board use'
                  : `Own funds ÷ minimum capital target · management target ${Math.round(TARGET_SCR_RATIO * 100)}%`}
              </div>
              <Link href="/compliance/nbfira/capital-adequacy" className="inline-flex items-center gap-1 mt-3 text-xs font-semibold text-[#F4A623]">
                See breakdown <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </CardContent>
          </Card>
        </div>

        {/* Module quick links */}
        <div>
          <div className="text-xs uppercase tracking-wider text-[#6B7280] mb-2">Sub-modules</div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {cards.map((c) => (
              <Link key={c.href} href={c.href}
                    className="block rounded-xl border bg-white p-4 hover:border-[#F4A623] transition-colors"
                    style={{ borderColor: '#E5E7EB' }}>
                <div className="flex items-start gap-3">
                  <div className="w-9 h-9 flex-none rounded-lg bg-[#FFFBEB] text-[#92400E] flex items-center justify-center">
                    <c.icon className="w-4 h-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-semibold text-[#0D1B2A]">{c.label}</div>
                    <div className="text-xs text-[#6B7280] mt-1">{c.desc}</div>
                  </div>
                  <ArrowRight className="w-4 h-4 text-[#9CA3AF] flex-none mt-2" />
                </div>
              </Link>
            ))}
          </div>
        </div>

        {/* Quick prudential summary */}
        <Card>
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold text-[#0D1B2A]">Prudential limits — snapshot</div>
                <div className="text-xs text-[#6B7280]">
                  Investment Regulations 2019 §5. Underlying portfolio numbers wired in once Investments module integrates.
                </div>
              </div>
              <Link href="/compliance/nbfira/prudential" className="text-xs font-semibold text-[#F4A623]">
                Open monitor →
              </Link>
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
              {PRUDENTIAL_LIMITS.map((l) => (
                <div key={l.id} className="flex items-center justify-between py-1.5 border-b border-[#F3F4F6]">
                  <span className="text-[#374151]">{l.label}</span>
                  <span className="font-mono text-[#6B7280]">{l.rule}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <div className="text-xs text-[#9CA3AF] flex items-center gap-1">
          <ShieldCheck className="w-3.5 h-3.5" />
          For internal audit review. Numbers seeded from current ledger; cross-check against the MA workbook before filing.
        </div>
      </div>
    </div>
  )
}
