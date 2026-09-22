'use client'

import { useEffect, useState, useCallback } from 'react'
import type { ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'

// ── Alpha light skin tokens (shared with /nexus, /rewards) ────────────────────
const NAVY = '#0D1B2A', ORANGE = '#F4A623', ORANGE_DK = '#9A640A'
const MUT = '#6B7280', HAIR = '#ECEEF2', CANVAS = '#F7F8FB', TRACK = '#EEF0F4'
const CARD_SHADOW = '0 1px 2px rgba(13,27,42,.05), 0 10px 28px rgba(13,27,42,.07)'

const card: React.CSSProperties = {
  background: '#fff', borderRadius: 20, boxShadow: CARD_SHADOW, border: `1px solid ${HAIR}`,
}
const eyebrow: React.CSSProperties = {
  fontSize: 11, letterSpacing: '.12em', textTransform: 'uppercase', color: MUT, fontWeight: 700,
}

// ── Types (mirror /staff-rewards/dashboard/ + /pending/) ──────────────────────
interface PillarActivity {
  id: string
  label: string
  points: number
  status: string          // approved | pending | rejected
  created_at: string
}
interface Pillar {
  code: string            // innovation | business_impact | health_wellness
  name: string
  total: number
  tier: string            // Bronze | Silver | Gold | Platinum | Diamond
  next_tier: string | null
  points_to_next: number
  recent: PillarActivity[]
}
interface StaffDashboard {
  member: { id: string; name: string } | null
  is_approver: boolean
  pillars: Pillar[]
}
interface PendingSubmission {
  id: string
  member_name: string
  feature_code: string
  feature_label: string
  pillar: string
  points: number
  detail: string
  created_at: string
}

// ── Staff tier ladder (spec §5) — used to draw progress bars ──────────────────
const TIER_FLOOR: Record<string, number> = {
  Bronze: 0, Silver: 1000, Gold: 2500, Platinum: 5000, Diamond: 7500,
}
const TIER_COLOR: Record<string, string> = {
  Bronze: '#A97142', Silver: '#9CA3AF', Gold: '#D4A017', Platinum: '#6B7280', Diamond: '#3FA7B8',
}

const PILLAR_BLURB: Record<string, string> = {
  innovation: 'Courses, certifications and upskilling that keep us ahead.',
  business_impact: 'Partnerships and verified customer compliments that grow the brand.',
  health_wellness: 'Fitness events and wellbeing — opt-in, never special-category data.',
}

const statusTint = (s: string) =>
  s === 'approved' ? { bg: '#E6F6EC', fg: '#1B7A3D', label: 'Approved' }
  : s === 'rejected' ? { bg: '#FCEAEA', fg: '#B42318', label: 'Rejected' }
  : { bg: '#FDEFD7', fg: ORANGE_DK, label: 'Pending' }

// ── The four LIVE submission features (spec §9.2 prompts 2,3,4,6) ─────────────
type FieldType = 'text' | 'url' | 'date' | 'select'
interface FeatureField {
  name: string
  label: string
  type: FieldType
  required?: boolean
  options?: string[]
  placeholder?: string
}
interface FeatureForm {
  code: string            // feature_code POSTed to /staff-rewards/submit/
  title: string
  pillar: string
  blurb: string
  fields: FeatureField[]
}

const LIVE_FEATURES: FeatureForm[] = [
  {
    code: 'profdev',
    title: 'Professional Development',
    pillar: 'Innovation',
    blurb: 'Log a completed course or certification (LinkedIn Learning, Coursera, Udemy). HR / line manager confirms relevance before points post.',
    fields: [
      { name: 'course_name', label: 'Course name', type: 'text', required: true, placeholder: 'e.g. Advanced Financial Modelling' },
      { name: 'level', label: 'Level', type: 'select', required: true, options: ['Short', 'Intermediate', 'Certification'] },
      { name: 'certificate_url', label: 'Certificate / verification URL', type: 'url', required: true, placeholder: 'https://…' },
    ],
  },
  {
    code: 'bizdev',
    title: 'Business Development',
    pillar: 'Business Impact',
    blurb: 'Log a verified partnership (university, school, society, community org). Stores organisation data only — no individual contact PII. BD lead signs off.',
    fields: [
      { name: 'partner_org', label: 'Partner organisation', type: 'text', required: true, placeholder: 'e.g. University of Botswana' },
      { name: 'agreement_ref', label: 'Agreement / MOU reference', type: 'text', required: true, placeholder: 'e.g. MOU-2026-014' },
      { name: 'reach', label: 'Reach / outcome (leads, recruits)', type: 'text', placeholder: 'e.g. 40 student leads' },
    ],
  },
  {
    code: 'fitness',
    title: 'Fitness Activity',
    pillar: 'Health & Wellness',
    blurb: 'Register a running club, marathon, charity walk or cycling session. Participation only — no medical data is recorded. Organiser confirms.',
    fields: [
      { name: 'event_name', label: 'Event / session', type: 'text', required: true, placeholder: 'e.g. Alpha Running Club — Saturday run' },
      { name: 'event_date', label: 'Date', type: 'date', required: true },
    ],
  },
  {
    code: 'compliment',
    title: 'Customer Compliment',
    pillar: 'Business Impact',
    blurb: 'Log verified positive customer feedback. Store a case reference and channel only — customer identity never enters the record. Supervisor / QA verifies.',
    fields: [
      { name: 'case_reference', label: 'Case reference', type: 'text', required: true, placeholder: 'e.g. CMP-2026-0331' },
      { name: 'channel', label: 'Channel', type: 'select', required: true, options: ['Email', 'Phone call', 'Survey', 'Social media'] },
    ],
  },
]

// ── Coming-soon features (spec §6 + §7 — blocked pending DPIA + opt-in) ───────
export default function StaffRewardsPage() {
  const router = useRouter()
  const [dash, setDash] = useState<StaffDashboard | null>(null)
  const [pending, setPending] = useState<PendingSubmission[]>([])
  const [err, setErr] = useState<string | null>(null)

  const loadDash = useCallback(async () => {
    try { setDash(await apiFetch<StaffDashboard>('/staff-rewards/dashboard/')) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load') }
  }, [])

  const loadPending = useCallback(async () => {
    try {
      const r = await apiFetch<{ results: PendingSubmission[] }>('/staff-rewards/pending/')
      setPending(r.results ?? [])
    } catch { /* approver-only; silently ignore for non-approvers */ }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    loadDash()
    loadPending()
  }, [loadDash, loadPending, router])

  const nf = (n: number) => n.toLocaleString()
  const isApprover = dash?.is_approver === true

  // Progress within the current tier band (current floor → next floor).
  function tierProgress(p: Pillar): number {
    if (!p.next_tier) return 100
    const floor = TIER_FLOOR[p.tier] ?? 0
    const nextFloor = TIER_FLOOR[p.next_tier] ?? floor + 1
    const span = Math.max(1, nextFloor - floor)
    return Math.min(100, Math.max(0, ((p.total - floor) / span) * 100))
  }

  return (
    <div className="flex flex-col min-h-screen" style={{ background: CANVAS }}>
      <TopBar title="Staff Rewards" breadcrumbs={[{ label: 'Alpha Rewards' }, { label: 'Staff Rewards' }]} />
      <div className="flex-1 p-6 max-w-6xl mx-auto w-full space-y-5" style={{ color: NAVY }}>

        {/* Intro — clearly the EMPLOYEE programme, separate ledger */}
        <div className="p-5" style={card}>
          <div style={eyebrow}>Employee programme</div>
          <h1 style={{ fontSize: 26, fontWeight: 600, color: NAVY, marginTop: 6 }}>Staff Rewards</h1>
          <p style={{ fontSize: 14, color: '#374151', marginTop: 6, maxWidth: 720 }}>
            This is the <b>staff</b> reward programme for Alpha Direct employees — a separate points ledger and
            Bronze-to-Diamond tier ladder, distinct from the customer rewards in this app. Earn points across three
            pillars: Innovation, Business Impact, and Health & Wellness.
          </p>
        </div>

        {err && (
          <div className="rounded-2xl p-4 text-sm" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9', color: '#B42318' }}>
            {err}
          </div>
        )}

        {/* ── Three pillar cards ──────────────────────────────────────────── */}
        {!dash && !err && <p className="text-sm" style={{ color: MUT }}>Loading…</p>}
        {dash && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            {dash.pillars.map(p => {
              const pct = tierProgress(p)
              const tierColor = TIER_COLOR[p.tier] ?? MUT
              return (
                <div key={p.code} className="p-5 flex flex-col" style={card}>
                  <div style={eyebrow}>{p.name}</div>
                  <p style={{ fontSize: 11.5, color: MUT, marginTop: 4, minHeight: 30 }}>
                    {PILLAR_BLURB[p.code] ?? ''}
                  </p>

                  {/* Pillar total — orange serif hero number */}
                  <div className="flex items-baseline gap-2 mt-3">
                    <b style={{ fontFamily: '"Book Antiqua", Palatino, Georgia, serif', fontSize: 46, fontWeight: 700, color: ORANGE, lineHeight: 1 }}>
                      {nf(p.total)}
                    </b>
                    <span style={{ fontSize: 13, color: MUT }}>points</span>
                  </div>

                  {/* Current tier */}
                  <div className="mt-3">
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-white"
                          style={{ fontSize: 12, fontWeight: 700, background: tierColor }}>
                      ★ {p.tier}
                    </span>
                  </div>

                  {/* Progress to next tier */}
                  <div className="mt-4">
                    <div className="flex justify-between" style={{ fontSize: 11.5, color: MUT, marginBottom: 5 }}>
                      <span>{p.next_tier ? `Next: ${p.next_tier}` : 'Top tier reached'}</span>
                      <span>{p.next_tier ? `${nf(p.points_to_next)} to go` : '—'}</span>
                    </div>
                    <div className="rounded-full overflow-hidden" style={{ height: 9, background: TRACK }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: ORANGE, borderRadius: 999, transition: 'width 1.2s cubic-bezier(.16,1,.3,1)' }} />
                    </div>
                  </div>

                  {/* Recent activity */}
                  <div className="mt-4 flex-1" style={{ borderTop: `1px solid ${HAIR}`, paddingTop: 10 }}>
                    <div style={{ ...eyebrow, fontSize: 10 }}>Recent activity</div>
                    {p.recent.length === 0 && <p style={{ fontSize: 12.5, color: '#9CA3AF', marginTop: 8 }}>Nothing yet.</p>}
                    {p.recent.slice(0, 4).map(a => {
                      const t = statusTint(a.status)
                      return (
                        <div key={a.id} className="flex items-center gap-2 py-1.5" style={{ fontSize: 12.5, borderBottom: `1px dashed ${HAIR}` }}>
                          <span style={{ color: '#374151', flex: 1, minWidth: 0 }} className="truncate">{a.label}</span>
                          <span className="px-1.5 py-0.5 rounded-full" style={{ fontSize: 10, fontWeight: 700, background: t.bg, color: t.fg }}>{t.label}</span>
                          <span className="font-bold" style={{ color: a.status === 'approved' ? '#1B7A3D' : MUT }}>+{a.points}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* ── Submit an activity ──────────────────────────────────────────── */}
        <div>
          <div style={eyebrow}>Submit an activity</div>
          <p style={{ fontSize: 13, color: MUT, marginTop: 4 }}>
            Submissions are routed for approval — points post only once verified.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mt-3">
            {LIVE_FEATURES.map(f => (
              <SubmitCard key={f.code} feature={f} onSubmitted={() => { loadDash(); loadPending() }} />
            ))}
          </div>
        </div>

        {/* ── Health & Wellness (opt-in) ──────────────────────────────────── */}
        <div>
          <div style={eyebrow}>Health &amp; Wellness · opt-in</div>
          <p style={{ fontSize: 13, color: MUT, marginTop: 4 }}>
            These use health-related data, so they are strictly opt-in. Steps records only your daily count;
            meal scoring keeps only the AI healthiness score — your photo is scored and discarded, never stored.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mt-3">
            <StepsCard onSubmitted={() => loadDash()} />
            <MealCard onSubmitted={() => loadDash()} />
          </div>
        </div>

        {/* ── Approvals queue (approvers only) ────────────────────────────── */}
        {isApprover && (
          <div className="p-5" style={card}>
            <div className="flex items-center justify-between">
              <div style={eyebrow}>Approvals queue</div>
              <span style={{ fontSize: 12, color: MUT }}>{pending.length} pending</span>
            </div>
            <div className="mt-3 space-y-2">
              {pending.length === 0 && <p style={{ fontSize: 13, color: '#9CA3AF' }}>Nothing waiting for approval.</p>}
              {pending.map(s => (
                <ApprovalRow
                  key={s.id}
                  submission={s}
                  onDone={() => {
                    setPending(prev => prev.filter(x => x.id !== s.id))
                    loadDash()
                  }}
                />
              ))}
            </div>
          </div>
        )}

        {/* ── Footer note ─────────────────────────────────────────────────── */}
        <p style={{ fontSize: 12, color: MUT, paddingTop: 4, borderTop: `1px solid ${HAIR}` }}>
          All point values are provisional pending HR and CFO sign-off.
        </p>
      </div>
    </div>
  )
}

// ── Submission card ───────────────────────────────────────────────────────────
function SubmitCard({ feature, onSubmitted }: { feature: FeatureForm; onSubmitted: () => void }): ReactNode {
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const setField = (name: string, v: string) =>
    setValues(prev => ({ ...prev, [name]: v }))

  const missingRequired = feature.fields.some(f => f.required && !(values[f.name] || '').trim())

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (missingRequired || busy) return
    setBusy(true); setError(null)
    try {
      await apiFetch('/staff-rewards/submit/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feature_code: feature.code, payload: values }),
      })
      setDone(true)
      setValues({})
      onSubmitted()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed')
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <div className="p-5 flex flex-col" style={{ ...card }}>
        <div className="flex items-center gap-2">
          <span style={eyebrow}>{feature.pillar}</span>
        </div>
        <h2 style={{ fontSize: 17, fontWeight: 600, color: NAVY, marginTop: 8 }}>{feature.title}</h2>
        <div className="rounded-2xl p-4 mt-3" style={{ background: '#E6F6EC', border: '1px solid #BFE6CC' }}>
          <p style={{ fontSize: 13.5, fontWeight: 700, color: '#1B7A3D' }}>✓ Submitted for approval</p>
          <p style={{ fontSize: 12.5, color: '#1B7A3D', marginTop: 4 }}>
            Points post once your submission is verified.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setDone(false)}
          className="mt-3 self-start"
          style={{ fontSize: 12.5, fontWeight: 600, color: ORANGE_DK }}
        >
          Submit another →
        </button>
      </div>
    )
  }

  return (
    <form onSubmit={submit} className="p-5 flex flex-col" style={card}>
      <div style={eyebrow}>{feature.pillar}</div>
      <h2 style={{ fontSize: 17, fontWeight: 600, color: NAVY, marginTop: 8 }}>{feature.title}</h2>
      <p style={{ fontSize: 12, color: MUT, marginTop: 6 }}>{feature.blurb}</p>

      <div className="mt-3 space-y-3">
        {feature.fields.map(f => (
          <label key={f.name} className="block">
            <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
              {f.label}{f.required && <span style={{ color: '#B42318' }}> *</span>}
            </span>
            {f.type === 'select' ? (
              <select
                value={values[f.name] || ''}
                onChange={e => setField(f.name, e.target.value)}
                className="mt-1 w-full rounded-lg px-3 py-2"
                style={{ fontSize: 13.5, border: `1px solid ${HAIR}`, background: '#fff', color: NAVY }}
              >
                <option value="">Select…</option>
                {f.options?.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : (
              <input
                type={f.type === 'url' ? 'url' : f.type === 'date' ? 'date' : 'text'}
                value={values[f.name] || ''}
                onChange={e => setField(f.name, e.target.value)}
                placeholder={f.placeholder}
                className="mt-1 w-full rounded-lg px-3 py-2"
                style={{ fontSize: 13.5, border: `1px solid ${HAIR}`, background: '#fff', color: NAVY }}
              />
            )}
          </label>
        ))}
      </div>

      {error && <p style={{ fontSize: 12.5, color: '#B42318', marginTop: 10 }}>{error}</p>}

      <button
        type="submit"
        disabled={missingRequired || busy}
        className="mt-4 self-start rounded-lg px-4 py-2 transition-opacity"
        style={{
          fontSize: 13.5, fontWeight: 700, color: NAVY, background: ORANGE,
          opacity: missingRequired || busy ? 0.5 : 1,
          cursor: missingRequired || busy ? 'not-allowed' : 'pointer',
        }}
      >
        {busy ? 'Submitting…' : 'Submit for approval'}
      </button>
    </form>
  )
}

// ── Approval row ────────────────────────────────────────────────────────────
function ApprovalRow({ submission, onDone }: { submission: PendingSubmission; onDone: () => void }): ReactNode {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function decide(action: 'approve' | 'reject') {
    if (busy) return
    setBusy(action); setError(null)
    try {
      await apiFetch(`/staff-rewards/submissions/${submission.id}/${action}/`, { method: 'POST' })
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`)
      setBusy(null)
    }
  }

  return (
    <div className="rounded-2xl p-3 flex items-center gap-3 flex-wrap" style={{ background: CANVAS, border: `1px solid ${HAIR}` }}>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <b style={{ fontSize: 14, fontWeight: 600, color: NAVY }}>{submission.member_name}</b>
          <span className="px-2 py-0.5 rounded-full" style={{ fontSize: 10.5, fontWeight: 700, background: '#FEF3DD', color: ORANGE_DK }}>{submission.feature_label}</span>
          <span style={{ fontSize: 11.5, color: MUT }}>{submission.pillar}</span>
        </div>
        {submission.detail && <p style={{ fontSize: 12.5, color: '#374151', marginTop: 4 }}>{submission.detail}</p>}
        {error && <p style={{ fontSize: 12, color: '#B42318', marginTop: 4 }}>{error}</p>}
      </div>
      <span className="font-bold" style={{ fontSize: 14, color: NAVY }}>+{submission.points}</span>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => decide('approve')}
          disabled={busy !== null}
          className="rounded-lg px-3 py-1.5"
          style={{ fontSize: 12.5, fontWeight: 700, color: '#fff', background: '#1B7A3D', opacity: busy ? 0.6 : 1, cursor: busy ? 'wait' : 'pointer' }}
        >
          {busy === 'approve' ? '…' : 'Approve'}
        </button>
        <button
          type="button"
          onClick={() => decide('reject')}
          disabled={busy !== null}
          className="rounded-lg px-3 py-1.5"
          style={{ fontSize: 12.5, fontWeight: 700, color: '#B42318', background: '#FCEAEA', border: '1px solid #F6C9C9', opacity: busy ? 0.6 : 1, cursor: busy ? 'wait' : 'pointer' }}
        >
          {busy === 'reject' ? '…' : 'Reject'}
        </button>
      </div>
    </div>
  )
}

// ── Daily Step Goals (Health & Wellness, opt-in) ──────────────────────────────
function StepsCard({ onSubmitted }: { onSubmitted: () => void }): ReactNode {
  const [consent, setConsent] = useState(false)
  const [targetMet, setTargetMet] = useState(true)
  const [weeklyDays, setWeeklyDays] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!consent || busy) return
    setBusy(true); setError(null); setMsg(null)
    try {
      const r = await apiFetch<{ status: string }>('/staff-rewards/submit/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feature_code: 'steps', payload: {
          targetMet, weeklyDaysMet: parseInt(weeklyDays || '0', 10) || 0 } }),
      })
      setMsg(r.status === 'approved' ? 'Logged — points awarded.' : 'Submitted.')
      onSubmitted()
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed') }
    finally { setBusy(false) }
  }

  return (
    <form onSubmit={submit} className="p-5 flex flex-col" style={card}>
      <div style={eyebrow}>Health &amp; Wellness</div>
      <h2 style={{ fontSize: 17, fontWeight: 600, color: NAVY, marginTop: 8 }}>Daily Step Goals</h2>
      <p style={{ fontSize: 12, color: MUT, marginTop: 6 }}>
        Log your daily step target. Only the count and target-met flag are stored — no location or sensor data.
      </p>
      <label className="flex items-start gap-2 mt-3" style={{ fontSize: 12, color: '#374151' }}>
        <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} style={{ marginTop: 3 }} />
        <span>I opt in to share my daily step count for rewards (you can stop any time).</span>
      </label>
      <label className="flex items-center gap-2 mt-3" style={{ fontSize: 13, color: '#374151' }}>
        <input type="checkbox" checked={targetMet} onChange={e => setTargetMet(e.target.checked)} />
        <span>I hit my step goal today</span>
      </label>
      <label className="block mt-3">
        <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>Days hit this week</span>
        <input type="number" min={0} max={7} value={weeklyDays} onChange={e => setWeeklyDays(e.target.value)}
          className="w-full mt-1 rounded-lg px-3 py-2" style={{ border: `1px solid ${HAIR}`, fontSize: 13 }} placeholder="0-7" />
      </label>
      {error && <p style={{ fontSize: 12, color: '#B42318', marginTop: 8 }}>{error}</p>}
      {msg && <p style={{ fontSize: 12.5, fontWeight: 700, color: '#1B7A3D', marginTop: 8 }}>✓ {msg}</p>}
      <button type="submit" disabled={!consent || busy} className="mt-4 self-start px-5 py-2.5 rounded-full font-semibold"
        style={{ fontSize: 13.5, background: consent ? ORANGE : TRACK, color: consent ? '#3A2A04' : MUT }}>
        {busy ? 'Logging…' : 'Log steps'}
      </button>
    </form>
  )
}

// ── Healthy Eating (Health & Wellness, opt-in, camera-only) ───────────────────
function MealCard({ onSubmitted }: { onSubmitted: () => void }): ReactNode {
  const [consent, setConsent] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ aiScore: number; qualifying: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const videoRef = useState<HTMLVideoElement | null>(null)
  let vid: HTMLVideoElement | null = videoRef[0]

  async function startCamera() {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
      if (vid) { vid.srcObject = stream; await vid.play() }
      setStreaming(true)
    } catch { setError('Camera not available. Meal scoring needs the in-app camera (gallery uploads are blocked).') }
  }

  async function capture() {
    if (!vid || busy) return
    setBusy(true); setError(null)
    try {
      const canvas = document.createElement('canvas')
      canvas.width = vid.videoWidth || 640; canvas.height = vid.videoHeight || 480
      canvas.getContext('2d')!.drawImage(vid, 0, 0, canvas.width, canvas.height)
      const imageData = canvas.toDataURL('image/jpeg', 0.7)
      // stop the camera immediately — the frame is in memory only
      ;(vid.srcObject as MediaStream | null)?.getTracks().forEach(t => t.stop())
      setStreaming(false)
      const r = await apiFetch<{ aiScore: number; qualifying: boolean }>('/staff-rewards/score-meal/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imageData, consent: true, mealsTodayCount: 1 }),
      })
      setResult(r); onSubmitted()
    } catch (err) { setError(err instanceof Error ? err.message : 'Scoring failed') }
    finally { setBusy(false) }
  }

  return (
    <div className="p-5 flex flex-col" style={card}>
      <div style={eyebrow}>Health &amp; Wellness</div>
      <h2 style={{ fontSize: 17, fontWeight: 600, color: NAVY, marginTop: 8 }}>Healthy Eating</h2>
      <p style={{ fontSize: 12, color: MUT, marginTop: 6 }}>
        Snap your meal with the in-app camera. The photo is scored for healthiness and then discarded — only the score is kept.
      </p>
      <label className="flex items-start gap-2 mt-3" style={{ fontSize: 12, color: '#374151' }}>
        <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} style={{ marginTop: 3 }} />
        <span>I opt in to AI meal scoring. I understand my photo is scored then deleted, not stored.</span>
      </label>
      {consent && (
        <div className="mt-3">
          <video ref={el => { videoRef[1](el); vid = el }} playsInline muted
            style={{ width: '100%', borderRadius: 14, background: '#000', display: streaming ? 'block' : 'none', maxHeight: 220 }} />
          {!streaming && !result && (
            <button type="button" onClick={startCamera} className="px-5 py-2.5 rounded-full font-semibold"
              style={{ fontSize: 13.5, background: ORANGE, color: '#3A2A04' }}>Open camera</button>
          )}
          {streaming && (
            <button type="button" onClick={capture} disabled={busy} className="mt-3 px-5 py-2.5 rounded-full font-semibold"
              style={{ fontSize: 13.5, background: ORANGE, color: '#3A2A04' }}>{busy ? 'Scoring…' : 'Capture & score'}</button>
          )}
        </div>
      )}
      {result && (
        <div className="rounded-2xl p-4 mt-3" style={{ background: result.qualifying ? '#E6F6EC' : '#FDEFD7', border: `1px solid ${HAIR}` }}>
          <p style={{ fontSize: 13.5, fontWeight: 700, color: result.qualifying ? '#1B7A3D' : ORANGE_DK }}>
            Healthiness score: {result.aiScore}/100 — {result.qualifying ? 'points awarded ✓' : 'below threshold, no points'}
          </p>
        </div>
      )}
      {error && <p style={{ fontSize: 12, color: '#B42318', marginTop: 8 }}>{error}</p>}
    </div>
  )
}
