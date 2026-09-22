'use client'

import { useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import {
  getPayment, confirmPayment, getToken,
  updatePayment, getContacts, getAccounts, resetPaymentToDraft,
  submitPaymentForApproval, approvePayment, rejectPayment,
} from '@/lib/api'
import type { PaymentDetail, Contact, Account } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { StatusBadge, TypeBadge } from '@/components/ui/badge'
import { ConfirmDialog } from '@/components/ui/modal'
import { LoadingCard } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { CheckCircle, AlertCircle, Pencil, Save, X, RotateCcw, ShieldCheck, Send, ThumbsUp, ThumbsDown } from 'lucide-react'

const PAYMENT_METHODS = [
  { value: 'bank_transfer', label: 'Bank Transfer' },
  { value: 'debit_order',   label: 'Debit Order' },
  { value: 'mobile_money',  label: 'Mobile Money' },
  { value: 'cash',          label: 'Cash' },
  { value: 'cheque',        label: 'Cheque' },
  { value: 'gateway',       label: 'Payment Gateway' },
]

const PAYMENT_TYPES = [
  { value: 'received', label: 'Received (money IN)' },
  { value: 'sent',     label: 'Sent (money OUT)' },
]

export default function PaymentDetailPage() {
  const router = useRouter()
  const params = useParams()
  const id = params.id as string
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const [payment, setPayment] = useState<PaymentDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [showConfirmDialog, setShowConfirmDialog] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Edit mode state
  const [isEditing, setIsEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [bankAccounts, setBankAccounts] = useState<Account[]>([])
  const [refDataLoaded, setRefDataLoaded] = useState(false)
  const [editType, setEditType] = useState('')
  const [editContact, setEditContact] = useState('')
  const [editCompany, setEditCompany] = useState('')
  const [editBank, setEditBank] = useState('')
  const [editDate, setEditDate] = useState('')
  const [editAmount, setEditAmount] = useState('')
  const [editMethod, setEditMethod] = useState('')
  const [editRef, setEditRef] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const { companies } = useCompany()

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const p = await getPayment(id)
      setPayment(p)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load payment')
    } finally { setLoading(false) }
  }

  const enterEditMode = async () => {
    if (!payment) return
    setIsEditing(true)
    if (!refDataLoaded) {
      try {
        const [c, a] = await Promise.all([
          getContacts({ page_size: '500' }),
          getAccounts({ page_size: '500' }),
        ])
        setContacts(c.results)
        setBankAccounts(a.results.filter((acct) => acct.is_bank_account))
        setRefDataLoaded(true)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load reference data')
      }
    }
    setEditType(payment.payment_type)
    setEditContact(payment.contact)
    setEditCompany(payment.company || '')
    setEditBank(payment.bank_account)
    setEditDate(payment.payment_date)
    setEditAmount(String(parseAmount(payment.amount)))
    setEditMethod(payment.payment_method || 'bank_transfer')
    setEditRef(payment.reference || '')
    setEditDesc(payment.description || '')
  }

  const cancelEdit = () => { setIsEditing(false); setError(null) }

  const saveEdits = async () => {
    if (!payment) return
    setError(null)
    if (!editContact)            { setError('Contact is required'); return }
    if (!editBank)               { setError('Bank account is required'); return }
    if (parseAmount(editAmount) <= 0) { setError('Amount must be > 0'); return }
    setSaving(true)
    try {
      const updated = await updatePayment(id, {
        payment_type:   editType,
        contact:        editContact,
        company:        editCompany || undefined,
        bank_account:   editBank,
        payment_date:   editDate,
        amount:         editAmount,
        payment_method: editMethod,
        reference:      editRef || undefined,
      })
      // description isn't on CreatePaymentInput so we reload to pick up server state
      setPayment(updated)
      setIsEditing(false)
      setSuccess('Draft payment updated')
      setTimeout(() => setSuccess(null), 3500)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update payment')
    } finally { setSaving(false) }
  }

  const handleConfirm = async () => {
    setConfirming(true)
    try {
      const updated = await confirmPayment(id)
      setPayment(updated)
      setShowConfirmDialog(false)
      setSuccess('Payment confirmed successfully')
      setTimeout(() => setSuccess(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to confirm payment')
      setShowConfirmDialog(false)
    } finally { setConfirming(false) }
  }

  const handleResetToDraft = async () => {
    setShowResetConfirm(false)
    setResetting(true); setError(null)
    try {
      const updated = await resetPaymentToDraft(id)
      setPayment(updated)
      setSuccess('Payment reset to draft. Edit and re-confirm when ready.')
      setTimeout(() => setSuccess(null), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset to draft')
    } finally { setResetting(false) }
  }

  // ── PAY-003 tier maker-checker ──────────────────────────────────────────
  const [submitting, setSubmitting] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [approvalComment, setApprovalComment] = useState('')

  const handleSubmitForApproval = async () => {
    setSubmitting(true); setError(null)
    try {
      const res = await submitPaymentForApproval(id)
      setPayment(res)
      setSuccess(`Submitted for Tier ${res.assigned_tier ?? '?'} (${res.assigned_role ?? ''}) approval.`)
      setTimeout(() => setSuccess(null), 5000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit for approval')
    } finally { setSubmitting(false) }
  }

  const handleApprove = async () => {
    if (!approvalComment.trim()) { setError('An approval comment is mandatory.'); return }
    setDeciding(true); setError(null)
    try {
      const res = await approvePayment(id, approvalComment.trim())
      setPayment(res)
      setApprovalComment('')
      const quorum = (res as any).quorum
      if (quorum && quorum.quorum_met === false) {
        // Signature recorded — nothing posted yet (needs 1×FM/FC + 1×CFO/CEO).
        setSuccess(`Signature recorded — ${quorum.fm_fc || 0}×FM/FC + ${quorum.exec || 0}×CFO/CEO so far; needs 1×FM/FC + 1×CFO/CEO before it posts.`)
      } else {
        const tie = (res as any).bs_tie
        const tieMsg = tie ? ` BS tie: variance ${tie.variance} (${tie.reconciled ? 'OK' : 'INVESTIGATE'}).` : ''
        setSuccess(`Approved & posted (DR AP / CR Bank).${tieMsg}`)
      }
      setTimeout(() => setSuccess(null), 8000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve payment')
    } finally { setDeciding(false) }
  }

  const handleReject = async () => {
    if (!approvalComment.trim()) { setError('A rejection comment is mandatory.'); return }
    setDeciding(true); setError(null)
    try {
      const res = await rejectPayment(id, approvalComment.trim())
      setPayment(res)
      setApprovalComment('')
      setSuccess('Payment rejected. It is back at draft for amendment.')
      setTimeout(() => setSuccess(null), 5000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reject payment')
    } finally { setDeciding(false) }
  }

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Payment Detail" breadcrumbs={[{ label: 'Payments', href: '/payments' }]} />
        <div className="p-6"><LoadingCard message="Loading payment..." className="h-40" /></div>
      </div>
    )
  }

  if (error && !payment) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Payment Detail" breadcrumbs={[{ label: 'Payments', href: '/payments' }]} />
        <div className="p-6">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-4">
            <p className="text-[#DC2626]">{error}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!payment) return null

  const isDraft = payment.status === 'draft'
  const isConfirmed = payment.status === 'confirmed' || payment.status === 'reconciled'
  // PAY-003 tier maker-checker state
  const isSent = payment.payment_type === 'sent'
  const appr = payment.approval_status || 'not_required'
  const isPendingApproval = appr === 'pending'
  // SENT drafts route through the tier queue; RECEIVED drafts confirm directly.
  const canSubmitForApproval = isDraft && !isEditing && isSent && !isPendingApproval && appr !== 'approved'
  const canConfirmDirect = isDraft && !isEditing && !isPendingApproval && (!isSent || appr === 'approved')

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={payment.payment_number}
        breadcrumbs={[{ label: 'Payments', href: '/payments' }, { label: payment.payment_number }]}
        actions={
          <div className="flex items-center gap-2">
            {/* Draft mode: edit / save / cancel / confirm */}
            {isDraft && !isEditing && (
              <Button variant="outline" size="sm" leftIcon={<Pencil className="w-3.5 h-3.5" />} onClick={enterEditMode}>
                Edit Draft
              </Button>
            )}
            {isDraft && isEditing && (
              <>
                <Button variant="outline" size="sm" leftIcon={<X className="w-3.5 h-3.5" />} onClick={cancelEdit} disabled={saving}>
                  Cancel
                </Button>
                <Button variant="accent" size="sm" leftIcon={<Save className="w-3.5 h-3.5" />} onClick={saveEdits} disabled={saving}>
                  {saving ? 'Saving…' : 'Save'}
                </Button>
              </>
            )}
            {canSubmitForApproval && (
              <Button variant="accent" size="sm" leftIcon={<Send className="w-3.5 h-3.5" />} onClick={handleSubmitForApproval} disabled={submitting}>
                {submitting ? 'Submitting…' : 'Submit for Approval'}
              </Button>
            )}
            {canConfirmDirect && (
              <Button variant="accent" size="sm" leftIcon={<CheckCircle className="w-3.5 h-3.5" />} onClick={() => setShowConfirmDialog(true)}>
                Confirm Payment
              </Button>
            )}

            {/* Confirmed/Reconciled mode: Reset to Draft */}
            {payment.status === 'confirmed' && (
              <Button variant="outline" size="sm" leftIcon={<RotateCcw className="w-3.5 h-3.5" />} onClick={() => setShowResetConfirm(true)} disabled={resetting}>
                Reset to Draft
              </Button>
            )}

            {/* Status pill */}
            <div className="flex items-center gap-1 ml-2 pl-3 border-l border-[#E5E7EB]">
              <span className={cn(
                'px-2.5 py-1 rounded-md text-[11px] font-medium',
                isDraft ? 'bg-[#EFF6FF] text-[#2563EB]' : 'bg-[#F3F4F6] text-[#9CA3AF]'
              )}>Draft</span>
              <span className="text-[#D1D5DB] text-xs">→</span>
              <span className={cn(
                'px-2.5 py-1 rounded-md text-[11px] font-medium',
                isConfirmed ? 'bg-[#ECFDF5] text-[#059669]'
                  : payment.status === 'cancelled' ? 'bg-[#F3F4F6] text-[#6B7280]'
                  : 'bg-[#F3F4F6] text-[#9CA3AF]'
              )}>
                {payment.status === 'cancelled' ? 'Cancelled'
                  : payment.status === 'reconciled' ? 'Reconciled'
                  : 'Confirmed'}
              </span>
            </div>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-xl p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#059669]" />
            <p className="text-[#059669] text-sm">{success}</p>
          </div>
        )}
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Payment Header */}
        <Card>
          <CardContent className="py-5">
            {!isEditing ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-6">
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Payment #</p>
                    <p className="text-base font-bold text-[#CC6C00] mt-1">{payment.payment_number}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Type</p>
                    <div className="mt-1.5"><TypeBadge type={payment.payment_type} /></div>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Company</p>
                    <p className="text-sm text-[#111827] font-medium mt-1">
                      {payment.company_code ? (
                        <><span className="font-mono text-[#CC6C00]">{payment.company_code}</span> · {payment.company_name}</>
                      ) : <span className="text-[#9CA3AF]">—</span>}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Contact</p>
                    <p className="text-sm text-[#111827] font-medium mt-1">{payment.contact_name}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Date</p>
                    <p className="text-sm text-[#374151] mt-1">{formatDate(payment.payment_date)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Status</p>
                    <div className="mt-1.5"><StatusBadge status={payment.status} size="md" /></div>
                  </div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-6 mt-5 pt-5 border-t border-[#E5E7EB]">
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Amount</p>
                    <p className={cn('text-xl font-bold mt-1 font-mono-nums', payment.payment_type === 'received' ? 'text-[#059669]' : 'text-[#DC2626]')}>
                      {payment.payment_type === 'received' ? '+' : '-'}
                      {fmt(payment.amount, payment.currency)}
                    </p>
                  </div>
                  {payment.currency !== 'BWP' && (
                    <div>
                      <p className="text-xs text-[#6B7280] uppercase tracking-wider">Amount (BWP)</p>
                      <p className="text-sm font-semibold text-[#374151] mt-1 font-mono-nums">{fmt(payment.amount_bwp)}</p>
                    </div>
                  )}
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Method</p>
                    <p className="text-sm text-[#374151] mt-1 capitalize">{payment.payment_method?.replace(/_/g, ' ')}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Bank Account</p>
                    <p className="text-sm text-[#374151] mt-1">{payment.bank_account_code}</p>
                  </div>
                  {payment.reference && (
                    <div>
                      <p className="text-xs text-[#6B7280] uppercase tracking-wider">Reference</p>
                      <p className="text-sm text-[#374151] mt-1 font-mono">{payment.reference}</p>
                    </div>
                  )}
                  {payment.je_number && (
                    <div>
                      <p className="text-xs text-[#6B7280] uppercase tracking-wider">Journal Entry</p>
                      <p className="text-sm text-[#CC6C00] mt-1 font-mono">{payment.je_number}</p>
                    </div>
                  )}
                </div>
                {payment.description && (
                  <div className="mt-4 pt-4 border-t border-[#E5E7EB]">
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider mb-1">Description</p>
                    <p className="text-sm text-[#6B7280]">{payment.description}</p>
                  </div>
                )}
              </>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Company / Subsidiary</label>
                  <select value={editCompany} onChange={(e) => setEditCompany(e.target.value)} className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white">
                    <option value="">— Default —</option>
                    {companies.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Type (direction)</label>
                  <select value={editType} onChange={(e) => setEditType(e.target.value)} className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white">
                    {PAYMENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Contact</label>
                  <select value={editContact} onChange={(e) => setEditContact(e.target.value)} className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white">
                    <option value="">— Select contact —</option>
                    {contacts.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.contact_type})</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Bank Account</label>
                  <select value={editBank} onChange={(e) => setEditBank(e.target.value)} className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white">
                    <option value="">— Select bank account —</option>
                    {bankAccounts.map((a) => <option key={a.id} value={a.id}>{a.code} — {a.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Date</label>
                  <input type="date" value={editDate} onChange={(e) => setEditDate(e.target.value)} className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Amount (BWP)</label>
                  <input type="number" step="0.01" value={editAmount} onChange={(e) => setEditAmount(e.target.value)} className="w-full text-sm font-mono rounded-md px-3 py-2 border border-[#E5E7EB] bg-white" />
                </div>
                <div>
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Method</label>
                  <select value={editMethod} onChange={(e) => setEditMethod(e.target.value)} className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white">
                    {PAYMENT_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                </div>
                <div className="col-span-full">
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Reference</label>
                  <input type="text" value={editRef} onChange={(e) => setEditRef(e.target.value)} placeholder="INV-12345 / EFT ref" className="w-full text-sm font-mono rounded-md px-3 py-2 border border-[#E5E7EB] bg-white" />
                </div>
                <div className="col-span-full">
                  <label className="block text-xs text-[#6B7280] uppercase tracking-wider mb-1">Description (read-only on save)</label>
                  <input type="text" value={editDesc} readOnly className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-[#F9FAFB] text-[#9CA3AF]" />
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Plain-English status timeline (CFO 2026-08-22) */}
        {!isEditing && isSent && (() => {
          const p = payment as PaymentDetail & { bank_submitted_at?: string | null }
          const rejected = appr === 'rejected'
          const steps: { label: string; at?: string | null; done: boolean; bad?: boolean; hint?: string }[] = [
            { label: 'Created in Omni', at: p.created_at, done: true },
            { label: 'Submitted for approval', at: p.submitted_for_approval_at, done: !!p.submitted_for_approval_at || appr === 'approved' || rejected },
            rejected
              ? { label: 'Rejected', at: p.approval_decided_at, done: true, bad: true }
              : { label: 'Approved', at: p.approval_decided_at,
                  done: appr === 'approved' || !!p.bank_submitted_at
                        || (appr === 'not_required' && payment.status !== 'draft') },
            { label: 'Loaded into FNB', at: p.bank_submitted_at, done: !!p.bank_submitted_at,
              hint: 'Now waiting for you to authorise it on FNB (phone/computer, separate password). Money does not leave Omni.' },
            { label: 'Paid & settled at the bank', done: payment.status === 'reconciled',
              hint: 'Confirmed once the bank statement is reconciled.' },
          ]
          return (
            <Card>
              <CardHeader><CardTitle>Where this payment is</CardTitle></CardHeader>
              <CardContent>
                <ol className="space-y-3">
                  {steps.map((s, i) => (
                    <li key={i} className="flex items-start gap-3">
                      <span className={cn('mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px]',
                        s.bad ? 'bg-[#FEF2F2] text-[#DC2626]'
                          : s.done ? 'bg-[#ECFDF5] text-[#059669]'
                          : 'bg-[#F3F4F6] text-[#9CA3AF]')}>
                        {s.bad ? '✕' : s.done ? '✓' : (i + 1)}
                      </span>
                      <div className="min-w-0">
                        <div className={cn('text-sm font-medium', s.done || s.bad ? 'text-[#111827]' : 'text-[#9CA3AF]')}>
                          {s.label}
                          {s.at ? <span className="ml-2 text-xs font-normal text-[#6B7280]">{formatDate(s.at)}</span> : null}
                        </div>
                        {s.hint && s.done && !s.bad && <div className="text-xs text-[#6B7280] mt-0.5">{s.hint}</div>}
                      </div>
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          )
        })()}

        {/* PAY-003 — Tier approval panel */}
        {!isEditing && (isPendingApproval || appr === 'approved' || appr === 'rejected') && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-[#CC6C00]" />
                Tier Approval
                {payment.approval_tier ? (
                  <span className="ml-1 inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold bg-[#FFF7ED] text-[#CC6C00] border border-[#FFD7B5]">
                    Tier {payment.approval_tier}
                  </span>
                ) : null}
                <span className={cn(
                  'ml-auto inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-medium',
                  appr === 'pending'  ? 'bg-[#FFFBEB] text-[#D97706]'
                  : appr === 'approved' ? 'bg-[#ECFDF5] text-[#059669]'
                  : 'bg-[#FEF2F2] text-[#DC2626]'
                )}>
                  {appr === 'pending' ? 'Pending approval' : appr === 'approved' ? 'Approved' : 'Rejected'}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Submitted by</p>
                  <p className="text-[#374151] mt-1">{payment.submitted_by_name || '—'}</p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Submitted at</p>
                  <p className="text-[#374151] mt-1">{payment.submitted_for_approval_at ? formatDate(payment.submitted_for_approval_at) : '—'}</p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Decided by</p>
                  <p className="text-[#374151] mt-1">{payment.approval_decided_by_name || '—'}</p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Decided at</p>
                  <p className="text-[#374151] mt-1">{payment.approval_decided_at ? formatDate(payment.approval_decided_at) : '—'}</p>
                </div>
              </div>

              {(payment as any).is_once_off && (
                <div className="rounded-lg border border-[#FFD7B5] bg-[#FFFBF5] p-3">
                  <p className="text-xs text-[#92400E] uppercase tracking-wider mb-1">One-off payee — money goes to</p>
                  <p className="text-sm text-[#374151] font-medium">{(payment as any).payee_name || '—'}</p>
                  <p className="text-xs text-[#6B7280] mt-0.5">
                    {(payment as any).payee_bank_name || 'Bank n/a'} · acct {(payment as any).payee_account_number || '—'}
                    {(payment as any).payee_branch_code ? ` · branch ${(payment as any).payee_branch_code}` : ''}
                  </p>
                </div>
              )}

              {Array.isArray((payment as any).approvals) && (payment as any).approvals.length > 0 && (
                <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-3">
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider mb-2">
                    Signatures ({(payment as any).approvals.length}) — needs 1×FM/FC + 1×CFO/CEO
                  </p>
                  <ul className="space-y-1">
                    {(payment as any).approvals.map((a: any) => (
                      <li key={a.id} className="text-sm text-[#374151] flex items-center gap-2">
                        <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#EEF2FF] text-[#3730A3] uppercase">{a.role}</span>
                        <span className="font-medium">{a.approver_name || a.approver_username}</span>
                        <span className="text-xs text-[#9CA3AF]">{a.comment ? `— ${a.comment}` : ''}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {appr !== 'pending' && payment.approval_comment && (
                <div className="rounded-lg border border-[#E5E7EB] bg-[#F9FAFB] p-3">
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider mb-1">Approver comment</p>
                  <p className="text-sm text-[#374151]">{payment.approval_comment}</p>
                </div>
              )}

              {isPendingApproval && (
                <div className="rounded-lg border border-[#FFD7B5] bg-[#FFFBF5] p-4 space-y-3">
                  <p className="text-xs text-[#92400E]">
                    Maker-checker: the creator/submitter cannot approve. A comment is mandatory.
                    Approving posts the journal entry (DR Accounts Payable / CR Bank) and marks the bill Paid.
                  </p>
                  <textarea
                    value={approvalComment}
                    onChange={(e) => setApprovalComment(e.target.value)}
                    rows={2}
                    placeholder="Comment (required for approve or reject)…"
                    className="w-full text-sm rounded-md px-3 py-2 border border-[#D1D5DB] bg-white focus:outline-none focus:border-[#F07F00]"
                  />
                  <div className="flex items-center gap-2">
                    <Button variant="accent" size="sm" leftIcon={<ThumbsUp className="w-3.5 h-3.5" />} onClick={handleApprove} disabled={deciding}>
                      {deciding ? 'Working…' : 'Approve & Post'}
                    </Button>
                    <Button variant="outline" size="sm" leftIcon={<ThumbsDown className="w-3.5 h-3.5" />} onClick={handleReject} disabled={deciding}>
                      Reject
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Allocations */}
        {!isEditing && payment.allocations && payment.allocations.length > 0 && (
          <Card>
            <CardHeader><CardTitle>Invoice Allocations</CardTitle></CardHeader>
            <CardContent className="p-0">
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Invoice #</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Invoice Total</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Allocated</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Invoice Status</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {payment.allocations.map((alloc) => (
                    <tr key={alloc.id} className="table-row-alt hover:bg-[#FFF7ED] cursor-pointer transition-colors" onClick={() => router.push(`/invoices/${alloc.invoice}`)}>
                      <td className="px-4 py-3 font-mono text-xs text-[#CC6C00]">{alloc.invoice_number}</td>
                      <td className="px-4 py-3 text-right font-mono-nums text-[#6B7280]">{fmt(alloc.invoice_total, payment.currency)}</td>
                      <td className="px-4 py-3 text-right font-mono-nums text-[#059669] font-medium">{fmt(alloc.amount_allocated, payment.currency)}</td>
                      <td className="px-4 py-3"><StatusBadge status={alloc.invoice_status} /></td>
                      <td className="px-4 py-3 text-[#6B7280] text-xs">{formatDate(alloc.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="bg-[#F9FAFB] border-t border-[#E5E7EB]">
                  <tr>
                    <td colSpan={2} className="px-4 py-3 text-xs font-semibold text-[#6B7280] uppercase">Total Allocated</td>
                    <td className="px-4 py-3 text-right font-semibold font-mono-nums text-[#059669]">
                      {fmt(
                        payment.allocations.reduce((s, a) => s + parseAmount(a.amount_allocated), 0),
                        payment.currency
                      )}
                    </td>
                    <td colSpan={2} />
                  </tr>
                </tfoot>
              </table>
            </CardContent>
          </Card>
        )}

        {/* WHT Record */}
        {!isEditing && payment.wht_record && (
          <Card>
            <CardHeader><CardTitle>Withholding Tax Record</CardTitle></CardHeader>
            <CardContent>
              <pre className="text-xs text-[#374151] bg-[#F9FAFB] border border-[#E5E7EB] rounded-lg p-4 overflow-x-auto">
                {JSON.stringify(payment.wht_record, null, 2)}
              </pre>
            </CardContent>
          </Card>
        )}
      </div>

      <ConfirmDialog
        open={showConfirmDialog}
        onOpenChange={setShowConfirmDialog}
        title="Confirm Payment"
        description={`Are you sure you want to confirm payment ${payment.payment_number}? This will create a journal entry.`}
        confirmLabel="Confirm Payment"
        variant="primary"
        loading={confirming}
        onConfirm={handleConfirm}
      />

      <ConfirmDialog
        open={showResetConfirm}
        onOpenChange={setShowResetConfirm}
        title="Reset to Draft"
        description={`Reverse the journal entry for ${payment.payment_number} and put it back into draft? The original journal entry will be reversed in the GL (audit trail preserved). You can then edit and re-confirm.`}
        confirmLabel="Reset to Draft"
        variant="warning"
        loading={resetting}
        onConfirm={handleResetToDraft}
      />
    </div>
  )
}
