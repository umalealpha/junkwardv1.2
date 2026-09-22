'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  Boxes, ArrowRight, Sparkles, AlertCircle, type LucideIcon,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'

interface SalvageItem {
  id: string
  item_code: string
  part_name: string
  status: 'available' | 'reserved' | 'sold' | 'scrapped' | 'on_hold'
  condition: string
  asking_price: string
  vehicle_brand_name?: string | null
  vehicle_year?: number | null
}

interface ListResponse { count: number; results: SalvageItem[] }

export default function SalvageLandingPage() {
  const { theme } = useTheme()
  const [items, setItems] = useState<SalvageItem[]>([])
  const [count, setCount] = useState<number>(0)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch<ListResponse>('/salvage-items/?page_size=6')
      .then(r => { setItems(r.results || []); setCount(r.count || 0) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  const byStatus = items.reduce<Record<string, number>>((acc, it) => {
    acc[it.status] = (acc[it.status] || 0) + 1
    return acc
  }, {})

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Salvage" />

      <div className="p-4 lg:p-6 max-w-[1400px] mx-auto space-y-5">
        {/* Hero */}
        <div className="relative overflow-hidden rounded-2xl"
             style={{
               background: '#FFFFFF',
               border: '1px solid rgba(13,27,42,0.06)',
               boxShadow: '0 10px 40px -12px rgba(13,27,42,0.12)',
             }}>
          <div className="absolute -top-40 -right-32 w-[44rem] h-[44rem] rounded-full pointer-events-none"
               style={{
                 background: `radial-gradient(circle at 30% 30%, rgba(240,127,0,0.18) 0%, transparent 55%),
                              radial-gradient(circle at 70% 60%, rgba(124,58,237,0.18) 0%, transparent 55%)`,
                 filter: 'blur(40px)',
               }} />
          <div className="relative px-6 py-10 lg:px-12 lg:py-12">
            <p className="text-[11px] uppercase tracking-[0.25em] font-semibold mb-3"
               style={{ color: '#0D1B2A', opacity: 0.55 }}>
              Veritas · Motor Salvage Yard
            </p>
            <h1 className="font-display-tight text-4xl lg:text-6xl font-bold leading-[0.95] mb-4"
                style={{ color: '#0D1B2A' }}>
              Salvage{' '}
              <span className="italic bg-clip-text text-transparent"
                    style={{ backgroundImage: 'linear-gradient(120deg, #0D1B2A 0%, #F07F00 60%, #FF9A2E 100%)' }}>
                Operations.
              </span>
            </h1>
            <p className="font-display text-lg italic max-w-xl" style={{ color: '#374151' }}>
              Inventory, quotes, sales and stock counts — one record, one truth.
            </p>
          </div>
        </div>

        {/* KPI row */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <KpiCard theme={theme} icon={Boxes} label="Items on hand" value={String(count)} />
          <KpiCard theme={theme} icon={Boxes} label="Available"     value={String(byStatus.available || 0)} tone="emerald" />
          <KpiCard theme={theme} icon={Boxes} label="Reserved"      value={String(byStatus.reserved  || 0)} tone="amber" />
          <KpiCard theme={theme} icon={Boxes} label="Sold (page)"   value={String(byStatus.sold      || 0)} tone="violet" />
        </div>

        {/* Recent items + jump-in tile */}
        <div className="grid lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 rounded-xl p-5"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-display text-lg font-bold" style={{ color: theme.navy }}>
                Recent intake
              </h3>
              <Link href="/salvage/inventory"
                    className="text-xs font-semibold inline-flex items-center gap-1"
                    style={{ color: theme.orange }}>
                View all <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
            {error && (
              <div className="rounded-md p-3 flex items-start gap-2"
                   style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
                <AlertCircle className="w-4 h-4 mt-0.5" style={{ color: theme.er }} />
                <span className="text-xs" style={{ color: theme.er }}>{error}</span>
              </div>
            )}
            {loading && <div className="text-sm" style={{ color: theme.t3 }}>Loading…</div>}
            {!loading && items.length === 0 && !error && (
              <div className="text-sm" style={{ color: theme.t3 }}>
                No items yet. The legacy data import seeds 51 items from the old motor-liquidators DB.
              </div>
            )}
            <ul className="divide-y" style={{ borderColor: theme.cardBdr }}>
              {items.map(it => (
                <li key={it.id} className="py-3 flex items-center justify-between">
                  <Link href={`/salvage/inventory/${it.id}`}
                        className="flex-1 min-w-0">
                    <div className="text-sm font-semibold truncate" style={{ color: theme.text }}>
                      {it.item_code} · {it.part_name}
                    </div>
                    <div className="text-xs mt-0.5" style={{ color: theme.t3 }}>
                      {[it.vehicle_brand_name, it.vehicle_year, it.condition]
                        .filter(Boolean).join(' · ') || '—'}
                    </div>
                  </Link>
                  <div className="text-right ml-4">
                    <div className="font-display-tight text-base font-bold tabular-nums"
                         style={{ color: theme.navy }}>
                      BWP {Number(it.asking_price).toLocaleString('en-BW')}
                    </div>
                    <div className="text-[10px] uppercase tracking-wider"
                         style={{ color: theme.t3 }}>
                      {it.status}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-xl p-5 relative overflow-hidden"
               style={{
                 background: `linear-gradient(135deg, ${theme.navy} 0%, #1F1547 100%)`,
                 color: '#FFFFFF',
               }}>
            <div className="absolute -top-12 -right-12 w-40 h-40 rounded-full blur-2xl"
                 style={{ background: 'radial-gradient(circle, rgba(240,127,0,0.5), transparent 70%)' }} />
            <div className="relative">
              <Sparkles className="w-5 h-5 mb-3" style={{ color: '#F07F00' }} />
              <div className="font-display text-xl font-bold mb-2">Salvage Portal v1 — read-only</div>
              <p className="text-xs leading-relaxed opacity-80 mb-4">
                Phase 1 of 4. Write actions (new intake, quotes,
                approvals, stock counts) ship in Phase 2-3.
              </p>
              <Link href="/salvage/inventory"
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-semibold"
                    style={{
                      background: 'linear-gradient(135deg, #F07F00, #FF9A2E)',
                      boxShadow: '0 8px 24px -8px rgba(240,127,0,0.55)',
                    }}>
                Browse inventory <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function KpiCard({
  theme, icon: Icon, label, value, tone,
}: {
  theme: any; icon: LucideIcon; label: string; value: string;
  tone?: 'emerald' | 'amber' | 'violet';
}) {
  const TONES = {
    default: { bg: '#EEF2FF', fg: '#6366F1' },
    emerald: { bg: '#ECFDF5', fg: '#059669' },
    amber:   { bg: '#FFFBEB', fg: '#D97706' },
    violet:  { bg: '#F5F3FF', fg: '#7C3AED' },
  }
  const t = TONES[tone ?? 'default']
  return (
    <div className="rounded-xl p-4"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <div className="flex items-start justify-between mb-3">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center"
             style={{ background: t.bg }}>
          <Icon className="w-4 h-4" style={{ color: t.fg }} strokeWidth={2} />
        </div>
        <span className="text-[10px] uppercase tracking-wider font-semibold"
              style={{ color: theme.t3 }}>{label}</span>
      </div>
      <div className="font-display-tight text-2xl font-bold tabular-nums"
           style={{ color: theme.navy }}>{value}</div>
    </div>
  )
}
