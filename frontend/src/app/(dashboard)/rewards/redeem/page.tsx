'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getRewardPartners } from '@/lib/api'
import type { RewardPartner } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'

// ── Alpha light skin tokens (shared with nexus/rewards pages) ─────────────────
const NAVY = '#0D1B2A', ORANGE = '#F4A623', MUT = '#6B7280'
const HAIR = '#ECEEF2', CANVAS = '#F7F8FB'
const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'
const SERIF = 'Spectral, "Book Antiqua", Georgia, serif'

// Status pill tints — active green / pending orange / future grey.
const STATUS_TINT: Record<string, { bg: string; fg: string }> = {
  active:  { bg: '#E6F6EC', fg: '#1B7A3D' },
  pending: { bg: '#FDEFD7', fg: '#9A640A' },
  future:  { bg: '#EEF0F4', fg: '#6B7280' },
}

const card: React.CSSProperties = {
  background: '#fff', borderRadius: 20, boxShadow: CARD_SHADOW, border: `1px solid ${HAIR}`,
}
const eyebrow: React.CSSProperties = {
  fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase', color: MUT, fontWeight: 700,
}

export default function RewardsRedeemPage() {
  const router = useRouter()
  const [partners, setPartners] = useState<RewardPartner[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await getRewardPartners().catch(() => ({ results: [] as RewardPartner[] }))
      setPartners(r.results)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  return (
    <div className="flex flex-col min-h-screen" style={{ background: CANVAS }}>
      <TopBar title="Redeem" breadcrumbs={[{ label: 'Alpha Rewards', href: '/rewards' }, { label: 'Redeem' }]} />
      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-4" style={{ color: NAVY }}>
        {/* eyebrow + headline */}
        <div>
          <div style={eyebrow}>Redemption catalog</div>
          <h1 style={{ fontFamily: SERIF, fontSize: 34, fontWeight: 600, color: NAVY, marginTop: 4, letterSpacing: '-0.01em' }}>Redeem with partners</h1>
          <p style={{ fontSize: 13, color: MUT, marginTop: 4 }}>Turn reward points into airtime, vouchers and more. Redemption opens as partners go live.</p>
        </div>

        {loading && <p className="text-sm" style={{ color: MUT }}>Loading…</p>}

        {!loading && partners.length === 0 && (
          <div className="p-10 text-center" style={card}>
            <div className="mx-auto grid place-items-center" style={{ width: 64, height: 64, borderRadius: '50%', background: CANVAS, border: `1px solid ${HAIR}`, fontSize: 28, color: MUT }}>🎁</div>
            <h2 style={{ fontFamily: SERIF, fontSize: 22, fontWeight: 600, color: NAVY, marginTop: 14 }}>No partners yet</h2>
            <p style={{ fontSize: 13, color: MUT, marginTop: 6, maxWidth: 320, marginLeft: 'auto', marginRight: 'auto' }}>
              We are expanding the reward network. Redemption partners will appear here once they are onboarded.
            </p>
          </div>
        )}

        {!loading && partners.length > 0 && (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(240px,1fr))' }}>
            {partners.map((p) => {
              const tint = STATUS_TINT[p.status] ?? STATUS_TINT.future
              return (
                <div key={p.id} className="p-5 flex flex-col" style={card}>
                  <div className="flex items-start justify-between gap-2">
                    <span className="px-2.5 py-0.5 rounded-full" style={{
                      fontSize: 10.5, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase',
                      background: tint.bg, color: tint.fg,
                    }}>{p.status}</span>
                    <div className="grid place-items-center flex-none" style={{ width: 42, height: 42, borderRadius: 12, background: CANVAS, border: `1px solid ${HAIR}`, fontSize: 20 }}>🏷️</div>
                  </div>
                  <div style={{ fontSize: 17, fontWeight: 600, color: NAVY, marginTop: 14, lineHeight: 1.2 }}>{p.name}</div>
                  <div style={{ fontSize: 12, color: MUT, marginTop: 4 }}>{p.kind_display}</div>
                  {p.notes && <p style={{ fontSize: 12, color: '#374151', marginTop: 8 }}>{p.notes}</p>}
                  {/* Redeem button — visibly disabled / coming soon (no handler exists). */}
                  <button
                    type="button"
                    disabled
                    title="Redemption coming soon"
                    className="mt-4 w-full px-4 py-2 rounded-full font-semibold"
                    style={{ fontSize: 12.5, background: CANVAS, color: MUT, border: `1px solid ${HAIR}`, cursor: 'not-allowed' }}
                  >
                    Redeem · coming soon
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
