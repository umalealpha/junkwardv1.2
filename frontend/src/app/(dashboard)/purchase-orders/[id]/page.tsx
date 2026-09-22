'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter, useParams } from 'next/navigation'
import Link from 'next/link'
import {
  getPurchaseOrder, submitPO, fmApprovePO, cfoApprovePO, rejectPO, cancelPO,
  createGRN, postGRN, getInvoices, createPOBillMatch, getToken,
  downloadPurchaseOrderPdf, emailPurchaseOrder, getGRNs,
} from '@/lib/api'
import type { PurchaseOrderDetail, Invoice, CreateGRNLineInput, GRNListItem } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import PurchaseOrderHistory from '@/components/procurement/PurchaseOrderHistory'
import PurchaseOrderBillCheck from '@/components/procurement/PurchaseOrderBillCheck'
import {
  ArrowLeft, AlertCircle, CheckCircle2, FileText, Send, X, Truck, Link2,
  Printer, Mail, Pencil,
} from 'lucide-react'
import { localYmd } from '@/lib/utils'

const fmt = (v: string | number | null | undefined): string => {
  if (v === null || v === undefined) return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

export default function PurchaseOrderDetailPage() {
  const router = useRouter()
  const params = useParams()
  const id = params?.id as string

  const [po, setPo]               = useState<PurchaseOrderDetail | null>(null)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [info, setInfo]           = useState<string | null>(null)
  const [acting, setActing]       = useState(false)

  const [showGRN, setShowGRN]     = useState(false)
  const [showMatch, setShowMatch] = useState(false)
  const [showReject, setShowReject] = useState(false)
  const [showCancel, setShowCancel] = useState(false)

  // One-click "email PO to supplier" (CFO directive 2026-07-07)
  const [showEmail, setShowEmail] = useState(false)
  const [emailTo, setEmailTo]     = useState('')
  const [emailCc, setEmailCc]     = useState('')
  const [emailNote, setEmailNote] = useState('')
  const [sendingEmail, setSendingEmail] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getPurchaseOrder(id)
      setPo(data)
      // Prefill the supplier's email on file (F3) — never clobber a typed one.
      setEmailTo((prev) => (prev.trim() ? prev : data.supplier_email || ''))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const action = async (fn: () => Promise<unknown>, successMsg: string) => {
    setError(null); setInfo(null); setActing(true)
    try {
      await fn()
      setInfo(successMsg)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setActing(false)
    }
  }

  if (loading || !po) {
    return (
      <div className="min-h-screen bg-[#F8F9FA]">
        <TopBar title="Purchase Order" subtitle="..." />
        <div className="p-8 text-gray-500">Loading...</div>
      </div>
    )
  }

  // CFO/Kao 2026-07-08: pre-approval the document is an RFQ; it becomes a
  // Purchase Order only once approved.
  const isApprovedDoc = ['approved', 'partially_received', 'fully_received', 'closed'].includes(po.status)
  const docType = isApprovedDoc ? 'Purchase Order' : 'RFQ (Request for Quotation)'

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title={`${docType} · ${po.po_number}`} subtitle={`${po.supplier_name} — ${po.status_display}`} />

      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        <Link href="/purchase-orders" className="text-sm text-gray-600 hover:text-gray-800 inline-flex items-center gap-1">
          <ArrowLeft className="w-4 h-4" /> Back to list
        </Link>

        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}
        {info && (
          <Card className="border-emerald-200 bg-emerald-50">
            <CardContent className="p-3 flex items-start gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-700 mt-0.5" />
              <span className="text-sm text-emerald-700">{info}</span>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-6">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <Stat label="Document" value={docType} />
              <Stat label="Status" value={po.status_display} />
              <Stat label="Department" value={po.department_display} />
              <Stat label="Issue date" value={po.issue_date} />
              <Stat label="Expected delivery" value={po.expected_delivery_date || '—'} />

              <Stat label="Supplier" value={po.supplier_name} />
              <Stat label="Currency" value={po.currency_code} />
              <Stat label={`Total (${po.currency_code})`} value={`${po.currency_code} ${fmt(po.total_amount)}`} />
              <Stat label="Total (BWP)" value={`BWP ${fmt(po.total_bwp)}`} />

              <Stat label="Submitted by" value={po.submitted_by_username || '—'} />
              <Stat label="FM-approved by" value={po.fm_approved_by_username || '—'} />
              <Stat label="CFO-approved by" value={po.cfo_approved_by_username || '—'} />
              <Stat label="Period" value={po.fiscal_period_name || '—'} />
            </div>

            {po.last_emailed_at && (
              <div className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-full px-3 py-1">
                <CheckCircle2 className="w-3.5 h-3.5" />
                Emailed to {po.last_emailed_to} · {new Date(po.last_emailed_at).toLocaleDateString('en-GB')}
              </div>
            )}

            {po.justification && (
              <div className="mt-4 border-t pt-3">
                <div className="text-xs uppercase text-gray-600 mb-1">Justification</div>
                <p className="text-sm text-gray-800">{po.justification}</p>
              </div>
            )}

            {po.related_claim_reference && (
              <div className="mt-3 text-sm">
                <span className="text-xs uppercase text-gray-600 mr-2">Related claim:</span>
                <span className="font-mono">{po.related_claim_reference}</span>
              </div>
            )}

            {po.rejection_reason && (
              <div className="mt-3 text-sm text-red-700">Rejection reason: {po.rejection_reason}</div>
            )}
            {po.cancellation_reason && (
              <div className="mt-3 text-sm text-zinc-700">Cancellation reason: {po.cancellation_reason}</div>
            )}
          </CardContent>
        </Card>

        {/* Action bar */}
        <div className="flex flex-wrap gap-2">
          {/* CFO directive 2026-05-21 (Purchase order Omni.docx): every PO
              must be printable so it can be emailed to suppliers. PDF
              button visible on every status (draft → cancelled). */}
          <Button
            variant="outline"
            onClick={() => downloadPurchaseOrderPdf(po.id, po.po_number).catch((e) => {
              setError(e instanceof Error ? e.message : 'PDF download failed')
            })}
            disabled={acting}
          >
            <Printer className="w-4 h-4 mr-1" /> Download PDF
          </Button>
          {po.status !== 'cancelled' && (
            <Button
              variant="outline"
              onClick={() => setShowEmail((v) => !v)}
              disabled={acting || sendingEmail}
            >
              <Mail className="w-4 h-4 mr-1" /> Email to supplier
            </Button>
          )}
          {['draft', 'pending_fm_approval', 'pending_cfo_approval'].includes(po.status) && (
            <Button variant="outline"
              onClick={() => router.push(`/purchase-orders/new?edit=${po.id}`)}
              disabled={acting}>
              <Pencil className="w-4 h-4 mr-1" /> Edit / Amend
            </Button>
          )}
          {po.status === 'draft' && (
            <Button onClick={() => action(() => submitPO(po.id), 'Submitted for FM approval')} disabled={acting}>
              <Send className="w-4 h-4 mr-1" /> Submit for approval
            </Button>
          )}
          {po.status === 'pending_fm_approval' && (
            <>
              {/* Approve only shows to a user the server says may approve this
                  leg — includes segregation of duties, so the PO's raiser does
                  NOT see a button that would only 400. Reject has no SoD bar. */}
              {po.can_approve && (
                <Button
                  onClick={() => action(
                    () => fmApprovePO(po.id),
                    po.department === 'claims' ? 'Claims-Manager approved' : 'FM-approved',
                  )}
                  disabled={acting}
                  className="bg-emerald-700 hover:bg-emerald-800 text-white">
                  <CheckCircle2 className="w-4 h-4 mr-1" />
                  {po.department === 'claims' ? 'Claims Manager Approve' : 'FM Approve'}
                </Button>
              )}
              <Button variant="outline" onClick={() => setShowReject(true)} disabled={acting}>
                <X className="w-4 h-4 mr-1" /> Reject
              </Button>
            </>
          )}
          {po.status === 'pending_cfo_approval' && (
            <>
              {po.can_approve && (
                <Button onClick={() => action(() => cfoApprovePO(po.id), 'CFO-approved — PO is now committed')} disabled={acting}
                  className="bg-[#F4A623] hover:bg-[#d99320] text-white">
                  <CheckCircle2 className="w-4 h-4 mr-1" /> CFO Approve
                </Button>
              )}
              <Button variant="outline" onClick={() => setShowReject(true)} disabled={acting}>
                <X className="w-4 h-4 mr-1" /> Reject
              </Button>
            </>
          )}
          {(po.status === 'approved' || po.status === 'partially_received') && (
            <>
              <Button onClick={() => setShowGRN(true)} disabled={acting} className="bg-blue-700 hover:bg-blue-800 text-white">
                <Truck className="w-4 h-4 mr-1" /> Receive goods (GRN)
              </Button>
              {/* Add extras after approval (Kao 2026-07-08): approved POs stay
                  locked as a control — "Revise" copies this PO into a NEW draft
                  you can edit + re-approve, and cancel the old one if it replaces it. */}
              <Button variant="outline"
                onClick={() => router.push(`/purchase-orders/new?copy=${po.id}`)}
                disabled={acting}>
                <Pencil className="w-4 h-4 mr-1" /> Revise (add extras)
              </Button>
              {/* Cancelling an approved PO reverses its commitment — CFO-only
                  (backend services.cancel). Only show the button to a user who
                  can actually cancel, so it never appears-then-fails for e.g.
                  a claims associate. (Kao "Cancel shows but can't cancel".) */}
              {po.can_cancel && (
                <Button variant="outline" onClick={() => setShowCancel(true)} disabled={acting}>
                  <X className="w-4 h-4 mr-1" /> Cancel PO
                </Button>
              )}
            </>
          )}
          {(po.status === 'partially_received' || po.status === 'fully_received') && (
            <Button onClick={() => setShowMatch(true)} disabled={acting} variant="outline">
              <Link2 className="w-4 h-4 mr-1" /> Match bill
            </Button>
          )}
        </div>

        {/* Email PO to supplier — inline form (CFO directive 2026-07-07) */}
        {showEmail && (
          <Card className="border-[#0D1B2A]/20">
            <CardContent className="p-6">
              <h3 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-1">
                Email this PO to the supplier
              </h3>
              <p className="text-xs text-gray-500 mb-4">
                The PDF is attached automatically and the email is sent from
                <span className="font-medium"> your own mailbox</span>.
                {po.status === 'draft' && (
                  <span className="text-orange-600 font-medium"> Note: this PO is still DRAFT — it has not been approved.</span>
                )}
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor="email-to">
                    Supplier email *
                  </label>
                  <input
                    id="email-to" type="email" value={emailTo}
                    onChange={(e) => setEmailTo(e.target.value)}
                    placeholder="supplier@example.co.bw"
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor="email-cc">
                    CC (optional)
                  </label>
                  <input
                    id="email-cc" type="email" value={emailCc}
                    onChange={(e) => setEmailCc(e.target.value)}
                    placeholder="colleague@alphadirect.co.bw"
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor="email-note">
                    Extra note (optional — added to the email body)
                  </label>
                  <input
                    id="email-note" type="text" value={emailNote}
                    onChange={(e) => setEmailNote(e.target.value)}
                    placeholder="e.g. Please prioritise — vehicle is in the yard."
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  />
                </div>
              </div>
              <div className="flex gap-2 mt-4">
                <Button
                  className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white"
                  disabled={sendingEmail || !emailTo.trim()}
                  onClick={async () => {
                    setError(null); setInfo(null); setSendingEmail(true)
                    try {
                      const r = await emailPurchaseOrder(
                        po.id, emailTo.trim(), emailCc.trim(), emailNote.trim(),
                      )
                      setInfo(`PO ${r.po_number} emailed to ${r.to} (from ${r.from}).`)
                      setShowEmail(false)
                      setEmailTo(''); setEmailCc(''); setEmailNote('')
                      // Refresh so the "Emailed to …" badge (and the now-
                      // remembered supplier email) show immediately.
                      await load()
                    } catch (e) {
                      setError(e instanceof Error ? e.message : 'Email failed')
                    } finally {
                      setSendingEmail(false)
                    }
                  }}
                >
                  <Mail className="w-4 h-4 mr-1" />
                  {sendingEmail ? 'Sending…' : 'Send email'}
                </Button>
                <Button variant="outline" disabled={sendingEmail} onClick={() => setShowEmail(false)}>
                  Cancel
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Line items — redesigned finance table (CFO 2026-07-13) */}
        <Card className="overflow-hidden">
          <CardContent className="p-0">
            <div className="flex items-center justify-between px-5 pt-4 pb-3">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
                Line items
              </h3>
              <span className="text-[11px] text-gray-400">
                {po.lines.length} line{po.lines.length !== 1 ? 's' : ''}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" style={{ fontVariantNumeric: 'tabular-nums' }}>
                <thead>
                  <tr className="bg-[#0D1B2A] text-white/90 text-[10.5px] uppercase tracking-[0.10em]">
                    <th className="text-left font-semibold px-5 py-3">Description</th>
                    <th className="text-right font-semibold px-3 py-3">Qty</th>
                    <th className="text-right font-semibold px-3 py-3">Unit price</th>
                    <th className="text-right font-semibold px-5 py-3">Line total</th>
                    <th className="text-right font-semibold px-3 py-3 hidden sm:table-cell">Rec&rsquo;d</th>
                    <th className="text-right font-semibold px-5 py-3 hidden sm:table-cell">Outstanding</th>
                  </tr>
                </thead>
                <tbody>
                  {po.lines.map((ln, i) => {
                    const neg = Number(ln.line_total) < 0
                    return (
                      <tr key={ln.id}
                        className={`border-b border-gray-100 dark:border-white/5 transition-colors hover:bg-[#F4A623]/[0.06] ${i % 2 ? 'bg-gray-50/50 dark:bg-white/[0.015]' : ''}`}>
                        <td className="px-5 py-3 align-top">
                          <div className={`font-medium leading-snug ${neg ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-gray-100'}`}>
                            {ln.description}
                          </div>
                          {ln.account_code && (
                            <div className="text-[11px] text-gray-400 mt-0.5">{ln.account_code} · {ln.account_name}</div>
                          )}
                        </td>
                        <td className="px-3 py-3 text-right text-gray-500 dark:text-gray-400 align-top tabular-nums">{fmt(ln.quantity)}</td>
                        <td className="px-3 py-3 text-right text-gray-700 dark:text-gray-300 align-top tabular-nums">{fmt(ln.unit_price)}</td>
                        <td className={`px-5 py-3 text-right font-semibold align-top tabular-nums ${neg ? 'text-red-600 dark:text-red-400' : 'text-gray-900 dark:text-gray-100'}`}>{fmt(ln.line_total)}</td>
                        <td className="px-3 py-3 text-right text-gray-400 align-top tabular-nums hidden sm:table-cell">{fmt(ln.quantity_received)}</td>
                        <td className="px-5 py-3 text-right text-gray-400 align-top tabular-nums hidden sm:table-cell">{fmt(ln.quantity_outstanding)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Totals — right-aligned summary panel */}
            <div className="flex justify-end border-t border-gray-100 dark:border-white/10 px-5 py-4">
              <div className="w-full max-w-[280px] space-y-1.5 text-sm" style={{ fontVariantNumeric: 'tabular-nums' }}>
                <TotalRow label={`Subtotal (${po.currency_code})`}
                  value={`${po.currency_code} ${fmt(Number(po.discount_total) > 0 ? Number(po.subtotal) + Number(po.discount_total) : Number(po.subtotal))}`} />
                {Number(po.discount_total) > 0 && (
                  <TotalRow label={`Discount (${Number(po.discount_percent)}%)`}
                    value={`−${po.currency_code} ${fmt(po.discount_total)}`} tone="red" />
                )}
                <TotalRow label="Tax" value={`${po.currency_code} ${fmt(po.tax_total)}`} />
                <div className="flex items-center justify-between pt-2.5 mt-1 border-t border-gray-200 dark:border-white/15">
                  <span className="font-bold text-[#0D1B2A] dark:text-white">Total ({po.currency_code})</span>
                  <span className="font-bold text-base text-[#0D1B2A] dark:text-[#F4A623] tabular-nums">{po.currency_code} {fmt(po.total_amount)}</span>
                </div>
                {po.currency_code !== 'BWP' && (
                  <div className="flex items-center justify-between text-xs text-gray-400">
                    <span>Total in BWP</span>
                    <span className="tabular-nums">BWP {fmt(po.total_bwp)}</span>
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {po && <GRNList poId={po.id} onChanged={load} />}
        {po && <PurchaseOrderBillCheck poId={po.id} />}
        {po && <PurchaseOrderHistory poId={po.id} />}
      </div>

      {showReject && (
        <ReasonModal
          title="Reject PO"
          confirmLabel="Reject"
          onClose={() => setShowReject(false)}
          onConfirm={async (reason) => {
            await action(() => rejectPO(po.id, reason), 'PO rejected')
            setShowReject(false)
          }}
        />
      )}
      {showCancel && (
        <ReasonModal
          title="Cancel PO"
          warning="Only the CFO can cancel an approved PO. POs with received goods cannot be cancelled."
          confirmLabel="Cancel PO"
          onClose={() => setShowCancel(false)}
          onConfirm={async (reason) => {
            await action(() => cancelPO(po.id, reason), 'PO cancelled')
            setShowCancel(false)
          }}
        />
      )}
      {showGRN && (
        <GRNModal
          po={po}
          onClose={() => setShowGRN(false)}
          onCreated={async () => {
            setShowGRN(false)
            setInfo('Goods receipt posted')
            await load()
          }}
        />
      )}
      {showMatch && (
        <MatchBillModal
          po={po}
          onClose={() => setShowMatch(false)}
          onMatched={async () => {
            setShowMatch(false)
            setInfo('Bill matched to PO')
            await load()
          }}
        />
      )}
    </div>
  )
}

// ─── GRN list on the PO (view / open / print a goods-receipt note) ───────────
// The GRN existed only as a line in the history log with no way to open or print
// it, and a draft GRN could not be posted from anywhere (Omogomotsi, 2026-09-02).
function GRNList({ poId, onChanged }: { poId: string; onChanged: () => Promise<void> }) {
  const [grns, setGrns] = useState<GRNListItem[]>([])
  const [busy, setBusy] = useState('')
  const [err, setErr]   = useState<string | null>(null)

  const load = useCallback(() => {
    getGRNs({ purchase_order: poId })
      .then((r) => setGrns(r.results || [])).catch(() => setGrns([]))
  }, [poId])
  useEffect(() => { load() }, [load])

  const post = async (id: string) => {
    setErr(null); setBusy(id)
    try { await postGRN(id); load(); await onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not post GRN') }
    finally { setBusy('') }
  }

  if (grns.length === 0) return null
  return (
    <Card>
      <CardContent className="p-5">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">
          Goods receipt notes
        </h3>
        {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 mb-3">{err}</div>}
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {grns.map((g) => (
            <div key={g.id} className="flex items-center justify-between py-2 text-sm">
              <div className="flex items-center gap-3">
                <Link href={`/goods-receipt-notes/${g.id}`}
                  className="font-medium text-[#0D1B2A] dark:text-blue-300 underline">
                  {g.grn_number}
                </Link>
                <span className="text-gray-500">{g.receipt_date}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full ${
                  g.status === 'posted' ? 'bg-green-100 text-green-800'
                  : g.status === 'cancelled' ? 'bg-gray-200 text-gray-700'
                  : 'bg-amber-100 text-amber-800'}`}>
                  {g.status_display || g.status}
                </span>
              </div>
              <div className="flex items-center gap-3">
                {g.status === 'draft' && (
                  <Button variant="outline" onClick={() => post(g.id)} disabled={busy === g.id}
                    className="h-7 px-2 text-xs">
                    {busy === g.id ? 'Posting…' : 'Post'}
                  </Button>
                )}
                <Link href={`/goods-receipt-notes/${g.id}`}
                  className="text-[#0D1B2A] dark:text-blue-300 inline-flex items-center gap-1">
                  <Printer className="w-4 h-4" /> View / print
                </Link>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase text-gray-500">{label}</div>
      <div className="text-sm font-medium text-gray-800 dark:text-gray-100">{value}</div>
    </div>
  )
}

function TotalRow({ label, value, tone }: { label: string; value: string; tone?: 'red' }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-500 dark:text-gray-400">{label}</span>
      <span className={`tabular-nums ${tone === 'red' ? 'text-red-600 dark:text-red-400' : 'text-gray-700 dark:text-gray-200'}`}>{value}</span>
    </div>
  )
}

// ─── Reason modal (reject / cancel) ──────────────────────────────────────────

function ReasonModal({ title, warning, confirmLabel, onClose, onConfirm }: {
  title: string; warning?: string; confirmLabel: string;
  onClose: () => void; onConfirm: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy]     = useState(false)
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg max-w-md w-full p-6 space-y-3">
        <h3 className="text-lg font-medium">{title}</h3>
        {warning && <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">{warning}</div>}
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3}
          placeholder="Reason (required)"
          className="w-full p-2 border border-gray-300 rounded text-sm" />
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>Close</Button>
          <Button onClick={async () => { setBusy(true); await onConfirm(reason); setBusy(false) }}
            disabled={!reason.trim() || busy}
            className="bg-red-600 hover:bg-red-700 text-white">
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── GRN modal ──────────────────────────────────────────────────────────────

function GRNModal({ po, onClose, onCreated }: {
  po: PurchaseOrderDetail; onClose: () => void; onCreated: () => Promise<void>;
}) {
  const [receiptDate, setReceiptDate] = useState(localYmd(new Date()))
  const [deliveryRef, setDeliveryRef] = useState('')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<{ po_line: string; quantity_received: string; condition: 'good' | 'damaged' | 'rejected' }[]>(
    po.lines.map((ln) => ({
      po_line: ln.id,
      quantity_received: String(parseFloat(ln.quantity_outstanding) || 0),
      condition: 'good' as const,
    })),
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState<string | null>(null)

  const onSubmit = async () => {
    setErr(null); setBusy(true)
    try {
      const grn = await createGRN({
        purchase_order: po.id,
        receipt_date: receiptDate,
        delivery_note_reference: deliveryRef,
        received_by: po.created_by,
        notes,
        lines: lines.filter((l) => parseFloat(l.quantity_received) > 0) as CreateGRNLineInput[],
      })
      // Guard the exact failure Oprah hit: if the create response ever comes
      // back without an id again, fail loudly and usefully instead of posting
      // to .../undefined/post_grn/ and showing a bare "Not found."
      if (!grn?.id) {
        throw new Error(
          'The goods receipt was created but no reference came back, so it could ' +
          'not be posted. Open the GRN list on this PO and post it from there.',
        )
      }
      await postGRN(grn.id)
      await onCreated()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to receive')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg max-w-2xl w-full p-6 space-y-3">
        <h3 className="text-lg font-medium">Receive goods — Create GRN</h3>
        {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">{err}</div>}
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            <div className="text-xs text-gray-600 mb-1">Receipt date</div>
            <input type="date" value={receiptDate} onChange={(e) => setReceiptDate(e.target.value)}
              className="w-full p-2 border border-gray-300 rounded" />
          </label>
          <label className="text-sm">
            <div className="text-xs text-gray-600 mb-1">Delivery note ref</div>
            <input value={deliveryRef} onChange={(e) => setDeliveryRef(e.target.value)}
              placeholder="From supplier's delivery note"
              className="w-full p-2 border border-gray-300 rounded" />
          </label>
        </div>
        <div className="text-xs text-gray-600">Quantities to receive (cannot exceed outstanding):</div>
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-gray-600 border-b">
            <tr><th className="text-left py-1">Line</th><th className="text-right py-1">Outstanding</th><th className="text-right py-1">Receive now</th><th className="text-left py-1">Condition</th></tr>
          </thead>
          <tbody>
            {po.lines.map((ln, i) => (
              <tr key={ln.id} className="border-b border-gray-100">
                <td className="py-1.5">{ln.description}</td>
                <td className="py-1.5 text-right font-mono">{ln.quantity_outstanding}</td>
                <td className="py-1.5 text-right">
                  <input type="number" step="0.0001" value={lines[i]?.quantity_received ?? ''}
                    onChange={(e) => {
                      const copy = [...lines]; copy[i].quantity_received = e.target.value; setLines(copy)
                    }}
                    className="w-24 p-1 border border-gray-300 rounded text-right" />
                </td>
                <td className="py-1.5">
                  <select value={lines[i]?.condition ?? 'good'}
                    onChange={(e) => {
                      const copy = [...lines]; copy[i].condition = e.target.value as 'good' | 'damaged' | 'rejected'; setLines(copy)
                    }}
                    className="p-1 border border-gray-300 rounded">
                    <option value="good">Good</option>
                    <option value="damaged">Damaged</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} placeholder="Notes (optional)"
          className="w-full p-2 border border-gray-300 rounded text-sm" />
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>Close</Button>
          <Button onClick={onSubmit} disabled={busy} className="bg-blue-700 hover:bg-blue-800 text-white">
            {busy ? 'Posting...' : 'Post GRN'}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── Match bill modal ───────────────────────────────────────────────────────

function MatchBillModal({ po, onClose, onMatched }: {
  po: PurchaseOrderDetail; onClose: () => void; onMatched: () => Promise<void>;
}) {
  const [bills, setBills]   = useState<Invoice[]>([])
  const [billId, setBillId] = useState('')
  const [override, setOverride] = useState('')
  const [busy, setBusy]     = useState(false)
  const [err, setErr]       = useState<string | null>(null)

  useEffect(() => {
    // List the vendor bills captured AGAINST THIS PO (Invoice.purchase_order),
    // not every bill of the supplier. Filtering by supplier alone returned
    // nothing where the vendor contact had no company (Omogomotsi, 2026-09-02).
    getInvoices({ invoice_type: 'vendor_bill', purchase_order: po.id, page_size: '100' })
      .then((r) => setBills(r.results || [])).catch(() => setBills([]))
  }, [po.id])

  const onConfirm = async () => {
    setErr(null); setBusy(true)
    try {
      await createPOBillMatch({ purchase_order: po.id, bill: billId, override_reason: override })
      await onMatched()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Match failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg max-w-xl w-full p-6 space-y-3">
        <h3 className="text-lg font-medium">Match bill to PO {po.po_number}</h3>
        {err && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">{err}</div>}
        <div className="text-xs text-gray-600">
          Vendor bills captured against this PO:
        </div>
        {bills.length === 0 ? (
          <div className="text-sm text-gray-700 bg-amber-50 border border-amber-200 rounded p-3 space-y-1">
            <div>No bills have been captured against this PO yet.</div>
            <a href={`/invoices/new?type=vendor_bill&purchase_order=${po.id}`}
              className="text-[#0D1B2A] underline font-medium">
              Capture a supplier invoice for {po.po_number} →
            </a>
          </div>
        ) : (
          <select value={billId} onChange={(e) => setBillId(e.target.value)}
            className="w-full p-2 border border-gray-300 rounded text-sm">
            <option value="">— Choose bill —</option>
            {bills.map((b) => (
              <option key={b.id} value={b.id}>
                {b.invoice_number} — {b.currency_code} {b.total_amount}
                {b.status !== 'posted' ? `  (${b.status} — post it before matching)` : ''}
              </option>
            ))}
          </select>
        )}
        <textarea value={override} onChange={(e) => setOverride(e.target.value)} rows={2}
          placeholder="Variance override reason (only if bill total ≠ received value)"
          className="w-full p-2 border border-gray-300 rounded text-sm" />
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>Close</Button>
          <Button onClick={onConfirm} disabled={!billId || busy}
            className="bg-[#F4A623] hover:bg-[#d99320] text-white">
            {busy ? 'Matching...' : 'Match'}
          </Button>
        </div>
      </div>
    </div>
  )
}
