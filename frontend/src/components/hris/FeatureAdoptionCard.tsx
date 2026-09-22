'use client'

/**
 * FeatureAdoptionCard — the HR team's "sign off the new features" task list,
 * shown on the HRIS home dashboard (CFO directive 2026-07-21).
 *
 * Each new HRIS feature is a task: OPEN it and press "I am happy with this
 * feature". This card shows progress and links straight to each pending one.
 * Once every feature is signed off the card turns into a done state.
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { apiFetch } from '@/lib/api'

interface Feat { key: string; label: string; route: string; desc: string; accepted: boolean; accepted_at: string | null }

export default function FeatureAdoptionCard() {
  const [feats, setFeats] = useState<Feat[] | null>(null)
  const [done, setDone] = useState(0)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    let live = true
    apiFetch<{ features: Feat[]; done: number; total: number }>('/hris/feature-adoption/')
      .then((d) => { if (!live) return; setFeats(d.features || []); setDone(d.done || 0); setTotal(d.total || 0) })
      .catch(() => { if (live) setFeats([]) })
    return () => { live = false }
  }, [])

  if (feats === null || feats.length === 0) return null   // nothing to show / not loaded

  const allDone = done >= total && total > 0
  const pct = total ? Math.round((done / total) * 100) : 0

  return (
    <div style={{
      border: `1px solid ${allDone ? '#1f7a4d' : '#F4A623'}`,
      background: allDone ? 'rgba(31,122,77,0.08)' : 'rgba(244,166,35,0.07)',
      borderRadius: 16, padding: 20, margin: '0 0 24px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <span style={{ fontSize: 22 }}>{allDone ? '🎉' : '📋'}</span>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ fontWeight: 800, fontSize: 16 }}>
            {allDone ? 'All new features signed off — thank you!' : 'Your task: check & sign off the new HR features'}
          </div>
          <div style={{ fontSize: 13, opacity: 0.8 }}>
            {allDone
              ? 'You’ve confirmed you’re happy with every new feature.'
              : `Open each feature and press “I am happy with this feature”. ${done} of ${total} done.`}
          </div>
        </div>
        <div style={{ fontWeight: 800, fontSize: 20, color: allDone ? '#1f7a4d' : '#B04E00' }}>{pct}%</div>
      </div>

      {/* progress bar */}
      <div style={{ height: 8, borderRadius: 99, background: 'rgba(0,0,0,0.12)', overflow: 'hidden', marginBottom: 16 }}>
        <div style={{ height: '100%', width: `${pct}%`, background: allDone ? '#1f7a4d' : '#F4A623', transition: 'width .4s ease' }} />
      </div>

      <div style={{ display: 'grid', gap: 8 }}>
        {feats.map((f) => (
          <Link key={f.key} href={f.route} style={{
            display: 'flex', alignItems: 'center', gap: 12, textDecoration: 'none', color: 'inherit',
            padding: '10px 12px', borderRadius: 10,
            border: '1px solid rgba(0,0,0,0.10)',
            background: f.accepted ? 'rgba(31,122,77,0.06)' : 'rgba(255,255,255,0.03)',
          }}>
            <span style={{ fontSize: 16 }}>{f.accepted ? '✅' : '⬜'}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>{f.label}</div>
              <div style={{ fontSize: 12, opacity: 0.7, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.desc}</div>
            </div>
            <span style={{
              fontSize: 12.5, fontWeight: 700, whiteSpace: 'nowrap',
              color: f.accepted ? '#1f7a4d' : '#B04E00',
            }}>{f.accepted ? 'Done' : 'Open & sign off →'}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}
