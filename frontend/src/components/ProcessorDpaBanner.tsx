'use client'

// Shows a call-to-action on the signed-in user's own dashboard when an external
// processor agreement (DPA) is awaiting THEIR signature — matched to their email
// by the backend. Renders nothing for everyone else. CFO 2026-07-29 ("put in his
// dashboard"): so a logged-in processor (e.g. Pramod) reviews and signs in-app.

import { useEffect, useState } from 'react'
import { getMyDpas, type MyDpaItem } from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

export function ProcessorDpaBanner() {
  const [items, setItems] = useState<MyDpaItem[]>([])

  useEffect(() => {
    let alive = true
    getMyDpas()
      .then((r) => { if (alive) setItems(r.pending || []) })
      .catch(() => { /* silent — the banner just stays hidden */ })
    return () => { alive = false }
  }, [])

  if (!items.length) return null

  return (
    <div className="space-y-3">
      {items.map((d) => (
        <div
          key={d.reference || d.processor}
          role="alert"
          style={{
            background: NAVY,
            borderRadius: 14,
            padding: '18px 20px',
            color: '#fff',
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 14,
            boxShadow: '0 6px 22px rgba(13,27,42,0.18)',
          }}
        >
          <div style={{ minWidth: 240, flex: 1 }}>
            <div style={{ color: ORANGE, fontSize: 11, letterSpacing: '2px', textTransform: 'uppercase', fontWeight: 700, marginBottom: 4 }}>
              Agreement awaiting your signature
            </div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>
              Data Processing Agreement — {d.processor}
            </div>
            <div style={{ fontSize: 13, color: '#C7D0DC', marginTop: 4 }}>
              {d.purpose}
            </div>
          </div>
          <a
            href={d.sign_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              background: ORANGE,
              color: NAVY,
              fontWeight: 700,
              fontSize: 15,
              padding: '11px 20px',
              borderRadius: 10,
              textDecoration: 'none',
              whiteSpace: 'nowrap',
            }}
          >
            Review &amp; sign →
          </a>
        </div>
      ))}
    </div>
  )
}
