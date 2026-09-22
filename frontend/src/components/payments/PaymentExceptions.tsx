'use client'
/*
 * PaymentExceptions — the committee decides fraud-risk exceptions (CFO 2026-09-02).
 *
 * A changed bank account (and later anything deemed possible fraud) never blocks
 * the raiser. The payment is entered and flagged; three of the six-member
 * committee decide it here. Approve -> it rejoins the normal flow; reject -> it
 * is rejected. The CFO also gets a records-only "clear" that never holds a
 * payment up. Omni moves no money.
 */
import { useCallback, useEffect, useState } from 'react'
import { ShieldAlert, CheckCircle2, XCircle, X } from 'lucide-react'
import {
  listPaymentExceptions, signPaymentException, clearPaymentException,
  uploadPaymentRequestAttachment,
  type PaymentExceptionRow, type PaymentExceptionsBoard,
} from '@/lib/api'

const NAVY = '#0D1B2A', ORANGE = '#F4A623'

function money(cur: string, v: string) {
  const n = Number(v)
  return `${cur} ${Number.isFinite(n) ? n.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : v}`
}

export function PaymentExceptions({ onClose, onChanged }: { onClose: () => void; onChanged?: () => void }) {
  const [board, setBoard] = useState<PaymentExceptionsBoard | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<Record<string, string>>({})
  const [call, setCall] = useState<Record<string, { who: string; number: string }>>({})
  const [loadErr, setLoadErr] = useState<string | null>(null)
  // The CFO's reason for closing an undecided exception (this rejects it).
  const [closeReason, setCloseReason] = useState<Record<string, string>>({})
  // Which attached file the committee member picked as the bank-error proof (PAY-PREM-01).
  const [proofPick, setProofPick] = useState<Record<string, string>>({})

  const refresh = useCallback(async () => {
    setLoading(true)
    try { setBoard(await listPaymentExceptions()); setLoadErr(null) }
    catch (e) {
      // Say what actually happened — a 502 during a deploy is not "you are not
      // on the committee" (Fable 5.1 audit 2026-09-02, M2).
      setBoard(null)
      setLoadErr(e instanceof Error ? e.message : 'Could not load the exceptions board.')
    }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const setCallField = (id: string, patch: Partial<{ who: string; number: string }>) =>
    setCall((c) => {
      const cur = c[id] || { who: '', number: '' }
      return { ...c, [id]: { ...cur, ...patch } }
    })

  const sign = async (row: PaymentExceptionRow, decision: 'approve' | 'reject') => {
    setBusy(true); setErr((e) => ({ ...e, [row.id]: '' }))
    try {
      const cb = call[row.id] || { who: '', number: '' }
      const r = await signPaymentException(row.id, {
        decision, called_who: cb.who, called_number: cb.number,
        proof_attachment_id: proofPick[row.id],
      })
      if (!r.ok) { setErr((e) => ({ ...e, [row.id]: r.detail || 'Could not record your decision.' })); return }
      await refresh(); onChanged?.()
    } catch {
      // A thrown fetch (network drop) must not leave every button frozen.
      setErr((e) => ({ ...e, [row.id]: 'Network problem — please try again.' }))
    } finally {
      setBusy(false)
    }
  }

  // Upload the bank-error proof onto a PAY-PREM-01 request, then auto-pick it.
  const uploadProof = async (row: PaymentExceptionRow, file: File) => {
    setBusy(true); setErr((e) => ({ ...e, [row.id]: '' }))
    try {
      const att = await uploadPaymentRequestAttachment(row.id, file)
      const id = (att as { id?: string })?.id
      await refresh()
      if (id) setProofPick((p) => ({ ...p, [row.id]: id }))
    } catch (e) {
      setErr((er) => ({ ...er, [row.id]: e instanceof Error ? e.message : 'Could not upload the proof.' }))
    } finally { setBusy(false) }
  }

  const clear = async (row: PaymentExceptionRow) => {
    setBusy(true); setErr((e) => ({ ...e, [row.id]: '' }))
    try { await clearPaymentException(row.id); await refresh(); onChanged?.() }
    catch (e) { setErr((er) => ({ ...er, [row.id]: e instanceof Error ? e.message : 'Could not clear.' })) }
    finally { setBusy(false) }
  }

  // The CFO closes an undecided exception (rejects the payment, reason on record).
  const closeOpen = async (row: PaymentExceptionRow) => {
    setBusy(true); setErr((e) => ({ ...e, [row.id]: '' }))
    try {
      await clearPaymentException(row.id, { close: true, reason: (closeReason[row.id] || '').trim() })
      await refresh(); onChanged?.()
    } catch (e) { setErr((er) => ({ ...er, [row.id]: e instanceof Error ? e.message : 'Could not close.' })) }
    finally { setBusy(false) }
  }

  const isCfo = board?.is_cfo
  const isMember = board?.is_committee_member
  const required = board?.required ?? 3

  return (
    <div className="fixed inset-0 z-[10000] bg-black/50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b" style={{ borderColor: '#E5E7EB' }}>
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-5 h-5" style={{ color: ORANGE }} />
            <h2 className="text-base font-semibold" style={{ color: NAVY }}>Payment exceptions — committee</h2>
          </div>
          <button onClick={onClose} className="text-[#9CA3AF] hover:text-[#374151]"><X className="w-5 h-5" /></button>
        </div>

        <div className="overflow-y-auto px-5 py-4 space-y-5">
          {loading ? (
            <p className="text-sm text-[#9CA3AF]">Loading…</p>
          ) : !board ? (
            <p className="text-sm text-[#991B1B]">{loadErr || 'Could not load the exceptions board.'}</p>
          ) : (
            <>
              {/* To decide — committee */}
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-[#6B7280] mb-2">
                  To decide {board.open.length ? `(${board.open.length})` : ''}
                </h3>
                {board.open.length === 0 ? (
                  <p className="text-sm text-[#9CA3AF]">Nothing waiting. A fraud-risk exception will appear here.</p>
                ) : board.open.map((row) => {
                  const isBankChange = row.exception_control === 'PAY-BANK-01'
                  const isPremium = row.exception_control === 'PAY-PREM-01'
                    || (row.exception_reason || '').includes('[PAY-PREM-01]')
                  const cb = call[row.id] || { who: '', number: '' }
                  return (
                    <div key={row.id} className="border rounded-lg p-3 mb-3" style={{ borderColor: '#E5E7EB' }}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="text-sm">
                          <div className="font-semibold" style={{ color: NAVY }}>{row.payee || '—'}</div>
                          <div className="text-[#6B7280]">{row.ref} · {money(row.currency, row.total)} · raised by {row.raised_by}</div>
                        </div>
                        <span className="text-[11px] px-2 py-0.5 rounded-full whitespace-nowrap"
                              style={{ background: '#FFFBEB', color: '#92400E', border: `1px solid ${ORANGE}` }}>
                          {row.approvals} of {required} approved · any reject ends it
                        </span>
                      </div>
                      <p className="mt-2 text-xs whitespace-pre-line text-[#78350F] bg-[#FFFBEB] rounded p-2 border" style={{ borderColor: ORANGE }}>
                        {row.exception_reason}
                      </p>
                      <p className="mt-1 text-[11px] text-[#374151]">
                        <span className="text-[#6B7280]">Raiser&apos;s explanation:</span>{' '}
                        {row.bank_change_reason?.trim() || <span className="text-[#9CA3AF]">none given</span>}
                      </p>
                      {row.signoffs.length > 0 && (
                        <p className="mt-1 text-[11px] text-[#6B7280]">
                          Signed: {row.signoffs.map((s) => `${s.signer} (${s.decision})`).join(', ')}
                        </p>
                      )}
                      {isMember && (
                        <>
                          {isBankChange && (
                            <div className="mt-2 grid grid-cols-2 gap-2">
                              <input value={cb.who} onChange={(e) => setCallField(row.id, { who: e.target.value })}
                                     placeholder="Who did you phone?"
                                     className="border rounded px-2 py-1 text-xs" style={{ borderColor: '#D1D5DB' }} />
                              <input value={cb.number} onChange={(e) => setCallField(row.id, { number: e.target.value })}
                                     placeholder="Number you used (from our records)"
                                     className="border rounded px-2 py-1 text-xs" style={{ borderColor: '#D1D5DB' }} />
                            </div>
                          )}
                          {isPremium && (
                            <div className="mt-2">
                              <p className="text-[11px] text-[#6B7280] mb-1">
                                Pick the bank-error proof (the debit failed on the bank&apos;s side, not the customer&apos;s):
                              </p>
                              {(row.attachments || []).length === 0 ? (
                                <p className="text-[11px] text-[#9CA3AF]">No files attached yet — upload the proof below.</p>
                              ) : (
                                <div className="space-y-1">
                                  {(row.attachments || []).map((a) => (
                                    <label key={a.id} className="flex items-center gap-2 text-xs text-[#374151]">
                                      <input type="radio" name={`proof-${row.id}`} checked={proofPick[row.id] === a.id}
                                             onChange={() => setProofPick((p) => ({ ...p, [row.id]: a.id }))} />
                                      {a.name}
                                    </label>
                                  ))}
                                </div>
                              )}
                              <input type="file" className="mt-1 text-[11px]" disabled={busy}
                                     onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadProof(row, f) }} />
                            </div>
                          )}
                          <div className="mt-2 flex items-center gap-2">
                            <button onClick={() => sign(row, 'approve')} disabled={busy}
                                    className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg text-white"
                                    style={{ background: '#166534' }}>
                              <CheckCircle2 className="w-3.5 h-3.5" /> Approve
                            </button>
                            <button onClick={() => sign(row, 'reject')} disabled={busy}
                                    className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg text-white"
                                    style={{ background: '#991B1B' }}>
                              <XCircle className="w-3.5 h-3.5" /> Reject
                            </button>
                          </div>
                        </>
                      )}
                      {isCfo && (
                        <div className="mt-2 flex items-center gap-2">
                          <input value={closeReason[row.id] || ''}
                                 onChange={(e) => setCloseReason((c) => ({ ...c, [row.id]: e.target.value }))}
                                 placeholder="Reason to close it (this rejects the payment)"
                                 className="flex-1 border rounded px-2 py-1 text-xs" style={{ borderColor: '#D1D5DB' }} />
                          <button onClick={() => closeOpen(row)}
                                  disabled={busy || (closeReason[row.id] || '').trim().length < 5}
                                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg text-white disabled:opacity-50"
                                  style={{ background: NAVY }}>
                            <XCircle className="w-3.5 h-3.5" /> Close (reject)
                          </button>
                        </div>
                      )}
                      {err[row.id] && <p className="mt-2 text-xs text-[#991B1B]">{err[row.id]}</p>}
                    </div>
                  )
                })}
              </div>

              {/* To clear — CFO records only */}
              {isCfo && (
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-[#6B7280] mb-2">
                    To clear for your records {board.to_clear.length ? `(${board.to_clear.length})` : ''}
                  </h3>
                  <p className="text-[11px] text-[#9CA3AF] mb-2">Clearing is records-only. It never holds a payment up.</p>
                  {board.to_clear.length === 0 ? (
                    <p className="text-sm text-[#9CA3AF]">Nothing to clear.</p>
                  ) : board.to_clear.map((row) => (
                    <div key={row.id} className="border rounded-lg p-3 mb-3" style={{ borderColor: '#E5E7EB' }}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="text-sm">
                          <div className="font-semibold" style={{ color: NAVY }}>{row.payee || '—'}</div>
                          <div className="text-[#6B7280]">
                            {row.ref} · {money(row.currency, row.total)} · committee {row.exception_decision === 'approve' ? 'approved' : 'rejected'}
                          </div>
                        </div>
                        <button onClick={() => clear(row)} disabled={busy}
                                className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg text-white"
                                style={{ background: NAVY }}>
                          <CheckCircle2 className="w-3.5 h-3.5" /> Clear
                        </button>
                      </div>
                      {err[row.id] && <p className="mt-2 text-xs text-[#991B1B]">{err[row.id]}</p>}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        <div className="px-5 py-3 border-t text-xs text-[#6B7280] flex items-center gap-2" style={{ borderColor: '#E5E7EB' }}>
          <CheckCircle2 className="w-4 h-4" style={{ color: ORANGE }} />
          Three of the committee decide an exception. Omni moves no money — the bank and your own 2-step stay the real gate.
        </div>
      </div>
    </div>
  )
}
