'use client'

/**
 * /recruitment/tiers — Position tiers, salary bands & job-title tagging
 * (Unami Hiring-SOP, CFO 2026-09-02).
 *
 * Human Capital / the CFO set each tier's BASIC-salary band (min–max, basic
 * only) and tag every current job title to a tier. The tags drive the tier a
 * new Authority to Recruit is suggested at. Read-only for anyone else.
 *
 * Design: Finance brand (navy #0D1B2A / orange #F4A623 / Book Antiqua).
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Lock, Check } from 'lucide-react'

const NAVY = '#0D1B2A', ORANGE = '#F4A623', MUT = '#6B7280', HAIR = '#ECEEF2'
const SERIF = "'Book Antiqua', 'Palatino Linotype', Palatino, Georgia, serif"
const card: React.CSSProperties = {
  background: '#fff', borderRadius: 18,
  boxShadow: '0 1px 2px rgba(13,27,42,.04), 0 12px 30px rgba(13,27,42,.06)',
  border: `1px solid ${HAIR}`,
}
const eyebrow: React.CSSProperties = {
  fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: MUT, fontWeight: 700,
}

interface Sig { slug: string; label: string }
interface Tier {
  tier: number; name: string
  basic_salary_min: string | null; basic_salary_max: string | null
  standard_signatories: Sig[]; exception_signatories: Sig[]
}
interface TitleRow { title: string; count: number; tier: number | null }

export default function TiersPage() {
  const [tiers, setTiers] = useState<Tier[]>([])
  const [titles, setTitles] = useState<TitleRow[]>([])
  const [canManage, setCanManage] = useState(false)
  const [denied, setDenied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState<string | null>(null)
  const [edit, setEdit] = useState<Record<number, { min: string; max: string }>>({})

  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  useEffect(() => {
    (async () => {
      try {
        const t = await apiFetch<{ can_manage: boolean; tiers: Tier[] }>('/recruitment/position-tiers/')
        setTiers(t.tiers || []); setCanManage(t.can_manage)
        const e: Record<number, { min: string; max: string }> = {}
        for (const x of t.tiers) e[x.tier] = { min: x.basic_salary_min ?? '', max: x.basic_salary_max ?? '' }
        setEdit(e)
        const j = await apiFetch<{ titles: TitleRow[] }>('/recruitment/job-title-tiers/')
        setTitles(j.titles || [])
      } catch (err) {
        const m = err instanceof Error ? err.message : ''
        const status = (err as { status?: number })?.status
        if (status === 403 || /restricted|permission|403/i.test(m)) setDenied(true)
        else show(m || 'Could not load.')
      } finally { setLoading(false) }
    })()
  }, [])

  async function saveBand(tier: number) {
    const e = edit[tier]
    try {
      const r = await apiFetch<Tier>(`/recruitment/position-tiers/${tier}/`, {
        method: 'PUT',
        body: JSON.stringify({ basic_salary_min: e.min || null, basic_salary_max: e.max || null }),
      })
      setTiers(prev => prev.map(t => t.tier === tier ? r : t))
      show(`Tier ${tier} band saved. ✅`)
    } catch (err) { show(err instanceof Error ? err.message : 'Could not save.') }
  }

  async function tagTitle(title: string, tier: number | '') {
    try {
      await apiFetch('/recruitment/job-title-tiers/', {
        method: 'POST', body: JSON.stringify({ title, tier: tier === '' ? 0 : tier }),
      })
      setTitles(prev => prev.map(x => x.title === title ? { ...x, tier: tier === '' ? null : Number(tier) } : x))
    } catch (err) { show(err instanceof Error ? err.message : 'Could not tag.') }
  }

  if (denied) {
    return (
      <div style={{ fontFamily: SERIF, color: NAVY }}>
        <TopBar title="Tiers & bands" />
        <div style={{ maxWidth: 620, margin: '48px auto', padding: 24, ...card, textAlign: 'center' }}>
          <Lock size={26} color={MUT} />
          <h2 style={{ fontSize: 19, margin: '12px 0 8px' }}>Not available to you</h2>
          <p style={{ color: MUT, fontSize: 14, lineHeight: 1.6, margin: 0 }}>
            Position tiers and salary bands are restricted. Speak to the CFO or Human Capital.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div style={{ fontFamily: SERIF, color: NAVY, paddingBottom: 80 }}>
      <TopBar title="Tiers & bands" />
      <div style={{ maxWidth: 1000, margin: '0 auto', padding: '20px 20px 0' }}>
        <div style={eyebrow}>Human Capital</div>
        <h1 style={{ fontSize: 26, margin: '4px 0 4px' }}>Position tiers &amp; salary bands</h1>
        <p style={{ color: MUT, fontSize: 14, margin: '0 0 20px', maxWidth: 760, lineHeight: 1.6 }}>
          Each tier&rsquo;s <b>basic-salary band</b> (basic only, allowances excluded) and who signs a
          hire at that tier. {canManage ? 'Enter the band numbers below.' : 'Read-only — only Human Capital or the CFO can change these.'}
        </p>

        {loading && <p style={{ color: MUT, fontSize: 14 }}>Loading…</p>}

        <div style={{ display: 'grid', gap: 12 }}>
          {tiers.map(t => (
            <div key={t.tier} style={{ ...card, padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
                <div style={{ minWidth: 240, flex: 1 }}>
                  <div style={{ fontSize: 17, fontWeight: 700 }}>Tier {t.tier} — {t.name}</div>
                  <div style={{ fontSize: 12.5, color: MUT, marginTop: 4 }}>
                    <b style={{ color: NAVY }}>Signs:</b> {t.standard_signatories.map(s => s.label).join(' · ')}
                  </div>
                  <div style={{ fontSize: 12.5, color: MUT, marginTop: 3 }}>
                    <b style={{ color: NAVY }}>Over-band approver:</b> {t.exception_signatories.map(s => s.label).join(' and ')}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                  <BandInput label="Min basic (mo)" value={edit[t.tier]?.min ?? ''} disabled={!canManage}
                    onChange={v => setEdit(p => ({ ...p, [t.tier]: { ...p[t.tier], min: v } }))} />
                  <BandInput label="Max basic (mo)" value={edit[t.tier]?.max ?? ''} disabled={!canManage}
                    onChange={v => setEdit(p => ({ ...p, [t.tier]: { ...p[t.tier], max: v } }))} />
                  {canManage && (
                    <button onClick={() => saveBand(t.tier)} style={primaryBtn}>
                      <Check size={14} /> Save
                    </button>)}
                </div>
              </div>
            </div>))}
        </div>

        <h2 style={{ fontSize: 19, margin: '28px 0 6px' }}>Tag every job title to a tier</h2>
        <p style={{ color: MUT, fontSize: 13.5, margin: '0 0 14px', lineHeight: 1.6 }}>
          Every current job title, most common first. Set its tier once and every person holding it
          is covered. {canManage ? '' : '(Read-only)'}
        </p>
        {titles.length === 0 && !loading && (
          <div style={{ ...card, padding: 20, color: MUT, fontSize: 14 }}>No job titles found yet.</div>)}
        <div style={{ ...card, overflow: 'hidden' }}>
          {titles.map((row, i) => (
            <div key={row.title} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                          gap: 12, padding: '11px 16px', borderTop: i ? `1px solid ${HAIR}` : 'none' }}>
              <div>
                <span style={{ fontSize: 14, fontWeight: 600 }}>{row.title}</span>
                <span style={{ fontSize: 12, color: MUT, marginLeft: 8 }}>
                  {row.count} {row.count === 1 ? 'person' : 'people'}
                </span>
              </div>
              <select value={row.tier ?? ''} disabled={!canManage}
                onChange={e => tagTitle(row.title, e.target.value ? Number(e.target.value) : '')}
                style={{ padding: '7px 10px', borderRadius: 10, border: `1px solid ${HAIR}`,
                         fontSize: 13, fontFamily: SERIF, color: row.tier ? NAVY : MUT, background: '#fff',
                         minWidth: 160 }}>
                <option value="">— not tagged —</option>
                {tiers.map(t => <option key={t.tier} value={t.tier}>Tier {t.tier} — {t.name}</option>)}
              </select>
            </div>))}
        </div>
      </div>

      {toast && (
        <div style={{ position: 'fixed', bottom: 28, left: '50%', transform: 'translateX(-50%)',
                      background: NAVY, color: '#fff', padding: '11px 20px', borderRadius: 999,
                      fontSize: 13.5, zIndex: 80 }}>{toast}</div>)}
    </div>
  )
}

function BandInput({ label, value, onChange, disabled }:
  { label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <label style={{ display: 'block' }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: MUT, marginBottom: 4 }}>{label}</div>
      <input value={value} disabled={disabled} inputMode="decimal"
        onChange={e => onChange(e.target.value.replace(/[^\d.]/g, ''))}
        placeholder="—"
        style={{ width: 130, boxSizing: 'border-box', padding: '9px 11px', borderRadius: 10,
                 border: `1px solid ${HAIR}`, fontSize: 14, fontFamily: SERIF, color: NAVY,
                 background: disabled ? '#F7F8FA' : '#fff' }} />
    </label>)
}

const primaryBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 16px', borderRadius: 999,
  fontSize: 13, fontWeight: 700, cursor: 'pointer', fontFamily: SERIF, border: 'none',
  background: NAVY, color: ORANGE, height: 38,
}
