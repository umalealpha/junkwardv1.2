'use client'

/**
 * /recruitment/authorities/new — raise an Authority to Recruit / Regrade
 * (Unami Hiring-SOP, CFO 2026-09-02).
 *
 * The entry form the queue never had. Pick the role's TIER and the signatories
 * follow automatically; enter the BASIC salary and it is checked against the
 * tier's band — over the ceiling, a written justification becomes mandatory
 * before the paper can be raised (below the floor is not flagged).
 *
 * Design: Finance brand (navy #0D1B2A / orange #F4A623 / Book Antiqua).
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { AlertTriangle, ArrowLeft, Lock } from 'lucide-react'

const NAVY = '#0D1B2A', ORANGE = '#F4A623', MUT = '#6B7280', HAIR = '#ECEEF2'
const RED = '#B42318', AMBERBG = '#FEF7ED', AMBERTX = '#9A5B00'
const SERIF = "'Book Antiqua', 'Palatino Linotype', Palatino, Georgia, serif"
const card: React.CSSProperties = {
  background: '#fff', borderRadius: 18,
  boxShadow: '0 1px 2px rgba(13,27,42,.04), 0 12px 30px rgba(13,27,42,.06)',
  border: `1px solid ${HAIR}`,
}

interface Sig { slug: string; label: string }
interface Tier {
  tier: number; name: string
  basic_salary_min: string | null; basic_salary_max: string | null
  standard_signatories: Sig[]; exception_signatories: Sig[]
}

const money = (v: string | number | null | undefined) => {
  const n = Number(v ?? 0)
  return `P ${(Number.isFinite(n) ? n : 0).toLocaleString('en-GB', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

export default function NewAuthorityPage() {
  const router = useRouter()
  const [tiers, setTiers] = useState<Tier[]>([])
  const [denied, setDenied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const [kind, setKind] = useState<'recruit' | 'regrade'>('recruit')
  const [personName, setPersonName] = useState('')
  const [position, setPosition] = useState('')
  const [department, setDepartment] = useState('')
  const [tierNo, setTierNo] = useState<number | ''>('')
  const [basic, setBasic] = useState('')
  const [allowances, setAllowances] = useState('')
  const [hmName, setHmName] = useState('')
  const [hmEmail, setHmEmail] = useState('')
  const [effective, setEffective] = useState('')
  const [headcount, setHeadcount] = useState('1')
  const [justification, setJustification] = useState('')

  useEffect(() => {
    apiFetch<{ tiers: Tier[] }>('/recruitment/position-tiers/')
      .then(d => setTiers(d.tiers || []))
      .catch(e => {
        const m = e instanceof Error ? e.message : ''
        const status = (e as { status?: number })?.status
        if (status === 403 || /restricted|permission|403/i.test(m)) setDenied(true)
        else setErr(m || 'Could not load tiers.')
      })
  }, [])

  const tier = useMemo(() => tiers.find(t => t.tier === tierNo) || null, [tiers, tierNo])
  const needsHiringManager = !!tier?.standard_signatories.some(s => s.slug === 'hiring_manager')
  const basicN = Number(basic || 0)
  const ceiling = tier?.basic_salary_max != null ? Number(tier.basic_salary_max) : null
  const overBand = ceiling != null && basicN > ceiling
  const justificationRequired = overBand
  const canSubmit =
    personName.trim() && position.trim() && tierNo !== '' && basicN > 0 &&
    (!needsHiringManager || (hmName.trim() && hmEmail.trim())) &&
    (!justificationRequired || justification.trim()) && !busy

  async function submit() {
    setErr(''); setBusy(true)
    const allowN = Number(allowances || 0)
    const ctcM = basicN + allowN
    const lines: Record<string, unknown>[] = [
      { item: 'Base Salary (Pre-Tax)', monthly: basicN, annual: basicN * 12 },
    ]
    if (allowN > 0) lines.push({ item: 'Allowances', monthly: allowN, annual: allowN * 12 })
    try {
      await apiFetch('/recruitment/authorities/', {
        method: 'POST',
        body: JSON.stringify({
          kind, person_name: personName.trim(), position: position.trim(),
          department: department.trim(), tier: tierNo,
          proposed_basic_salary: basicN,
          hiring_manager_name: hmName.trim(), hiring_manager_email: hmEmail.trim(),
          effective_date: effective || undefined, headcount: Number(headcount || 1),
          justification: justification.trim(), currency: 'BWP',
          quoted_ctc_monthly: ctcM, quoted_ctc_annual: ctcM * 12,
          salary_lines: lines,
        }),
      })
      router.push('/recruitment/authorities')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not raise the authority.')
      setBusy(false)
    }
  }

  if (denied) {
    return (
      <div style={{ fontFamily: SERIF, color: NAVY }}>
        <TopBar title="Raise an authority" />
        <div style={{ maxWidth: 620, margin: '48px auto', padding: 24, ...card, textAlign: 'center' }}>
          <Lock size={26} color={MUT} />
          <h2 style={{ fontSize: 19, margin: '12px 0 8px' }}>Not available to you</h2>
          <p style={{ color: MUT, fontSize: 14, lineHeight: 1.6, margin: 0 }}>
            Raising an authority is restricted. Speak to the CFO or Human Capital.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div style={{ fontFamily: SERIF, color: NAVY, paddingBottom: 80 }}>
      <TopBar title="Raise an authority" />
      <div style={{ maxWidth: 760, margin: '0 auto', padding: '20px 20px 0' }}>
        <button onClick={() => router.push('/recruitment/authorities')} style={ghost}>
          <ArrowLeft size={15} /> Back to authorities
        </button>
        <h1 style={{ fontSize: 26, margin: '10px 0 4px' }}>Raise an Authority to Recruit</h1>
        <p style={{ color: MUT, fontSize: 14, margin: '0 0 18px', lineHeight: 1.6 }}>
          Pick the tier and the signatures follow. The basic salary is checked against the
          tier&rsquo;s band.
        </p>

        <div style={{ ...card, padding: 20, display: 'grid', gap: 14 }}>
          <Field label="This is…">
            <div style={{ display: 'flex', gap: 8 }}>
              {(['recruit', 'regrade'] as const).map(k => (
                <button key={k} onClick={() => setKind(k)}
                  style={{ ...chip, ...(kind === k ? chipOn : {}) }}>
                  {k === 'recruit' ? 'A new hire (external)' : 'A regrade (existing employee)'}
                </button>))}
            </div>
          </Field>

          <Two>
            <Field label="Person's name"><input style={inp} value={personName}
              onChange={e => setPersonName(e.target.value)} placeholder="Full name" /></Field>
            <Field label="Position / job title"><input style={inp} value={position}
              onChange={e => setPosition(e.target.value)} placeholder="e.g. Financial Controller" /></Field>
          </Two>

          <Two>
            <Field label="Department"><input style={inp} value={department}
              onChange={e => setDepartment(e.target.value)} placeholder="e.g. Finance" /></Field>
            <Field label="Position tier">
              <select style={inp} value={tierNo}
                onChange={e => setTierNo(e.target.value ? Number(e.target.value) : '')}>
                <option value="">Choose a tier…</option>
                {tiers.map(t => <option key={t.tier} value={t.tier}>Tier {t.tier} — {t.name}</option>)}
              </select>
            </Field>
          </Two>

          {tier && (
            <div style={{ background: '#FAFBFC', border: `1px solid ${HAIR}`, borderRadius: 12, padding: '10px 14px' }}>
              <div style={{ fontSize: 12.5, color: MUT }}>
                <b style={{ color: NAVY }}>Signatures for this tier:</b>{' '}
                {tier.standard_signatories.map(s => s.label).join(' · ')}
              </div>
              <div style={{ fontSize: 12.5, color: MUT, marginTop: 4 }}>
                <b style={{ color: NAVY }}>Basic-salary band:</b>{' '}
                {tier.basic_salary_min == null && tier.basic_salary_max == null
                  ? 'not set yet — Human Capital still to enter it'
                  : `${money(tier.basic_salary_min)} — ${money(tier.basic_salary_max)} / mo`}
              </div>
            </div>)}

          <Two>
            <Field label="Basic salary (monthly, Pula)"><input style={inp} value={basic} inputMode="decimal"
              onChange={e => setBasic(e.target.value.replace(/[^\d.]/g, ''))} placeholder="e.g. 35000" /></Field>
            <Field label="Other allowances (monthly, optional)"><input style={inp} value={allowances} inputMode="decimal"
              onChange={e => setAllowances(e.target.value.replace(/[^\d.]/g, ''))} placeholder="e.g. 6000" /></Field>
          </Two>

          {overBand && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', background: AMBERBG,
                          border: '1px solid #F5D9AE', borderRadius: 12, padding: '10px 12px' }}>
              <AlertTriangle size={15} color={AMBERTX} style={{ marginTop: 2, flexShrink: 0 }} />
              <div style={{ fontSize: 13, color: NAVY, lineHeight: 1.55 }}>
                This basic is <b>above the Tier {tier?.tier} ceiling</b> ({money(ceiling)} / mo). A written
                justification is required, and it must be approved by{' '}
                <b>{(tier?.exception_signatories || []).map(s => s.label).join(' and ')}</b>.
              </div>
            </div>)}

          {needsHiringManager && (
            <Two>
              <Field label="Hiring manager — name"><input style={inp} value={hmName}
                onChange={e => setHmName(e.target.value)} placeholder="Line manager" /></Field>
              <Field label="Hiring manager — email"><input style={inp} value={hmEmail} type="email"
                onChange={e => setHmEmail(e.target.value)} placeholder="name@alphadirect.co.bw" /></Field>
            </Two>)}

          <Two>
            <Field label="Effective date (optional)"><input style={inp} type="date" value={effective}
              onChange={e => setEffective(e.target.value)} /></Field>
            <Field label="Headcount"><input style={inp} value={headcount} inputMode="numeric"
              onChange={e => setHeadcount(e.target.value.replace(/[^\d]/g, '') || '1')} /></Field>
          </Two>

          <Field label={justificationRequired ? 'Justification (required — over band)' : 'Why this role is needed'}>
            <textarea style={{ ...inp, minHeight: 84, resize: 'vertical' }} value={justification}
              onChange={e => setJustification(e.target.value)}
              placeholder={justificationRequired
                ? 'Explain why the offer is above the tier ceiling…'
                : 'A short reason for the appointment…'} />
          </Field>

          {err && <div style={{ color: RED, fontSize: 13, fontWeight: 600 }}>{err}</div>}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 4 }}>
            <button onClick={() => router.push('/recruitment/authorities')} style={ghost}>Cancel</button>
            <button disabled={!canSubmit} onClick={submit}
              style={{ ...primary, opacity: canSubmit ? 1 : 0.5, cursor: canSubmit ? 'pointer' : 'not-allowed' }}>
              {busy ? 'Raising…' : 'Raise authority'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

const inp: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '10px 12px', borderRadius: 11,
  border: `1px solid ${HAIR}`, fontSize: 14, fontFamily: SERIF, color: NAVY, background: '#fff',
}
const chip: React.CSSProperties = {
  padding: '9px 14px', borderRadius: 999, border: `1px solid ${HAIR}`, background: '#fff',
  color: NAVY, fontSize: 13, fontWeight: 700, cursor: 'pointer', fontFamily: SERIF,
}
const chipOn: React.CSSProperties = { background: NAVY, color: ORANGE, border: 'none' }
const ghost: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 14px', borderRadius: 999,
  fontSize: 13, fontWeight: 700, cursor: 'pointer', fontFamily: SERIF, border: `1px solid ${HAIR}`,
  background: '#fff', color: NAVY,
}
const primary: React.CSSProperties = { ...ghost, background: NAVY, color: ORANGE, border: 'none' }

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block' }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: MUT, marginBottom: 6 }}>{label}</div>
      {children}
    </label>)
}
function Two({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>{children}</div>
}
