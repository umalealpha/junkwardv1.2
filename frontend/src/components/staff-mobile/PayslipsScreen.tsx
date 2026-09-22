'use client'

/** /m/staff/payslips — your OWN payslips on your phone. Tap a month for the full
 * breakdown; download the PDF. Figures only; the payroll register stays locked to
 * Finance. A month that payroll has not finalised yet shows "not finalised" rather
 * than a misleading zero card (CFO 2026-07-14, Bharath feedback). */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, FileText, ChevronDown, Download } from 'lucide-react'
import { sfetch, sfetchBlob } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { hasSlipDetail, slipLines } from './payslipDetail'

interface SlipLine { label: string; amount: string }
interface Slip {
  id: string; period: string; company: string; gross: string; paye: string; net: string
  currency: string; status: string; available: boolean
  /** Optional in practice: a payslip that arrives without it must still render. */
  detail?: { earnings?: SlipLine[]; deductions?: SlipLine[] }
}

const money = (v: string) => Number(v).toLocaleString(undefined, { minimumFractionDigits: 2 })

export default function StaffPayslips() {
  const base = useStaffBase()
  const [slips, setSlips] = useState<Slip[] | null>(null)
  const [note, setNote] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [dl, setDl] = useState<string | null>(null)
  const [toast, setToast] = useState('')

  useEffect(() => {
    sfetch<{ payslips: Slip[]; detail?: string }>('/payroll/my-payslips/')
      .then(d => { setSlips(d.payslips); if (!d.payslips.length) setNote(d.detail || 'No payslips yet.') })
      .catch(e => { setSlips([]); setNote(e instanceof Error ? e.message : 'Could not load.') })
  }, [])

  async function download(s: Slip) {
    if (dl) return
    setDl(s.id)
    try {
      const blob = await sfetchBlob(`/payslips/${s.id}/pdf/`)
      const url = URL.createObjectURL(blob)
      // Open the PDF in a new view. iOS WKWebView ignores <a download>, but it
      // DOES open a blob URL in its PDF viewer (where the user can share/save);
      // desktop/Android honour the download attribute (CFO 2026-07-14 fix).
      const opened = window.open(url, '_blank')
      if (!opened) {
        const a = document.createElement('a')
        a.href = url; a.download = `payslip_${s.period}.pdf`
        document.body.appendChild(a); a.click(); a.remove()
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) {
      setToast(e instanceof Error ? e.message : 'Could not open the payslip.')
      setTimeout(() => setToast(''), 4000)
    } finally { setDl(null) }
  }

  const tile = (l: string, v: string, col: string, cur: string) => (
    <div key={l} style={{ flex: 1, background: '#F6F7F9', borderRadius: 12, padding: '10px 4px', textAlign: 'center' }}>
      <p style={{ margin: 0, fontSize: 10.5, color: C.inkSoft, letterSpacing: '0.05em' }}>{l}</p>
      <p style={{ margin: '3px 0 0', fontWeight: 800, fontSize: 14.5, color: col, fontVariantNumeric: 'tabular-nums' }}>{cur} {money(v)}</p>
    </div>
  )

  const detailRow = (label: string, amount: string, cur: string, sign: string, col: string) => (
    <div key={label + amount} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid #EEF0F3', fontSize: 13 }}>
      <span style={{ color: C.ink }}>{label}</span>
      <span style={{ color: col, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{sign}{cur} {money(amount)}</span>
    </div>
  )

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>My payslips</h1>
      </header>
      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {slips === null && <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>Loading…</p>}
        {slips !== null && slips.length === 0 && (
          <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>{note}</p>)}

        {(slips || []).map(s => {
          const isOpen = open === s.id
          const hasDetail = hasSlipDetail(s.detail)
          const lines = slipLines(s.detail)
          return (
            <div key={s.id} style={{ ...card, padding: 18 }}>
              {/* header (tap to expand when finalised) */}
              <button
                type="button"
                onClick={() => hasDetail && setOpen(isOpen ? null : s.id)}
                aria-expanded={hasDetail ? isOpen : undefined}
                style={{ all: 'unset', cursor: hasDetail ? 'pointer' : 'default', display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', minHeight: 44 }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 800, color: C.ink, fontSize: 16 }}>
                  <FileText size={16} style={{ color: C.orange }} /> {s.period}
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: C.inkSoft }}>
                  {s.company}
                  {hasDetail && <ChevronDown size={16} style={{ transform: isOpen ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }} />}
                </span>
              </button>

              {!s.available ? (
                <p style={{ margin: '12px 0 0', fontSize: 13, color: C.inkSoft, background: '#F6F7F9', borderRadius: 12, padding: '10px 12px' }}>
                  Not finalised yet — your payslip for this month appears here once payroll has been run.
                </p>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
                    {tile('Gross', s.gross, C.ink, s.currency)}
                    {tile('Tax (PAYE)', s.paye, '#B91C1C', s.currency)}
                    {tile('Take-home', s.net, '#047857', s.currency)}
                  </div>

                  {isOpen && (
                    <div style={{ marginTop: 14 }}>
                      {lines.earnings.length > 0 && (
                        <>
                          <p style={{ margin: '0 0 4px', fontSize: 11, fontWeight: 800, color: C.inkSoft, letterSpacing: '0.06em' }}>EARNINGS</p>
                          {lines.earnings.map(l => detailRow(l.label, l.amount, s.currency, '', C.ink))}
                        </>
                      )}
                      {lines.deductions.length > 0 && (
                        <>
                          <p style={{ margin: '12px 0 4px', fontSize: 11, fontWeight: 800, color: C.inkSoft, letterSpacing: '0.06em' }}>DEDUCTIONS</p>
                          {lines.deductions.map(l => detailRow(l.label, l.amount, s.currency, '-', '#B91C1C'))}
                        </>
                      )}
                      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0 0', fontSize: 14, fontWeight: 800 }}>
                        <span style={{ color: C.ink }}>Take-home</span>
                        <span style={{ color: '#047857', fontVariantNumeric: 'tabular-nums' }}>{s.currency} {money(s.net)}</span>
                      </div>
                    </div>
                  )}

                  <button
                    type="button"
                    onClick={() => download(s)}
                    disabled={dl === s.id}
                    style={{ marginTop: 14, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: C.navy, color: '#fff', border: 'none', borderRadius: 12, padding: '11px 0', fontSize: 14, fontWeight: 700, cursor: 'pointer', opacity: dl === s.id ? 0.6 : 1 }}
                  >
                    <Download size={16} /> {dl === s.id ? 'Preparing…' : 'Download PDF'}
                  </button>
                </>
              )}
            </div>
          )
        })}

        {(slips || []).length > 0 && (
          <p style={{ textAlign: 'center', color: C.inkSoft, fontSize: 12 }}>Only you can see these. Questions → Finance.</p>)}
      </main>
      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center', fontFamily: sans }}>
          {toast}
        </div>)}
    </div>
  )
}
