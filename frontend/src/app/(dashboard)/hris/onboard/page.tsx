'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  AlertTriangle, CheckCircle2, ChevronLeft, Loader2, RefreshCw,
  ShieldCheck, UserPlus, XCircle,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'
import { localYmd } from '@/lib/utils'

const today = () => localYmd(new Date())

type Company = { id: string; code: string; name: string }
type Manager = {
  id: string; name: string; department: string; company_id: string | null
  company_code: string; user_id: number | null; can_receive_leave: boolean
}
type RiskWarning = { code: string; severity: 'high' | 'medium' | string; message: string }
type OnboardingRequest = {
  id: string; correlation_id: string; status: string; full_name: string; email: string
  employee_number: string; department: string; job_title: string; hire_date: string
  company: Company; manager: { id: string; name: string } | null
  default_leave_approver: { id: number; name: string } | null
  risk_warnings: RiskWarning[]; maker: string; can_decide: boolean; created_at: string
}

export default function HrisOnboardPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [companies, setCompanies] = useState<Company[]>([])
  const [managers, setManagers] = useState<Manager[]>([])
  const [departments, setDepartments] = useState<string[]>([])
  const [queue, setQueue] = useState<OnboardingRequest[]>([])
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [employeeNumber, setEmployeeNumber] = useState('')
  const [companyId, setCompanyId] = useState('')
  const [department, setDepartment] = useState('')
  const [jobTitle, setJobTitle] = useState('')
  const [hireDate, setHireDate] = useState(today())
  const [phone, setPhone] = useState('')
  const [annualLeave, setAnnualLeave] = useState('')
  const [managerId, setManagerId] = useState('')
  const [leaveApproverId, setLeaveApproverId] = useState('')
  const [decisionNotes, setDecisionNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [loadingQueue, setLoadingQueue] = useState(false)
  const [deciding, setDeciding] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => { if (allowed === false) router.replace('/dashboard') }, [allowed, router])

  const loadData = useCallback(async () => {
    if (allowed !== true) return
    setLoadingQueue(true)
    try {
      const [optionsResponse, queueResponse] = await Promise.all([
        authedHrisFetch('/hris/api/onboarding/options/'),
        authedHrisFetch('/hris/api/onboarding/queue/?status=pending'),
      ])
      const options = await optionsResponse.json().catch(() => ({}))
      const pending = await queueResponse.json().catch(() => ({}))
      if (!optionsResponse.ok) throw new Error(options.detail || 'Could not load onboarding options.')
      if (!queueResponse.ok) throw new Error(pending.detail || 'Could not load the approval queue.')
      setCompanies(options.companies || [])
      setManagers(options.managers || [])
      setDepartments(options.departments || [])
      setQueue(pending.requests || [])
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Could not load onboarding data.' })
    } finally {
      setLoadingQueue(false)
    }
  }, [allowed])

  useEffect(() => { void loadData() }, [loadData])

  const scopedManagers = useMemo(
    () => managers.filter(m => !companyId || m.company_id === companyId),
    [managers, companyId],
  )
  const leaveApprovers = useMemo(
    () => scopedManagers.filter(m => m.can_receive_leave && m.user_id),
    [scopedManagers],
  )

  function selectManager(id: string) {
    setManagerId(id)
    const manager = managers.find(m => m.id === id)
    if (manager?.user_id && manager.can_receive_leave) setLeaveApproverId(String(manager.user_id))
  }

  async function submit() {
    if (!fullName.trim() || !email.trim() || !companyId || !department || !hireDate) {
      setMsg({ ok: false, text: 'Full name, work email, entity, department and start date are required.' }); return
    }
    setBusy(true); setMsg(null)
    try {
      const response = await authedHrisFetch('/hris/api/onboard-employee/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: fullName.trim(), email: email.trim(), employee_number: employeeNumber.trim(),
          company_id: companyId, department, job_title: jobTitle.trim(),
          hire_date: hireDate, phone: phone.trim(), manager_id: managerId || null,
          leave_approver_id: leaveApproverId || null,
          annual_leave_entitlement: annualLeave.trim() || null,
        }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not submit onboarding.')
      setMsg({
        ok: true,
        text: data.idempotent_retry
          ? `${data.full_name} is already waiting for approval; the existing request was reused.`
          : `${data.full_name} was submitted for independent approval. No live employee was activated yet.`,
      })
      setFullName(''); setEmail(''); setEmployeeNumber(''); setDepartment(''); setJobTitle('')
      setPhone(''); setCompanyId(''); setManagerId(''); setLeaveApproverId(''); setHireDate(today())
      await loadData()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setBusy(false) }
  }

  async function decide(item: OnboardingRequest, action: 'approve' | 'reject') {
    const notes = (decisionNotes[item.id] || '').trim()
    if (action === 'reject' && !notes) {
      setMsg({ ok: false, text: 'Enter a rejection reason before rejecting a request.' }); return
    }
    setDeciding(item.id); setMsg(null)
    try {
      const response = await authedHrisFetch(`/hris/api/onboarding/${item.id}/decide/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, notes }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Could not ${action} request.`)
      setMsg({
        ok: true,
        text: action === 'approve'
          ? `${item.full_name} was approved and activated across payroll and HRIS.`
          : `${item.full_name}'s onboarding request was rejected; no employee record was activated.`,
      })
      await loadData()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setDeciding(null) }
  }

  if (allowed !== true) {
    return <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
      <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
    </div>
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inp = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }
  const lbl = { color: theme.t2 }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Onboard Employee" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Onboard' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris/directory" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to Directory
        </Link>

        <div className="rounded-xl px-4 py-3 flex items-start gap-3 max-w-4xl" style={{ ...card, background: theme.orange + '0D' }}>
          <ShieldCheck className="w-5 h-5 mt-0.5 shrink-0" style={{ color: theme.orange }} />
          <div>
            <p className="text-sm font-semibold" style={{ color: theme.text }}>Independent approval is required</p>
            <p className="text-xs mt-1" style={lbl}>Submitting this form creates a pending request only. A different authorised HR user must approve it before the employee becomes active.</p>
          </div>
        </div>

        <div className="rounded-2xl p-5 space-y-4 max-w-4xl" style={card}>
          <div>
            <h3 className="font-semibold flex items-center gap-2" style={{ color: theme.text }}>
              <UserPlus className="w-4 h-4" style={{ color: theme.orange }} /> Submit a new staff member
            </h3>
            <p className="text-xs mt-1" style={lbl}>The approver sees duplicate warnings, assignments and the complete request before activation.</p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Full name *" value={fullName} onChange={setFullName} placeholder="e.g. Amantle Adelaide Thake" inp={inp} lbl={lbl} wide />
            <Field label="Work email *" value={email} onChange={setEmail} placeholder="e.g. athake@alphadirect.co.bw" inp={inp} lbl={lbl} wide type="email" />
            <Field label="Employee number (optional)" value={employeeNumber} onChange={setEmployeeNumber} placeholder="e.g. ADI-0241" inp={inp} lbl={lbl} />
            <div>
              <label className="block text-xs mb-1" style={lbl}>Entity *</label>
              <select value={companyId} onChange={e => { setCompanyId(e.target.value); setManagerId(''); setLeaveApproverId('') }} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
                <option value="">— choose entity —</option>
                {companies.map(c => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="onboard-department" className="block text-xs mb-1" style={lbl}>Department *</label>
              <select id="onboard-department" required value={department} onChange={e => setDepartment(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
                <option value="">— choose department —</option>
                {departments.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <Field label="Job title" value={jobTitle} onChange={setJobTitle} placeholder="e.g. Client Onboarding Intern" inp={inp} lbl={lbl} />
            <div>
              <label className="block text-xs mb-1" style={lbl}>Start date *</label>
              <input type="date" value={hireDate} onChange={e => setHireDate(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
            </div>
            <Field label="Phone (optional)" value={phone} onChange={setPhone} placeholder="e.g. +267 71 234 567" inp={inp} lbl={lbl} />
            <div>
              <label className="block text-xs mb-1" style={lbl}>Annual leave entitlement (days/year)</label>
              <input type="number" min="0" max="365" step="0.5" value={annualLeave} onChange={e => setAnnualLeave(e.target.value)} placeholder="Leave blank for the standard 21 days" className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
              <p className="text-xs mt-1" style={lbl}>
                {annualLeave.trim() && Number(annualLeave) > 0
                  ? `Accrues ${(Number(annualLeave) / 12).toFixed(2)} days/month from the start date.`
                  : 'Accrues 1.75 days/month (21 ÷ 12) from the start date.'}
              </p>
            </div>
            <div>
              <label className="block text-xs mb-1" style={lbl}>Line manager</label>
              <select value={managerId} onChange={e => selectManager(e.target.value)} disabled={!companyId} className="w-full rounded-lg px-3 py-2 text-sm outline-none disabled:opacity-50" style={inp}>
                <option value="">— assign later —</option>
                {scopedManagers.map(m => <option key={m.id} value={m.id}>{m.name}{m.department ? ` · ${m.department}` : ''}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs mb-1" style={lbl}>Default leave approver</label>
              <select value={leaveApproverId} onChange={e => setLeaveApproverId(e.target.value)} disabled={!companyId} className="w-full rounded-lg px-3 py-2 text-sm outline-none disabled:opacity-50" style={inp}>
                <option value="">— line manager fallback —</option>
                {leaveApprovers.map(m => <option key={m.user_id} value={String(m.user_id)}>{m.name}</option>)}
              </select>
            </div>
          </div>

          {msg && <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2" style={{ background: (msg.ok ? theme.ok : theme.er) + '18', color: msg.ok ? theme.ok : theme.er }}>
            {msg.ok ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" /> : <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />}
            <span>{msg.text}</span>
          </div>}

          <div className="flex justify-end">
            <button onClick={submit} disabled={busy} className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" style={{ background: theme.orange }}>
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
              {busy ? 'Submitting…' : 'Submit for approval'}
            </button>
          </div>
        </div>

        <section className="max-w-4xl space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold" style={{ color: theme.text }}>Pending approval</h3>
              <p className="text-xs" style={lbl}>A request cannot be approved by the person who submitted it.</p>
            </div>
            <button onClick={() => void loadData()} disabled={loadingQueue} className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg" style={card}>
              <RefreshCw className={`w-3.5 h-3.5 ${loadingQueue ? 'animate-spin' : ''}`} /> Refresh
            </button>
          </div>

          {!loadingQueue && queue.length === 0 && <div className="rounded-xl p-5 text-sm" style={{ ...card, color: theme.t2 }}>No onboarding requests are waiting for approval.</div>}
          {queue.map(item => <div key={item.id} className="rounded-xl p-4 space-y-3" style={card}>
            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2">
              <div>
                <p className="font-semibold" style={{ color: theme.text }}>{item.full_name}</p>
                <p className="text-xs" style={lbl}>{item.email} · {item.company.code} · starts {item.hire_date}</p>
                <p className="text-xs mt-1" style={lbl}>{item.job_title || 'Job title not set'} · {item.department ? <>Department: <strong style={{ color: theme.text }}>{item.department}</strong></> : <strong style={{ color: theme.orange }}>No department</strong>} · submitted by {item.maker}</p>
              </div>
              <span className="text-[11px] rounded-full px-2 py-1 self-start" style={{ background: theme.orange + '18', color: theme.orange }}>Pending</span>
            </div>

            {(item.manager || item.default_leave_approver) && <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs" style={lbl}>
              <div>Line manager: <strong style={{ color: theme.text }}>{item.manager?.name || 'Not assigned'}</strong></div>
              <div>Leave approver: <strong style={{ color: theme.text }}>{item.default_leave_approver?.name || 'Line manager fallback'}</strong></div>
            </div>}

            {item.risk_warnings.length > 0 && <div className="rounded-lg p-3 space-y-1" style={{ background: '#F59E0B14', border: '1px solid #F59E0B45' }}>
              <p className="text-xs font-semibold flex items-center gap-1.5" style={{ color: '#B45309' }}><AlertTriangle className="w-3.5 h-3.5" /> Duplicate-risk review required</p>
              {item.risk_warnings.map((warning, index) => <p key={`${warning.code}-${index}`} className="text-xs" style={{ color: theme.text }}>• {warning.message}</p>)}
            </div>}

            <textarea value={decisionNotes[item.id] || ''} onChange={e => setDecisionNotes(v => ({ ...v, [item.id]: e.target.value }))} placeholder="Approval note (optional) or rejection reason (required)" rows={2} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
            <div className="flex justify-end gap-2">
              <button onClick={() => void decide(item, 'reject')} disabled={!item.can_decide || deciding === item.id} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold disabled:opacity-40" style={{ color: theme.er, border: `1px solid ${theme.er}55` }}>
                <XCircle className="w-3.5 h-3.5" /> Reject
              </button>
              <button onClick={() => void decide(item, 'approve')} disabled={!item.can_decide || deciding === item.id} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-semibold text-white disabled:opacity-40" style={{ background: theme.ok }}>
                {deciding === item.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />} Approve and activate
              </button>
            </div>
            {!item.can_decide && <p className="text-[11px] text-right" style={lbl}>A different authorised HR user must decide this request.</p>}
          </div>)}
        </section>
      </main>
    </div>
  )
}

function Field({ label, value, onChange, placeholder, inp, lbl, wide = false, type = 'text' }: {
  label: string; value: string; onChange: (value: string) => void; placeholder: string
  inp: React.CSSProperties; lbl: React.CSSProperties; wide?: boolean; type?: string
}) {
  return <div className={wide ? 'sm:col-span-2' : ''}>
    <label className="block text-xs mb-1" style={lbl}>{label}</label>
    <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
  </div>
}
