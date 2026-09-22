'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getRBACMe, getRBACRoles, getRBACRole, getRBACUsers, getRBACAudit,
  assignRBACRole, revokeRBACAssignment, getToken,
} from '@/lib/api'
import type {
  RBACRole, RBACRoleDetail, RBACUserView, RBACAssignment, RBACAuditEntry,
  RBACMeResponse, RBACDepartment,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { AlertCircle, CheckCircle, ChevronRight, Shield, Lock, X } from 'lucide-react'

// ─── helpers ───────────────────────────────────────────────────────────────

const LEVEL_NAMES: Record<number, string> = {
  0: 'Super Admin',
  1: 'Executive',
  2: 'Department Head',
  3: 'Manager',
  4: 'Senior',
  5: 'Officer',
  6: 'Read-Only',
  9: 'System',
}

const LEVEL_COLOURS: Record<number, string> = {
  0: 'bg-red-100 text-red-900 border-red-300',
  1: 'bg-orange-100 text-orange-900 border-orange-300',
  2: 'bg-amber-100 text-amber-900 border-amber-300',
  3: 'bg-yellow-100 text-yellow-900 border-yellow-300',
  4: 'bg-blue-100 text-blue-900 border-blue-300',
  5: 'bg-emerald-100 text-emerald-900 border-emerald-300',
  6: 'bg-slate-100 text-slate-700 border-slate-300',
  9: 'bg-gray-200 text-gray-700 border-gray-400',
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

// ─── main page ─────────────────────────────────────────────────────────────

type Tab = 'roles' | 'users' | 'audit'

export default function RolesPage() {
  const router = useRouter()
  const [tab, setTab] = useState<Tab>('roles')
  const [me, setMe] = useState<RBACMeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) {
      router.push('/login')
      return
    }
    getRBACMe()
      .then(setMe)
      .catch(e => setError(e.message ?? 'Failed to load profile'))
  }, [router])

  if (!me) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        {error ? (
          <div className="text-red-600 flex items-center gap-2">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        ) : (
          <span className="text-gray-500">Loading…</span>
        )}
      </div>
    )
  }

  const canAssign = me.permissions.includes('roles.assign') || me.legacy_admin || me.is_superuser

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar title="Roles & Hierarchy" />
      <div className="max-w-7xl mx-auto px-6 py-6 space-y-6">

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-2 rounded flex items-center gap-2">
            <AlertCircle className="w-4 h-4" /> {error}
            <button onClick={() => setError(null)} className="ml-auto"><X className="w-4 h-4" /></button>
          </div>
        )}
        {success && (
          <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 px-4 py-2 rounded flex items-center gap-2">
            <CheckCircle className="w-4 h-4" /> {success}
            <button onClick={() => setSuccess(null)} className="ml-auto"><X className="w-4 h-4" /></button>
          </div>
        )}

        {/* "Me" summary */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="w-5 h-5 text-[#0B1272]" />
              Your access
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
              <div>
                <div className="text-gray-500">Roles held</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {me.roles.length === 0 ? (
                    <span className="text-gray-400 italic">none — legacy admin only</span>
                  ) : me.roles.map(r => (
                    <span key={r.id}
                      className={`inline-flex items-center px-2 py-0.5 rounded border text-xs ${LEVEL_COLOURS[r.level] ?? ''}`}
                    >
                      {r.name}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-gray-500">Authority level</div>
                <div className="mt-1 font-medium">
                  {me.is_superuser ? '0 (Django superuser)' : `${me.max_authority_level} (${LEVEL_NAMES[me.max_authority_level] ?? '—'})`}
                </div>
              </div>
              <div>
                <div className="text-gray-500">Effective permissions</div>
                <div className="mt-1 font-medium">{me.permissions.length}</div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Tabs */}
        <div className="border-b border-gray-200">
          <nav className="flex gap-6">
            {(['roles','users','audit'] as Tab[]).map(t => (
              <button key={t}
                onClick={() => setTab(t)}
                className={`pb-3 text-sm font-medium border-b-2 ${tab === t
                  ? 'border-[#FF6600] text-[#0B1272]'
                  : 'border-transparent text-gray-500 hover:text-gray-700'}`}
              >
                {t === 'roles' ? 'Role Catalogue' : t === 'users' ? 'Users & Assignments' : 'Audit Trail'}
              </button>
            ))}
          </nav>
        </div>

        {tab === 'roles' && <RolesTab />}
        {tab === 'users' && (
          <UsersTab canAssign={canAssign} onSuccess={setSuccess} onError={setError} />
        )}
        {tab === 'audit' && <AuditTab />}
      </div>
    </div>
  )
}

// ─── Roles tab ─────────────────────────────────────────────────────────────

function RolesTab() {
  const [roles, setRoles] = useState<RBACRole[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [detail, setDetail] = useState<RBACRoleDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getRBACRoles().then(r => setRoles(r.results)).catch(e => setError(e.message))
  }, [])

  const toggle = async (id: string) => {
    if (expanded === id) {
      setExpanded(null); setDetail(null); return
    }
    setExpanded(id)
    setDetail(null)
    try {
      const d = await getRBACRole(id)
      setDetail(d)
    } catch (e: any) {
      setError(e.message)
    }
  }

  // Group by level
  const byLevel: Record<number, RBACRole[]> = {}
  for (const r of roles) {
    if (!byLevel[r.level]) byLevel[r.level] = []
    byLevel[r.level].push(r)
  }

  return (
    <div className="space-y-4">
      {error && <div className="text-red-600 text-sm">{error}</div>}
      {Object.keys(byLevel).map(n => Number(n)).sort((a,b) => a-b).map(level => (
        <Card key={level}>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${LEVEL_COLOURS[level] ?? ''}`}>
                L{level}
              </span>
              {LEVEL_NAMES[level] ?? `Level ${level}`}
              <span className="text-xs text-gray-500 font-normal">({byLevel[level].length} roles)</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              {byLevel[level].map(r => (
                <div key={r.id} className="border border-gray-200 rounded">
                  <button
                    onClick={() => toggle(r.id)}
                    className="w-full px-4 py-2 flex items-center gap-3 hover:bg-gray-50 text-left"
                  >
                    <ChevronRight className={`w-4 h-4 text-gray-400 transition-transform ${expanded === r.id ? 'rotate-90' : ''}`} />
                    <div className="flex-1">
                      <div className="font-medium text-sm">{r.name}</div>
                      <div className="text-xs text-gray-500">{r.code}{r.department_label ? ` · ${r.department_label}` : ''}</div>
                    </div>
                    <div className="text-xs text-gray-500">{r.permission_count} perms</div>
                  </button>
                  {expanded === r.id && (
                    <div className="border-t border-gray-200 px-4 py-3 bg-gray-50">
                      {detail ? (
                        <>
                          <div className="text-xs text-gray-700 mb-2">{detail.description}</div>
                          <div className="grid grid-cols-2 md:grid-cols-3 gap-1 text-xs">
                            {detail.permissions.map(p => (
                              <code key={p.code} className="text-gray-700 truncate" title={p.description}>{p.code}</code>
                            ))}
                          </div>
                        </>
                      ) : (
                        <div className="text-xs text-gray-500">Loading…</div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

// ─── Users tab ─────────────────────────────────────────────────────────────

function UsersTab({ canAssign, onSuccess, onError }: {
  canAssign: boolean
  onSuccess: (m: string) => void
  onError: (m: string) => void
}) {
  const [users, setUsers] = useState<RBACUserView[]>([])
  const [allRoles, setAllRoles] = useState<RBACRole[]>([])
  const [assignTarget, setAssignTarget] = useState<RBACUserView | null>(null)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    Promise.all([getRBACUsers(), getRBACRoles()])
      .then(([u, r]) => { setUsers(u.results); setAllRoles(r.results) })
      .catch(e => onError(e.message))
  }, [refresh, onError])

  const onRevoke = async (a: RBACAssignment) => {
    const reason = window.prompt(`Revoke ${a.role.name} from ${a.user.username}?\n\nReason (required for elevated roles):`)
    if (reason === null) return
    try {
      await revokeRBACAssignment(a.id, reason)
      onSuccess(`Revoked ${a.role.name} from ${a.user.username}`)
      setRefresh(x => x + 1)
    } catch (e: any) {
      onError(e.message ?? 'Revoke failed')
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs text-gray-500 uppercase">
              <tr>
                <th className="px-4 py-2">User</th>
                <th className="px-4 py-2">Email</th>
                <th className="px-4 py-2">Active roles</th>
                <th className="px-4 py-2">Authority</th>
                <th className="px-4 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id} className="border-t border-gray-100 align-top">
                  <td className="px-4 py-3">
                    <div className="font-medium">{u.username}</div>
                    {(u.first_name || u.last_name) && (
                      <div className="text-xs text-gray-500">{u.first_name} {u.last_name}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{u.email || '—'}</td>
                  <td className="px-4 py-3">
                    {u.active_assignments.length === 0 ? (
                      <span className="text-xs text-gray-400 italic">no roles</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {u.active_assignments.map(a => (
                          <span key={a.id}
                            className={`group inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs ${LEVEL_COLOURS[a.role.level] ?? ''}`}
                          >
                            {a.role.name}
                            {canAssign && (
                              <button onClick={() => onRevoke(a)} title="Revoke" className="opacity-50 hover:opacity-100">
                                <X className="w-3 h-3" />
                              </button>
                            )}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs font-medium">
                      L{u.max_authority_level === 99 ? '—' : u.max_authority_level}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {canAssign && (
                      <Button size="sm" variant="outline" onClick={() => setAssignTarget(u)}>
                        Assign role
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {assignTarget && (
        <AssignModal
          target={assignTarget}
          roles={allRoles}
          onClose={() => setAssignTarget(null)}
          onDone={(msg) => { onSuccess(msg); setAssignTarget(null); setRefresh(x => x+1) }}
          onError={onError}
        />
      )}
    </div>
  )
}

function AssignModal({ target, roles, onClose, onDone, onError }: {
  target: RBACUserView
  roles: RBACRole[]
  onClose: () => void
  onDone: (msg: string) => void
  onError: (m: string) => void
}) {
  const [roleId, setRoleId] = useState('')
  const [justification, setJustification] = useState('')
  const [scopeDept, setScopeDept] = useState<RBACDepartment | ''>('')
  const [expiresAt, setExpiresAt] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const selectedRole = roles.find(r => r.id === roleId)
  const elevated = !!selectedRole && selectedRole.level <= 2
  const justificationOk = !elevated || justification.trim().length > 0

  const submit = async () => {
    if (!roleId) return
    setSubmitting(true)
    try {
      await assignRBACRole({
        user: target.id,
        role: roleId,
        scope_department: (scopeDept || null) as RBACDepartment | null,
        justification: justification.trim(),
        expires_at: expiresAt || null,
      })
      onDone(`Assigned ${selectedRole?.name} to ${target.username}`)
    } catch (e: any) {
      onError(e.message ?? 'Assign failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg">
        <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Assign role to {target.username}</h2>
          <button onClick={onClose}><X className="w-5 h-5 text-gray-400" /></button>
        </div>
        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Role</label>
            <select value={roleId} onChange={e => setRoleId(e.target.value)}
              className="w-full h-10 border border-gray-300 rounded px-3 text-sm">
              <option value="">— Select a role —</option>
              {roles.sort((a,b) => a.level - b.level || a.name.localeCompare(b.name)).map(r => (
                <option key={r.id} value={r.id}>
                  L{r.level} · {r.name}{r.department_label ? ` (${r.department_label})` : ''}
                </option>
              ))}
            </select>
            {selectedRole && (
              <div className="mt-1 text-xs text-gray-500">{selectedRole.description}</div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">
              Justification {elevated && <span className="text-red-600">*</span>}
            </label>
            <textarea value={justification} onChange={e => setJustification(e.target.value)}
              rows={3}
              placeholder={elevated ? 'Required — business reason for this elevated grant' : 'Optional'}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm" />
            {elevated && !justificationOk && (
              <div className="text-xs text-red-600 mt-1">Justification is required for level ≤ 2 roles</div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium mb-1">Scope department (optional)</label>
              <select value={scopeDept} onChange={e => setScopeDept(e.target.value as RBACDepartment | '')}
                className="w-full h-10 border border-gray-300 rounded px-3 text-sm">
                <option value="">— default (role's own) —</option>
                <option value="finance">Finance</option>
                <option value="claims">Claims</option>
                <option value="underwriting">Underwriting</option>
                <option value="reinsurance">Reinsurance</option>
                <option value="compliance">Compliance &amp; Risk</option>
                <option value="hr">HR</option>
                <option value="it">IT</option>
                <option value="operations">Operations</option>
                <option value="external">External</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Expires (optional)</label>
              <input type="datetime-local" value={expiresAt} onChange={e => setExpiresAt(e.target.value)}
                className="w-full h-10 border border-gray-300 rounded px-3 text-sm" />
            </div>
          </div>
        </div>
        <div className="px-6 py-4 border-t border-gray-200 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!roleId || !justificationOk || submitting}>
            {submitting ? 'Assigning…' : 'Assign'}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── Audit tab ─────────────────────────────────────────────────────────────

function AuditTab() {
  const [entries, setEntries] = useState<RBACAuditEntry[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getRBACAudit().then(r => setEntries(r.results)).catch(e => setError(e.message))
  }, [])

  if (error) return <div className="text-red-600 text-sm">{error}</div>

  return (
    <Card>
      <CardContent className="p-0">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-left text-gray-500 uppercase">
            <tr>
              <th className="px-4 py-2">When</th>
              <th className="px-4 py-2">Action</th>
              <th className="px-4 py-2">Target user</th>
              <th className="px-4 py-2">Role</th>
              <th className="px-4 py-2">By</th>
              <th className="px-4 py-2">Detail</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(e => {
              const nv = e.new_values ?? {}
              const revoked = nv.revoked === true
              return (
                <tr key={e.id} className="border-t border-gray-100">
                  <td className="px-4 py-2">{formatDate(e.created_at)}</td>
                  <td className="px-4 py-2">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${revoked ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
                      {revoked ? 'revoke' : 'grant'}
                    </span>
                  </td>
                  <td className="px-4 py-2">{nv.username ?? '—'}</td>
                  <td className="px-4 py-2"><code>{nv.role_code ?? '—'}</code></td>
                  <td className="px-4 py-2">{e.user?.username ?? <span className="italic text-gray-400 flex items-center gap-1"><Lock className="w-3 h-3" />system</span>}</td>
                  <td className="px-4 py-2 text-gray-600">
                    {nv.bypass_hierarchy === true && <span className="text-amber-700 font-medium">[bootstrap] </span>}
                    {nv.justification || nv.revocation_reason || ''}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}
