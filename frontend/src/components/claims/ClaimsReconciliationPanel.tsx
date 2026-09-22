'use client'

/**
 * Omni vs Graphite on the Claims Register — Bokani's reconciliation, built in
 * (bug 31883c46). Same date range as the register filter; both sides read at
 * one moment. Graphite unreachable shows as unavailable, never as zeros.
 */
import { useEffect, useState } from 'react'
import { getClaimsReconciliation } from '@/lib/api'
import type { ClaimsBridge, ClaimsReconciliation } from '@/lib/api'
import { CheckCircle2, AlertTriangle, ChevronDown, ChevronRight, Scale } from 'lucide-react'

const NAVY = '#0D1B2A'
const P = (s: string | undefined) => {
  const v = Number(s || 0)
  const t = Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return v < 0 ? `(${t})` : t
}
const when = (s: string | null) => s
  ? new Date(s).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Gaborone' })
  : '—'

function Bridge({ title, b, lessLabel }: { title: string; b: ClaimsBridge; lessLabel: string }) {
  const rows: [string, string, boolean?][] = [
    ['Total per Omni', b.omni_total, true],
    [`Less: claims in Omni only (${lessLabel})`, b.less_omni_only],
    ['Add: claims in Graphite only', b.add_graphite_only],
    ['Add: differences on matched claims', b.add_differences_on_matched],
    ['Expected Graphite', b.expected_graphite, true],
    ['Total per Graphite', b.actual_graphite, true],
  ]
  return (
    <div className="rounded-xl border border-[#E4E4E7] bg-white p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[#6B6B76] mb-3">{title}</div>
      <table className="w-full text-sm">
        <tbody>
          {rows.map(([l, v, strong]) => (
            <tr key={l} className={strong ? 'font-semibold text-[#0D1B2A]' : 'text-[#374151]'}>
              <td className="py-1 pr-3">{l}</td>
              <td className="py-1 text-right font-mono tabular-nums">{P(v)}</td>
            </tr>
          ))}
          <tr className="border-t border-[#E4E4E7]">
            <td className="pt-2 pr-3 font-semibold">Unexplained variance</td>
            <td className="pt-2 text-right font-mono font-semibold" style={{ color: b.ties ? '#047857' : '#B91C1C' }}>
              {b.ties ? '—' : P(b.unexplained_variance)}
            </td>
          </tr>
          <tr className="text-[#6B7280]">
            <td className="pt-1 pr-3 text-xs">Headline gap (Graphite less Omni)</td>
            <td className="pt-1 text-right font-mono text-xs">{P(b.headline_gap)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function Detail({ title, head, rows }: { title: string; head: string[]; rows: string[][] }) {
  const [open, setOpen] = useState(false)
  if (!rows.length) return null
  return (
    <div className="border-t border-[#E4E4E7]">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="w-full flex items-center gap-2 py-2.5 text-sm font-medium text-[#0D1B2A] hover:opacity-80">
        {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        {title} <span className="text-[#6B7280] font-normal">({rows.length})</span>
      </button>
      {open && (
        <table className="w-full text-sm mb-3">
          <thead><tr className="text-left text-xs text-[#6B7280]">
            {head.map((h, i) => <th key={h} className={`py-1 ${i ? 'text-right' : ''}`}>{h}</th>)}
          </tr></thead>
          <tbody>{rows.map(r => (
            <tr key={r[0]} className="border-t border-[#F4F4F5]">
              {r.map((c, i) => <td key={i} className={`py-1 ${i ? 'text-right font-mono tabular-nums' : 'font-mono'}`}>{c}</td>)}
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  )
}

export function ClaimsReconciliationPanel({ from, to }: { from?: string; to?: string }) {
  const [d, setD] = useState<ClaimsReconciliation | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setErr(null)
    getClaimsReconciliation(from, to)
      .then(r => { if (alive) setD(r) })
      .catch(e => { if (alive) setErr(e instanceof Error ? e.message : 'Could not load the reconciliation') })
    return () => { alive = false }
  }, [from, to])

  const c = d?.counts
  return (
    <section aria-labelledby="recon-h" className="rounded-2xl border border-[#E4E4E7] bg-[#FCFCFD] p-5 mb-5">
      <div className="flex items-center gap-3 mb-1">
        <Scale className="w-5 h-5" style={{ color: NAVY }} />
        <h2 id="recon-h" className="text-base font-bold text-[#0D1B2A]">Omni vs Graphite</h2>
        {d?.graphite_available && d.reserve_bridge && d.payment_bridge && (
          (d.reserve_bridge.ties && d.payment_bridge.ties)
            ? <span className="inline-flex items-center gap-1 text-xs font-medium text-[#047857]"><CheckCircle2 className="w-3.5 h-3.5" /> Both bridges tie</span>
            : <span className="inline-flex items-center gap-1 text-xs font-medium text-[#B91C1C]"><AlertTriangle className="w-3.5 h-3.5" /> Unexplained variance</span>
        )}
      </div>
      <p className="text-xs text-[#6B7280] mb-4">
        {d ? <>Claims registered {d.date_from} to {d.date_to}. Graphite read {when(d.as_of)}; Omni&rsquo;s copy last refreshed {when(d.omni_copy_synced_at)}.</> : 'Loading…'}
      </p>
      {err && <div className="text-sm text-[#B91C1C]">{err}</div>}
      {d && !d.graphite_available && (
        <div className="text-sm text-[#92400E] bg-[#FFFBEB] border border-[#FDE68A] rounded-lg p-3">
          Graphite unavailable — {d.note} Omni has {d.counts.omni} claim(s) in this range.
        </div>
      )}
      {d?.graphite_available && c && d.reserve_bridge && d.payment_bridge && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
            {([['In Omni', c.omni], ['In Graphite', c.graphite], ['Matched', c.matched],
               ['Omni only', c.omni_only], ['Graphite only', c.graphite_only],
               ['Reserve differs', c.matched_reserve_difference]] as [string, number | undefined][]).map(([l, v]) => (
              <div key={l} className="rounded-lg bg-white border border-[#E4E4E7] px-3 py-2">
                <div className="text-[11px] text-[#6B7280]">{l}</div>
                <div className="text-lg font-bold tabular-nums text-[#0D1B2A]">{(v ?? 0).toLocaleString()}</div>
              </div>
            ))}
          </div>
          <div className="grid md:grid-cols-2 gap-4 mb-3">
            <Bridge title="Reserve bridge — Omni to Graphite" b={d.reserve_bridge} lessLabel="not yet in Graphite" />
            <Bridge title="Payment bridge — Omni to Graphite" b={d.payment_bridge} lessLabel="not yet in Graphite" />
          </div>
          <Detail title="Reserve restated between systems" head={['Claim', 'Omni', 'Graphite', 'Graphite higher by']}
            rows={d.restated.map(r => [r.claim_number, P(r.omni), P(r.graphite), P(r.difference)])} />
          <Detail title="Payment differs between systems" head={['Claim', 'Omni', 'Graphite', 'Difference']}
            rows={d.payment_differences.map(r => [r.claim_number, P(r.omni), P(r.graphite), P(r.difference)])} />
          <Detail title="In Graphite only" head={['Claim', 'Reserve', 'Paid']}
            rows={d.graphite_only.map(r => [r.claim_number, P(r.reserve), P(r.paid)])} />
          <Detail title="In Omni only (latest reported)" head={['Claim', 'Reserve', 'Reported']}
            rows={d.omni_only.map(r => [r.claim_number, P(r.reserve), r.reported_date || '—'])} />
        </>
      )}
    </section>
  )
}
