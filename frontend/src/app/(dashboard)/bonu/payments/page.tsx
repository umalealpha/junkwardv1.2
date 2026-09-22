'use client'

/**
 * /bonu/payments — the lawyer payment area (Phase 2).
 *
 * A confirmed firm bill becomes a payment here, paid through the firm's VAULTED
 * bank account (maker-checker, immutable), sent through the standard two-signature
 * approval, then LOADED into FNB. The money only ever leaves when the CFO releases
 * the batch inside the FNB app with his phone — Omni never moves money.
 *
 * Backend: /api/v1/bonu/payables/ · /bonu/invoices/<id>/raise-payment/ · /fnb/submit-batch/
 */

import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch, getBankAccounts, submitFNBBatch, type BankAccount } from '@/lib/api'
import { Banknote, Landmark, Loader2, Lock, Send } from 'lucide-react'
import {
  AMBER, BonuTabs, GREEN, LINE, NAVY, Note, ORANGE, RED, Stat, Table, money2, td, tdNum, trBorder,
} from '../_shared'

interface PayRef { id: string; reference: string; status: string; approval_status: string; bank_submitted?: boolean }
const isReadyForFnb = (i: Payable) =>
  !!i.payment && i.payment.status === 'confirmed'
  && i.payment.approval_status === 'approved' && !i.payment.bank_submitted
interface Payable {
  id: string; invoice_number: string; firm: string; firm_id: string
  invoice_date: string; total: number; status: string
  linked: boolean; bank_ready: boolean; payment: PayRef | null
}
interface PayablesResp { invoices: Payable[]; note: string }

const PAY_TONE: Record<string, string> = {
  draft: '#6B7280', confirmed: NAVY, paid: GREEN, rejected: RED,
}

export default function BonuPaymentsPage() {
  const [d, setD] = useState<PayablesResp | null>(null)
  const [banks, setBanks] = useState<BankAccount[]>([])
  const [bankId, setBankId] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    apiFetch<PayablesResp>('/bonu/payables/')
      .then((x) => { setD(x); setErr('') })
      .catch(() => setErr('Could not load the lawyer bills.'))
    getBankAccounts()
      .then((r) => setBanks((r.results || []).filter((b) => b.is_active && !b.hide_in_banking_ui)))
      .catch(() => { /* the picker just stays empty; raising will prompt for a bank */ })
  }, [])
  useEffect(load, [load])

  const raise = async (inv: Payable) => {
    if (!bankId) { setErr('Choose the bank account the payment goes out from (top right).'); return }
    setBusy(inv.id); setMsg(''); setErr('')
    try {
      const out = await apiFetch<{ message: string }>(`/bonu/invoices/${inv.id}/raise-payment/`, {
        method: 'POST', body: JSON.stringify({ bank_account_id: bankId }),
      })
      setMsg(out.message)
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not raise the payment.')
    } finally { setBusy('') }
  }

  const loadToFnb = async () => {
    if (!bankId) { setErr('Choose the source bank account first (top right).'); return }
    const ready = (d?.invoices || []).filter(isReadyForFnb)
    if (!ready.length) { setErr('Nothing approved to load yet — payments need both signatures first.'); return }
    setBusy('fnb'); setMsg(''); setErr('')
    try {
      const out = await submitFNBBatch({
        payment_ids: ready.map((i) => i.payment!.id),
        source_account_id: bankId,
        service_level_code: 'SDVA',
      })
      if (out.success) {
        setMsg(`Loaded ${ready.length} payment(s) into FNB. Approve the batch in the FNB app with your phone to release the money.`)
      } else {
        setErr(out.detail || 'FNB did not accept the batch.')
      }
      load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load the batch to FNB.')
    } finally { setBusy('') }
  }

  if (!d) {
    return (
      <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
        <TopBar title="BONU — lawyer payments" />
        <div className="mx-auto max-w-[1400px] px-6 py-5">
          <BonuTabs active="/bonu/payments" />
          <div className="mt-5">
            {err ? <Note tone="warn" title="Not loaded">{err}</Note> : (
              <div className="flex items-center gap-2 text-[13px]" style={{ color: '#6B7280' }}>
                <Loader2 className="h-4 w-4 animate-spin" style={{ color: ORANGE }} /> Loading the bills…
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  const rows = d.invoices
  const readyCount = rows.filter(isReadyForFnb).length
  const raisedCount = rows.filter((i) => i.payment).length

  return (
    <div className="min-h-screen" style={{ background: '#F6F8FB' }}>
      <TopBar title="BONU — lawyer payments" />
      <div className="mx-auto max-w-[1400px] px-6 py-5">
        <BonuTabs active="/bonu/payments" />

        {msg ? <div className="mt-4"><Note tone="good">{msg}</Note></div> : null}
        {err ? <div className="mt-4"><Note tone="warn" title="Check this">{err}</Note></div> : null}

        <div className="mt-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Card><CardContent className="p-4"><Stat label="Bills on file" value={String(rows.length)} /></CardContent></Card>
          <Card><CardContent className="p-4"><Stat label="Payments raised" value={String(raisedCount)} tone={NAVY} /></CardContent></Card>
          <Card><CardContent className="p-4"><Stat label="Approved, ready for FNB" value={String(readyCount)} tone={readyCount ? GREEN : '#6B7280'} /></CardContent></Card>
          <Card><CardContent className="p-4">
            <div className="text-[11px] font-medium mb-1" style={{ color: '#6B7280' }}>Pay from</div>
            <select aria-label="Bank account to pay from"
              value={bankId} onChange={(e) => setBankId(e.target.value)}
              className="w-full rounded-lg px-2 py-1.5 text-[13px]" style={{ border: `1px solid ${LINE}`, background: '#fff' }}>
              <option value="">Choose account…</option>
              {banks.map((b) => <option key={b.id} value={b.id}>{b.bank_name} · {b.account_number}</option>)}
            </select>
          </CardContent></Card>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="max-w-2xl text-[13px]" style={{ color: '#6B7280' }}>
            {d.note}
          </div>
          <button onClick={loadToFnb} disabled={busy === 'fnb' || !readyCount}
            className="flex items-center gap-2 rounded-lg px-4 py-2 text-[13px] font-semibold text-white transition-transform duration-150 hover:-translate-y-px disabled:opacity-50"
            style={{ background: NAVY }}>
            {busy === 'fnb' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Landmark className="h-4 w-4" style={{ color: ORANGE }} />}
            Load approved to FNB ({readyCount})
          </button>
        </div>

        <Card className="mt-4">
          <CardContent className="p-0">
            <Table head={['Invoice', 'Firm', 'Date', 'Amount', 'Bill status', 'Payment', 'Action']}>
              {rows.map((i) => {
                const p = i.payment
                const canRaise = i.linked && i.bank_ready && !p && i.status === 'approved'
                return (
                  <tr key={i.id} style={trBorder}>
                    <td className={td}>{i.invoice_number}</td>
                    <td className={td}>{i.firm}</td>
                    <td className={td}>{i.invoice_date || '—'}</td>
                    <td className={tdNum}>{money2(i.total)}</td>
                    <td className={td}>{i.status}</td>
                    <td className={td}>
                      {p ? (
                        <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
                          style={{ background: '#F3F4F6', color: PAY_TONE[p.status] || '#374151' }}>
                          {p.status}{p.approval_status ? ` · ${p.approval_status}` : ''}
                        </span>
                      ) : !i.linked ? (
                        <span className="inline-flex items-center gap-1 text-[12px]" style={{ color: AMBER }}>
                          <Lock className="h-3 w-3" /> firm not in vault
                        </span>
                      ) : !i.bank_ready ? (
                        <span className="inline-flex items-center gap-1 text-[12px]" style={{ color: AMBER }}>
                          <Lock className="h-3 w-3" /> bank not approved
                        </span>
                      ) : i.status !== 'approved' ? (
                        <span className="inline-flex items-center gap-1 text-[12px]" style={{ color: AMBER }}>
                          <Lock className="h-3 w-3" /> bill not approved
                        </span>
                      ) : <span className="text-[12px]" style={{ color: '#6B7280' }}>ready to raise</span>}
                    </td>
                    <td className={td}>
                      {canRaise ? (
                        <button onClick={() => raise(i)} disabled={busy === i.id}
                          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-semibold text-white disabled:opacity-50"
                          style={{ background: NAVY }}>
                          {busy === i.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" style={{ color: ORANGE }} />}
                          Raise payment
                        </button>
                      ) : p ? (
                        <span className="text-[12px]" style={{ color: '#6B7280' }}>in the queue</span>
                      ) : (
                        <span className="text-[12px]" style={{ color: '#9CA3AF' }}>—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
              {!rows.length ? (
                <tr><td colSpan={7} className="px-3 py-6 text-center text-[13px]" style={{ color: '#6B7280' }}>
                  No lawyer bills recorded yet. Record them under Capture or Bills to check.
                </td></tr>
              ) : null}
            </Table>
          </CardContent>
        </Card>

        <div className="mt-3 flex items-start gap-2 text-[12px]" style={{ color: '#6B7280' }}>
          <Banknote className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: ORANGE }} />
          A raised payment goes to the approval queue and needs a Finance signature and a CFO/CEO
          signature before it appears here as approved. Loading to FNB only queues the batch in the
          bank — you release the money yourself in the FNB app.
        </div>
      </div>
    </div>
  )
}
