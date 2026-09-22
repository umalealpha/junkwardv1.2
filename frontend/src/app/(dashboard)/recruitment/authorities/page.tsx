'use client'

/**
 * /recruitment/authorities — Authority to Recruit (CFO 2026-08-03).
 *
 * The formal paper that authorises filling a role at a stated package, signed by
 * the CEO, COO, Human Capital (x2) and the CFO. Restricted to those five: the
 * server returns 403 to anyone else, and this page says so plainly rather than
 * showing an empty list (an empty list reads as "nothing to do", which is a
 * different and misleading message).
 *
 * Two figures are always shown side by side — what HR quoted, and what the role
 * actually costs. HR's own workbooks understated the monthly cost by deducting
 * the employee's provident-fund contribution from the company's cost, so a
 * signatory must never see only the quoted number.
 *
 * Design: Finance brand (navy #0D1B2A / orange #F4A623 / Book Antiqua).
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { apiFetch, apiFetchRaw } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { FileSignature, Download, Check, X, Lock, AlertTriangle, Plus, Layers } from 'lucide-react'

const NAVY = '#0D1B2A', ORANGE = '#F4A623', MUT = '#6B7280', HAIR = '#ECEEF2'
const RED = '#B42318', GREEN = '#1B7A3D'
const SERIF = "'Book Antiqua', 'Palatino Linotype', Palatino, Georgia, serif"
const card: React.CSSProperties = {
  background: '#fff', borderRadius: 18,
  boxShadow: '0 1px 2px rgba(13,27,42,.04), 0 12px 30px rgba(13,27,42,.06)',
  border: `1px solid ${HAIR}`,
}
const eyebrow: React.CSSProperties = {
  fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: MUT, fontWeight: 700,
}

interface Approval { decision?: string; by?: string; at?: string; notes?: string }
interface Signatory { slug: string; label: string }
interface Line {
  item?: string; monthly?: string | number; annual?: string | number; note?: string
}
interface Authority {
  id: string; reference: string; kind: string; kind_label: string
  person_name: string; entity: string; position: string; department: string; level: string
  employment_type: string; headcount: number; effective_date: string | null
  currency: string
  quoted_ctc_monthly: string; quoted_ctc_annual: string
  cost_to_company_monthly: string; cost_to_company_annual: string
  variance_monthly: string; variance_annual: string
  status: string; status_label: string
  outstanding: string[]; signatories: Signatory[]; approvals: Record<string, Approval>
  justification?: string; salary_lines?: Line[]
  can_sign?: boolean; cannot_sign_reason?: string
  created_at: string
  can_convert?: boolean
  converted_employee_name?: string | null
  converted_at?: string | null
  // Position tier + basic-salary band (Unami Hiring-SOP).
  tier?: number | null; tier_name?: string | null
  proposed_basic_salary?: string
  salary_band_min?: string | null; salary_band_max?: string | null
  is_salary_exception?: boolean
  exception_signers?: Signatory[]
  hiring_manager_name?: string; hiring_manager_email?: string
  my_slug?: string; mine?: boolean
}

const money = (ccy: string, v: string | number | undefined) => {
  const n = Number(v ?? 0)
  return `${ccy} ${(Number.isFinite(n) ? n : 0).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function AuthoritiesPage() {
  const [rows, setRows] = useState<Authority[]>([])
  const [mySlug, setMySlug] = useState('')
  const [denied, setDenied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Authority | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [declineFor, setDeclineFor] = useState<Authority | null>(null)
  const [reason, setReason] = useState('')

  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 5000) }

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await apiFetch<{ my_signature_slug: string; authorities: Authority[] }>(
        '/recruitment/authorities/')
      setRows(d.authorities || [])
      setMySlug(d.my_signature_slug || '')
      setDenied(false)
    } catch (e) {
      // 403 is the designed answer for anyone who is not a signatory.
      const msg = e instanceof Error ? e.message : ''
      if (/restricted|permission|403/i.test(msg)) setDenied(true)
      else show(msg || 'Could not load.')
    } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  async function openDetail(a: Authority) {
    try {
      const full = await apiFetch<Authority>(`/recruitment/authorities/${a.id}/`)
      setOpen(full)
    } catch (e) { show(e instanceof Error ? e.message : 'Could not open.') }
  }

  async function sign(a: Authority, decision: 'approve' | 'decline', notes = '') {
    setBusy(true)
    try {
      await apiFetch(`/recruitment/authorities/${a.id}/sign/`, {
        method: 'POST', body: JSON.stringify({ decision, notes }),
      })
      show(decision === 'approve' ? 'Signed. ✅' : 'Declined — the others have been left as they are.')
      setOpen(null); setDeclineFor(null); setReason('')
      load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not record that.') }
    finally { setBusy(false) }
  }

  async function convert(a: Authority) {
    setBusy(true)
    try {
      const r = await apiFetch<{ employee_name?: string }>(
        `/recruitment/authorities/${a.id}/convert/`, { method: 'POST' })
      show(`Employee created — ${r.employee_name || a.person_name}. Onboarding started; IT will set up the login. ✅`)
      setOpen(null)
      load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not create the employee.') }
    finally { setBusy(false) }
  }

  async function download(a: Authority) {
    try {
      const res = await apiFetchRaw(`/recruitment/authorities/${a.id}/document/`)
      if (!res.ok) throw new Error(`Download failed (${res.status})`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const el = document.createElement('a')
      el.href = url
      el.download = `${a.kind === 'regrade' ? 'Authority_to_Regrade' : 'Authority_to_Recruit'}-${a.person_name.replace(/ /g, '_')}-${a.reference}.docx`
      el.click()
      setTimeout(() => URL.revokeObjectURL(url), 10000)
    } catch (e) { show(e instanceof Error ? e.message : 'Could not download.') }
  }

  if (denied) {
    return (
      <div style={{ fontFamily: SERIF, color: NAVY }}>
        <TopBar title="Authority to Recruit" />
        <div style={{ maxWidth: 620, margin: '48px auto', padding: 24, ...card, textAlign: 'center' }}>
          <Lock size={28} color={MUT} />
          <h2 style={{ fontSize: 20, margin: '12px 0 8px' }}>Not available to you</h2>
          <p style={{ color: MUT, fontSize: 14, lineHeight: 1.6, margin: 0 }}>
            These papers carry a named person&rsquo;s full pay, so each is restricted to its own
            signatories. If you believe you should see these, speak to the CFO or Human Capital.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div style={{ fontFamily: SERIF, color: NAVY, paddingBottom: 60 }}>
      <TopBar title="Authority to Recruit" />

      <div style={{ maxWidth: 1120, margin: '0 auto', padding: '20px 20px 0' }}>
        <div style={eyebrow}>Human Capital</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 27, margin: '4px 0 4px', letterSpacing: '-.01em' }}>
            Authority to Recruit
          </h1>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Link href="/recruitment/tiers" style={{ ...btn('ghost'), textDecoration: 'none' }}>
              <Layers size={15} /> Tiers &amp; bands
            </Link>
            <Link href="/recruitment/authorities/new" style={{ ...btn('primary'), textDecoration: 'none' }}>
              <Plus size={15} /> Raise an authority
            </Link>
          </div>
        </div>
        <p style={{ color: MUT, fontSize: 14, margin: '0 0 20px', maxWidth: 760, lineHeight: 1.6 }}>
          The signatures needed depend on the role&rsquo;s tier. An offer only goes out once every
          signatory shown on the paper is in. Papers carry a named person&rsquo;s pay, so each is
          visible only to its own signatories.
        </p>

        {loading && <p style={{ color: MUT, fontSize: 14 }}>Loading…</p>}
        {!loading && rows.length === 0 && (
          <div style={{ ...card, padding: 28, textAlign: 'center', color: MUT, fontSize: 14 }}>
            Nothing waiting for signature.
          </div>)}

        <div style={{ display: 'grid', gap: 14 }}>
          {rows.map(a => {
            const isRegrade = a.kind === 'regrade'
            const signedCount = a.signatories.length - a.outstanding.length
            const mine = a.mine ?? Boolean(mySlug && a.outstanding.includes(mySlug))
            const varM = Number(a.variance_monthly || 0)
            const gradeText = a.tier ? `Tier ${a.tier}` : a.level
            return (
              <div key={a.id} style={{ ...card, padding: 18 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
                  <div style={{ minWidth: 260, flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ ...eyebrow, color: isRegrade ? RED : MUT }}>
                        {isRegrade ? 'Regrade — existing employee' : 'Recruit — external'}
                      </span>
                      <span style={{ fontSize: 11, color: MUT }}>{a.reference}</span>
                      {a.is_salary_exception && (
                        <span style={pill('#FEF3E6', '#9A5B00')}>Above band — exception</span>)}
                    </div>
                    <div style={{ fontSize: 19, fontWeight: 700, margin: '4px 0 2px' }}>{a.person_name}</div>
                    <div style={{ fontSize: 14, color: MUT }}>
                      {a.position}{a.department ? ` · ${a.department}` : ''}{gradeText ? ` · ${gradeText}` : ''}
                      {a.tier_name && a.tier ? ` (${a.tier_name})` : ''}
                    </div>
                  </div>

                  <div style={{ textAlign: 'right', minWidth: 200 }}>
                    <div style={{ fontSize: 11, color: MUT }}>Cost of employment</div>
                    <div style={{ fontSize: 21, fontWeight: 700 }}>
                      {money(a.currency, a.cost_to_company_monthly)}<span style={{ fontSize: 12, color: MUT }}> /mo</span>
                    </div>
                    <div style={{ fontSize: 12, color: MUT, textDecoration: varM ? 'line-through' : 'none' }}>
                      quoted {money(a.currency, a.quoted_ctc_monthly)}
                    </div>
                  </div>
                </div>

                {!!varM && (
                  <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', background: '#FEF7F7',
                                border: `1px solid #F3D6D6`, borderRadius: 12, padding: '10px 12px', margin: '12px 0 0' }}>
                    <AlertTriangle size={15} color={RED} style={{ marginTop: 2, flexShrink: 0 }} />
                    <div style={{ fontSize: 13, color: NAVY, lineHeight: 1.55 }}>
                      Costs <b>{money(a.currency, Math.abs(varM))} a month more</b> than HR quoted — the
                      quote deducted the employee&rsquo;s own pension contribution from the company&rsquo;s
                      cost. Budget on the figure above.
                    </div>
                  </div>)}

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 14 }}>
                  <span style={{ fontSize: 13, color: MUT }}>
                    {signedCount} of {a.signatories.length} signed
                  </span>
                  <span style={{ fontSize: 13, fontWeight: 700,
                                 color: a.status === 'approved' ? GREEN : a.status === 'declined' ? RED : ORANGE }}>
                    · {a.status_label}
                  </span>
                  <div style={{ flex: 1 }} />
                  <button onClick={() => openDetail(a)} style={btn('ghost')}>
                    <FileSignature size={15} /> Read it
                  </button>
                  <button onClick={() => download(a)} style={btn('ghost')}>
                    <Download size={15} /> Word
                  </button>
                  {mine && (
                    <>
                      <button disabled={busy} onClick={() => sign(a, 'approve')} style={btn('primary')}>
                        <Check size={15} /> Sign
                      </button>
                      <button disabled={busy} onClick={() => { setDeclineFor(a); setReason('') }} style={btn('danger')}>
                        <X size={15} /> Decline
                      </button>
                    </>)}
                  {a.converted_employee_name && (
                    <span style={{ fontSize: 13, fontWeight: 700, color: GREEN }}>
                      ✓ Employee created — {a.converted_employee_name}
                    </span>)}
                  {a.can_convert && !a.converted_employee_name && (
                    <button disabled={busy} onClick={() => convert(a)} style={btn('primary')}>
                      <Check size={15} /> Create employee
                    </button>)}
                </div>
              </div>)
          })}
        </div>
      </div>

      {open && (
        <Sheet onClose={() => setOpen(null)} title={`${open.reference} — ${open.person_name}`}>
          {open.kind === 'regrade' && (
            <p style={{ color: RED, fontWeight: 700, fontSize: 13, margin: '0 0 12px' }}>
              This is a regrade of an existing employee, not an external appointment.
            </p>)}
          <Row k="Position" v={open.position} />
          <Row k="Department" v={open.department || '—'} />
          {open.tier
            ? <Row k="Tier" v={`Tier ${open.tier}${open.tier_name ? ` — ${open.tier_name}` : ''}`} />
            : <Row k="Grade / level" v={open.level || '—'} />}
          {open.tier && (open.salary_band_min || open.salary_band_max) && (
            <Row k="Basic-salary band (mo)"
              v={`${money(open.currency, open.salary_band_min || 0)} — ${money(open.currency, open.salary_band_max || 0)}`} />)}
          {open.tier && Number(open.proposed_basic_salary || 0) > 0 && (
            <Row k="Proposed basic (mo)" v={money(open.currency, open.proposed_basic_salary)} bold />)}
          {open.hiring_manager_name && <Row k="Hiring manager" v={open.hiring_manager_name} />}
          <Row k="Headcount" v={String(open.headcount)} />
          <Row k="Effective" v={open.effective_date || 'On acceptance'} />
          {open.is_salary_exception && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', background: '#FEF7ED',
                          border: '1px solid #F5D9AE', borderRadius: 12, padding: '10px 12px', margin: '12px 0 0' }}>
              <AlertTriangle size={15} color="#9A5B00" style={{ marginTop: 2, flexShrink: 0 }} />
              <div style={{ fontSize: 13, color: NAVY, lineHeight: 1.55 }}>
                The proposed basic salary is <b>above the tier ceiling</b>. It proceeds on the
                justification below and must be approved by{' '}
                <b>{(open.exception_signers || []).map(s => s.label).join(' and ') || 'the exception approvers'}</b>.
              </div>
            </div>)}
          <h4 style={{ margin: '18px 0 6px', fontSize: 14 }}>Why this role is needed</h4>
          <p style={{ fontSize: 13.5, lineHeight: 1.65, color: NAVY, margin: 0 }}>{open.justification || '—'}</p>

          <h4 style={{ margin: '18px 0 6px', fontSize: 14 }}>Salary and benefits</h4>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr style={{ background: '#FAFBFC' }}>
              <th style={th}>Item</th><th style={{ ...th, textAlign: 'right' }}>Monthly</th>
              <th style={{ ...th, textAlign: 'right' }}>Annual</th><th style={th}>Note</th>
            </tr></thead>
            <tbody>
              {(open.salary_lines || [])
                .filter(l => (l.item || '') && !(l.item || '').toLowerCase().startsWith('total'))
                .map((l, i) => (
                  <tr key={i}>
                    <td style={td}>{l.item}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{Number(l.monthly || 0).toLocaleString('en-GB', { minimumFractionDigits: 2 })}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{Number(l.annual || 0).toLocaleString('en-GB', { minimumFractionDigits: 2 })}</td>
                    <td style={{ ...td, color: MUT, fontSize: 12 }}>{l.note || ''}</td>
                  </tr>))}
            </tbody>
          </table>

          <h4 style={{ margin: '18px 0 6px', fontSize: 14 }}>What it costs</h4>
          <Row k="Quoted to the candidate (monthly)" v={money(open.currency, open.quoted_ctc_monthly)} />
          <Row k="True cost of employment (monthly)" v={money(open.currency, open.cost_to_company_monthly)} bold />
          <Row k="True cost of employment (annual)" v={money(open.currency, open.cost_to_company_annual)} />

          <h4 style={{ margin: '18px 0 6px', fontSize: 14 }}>Signatures</h4>
          {open.signatories.map(s => {
            const rec = open.approvals?.[s.slug] || {}
            const d = (rec.decision || '').toLowerCase()
            return (
              <div key={s.slug} style={{ display: 'flex', justifyContent: 'space-between', gap: 10,
                                         padding: '7px 0', borderBottom: `1px solid ${HAIR}`, fontSize: 13 }}>
                <span>{s.label}</span>
                <span style={{ fontWeight: 700, color: d === 'approved' ? GREEN : d === 'declined' ? RED : MUT }}>
                  {d === 'approved' ? `Signed — ${rec.by}` : d === 'declined' ? `Declined — ${rec.by}` : 'Outstanding'}
                </span>
              </div>)
          })}
          {open.cannot_sign_reason && (
            <p style={{ color: MUT, fontSize: 12.5, marginTop: 12 }}>{open.cannot_sign_reason}</p>)}
        </Sheet>)}

      {declineFor && (
        <Sheet onClose={() => setDeclineFor(null)} title={`Decline ${declineFor.reference}`}>
          <p style={{ fontSize: 13.5, color: MUT, margin: '0 0 10px' }}>
            A reason is required — it is recorded on the paper.
          </p>
          <textarea value={reason} onChange={e => setReason(e.target.value)} rows={4} autoFocus
            placeholder="Why are you declining?"
            style={{ width: '100%', boxSizing: 'border-box', padding: 12, borderRadius: 11,
                     border: `1px solid ${HAIR}`, fontSize: 14, fontFamily: SERIF, color: NAVY }} />
          <button disabled={busy || !reason.trim()}
            onClick={() => sign(declineFor, 'decline', reason.trim())}
            style={{ ...btn('danger'), marginTop: 12, width: '100%', justifyContent: 'center',
                     opacity: busy || !reason.trim() ? 0.5 : 1 }}>
            Record the decline
          </button>
        </Sheet>)}

      {toast && (
        <div style={{ position: 'fixed', bottom: 28, left: '50%', transform: 'translateX(-50%)',
                      background: NAVY, color: '#fff', padding: '11px 20px', borderRadius: 999,
                      fontSize: 13.5, zIndex: 80, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}

// ── small presentational helpers ─────────────────────────────────────────────
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', borderBottom: `1px solid ${HAIR}`, fontSize: 12, color: MUT, fontWeight: 700 }
const td: React.CSSProperties = { padding: '7px 10px', borderBottom: `1px solid ${HAIR}` }

function pill(bg: string, fg: string): React.CSSProperties {
  return { display: 'inline-block', padding: '2px 9px', borderRadius: 999, fontSize: 11,
           fontWeight: 700, background: bg, color: fg }
}

function btn(kind: 'primary' | 'ghost' | 'danger'): React.CSSProperties {
  const base: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 14px', borderRadius: 999,
    fontSize: 13, fontWeight: 700, cursor: 'pointer', fontFamily: SERIF, border: `1px solid ${HAIR}`,
  }
  if (kind === 'primary') return { ...base, background: NAVY, color: ORANGE, border: 'none' }
  if (kind === 'danger') return { ...base, background: '#fff', color: RED, borderColor: '#F3D6D6' }
  return { ...base, background: '#fff', color: NAVY }
}

function Row({ k, v, bold }: { k: string; v: string; bold?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '6px 0',
                  borderBottom: `1px solid ${HAIR}`, fontSize: 13.5 }}>
      <span style={{ color: MUT }}>{k}</span>
      <span style={{ fontWeight: bold ? 700 : 400, textAlign: 'right' }}>{v}</span>
    </div>)
}

function Sheet({ title, onClose, children }:
  { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div role="dialog" aria-modal="true" aria-label={title} onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(13,27,42,.5)', zIndex: 70,
               display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
      <div onClick={e => e.stopPropagation()}
        style={{ background: '#fff', borderRadius: 18, width: '100%', maxWidth: 660,
                 maxHeight: '88vh', overflowY: 'auto', padding: 22, fontFamily: SERIF, color: NAVY }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 14 }}>
          <b style={{ fontSize: 17 }}>{title}</b>
          <button onClick={onClose} aria-label="Close"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: MUT }}>
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>)
}
