'use client'

/**
 * /apply/[rid] — PUBLIC job application page (no login).
 *
 * Candidates reach this from a link HR posts on LinkedIn / Facebook. It shows
 * one OPEN vacancy and takes name / email / phone + CV. On submit the CV lands
 * in HR's recruitment queue already screened and ranked.
 *
 * Lives OUTSIDE the (dashboard) group so it is not wrapped by the signed-in
 * guard. Backed by:
 *   GET  /api/v1/recruitment/public/jobs/<rid>/
 *   POST /api/v1/recruitment/public/jobs/<rid>/apply/
 */

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import {
  Loader2, CheckCircle2, AlertCircle, UploadCloud,
  Briefcase, Building2, FileText,
} from 'lucide-react'

const API = '/api/v1' // same-origin via Next rewrites
const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const FONT = "'Montserrat', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
const MAX_MB = 8
const ALLOWED = ['.pdf', '.doc', '.docx']

interface Job {
  id: string
  title: string
  department: string
  employment_type: string
  jd_text: string
  required_skills: string[]
  is_open: boolean
}

export default function ApplyPage() {
  const { rid } = useParams<{ rid: string }>()

  const [job, setJob] = useState<Job | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [linkedin, setLinkedin] = useState('')
  const [heard, setHeard] = useState('')
  const [companyWebsite, setCompanyWebsite] = useState('') // honeypot — hidden
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  useEffect(() => {
    if (!rid) return
    setLoading(true)
    fetch(`${API}/recruitment/public/jobs/${rid}/`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status === 404 ? 'notfound' : 'error'))
      .then((j: Job) => setJob(j))
      .catch(e => setLoadError(e === 'notfound' ? 'notfound' : 'error'))
      .finally(() => setLoading(false))
  }, [rid])

  function pickFile(f: File | null) {
    setFileError(null)
    if (!f) { setFile(null); return }
    const ok = ALLOWED.some(ext => f.name.toLowerCase().endsWith(ext))
    if (!ok) { setFileError('Please upload a PDF or Word document.'); setFile(null); return }
    if (f.size > MAX_MB * 1024 * 1024) { setFileError(`File is too big — keep it under ${MAX_MB} MB.`); setFile(null); return }
    setFile(f)
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitError(null)
    if (!fullName.trim()) { setSubmitError('Please enter your full name.'); return }
    if (!email.trim()) { setSubmitError('Please enter your email address.'); return }
    if (!file) { setSubmitError('Please attach your CV.'); return }

    const fd = new FormData()
    fd.append('full_name', fullName.trim())
    fd.append('email', email.trim())
    fd.append('phone', phone.trim())
    fd.append('linkedin', linkedin.trim())
    fd.append('heard', heard.trim())
    fd.append('company_website', companyWebsite) // honeypot
    fd.append('cv', file)

    setSubmitting(true)
    try {
      const res = await fetch(`${API}/recruitment/public/jobs/${rid}/apply/`, { method: 'POST', body: fd })
      const j = await res.json().catch(() => ({}))
      if (!res.ok) { setSubmitError(j.detail || 'Something went wrong. Please try again.'); return }
      setDone(j.message || 'Thank you — your application has been received.')
    } catch {
      setSubmitError('Network problem. Please check your connection and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  // ── shells ────────────────────────────────────────────────────────────────
  const page = (inner: React.ReactNode) => (
    <div style={{ minHeight: '100vh', background: '#F4F6FA', fontFamily: FONT, color: '#1e293b' }}>
      <header style={{ background: NAVY, padding: '18px 20px' }}>
        <div style={{ maxWidth: 680, margin: '0 auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontWeight: 800, fontSize: 20, color: '#fff', letterSpacing: 0.3 }}>Alpha Direct</span>
          <span style={{ color: ORANGE, fontWeight: 700, fontSize: 20 }}>Careers</span>
        </div>
      </header>
      <main style={{ maxWidth: 680, margin: '0 auto', padding: '20px 16px 64px' }}>{inner}</main>
      <footer style={{ textAlign: 'center', color: '#94a3b8', fontSize: 12, padding: '20px 16px 40px' }}>
        © Alpha Direct Insurance · Gaborone, Botswana
      </footer>
    </div>
  )

  if (loading) return page(
    <div style={{ padding: 64, textAlign: 'center', color: '#64748b' }}>
      <Loader2 size={28} className="animate-spin" style={{ margin: '0 auto' }} />
      <p style={{ marginTop: 12 }}>Loading the role…</p>
    </div>
  )

  if (loadError === 'notfound' || !job) return page(
    <Card>
      <AlertCircle size={28} color={ORANGE} />
      <h1 style={{ fontSize: 20, margin: '10px 0 6px' }}>Vacancy not found</h1>
      <p style={{ color: '#64748b' }}>This link may be old or the role has been taken down. Please check the Alpha Direct careers page for current openings.</p>
    </Card>
  )

  if (loadError === 'error') return page(
    <Card>
      <AlertCircle size={28} color={ORANGE} />
      <h1 style={{ fontSize: 20, margin: '10px 0 6px' }}>Something went wrong</h1>
      <p style={{ color: '#64748b' }}>We could not load this role right now. Please try again in a few minutes.</p>
    </Card>
  )

  if (!job.is_open) return page(
    <>
      <JobHeader job={job} />
      <Card>
        <AlertCircle size={26} color={ORANGE} />
        <h2 style={{ fontSize: 18, margin: '10px 0 6px' }}>This role is no longer accepting applications</h2>
        <p style={{ color: '#64748b' }}>Thank you for your interest. Please look out for future openings at Alpha Direct.</p>
      </Card>
    </>
  )

  if (done) return page(
    <Card>
      <CheckCircle2 size={34} color="#16a34a" />
      <h1 style={{ fontSize: 22, margin: '12px 0 8px' }}>{done}</h1>
      <div style={{ color: '#475569', fontSize: 14, lineHeight: 1.6, textAlign: 'left', marginTop: 8 }}>
        <p style={{ fontWeight: 700, color: NAVY, marginBottom: 6 }}>What happens next</p>
        <p>1. Our HR team reviews every application.</p>
        <p>2. If your experience fits the role, we will contact you on the email or phone you gave us.</p>
        <p>3. You do not need to do anything else for now.</p>
      </div>
    </Card>
  )

  // ── the form ────────────────────────────────────────────────────────────
  return page(
    <>
      <JobHeader job={job} />

      {job.jd_text ? (
        <section style={cardStyle}>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: NAVY, marginBottom: 8 }}>About the role</h2>
          <p style={{ whiteSpace: 'pre-wrap', color: '#334155', fontSize: 14, lineHeight: 1.6 }}>{job.jd_text}</p>
        </section>
      ) : null}

      {job.required_skills?.length ? (
        <section style={cardStyle}>
          <h2 style={{ fontSize: 15, fontWeight: 700, color: NAVY, marginBottom: 10 }}>What we are looking for</h2>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {job.required_skills.map((s, i) => (
              <span key={i} style={{ background: '#EAF0FB', color: NAVY, borderRadius: 999, padding: '5px 12px', fontSize: 13, fontWeight: 600 }}>{s}</span>
            ))}
          </div>
        </section>
      ) : null}

      <form onSubmit={submit} style={cardStyle}>
        <h2 style={{ fontSize: 16, fontWeight: 800, color: NAVY, marginBottom: 4 }}>Apply now</h2>
        <p style={{ color: '#64748b', fontSize: 13, marginBottom: 16 }}>It takes about a minute. Your details go straight to our HR team.</p>

        <Field label="Full name *"><input style={inputStyle} value={fullName} onChange={e => setFullName(e.target.value)} placeholder="e.g. Kagiso Mokoena" /></Field>
        <Field label="Email *"><input style={inputStyle} type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" /></Field>
        <Field label="Phone"><input style={inputStyle} value={phone} onChange={e => setPhone(e.target.value)} placeholder="+267 …" /></Field>
        <Field label="LinkedIn profile (optional)"><input style={inputStyle} value={linkedin} onChange={e => setLinkedin(e.target.value)} placeholder="https://linkedin.com/in/…" /></Field>
        <Field label="How did you hear about us? (optional)">
          <select style={inputStyle} value={heard} onChange={e => setHeard(e.target.value)}>
            <option value="">Select…</option>
            <option>LinkedIn</option>
            <option>Facebook</option>
            <option>Referral</option>
            <option>Alpha Direct website</option>
            <option>Other</option>
          </select>
        </Field>

        {/* Honeypot — hidden from humans, catches bots */}
        <input value={companyWebsite} onChange={e => setCompanyWebsite(e.target.value)} tabIndex={-1} autoComplete="off"
          style={{ position: 'absolute', left: '-9999px', width: 1, height: 1, opacity: 0 }} aria-hidden="true" />

        <Field label="Your CV *">
          <div onClick={() => fileRef.current?.click()}
            style={{ border: `2px dashed ${file ? '#16a34a' : '#cbd5e1'}`, borderRadius: 10, padding: 16, textAlign: 'center', cursor: 'pointer', background: '#fff' }}>
            {file ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: '#166534', fontSize: 14, fontWeight: 600 }}>
                <FileText size={16} /> {file.name}
              </div>
            ) : (
              <div style={{ color: '#64748b', fontSize: 14 }}>
                <UploadCloud size={22} style={{ margin: '0 auto 6px' }} />
                Tap to attach your CV
                <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>PDF or Word, up to {MAX_MB} MB</div>
              </div>
            )}
          </div>
          <input ref={fileRef} type="file" accept=".pdf,.doc,.docx" style={{ display: 'none' }}
            onChange={e => pickFile(e.target.files?.[0] ?? null)} />
          {fileError ? <p style={{ color: '#dc2626', fontSize: 13, marginTop: 6 }}>{fileError}</p> : null}
        </Field>

        {submitError ? (
          <div style={{ background: '#FEF2F2', color: '#b91c1c', padding: '10px 12px', borderRadius: 8, fontSize: 13, display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
            <AlertCircle size={16} /> {submitError}
          </div>
        ) : null}

        <button type="submit" disabled={submitting}
          style={{ width: '100%', background: submitting ? '#94a3b8' : ORANGE, color: '#fff', border: 'none', borderRadius: 10, padding: '13px 16px', fontSize: 15, fontWeight: 700, cursor: submitting ? 'default' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          {submitting ? <><Loader2 size={16} className="animate-spin" /> Sending…</> : 'Submit application'}
        </button>
        <p style={{ color: '#94a3b8', fontSize: 11, textAlign: 'center', marginTop: 10 }}>
          Your CV is stored securely by Alpha Direct and used only for this application.
        </p>
      </form>
    </>
  )
}

// ── small presentational helpers ──────────────────────────────────────────
const cardStyle: React.CSSProperties = {
  background: '#fff', borderRadius: 14, padding: 20, marginBottom: 16,
  boxShadow: '0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.04)',
}
const inputStyle: React.CSSProperties = {
  width: '100%', padding: '11px 12px', border: '1px solid #d0d7e2', borderRadius: 8,
  fontSize: 15, color: '#1e293b', background: '#fff', boxSizing: 'border-box',
}

function Card({ children }: { children: React.ReactNode }) {
  return <div style={{ ...cardStyle, textAlign: 'center', padding: 28 }}>{children}</div>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block', marginBottom: 14 }}>
      <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 6 }}>{label}</span>
      {children}
    </label>
  )
}

function JobHeader({ job }: { job: Job }) {
  return (
    <section style={{ ...cardStyle, borderTop: `4px solid ${ORANGE}` }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, color: NAVY, margin: 0 }}>{job.title}</h1>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, marginTop: 10, color: '#64748b', fontSize: 13 }}>
        {job.department ? <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><Building2 size={14} /> {job.department}</span> : null}
        {job.employment_type ? <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><Briefcase size={14} /> {job.employment_type}</span> : null}
      </div>
    </section>
  )
}
