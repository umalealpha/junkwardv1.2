'use client'

/** Request an HR letter from the phone. Drives the SAME HRIS endpoints as the
 * desktop /hris/letters page (frontend/src/app/(dashboard)/hris/letters/page.tsx);
 * backend hris/letter_views.py:
 *   GET  /hris/api/letters/            my letters + the letter types the server offers
 *   POST /hris/api/letters/            { letter_type, purpose, addressee } — manager/HR signs it off
 *   GET  /hris/api/letters/<id>/pdf/   the issued letter (only once signed) */
import { useCallback, useEffect, useState } from 'react'
import { FileText, Send } from 'lucide-react'
import { hfetch, reauthOn401 } from '@/app/(customer)/api'
import { C } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, StatusPill, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, openHrisFile, primaryBtn, rawHrisFetch,
} from './StaffFormKit'

interface Letter {
  id: string; letter_type: string; letter_type_label: string; addressee: string; purpose: string
  status: 'pending' | 'issued' | 'declined'; status_label: string; reference: string; signatory_name: string
  decline_reason: string; created_at: string; can_download: boolean; pdf_url: string
}
interface LettersResp { mine: Letter[]; letter_types: { value: string; label: string }[] }
const DEFAULT_ADDRESSEE = 'To Whom It May Concern'
const fmtDate = (iso: string) => { try { return new Date(iso).toLocaleDateString('en-BW', { day: '2-digit', month: 'short', year: 'numeric' }) } catch { return iso } }

export default function LetterRequestScreen() {
  const base = useStaffBase()
  const [data, setData] = useState<LettersResp | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [letterType, setLetterType] = useState('')
  const [addressee, setAddressee] = useState(DEFAULT_ADDRESSEE)
  const [purpose, setPurpose] = useState('')
  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null)
    hfetch<LettersResp>('/letters/')
      .then(d => { setData(d); setLetterType(cur => cur || d.letter_types?.[0]?.value || 'employment_confirmation') })
      .catch(e => { if (!reauthOn401(e)) { setData(d => d ?? { mine: [], letter_types: [] }); setLoadErr(errText(e, 'Could not load your letters.')) } })
  }, [])
  useEffect(() => { load() }, [load])

  const pendingSameType = (data?.mine || []).some(l => l.status === 'pending' && l.letter_type === letterType)

  async function request() {
    setServerErr(null)
    setBusy(true)
    try {
      // Same body the desktop sends, plus the addressee the endpoint accepts (defaults server-side).
      const r = await rawHrisFetch('/letters/', { method: 'POST', body: JSON.stringify({
        letter_type: letterType || 'employment_confirmation', purpose: purpose.trim().slice(0, 200),
        addressee: (addressee.trim() || DEFAULT_ADDRESSEE).slice(0, 200),
      }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      // 200 = an identical request is already pending (the server does not stack them); 201 = new.
      show(r.status === 200 ? 'You already have this letter awaiting sign-off.' : 'Request sent to your manager for sign-off. ✅')
      setPurpose(''); setAddressee(DEFAULT_ADDRESSEE)
      load()
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not send the request.')) }
    finally { setBusy(false) }
  }

  async function openPdf(l: Letter) {
    try { await openHrisFile(l.pdf_url || `/hris/api/letters/${l.id}/pdf/`) }
    catch (e) { if (!reauthOn401(e)) show(errText(e, 'Could not open the PDF.')) }
  }

  return (
    <ScreenFrame title="Request a letter" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 40, height: 40, borderRadius: 12, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><FileText size={20} style={{ color: C.orange }} /></div>
          <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>Omni fills the letter from your payroll record. Your manager (or HR) signs it off before it is issued on the letterhead.</p>
        </div>
        <label htmlFor="letter-type" style={labelStyle}>Letter type</label>
        <select id="letter-type" value={letterType} onChange={e => setLetterType(e.target.value)} style={inputStyle} disabled={!data}>
          {!data && <option value="">Loading…</option>}
          {data && data.letter_types.length === 0 && <option value="employment_confirmation">Employment confirmation</option>}
          {data?.letter_types.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <label htmlFor="letter-addressee" style={labelStyle}>Addressed to</label>
        <input id="letter-addressee" value={addressee} onChange={e => setAddressee(e.target.value)} maxLength={200} placeholder={DEFAULT_ADDRESSEE} style={inputStyle} />
        <label htmlFor="letter-purpose" style={labelStyle}>What is it for? (optional)</label>
        <input id="letter-purpose" value={purpose} onChange={e => setPurpose(e.target.value)} maxLength={200} placeholder="e.g. a home loan application" style={inputStyle} />
        {pendingSameType && <p style={{ margin: '10px 0 0', fontSize: 12.5, color: '#B45309' }}>You already have this letter awaiting sign-off — it will appear below once issued.</p>}
        {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
        <button onClick={request} disabled={busy || pendingSameType || !data} style={{ ...primaryBtn(busy || pendingSameType || !data), marginTop: 14 }}>
          <Send size={16} /> {busy ? 'Sending…' : pendingSameType ? 'Request pending' : 'Request letter'}
        </button>
      </Card>

      <b style={{ color: C.ink, fontSize: 15 }}>My letters</b>
      {data && data.mine.length === 0 && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>No letters yet.</p>}
      {data?.mine.map(l => (
        <Card key={l.id} style={{ padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <b style={{ color: C.ink, fontSize: 14.5 }}>{l.letter_type_label}{l.reference ? ` · ${l.reference}` : ''}</b>
            <StatusPill status={l.status} label={l.status_label} />
          </div>
          <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.inkSoft }}>
            Requested {fmtDate(l.created_at)}{l.purpose ? ` · ${l.purpose}` : ''}
            {l.status === 'issued' && l.signatory_name ? ` · signed by ${l.signatory_name}` : ''}
          </p>
          {l.status === 'declined' && l.decline_reason && <p style={{ color: '#B91C1C', fontSize: 12.5, margin: '6px 0 0' }}>↩ {l.decline_reason}</p>}
          {l.can_download && <button onClick={() => openPdf(l)} style={{ ...ghostBtn, marginTop: 10, color: '#B45309' }}>Open PDF</button>}
        </Card>
      ))}
      <Toast text={toast} />
    </ScreenFrame>
  )
}
