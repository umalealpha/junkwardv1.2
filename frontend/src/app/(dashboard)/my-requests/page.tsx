'use client'

/**
 * /my-requests — the requester-side tracker (CFO directive 2026-08-04).
 * One place showing everything the signed-in user has SUBMITTED across Omni —
 * leave, leave pay, staff loan, petty cash, payment request — with the current
 * status, whose desk it is on, how long it has waited, and a plain-English line.
 * The twin of /my-approvals (what awaits ME to sign). Data: /api/v1/my-requests/.
 */
import { useCallback, useEffect, useState, type ComponentType, type CSSProperties } from 'react'
import Link from 'next/link'
import { apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import {
  CalendarDays, Banknote, HandCoins, Wallet, Landmark,
  ArrowRight, CheckCircle2, XCircle, Clock, AlertTriangle, Loader2, Inbox,
} from 'lucide-react'

interface Step { label: string; done: boolean; at: string | null }
interface Req {
  kind: string; kind_label: string; id: string; title: string
  amount: number | null; currency: string
  bucket: string; status_label: string
  holder_label: string; next_label: string
  submitted_at: string | null; updated_at: string | null
  days_waiting: number | null; href: string
  timeline: Step[]; stuck: boolean; status_line: string
}
interface Payload { items: Req[]; active: number; stuck: number; total: number }

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

const KIND_ICON: Record<string, ComponentType<{ className?: string; style?: CSSProperties }>> = {
  leave: CalendarDays, leave_pay: Banknote, loan: HandCoins,
  petty_cash: Wallet, payment: Landmark,
}

const BUCKET_STYLE: Record<string, { label: string; cls: string }> = {
  pending:   { label: 'In progress', cls: 'bg-amber-100 text-amber-800' },
  approved:  { label: 'In progress', cls: 'bg-amber-100 text-amber-800' },
  paid:      { label: 'Done',        cls: 'bg-green-100 text-green-800' },
  rejected:  { label: 'Rejected',    cls: 'bg-red-100 text-red-700' },
  cancelled: { label: 'Cancelled',   cls: 'bg-gray-100 text-gray-600' },
  draft:     { label: 'Draft',       cls: 'bg-gray-100 text-gray-600' },
}

const money = (n: number | null, ccy: string) =>
  n == null ? '' : `${ccy || 'BWP'} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function Timeline({ steps }: { steps: Step[] }) {
  return (
    <div className="flex items-center gap-1 flex-wrap mt-3">
      {steps.map((s, i) => (
        <div key={i} className="flex items-center gap-1">
          <span
            className={`inline-flex items-center justify-center w-4 h-4 rounded-full text-[9px] ${
              s.done ? 'text-white' : 'bg-gray-200 text-gray-400'
            }`}
            style={s.done ? { background: NAVY } : undefined}
            title={s.at ? new Date(s.at).toLocaleDateString() : s.label}
          >
            {s.done ? '✓' : ''}
          </span>
          <span className={`text-[11px] ${s.done ? 'text-gray-700' : 'text-gray-400'}`}>{s.label}</span>
          {i < steps.length - 1 && <ArrowRight className="w-3 h-3 text-gray-300" />}
        </div>
      ))}
    </div>
  )
}

function RequestCard({ r }: { r: Req }) {
  const Icon = KIND_ICON[r.kind] ?? Inbox
  const bs = BUCKET_STYLE[r.bucket] ?? BUCKET_STYLE.pending
  const inFlight = r.bucket === 'pending' || r.bucket === 'approved'
  return (
    <Card className="mb-3">
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 shrink-0 rounded-lg p-2" style={{ background: `${NAVY}12` }}>
            <Icon className="w-5 h-5" style={{ color: NAVY }} />
          </span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: ORANGE }}>
                {r.kind_label}
              </span>
              <span className={`text-[11px] px-2 py-0.5 rounded-full ${bs.cls}`}>{bs.label}</span>
              {r.stuck && (
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-red-100 text-red-700 inline-flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3" /> Waiting a while
                </span>
              )}
            </div>
            <div className="font-medium text-gray-900 truncate mt-0.5">
              {r.title}{r.amount != null && <span className="text-gray-500 font-normal"> · {money(r.amount, r.currency)}</span>}
            </div>
            <p className="text-sm text-gray-600 mt-1">{r.status_line}</p>

            <div className="flex items-center gap-3 mt-2 flex-wrap text-[12px] text-gray-500">
              {inFlight && r.holder_label && (
                <span className="inline-flex items-center gap-1">
                  <Clock className="w-3.5 h-3.5" />
                  With {r.holder_label}{r.days_waiting != null && ` · ${r.days_waiting} day${r.days_waiting === 1 ? '' : 's'}`}
                </span>
              )}
              {r.bucket === 'paid' && <span className="inline-flex items-center gap-1 text-green-700"><CheckCircle2 className="w-3.5 h-3.5" /> Completed</span>}
              {r.bucket === 'rejected' && <span className="inline-flex items-center gap-1 text-red-600"><XCircle className="w-3.5 h-3.5" /> Not approved</span>}
              <Link href={r.href} className="inline-flex items-center gap-1 hover:underline" style={{ color: NAVY }}>
                Open <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>

            <Timeline steps={r.timeline} />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default function MyRequestsPage() {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const d = await apiFetch<Payload>('/my-requests/')
      setData(d)
    } catch {
      setErr('Could not load your requests — refresh to retry.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const items = data?.items ?? []
  const active = items.filter(r => r.bucket === 'pending' || r.bucket === 'approved')
  const done = items.filter(r => !(r.bucket === 'pending' || r.bucket === 'approved'))

  return (
    <div>
      <TopBar title="My Requests" />
      <div className="max-w-3xl mx-auto p-4">
        {data && (
          <div className="mb-4 flex items-center gap-3 text-sm">
            <span className="px-3 py-1 rounded-full text-white" style={{ background: NAVY }}>
              {data.active} in progress
            </span>
            {data.stuck > 0 && (
              <span className="px-3 py-1 rounded-full bg-red-100 text-red-700 inline-flex items-center gap-1">
                <AlertTriangle className="w-4 h-4" /> {data.stuck} waiting a while
              </span>
            )}
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 text-gray-500 py-10 justify-center">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading your requests…
          </div>
        )}
        {err && !loading && <div className="text-red-600 text-sm py-6">{err}</div>}

        {!loading && !err && items.length === 0 && (
          <div className="text-center text-gray-500 py-12">
            <Inbox className="w-8 h-8 mx-auto mb-2 text-gray-300" />
            You haven’t submitted any requests yet.
          </div>
        )}

        {active.length > 0 && (
          <>
            <h2 className="text-sm font-semibold text-gray-700 mb-2">In progress</h2>
            {active.map(r => <RequestCard key={`${r.kind}-${r.id}`} r={r} />)}
          </>
        )}
        {done.length > 0 && (
          <>
            <h2 className="text-sm font-semibold text-gray-700 mb-2 mt-6">History</h2>
            {done.map(r => <RequestCard key={`${r.kind}-${r.id}`} r={r} />)}
          </>
        )}
      </div>
    </div>
  )
}
