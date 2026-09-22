'use client'

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getPettyCashLocations,
  getPettyCashVouchers,
  type PettyCashLocation,
  type PettyCashVoucher,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Plus, Coins, AlertTriangle, CheckCircle2, Receipt, Clock,
  RefreshCw,
} from 'lucide-react'

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmtMoney(s: string | number | null | undefined): string {
  if (s === null || s === undefined || s === '') return '—'
  const n = typeof s === 'number' ? s : Number(s)
  if (!isFinite(n)) return String(s)
  return n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:            { bg: '#F3F4F6', fg: '#374151' },
  pending_approval: { bg: '#FFFBEB', fg: '#92400E' },
  one_signature:    { bg: '#FEF3C7', fg: '#B45309' },
  posted:           { bg: '#ECFDF5', fg: '#047857' },
  reimbursed:       { bg: '#EFF6FF', fg: '#1D4ED8' },
  rejected:         { bg: '#FEF2F2', fg: '#B91C1C' },
}

function StatusPill({ status, label }: { status: string; label: string }) {
  const c = STATUS_BADGE[status] || STATUS_BADGE.draft
  return (
    <span
      className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
      style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
    >
      {label}
    </span>
  )
}

// ─── Float status widget ────────────────────────────────────────────────────

function FloatStatusWidget({ loc }: { loc: PettyCashLocation }) {
  const float = Number(loc.float_amount)
  const cash = Number(loc.cash_on_hand)
  const unreim = Number(loc.total_unreimbursed)
  const pct = float > 0 ? Math.max(0, Math.min(100, (cash / float) * 100)) : 0
  const lowFloat = pct < 25

  return (
    <Card>
      <CardHeader>
        <div className="flex items-baseline justify-between">
          <CardTitle className="flex items-center gap-2">
            <Coins className="w-4 h-4 text-[#F07F00]" />
            {loc.name}
          </CardTitle>
          <span className="text-xs text-[#6B7280]">
            Float P{fmtMoney(loc.float_amount)}
          </span>
        </div>
        {loc.address && (
          <p className="text-xs text-[#9CA3AF] mt-1">{loc.address}</p>
        )}
        {loc.access_notice && (
          <div className="mt-3 flex items-start gap-2 p-3 rounded-md bg-[#FEF2F2] border-2 border-[#DC2626]">
            <AlertTriangle className="w-5 h-5 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-sm text-[#991B1B] font-medium leading-relaxed">
              {loc.access_notice.text}
            </p>
          </div>
        )}
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[#6B7280] mb-1">
              Cash on hand
            </p>
            <p
              className="text-2xl font-bold font-mono tabular-nums"
              style={{ color: lowFloat ? '#B91C1C' : '#0B0B3B' }}
            >
              BWP {fmtMoney(loc.cash_on_hand)}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[#6B7280] mb-1">
              Disbursed since last top-up
            </p>
            <p className="text-2xl font-bold font-mono tabular-nums text-[#374151]">
              BWP {fmtMoney(loc.total_unreimbursed)}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[#6B7280] mb-1">
              Available for new voucher
            </p>
            <p className="text-2xl font-bold font-mono tabular-nums text-[#0B0B3B]">
              BWP {fmtMoney(loc.available_for_voucher)}
            </p>
          </div>
        </div>

        {/* Bar */}
        <div className="w-full h-2 bg-[#F3F4F6] rounded-full overflow-hidden mb-1">
          <div
            className="h-2 rounded-full transition-all"
            style={{
              width: `${pct}%`,
              background: lowFloat ? '#DC2626' : '#10B981',
            }}
          />
        </div>
        <div className="flex justify-between text-[10px] text-[#9CA3AF]">
          <span>0</span>
          <span>BWP {fmtMoney(loc.float_amount)}</span>
        </div>

        {lowFloat && (
          <div className="mt-3 flex items-start gap-2 p-2.5 rounded bg-[#FEF2F2] border border-[#FEE2E2]">
            <AlertTriangle className="w-4 h-4 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <p className="text-xs text-[#B91C1C]">
              Float is below 25%. Run a reimbursement to top the tin back up.
            </p>
          </div>
        )}
        {!lowFloat && unreim > 0 && (
          <div className="mt-3 flex items-start gap-2 p-2.5 rounded bg-[#ECFDF5] border border-[#A7F3D0]">
            <CheckCircle2 className="w-4 h-4 text-[#059669] flex-shrink-0 mt-0.5" />
            <p className="text-xs text-[#047857]">
              Float healthy. Custodian: {loc.custodian_username || '—'}.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ─── Page ───────────────────────────────────────────────────────────────────

export default function PettyCashListPage() {
  const router = useRouter()
  const [locations, setLocations] = useState<PettyCashLocation[]>([])
  const [vouchers, setVouchers] = useState<PettyCashVoucher[]>([])
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [locsRes, vouchersRes] = await Promise.all([
        getPettyCashLocations(),
        getPettyCashVouchers({ status: statusFilter || undefined }),
      ])
      setLocations(locsRes.results)
      setVouchers(vouchersRes.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load petty cash')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Petty Cash Vouchers"
        breadcrumbs={[{ label: 'Petty Cash' }]}
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline" size="sm"
              leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              onClick={load} disabled={loading}
            >
              Refresh
            </Button>
            <Link href="/petty-cash/reimbursements">
              <Button variant="outline" size="sm" leftIcon={<Coins className="w-3.5 h-3.5" />}>
                Reimbursements
              </Button>
            </Link>
            <Link href="/petty-cash/new">
              <Button size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />}>
                New Voucher
              </Button>
            </Link>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {/* Float status widget per location */}
        {locations.map((loc) => (
          <FloatStatusWidget key={loc.id} loc={loc} />
        ))}

        {locations.length === 0 && !loading && (
          <Card>
            <CardContent className="p-6 text-center">
              <Coins className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
              <p className="text-sm text-[#6B7280] mb-2">
                No petty cash location set up yet.
              </p>
              <p className="text-xs text-[#9CA3AF]">
                Run <code className="bg-[#F3F4F6] px-1 rounded">python manage.py setup_petty_cash</code>{' '}
                on the server, or create one in Django admin.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Filter strip */}
        <Card>
          <CardContent className="p-4 flex flex-wrap items-center gap-3">
            <span className="text-xs font-medium text-[#374151]">Status:</span>
            {[
              { value: '',                 label: 'All' },
              { value: 'draft',            label: 'Draft' },
              { value: 'pending_approval', label: 'Pending' },
              { value: 'one_signature',    label: '1 signature' },
              { value: 'posted',           label: 'Posted' },
              { value: 'reimbursed',       label: 'Reimbursed' },
              { value: 'rejected',         label: 'Rejected' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => setStatusFilter(opt.value)}
                className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                  statusFilter === opt.value
                    ? 'bg-[#0B0B3B] text-white border-[#0B0B3B]'
                    : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Voucher table */}
        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && vouchers.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Receipt className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No vouchers match the current filter.</p>
              </div>
            )}
            {!loading && vouchers.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Voucher #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Date</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Payee</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Account</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Amount</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Receipt?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vouchers.map((v) => (
                      <tr key={v.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                        <td className="px-4 py-2.5">
                          <Link
                            href={`/petty-cash/${v.id}`}
                            className="text-[#0B0B3B] font-mono text-xs hover:underline"
                          >
                            {v.voucher_number}
                          </Link>
                        </td>
                        <td className="px-4 py-2.5 text-[#374151]">{fmtDate(v.voucher_date)}</td>
                        <td className="px-4 py-2.5 text-[#374151]">{v.payee}</td>
                        <td className="px-4 py-2.5 text-[#6B7280] text-xs">
                          {v.expense_account_code} · {v.expense_account_name}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                          {fmtMoney(v.amount)}
                        </td>
                        <td className="px-4 py-2.5">
                          <StatusPill status={v.status} label={v.status_display} />
                        </td>
                        <td className="px-4 py-2.5">
                          {v.receipt_attached
                            ? <CheckCircle2 className="w-4 h-4 text-[#059669]" />
                            : <Clock className="w-4 h-4 text-[#9CA3AF]" />}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
