'use client'
import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { C, serif, card } from '../../ui'
import { APP_THEMES, readTheme, saveTheme, type AppThemeKey } from '../../appThemes'

const ORDER: AppThemeKey[] = ['direct', 'warm', 'night']

export default function Appearance() {
  const [picked, setPicked] = useState<AppThemeKey>('direct')
  useEffect(() => { setPicked(readTheme()) }, [])

  function choose(k: AppThemeKey) {
    setPicked(k)
    saveTheme(k)          // applies immediately — the screen recolours under you
  }

  return (
    <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div>
        <h1 style={{ fontFamily: serif, fontSize: 26, fontWeight: 700, color: C.head, margin: 0 }}>Appearance</h1>
        <p style={{ fontSize: 14, color: C.inkSoft, margin: '6px 0 0' }}>
          Pick how Omni looks on this phone. It changes straight away and only for you.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {ORDER.map(k => {
          const t = APP_THEMES[k]
          const on = picked === k
          return (
            <button key={k} onClick={() => choose(k)} className="oa-press"
              aria-pressed={on}
              style={{
                ...card, textAlign: 'left', padding: 14, minHeight: 44, cursor: 'pointer',
                borderColor: on ? C.orange : C.line, borderWidth: on ? 2 : 1, borderStyle: 'solid',
                display: 'flex', alignItems: 'center', gap: 14,
              }}>
              <div aria-hidden="true" style={{
                width: 54, height: 54, borderRadius: 14, overflow: 'hidden',
                display: 'grid', gridTemplateRows: '1fr 1fr', flex: '0 0 auto',
                border: `1px solid ${C.line}`,
              }}>
                <div style={{ background: t.swatch[0] }} />
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
                  <div style={{ background: t.swatch[1] }} />
                  <div style={{ background: t.swatch[2] }} />
                </div>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: serif, fontSize: 18, fontWeight: 700, color: C.head }}>{t.name}</div>
                <div style={{ fontSize: 13, color: C.inkSoft, marginTop: 2 }}>{t.blurb}</div>
              </div>
              {on && <Check size={22} color={C.orange} aria-label="Chosen" />}
            </button>
          )
        })}
      </div>
    </div>
  )
}
