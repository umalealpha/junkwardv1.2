/**
 * Same-timestamp claims bridge (CFO file 2 Medium, 18-Sep-2026): the Omni
 * mirror behind the claims cards next to live Graphite, read in one request.
 * The label says when Graphite was read AND how old Omni's copy is, so a gap
 * is never passed off as same-moment when the copy is stale.
 */
import { Card } from '@/components/ui/card'
import { CheckCircle2, AlertTriangle, Clock } from 'lucide-react'

export interface ClaimsBridgeData {
  as_of: string
  omni_mirror_synced_at: string | null
  omni: { claim_count: number; paid: string }
  graphite: { claim_count: number; paid: string } | null
  difference: { claim_count: number; paid: string } | null
  ties: { claim_count: boolean; paid: boolean } | null
  note: string
}

const when = (iso: string | null) => iso
  ? new Date(iso).toLocaleString('en-GB', { timeZone: 'Africa/Gaborone', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
  : '—'
const pula = (s: string) => `P${Number(s).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const n = (v: number) => v.toLocaleString('en-GB')

function Tie({ ok }: { ok: boolean | undefined }) {
  if (ok === undefined) return <span className="text-[#9CA3AF]">—</span>
  return ok
    ? <span className="inline-flex items-center gap-1 text-[#047857]"><CheckCircle2 className="w-3.5 h-3.5" aria-hidden /> Ties</span>
    : <span className="inline-flex items-center gap-1 text-[#B45309]"><AlertTriangle className="w-3.5 h-3.5" aria-hidden /> Gap</span>
}

export default function ClaimsBridge({ bridge }: { bridge: ClaimsBridgeData }) {
  const g = bridge.graphite, d = bridge.difference, t = bridge.ties
  const stale = !bridge.omni_mirror_synced_at
    || Date.parse(bridge.as_of) - Date.parse(bridge.omni_mirror_synced_at) > 24 * 3600 * 1000
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-[#0B0B3B]">Claims bridge — Omni vs Graphite</h2>
          <p className="text-xs text-[#6B7280] mt-0.5 flex items-center gap-1.5">
            <Clock className="w-3 h-3" aria-hidden /> Graphite read <strong>{when(bridge.as_of)}</strong>
            <span>· Omni copy as of <strong>{when(bridge.omni_mirror_synced_at)}</strong></span>
          </p>
        </div>
      </div>
      <div className="overflow-x-auto mt-3">
        <table className="w-full text-sm">
          <caption className="sr-only">Omni claims copy compared with live Graphite at the same moment</caption>
          <thead>
            <tr className="text-left text-xs text-[#6B7280]">
              <th scope="col" className="py-1 pr-4 font-medium"> </th>
              <th scope="col" className="py-1 pr-4 font-medium text-right">Omni copy</th>
              <th scope="col" className="py-1 pr-4 font-medium text-right">Graphite</th>
              <th scope="col" className="py-1 pr-4 font-medium text-right">Difference</th>
              <th scope="col" className="py-1 font-medium"> </th>
            </tr>
          </thead>
          <tbody className="text-[#0B0B3B] tabular-nums">
            <tr className="border-t border-[#F3F4F6]">
              <th scope="row" className="py-1.5 pr-4 text-left font-medium">Claims</th>
              <td className="py-1.5 pr-4 text-right">{n(bridge.omni.claim_count)}</td>
              <td className="py-1.5 pr-4 text-right">{g ? n(g.claim_count) : '—'}</td>
              <td className="py-1.5 pr-4 text-right">{d ? n(d.claim_count) : '—'}</td>
              <td className="py-1.5 text-xs"><Tie ok={t?.claim_count} /></td>
            </tr>
            <tr className="border-t border-[#F3F4F6]">
              <th scope="row" className="py-1.5 pr-4 text-left font-medium">Paid</th>
              <td className="py-1.5 pr-4 text-right">{pula(bridge.omni.paid)}</td>
              <td className="py-1.5 pr-4 text-right">{g ? pula(g.paid) : '—'}</td>
              <td className="py-1.5 pr-4 text-right">{d ? pula(d.paid) : '—'}</td>
              <td className="py-1.5 text-xs"><Tie ok={t?.paid} /></td>
            </tr>
          </tbody>
        </table>
      </div>
      {stale && <p className="text-xs text-[#B45309] mt-3">Omni&rsquo;s copy is more than a day older than the Graphite read, so part of any gap may simply be the copy being out of date.</p>}
      {bridge.note && <p className="text-xs text-[#B45309] mt-3">{bridge.note}</p>}
    </Card>
  )
}
