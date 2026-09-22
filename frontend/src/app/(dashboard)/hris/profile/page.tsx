'use client'

/**
 * /hris/profile — native My Profile surface.
 *
 * Wired to /hris/api/me/ (CFO directive 2026-05-20, Manus HRIS Part 3).
 * GET   — load my HRIS profile.
 * PATCH — self-update phone + bank fields. Bank-field changes flip a
 *         `pending_bank_review` flag so HR/Finance can review.
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, ChevronRight, Target, Users, Mail, Building2, UserCircle, Phone, Landmark,
  Save, AlertCircle, CheckCircle2, Loader2, CalendarDays, GraduationCap, ShieldAlert, BriefcaseBusiness, TrendingUp,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisSelfService } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface MeProfile {
  employee_number: string
  full_name: string
  email: string
  phone: string
  department: string
  job_title: string
  bank_name: string
  bank_account_no: string
  bank_branch: string
  national_id: string
  qualifications: string
  hire_date: string
  contract_start: string
  contract_end: string
  contract_permanent: boolean
  contract_duration: string
}

interface DisciplinaryAction {
  id: string
  status: string
  category: string
  category_label: string
  incident_date: string | null
  issued_at: string | null
  allegation: string
  proposed_action: string
  // Natural justice (CFO directive 2026-08-11): a case awaiting MY response.
  awaiting_my_response: boolean
  response_deadline: string | null
  deadline_passed: boolean
  my_response: string
  my_responded_at: string | null
  can_respond: boolean
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? '—'
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

interface TalentSnapshot {
  has_profile: boolean
  ninebox: { label: string; box: number; colour: string; text_colour: string; advice: string; performance: number; potential: number } | null
  dialogue: { rating: string; overall: number | null; period: string; date: string } | null
  skills: { total: number; proficient: number; items: { name: string; category: string; level: number; proficient: boolean }[] }
  review: { date: string; period: string; status: string; rating: number | null; overdue: boolean } | null
  idp: { available: boolean; link: string }
}

export default function HrisProfilePage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  // Self-service (CFO directive 2026-06-16, bug aec2f3ce): every employee may
  // view/update their OWN profile — /hris/api/me/ is self-scoped to caller.
  const selfService = useHrisSelfService()
  const canView = allowed === true || selfService === true
  const accessDenied = allowed === false && selfService === false

  const [me, setMe] = useState<MeProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)

  const [phone, setPhone] = useState('')
  const [bankName, setBankName] = useState('')
  const [bankAcct, setBankAcct] = useState('')
  const [bankBranch, setBankBranch] = useState('')
  const [qualifications, setQualifications] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [discipline, setDiscipline] = useState<DisciplinaryAction[] | null>(null)
  const [talent, setTalent] = useState<TalentSnapshot | null>(null)

  useEffect(() => {
    if (accessDenied) router.replace('/dashboard')
  }, [accessDenied, router])

  useEffect(() => {
    if (!canView) return
    setLoading(true)
    authedHrisFetch('/hris/api/me/')
      .then(async r => {
        if (r.status === 404) { setNotFound(true); return }
        if (!r.ok) return
        const data: MeProfile = await r.json()
        setMe(data)
        setPhone(data.phone || '')
        setBankName(data.bank_name || '')
        setBankAcct(data.bank_account_no || '')
        setBankBranch(data.bank_branch || '')
        setQualifications(data.qualifications || '')
      })
      .finally(() => setLoading(false))
  }, [canView])

  // Item 4c: the caller's OWN issued disciplinary actions (self-view only),
  // plus (CFO 2026-08-11) any case awaiting THEIR OWN response.
  const loadDiscipline = useCallback(() => {
    authedHrisFetch('/hris/api/my-disciplinary/')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then(d => setDiscipline(Array.isArray(d.cases) ? d.cases : []))
      .catch(() => setDiscipline(null))
  }, [])

  useEffect(() => {
    if (!canView) return
    loadDiscipline()
  }, [canView, loadDiscipline])

  // My talent snapshot — 9-box, latest review, skills, next review. Read-only,
  // own record only (Oprah Mogomotsi feature request, 2026-08-13).
  useEffect(() => {
    if (!canView) return
    authedHrisFetch('/hris/api/my-talent/')
      .then(r => (r.ok ? r.json() : null))
      .then(d => setTalent(d))
      .catch(() => setTalent(null))
  }, [canView])

  async function save() {
    if (!me) return
    setBusy(true); setMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/me/', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone,
          bank_name: bankName,
          bank_account_no: bankAcct,
          bank_branch: bankBranch,
          qualifications,
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      const changed: string[] = data.updated_fields || []
      if (changed.length === 0) {
        setMsg({ ok: true, text: 'No changes to save.' })
      } else if (data.pending_bank_review) {
        setMsg({ ok: true, text: `Saved (${changed.join(', ')}). Bank-detail changes queued for HR/Finance review.` })
      } else {
        setMsg({ ok: true, text: `Saved (${changed.join(', ')}).` })
      }
      setMe({ ...me, phone, bank_name: bankName, bank_account_no: bankAcct, bank_branch: bankBranch, qualifications })
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setBusy(false)
    }
  }

  if (!canView) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const displayName = me?.full_name || 'My Profile'
  const initials = displayName.split(' ').map(p => p[0]).slice(0, 2).join('').toUpperCase() || 'ME'

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="My Profile" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Profile' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        <div className="rounded-2xl p-6 flex items-center gap-5"
             style={{ background: `linear-gradient(135deg, ${theme.navy}, ${theme.navy}e6)`, color: '#fff' }}>
          <div className="w-20 h-20 rounded-full flex items-center justify-center font-display text-2xl font-bold flex-shrink-0"
               style={{ background: theme.orange }}>
            {initials}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[11px] uppercase tracking-widest opacity-60 font-semibold">Signed in as</div>
            <h2 className="font-display text-2xl font-bold mt-1 italic truncate">{displayName}</h2>
            <p className="text-sm opacity-80 mt-1 truncate">
              {me?.job_title || me?.email || '—'}
              {me?.employee_number && <span className="opacity-60"> · #{me.employee_number}</span>}
            </p>
          </div>
        </div>

        <MyTalentCard talent={talent} theme={theme} />

        <Link href="/hris/my-dialogue"
              className="flex items-center gap-3 rounded-2xl p-4 transition hover:shadow-md"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
               style={{ background: theme.orange, color: '#fff' }}>
            <Target className="w-5 h-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-sm" style={{ color: theme.navy }}>My Development Dialogue</div>
            <div className="text-xs" style={{ color: theme.t2 }}>View your performance review — private to you and HR</div>
          </div>
          <ChevronRight className="w-4 h-4 flex-shrink-0" style={{ color: theme.t2 }} />
        </Link>

        <Link href="/hris/team-dialogues"
              className="flex items-center gap-3 rounded-2xl p-4 transition hover:shadow-md"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
               style={{ background: theme.navy, color: '#fff' }}>
            <Users className="w-5 h-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-semibold text-sm" style={{ color: theme.navy }}>My Team&apos;s Dialogues</div>
            <div className="text-xs" style={{ color: theme.t2 }}>Reviews of the people who report to you (if any)</div>
          </div>
          <ChevronRight className="w-4 h-4 flex-shrink-0" style={{ color: theme.t2 }} />
        </Link>

        {loading && (
          <p className="text-xs" style={{ color: theme.t2 }}>
            <Loader2 className="w-3.5 h-3.5 inline mr-1 animate-spin" /> Loading profile…
          </p>
        )}

        {!loading && notFound && (
          <div className="rounded-xl px-4 py-3 text-sm flex items-start gap-2"
               style={{ background: '#fef3c7', color: '#92400e', border: '1px solid #fde68a' }}>
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <div>
              HR has not yet linked a HRISProfile to your account. Email <strong>hr@alphadirect.co.bw</strong> so they can pair your record.
            </div>
          </div>
        )}

        {!loading && me && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div className="rounded-2xl p-5 space-y-3"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
              <h3 className="font-semibold mb-2 inline-flex items-center gap-1.5" style={{ color: theme.text }}>
                <UserCircle className="w-4 h-4" style={{ color: theme.orange }} /> Identity
              </h3>
              <ProfileRow icon={Mail} label="Email" value={me.email || '—'} theme={theme} />
              <ProfileRow icon={Building2} label="Department" value={me.department || '—'} theme={theme} />
              <ProfileRow label="Job title" value={me.job_title || '—'} theme={theme} />
              <ProfileRow label="National ID" value={me.national_id || '—'} theme={theme} />

              <h3 className="font-semibold pt-3 mb-1 inline-flex items-center gap-1.5" style={{ color: theme.text }}>
                <BriefcaseBusiness className="w-4 h-4" style={{ color: theme.orange }} /> Employment
              </h3>
              <ProfileRow icon={CalendarDays} label="Joined" value={fmtDate(me.hire_date)} theme={theme} />
              <div className="flex items-center gap-2 text-sm">
                <CalendarDays className="w-4 h-4 flex-shrink-0" style={{ color: theme.t2 }} />
                <span className="opacity-60 w-28 flex-shrink-0" style={{ color: theme.t2 }}>Contract</span>
                <span className="font-medium truncate" style={{ color: theme.text }}>
                  {me.contract_duration || '—'}
                </span>
                {me.contract_permanent && (
                  <span className="ml-1 rounded-full px-2 py-0.5 text-[10px] font-semibold"
                        style={{ background: `${theme.orange}1a`, color: theme.orange }}>Permanent</span>
                )}
              </div>
            </div>

            <div className="rounded-2xl p-5 space-y-3"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
              <h3 className="font-semibold mb-2 inline-flex items-center gap-1.5" style={{ color: theme.text }}>
                <Phone className="w-4 h-4" style={{ color: theme.orange }} /> Contact
              </h3>
              <FieldInput label="Phone" value={phone} onChange={setPhone} theme={theme} placeholder="+267 ..." />

              <h3 className="font-semibold pt-3 mb-2 inline-flex items-center gap-1.5" style={{ color: theme.text }}>
                <Landmark className="w-4 h-4" style={{ color: theme.orange }} /> Bank details
              </h3>
              <p className="text-[11px]" style={{ color: theme.t2 }}>
                Edits to bank fields are flagged for HR/Finance maker-checker review.
              </p>
              <FieldInput label="Bank name" value={bankName} onChange={setBankName} theme={theme} />
              <FieldInput label="Account no." value={bankAcct} onChange={setBankAcct} theme={theme} />
              <FieldInput label="Branch" value={bankBranch} onChange={setBankBranch} theme={theme} />

              <h3 className="font-semibold pt-3 mb-2 inline-flex items-center gap-1.5" style={{ color: theme.text }}>
                <GraduationCap className="w-4 h-4" style={{ color: theme.orange }} /> Qualifications
              </h3>
              <p className="text-[11px]" style={{ color: theme.t2 }}>
                Your education / professional qualifications. Kept on your record and used by HR for succession planning.
              </p>
              <textarea value={qualifications} onChange={e => setQualifications(e.target.value)} rows={3}
                        placeholder="e.g. BCom Accounting (UB, 2015); ACCA (2019)"
                        className="w-full px-3 py-2 rounded-md text-sm outline-none resize-y"
                        style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />

              <div className="flex items-center justify-end pt-2">
                <button type="button" onClick={save} disabled={busy}
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                        style={{ background: theme.orange, color: '#fff' }}>
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  {busy ? 'Saving…' : 'Save changes'}
                </button>
              </div>

              {msg && (
                <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                     style={{
                       background: msg.ok ? theme.okB : theme.erB,
                       color:      msg.ok ? theme.ok  : theme.er,
                       border:     `1px solid ${msg.ok ? theme.ok : theme.er}40`,
                     }}>
                  {msg.ok ? <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" /> :
                            <AlertCircle  className="w-4 h-4 flex-shrink-0 mt-0.5" />}
                  <div>{msg.text}</div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Item 4c: the caller's OWN issued disciplinary actions — self-view
            only, shown when there is anything on file. Never anyone else's.
            CFO 2026-08-11: a case awaiting THEIR response carries a box to
            answer in, so their explanation lands on the case record. */}
        {!loading && discipline && discipline.length > 0 && (
          <div className="rounded-2xl p-5"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <h3 className="font-semibold mb-1 inline-flex items-center gap-1.5" style={{ color: theme.text }}>
              <ShieldAlert className="w-4 h-4" style={{ color: theme.orange }} /> Disciplinary record
            </h3>
            <p className="text-[11px] mb-3" style={{ color: theme.t2 }}>
              Disciplinary actions on your file. Private to you and HR — speak to HR if anything here is unclear.
            </p>
            <div className="space-y-3">
              {discipline.map(d => (
                <div key={d.id} className="rounded-xl p-3.5"
                     style={{ background: theme.g100,
                              border: `1px solid ${d.awaiting_my_response ? theme.orange : theme.cardBdr}` }}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-sm" style={{ color: theme.text }}>
                      {d.awaiting_my_response ? 'Under consideration: ' : ''}{d.category_label}
                    </span>
                    <span className="text-xs" style={{ color: theme.t2 }}>
                      {d.awaiting_my_response
                        ? (d.response_deadline ? `Respond by ${fmtDate(d.response_deadline)}` : 'Response invited')
                        : `Issued ${fmtDate(d.issued_at)}`}
                    </span>
                  </div>
                  <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>Incident {fmtDate(d.incident_date)}</div>
                  {d.allegation && (
                    <p className="text-sm mt-2 whitespace-pre-wrap" style={{ color: theme.text }}>{d.allegation}</p>
                  )}
                  {d.proposed_action && d.awaiting_my_response && (
                    <p className="text-xs mt-2" style={{ color: theme.t2 }}>
                      Action being considered: {d.proposed_action}
                    </p>
                  )}
                  {d.my_responded_at ? (
                    <div className="mt-3 rounded-lg p-3"
                         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
                      <div className="text-[11px] font-semibold" style={{ color: theme.t2 }}>
                        Your response · {fmtDate(d.my_responded_at)}
                      </div>
                      <p className="text-sm mt-1 whitespace-pre-wrap" style={{ color: theme.text }}>{d.my_response}</p>
                    </div>
                  ) : d.can_respond ? (
                    <RespondBox caseId={d.id} deadline={d.response_deadline}
                                latePast={d.deadline_passed} theme={theme} onSaved={loadDiscipline} />
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

/**
 * The employee's own answer to an allegation about them (natural justice, CFO
 * directive 2026-08-11). One submission only — it becomes part of the case
 * record, so the box warns before saving and disappears afterwards.
 */
function RespondBox({ caseId, deadline, latePast, theme, onSaved }: {
  caseId: string; deadline: string | null; latePast: boolean; theme: any; onSaved: () => void
}) {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function submit() {
    if (!text.trim()) { setErr('Write your explanation first.'); return }
    if (!window.confirm('Send this to HR? It goes on your case record and cannot be edited afterwards.')) return
    setSaving(true); setErr('')
    try {
      const r = await authedHrisFetch(`/hris/api/my-disciplinary/${caseId}/respond/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response: text }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setErr(d.detail || `HTTP ${r.status}`); return }
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Network error')
    } finally { setSaving(false) }
  }

  return (
    <div className="mt-3 rounded-lg p-3" style={{ background: theme.card, border: `1px solid ${theme.orange}` }}>
      <div className="text-xs font-semibold mb-1" style={{ color: theme.text }}>Your side of it</div>
      <p className="text-[11px] mb-2" style={{ color: theme.t2 }}>
        {latePast
          ? 'Your deadline has passed, but you can still answer while the case is open.'
          : deadline
            ? `No decision will be recorded before ${fmtDate(deadline)}. Write your explanation here.`
            : 'Write your explanation here.'}
        {' '}You can send this once, so say everything you want on the record.
      </p>
      <textarea value={text} onChange={e => setText(e.target.value)} rows={5}
                placeholder="What happened, in your own words. Add anything you want HR to take into account."
                className="w-full px-3 py-2 rounded-md text-sm outline-none"
                style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
      <div className="flex items-center gap-2 mt-2">
        <button type="button" onClick={submit} disabled={saving || !text.trim()}
                className="px-3 py-1.5 rounded-md text-sm font-semibold text-white disabled:opacity-50"
                style={{ background: theme.orange }}>
          {saving ? 'Sending…' : 'Send to HR'}
        </button>
        {err && <span className="text-xs text-red-500">{err}</span>}
      </div>
    </div>
  )
}

function MyTalentCard({ talent, theme }: { talent: TalentSnapshot | null; theme: any }) {
  if (!talent) return null
  const { ninebox, dialogue, skills, review, idp } = talent
  const nothing = !ninebox && !dialogue && (!skills || skills.total === 0) && !review
  return (
    <div className="rounded-2xl p-5 space-y-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-center gap-2">
        <TrendingUp className="w-4 h-4" style={{ color: theme.orange }} />
        <h3 className="font-display font-bold text-sm" style={{ color: theme.navy }}>Where I stand</h3>
        <span className="text-xs" style={{ color: theme.t3 }}>· your talent snapshot</span>
      </div>
      {nothing ? (
        <p className="text-sm" style={{ color: theme.t2 }}>
          Nothing on record yet. Your 9-box, latest review, skills and next review appear here as they are captured.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {ninebox && (
            <div className="rounded-xl p-3" style={{ background: ninebox.colour, color: ninebox.text_colour }}>
              <div className="text-[10px] uppercase tracking-widest opacity-80 font-semibold">9-Box</div>
              <div className="font-display font-bold text-base mt-0.5">{ninebox.label}</div>
              <div className="text-[11px] opacity-90 mt-1 leading-snug">{ninebox.advice}</div>
            </div>
          )}
          {dialogue && (
            <div className="rounded-xl p-3" style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
              <div className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: theme.t3 }}>Latest review</div>
              <div className="font-display font-bold text-base mt-0.5" style={{ color: theme.navy }}>
                {dialogue.rating || (dialogue.overall != null ? `${dialogue.overall.toFixed(0)}%` : '—')}
              </div>
              <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>{[dialogue.period, dialogue.date && fmtDate(dialogue.date)].filter(Boolean).join(' · ') || '—'}</div>
            </div>
          )}
          <div className="rounded-xl p-3" style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
            <div className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: theme.t3 }}>Skills</div>
            <div className="font-display font-bold text-base mt-0.5" style={{ color: theme.navy }}>
              {skills.total}<span className="text-xs font-normal" style={{ color: theme.t2 }}> logged</span>
            </div>
            <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>{skills.proficient} proficient (level 3+)</div>
          </div>
          <div className="rounded-xl p-3"
               style={{ background: review?.overdue ? theme.erB : theme.g50, border: `1px solid ${review?.overdue ? theme.er : theme.cardBdr}` }}>
            <div className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: review?.overdue ? theme.er : theme.t3 }}>Next review</div>
            {review ? (
              <>
                <div className="font-display font-bold text-base mt-0.5" style={{ color: review.overdue ? theme.er : theme.navy }}>
                  {review.overdue ? 'Overdue' : fmtDate(review.date)}
                </div>
                <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>{review.period || review.status}</div>
              </>
            ) : (
              <div className="text-sm mt-0.5" style={{ color: theme.t2 }}>Not scheduled</div>
            )}
          </div>
        </div>
      )}
      <Link href={idp?.link || '/hris/idp'} className="inline-flex items-center gap-1.5 text-xs font-semibold" style={{ color: theme.orangeText }}>
        View my development plan <ChevronRight className="w-3.5 h-3.5" />
      </Link>
    </div>
  )
}

function ProfileRow({ icon: Icon, label, value, theme }: { icon?: any; label: string; value: string; theme: any }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      {Icon && <Icon className="w-4 h-4 flex-shrink-0" style={{ color: theme.t2 }} />}
      <span className="opacity-60 w-28 flex-shrink-0" style={{ color: theme.t2 }}>{label}</span>
      <span className="font-medium truncate" style={{ color: theme.text }}>{value}</span>
    </div>
  )
}

function FieldInput({ label, value, onChange, theme, placeholder }:
                    { label: string; value: string; onChange: (v: string) => void; theme: any; placeholder?: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 text-xs flex-shrink-0" style={{ color: theme.t2 }}>{label}</span>
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
             className="flex-1 px-3 py-1.5 rounded-md text-sm outline-none"
             style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
    </div>
  )
}
