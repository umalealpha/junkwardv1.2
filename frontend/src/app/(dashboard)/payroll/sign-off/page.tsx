'use client'

/**
 * /payroll/sign-off — DUAL sign-off (CFO directive 2026-07-28).
 *
 * A company's month is released to staff only when BOTH an HR signer
 * (Unami or Dorothy) AND a Finance signer (Kago or Pako) have signed the
 * current figures. The CFO is out of the routine loop. If the figures change
 * after a signature, that signature drops and both sign again.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getPayrollSignOffBoard, signPayroll, rejectPayrollSignOff } from '@/lib/api'
import type { PayrollSignOffBoard, PayrollSignOffRow } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, CheckCircle2, AlertTriangle, ShieldCheck, Clock } from 'lucide-react'

const pula = (v: string | number | undefined) =>
  'P' + Number(v ?? 0).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

function Leg({ label, signedBy, at }: { label: string; signedBy?: string | null; at?: string | null }) {
  if (signedBy) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded"
            style={{ background: '#D1FAE5', color: '#065F46' }}>
        <CheckCircle2 className="w-3.5 h-3.5" />{label}: {signedBy}
        {at ? ` · ${new Date(at).toLocaleDateString('en-GB')}` : ''}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded"
          style={{ background: '#FEF3C7', color: '#92400E' }}>
      <Clock className="w-3.5 h-3.5" />{label}: awaiting sign
    </span>
  )
}

export default function PayrollSignOffPage() {
  const router = useRouter()
  const [board, setBoard] = useState<PayrollSignOffBoard | null>(null)
  const [period, setPeriod] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [rejectFor, setRejectFor] = useState<PayrollSignOffRow | null>(null)
  const [reason, setReason] = useState('')

  const load = useCallback(async (p?: string) => {
    setLoading(true)
    try {
      const b = await getPayrollSignOffBoard(p)
      setBoard(b); setPeriod(b.period || '')
    } catch {
      setMsg({ kind: 'err', text: 'Could not load the payroll sign-off board.' })
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const act = async (label: string, fn: () => Promise<unknown>, key: string) => {
    setBusy(key); setMsg(null)
    try { await fn(); setMsg({ kind: 'ok', text: label }); await load(period) }
    catch (e) { setMsg({ kind: 'err', text: (e as Error).message || 'That did not go through.' }) }
    finally { setBusy(null) }
  }

  const rows = board?.rows ?? []
  const open = rows.filter(r => !r.closed).length
  const canHr = board?.can_sign_hr
  const canFin = board?.can_sign_fin
  // The CFO / a superuser is a BACK-STOP for both legs (signoff_side='both') —
  // not the routine signer (HR = Unami/Dorothy, Finance = Kago/Pako). So show his
  // sign buttons as clearly-labelled STAND-IN actions, secondary style, so an
  // HR-awaiting payroll no longer reads as his own to-do (CFO 2026-08-31:
  // "why is this on my queue if this must be signed by HR?").
  const isBackstop = board?.my_side === 'both'

  return (
    <div className="min-h-screen bg-[#F9FAFB]">
      <TopBar />
      <div className="max-w-6xl mx-auto px-4 py-6 space-y-4">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => router.push('/payroll')}>
            <ArrowLeft className="w-4 h-4 mr-1" /> Payroll
          </Button>
          <h1 className="text-xl font-bold text-[#0D1B2A]">Payroll sign-off</h1>
        </div>

        <Card>
          <CardContent className="py-4 text-sm text-[#374151] space-y-1">
            <p><strong>Payslips are released only when HR and Finance have both signed.</strong></p>
            <p className="text-[#6B7280]">
              One HR signer (Unami or Dorothy) and one Finance signer (Kago or Pako)
              each check the figures and sign. Two different people. If the figures
              change after a signature, that signature drops and both sign again —
              a signature only ever covers the numbers on screen.
            </p>
          </CardContent>
        </Card>

        {msg && (
          <div className={`px-4 py-3 rounded text-sm ${msg.kind === 'ok'
            ? 'bg-[#D1FAE5] text-[#065F46]' : 'bg-[#FEE2E2] text-[#991B1B]'}`}>{msg.text}</div>
        )}

        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-[#F07F00]" /> Month
            </CardTitle>
            <select className="border rounded px-3 py-1.5 text-sm" value={period}
                    onChange={e => { setPeriod(e.target.value); load(e.target.value) }} disabled={loading}>
              {(board?.periods ?? []).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </CardHeader>
          <CardContent>
            {loading ? <p className="text-sm text-[#6B7280] py-6 text-center">Loading…</p>
            : rows.length === 0 ? (
              <p className="text-sm text-[#6B7280] py-6 text-center">
                No entity has a payroll in {period || 'this month'} yet.
              </p>
            ) : (
              <>
                <p className="text-sm text-[#374151] mb-3">
                  {open > 0 ? <strong>{open} still awaiting an HR or Finance signature.</strong>
                            : <span>All signed off. Payslips are released.</span>}
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs uppercase text-[#6B7280] border-b">
                        <th className="px-3 py-2">Company</th>
                        <th className="px-3 py-2 text-right">People</th>
                        <th className="px-3 py-2 text-right">Net pay</th>
                        <th className="px-3 py-2">Sign-off</th>
                        <th className="px-3 py-2 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map(r => (
                        <tr key={r.company_id} className="border-b last:border-0 align-top">
                          <td className="px-3 py-3 font-semibold text-[#0D1B2A]">{r.company}</td>
                          <td className="px-3 py-3 text-right">{r.live_headcount}</td>
                          <td className="px-3 py-3 text-right font-mono font-semibold">{pula(r.live_net)}</td>
                          <td className="px-3 py-3 space-y-1">
                            {r.closed ? (
                              <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-1 rounded"
                                    style={{ background: '#D1FAE5', color: '#065F46' }}>
                                <CheckCircle2 className="w-3.5 h-3.5" /> Released
                              </span>
                            ) : (
                              <div className="flex flex-col gap-1 items-start">
                                <Leg label="HR" signedBy={r.hr_signed_by} at={r.hr_signed_at} />
                                <Leg label="Finance" signedBy={r.fin_signed_by} at={r.fin_signed_at} />
                              </div>
                            )}
                            {r.rejection_reason && (
                              <p className="text-xs text-[#991B1B]">Sent back: {r.rejection_reason}</p>
                            )}
                            {r.drift && (
                              <p className="text-xs text-[#92400E] flex items-start gap-1">
                                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                                Figures changed — both sign again.
                              </p>
                            )}
                          </td>
                          <td className="px-3 py-3 text-right whitespace-nowrap space-x-2">
                            {canHr && r.needs_hr && r.live_headcount > 0 && (
                              <Button size="sm" variant={isBackstop ? 'outline' : undefined}
                                      disabled={busy === r.company_id}
                                      title={isBackstop ? 'Routine HR signer is Unami or Dorothy — sign only as a stand-in if they are away' : undefined}
                                      onClick={() => act(`HR signed ${r.company} ${r.period}.`,
                                        () => signPayroll(r.period, r.company_id, board?.my_side === 'both' ? 'hr' : undefined),
                                        r.company_id)}>
                                <CheckCircle2 className="w-4 h-4 mr-1" /> {isBackstop ? 'Sign as HR (stand-in)' : 'Sign (HR)'}
                              </Button>
                            )}
                            {canFin && r.needs_fin && r.live_headcount > 0 && (
                              <Button size="sm" variant={isBackstop ? 'outline' : undefined}
                                      disabled={busy === r.company_id}
                                      title={isBackstop ? 'Routine Finance signer is Kago or Pako — sign only as a stand-in if they are away' : undefined}
                                      onClick={() => act(`Finance signed ${r.company} ${r.period}.`,
                                        () => signPayroll(r.period, r.company_id, board?.my_side === 'both' ? 'finance' : undefined),
                                        r.company_id)}>
                                <CheckCircle2 className="w-4 h-4 mr-1" /> {isBackstop ? 'Sign as Finance (stand-in)' : 'Sign (Finance)'}
                              </Button>
                            )}
                            {(canHr || canFin) && !r.closed && (r.hr_signed_by || r.fin_signed_by) && (
                              <Button size="sm" variant="outline"
                                      onClick={() => { setRejectFor(r); setReason('') }}>
                                Send back
                              </Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {rejectFor && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg p-5 w-full max-w-md space-y-3">
            <h2 className="font-bold text-[#0D1B2A]">Send {rejectFor.company} {rejectFor.period} back</h2>
            <p className="text-sm text-[#6B7280]">Say what needs fixing. Both signatures drop and it goes back to the payroll team.</p>
            <textarea className="w-full border rounded px-3 py-2 text-sm min-h-24"
                      value={reason} onChange={e => setReason(e.target.value)}
                      placeholder="e.g. Overtime looks wrong for two people in Claims." />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setRejectFor(null)}>Cancel</Button>
              <Button size="sm" disabled={!reason.trim() || busy === rejectFor.company_id}
                      onClick={async () => {
                        const r = rejectFor; setRejectFor(null)
                        await act(`${r.company} ${r.period} sent back.`,
                                  () => rejectPayrollSignOff(r.period, r.company_id, reason), r.company_id)
                      }}>
                Send back
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
