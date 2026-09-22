'use client'

/**
 * /health-care/providers — the Alpha Direct Health network.
 *
 * The "Find providers near you" button on /health-care/cover has pointed here
 * since 2026-08-07 and there was no page behind it. This is the page.
 *
 * Data: lib/healthProviders.ts, generated from the register Steven Diaz supplied
 * on 2026-08-09 — 247 practices, 34 towns, 17 disciplines. Generated, never
 * re-typed.
 *
 * NO PRACTITIONER EMAIL ADDRESSES. 197 of the 248 source rows carry an
 * individual doctor's personal gmail / yahoo / hotmail address. A member does
 * not need a doctor's private address to find a clinic — they need the
 * practice, what it does, and where it is.
 *
 * ON "PUBLIC": this page is DESIGNED for public use (Steven Diaz, 2026-08-09)
 * but is NOT public — it sits inside the Omni dashboard behind SSO. Taking it
 * public is a separate, deliberate decision with its own security posture.
 * Do not read "already public" off this comment when deciding what data is
 * safe to put on the page.
 */
import { useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { PROVIDERS, type Provider } from '@/lib/healthProviders'
import { MapPin, Search, X, Building2, ArrowLeft } from 'lucide-react'

const NAVY = '#0A1030', ORANGE = '#F47C20'
const mono = 'ui-monospace, SFMono-Regular, Menlo, monospace'

const ALL = 'All'

function useFacets() {
  return useMemo(() => {
    const towns = new Map<string, number>()
    const discs = new Map<string, number>()
    for (const p of PROVIDERS) {
      towns.set(p.t, (towns.get(p.t) ?? 0) + 1)
      discs.set(p.d, (discs.get(p.d) ?? 0) + 1)
    }
    const bySize = (a: [string, number], b: [string, number]) =>
      b[1] - a[1] || a[0].localeCompare(b[0])
    return {
      towns: [...towns.entries()].sort(bySize),
      discs: [...discs.entries()].sort(bySize),
    }
  }, [])
}

function Chip({ label, count, on, onClick }: {
  label: string; count?: number; on: boolean; onClick: () => void
}) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on}
      className="rounded-full px-3 py-1.5 text-xs font-semibold transition-colors whitespace-nowrap"
      style={{
        background: on ? ORANGE : 'rgba(255,255,255,.06)',
        color: on ? '#fff' : 'rgba(255,255,255,.75)',
        border: `1px solid ${on ? ORANGE : 'rgba(255,255,255,.16)'}`,
      }}>
      {label}{count != null && <span style={{ opacity: .65 }}> {count}</span>}
    </button>
  )
}

export default function HealthProvidersPage() {
  const { towns, discs } = useFacets()
  const [town, setTown] = useState(ALL)
  const [disc, setDisc] = useState(ALL)
  const [q, setQ] = useState('')

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return PROVIDERS.filter((p: Provider) =>
      (town === ALL || p.t === town) &&
      (disc === ALL || p.d === disc) &&
      (!needle ||
        p.n.toLowerCase().includes(needle) ||
        p.l.toLowerCase().includes(needle) ||
        p.t.toLowerCase().includes(needle) ||
        p.d.toLowerCase().includes(needle)))
  }, [town, disc, q])

  const grouped = useMemo(() => {
    const m = new Map<string, Provider[]>()
    for (const p of results) (m.get(p.t) ?? m.set(p.t, []).get(p.t)!).push(p)
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
  }, [results])

  const filtered = town !== ALL || disc !== ALL || q.trim() !== ''

  return (
    <>
      <TopBar />
      <div style={{ background: NAVY, minHeight: '100vh' }}>
        <div className="mx-auto w-full max-w-5xl px-4 sm:px-6 py-8">

          <a href="/health-care/cover"
             className="inline-flex items-center gap-2 mb-6 text-sm"
             style={{ color: 'rgba(255,255,255,.6)' }}>
            <ArrowLeft className="w-4 h-4" /> Health Cover
          </a>

          <header className="mb-7">
            <p style={{ font: `700 10px/1 ${mono}`, letterSpacing: '.18em', color: ORANGE }}>
              ALPHA DIRECT HEALTH
            </p>
            <h1 className="mt-2 text-3xl sm:text-4xl font-semibold text-white tracking-tight">
              Where you can be seen
            </h1>
            <p className="mt-2 text-sm sm:text-base" style={{ color: 'rgba(255,255,255,.6)' }}>
              {PROVIDERS.length} practices across {towns.length} towns. Show your membership
              card — no cash, no claim form.
            </p>
          </header>

          {/* search */}
          <div className="relative mb-4">
            <Search className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2"
                    style={{ color: 'rgba(255,255,255,.4)' }} />
            <label htmlFor="q" className="sr-only">Search providers</label>
            <input id="q" value={q} onChange={e => setQ(e.target.value)}
              placeholder="Search a practice, a town, a street…"
              className="w-full h-12 pl-11 pr-11 text-white text-sm"
              style={{ background: 'rgba(255,255,255,.05)', border: '1px solid rgba(255,255,255,.16)' }} />
            {q && (
              <button type="button" onClick={() => setQ('')} aria-label="Clear search"
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1">
                <X className="w-4 h-4" style={{ color: 'rgba(255,255,255,.5)' }} />
              </button>
            )}
          </div>

          {/* facets */}
          <div className="space-y-3 mb-6">
            <div>
              <p style={{ font: `700 9px/1 ${mono}`, letterSpacing: '.16em', color: 'rgba(255,255,255,.4)' }}
                 className="mb-2">WHAT YOU NEED</p>
              <div className="flex flex-wrap gap-2">
                <Chip label="All" on={disc === ALL} onClick={() => setDisc(ALL)} />
                {discs.map(([d, n]) => (
                  <Chip key={d} label={d} count={n} on={disc === d}
                        onClick={() => setDisc(disc === d ? ALL : d)} />
                ))}
              </div>
            </div>
            <div>
              <p style={{ font: `700 9px/1 ${mono}`, letterSpacing: '.16em', color: 'rgba(255,255,255,.4)' }}
                 className="mb-2">WHERE</p>
              <div className="flex flex-wrap gap-2">
                <Chip label="All" on={town === ALL} onClick={() => setTown(ALL)} />
                {towns.map(([t, n]) => (
                  <Chip key={t} label={t} count={n} on={town === t}
                        onClick={() => setTown(town === t ? ALL : t)} />
                ))}
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
            <p className="text-sm" style={{ color: 'rgba(255,255,255,.6)' }}>
              <b className="text-white">{results.length}</b>
              {results.length === 1 ? ' practice' : ' practices'}
            </p>
            {filtered && (
              <button type="button"
                onClick={() => { setTown(ALL); setDisc(ALL); setQ('') }}
                className="text-xs underline" style={{ color: 'rgba(255,255,255,.55)' }}>
                Clear filters
              </button>
            )}
          </div>

          {results.length === 0 ? (
            <div className="py-16 text-center" style={{ color: 'rgba(255,255,255,.55)' }}>
              <Building2 className="w-7 h-7 mx-auto mb-3" style={{ opacity: .5 }} />
              <p className="text-sm">Nothing matches that.</p>
              <p className="text-xs mt-2">
                Try a nearby town, or call us on <span className="text-white">+267 370 2744</span>.
              </p>
            </div>
          ) : (
            <div className="space-y-7">
              {grouped.map(([t, list]) => (
                <section key={t}>
                  <h2 className="flex items-center gap-2 mb-2 text-white font-semibold text-sm">
                    <MapPin className="w-4 h-4" style={{ color: ORANGE }} /> {t}
                    <span style={{ font: `500 11px/1 ${mono}`, color: 'rgba(255,255,255,.4)' }}>
                      {list.length}
                    </span>
                  </h2>
                  <div style={{ border: '1px solid rgba(255,255,255,.14)' }}>
                    {list.map((p, i) => (
                      <div key={`${p.n}-${i}`} className="px-4 py-3 flex flex-wrap gap-x-4 gap-y-1 items-baseline"
                           style={{ borderTop: i ? '1px solid rgba(255,255,255,.08)' : undefined }}>
                        <p className="text-white text-sm font-medium flex-1 min-w-[180px]">{p.n}</p>
                        <p style={{ font: `600 10px/1 ${mono}`, letterSpacing: '.08em',
                                    color: ORANGE, textTransform: 'uppercase' }}>{p.d}</p>
                        {p.l && (
                          <p className="w-full text-xs" style={{ color: 'rgba(255,255,255,.5)' }}>{p.l}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}

          {/* contact — the health team, per the Benefits Booklet 2025/26 p.24 */}
          <div className="mt-10 pt-6" style={{ borderTop: '1px solid rgba(255,255,255,.14)' }}>
            <p style={{ font: `700 9px/1 ${mono}`, letterSpacing: '.16em', color: 'rgba(255,255,255,.4)' }}>
              CAN&rsquo;T FIND ONE?
            </p>
            <div className="mt-3 flex flex-wrap gap-x-10 gap-y-3">
              <div>
                <p className="text-xs" style={{ color: 'rgba(255,255,255,.5)' }}>Customer care</p>
                <p className="text-white text-sm mt-0.5">+267 370 2744</p>
              </div>
              <div>
                <p className="text-xs" style={{ color: 'rgba(255,255,255,.5)' }}>Health team</p>
                <p className="text-white text-sm mt-0.5">health@alphadirect.co.bw</p>
              </div>
              <div>
                <p className="text-xs" style={{ color: 'rgba(255,255,255,.5)' }}>Emergency (MRI)</p>
                <p className="text-white text-sm mt-0.5">992</p>
              </div>
            </div>
            <p className="mt-5" style={{ font: `400 11px/1.5 ${mono}`, color: 'rgba(255,255,255,.38)' }}>
              Network as at 13 July 2026. Providers change — confirm with the practice before you travel.
            </p>
          </div>

        </div>
      </div>
    </>
  )
}
