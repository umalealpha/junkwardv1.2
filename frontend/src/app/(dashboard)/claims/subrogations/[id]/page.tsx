'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter, useParams } from 'next/navigation'
import {
  getSubrogation, getSubrogationReceipts, createSubrogationReceipt, getToken,
} from '@/lib/api'
import type { Subrogation, SubrogationReceipt, ReceiptMethod, PrescriptionRisk } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Modal, ModalBody, ModalFooter } from '@/components/ui/modal'
import {
  ArrowLeft, AlertCircle, Plus, Save, Banknote, ShieldAlert, Clock, Scale,
} from 'lucide-react'
import { localYmd } from '@/lib/utils'

function fmt(v: string | number | null): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

const STATUS_STYLES: Record<string, string> = {
  pending:         'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
  partial:         'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]',
  fully_recovered: 'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  written_off:     'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
  in_litigation:   'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
}

// Prescription alarm — the CFO's idea 1. Red for lost/about-to-be-lost time.
const PRESCRIPTION_UI: Record<PrescriptionRisk, { label: string; cls: string; banner: string }> = {
  EXPIRED:  { label: 'Prescription EXPIRED', cls: 'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]', banner: 'bg-[#FEF2F2] border-[#FECACA] text-[#991B1B]' },
  CRITICAL: { label: 'Prescribes within 30 days', cls: 'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]', banner: 'bg-[#FEF2F2] border-[#FECACA] text-[#991B1B]' },
  URGENT:   { label: 'Prescribes within 90 days', cls: 'bg-[#FFF7ED] text-[#C2410C] border-[#FED7AA]', banner: 'bg-[#FFF7ED] border-[#FED7AA] text-[#9A3412]' },
  WATCH:    { label: 'Prescribes within a year', cls: 'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]', banner: 'bg-[#FFFBEB] border-[#FDE68A] text-[#92400E]' },
  SAFE:     { label: 'Within time', cls: 'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]', banner: '' },
  UNKNOWN:  { label: 'No date — cannot age', cls: 'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]', banner: 'bg-[#F3F4F6] border-[#E5E7EB] text-[#374151]' },
}

const inputCls = 'w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]'

export default function SubrogationDetailPage() {
  const router = useRouter()
  const params = useParams()
  const id = String(params.id)

  const [sub, setSub] = useState<Subrogation | null>(null)
  const [receipts, setReceipts] = useState<SubrogationReceipt[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [payOpen, setPayOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [s, r] = await Promise.all([getSubrogation(id), getSubrogationReceipts(id)])
      setSub(s); setReceipts(r.results)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Subrogation" breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Subrogations', href: '/claims/subrogations' }, { label: '…' }]} />
        <p className="px-6 py-8 text-sm text-[#6B7280]">Loading…</p>
      </div>
    )
  }
  if (error || !sub) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Subrogation" breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Subrogations', href: '/claims/subrogations' }]}
          actions={<Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/claims/subrogations')}>Back</Button>} />
        <div className="p-6"><div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error || 'Not found'}</p></div></div>
      </div>
    )
  }

  const p = sub.prescription
  const pui = PRESCRIPTION_UI[p.risk]

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={sub.claim_reference}
        breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Subrogations', href: '/claims/subrogations' }, { label: sub.claim_reference }]}
        actions={
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/claims/subrogations')}>Back</Button>
            <Button variant="accent" size="sm" leftIcon={<Banknote className="w-3.5 h-3.5" />} onClick={() => setPayOpen(true)}>Record a payment</Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4 max-w-5xl">
        {/* Prescription alarm — only when it needs attention */}
        {p.needs_attention && pui.banner && (
          <div className={`rounded-lg border p-3 flex items-center gap-2 ${pui.banner}`}>
            <ShieldAlert className="w-4 h-4 flex-shrink-0" />
            <p className="text-sm font-medium">
              {pui.label}
              {p.expires_on && ` — prescribes ${p.expires_on}`}
              {p.days_remaining !== null && !p.expired && ` (${p.days_remaining} days left)`}
              {p.risk === 'UNKNOWN' && ' — no date of loss or appointment recorded, so the three-year clock cannot be measured.'}
            </p>
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Kpi label="Total recoverable" value={`P ${fmt(sub.total_recoverable)}`} />
          <Kpi label="Recovered" value={`P ${fmt(sub.amount_recovered)}`} tone="green" />
          <Kpi label="Outstanding" value={`P ${fmt(sub.outstanding_balance)}`} tone={parseFloat(sub.outstanding_balance) > 0 ? 'amber' : 'grey'} />
          <Kpi label="Recovery rate" value={`${fmt(sub.recovery_pct)}%`} />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Case */}
          <Card>
            <CardHeader><CardTitle>Case</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Row k="Status"><span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${STATUS_STYLES[sub.status] || STATUS_STYLES.pending}`}>{sub.status_display}</span></Row>
              <Row k="Claim type">{sub.claim_type_display || '—'}</Row>
              <Row k="Third party">{sub.third_party_name}</Row>
              <Row k="Third-party insurer">{sub.third_party_insurer || '—'}</Row>
              <Row k="Date of loss">{sub.incident_date || '—'}</Row>
              <Row k="Graphite claim">{sub.graphite_id || '—'}</Row>
              {sub.notes && <div className="pt-2 border-t border-[#F3F4F6]"><p className="text-xs text-[#6B7280] whitespace-pre-wrap">{sub.notes}</p></div>}
            </CardContent>
          </Card>

          {/* Collection & ageing */}
          <Card>
            <CardHeader><CardTitle>Collection &amp; ageing</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Row k="Appointed to">{sub.appointed_to_name || <span className="text-[#B91C1C] font-medium">Nobody appointed</span>}</Row>
              <Row k="Date appointed">{sub.date_appointed || '—'}</Row>
              <Row k="Age"><span className="inline-flex items-center gap-1"><Clock className="w-3.5 h-3.5 text-[#9CA3AF]" />{sub.age_bucket_label}{sub.age_days !== null && ` (${sub.age_days} days)`}</span></Row>
              <Row k="Prescription"><span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium border ${pui.cls}`}><Scale className="w-3 h-3" />{pui.label}</span></Row>
            </CardContent>
          </Card>
        </div>

        {/* Cost build-up */}
        <Card>
          <CardHeader><CardTitle>Cost build-up</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-sm">
              <Row k="Assessor's fees">{fmt(sub.assessor_fees)}</Row>
              <Row k="Repair costs">{fmt(sub.repair_costs)}</Row>
              <Row k="Client's excess">{fmt(sub.client_excess)}</Row>
              <Row k="Towing fees">{fmt(sub.towing_fees)}</Row>
              <Row k="Legal fees">{fmt(sub.legal_fees)}</Row>
              <Row k="Less: salvage">{fmt(sub.salvage_amount)}</Row>
            </div>
            <div className="mt-3 pt-3 border-t border-[#E5E7EB] flex justify-between text-sm font-semibold">
              <span>Total recoverable</span><span className="tabular-nums">P {fmt(sub.total_recoverable)}</span>
            </div>
          </CardContent>
        </Card>

        {/* Receipts */}
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Payments received</CardTitle>
            <Button variant="outline" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => setPayOpen(true)}>Record a payment</Button>
          </CardHeader>
          <CardContent className="p-0">
            {receipts.length === 0 ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">No payments recorded yet.</p>
            ) : (
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Date</th>
                    <th className="px-4 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Method</th>
                    <th className="px-4 py-2 text-left text-xs font-semibold text-[#374151] uppercase">Reference</th>
                    <th className="px-4 py-2 text-right text-xs font-semibold text-[#374151] uppercase">Amount</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB]">
                  {receipts.map(r => (
                    <tr key={r.id}>
                      <td className="px-4 py-2">{r.received_date}</td>
                      <td className="px-4 py-2">{r.method_display}</td>
                      <td className="px-4 py-2 text-[#6B7280]">{r.reference || '—'}</td>
                      <td className="px-4 py-2 text-right tabular-nums font-medium">{fmt(r.amount)}</td>
                    </tr>
                  ))}
                  <tr className="bg-[#F9FAFB] font-semibold">
                    <td className="px-4 py-2" colSpan={3}>Total recovered</td>
                    <td className="px-4 py-2 text-right tabular-nums">{fmt(sub.amount_recovered)}</td>
                  </tr>
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>

      <RecordPaymentModal open={payOpen} onClose={() => setPayOpen(false)} subrogationId={id} onSaved={() => { setPayOpen(false); load() }} />
    </div>
  )
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: 'green' | 'amber' | 'grey' }) {
  const color = tone === 'green' ? 'text-[#047857]' : tone === 'amber' ? 'text-[#C2410C]' : 'text-[#0B0B3B]'
  return (
    <div className="bg-white border border-[#E5E7EB] rounded-lg px-4 py-3">
      <p className="text-xs text-[#6B7280] uppercase tracking-wide">{label}</p>
      <p className={`text-lg font-semibold tabular-nums mt-1 ${color}`}>{value}</p>
    </div>
  )
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-[#6B7280]">{k}</span>
      <span className="text-[#111827] text-right tabular-nums">{children}</span>
    </div>
  )
}

function RecordPaymentModal({ open, onClose, subrogationId, onSaved }: { open: boolean; onClose: () => void; subrogationId: string; onSaved: () => void }) {
  const today = localYmd(new Date())
  const [amount, setAmount] = useState('')
  const [receivedDate, setReceivedDate] = useState(today)
  const [method, setMethod] = useState<ReceiptMethod>('eft')
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function save() {
    setBusy(true); setErr(null)
    try {
      await createSubrogationReceipt({
        subrogation: subrogationId, amount, received_date: receivedDate,
        method, reference, notes,
      })
      // reset for next time — including busy, or the still-mounted modal's
      // Save button stays disabled on the next open until a full page refresh.
      setAmount(''); setReference(''); setNotes(''); setMethod('eft'); setReceivedDate(today)
      setBusy(false)
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save the payment')
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onOpenChange={o => !o && onClose()} title="Record a payment" description="Money recovered against this subrogation." size="md">
      <ModalBody className="space-y-3">
        {err && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-md p-2 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{err}</p></div>}
        <label className="block"><span className="block text-xs font-medium text-[#374151] mb-1">Amount (BWP) *</span>
          <input type="number" step="0.01" min="0" inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} className={inputCls} placeholder="0.00" autoFocus /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block"><span className="block text-xs font-medium text-[#374151] mb-1">Date received *</span>
            <input type="date" value={receivedDate} onChange={e => setReceivedDate(e.target.value)} className={inputCls} /></label>
          <label className="block"><span className="block text-xs font-medium text-[#374151] mb-1">Method</span>
            <select value={method} onChange={e => setMethod(e.target.value as ReceiptMethod)} className={inputCls}>
              <option value="eft">EFT</option><option value="cash">Cash</option><option value="pos">Card / POS</option>
              <option value="realpay">RealPay collection</option><option value="other">Other</option>
            </select></label>
        </div>
        <label className="block"><span className="block text-xs font-medium text-[#374151] mb-1">Reference</span>
          <input value={reference} onChange={e => setReference(e.target.value)} className={inputCls} placeholder="Bank / receipt reference" /></label>
        <label className="block"><span className="block text-xs font-medium text-[#374151] mb-1">Notes</span>
          <textarea value={notes} onChange={e => setNotes(e.target.value)} className={inputCls + ' min-h-[60px]'} /></label>
      </ModalBody>
      <ModalFooter>
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="accent" size="sm" leftIcon={<Save className="w-3.5 h-3.5" />} onClick={save} disabled={busy || !amount || !receivedDate}>
          {busy ? 'Saving…' : 'Save payment'}
        </Button>
      </ModalFooter>
    </Modal>
  )
}
