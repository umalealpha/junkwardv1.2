'use client'

/**
 * Tool Adoption — who is actually USING the underwriting automation, and who is
 * still doing the job by hand.
 *
 * CFO Amendment 3, 2026-09-08: "measure who actually uses the underwriting
 * automation, show it, feed a per-underwriter AI readiness score, and chase the
 * low adopters every Monday." This screen is the "show it".
 *
 * Worst first, deliberately. A table sorted by name buries the person nobody has
 * noticed is still typing quotes into Word; sorting by readiness ascending puts
 * them at the top, which is why this screen exists. The two figures each score is
 * worked out from sit in the row NEXT TO the score — nobody is asked to act on a
 * number they cannot see the inputs for.
 *
 * Data: GET /api/v1/underwriting/quotes/adoption/ (see underwriting/adoption.py).
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { AlertTriangle, RefreshCw, TrendingUp } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

interface AdoptionRow {
  user_id: number
  name: string
  job_title: string
  is_manager: boolean
  quotes: number
  quotes_last_used: string | null
  documents: number
  documents_last_used: string | null
  ever_used: boolean
  renewals: number
  gap: number
  readiness: number | null
  readiness_band: string
}

interface AdoptionResponse {
  window: { start: string; end: string; days: number }
  team: {
    renewals_available: boolean
    renewals_note: string
    renewals_total: number
    renewals_unattributed: number
    quotes_total: number
    documents_total: number
    headcount: number
    never_used: number
    readiness_avg: number | null
    readiness_numerator: number
    readiness_denominator: number
    scored_headcount: number
  }
  rows: AdoptionRow[]
}

/** 1,200 — house format. Whole numbers only on this screen; no money here. */
function n(v: number | null | undefined): string {
  return Number(v || 0).toLocaleString('en-GB')
}

/** '12 Aug 2026', or the word that actually means something: never. */
function when(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso)
  return isNaN(d.getTime())
    ? 'never'
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

/** Traffic light on the band, not on the raw number — the band is the decision. */
function bandStyle(band: string): { bg: string; fg: string; bar: string } {
  switch (band) {
    case 'Using the tools': return { bg: '#ECFDF5', fg: '#047857', bar: '#059669' }
    case 'Partly manual':   return { bg: '#FFFBEB', fg: '#92400E', bar: ORANGE }
    case 'Still manual':    return { bg: '#FEF2F2', fg: '#B91C1C', bar: '#DC2626' }
    default:                return { bg: '#F3F4F6', fg: '#6B7280', bar: '#9CA3AF' }
  }
}

export default function UnderwritingAdoptionPage() {
  const router = useRouter()
  const [data, setData] = useState<AdoptionResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(7)

  const load = useCallback((d: number) => {
    setLoading(true)
    setError(null)
    apiFetch<AdoptionResponse>(`/underwriting/quotes/adoption/?days=${d}`)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : 'Could not load adoption figures.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load(days)
  }, [router, load, days])

  // Worst first. Anyone who cannot be scored (no renewals to measure against)
  // sits below the scored rows rather than pretending to be a perfect 100.
  const rows = useMemo(() => {
    if (!data) return []
    return [...data.rows].sort((a, b) => {
      if (a.readiness === null && b.readiness === null) return b.gap - a.gap
      if (a.readiness === null) return 1
      if (b.readiness === null) return -1
      return a.readiness - b.readiness || b.gap - a.gap
    })
  }, [data])

  const team = data?.team

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Tool Adoption"
              breadcrumbs={[{ label: 'Underwriting' }, { label: 'Tool Adoption' }]} />

      <div className="flex-1 p-4 md:p-6 space-y-6">
        {/* ── The headline: one score, its inputs, and the scope in words ── */}
        <Card className="overflow-hidden p-0">
          <div className="grid md:grid-cols-[minmax(0,17rem)_1fr]">
            {/* Left: the number, on navy so it reads as the masthead of the page */}
            <div className="p-6 flex flex-col justify-center" style={{ background: NAVY }}>
              <div className="text-[11px] uppercase tracking-[0.14em] text-white/60">
                Quote builder adoption
              </div>
              <div className="mt-2 flex items-end gap-2">
                <span className="text-5xl font-semibold leading-none tabular-nums"
                      style={{ color: ORANGE }}>
                  {team?.readiness_avg ?? '—'}
                </span>
                {team?.readiness_avg != null && (
                  <span className="text-lg text-white/45 leading-none pb-1">/ 100</span>
                )}
              </div>
              {/* The label states the fraction the number came from. It used to
                * say "averaged over the underwriters we can score", which was
                * both the wrong arithmetic and a sentence nobody could check. */}
              <p className="mt-3 text-xs leading-relaxed text-white/55">
                {team?.readiness_denominator
                  ? <>Of the {n(team.readiness_denominator)} renewals booked by the{' '}
                      {team.scored_headcount} underwriter{team.scored_headcount === 1 ? '' : 's'}{' '}
                      we can score, {n(team.readiness_numerator)} were built with the
                      Quote builder.</>
                  : <>Nobody has a renewal in this window yet, so there is nothing to score.</>}
              </p>
            </div>

            {/* Right: the inputs. Nobody should have to trust the number alone. */}
            <div className="p-5 md:p-6">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
                    Renewals, quotes and documents — last {team ? days : '—'} days
                  </h2>
                  <p className="mt-1 text-xs text-[#6B7280] max-w-prose">
                    Renewals are Domestic &amp; Commercial only, from Graphite. MIS and
                    Unicoin are out of scope. Quotes and documents are counted from the
                    rows the two tools write in Omni.
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  {[7, 30, 90].map((d) => (
                    <button
                      key={d}
                      onClick={() => setDays(d)}
                      aria-pressed={days === d}
                      className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors
                        focus:outline-none focus-visible:ring-2 focus-visible:ring-[#F4A623]
                        focus-visible:ring-offset-1 ${
                        days === d
                          ? 'bg-[#0D1B2A] text-white'
                          : 'text-[#6B7280] hover:bg-[#F3F4F6] hover:text-[#0D1B2A]'
                      }`}
                    >
                      {d}d
                    </button>
                  ))}
                  <button
                    onClick={() => load(days)}
                    title="Reload"
                    className="ml-1 p-1.5 rounded-md text-[#6B7280] transition-colors
                               hover:bg-[#F3F4F6] hover:text-[#0D1B2A] focus:outline-none
                               focus-visible:ring-2 focus-visible:ring-[#F4A623]"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                  </button>
                </div>
              </div>

              <dl className="mt-5 grid grid-cols-2 sm:grid-cols-4 gap-x-5 gap-y-4">
                <Stat label="Renewals"
                      value={team?.renewals_available ? n(team.renewals_total) : '—'} />
                <Stat label="Quotes built" value={n(team?.quotes_total)} />
                <Stat label="Documents issued" value={n(team?.documents_total)} />
                <Stat label="Never used a tool"
                      value={`${n(team?.never_used)} of ${n(team?.headcount)}`}
                      alert={!!team?.never_used} />
              </dl>
            </div>
          </div>
        </Card>

        {error && (
          <div className="rounded-lg border border-[#FECACA] bg-[#FEF2F2] px-4 py-3 text-sm text-[#B91C1C]">
            {error}
          </div>
        )}

        {/* Two things that must be said out loud rather than shown as a zero. */}
        {team && !team.renewals_available && (
          <Notice>
            {team.renewals_note} Tool usage below is exact; the renewal column and
            every readiness score are blank until Graphite can be read.
          </Notice>
        )}
        {team && team.renewals_available && team.renewals_unattributed > 0 && (
          <Notice>
            {n(team.renewals_unattributed)} of {n(team.renewals_total)} renewals could
            not be tied to a person in Graphite. They are counted in the team total
            only — never guessed onto an underwriter.
          </Notice>
        )}

        {/* ── The list management reads. Worst first. ── */}
        <Card>
          <CardContent className="p-0">
            <div className="flex items-center gap-2 px-4 py-3.5 border-b border-[#F1F3F7]">
              <TrendingUp className="w-4 h-4" style={{ color: ORANGE }} />
              <h2 className="text-sm font-semibold" style={{ color: NAVY }}>
                Underwriter by underwriter
              </h2>
              <span className="text-xs text-[#9CA3AF]">lowest readiness first</span>
            </div>

            {rows.length === 0 ? (
              <p className="px-4 py-8 text-sm text-[#9CA3AF]">
                {loading
                  ? 'Loading…'
                  : 'No Underwriting staff with an Omni login on the register. Check the department names in HR.'}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-wider text-[#6B7280] border-b border-[#E5E7EB]">
                      <th className="py-2.5 pl-4 pr-3 font-semibold">Underwriter</th>
                      <th className="py-2.5 pr-3 font-semibold text-right">Renewals</th>
                      <th className="py-2.5 pr-3 font-semibold text-right">Quotes built</th>
                      <th className="py-2.5 pr-3 font-semibold text-right">Done by hand</th>
                      <th className="py-2.5 pr-3 font-semibold text-right">Documents</th>
                      <th className="py-2.5 pr-3 font-semibold">Last used</th>
                      <th className="py-2.5 pr-4 font-semibold w-[13rem]">AI readiness</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#F1F3F7]">
                    {rows.map((r) => {
                      const s = bandStyle(r.readiness_band)
                      return (
                        <tr key={r.user_id} className="transition-colors hover:bg-[#FAFBFD]">
                          <td className="py-3 pl-4 pr-3">
                            <div className="font-medium" style={{ color: NAVY }}>{r.name}</div>
                            <div className="text-xs text-[#9CA3AF]">
                              {r.job_title || 'Underwriting'}
                            </div>
                          </td>
                          <td className="py-3 pr-3 text-right tabular-nums text-[#374151]">
                            {team?.renewals_available ? n(r.renewals) : '—'}
                          </td>
                          <td className="py-3 pr-3 text-right tabular-nums text-[#374151]">
                            {n(r.quotes)}
                          </td>
                          <td className="py-3 pr-3 text-right tabular-nums font-medium"
                              style={{ color: r.gap > 0 ? '#B91C1C' : '#9CA3AF' }}>
                            {team?.renewals_available ? n(r.gap) : '—'}
                          </td>
                          <td className="py-3 pr-3 text-right tabular-nums text-[#374151]">
                            {n(r.documents)}
                          </td>
                          <td className="py-3 pr-3 text-xs">
                            {r.ever_used ? (
                              <span className="text-[#6B7280]">
                                quote {when(r.quotes_last_used)}
                                <span className="mx-1 text-[#D1D5DB]">·</span>
                                doc {when(r.documents_last_used)}
                              </span>
                            ) : (
                              <span className="font-semibold text-[#B91C1C]">
                                never opened either tool
                              </span>
                            )}
                          </td>
                          <td className="py-3 pr-4">
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 flex-1 min-w-[3.5rem] rounded-full bg-[#EEF1F5] overflow-hidden">
                                <div className="h-full rounded-full transition-[width] duration-500"
                                     style={{ width: `${r.readiness ?? 0}%`, background: s.bar }} />
                              </div>
                              <span className="tabular-nums text-xs font-semibold w-8 text-right"
                                    style={{ color: s.fg }}>
                                {r.readiness ?? '—'}
                              </span>
                            </div>
                            <span className="mt-1.5 inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
                                  style={{ background: s.bg, color: s.fg }}>
                              {r.readiness_band}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* The formula, in the open. A score nobody can check is a score nobody
            acts on (CFO Amendment 3: keep it explainable, no black box). */}
        <p className="text-xs leading-relaxed text-[#6B7280] max-w-3xl">
          <b style={{ color: NAVY }}>How the score is worked out.</b>{' '}
          Readiness = the renewals that went through the Quote builder, divided by the
          renewals that person booked in the period, as a percentage out of 100, capped
          at 100. Renewals come from Graphite V2 (Domestic &amp; Commercial only);
          quotes and documents are counted from the records the tools already write in
          Omni, so the figures go back to the day each tool went live. An underwriter
          with no renewals in the period is shown as <i>not scored</i> rather than
          zero — nobody has failed to use a tool they had no work for. Each underwriter
          gets these same figures by email on a Monday morning.
        </p>
      </div>
    </div>
  )
}

function Stat({ label, value, alert = false }: {
  label: string; value: string; alert?: boolean
}) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-[0.12em] text-[#9CA3AF]">{label}</dt>
      <dd className="mt-0.5 text-xl font-semibold tabular-nums"
          style={{ color: alert ? '#B91C1C' : NAVY }}>
        {value}
      </dd>
    </div>
  )
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-2.5 rounded-lg border border-[#FDE68A] bg-[#FFFBEB] px-4 py-3">
      <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-[#B45309]" />
      <p className="text-xs leading-relaxed text-[#92400E]">{children}</p>
    </div>
  )
}
