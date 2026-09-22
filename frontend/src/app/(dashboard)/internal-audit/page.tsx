'use client'

/**
 * /internal-audit — Dashboard (spec Module 10).
 *
 * Every tile is pulled LIVE from Modules 5 (Findings) + 7 (Follow-up) — no
 * hand-entered summary numbers. Each tile shows its source module, per the
 * spec: "If a number can't be traced to Module 1–9, it doesn't belong here."
 *
 * Backend: /api/v1/internal-audit/dashboard/
 */

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { AlertTriangle, Clock, ShieldAlert, Scale, ListChecks, Info } from 'lucide-react'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

const RATING_COLOR: Record<string, string> = {
  low: '#0F8B6C', medium: '#F4A623', high: '#E8590C', critical: '#D72638',
}

interface Tile<T = unknown> { source: string; value: T }
interface DashboardResponse {
  generated_at: string
  can_edit: boolean
  tiles: {
    open_by_rating: Tile<Record<string, number>>
    open_by_age: Tile<Record<string, number>>
    overdue_followup_pct: Tile<number> & { overdue: number; active: number }
    fraud_open: Tile<number>
    regulatory_open: Tile<number>
    plan_completion_pct: Tile<number> & { closed: number; total: number }
  }
}

function SourceBadge({ source }: { source: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-medium text-gray-400" title={`Source: ${source}`}>
      <Info className="h-3 w-3" /> {source}
    </span>
  )
}

export default function InternalAuditDashboard() {
  const [data, setData] = useState<DashboardResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<DashboardResponse>('/internal-audit/dashboard/')
      .then(setData)
      .catch((e) => setErr(String(e?.message || e)))
  }, [])

  const t = data?.tiles

  return (
    <div>
      <TopBar title="Internal Audit — Dashboard" />
      <div className="p-6 space-y-6">
        <p className="text-sm text-gray-500">
          Live figures from the Findings and Follow-up registers. No number here is typed —
          each tile is traceable to its source module.
        </p>

        {err && (
          <Card><CardContent className="p-4 text-sm text-red-600">Could not load dashboard: {err}</CardContent></Card>
        )}

        {t && (
          <>
            {/* Open findings by rating */}
            <Card>
              <CardContent className="p-5">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="font-semibold" style={{ color: NAVY }}>Open findings by rating</h3>
                  <SourceBadge source={t.open_by_rating.source} />
                </div>
                <div className="grid grid-cols-4 gap-3">
                  {(['critical', 'high', 'medium', 'low'] as const).map((r) => (
                    <div key={r} className="rounded-lg border p-3 text-center">
                      <div className="text-2xl font-bold" style={{ color: RATING_COLOR[r] }}>
                        {t.open_by_rating.value[r] ?? 0}
                      </div>
                      <div className="text-xs capitalize text-gray-500">{r}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* KPI row */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <KpiCard icon={<Clock className="h-5 w-5" />} label="Overdue follow-ups"
                value={`${t.overdue_followup_pct.value}%`}
                sub={`${t.overdue_followup_pct.overdue} of ${t.overdue_followup_pct.active} active`}
                source={t.overdue_followup_pct.source} accent={ORANGE} />
              <KpiCard icon={<ShieldAlert className="h-5 w-5" />} label="Open fraud-flagged"
                value={String(t.fraud_open.value)} sub="findings with a fraud flag"
                source={t.fraud_open.source} accent="#D72638" />
              <KpiCard icon={<Scale className="h-5 w-5" />} label="Open regulatory"
                value={String(t.regulatory_open.value)} sub="NBFIRA-linked findings"
                source={t.regulatory_open.source} accent={NAVY} />
            </div>

            {/* Ageing + plan completion */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Card>
                <CardContent className="p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="font-semibold" style={{ color: NAVY }}>Open findings — ageing</h3>
                    <SourceBadge source={t.open_by_age.source} />
                  </div>
                  <div className="grid grid-cols-4 gap-2 text-center">
                    {Object.entries(t.open_by_age.value).map(([bucket, n]) => (
                      <div key={bucket} className="rounded-lg border p-3">
                        <div className="text-xl font-bold" style={{ color: NAVY }}>{n}</div>
                        <div className="text-[11px] text-gray-500">{bucket} days</div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="font-semibold" style={{ color: NAVY }}>Audit plan completion</h3>
                    <SourceBadge source={t.plan_completion_pct.source} />
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-3xl font-bold" style={{ color: ORANGE }}>{t.plan_completion_pct.value}%</span>
                    <span className="text-sm text-gray-500">
                      {t.plan_completion_pct.closed} of {t.plan_completion_pct.total} engagements closed
                    </span>
                  </div>
                </CardContent>
              </Card>
            </div>

            <div className="flex gap-3">
              <Link href="/internal-audit/findings"
                className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white"
                style={{ background: NAVY }}>
                <AlertTriangle className="h-4 w-4" /> Findings Register
              </Link>
              <Link href="/internal-audit/follow-up"
                className="inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium"
                style={{ color: NAVY, borderColor: NAVY }}>
                <ListChecks className="h-4 w-4" /> Follow-up Tracking
              </Link>
            </div>

            <p className="text-xs text-gray-400">
              Generated {new Date(data.generated_at).toLocaleString()} ·
              {data.can_edit ? ' You can edit audit content.' : ' View-only access.'}
            </p>
          </>
        )}
      </div>
    </div>
  )
}

function KpiCard({ icon, label, value, sub, source, accent }: {
  icon: React.ReactNode; label: string; value: string; sub: string; source: string; accent: string
}) {
  return (
    <Card>
      <CardContent className="p-5">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2" style={{ color: accent }}>{icon}
            <span className="text-sm font-medium text-gray-600">{label}</span>
          </div>
          <SourceBadge source={source} />
        </div>
        <div className="text-3xl font-bold" style={{ color: accent }}>{value}</div>
        <div className="text-xs text-gray-500">{sub}</div>
      </CardContent>
    </Card>
  )
}
