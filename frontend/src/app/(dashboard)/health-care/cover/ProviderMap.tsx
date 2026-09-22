'use client'

/**
 * ProviderMap — the accredited-practice locator for the Health Cover page.
 *
 * Ported from Steven Diaz's `provider-map.html` (design-canvas, 2026-08-12). The
 * original pulled d3 off a public CDN at runtime; this renders the same baked
 * survey geometry (country, 10 districts, rivers, salt pans) as plain bundled SVG
 * with a dependency-free equirectangular projection — no map-tile service, no
 * external download, works offline. Practice positions are approximate within each
 * town (the source list carries a town, not a per-address lat/lng).
 *
 * Accessible by construction: the map is decorative (aria-hidden); every town is a
 * real button, and the town list below works as a full locator without the figure.
 */

import { useMemo, useState } from 'react'
import geo from '@/lib/data/botswana-geo.json'
import providers from '@/lib/data/providers.json'
import { MapPin } from 'lucide-react'

const NAVY = '#0A1030', ORANGE = '#F47C20'

type Pt = [number, number]
type Town = { town: string; lat: number; lng: number; count: number }
type Prov = { name: string; town: string; lat: number; lng: number }

/** Collect every ring (an array of [lon,lat] pairs) from arbitrarily nested coords. */
function rings(x: unknown, out: Pt[][] = []): Pt[][] {
  if (Array.isArray(x) && x.length && Array.isArray(x[0]) && typeof (x[0] as number[])[0] === 'number') {
    out.push(x as Pt[])
  } else if (Array.isArray(x)) {
    for (const e of x) rings(e, out)
  }
  return out
}

export default function ProviderMap() {
  const towns = (providers.towns as Town[])
  const provs = (providers.providers as Prov[])
  const [sel, setSel] = useState<string | null>(null)

  const m = useMemo(() => {
    const g = geo as Record<string, any>
    const cpts: Pt[] = []
    rings(g.country).forEach(r => r.forEach(p => cpts.push(p)))
    const lons = cpts.map(p => p[0]), lats = cpts.map(p => p[1])
    const minLon = Math.min(...lons), maxLon = Math.max(...lons)
    const minLat = Math.min(...lats), maxLat = Math.max(...lats)
    const cos = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180)
    const W = 1000, scale = W / ((maxLon - minLon) * cos), H = (maxLat - minLat) * scale
    const proj = (lon: number, lat: number): Pt => [(lon - minLon) * cos * scale, (maxLat - lat) * scale]
    const toPath = (coords: unknown, close: boolean) =>
      rings(coords).map(r => 'M' + r.map(p => { const [x, y] = proj(p[0], p[1]); return `${x.toFixed(1)} ${y.toFixed(1)}` }).join('L') + (close ? 'Z' : '')).join(' ')
    return {
      W, H, proj,
      country: toPath(g.country, true),
      districts: (g.districts as any[]).map((d: any) => toPath(d.g, true)) as string[],
      rivers: (g.rivers as any[]).map((r: any) => toPath(r.c, false)) as string[],
      pans: ([...g.pans, ...g.lakes] as any[]).map((p: any) => toPath(p.g, true)) as string[],
    }
  }, [])

  const selProvs = useMemo(() => sel ? provs.filter(p => p.town === sel) : [], [sel, provs])
  const maxCount = useMemo(() => Math.max(...towns.map(t => t.count)), [towns])

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      {/* ── the map ─────────────────────────────────────────────── */}
      <div style={{ border: '1px solid rgba(255,255,255,.14)', background: 'rgba(255,255,255,.02)' }}>
        <svg viewBox={`0 0 ${m.W} ${m.H}`} role="img"
             aria-label="Map of Botswana showing Alpha Direct accredited practices by town"
             style={{ width: '100%', height: 'auto', display: 'block' }}>
          <path d={m.country} fill="rgba(255,255,255,.04)" stroke="rgba(255,255,255,.28)" strokeWidth={2} />
          {m.districts.map((d, i) => (
            <path key={i} d={d} fill="none" stroke="rgba(255,255,255,.12)" strokeWidth={1} />
          ))}
          {m.pans.map((p, i) => (
            <path key={i} d={p} fill="rgba(255,255,255,.07)" stroke="none" />
          ))}
          {m.rivers.map((r, i) => (
            <path key={i} d={r} fill="none" stroke="rgba(120,170,255,.28)" strokeWidth={1} />
          ))}
          {/* selected town's individual practices */}
          {selProvs.map((p, i) => {
            const [x, y] = m.proj(p.lng, p.lat)
            return <circle key={i} cx={x} cy={y} r={3} fill="#fff" opacity={0.9} />
          })}
          {/* town markers, sized by practice count */}
          {towns.map(t => {
            const [x, y] = m.proj(t.lng, t.lat)
            const r = 4 + 12 * Math.sqrt(t.count / maxCount)
            const on = sel === t.town
            return (
              <g key={t.town}>
                <circle cx={x} cy={y} r={r} fill={on ? '#fff' : ORANGE} opacity={on ? 1 : 0.82}
                        stroke={on ? ORANGE : 'none'} strokeWidth={on ? 3 : 0} />
                <circle cx={x} cy={y} r={Math.max(r, 14)} fill="transparent" style={{ cursor: 'pointer' }}
                        role="button" tabIndex={0}
                        aria-label={`${t.town} — ${t.count} accredited ${t.count === 1 ? 'practice' : 'practices'}`}
                        onClick={() => setSel(on ? null : t.town)}
                        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSel(on ? null : t.town) } }} />
              </g>
            )
          })}
        </svg>
      </div>

      {/* ── town list (the accessible locator; works without the map) ─ */}
      <div>
        <p className="flex items-center justify-between text-white">
          <span className="font-semibold text-sm inline-flex items-center gap-2">
            <MapPin className="w-4 h-4" style={{ color: ORANGE }} />
            {(providers as any).practiceCount ?? provs.length} practices · {towns.length} towns
          </span>
          {sel && (
            <button onClick={() => setSel(null)} className="text-xs underline"
                    style={{ color: 'rgba(255,255,255,.6)' }}>clear</button>
          )}
        </p>
        <div className="mt-3 overflow-y-auto" style={{ maxHeight: 460, border: '1px solid rgba(255,255,255,.12)' }}>
          {[...towns].sort((a, b) => b.count - a.count).map((t, i) => {
            const on = sel === t.town
            return (
              <div key={t.town} style={{ borderTop: i ? '1px solid rgba(255,255,255,.08)' : undefined }}>
                <button onClick={() => setSel(on ? null : t.town)} aria-pressed={on}
                  className="w-full flex items-center justify-between px-4 py-2.5 text-left transition-colors"
                  style={{ background: on ? 'rgba(244,124,32,.16)' : 'transparent', color: '#fff' }}>
                  <span className="text-sm">{t.town.charAt(0) + t.town.slice(1).toLowerCase()}</span>
                  <span className="text-xs px-2 py-0.5 rounded"
                        style={{ background: 'rgba(255,255,255,.1)', color: 'rgba(255,255,255,.75)' }}>{t.count}</span>
                </button>
                {on && (
                  <ul className="px-4 pb-3" style={{ background: 'rgba(0,0,0,.18)' }}>
                    {selProvs.map((p, j) => (
                      <li key={j} className="text-xs py-1" style={{ color: 'rgba(255,255,255,.7)' }}>
                        {p.name.charAt(0) + p.name.slice(1).toLowerCase()}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
        <p className="mt-3 text-xs" style={{ color: 'rgba(255,255,255,.4)' }}>
          Practice positions are approximate within each town.
        </p>
      </div>
    </div>
  )
}
