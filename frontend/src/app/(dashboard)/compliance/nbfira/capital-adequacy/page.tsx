'use client'

/**
 * /compliance/nbfira/capital-adequacy — Solvency & Capital Adequacy.
 *
 * Reads /api/v1/regulatory/capital-check/ (already exists). Displays:
 *   - Available Capital
 *   - Minimum Capital Requirement (MCR) — BWP 5M for general insurance
 *   - Solvency Capital Requirement (SCR) — risk-based formula output
 *   - Coverage ratios + target / floor comparison
 *   - Component breakdown if the endpoint provides it
 *
 * Auditor uses this to certify the SCR ratio before the quarterly return
 * goes out.
 */

import { useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { formatAmount, parseAmount } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { Scale, AlertCircle, RefreshCw, ShieldCheck } from 'lucide-react'
import { MIN_CAPITAL_BWP, TARGET_SCR_RATIO, FLOOR_SCR_RATIO } from '../_lib'

export default function CapitalAdequacyPage() {
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (a: string | number) => formatAmount(a, currency, mode)

  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true); setError(null)
    try {
      const r = await apiFetch<any>(`/regulatory/capital-check/${companyId ? `?company=${companyId}` : ''}`)
      setData(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() /* eslint-disable-line react-hooks/exhaustive-deps */ }, [companyId])

  // The server withholds the ratio while any parameter is an unadopted
  // placeholder. Honour that: this page used to divide available ÷ required
  // itself, so it published 612% without ever reading the server's own ratio —
  // and it would start again the moment a requirement came back.
  const underRevision = data?.status === 'under_revision'
  const available = data?.available_capital ?? data?.eligible_own_funds ?? null
  // Under revision the adopted requirement is null, but reg 4 still computes
  // one — show it, labelled indicative. The ratio stays withheld either way.
  const scr       = underRevision
    ? (data?.indicative_required_capital ?? null)
    : (data?.required_capital ?? data?.scr ?? null)
  const mcr       = data?.mcr               ?? MIN_CAPITAL_BWP
  const ratio     = underRevision
    ? null
    : (data?.capital_adequacy_ratio != null ? Number(data.capital_adequacy_ratio)
       : data?.ratio ?? data?.solvency_ratio
         ?? (available && scr ? available / scr : null))
  const indicativeCar = underRevision ? (data?.indicative_car_percent ?? null) : null
  const c = data?.components ?? {}
  const riShareBasis: any[] = Array.isArray(c.reinsurance_share_basis)
    ? c.reinsurance_share_basis : []
  const ratioColour =
    ratio == null         ? 'text-[#6B7280]' :
    ratio >= TARGET_SCR_RATIO ? 'text-[#047857]' :
    ratio >= FLOOR_SCR_RATIO  ? 'text-[#92400E]' :
                                'text-[#B91C1C]'

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Capital Adequacy / Solvency"
        subtitle="Insurance Industry Regulations 2019 (S.I. 68 of 2019), reg 4 (capital target) + reg 5 (capital base)"
        breadcrumbs={[{ label: 'Compliance' }, { label: 'NBFIRA' }, { label: 'Capital Adequacy' }]}
        actions={
          <Button variant="secondary" size="sm" onClick={load} loading={loading}
                  leftIcon={<RefreshCw className="w-3.5 h-3.5" />}>
            Recalculate
          </Button>
        }
      />
      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {underRevision && (
          <div className="rounded-lg border-2 border-amber-500 bg-amber-50 p-4">
            <p className="text-base font-bold text-amber-900">
              Under revision — not for regulatory or board use
            </p>
            <p className="mt-1 text-sm text-amber-900">{data.status_note}</p>
            {Array.isArray(data.unverified_parameters) && data.unverified_parameters.length > 0 && (
              <p className="mt-2 text-xs text-amber-800">
                Waiting on an effective date for: {data.unverified_parameters.join(', ')}
              </p>
            )}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Card>
            <CardContent className="p-5">
              <div className="text-xs uppercase tracking-wider text-[#6B7280]">Available Capital</div>
              <div className="text-2xl font-semibold text-[#0D1B2A] mt-1 font-mono-nums">
                {loading ? '...' : available != null ? fmt(available) : '—'}
              </div>
              <div className="text-xs text-[#6B7280] mt-1">
                Own funds — equity less intangibles. The reinsurers&apos; share of
                technical provisions is excluded: it is not an allowed asset
                under Schedule 1 (workings below).
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <div className="text-xs uppercase tracking-wider text-[#6B7280]">
                Minimum capital target{underRevision && scr != null ? ' (indicative)' : ''}
              </div>
              <div className="text-2xl font-semibold text-[#0D1B2A] mt-1 font-mono-nums">
                {loading ? '...' : scr != null ? fmt(scr) : '—'}
              </div>
              <div className="text-xs text-[#6B7280] mt-1">
                Reg 4 — higher of P5,000,000 or 25% of next year&apos;s estimated opex
                {c.opex_basis_amount && (
                  <> · 25% × {fmt(c.opex_basis_amount)} opex = {fmt(c.opex_required)}</>
                )}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <div className="text-xs uppercase tracking-wider text-[#6B7280]">Statutory floor</div>
              <div className="text-2xl font-semibold text-[#0D1B2A] mt-1 font-mono-nums">
                {fmt(mcr)}
              </div>
              <div className="text-xs text-[#6B7280] mt-1">
                Reg 4(a) · general insurance · {selectedCompany?.base_currency || 'BWP'}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardContent className="p-5">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs uppercase tracking-wider text-[#6B7280]">Solvency ratio</div>
                <div className={`text-4xl font-bold mt-1 ${ratioColour}`}>
                  {ratio != null ? (ratio * 100).toFixed(1) + '%' : '—'}
                </div>
                <div className="text-xs text-[#6B7280] mt-1">
                  {underRevision
                    ? (indicativeCar
                        ? `Withheld until the parameters are adopted. On the reg 4 workings below it computes to ${Number(indicativeCar).toFixed(1)}% — indicative only, do not sign or submit it.`
                        : 'Withheld until the parameters are adopted')
                    : `Floor ${Math.round(FLOOR_SCR_RATIO * 100)}% · internal management target ${Math.round(TARGET_SCR_RATIO * 100)}% (not a figure in the Regulations)`}
                </div>
              </div>
              <Scale className="w-10 h-10 text-[#F4A623]" />
            </div>

            {/* Bar */}
            <div className="mt-5">
              <div className="h-3 rounded-full bg-[#F3F4F6] overflow-hidden">
                <div className="h-full"
                     style={{
                       width: `${Math.min(100, Math.max(0, (ratio || 0) / (TARGET_SCR_RATIO * 1.5) * 100))}%`,
                       background:
                         ratio == null         ? '#D1D5DB' :
                         ratio >= TARGET_SCR_RATIO ? '#047857' :
                         ratio >= FLOOR_SCR_RATIO  ? '#F4A623' :
                                                    '#DC2626',
                     }} />
              </div>
              <div className="mt-2 flex justify-between text-[10px] text-[#9CA3AF]">
                <span>0%</span>
                <span>{Math.round(FLOOR_SCR_RATIO * 100)}% floor</span>
                <span>{Math.round(TARGET_SCR_RATIO * 100)}% target</span>
                {/* the bar spans target x 1.5, so the right-hand tick is that,
                    as a percentage — it read 225% only because a ratio was
                    multiplied by 150 instead of by 100 */}
                <span>{Math.round(TARGET_SCR_RATIO * 1.5 * 100)}%+</span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* The add-back workings. The endpoint has always returned these; this
            page asked for components as an ARRAY and got an object, so the one
            figure most in doubt — a 14.15m add-back that is entirely
            reinsurance share — was computed and then shown to nobody. */}
        {c.opex_basis_amount && (
          <Card>
            <CardContent className="p-5">
              <div className="text-sm font-semibold text-[#0D1B2A]">
                How the capital target is built — Regulation 4
              </div>
              <p className="text-xs text-[#6B7280] mt-1 mb-3">{c.opex_basis_note}</p>
              <table className="w-full text-sm">
                <tbody>
                  <tr className="border-b border-[#F3F4F6]">
                    <td className="py-2 px-3 text-[#374151]">
                      Operating expenses, basis for the estimate
                      {c.opex_basis_period && (
                        <span className="text-[#9CA3AF]"> · {c.opex_basis_period}</span>
                      )}
                    </td>
                    <td className="py-2 px-3 text-right font-mono-nums">{fmt(c.opex_basis_amount)}</td>
                  </tr>
                  <tr className="border-b border-[#F3F4F6]">
                    <td className="py-2 px-3 text-[#374151]">
                      × {(Number(c.opex_factor) * 100).toFixed(0)}% (reg 4(b))
                    </td>
                    <td className="py-2 px-3 text-right font-mono-nums">{fmt(c.opex_required)}</td>
                  </tr>
                  <tr className="border-b border-[#F3F4F6]">
                    <td className="py-2 px-3 text-[#374151]">Statutory floor (reg 4(a))</td>
                    <td className="py-2 px-3 text-right font-mono-nums">{fmt(c.minimum_floor)}</td>
                  </tr>
                  <tr className="font-semibold">
                    <td className="py-2 px-3">
                      Capital target — the higher of the two
                      {c.binding_constraint && (
                        <span className="text-[#9CA3AF] font-normal">
                          {' '}· binding: {c.binding_constraint === 'opex' ? '25% of opex' : 'the P5m floor'}
                        </span>
                      )}
                    </td>
                    <td className="py-2 px-3 text-right font-mono-nums">{scr != null ? fmt(scr) : '—'}</td>
                  </tr>
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}

        {riShareBasis.length > 0 && (
          <Card>
            <CardContent className="p-5">
              <div className="text-sm font-semibold text-[#0D1B2A]">
                Excluded from own funds — reinsurers&apos; share of technical provisions
              </div>
              <p className="text-xs text-[#6B7280] mt-1 mb-3">{c.reinsurance_share_note}</p>
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-[#6B7280] border-b border-[#E5E7EB]">
                  <tr>
                    <th className="py-2 px-3">Account</th>
                    <th className="py-2 px-3 text-right">Net balance</th>
                    <th className="py-2 px-3 text-right">Excluded from capital</th>
                  </tr>
                </thead>
                <tbody>
                  {riShareBasis.map((r: any) => (
                    <tr key={r.account_code} className="border-b border-[#F3F4F6]">
                      <td className="py-2 px-3 font-mono text-[#374151]">{r.account_code}</td>
                      <td className="py-2 px-3 text-right font-mono-nums">{fmt(r.net)}</td>
                      <td className="py-2 px-3 text-right font-mono-nums">{fmt(r.added)}</td>
                    </tr>
                  ))}
                  <tr className="font-semibold">
                    <td className="py-2 px-3">Total excluded</td>
                    <td className="py-2 px-3" />
                    <td className="py-2 px-3 text-right font-mono-nums">
                      {fmt(c.reinsurance_share_excluded ?? 0)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}

        {/* Legacy shape — kept for when the endpoint returns risk modules */}
        {data && Array.isArray(data?.components) && data.components.length > 0 && (
          <Card>
            <CardContent className="p-5">
              <div className="text-sm font-semibold text-[#0D1B2A] mb-3">SCR components</div>
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-[#6B7280] border-b border-[#E5E7EB]">
                  <tr>
                    <th className="py-2 px-3">Risk module</th>
                    <th className="py-2 px-3 text-right">Charge</th>
                    <th className="py-2 px-3 text-right">% of SCR</th>
                  </tr>
                </thead>
                <tbody>
                  {data.components.map((c: any) => (
                    <tr key={c.label} className="border-b border-[#F3F4F6]">
                      <td className="py-2 px-3 text-[#374151]">{c.label}</td>
                      <td className="py-2 px-3 text-right font-mono-nums">{fmt(c.amount)}</td>
                      <td className="py-2 px-3 text-right font-mono-nums">
                        {scr ? ((parseAmount(c.amount) / scr) * 100).toFixed(1) + '%' : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}

        <div className="text-xs text-[#9CA3AF] flex items-start gap-1">
          <ShieldCheck className="w-3.5 h-3.5 mt-0.5" />
          The capital target is Regulation 4 of the Insurance Industry Regulations 2019 — the higher
          of P5,000,000 or 25% of the operating expenses estimated for the following year. There is
          no premium factor and no claims factor in Botswana insurance law; the earlier version of
          this page applied both, and the resulting ratio has been withdrawn. Auditor: do not sign a
          quarterly return off this page until the parameters carry an effective date.
        </div>
      </div>
    </div>
  )
}
