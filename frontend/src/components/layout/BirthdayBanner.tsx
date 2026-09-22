'use client'

/**
 * BirthdayBanner — top-of-dashboard ribbon.
 *
 * CFO directive 2026-06-09 (original): show today's birthdays + a self splash.
 * CFO directive 2026-06-12 (enhancement): keep TODAY's birthday(s) pinned on
 * the left, and scroll the UPCOMING week's birthdays past in a marquee so the
 * team can see them coming. `<marquee>` is long-deprecated, so this is the
 * modern CSS-animation equivalent (transform loop, pauses on hover for
 * readability, respects prefers-reduced-motion).
 *
 * One call to /employees/birthdays-upcoming/?in_days=7 returns both:
 *   days_until === 0 → today's hero  ·  days_until >= 1 → marquee.
 *
 * No PII leak: API returns month-day only, never year-of-birth.
 */

import { useEffect, useState } from 'react'
import { getBirthdaysUpcoming, getRBACMe } from '@/lib/api'
import type { BirthdayEmployee } from '@/lib/api'
import { Cake, X, PartyPopper, Heart } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'

function todayKey(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function whenLabel(days: number): string {
  if (days <= 0) return 'today'
  if (days === 1) return 'tomorrow'
  return `in ${days} days`
}

export function BirthdayBanner() {
  const { theme } = useTheme()
  const [list, setList] = useState<BirthdayEmployee[]>([])
  const [me, setMe]     = useState<{ email?: string; full_name?: string } | null>(null)
  const [ribbonOpen, setRibbonOpen] = useState(true)
  const [selfOpen, setSelfOpen]     = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [bd, me] = await Promise.all([
          getBirthdaysUpcoming(7),
          getRBACMe().catch(() => null),
        ])
        if (cancelled) return
        setList(bd.employees || [])
        setMe(me ? { email: (me as any).email, full_name: (me as any).full_name } : null)
      } catch { /* fail silent — feature can't break the dashboard */ }
    })()
    return () => { cancelled = true }
  }, [])

  if (list.length === 0) return null

  const today    = list.filter(e => e.days_until <= 0)
  const upcoming = list.filter(e => e.days_until >= 1)
                       .sort((a, b) => a.days_until - b.days_until)

  const key = todayKey()
  const ribbonKey = `bd_seen_${key}`
  const selfKey   = `bd_self_seen_${key}`

  // Self-birthday detection — match by email (case-insensitive), today only.
  const myEmail = (me?.email || '').toLowerCase().trim()
  const selfMatch = myEmail
    ? today.find(e => (e.email || '').toLowerCase().trim() === myEmail)
    : null

  const ribbonDismissed = typeof window !== 'undefined' && localStorage.getItem(ribbonKey) === '1'
  const selfDismissed   = typeof window !== 'undefined' && localStorage.getItem(selfKey)   === '1'

  // Duplicate the upcoming list so the marquee track loops seamlessly.
  const marqueeItems = upcoming.length > 0 ? [...upcoming, ...upcoming] : []
  // Scale duration to item count so speed feels constant regardless of length.
  const marqueeSeconds = Math.max(18, upcoming.length * 6)

  return (
    <>
      {!ribbonDismissed && ribbonOpen && list.length > 0 && (
        // Redesign pack 05 §2: refined to a SLIM GLASS STRIP instead of the
        // loud full-bleed orange bar — a quiet gift glyph, names in the theme's
        // primary text, and a subtle orange "say happy birthday" cue. Data
        // source, per-day dismissal and the upcoming-week marquee are unchanged.
        <div
          role="region"
          aria-label="Birthdays this week"
          style={{
            background: theme.card,
            borderBottom: `1px solid ${theme.cardBdr}`,
            // hairline orange accent on the left edge — present, not dominant
            boxShadow: `inset 3px 0 0 ${theme.orange}`,
            WebkitBackdropFilter: 'blur(8px)',
            backdropFilter: 'blur(8px)',
          }}
        >
          <style>{`
            @keyframes bd-marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
            .bd-marquee-track {
              display: inline-flex; white-space: nowrap; will-change: transform;
              animation: bd-marquee var(--bd-dur, 30s) linear infinite;
            }
            .bd-marquee-mask:hover .bd-marquee-track { animation-play-state: paused; }
            @media (prefers-reduced-motion: reduce) {
              .bd-marquee-track { animation: none; }
            }
          `}</style>

          <div className="flex items-center gap-3 px-5 py-2 text-[13px]" style={{ color: theme.text }}>
            <Cake className="w-4 h-4 flex-shrink-0" style={{ color: theme.orange }} />

            {/* Pinned: today's birthdays (stays visible). */}
            {today.length > 0 ? (
              <span className="flex-shrink-0">
                <span className="font-semibold">Today&rsquo;s birthdays:</span>{' '}
                {today.map((e, i) => (
                  <span key={e.employee_id}>
                    {i > 0 && <span className="mx-1" style={{ color: theme.t3 }}>·</span>}
                    <span className="font-medium">{e.full_name}</span>
                    {e.department && <span style={{ color: theme.t2 }}> &mdash; {e.department}</span>}
                  </span>
                ))}
                <span className="hidden md:inline italic ml-2" style={{ color: theme.orange }}>say happy birthday!</span>
              </span>
            ) : (
              <span className="font-semibold flex-shrink-0">Upcoming birthdays:</span>
            )}

            {/* Marquee: the week ahead, scrolling. */}
            {marqueeItems.length > 0 && (
              <>
                {today.length > 0 && (
                  <span className="hidden sm:inline h-4 w-px flex-shrink-0" style={{ background: theme.cardBdr }} aria-hidden="true" />
                )}
                <div className="bd-marquee-mask relative flex-1 overflow-hidden min-w-0"
                     style={{ maskImage: 'linear-gradient(to right, transparent, #000 4%, #000 96%, transparent)',
                              WebkitMaskImage: 'linear-gradient(to right, transparent, #000 4%, #000 96%, transparent)' }}>
                  <div className="bd-marquee-track" style={{ ['--bd-dur' as any]: `${marqueeSeconds}s` }}>
                    {marqueeItems.map((e, i) => (
                      <span key={`${e.employee_id}-${i}`} className="mx-4 inline-flex items-center gap-1.5" style={{ color: theme.t2 }}>
                        <span aria-hidden="true">🎈</span>
                        <span>{whenLabel(e.days_until)}</span>
                        <span style={{ color: theme.t3 }}>·</span>
                        <span className="font-medium" style={{ color: theme.text }}>{e.full_name}</span>
                        {e.department && <span>&mdash; {e.department}</span>}
                      </span>
                    ))}
                  </div>
                </div>
              </>
            )}

            <button
              type="button"
              aria-label="Dismiss for today"
              onClick={() => {
                try { localStorage.setItem(ribbonKey, '1') } catch { /* ignore */ }
                setRibbonOpen(false)
              }}
              className="rounded p-1 transition-colors flex-shrink-0 hover:bg-black/5"
              style={{ color: theme.t3 }}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Self-birthday splash overlay — once per browser per day. */}
      {selfMatch && !selfDismissed && selfOpen && (
        <div
          className="fixed inset-0 z-[10000] flex items-center justify-center
                     bg-black/60 backdrop-blur-sm p-6"
          role="dialog"
          aria-modal="true"
        >
          <div
            className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8
                       text-center border-4"
            style={{ borderColor: '#F4A623' }}
          >
            <div className="flex justify-center mb-4">
              <div className="relative">
                <Cake className="w-20 h-20" style={{ color: '#F07F00' }} />
                <PartyPopper className="w-8 h-8 absolute -top-2 -right-3 text-[#F4A623] animate-bounce" />
              </div>
            </div>
            <h2 className="text-3xl font-bold mb-2" style={{ color: '#0D1B2A', fontFamily: 'serif' }}>
              Happy Birthday,
            </h2>
            <p className="text-2xl font-bold mb-4" style={{ color: '#F07F00', fontFamily: 'serif' }}>
              {selfMatch.full_name.split(' ')[0]}!
            </p>
            <p className="text-sm text-[#374151] mb-6">
              Wishing you a fantastic day from everyone at Alpha Direct.
              May this year bring you joy, growth, and big wins. <Heart className="inline w-4 h-4 text-[#DC2626]" />
            </p>
            <button
              type="button"
              onClick={() => {
                try { localStorage.setItem(selfKey, '1') } catch { /* ignore */ }
                setSelfOpen(false)
              }}
              className="bg-[#F07F00] hover:bg-[#CC6C00] text-white font-semibold
                         px-6 py-2.5 rounded-lg shadow transition-colors"
            >
              Thank you! 🎉
            </button>
          </div>
        </div>
      )}
    </>
  )
}
