'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getMe, getUserProfiles, createUserProfile, updateUserProfile,
  deactivateUserProfile, getToken,
} from '@/lib/api'
import type {
  UserProfile, UserTitle, UserRole,
  CreateUserProfileInput, UpdateUserProfileInput,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, CheckCircle, Plus, Edit2, UserX,
  Shield, ShieldCheck, ArrowLeft, Lock, Search, X,
} from 'lucide-react'

const TITLE_OPTIONS: { value: UserTitle; label: string; canApprove: boolean }[] = [
  { value: 'ceo',                  label: 'Chief Executive Officer',  canApprove: true },
  { value: 'coo',                  label: 'Chief Operating Officer',  canApprove: true },
  { value: 'cfo',                  label: 'Chief Financial Officer',  canApprove: true },
  { value: 'finance_manager',      label: 'Finance Manager',          canApprove: true },
  { value: 'financial_controller', label: 'Financial Controller',     canApprove: true },
  { value: 'accountant',           label: 'Accountant',               canApprove: false },
  { value: 'bookkeeper',           label: 'Bookkeeper',               canApprove: false },
  { value: 'finance_analyst',      label: 'Finance Analyst',          canApprove: false },
  { value: 'auditor',              label: 'Auditor (read-only)',      canApprove: false },
  { value: 'executive',            label: 'Executive (read-only)',    canApprove: false },
  { value: 'operations',           label: 'Operations Staff',         canApprove: false },
  { value: 'senior_operations',    label: 'Senior Operational Staff', canApprove: false },
  { value: 'system_api',           label: 'System / API',             canApprove: false },
]

const ROLE_OPTIONS: { value: UserRole; label: string }[] = [
  { value: 'finance_admin',    label: 'Finance Admin' },
  { value: 'accountant',       label: 'Accountant' },
  { value: 'finance_reviewer', label: 'Finance Reviewer' },
  { value: 'operations_staff', label: 'Operations Staff' },
  { value: 'executive',        label: 'Executive' },
  { value: 'system_api',       label: 'System API' },
]

export default function UsersPage() {
  const router = useRouter()
  const [me, setMe] = useState<UserProfile | null>(null)
  const [profiles, setProfiles] = useState<UserProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [editing, setEditing] = useState<UserProfile | null>(null)
  const [query, setQuery] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [meRes, list] = await Promise.all([getMe(), getUserProfiles()])
      setMe(meRes)
      setProfiles(list.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function flash(msg: string) {
    setSuccess(msg)
    setTimeout(() => setSuccess(null), 4000)
  }

  // Client-side search across the loaded profile list. Splits the query on
  // whitespace so "alana finance" matches a row containing both tokens in
  // any field. Case-insensitive. Empty query = no filter.
  const filteredProfiles = (() => {
    const q = query.trim().toLowerCase()
    if (!q) return profiles
    const tokens = q.split(/\s+/).filter(Boolean)
    return profiles.filter(p => {
      const hay = [
        p.username,
        p.first_name,
        p.last_name,
        p.email,
        p.department,
        p.title,
        p.title_display,
        p.role,
        p.role_display,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return tokens.every(t => hay.includes(t))
    })
  })()

  // The access administrator reaches this screen too — that is the whole
  // point of the flag (CFO 2026-09-15). The API refuses anything he may not do.
  if (!loading && me && !me.can_administer_users && !me.is_access_delegate) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar
          title="Users"
          breadcrumbs={[{ label: 'Settings' }, { label: 'Users' }]}
          actions={
            <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.back()}>
              Back
            </Button>
          }
        />
        <div className="flex-1 p-6">
          <Card>
            <CardContent className="p-12 text-center">
              <Lock className="w-10 h-10 mx-auto mb-3 text-[#9CA3AF]" strokeWidth={1.5} />
              <h2 className="text-lg font-semibold text-[#111827]">Administrator privileges required</h2>
              <p className="mt-2 text-sm text-[#6B7280] max-w-md mx-auto">
                Only the CFO or a user marked as administrator can manage users. Ask the CFO to grant
                you the appropriate access if you need it.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Users"
        breadcrumbs={[{ label: 'Settings' }, { label: 'Users' }]}
        actions={
          <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => setShowCreate(true)}>
            New User
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}
        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#047857]" />
            <p className="text-[#047857] text-sm">{success}</p>
          </div>
        )}

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <CardTitle>
                {query
                  ? `${filteredProfiles.length} of ${profiles.length} users`
                  : `${profiles.length} users`}
              </CardTitle>
              <div className="relative w-full sm:w-80">
                <Search
                  className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#9CA3AF]"
                  strokeWidth={1.75}
                />
                <input
                  type="search"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Search by name, email, username, title, role, dept…"
                  className="w-full pl-9 pr-9 py-2 text-sm rounded-md border border-[#D1D5DB] bg-white focus:outline-none focus:ring-2 focus:ring-[#F07F00] focus:border-transparent"
                  aria-label="Search users"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setQuery('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-[#9CA3AF] hover:text-[#374151]"
                    aria-label="Clear search"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {loading ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            ) : filteredProfiles.length === 0 ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">
                {query
                  ? `No users match “${query}”. Clear the filter to see all ${profiles.length}.`
                  : 'No users yet. Click “New User” above to add one.'}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">User</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Job Title</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Access Title</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Role</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase hidden md:table-cell">Department</th>
                      <th className="px-4 py-3 text-center text-xs font-semibold text-[#374151] uppercase">Approve JE</th>
                      <th className="px-4 py-3 text-center text-xs font-semibold text-[#374151] uppercase">Admin</th>
                      <th className="px-4 py-3 text-center text-xs font-semibold text-[#374151] uppercase">Status</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {filteredProfiles.map(p => (
                      <tr key={p.id} className={!p.is_active ? 'opacity-50' : ''}>
                        <td className="px-4 py-3">
                          <div className="font-medium text-[#111827]">{p.username}</div>
                          {(p.first_name || p.last_name) && (
                            <div className="text-xs text-[#6B7280]">{p.first_name} {p.last_name}</div>
                          )}
                          {p.email && <div className="text-xs text-[#9CA3AF]">{p.email}</div>}
                        </td>
                        <td className="px-4 py-3 text-[#111827]">{p.job_title || '—'}</td>
                        <td className="px-4 py-3 text-[#111827]">{p.title_display}</td>
                        <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{p.role_display}</td>
                        <td className="px-4 py-3 text-[#374151] hidden md:table-cell">{p.department || '—'}</td>
                        <td className="px-4 py-3 text-center">
                          {p.can_approve_journal_entries
                            ? <span className="inline-flex items-center gap-1 text-[#047857] text-xs"><CheckCircle className="w-3.5 h-3.5" /> Yes</span>
                            : <span className="text-xs text-[#9CA3AF]">No</span>}
                        </td>
                        <td className="px-4 py-3 text-center">
                          {p.can_administer_users
                            ? <span className="inline-flex items-center gap-1 text-[#F07F00] text-xs font-medium"><ShieldCheck className="w-3.5 h-3.5" /> Admin</span>
                            : <span className="text-xs text-[#9CA3AF]">—</span>}
                        </td>
                        <td className="px-4 py-3 text-center">
                          {p.is_active !== p.is_user_active
                            ? <span title={p.is_user_active
                                ? 'This person can still SIGN IN even though the profile is switched off. Switch the profile off again to close the login.'
                                : 'The profile is switched on but this person CANNOT sign in — their login account is closed.'}
                                className="inline-block px-2 py-0.5 rounded-md text-xs bg-[#FEF3C7] text-[#92400E] border border-[#FDE68A]">
                                {p.is_user_active ? 'Off, but can still sign in' : 'On, but cannot sign in'}
                              </span>
                            : p.is_active
                            ? <span className="inline-block px-2 py-0.5 rounded-md text-xs bg-[#ECFDF5] text-[#047857] border border-[#A7F3D0]">Active</span>
                            : <span className="inline-block px-2 py-0.5 rounded-md text-xs bg-[#F3F4F6] text-[#6B7280] border border-[#D1D5DB]">Inactive</span>}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Button variant="ghost" size="sm" leftIcon={<Edit2 className="w-3.5 h-3.5" />} onClick={() => setEditing(p)}>Edit</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Title permissions reference</CardTitle></CardHeader>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            <div>
              <p className="font-medium text-[#111827] mb-2 flex items-center gap-1.5">
                <Shield className="w-4 h-4 text-[#F07F00]" /> Approver titles (can approve & post JEs)
              </p>
              <ul className="text-[#374151] space-y-1">
                <li>• Chief Financial Officer</li>
                <li>• Finance Manager</li>
                <li>• Financial Controller</li>
              </ul>
            </div>
            <div>
              <p className="font-medium text-[#111827] mb-2 flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-[#F07F00]" /> Administrators (can manage users)
              </p>
              <ul className="text-[#374151] space-y-1">
                <li>• Anyone with <span className="font-medium">CFO</span> title (automatic)</li>
                <li>• Anyone explicitly marked <span className="font-medium">is_administrator</span></li>
                <li>• Django superusers (set in admin panel)</li>
              </ul>
            </div>
          </CardContent>
          <CardContent className="px-4 pb-4 text-xs text-[#6B7280] border-t border-[#E5E7EB] pt-3">
            <strong>Segregation of duties</strong> is enforced at the API: a journal entry's approver must
            never be its creator. Even the CFO cannot approve their own entries — there must always be a
            second pair of eyes.
          </CardContent>
        </Card>
      </div>

      {showCreate && (
        <UserModal
          mode="create"
          onClose={() => setShowCreate(false)}
          onSave={async (data) => {
            await createUserProfile(data as CreateUserProfileInput)
            setShowCreate(false)
            flash('User created')
            load()
          }}
        />
      )}

      {editing && (
        <UserModal
          mode="edit"
          initial={editing}
          onClose={() => setEditing(null)}
          onSave={async (data) => {
            await updateUserProfile(editing.id, data as UpdateUserProfileInput)
            setEditing(null)
            flash('User updated')
            load()
          }}
          onDeactivate={async () => {
            if (!editing) return
            await deactivateUserProfile(editing.id)
            setEditing(null)
            flash('User deactivated')
            load()
          }}
        />
      )}
    </div>
  )
}

// ─── Modal ───────────────────────────────────────────────────────────────────

interface UserFormState {
  username: string
  password: string
  first_name: string
  last_name: string
  email: string
  role: UserRole
  title: UserTitle
  department: string
  is_administrator: boolean
  is_access_delegate: boolean
  is_active: boolean
}

function UserModal({
  mode, initial, onClose, onSave, onDeactivate,
}: {
  mode: 'create' | 'edit'
  initial?: UserProfile
  onClose: () => void
  onSave: (data: Partial<UserFormState>) => Promise<void>
  onDeactivate?: () => Promise<void>
}) {
  const [form, setForm] = useState<UserFormState>({
    username:        initial?.username        ?? '',
    password:        '',
    first_name:      initial?.first_name      ?? '',
    last_name:       initial?.last_name       ?? '',
    email:           initial?.email           ?? '',
    role:            initial?.role            ?? 'accountant',
    title:           initial?.title           ?? 'accountant',
    department:      initial?.department      ?? '',
    is_administrator: initial?.is_administrator ?? false,
    is_access_delegate: initial?.is_access_delegate ?? false,
    is_active:       initial?.is_active       ?? true,
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  function set<K extends keyof UserFormState>(key: K, value: UserFormState[K]) {
    setForm(prev => ({ ...prev, [key]: value }))
  }

  async function handleSave() {
    setBusy(true)
    setErr(null)
    try {
      const payload: Partial<UserFormState> = { ...form }
      if (mode === 'edit' && !form.password) delete payload.password
      if (mode === 'edit') delete payload.username  // keep username stable on edit
      await onSave(payload)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed')
      setBusy(false)
    }
  }

  const titleMeta = TITLE_OPTIONS.find(o => o.value === form.title)

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={() => !busy && onClose()}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-[#111827]">
          {mode === 'create' ? 'New user' : `Edit ${initial?.username}`}
        </h2>

        {err && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{err}</p>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Username *">
            <input
              type="text"
              value={form.username}
              onChange={e => set('username', e.target.value)}
              disabled={mode === 'edit' || busy}
              className={inputCls + (mode === 'edit' ? ' bg-[#F9FAFB]' : '')}
            />
          </Field>
          <Field label={mode === 'create' ? 'Password' : 'Reset password (optional)'}>
            <input type="password" value={form.password} onChange={e => set('password', e.target.value)} className={inputCls} disabled={busy} />
          </Field>
          <Field label="First name">
            <input type="text" value={form.first_name} onChange={e => set('first_name', e.target.value)} className={inputCls} disabled={busy} />
          </Field>
          <Field label="Last name">
            <input type="text" value={form.last_name} onChange={e => set('last_name', e.target.value)} className={inputCls} disabled={busy} />
          </Field>
          <Field label="Email" className="md:col-span-2">
            <input type="email" value={form.email} onChange={e => set('email', e.target.value)} className={inputCls} disabled={busy} />
          </Field>
          <Field label="Title *">
            <select value={form.title} onChange={e => set('title', e.target.value as UserTitle)} className={inputCls} disabled={busy}>
              {TITLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </Field>
          <Field label="Role *">
            <select value={form.role} onChange={e => set('role', e.target.value as UserRole)} className={inputCls} disabled={busy}>
              {ROLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </Field>
          <Field label="Department" className="md:col-span-2">
            <input type="text" value={form.department} onChange={e => set('department', e.target.value)} className={inputCls} disabled={busy} placeholder="Finance / IT / Operations / ..." />
          </Field>
        </div>

        <div className="border-t border-[#E5E7EB] pt-3 space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_administrator} onChange={e => set('is_administrator', e.target.checked)} disabled={busy} className="rounded" />
            <span><span className="font-medium">Administrator</span> — can grant/revoke roles, titles, and admin rights to other users</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_access_delegate} onChange={e => set('is_access_delegate', e.target.checked)} disabled={busy} className="rounded" />
            <span><span className="font-medium">Access administrator</span> — can grant and revoke everyday access, but never payroll, manager-level, financial or administrator rights, never their own account, and never someone who already holds a reserved title</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_active} onChange={e => set('is_active', e.target.checked)} disabled={busy} className="rounded" />
            <span><span className="font-medium">Active</span> — uncheck to disable login & disqualify from approvals</span>
          </label>
        </div>

        {titleMeta && (
          <p className="text-xs text-[#6B7280] bg-[#F9FAFB] border border-[#E5E7EB] rounded-md p-2.5">
            <strong>{titleMeta.label}</strong>:&nbsp;
            {titleMeta.canApprove
              ? '✅ This title carries journal-entry approval authority.'
              : 'ℹ️ This title cannot approve journal entries.'}
          </p>
        )}

        <div className="flex justify-between items-center pt-3 border-t border-[#E5E7EB]">
          <div>
            {mode === 'edit' && initial?.is_active && onDeactivate && (
              <Button variant="ghost" size="sm" leftIcon={<UserX className="w-3.5 h-3.5" />} onClick={onDeactivate} disabled={busy}>
                Deactivate
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant="accent" onClick={handleSave} disabled={busy || !form.username}>
              {busy ? 'Saving…' : mode === 'create' ? 'Create user' : 'Save changes'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

const inputCls =
  'w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all disabled:bg-[#F9FAFB] disabled:text-[#6B7280]'

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`block ${className || ''}`}>
      <span className="block text-xs font-medium text-[#374151] mb-1">{label}</span>
      {children}
    </label>
  )
}
