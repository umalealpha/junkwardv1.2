'use client'

/**
 * /recruitment — native Omni ATS (CFO 2026-07-11; redesign 2026-07-13). Post a
 * vacancy, upload candidate CVs, and Omni ranks them by how well their skills
 * match the job. CV text is read and matched LOCALLY — no candidate data leaves
 * Omni. Design: Finance brand (navy #0D1B2A / orange #F4A623 / Book Antiqua),
 * design-guides principles + Emil-Kowalski motion. Logic unchanged.
 */
import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Briefcase, Users, UploadCloud, Sparkles, FileText, Plus, Check, Link2, Clock } from 'lucide-react'

interface Requisition {
  id: string; title: string; department: string; employment_type: string
  headcount: number; status: string; required_skills: string[]; jd_text: string
  applications: number; created_at: string; days_open: number
}
interface Application {
  id: string; candidate_id: string; name: string; email: string; phone: string
  stage: string; match_score: number; matched_skills: string[]; missing_skills: string[]
  ai_analysed: boolean; ai_score: number | null; ai_summary: string
  ai_strengths: string[]; ai_gaps: string[]; ai_questions: string[]
  human_reviewed: boolean; has_cv: boolean
  status_url?: string; scorecard_count?: number
  prior_applications?: number; repeat_applicant?: boolean
}

const NAVY = '#0D1B2A', ORANGE = '#F4A623', ORANGE_TX = '#B04E00', MUT = '#6B7280', HAIR = '#ECEEF2'
const SERIF = "'Book Antiqua', 'Palatino Linotype', Palatino, Georgia, serif"
const card: React.CSSProperties = {
  background: '#fff', borderRadius: 18,
  boxShadow: '0 1px 2px rgba(13,27,42,.04), 0 12px 30px rgba(13,27,42,.06)',
  border: `1px solid ${HAIR}`,
}
const eyebrow: React.CSSProperties = { fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: MUT, fontWeight: 700 }
const input: React.CSSProperties = { width: '100%', padding: '10px 12px', borderRadius: 11, border: `1px solid ${HAIR}`, fontSize: 14, background: '#FCFCFD', color: NAVY, outline: 'none' }
const STAGES = ['applied', 'screening', 'interview_1', 'interview_2', 'offer', 'hired', 'rejected']
const scoreColor = (s: number) => (s >= 70 ? '#1B7A3D' : s >= 40 ? ORANGE_TX : '#B42318')
const STATUS_PILL: Record<string, { bg: string; fg: string }> = {
  open: { bg: '#E6F6EC', fg: '#1B7A3D' }, closed: { bg: '#F2F4F7', fg: MUT },
  on_hold: { bg: '#FFF3E0', fg: ORANGE_TX }, filled: { bg: '#EEF2FF', fg: '#4338CA' },
}

interface Scorecard {
  id: string; interviewer: string; round: string; score: number
  recommendation: string; strengths: string; concerns: string; created_at: string
}

const SC_ROUNDS = [['screening', 'Screening'], ['interview_1', 'First interview'], ['interview_2', 'Second interview']]
const SC_RECS = [['strong_yes', 'Strong yes'], ['yes', 'Yes'], ['maybe', 'Maybe'], ['no', 'No'], ['strong_no', 'Strong no']]

export default function RecruitmentPage() {
  const router = useRouter()
  const [reqs, setReqs] = useState<Requisition[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [apps, setApps] = useState<Application[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [nf, setNf] = useState({ title: '', department: '', required_skills: '', jd_text: '' })
  const [uf, setUf] = useState({ full_name: '', email: '', phone: '' })
  const [cv, setCv] = useState<File | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [analysing, setAnalysing] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [scOpen, setScOpen] = useState<string | null>(null)
  const [scCards, setScCards] = useState<Record<string, Scorecard[]>>({})
  const [scForm, setScForm] = useState({ round: 'interview_1', score: '', recommendation: 'yes', strengths: '', concerns: '' })
  const [scBusy, setScBusy] = useState(false)

  async function toggleScorecards(aid: string) {
    if (scOpen === aid) { setScOpen(null); return }
    setScOpen(aid)
    try {
      const r = await apiFetch<{ scorecards: Scorecard[] }>(`/recruitment/applications/${aid}/scorecards/`)
      setScCards(m => ({ ...m, [aid]: r.scorecards }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load scorecards.')
    }
  }

  async function submitScorecard(aid: string) {
    const sc = Number(scForm.score)
    if (scForm.score.trim() === '' || !Number.isFinite(sc) || sc < 0 || sc > 100) {
      setErr('Enter a score between 0 and 100 before filing the scorecard.')
      return
    }
    setScBusy(true)
    try {
      await apiFetch(`/recruitment/applications/${aid}/scorecards/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...scForm, score: sc }),
      })
      const r = await apiFetch<{ scorecards: Scorecard[] }>(`/recruitment/applications/${aid}/scorecards/`)
      setScCards(m => ({ ...m, [aid]: r.scorecards }))
      setScForm({ round: 'interview_1', score: '', recommendation: 'yes', strengths: '', concerns: '' })
      if (sel) loadApps(sel)   // refresh the count on the card
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not save the scorecard.') }
    finally { setScBusy(false) }
  }

  function copyLink(id: string) {
    const url = `${window.location.origin}/apply/${id}`
    navigator.clipboard?.writeText(url)
      .then(() => { setCopiedId(id); setTimeout(() => setCopiedId(c => (c === id ? null : c)), 1800) })
      .catch(() => {})
  }

  // Copy the candidate's own no-login status link (H2) so HR can share it.
  function copyStatusLink(a: Application) {
    if (!a.status_url) return
    navigator.clipboard?.writeText(a.status_url)
      .then(() => { setCopiedId(a.id); setTimeout(() => setCopiedId(c => (c === a.id ? null : c)), 1800) })
      .catch(() => {})
  }

  const loadReqs = useCallback(async () => {
    try { const r = await apiFetch<{ requisitions: Requisition[] }>('/recruitment/requisitions/'); setReqs(r.requisitions) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load') }
  }, [])
  const loadApps = useCallback(async (id: string) => {
    try { const r = await apiFetch<{ applications: Application[] }>(`/recruitment/requisitions/${id}/applications/`); setApps(r.applications) }
    catch { setApps([]) }
  }, [])

  useEffect(() => { if (!getToken()) { router.replace('/login'); return } loadReqs() }, [loadReqs, router])
  useEffect(() => { if (sel) loadApps(sel) }, [sel, loadApps])

  const selected = reqs.find(r => r.id === sel) ?? null
  const totalCandidates = reqs.reduce((n, r) => n + (r.applications || 0), 0)
  const oldestOpenDays = reqs.filter(r => r.status === 'open').reduce((m, r) => Math.max(m, r.days_open || 0), 0)

  async function createReq(e: React.FormEvent) {
    e.preventDefault()
    if (!nf.title.trim() || busy) return
    setBusy(true); setErr(null)
    try {
      const r = await apiFetch<Requisition>('/recruitment/requisitions/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...nf, required_skills: nf.required_skills }),
      })
      setNf({ title: '', department: '', required_skills: '', jd_text: '' }); setShowNew(false)
      await loadReqs(); setSel(r.id)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not create') }
    finally { setBusy(false) }
  }

  async function uploadCv(e: React.FormEvent) {
    e.preventDefault()
    if (!sel || !uf.full_name.trim() || busy) return
    setBusy(true); setErr(null)
    try {
      const form = new FormData()
      form.append('full_name', uf.full_name); form.append('email', uf.email); form.append('phone', uf.phone)
      if (cv) form.append('cv', cv)
      await apiFetch(`/recruitment/requisitions/${sel}/candidates/`, { method: 'POST', body: form })
      setUf({ full_name: '', email: '', phone: '' }); setCv(null)
      const fi = document.getElementById('cvfile') as HTMLInputElement | null; if (fi) fi.value = ''
      await loadApps(sel); await loadReqs()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Upload failed') }
    finally { setBusy(false) }
  }

  async function analyse(aid: string) {
    setAnalysing(aid)
    try { await apiFetch(`/recruitment/applications/${aid}/analyse/`, { method: 'POST' }); if (sel) await loadApps(sel); setOpen(aid) }
    catch { /* keep deterministic score */ }
    finally { setAnalysing(null) }
  }

  async function moveStage(aid: string, stage: string) {
    try {
      await apiFetch(`/recruitment/applications/${aid}/stage/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage }),
      })
      if (sel) loadApps(sel)
    } catch { /* ignore */ }
  }

  const primaryBtn = (enabled: boolean): React.CSSProperties => ({
    background: enabled ? NAVY : '#C7CCD4', color: '#fff', cursor: enabled ? 'pointer' : 'not-allowed',
    boxShadow: enabled ? '0 6px 16px rgba(13,27,42,.22)' : 'none',
  })

  return (
    <div className="flex flex-col min-h-screen" style={{ background: '#F6F7FA' }}>
      <style>{`
        @keyframes rise { from { opacity:0; transform: translateY(10px) } to { opacity:1; transform:none } }
        .rise { animation: rise .45s cubic-bezier(.16,1,.3,1) both }
        .lift { transition: transform .2s cubic-bezier(.16,1,.3,1), box-shadow .2s ease, border-color .2s ease }
        .lift:hover { transform: translateY(-2px); box-shadow: 0 10px 26px rgba(13,27,42,.12) }
        .rec-input:focus { border-color:${ORANGE}; box-shadow: 0 0 0 3px rgba(244,166,35,.18); background:#fff }
        .rec-btn { transition: transform .15s ease, box-shadow .2s ease, filter .2s ease }
        .rec-btn:hover:not(:disabled) { filter: brightness(1.06) } .rec-btn:active:not(:disabled){ transform: translateY(1px) }
      `}</style>
      <TopBar title="Recruitment" breadcrumbs={[{ label: 'HR' }, { label: 'Recruitment' }]} />

      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-5" style={{ color: NAVY }}>
        {/* Hero */}
        <div className="rise overflow-hidden" style={{ borderRadius: 22, background: `linear-gradient(135deg, ${NAVY} 0%, #14273d 60%, #1b3350 100%)`, boxShadow: '0 14px 36px rgba(13,27,42,.28)' }}>
          <div className="p-6 md:p-7 relative">
            <div style={{ position: 'absolute', inset: 0, background: `radial-gradient(600px 200px at 90% -20%, rgba(244,166,35,.20), transparent)` }} />
            <div className="relative flex flex-wrap items-end justify-between gap-4">
              <div>
                <div style={{ ...eyebrow, color: ORANGE }}>Talent Management</div>
                <h1 style={{ fontFamily: SERIF, color: '#fff', fontSize: 30, fontWeight: 700, lineHeight: 1.05, marginTop: 6 }}>Recruitment</h1>
                <p style={{ color: 'rgba(255,255,255,.72)', fontSize: 13.5, marginTop: 6, maxWidth: 460 }}>
                  Post a vacancy, add candidates, and Omni ranks them by skills match — CVs read and scored on our own servers.
                </p>
              </div>
              <div className="flex gap-3">
                {[{ i: <Briefcase className="w-4 h-4" />, n: reqs.length, l: 'Open vacancies' },
                  { i: <Users className="w-4 h-4" />, n: totalCandidates, l: 'Candidates' },
                  { i: <Clock className="w-4 h-4" />, n: `${oldestOpenDays}d`, l: 'Oldest open role' }].map((s, k) => (
                  <div key={k} style={{ background: 'rgba(255,255,255,.08)', border: '1px solid rgba(255,255,255,.14)', borderRadius: 14, padding: '10px 16px', minWidth: 104 }}>
                    <div style={{ color: ORANGE, display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, letterSpacing: '.08em', textTransform: 'uppercase', fontWeight: 700 }}>{s.i}</div>
                    <div style={{ fontFamily: SERIF, color: '#fff', fontSize: 26, fontWeight: 700, lineHeight: 1 }}>{s.n}</div>
                    <div style={{ color: 'rgba(255,255,255,.6)', fontSize: 11 }}>{s.l}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {err && <div role="alert" aria-live="assertive" className="rise rounded-2xl p-3 text-sm" style={{ background: '#FCEAEA', border: '1px solid #F6C9C9', color: '#B42318' }}>{err}</div>}

        <div className="grid grid-cols-1 md:grid-cols-5 gap-5">
          {/* left — vacancies + new */}
          <div className="md:col-span-2 space-y-4">
            <div className="rise p-4" style={card}>
              <div className="flex items-center justify-between">
                <div style={eyebrow}>Open vacancies</div>
                <button className="rec-btn inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold"
                  onClick={() => setShowNew(v => !v)} style={{ background: showNew ? '#F2F4F7' : ORANGE + '1f', color: ORANGE_TX, border: 'none', cursor: 'pointer' }}>
                  <Plus className="w-3.5 h-3.5" /> New
                </button>
              </div>
              <div className="mt-3 space-y-2">
                {reqs.length === 0 && (
                  <div className="text-center py-8">
                    <div style={{ width: 46, height: 46, margin: '0 auto', borderRadius: 14, background: ORANGE + '1a', display: 'grid', placeItems: 'center' }}><Briefcase className="w-5 h-5" style={{ color: ORANGE_TX }} /></div>
                    <p style={{ fontSize: 13, color: MUT, marginTop: 10 }}>No vacancies yet.<br />Create one to start adding candidates.</p>
                  </div>
                )}
                {reqs.map(r => {
                  const on = sel === r.id
                  const pill = STATUS_PILL[r.status] || STATUS_PILL.closed
                  return (
                    <button key={r.id} onClick={() => setSel(r.id)} className="lift w-full text-left rounded-xl p-3 relative"
                      style={{ background: on ? '#FFF9EF' : '#fff', border: `1px solid ${on ? ORANGE + '66' : HAIR}`, cursor: 'pointer', paddingLeft: 16 }}>
                      {on && <span style={{ position: 'absolute', left: 0, top: 10, bottom: 10, width: 4, borderRadius: 4, background: ORANGE }} />}
                      <div className="flex items-center justify-between gap-2">
                        <div style={{ fontFamily: SERIF, fontWeight: 700, fontSize: 15.5 }}>{r.title}</div>
                        <span style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'capitalize', background: pill.bg, color: pill.fg, padding: '2px 8px', borderRadius: 999 }}>{(r.status || '').replace('_', ' ')}</span>
                      </div>
                      <div className="flex items-center justify-between gap-2" style={{ marginTop: 3 }}>
                        <div style={{ fontSize: 12, color: MUT }}>
                          {r.department || 'No department'} · {r.applications} candidate{r.applications !== 1 ? 's' : ''}
                          {r.status === 'open' && typeof r.days_open === 'number' ? ` · ${r.days_open}d open` : ''}
                        </div>
                        {r.status === 'open' && (
                          <span role="button" tabIndex={0}
                            onClick={(e) => { e.stopPropagation(); copyLink(r.id) }}
                            style={{ fontSize: 11, fontWeight: 700, color: ORANGE_TX, display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                            {copiedId === r.id ? <><Check className="w-3 h-3" /> Copied</> : <><Link2 className="w-3 h-3" /> Apply link</>}
                          </span>
                        )}
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>

            {(showNew || reqs.length === 0) && (
              <form onSubmit={createReq} className="rise p-4 space-y-2.5" style={card}>
                <div style={eyebrow}>New vacancy</div>
                <input className="rec-input" style={input} placeholder="Job title *" value={nf.title} onChange={e => setNf({ ...nf, title: e.target.value })} />
                <input className="rec-input" style={input} placeholder="Department" value={nf.department} onChange={e => setNf({ ...nf, department: e.target.value })} />
                <input className="rec-input" style={input} placeholder="Required skills (comma-separated)" value={nf.required_skills} onChange={e => setNf({ ...nf, required_skills: e.target.value })} />
                <textarea className="rec-input" style={{ ...input, minHeight: 78, resize: 'vertical' }} placeholder="Job description" value={nf.jd_text} onChange={e => setNf({ ...nf, jd_text: e.target.value })} />
                <button disabled={!nf.title.trim() || busy} type="submit"
                  className="rec-btn w-full inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold"
                  style={primaryBtn(!!nf.title.trim() && !busy)}>
                  <Plus className="w-4 h-4" /> Create vacancy
                </button>
              </form>
            )}
          </div>

          {/* right — selected vacancy + upload + ranked candidates */}
          <div className="md:col-span-3 space-y-4">
            {!selected && (
              <div className="rise p-10 text-center" style={card}>
                <div style={{ width: 54, height: 54, margin: '0 auto', borderRadius: 16, background: NAVY, display: 'grid', placeItems: 'center' }}><Users className="w-6 h-6" style={{ color: ORANGE }} /></div>
                <div style={{ fontFamily: SERIF, fontSize: 17, fontWeight: 700, marginTop: 14 }}>Pick a vacancy</div>
                <p style={{ fontSize: 13, color: MUT, marginTop: 4 }}>Choose a role on the left (or create one) to add and rank candidates.</p>
              </div>
            )}
            {selected && (
              <>
                <div className="rise p-5" style={card}>
                  <div style={{ fontFamily: SERIF, fontSize: 21, fontWeight: 700 }}>{selected.title}</div>
                  <div style={{ fontSize: 12.5, color: MUT, marginTop: 3 }}>{selected.department || 'No department'} · {selected.employment_type} · {selected.headcount} post(s)</div>
                  {selected.required_skills.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-3">
                      {selected.required_skills.map(s => <span key={s} style={{ fontSize: 11.5, background: NAVY + '0d', color: NAVY, padding: '3px 10px', borderRadius: 999, fontWeight: 600 }}>{s}</span>)}
                    </div>
                  )}
                </div>

                <form onSubmit={uploadCv} className="rise p-5 space-y-2.5" style={card}>
                  <div className="flex items-center gap-2" style={eyebrow}><UploadCloud className="w-3.5 h-3.5" style={{ color: ORANGE_TX }} /> Add a candidate</div>
                  <input className="rec-input" style={input} placeholder="Candidate full name *" value={uf.full_name} onChange={e => setUf({ ...uf, full_name: e.target.value })} />
                  <div className="grid grid-cols-2 gap-2">
                    <input className="rec-input" style={input} placeholder="Email" value={uf.email} onChange={e => setUf({ ...uf, email: e.target.value })} />
                    <input className="rec-input" style={input} placeholder="Phone" value={uf.phone} onChange={e => setUf({ ...uf, phone: e.target.value })} />
                  </div>
                  <label className="flex items-center gap-2 rounded-xl px-3 py-2.5 cursor-pointer" style={{ border: `1px dashed ${HAIR}`, background: '#FCFCFD', fontSize: 13, color: MUT }}>
                    <FileText className="w-4 h-4" style={{ color: ORANGE_TX }} />
                    <span className="truncate">{cv ? cv.name : 'Attach CV (PDF / Word)'}</span>
                    <input id="cvfile" type="file" accept=".pdf,.docx,.doc" onChange={e => setCv(e.target.files?.[0] ?? null)} className="hidden" />
                  </label>
                  <button disabled={!uf.full_name.trim() || busy} type="submit"
                    className="rec-btn w-full inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold"
                    style={{ background: uf.full_name.trim() && !busy ? ORANGE : '#C7CCD4', color: uf.full_name.trim() && !busy ? NAVY : '#fff', cursor: uf.full_name.trim() && !busy ? 'pointer' : 'not-allowed', boxShadow: uf.full_name.trim() && !busy ? '0 6px 16px rgba(244,166,35,.34)' : 'none' }}>
                    <UploadCloud className="w-4 h-4" /> {busy ? 'Reading CV…' : 'Upload & match'}
                  </button>
                  <p style={{ fontSize: 11, color: MUT }}>The CV is read on our server and scored instantly. The AI review strips the name, ID and contact details first — no personal details leave Omni.</p>
                </form>

                <div className="rise p-2" style={card}>
                  <div className="flex items-center gap-2 px-2 py-2" style={eyebrow}><Sparkles className="w-3.5 h-3.5" style={{ color: ORANGE_TX }} /> Candidates — ranked by match</div>
                  {apps.length === 0 && <p className="px-3 pb-4 pt-1" style={{ fontSize: 13, color: MUT }}>No candidates yet — upload a CV above.</p>}
                  {apps.map((a, i) => (
                    <div key={a.id} style={{ padding: '13px 12px', borderTop: i ? `1px solid ${HAIR}` : 'none' }}>
                      <div className="flex items-center gap-3 flex-wrap">
                        <div style={{ width: 46, height: 46, borderRadius: '50%', flex: 'none', display: 'grid', placeItems: 'center', background: `conic-gradient(${scoreColor(a.match_score)} ${a.match_score * 3.6}deg, #EEF0F4 0)` }}>
                          <div style={{ width: 37, height: 37, borderRadius: '50%', background: '#fff', display: 'grid', placeItems: 'center', color: scoreColor(a.match_score), fontWeight: 800, fontSize: 12.5 }}>{a.match_score}%</div>
                        </div>
                        <div style={{ minWidth: 140 }}>
                          <div style={{ fontWeight: 700, fontSize: 14.5, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            {a.name}
                            {a.repeat_applicant && (
                              <span title={`This person has ${(a.prior_applications || 0) + 1} applications on file`}
                                style={{ fontSize: 10, fontWeight: 700, background: '#FFF3E0', color: ORANGE_TX, padding: '1px 7px', borderRadius: 999 }}>
                                Applied before{a.prior_applications ? ` ×${a.prior_applications}` : ''}
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: 12, color: MUT, display: 'flex', alignItems: 'center', gap: 5 }}>{a.email || '—'}{a.has_cv && <span style={{ color: '#1B7A3D', display: 'inline-flex', alignItems: 'center', gap: 2 }}><Check className="w-3 h-3" />CV</span>}</div>
                        </div>
                        <select value={a.stage} onChange={e => moveStage(a.id, e.target.value)} className="rec-input"
                          style={{ ...input, width: 'auto', padding: '6px 10px', fontSize: 12.5, marginLeft: 'auto', textTransform: 'capitalize' }}>
                          {STAGES.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                        </select>
                      </div>
                      <div className="flex flex-wrap gap-1.5 mt-2.5">
                        {a.matched_skills.map(s => <span key={s} style={{ fontSize: 11, background: '#E6F6EC', color: '#1B7A3D', padding: '2px 9px', borderRadius: 999, fontWeight: 600 }}>✓ {s}</span>)}
                        {a.missing_skills.map(s => <span key={s} style={{ fontSize: 11, background: '#FCEAEA', color: '#B42318', padding: '2px 9px', borderRadius: 999, fontWeight: 600 }}>{s}</span>)}
                      </div>
                      <div className="mt-2.5">
                        {a.ai_analysed ? (
                          <>
                            <button onClick={() => setOpen(open === a.id ? null : a.id)}
                              style={{ fontSize: 12, color: '#4338CA', fontWeight: 700, background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                              <Sparkles className="w-3.5 h-3.5" /> AI review{a.ai_score != null ? ` · fit ${a.ai_score}%` : ''} {open === a.id ? '▾' : '▸'}
                            </button>
                            {open === a.id && (
                              <div className="rise" style={{ marginTop: 8, background: '#F5F6FF', border: '1px solid #E4E7F5', borderRadius: 12, padding: 13, fontSize: 12.5 }}>
                                {a.ai_summary && <p style={{ margin: '0 0 8px', color: '#1F2937' }}>{a.ai_summary}</p>}
                                {a.ai_strengths.length > 0 && <div style={{ marginBottom: 6 }}><b style={{ color: '#1B7A3D' }}>Strengths:</b> {a.ai_strengths.join(' · ')}</div>}
                                {a.ai_gaps.length > 0 && <div style={{ marginBottom: 6 }}><b style={{ color: '#B42318' }}>Gaps:</b> {a.ai_gaps.join(' · ')}</div>}
                                {a.ai_questions.length > 0 && (
                                  <div><b style={{ color: NAVY }}>Suggested interview questions:</b>
                                    <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{a.ai_questions.map((q, qi) => <li key={qi} style={{ margin: '3px 0' }}>{q}</li>)}</ul>
                                  </div>
                                )}
                                <button onClick={() => analyse(a.id)} disabled={analysing === a.id} style={{ marginTop: 8, fontSize: 11.5, color: MUT, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>{analysing === a.id ? 'Re-analysing…' : '↻ re-run AI'}</button>
                              </div>
                            )}
                          </>
                        ) : (
                          <button onClick={() => analyse(a.id)} disabled={analysing === a.id} className="rec-btn"
                            style={{ fontSize: 12, color: '#4338CA', fontWeight: 700, background: '#EEF2FF', border: 'none', cursor: 'pointer', padding: '5px 11px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                            <Sparkles className="w-3.5 h-3.5" /> {analysing === a.id ? 'Analysing CV…' : 'Run AI review'}
                          </button>
                        )}
                      </div>
                      <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        {a.status_url && (
                          <button onClick={() => copyStatusLink(a)} className="rec-btn"
                            style={{ fontSize: 11.5, color: NAVY, fontWeight: 700, background: '#F1F3F9', border: 'none', cursor: 'pointer', padding: '4px 10px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                            {copiedId === a.id ? <><Check className="w-3 h-3" /> Copied</> : <><Link2 className="w-3 h-3" /> Applicant status link</>}
                          </button>
                        )}
                        <button onClick={() => toggleScorecards(a.id)} className="rec-btn"
                          style={{ fontSize: 11.5, color: '#4338CA', fontWeight: 700, background: '#EEF2FF', border: 'none', cursor: 'pointer', padding: '4px 10px', borderRadius: 999 }}>
                          Interview scorecards{a.scorecard_count ? ` · ${a.scorecard_count}` : ''} {scOpen === a.id ? '▾' : '▸'}
                        </button>
                      </div>
                      {scOpen === a.id && (
                        <div className="rise" style={{ marginTop: 8, background: '#FAFAFF', border: '1px solid #E4E7F5', borderRadius: 12, padding: 13 }}>
                          {(scCards[a.id] || []).map(s => (
                            <div key={s.id} style={{ borderBottom: '1px solid #EEF0F4', padding: '6px 0', fontSize: 12.5 }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                                <b style={{ color: NAVY }}>{s.interviewer || 'Interviewer'}</b>
                                <span style={{ color: MUT }}>{s.round.replace('_', ' ')} · {s.score}/100 · {s.recommendation.replace('_', ' ')}</span>
                              </div>
                              {s.strengths && <div style={{ color: '#1B7A3D', marginTop: 2 }}>+ {s.strengths}</div>}
                              {s.concerns && <div style={{ color: '#B42318', marginTop: 2 }}>− {s.concerns}</div>}
                            </div>
                          ))}
                          {(scCards[a.id] || []).length === 0 && (
                            <div style={{ fontSize: 12, color: MUT }}>No scorecards yet — file the first one below.</div>
                          )}
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 10 }}>
                            <select value={scForm.round} onChange={e => setScForm({ ...scForm, round: e.target.value })} className="rec-input" style={input}>
                              {SC_ROUNDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </select>
                            <select value={scForm.recommendation} onChange={e => setScForm({ ...scForm, recommendation: e.target.value })} className="rec-input" style={input}>
                              {SC_RECS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </select>
                          </div>
                          <input type="number" min={0} max={100} placeholder="Score 0–100" value={scForm.score}
                            onChange={e => setScForm({ ...scForm, score: e.target.value })} className="rec-input" style={{ ...input, marginTop: 8 }} />
                          <textarea placeholder="Strengths (optional)" value={scForm.strengths} rows={2}
                            onChange={e => setScForm({ ...scForm, strengths: e.target.value })} className="rec-input" style={{ ...input, marginTop: 8 }} />
                          <textarea placeholder="Concerns (optional)" value={scForm.concerns} rows={2}
                            onChange={e => setScForm({ ...scForm, concerns: e.target.value })} className="rec-input" style={{ ...input, marginTop: 8 }} />
                          <button disabled={scBusy} onClick={() => submitScorecard(a.id)}
                            style={{ marginTop: 10, background: NAVY, color: '#fff', fontWeight: 700, fontSize: 13, border: 0, borderRadius: 8, padding: '9px 16px', cursor: 'pointer' }}>
                            {scBusy ? 'Saving…' : 'File scorecard'}
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
