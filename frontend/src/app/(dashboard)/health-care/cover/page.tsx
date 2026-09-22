'use client'

/**
 * /health-care/cover — Alpha Direct Health Cover.
 *
 * Ported from Steven Diaz's design-canvas mockup (2026-08-07). The design is his;
 * the delivery is rebuilt, because the original could not be put in front of anyone:
 *
 *  - It pulled React, ReactDOM and the Babel compiler off a public CDN and compiled
 *    itself in the browser on every visit. Omni already ships React, so that meant a
 *    second copy, a page that needs `unsafe-eval`, and a blank screen for anyone whose
 *    network blocks unpkg. Everything is local now — no runtime downloads at all.
 *  - The two hero photos were 1.6 MB each. Re-encoded to WebP: 3.23 MB -> 252 KB.
 *  - The whole thing was a fixed 1180x852 canvas scaled with a CSS transform, so a
 *    phone rendered it at about a third size. This is a real responsive layout.
 *  - The benefit hotspots were hover-only 9px dots — invisible to a keyboard and to a
 *    screen reader. They are buttons now, focusable, with aria-pressed.
 *  - The footer said (c) 2024 and the only call to action was "message the CFO". The CFO's
 *    address is gone: this is a public page, so it carries the health team's own contact
 *    details, taken from the Benefits Booklet 2025/26 p.24 (CFO directive 2026-08-09).
 *
 * THE FIGURES ARE NOT SIGNED OFF. Every limit in lib/healthCover.ts was typed in by
 * hand from the Benefits Booklet 2025/26 with no machine-readable source behind it.
 * The banner at the top of the page says so, deliberately, and stays until the Health
 * product owner has checked them line by line.
 */
import { useMemo, useState } from 'react'
import Image from 'next/image'
import { TopBar } from '@/components/layout/TopBar'
import {
  PLANS, IDX, LIMITS, NC, HOTSPOTS, REGION_ORDER, regionFor, money,
  type PlanId, type Cell,
} from '@/lib/healthCover'
import { AlertTriangle, ArrowRight, MapPin } from 'lucide-react'
import ProviderMap from './ProviderMap'

const NAVY = '#0A1030', ORANGE = '#F47C20'

const REGION_LABEL: Record<string, string> = {
  mind: 'Mind', eyes: 'Eyes', teeth: 'Teeth', heart: 'Heart',
  blood: 'Blood', body: 'Body', bones: 'Bones',
}

/** One benefit cell: a plain value, or single/family split. */
function CellValue({ c }: { c: Cell }) {
  if (Array.isArray(c)) {
    return (
      <span>
        <b style={{ color: '#fff' }}>{money(c[0])}</b>
        <span style={{ color: 'rgba(255,255,255,.45)' }}> single</span>
        <span style={{ color: 'rgba(255,255,255,.28)' }}> · </span>
        <b style={{ color: '#fff' }}>{money(c[1])}</b>
        <span style={{ color: 'rgba(255,255,255,.45)' }}> family</span>
      </span>
    )
  }
  const off = c === NC
  return <span style={{ color: off ? 'rgba(255,255,255,.38)' : '#fff' }}>{money(c)}</span>
}

export default function HealthCoverPage() {
  const [gender, setGender] = useState<'woman' | 'man'>('woman')
  const [plan, setPlan] = useState<PlanId>('essential')
  const [region, setRegion] = useState<string>('heart')

  const planIdx = IDX[plan]
  const limits = LIMITS[plan]
  const active = useMemo(() => regionFor(region, gender), [region, gender])
  const rows = useMemo(
    () => [...active.rows, ...(gender === 'woman' && active.female ? active.female : [])],
    [active, gender])
  const planMeta = PLANS.find(p => p.id === plan)!

  const mono = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"

  return (
    <div>
      <TopBar title="Health Cover" breadcrumbs={[{ label: 'Health Care' }, { label: 'Health Cover' }]} />

      <div style={{ background: NAVY, minHeight: '100vh' }}>
        {/* figures-not-signed-off banner — stays until Health checks them */}
        <div className="px-5 sm:px-8 pt-5">
          <div className="rounded-lg px-4 py-3 flex items-start gap-2.5"
               style={{ background: 'rgba(244,124,32,.12)', border: '1px solid rgba(244,124,32,.4)' }}>
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: ORANGE }} />
            <p className="text-xs leading-relaxed" style={{ color: 'rgba(255,255,255,.82)' }}>
              <b>Not yet checked.</b> Every limit on this page was typed in by hand from the
              Benefits Booklet 2025/26. Health has not yet read them back against the booklet,
              so do not quote them to a member or an employer.
            </p>
          </div>
        </div>

        <div className="px-5 sm:px-8 py-6">
          <p style={{ font: `700 11px/1 ${mono}`, letterSpacing: '.18em', color: ORANGE }}>
            EMPLOYER GROUP HEALTH COVER
          </p>
          <h1 className="mt-3 font-bold text-white"
              style={{ fontSize: 'clamp(26px, 4.4vw, 46px)', lineHeight: 1.08, letterSpacing: '-0.02em' }}>
            Welcome to Alpha Direct Health Insurance
          </h1>

          {/* three columns on desktop, stacked on a phone */}
          <div className="mt-7 grid gap-6 lg:gap-8" style={{ gridTemplateColumns: 'minmax(0,1fr)' }}>
            <div className="grid gap-6 lg:gap-8 lg:grid-cols-[minmax(0,1fr)_320px_minmax(0,1fr)]">

              {/* ── left: annual limits ─────────────────────────────── */}
              <div>
                <p style={{ font: `700 10px/1 ${mono}`, letterSpacing: '.18em', color: 'rgba(255,255,255,.5)' }}>
                  TOTAL ANNUAL LIMITS
                </p>
                <div className="mt-3" style={{ border: '1px solid rgba(255,255,255,.16)' }}>
                  {[
                    { k: 'INPATIENT', s: limits.inS, f: limits.inF },
                    { k: 'OUTPATIENT', s: limits.outS, f: limits.outF },
                  ].map((L, i) => (
                    <div key={L.k} className="px-4 py-4"
                         style={{ borderTop: i ? '1px solid rgba(255,255,255,.12)' : undefined,
                                  background: 'rgba(255,255,255,.03)' }}>
                      <p style={{ font: `500 10px/1 ${mono}`, letterSpacing: '.14em', color: 'rgba(255,255,255,.5)' }}>
                        {L.k} · SINGLE
                      </p>
                      <p className="mt-2 font-bold text-white" style={{ fontSize: 26, letterSpacing: '-0.01em' }}>
                        {L.s === NC ? NC : `P${L.s}`}
                      </p>
                      <p className="mt-1 text-xs" style={{ color: 'rgba(255,255,255,.55)' }}>
                        Family {L.f === NC ? NC.toLowerCase() : `P${L.f}`}
                      </p>
                    </div>
                  ))}
                </div>
                <p className="mt-3 text-xs" style={{ color: 'rgba(255,255,255,.45)' }}>
                  Pula per policy year, per plan
                </p>
              </div>

              {/* ── middle: figure + hotspots ───────────────────────── */}
              <div>
                <div role="group" aria-label="Choose a figure" className="flex"
                     style={{ border: '1px solid rgba(255,255,255,.2)', background: 'rgba(255,255,255,.16)', gap: 1 }}>
                  {(['man', 'woman'] as const).map(g => (
                    <button key={g} onClick={() => setGender(g)} aria-pressed={gender === g}
                      className="flex-1 py-3 transition-colors"
                      style={{ background: gender === g ? ORANGE : 'transparent', color: '#fff',
                               font: `700 12px/1 ${mono}`, letterSpacing: '.14em', textTransform: 'uppercase' }}>
                      {g}
                    </button>
                  ))}
                </div>

                <div className="relative mt-4 mx-auto" style={{
                  maxWidth: 300, aspectRatio: '300 / 640',
                  border: '1px solid rgba(244,124,32,.32)',
                  background: 'linear-gradient(180deg, rgba(20,32,80,.5), rgba(8,13,36,.2))',
                }}>
                  <Image
                    src={`/health/${gender}.webp`}
                    alt={gender === 'woman' ? 'Alpha Direct health cover — figure' : 'Alpha Direct health cover — figure'}
                    fill sizes="300px" priority
                    style={{ objectFit: 'cover' }}
                  />
                  {/* scanline texture */}
                  <div aria-hidden className="absolute inset-0 pointer-events-none" style={{
                    backgroundImage: 'repeating-linear-gradient(rgba(255,255,255,.04) 0 1px, rgba(255,255,255,0) 1px 7px)',
                  }} />
                  {/* hotspots — real buttons, keyboard reachable */}
                  {HOTSPOTS[gender].map(h => {
                    const on = region === h.key
                    return (
                      <button
                        key={h.key}
                        onClick={() => setRegion(h.key)}
                        onMouseEnter={() => setRegion(h.key)}
                        aria-pressed={on}
                        aria-label={`Show ${REGION_LABEL[h.key]} benefits`}
                        className="absolute rounded-full focus:outline-none"
                        style={{
                          left: `${h.x}%`, top: `${h.y}%`, width: 22, height: 22,
                          transform: 'translate(-50%,-50%)', background: 'transparent', border: 0, cursor: 'pointer',
                        }}
                      >
                        <span className="block mx-auto rounded-full" style={{
                          width: on ? 12 : 9, height: on ? 12 : 9,
                          background: on ? '#fff' : ORANGE,
                          boxShadow: `0 0 0 3px ${on ? 'rgba(255,255,255,.35)' : 'rgba(244,124,32,.25)'}`,
                          transition: 'all .15s ease',
                        }} />
                      </button>
                    )
                  })}
                </div>

                <div className="flex flex-wrap justify-center gap-1.5 mt-4">
                  {REGION_ORDER.map(k => (
                    <button key={k} onClick={() => setRegion(k)} aria-pressed={region === k}
                      className="px-2.5 py-1.5 rounded transition-colors"
                      style={{
                        font: `700 10px/1 ${mono}`, letterSpacing: '.1em', textTransform: 'uppercase',
                        color: region === k ? NAVY : 'rgba(255,255,255,.7)',
                        background: region === k ? ORANGE : 'rgba(255,255,255,.07)',
                      }}>
                      {REGION_LABEL[k]}
                    </button>
                  ))}
                </div>
              </div>

              {/* ── right: plan picker ──────────────────────────────── */}
              <div>
                <p style={{ font: `700 10px/1 ${mono}`, letterSpacing: '.18em', color: 'rgba(255,255,255,.5)' }}>
                  CURRENTLY VIEWING
                </p>
                <div className="mt-3" style={{ border: '1px solid rgba(255,255,255,.16)' }}>
                  {PLANS.map((p, i) => {
                    const on = p.id === plan
                    return (
                      <button key={p.id} onClick={() => setPlan(p.id as PlanId)} aria-pressed={on}
                        className="w-full flex items-center justify-between px-4 py-3 transition-colors"
                        style={{
                          borderTop: i ? '1px solid rgba(255,255,255,.1)' : undefined,
                          background: on ? ORANGE : 'rgba(255,255,255,.03)',
                          color: '#fff',
                        }}>
                        <span className="font-semibold text-sm">{p.name}</span>
                        <span style={{ font: `500 12px/1 ${mono}`, opacity: on ? 1 : .75 }}>P{p.price}/mth</span>
                      </button>
                    )
                  })}
                </div>
                <p className="mt-3 text-xs leading-relaxed" style={{ color: 'rgba(255,255,255,.6)' }}>
                  {planMeta.tag}
                </p>
                <a href="#providers"
                   className="mt-5 flex items-center justify-between gap-2 px-4 h-14 transition-colors"
                   style={{ border: '1.5px solid rgba(255,255,255,.4)', color: '#fff',
                            font: `700 12px/1.2 ${mono}`, letterSpacing: '.1em', textTransform: 'uppercase' }}>
                  <span className="inline-flex items-center gap-2"><MapPin className="w-4 h-4" /> Find providers near you</span>
                  <ArrowRight className="w-4 h-4" />
                </a>
              </div>
            </div>

            {/* ── benefits table for the chosen region + plan ───────── */}
            <div style={{ border: '1px solid rgba(255,255,255,.14)' }}>
              <div className="px-4 sm:px-5 py-3 flex items-center justify-between flex-wrap gap-2"
                   style={{ borderBottom: '1px solid rgba(255,255,255,.14)', background: 'rgba(255,255,255,.04)' }}>
                <p className="font-semibold text-white text-sm">{active.title}</p>
                <p style={{ font: `500 11px/1 ${mono}`, color: 'rgba(255,255,255,.55)' }}>
                  {planMeta.name.toUpperCase()} · P{planMeta.price}/MTH
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full" style={{ borderCollapse: 'collapse' }}>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={r.name} style={{ background: i % 2 ? 'rgba(255,255,255,.025)' : 'transparent' }}>
                        <th scope="row" className="text-left px-4 sm:px-5 py-3 font-normal align-top"
                            style={{ color: 'rgba(255,255,255,.72)', fontSize: 13, minWidth: 240 }}>
                          {r.name}
                        </th>
                        <td className="px-4 sm:px-5 py-3 align-top" style={{ fontSize: 13 }}>
                          <CellValue c={r.v[planIdx]} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* ── provider map (offline, no tile service) ──────────── */}
            <div id="providers" className="pt-4 scroll-mt-6">
              <h2 className="font-bold text-white"
                  style={{ fontSize: 'clamp(22px, 3.2vw, 34px)', lineHeight: 1.06, letterSpacing: '-0.02em' }}>
                Find health providers near you
              </h2>
              <p className="mt-2 mb-5 text-sm" style={{ color: 'rgba(255,255,255,.6)' }}>
                Alpha Direct accredits practices across all ten districts of Botswana.
              </p>
              <ProviderMap />
            </div>

            {/* ── footer ───────────────────────────────────────────── */}
            <div className="flex flex-wrap items-center justify-between gap-4 pt-2">
              <div className="flex flex-wrap gap-x-8 gap-y-2">
                <div>
                  <p style={{ font: `700 9px/1 ${mono}`, letterSpacing: '.16em', color: 'rgba(255,255,255,.4)' }}>CUSTOMER CARE</p>
                  <p className="text-white text-sm mt-1">+267 370 2744</p>
                </div>
                <div>
                  <p style={{ font: `700 9px/1 ${mono}`, letterSpacing: '.16em', color: 'rgba(255,255,255,.4)' }}>EMERGENCY (MRI)</p>
                  <p className="text-white text-sm mt-1">992</p>
                </div>
                <div>
                  <p style={{ font: `700 9px/1 ${mono}`, letterSpacing: '.16em', color: 'rgba(255,255,255,.4)' }}>HEALTH TEAM</p>
                  <p className="text-white text-sm mt-1">health@alphadirect.co.bw</p>
                </div>
                <div>
                  <p style={{ font: `700 9px/1 ${mono}`, letterSpacing: '.16em', color: 'rgba(255,255,255,.4)' }}>CLAIMS</p>
                  <p className="text-white text-sm mt-1">healthclaims@alphadirect.co.bw</p>
                </div>
              </div>
              <p style={{ font: `400 11px/1.5 ${mono}`, color: 'rgba(255,255,255,.38)' }}>
                Alpha Direct Insurance Company &copy; {new Date().getFullYear()} · Employer group cover only ·
                Benefits Booklet 2025/26 · Terms, exclusions and waiting periods apply.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
