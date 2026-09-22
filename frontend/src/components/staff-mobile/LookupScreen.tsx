'use client'

/** /app/lookup · /m/staff/lookup — look a client up in Graphite from the phone
 * (CFO 4-Sep-2026). Type a claim number, a policy number or a name; see the
 * claims and policies that match with their status; tap a policy for its card.
 *   GET /graphite/search/?q=        (integrations/graphite_lookup_views.py — read-only replica)
 *   GET /graphite/policy/?policy=
 * Read-only. No Omang, phone, email, address or bank detail is ever returned. */
import { useEffect, useState } from 'react'
import { ChevronRight, Search, Sparkles } from 'lucide-react'
import { sfetch, reauthOn401, ApiError } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { Card, ScreenFrame, ServerMessage, ghostBtn, inputStyle, labelStyle, primaryBtn, errText, formatServerErrors, rawStaffFetch } from './StaffFormKit'

interface Claim { claim_number: string; policy_number: string | null; customer_name: string; status: string; status_label: string; claim_type: string | null; type_of_loss?: string | null; vehicle_plate?: string | null; reported: string | null; date_of_loss: string | null; paid: string | null; outstanding: string | null }
interface Policy { policy_number: string; customer_name: string; business_name: string; status: number | null; status_label: string; premium: string | null; annual_premium: string | null; premium_freq: string; term_start: string | null; term_end: string | null; product: string; agent: string; broker: string }
interface PolicyCard extends Policy { claims: Claim[]; kyc_status: string | null; balance: string | null }
interface Results { q: string; claims: Claim[]; policies: Policy[] }

const bwp = (s: string | null) => s === null || s === undefined ? '—' : `BWP ${Number(s).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const dmy = (iso: string | null) => iso ? new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—'
const claimTone = (s: string) => /approved|closed/i.test(s) ? '#047857' : /reject/i.test(s) ? '#B91C1C' : '#B45309'
const policyTone = (st: number | null) => st === 1 ? '#047857' : st === 2 ? '#B91C1C' : C.inkSoft

export default function LookupScreen() {
  const base = useStaffBase()
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Results | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [card, setCard] = useState<PolicyCard | null>(null)
  const [openClaim, setOpenClaim] = useState<string | null>(null)

  useEffect(() => {
    const term = q.trim()
    if (term.length < 3) { setRes(null); return }
    const t = window.setTimeout(() => {
      setBusy(true); setErr(null)
      sfetch<Results>(`/graphite/search/?q=${encodeURIComponent(term)}`)
        .then(r => setRes(r))
        .catch(e => { if (!reauthOn401(e)) { setRes(null); setErr(e instanceof ApiError && e.status === 403 ? 'You do not have access to client lookups.' : errText(e, 'Graphite did not answer. Try again.')) } })
        .finally(() => setBusy(false))
    }, 400)
    return () => window.clearTimeout(t)
  }, [q])

  const openPolicy = async (no: string) => {
    setBusy(true); setErr(null)
    try { setCard(await sfetch<PolicyCard>(`/graphite/policy/?policy=${encodeURIComponent(no)}`)) }
    catch (e) { if (!reauthOn401(e)) setErr(errText(e, 'Could not open that policy.')) }
    finally { setBusy(false) }
  }

  if (card) return (
    <ScreenFrame title="Policy" base={base}>
      <button onClick={() => setCard(null)} style={{ ...ghostBtn, alignSelf: 'flex-start' }}>← Back to results</button>
      <Card style={{ padding: 16 }}>
        <p style={{ margin: 0, fontSize: 12, color: C.inkSoft, letterSpacing: '0.06em', textTransform: 'uppercase', fontWeight: 700 }}>{card.policy_number}</p>
        <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 22, color: C.ink, margin: '4px 0 6px' }}>{card.customer_name || card.business_name || 'Unnamed'}</h2>
        <span style={{ fontSize: 13, fontWeight: 800, color: policyTone(card.status) }}>{card.status_label}</span>
        <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px', margin: '14px 0 0', fontSize: 13.5 }}>
          <Fact k="Premium" v={`${bwp(card.premium)}${card.premium_freq ? ` · ${card.premium_freq}` : ''}`} />
          <Fact k="Annual premium" v={bwp(card.annual_premium)} />
          <Fact k="Term" v={`${dmy(card.term_start)} → ${dmy(card.term_end)}`} />
          <Fact k="Balance owing" v={card.balance === null ? 'Not in the data' : bwp(card.balance)} />
          <Fact k="KYC" v={card.kyc_status || 'Not recorded'} />
          <Fact k="Product" v={card.product || '—'} />
          <Fact k="Broker / agent" v={[card.broker, card.agent].filter(Boolean).join(' · ') || 'Direct'} />
        </dl>
      </Card>
      <b style={{ color: C.ink, fontSize: 15 }}>Claims on this policy ({card.claims.length})</b>
      {card.claims.length === 0 && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>No claims recorded.</p>}
      {card.claims.map(c => <ClaimRow key={c.claim_number} c={c} open={openClaim === c.claim_number} onToggle={() => setOpenClaim(o => o === c.claim_number ? null : c.claim_number)} />)}
    </ScreenFrame>
  )

  return (
    <ScreenFrame title="Look up a client" base={base}>
      <label style={{ ...labelStyle, marginTop: 0 }} htmlFor="lk-q">Claim number, policy number or name</label>
      <div style={{ position: 'relative' }}>
        <input id="lk-q" value={q} onChange={e => setQ(e.target.value)} placeholder="G2026004594 · COMG2025… · Molapo" autoFocus autoComplete="off" style={{ ...inputStyle, paddingRight: 44, fontSize: 16 }} />
        <Search size={18} color={C.inkSoft} style={{ position: 'absolute', right: 14, top: 13 }} />
      </div>
      <p style={{ margin: 0, fontSize: 12.5, color: C.inkSoft }}>Straight from Graphite, read-only. {busy ? 'Searching…' : res ? `${res.claims.length} claim${res.claims.length === 1 ? '' : 's'} · ${res.policies.length} polic${res.policies.length === 1 ? 'y' : 'ies'}` : 'Type at least 3 characters.'}</p>
      {err && <ServerMessage tone="error" text={err} />}

      {res && res.claims.length > 0 && (
        <>
          <b style={{ color: C.ink, fontSize: 15 }}>Claims</b>
          {res.claims.map(c => <ClaimRow key={c.claim_number} c={c} open={openClaim === c.claim_number} onToggle={() => setOpenClaim(o => o === c.claim_number ? null : c.claim_number)} onPolicy={openPolicy} />)}
        </>
      )}
      {res && res.policies.length > 0 && (
        <>
          <b style={{ color: C.ink, fontSize: 15 }}>Policies</b>
          {res.policies.map(p => (
            <Card key={p.policy_number} style={{ padding: 0 }}>
              <button onClick={() => openPolicy(p.policy_number)} style={{ width: '100%', minHeight: 56, textAlign: 'left', background: 'none', border: 'none', padding: 14, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ color: C.ink, fontSize: 15, display: 'block' }}>{p.customer_name || p.business_name || 'Unnamed'}</b>
                  <span style={{ color: C.inkSoft, fontSize: 12.5 }}>{p.policy_number}{p.product ? ` · ${p.product}` : ''} · {bwp(p.premium)}{p.premium_freq ? ` ${p.premium_freq.toLowerCase()}` : ''} · to {dmy(p.term_end)}</span>
                </span>
                <span style={{ fontSize: 12.5, fontWeight: 800, color: policyTone(p.status), whiteSpace: 'nowrap' }}>{p.status_label}</span>
                <ChevronRight size={18} color={C.inkSoft} />
              </button>
            </Card>
          ))}
        </>
      )}
      {res && res.claims.length === 0 && res.policies.length === 0 && !busy && (
        <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', margin: '16px 0' }}>Nothing in Graphite matches “{res.q}”.</p>
      )}
    </ScreenFrame>
  )
}

function Fact({ k, v }: { k: string; v: string }) {
  return (<div><dt style={{ color: C.inkSoft, fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{k}</dt><dd style={{ margin: '2px 0 0', color: C.ink, fontWeight: 600 }}>{v}</dd></div>)
}

function ClaimRow({ c, open, onToggle, onPolicy }: { c: Claim; open: boolean; onToggle: () => void; onPolicy?: (no: string) => void }) {
  return (
    <Card style={{ padding: 0 }}>
      <button onClick={onToggle} aria-expanded={open} style={{ width: '100%', minHeight: 56, textAlign: 'left', background: 'none', border: 'none', padding: 14, cursor: 'pointer' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <b style={{ color: C.ink, fontSize: 15, flex: 1 }}>{c.claim_number}</b>
          <span style={{ fontSize: 12.5, fontWeight: 800, color: claimTone(c.status), whiteSpace: 'nowrap' }}>{c.status_label}</span>
        </div>
        <span style={{ color: C.inkSoft, fontSize: 12.5 }}>{c.customer_name || 'Unnamed'}{c.policy_number ? ` · ${c.policy_number}` : ''} · reported {dmy(c.reported)}</span>
      </button>
      {open && (
        <div style={{ padding: '0 14px 14px', borderTop: `1px solid ${C.line}` }}>
          <dl style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px', margin: '12px 0 0', fontSize: 13.5 }}>
            <Fact k="Type" v={[c.claim_type, c.type_of_loss].filter(Boolean).join(' · ') || '—'} />
            {c.vehicle_plate ? <Fact k="Vehicle" v={c.vehicle_plate} /> : null}
            <Fact k="Date of loss" v={dmy(c.date_of_loss)} />
            <Fact k="Reported" v={dmy(c.reported)} />
            <Fact k="Paid so far" v={c.paid === null ? 'Not in the data' : bwp(c.paid)} />
            <Fact k="Still reserved" v={c.outstanding === null ? 'Not in the data' : bwp(c.outstanding)} />
          </dl>
          {onPolicy && c.policy_number && <button onClick={() => onPolicy(c.policy_number!)} style={{ ...ghostBtn, marginTop: 12 }}>Open policy {c.policy_number}</button>}
          <AskBox claim={c.claim_number} />
        </div>
      )}
    </Card>
  )
}

/** "Ask DeepSeek about this claim" (CFO 5-Sep-2026): the server gathers the claim,
 * policy, money, coverage lines and Omni payment requests, masks people to
 * initials, and asks the model for a ~200-word brief in the /largepayment shape.
 * POST /graphite/claim-brief/ {claim_number, question?} */
function AskBox({ claim }: { claim: string }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [brief, setBrief] = useState<{ brief: string; engine: string; words: number } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const ask = async () => {
    setBusy(true); setErr(null); setBrief(null)
    try {
      const r = await rawStaffFetch('/graphite/claim-brief/', { method: 'POST', body: JSON.stringify({ claim_number: claim, question: q.trim() }) })
      if (!r.ok) { setErr(formatServerErrors(r.body, r.status)); return }
      setBrief(r.body as unknown as { brief: string; engine: string; words: number })
    } catch (e) { if (!reauthOn401(e)) setErr(errText(e, 'The reviewer did not answer.')) }
    finally { setBusy(false) }
  }
  // Render the model's **bold** headings as bold; everything else as plain lines.
  const render = (t: string) => t.split(/\n+/).map((line, i) => (
    <p key={i} style={{ margin: '0 0 6px', fontSize: 13.5, lineHeight: 1.55, color: C.ink }}>
      {line.split(/(\*\*[^*]+\*\*)/g).map((part, j) => part.startsWith('**') && part.endsWith('**') ? <b key={j}>{part.slice(2, -2)}</b> : <span key={j}>{part}</span>)}
    </p>
  ))
  return (
    <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px dashed ${C.line}` }}>
      <label style={{ ...labelStyle, marginTop: 0 }} htmlFor={`ask-${claim}`}>Ask about this claim (optional)</label>
      <input id={`ask-${claim}`} value={q} onChange={e => setQ(e.target.value)} placeholder="e.g. Should we pay the repairer's invoice?" style={inputStyle} />
      <button onClick={ask} disabled={busy} style={{ ...primaryBtn(busy), marginTop: 10, minHeight: 44 }}>
        <Sparkles size={16} /> {busy ? 'Reviewing the claim…' : 'Get a 200-word review'}
      </button>
      {err && <div style={{ marginTop: 8 }}><ServerMessage tone="error" text={err} /></div>}
      {brief && (
        <div style={{ marginTop: 10, background: '#FFFBEB', border: '1px solid #FCD34D', borderRadius: 12, padding: '12px 14px' }}>
          {render(brief.brief)}
          <p style={{ margin: '8px 0 0', fontSize: 11.5, color: C.inkSoft }}>{brief.engine} · {brief.words} words · from the facts in Graphite and Omni; people shown to the reviewer as initials. A view, not a decision.</p>
        </div>
      )}
    </div>
  )
}
