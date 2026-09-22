'use client'

/**
 * /settings/user-access — Per-user entity allowlist (CFO directive 2026-05-22).
 *
 * Three input modes, picked via the tab bar at the top:
 *   1. Person       — pick ONE person → tick companies + access level → Apply
 *   2. Bulk         — multi-select users × multi-select companies × action
 *   3. Upload Excel — drop .xlsx / .csv (name / username / email / company /
 *                     access / title). Aria-style fuzzy name resolution
 *                     on the backend; report shown row-by-row after parse.
 *
 * Matrix table is always visible at the bottom so the CFO sees the
 * current state at a glance.
 */

import { useEffect, useMemo, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import {
  getUserAccessMatrix, bulkUserCompanyAccess,
  uploadUserAccessFile, userAccessTemplateUrl, getToken,
} from '@/lib/api'
import type {
  UserAccessMatrix, AccessUploadReport,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Check, X, RefreshCw, Users, Building2, ShieldCheck, Upload,
  Download, UserPlus, FileSpreadsheet,
} from 'lucide-react'

type Mode = 'person' | 'bulk' | 'upload'
type AccessLevel = 'view' | 'rw'

export default function UserAccessPage() {
  const router = useRouter()
  const [matrix, setMatrix] = useState<UserAccessMatrix | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('person')
  const [lastResult, setLastResult] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setMatrix(await getUserAccessMatrix()) }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to load matrix') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="User access — entity allowlist"
        breadcrumbs={[{ label: 'Settings', href: '/settings' }, { label: 'User access' }]}
        actions={
          <Button variant="secondary" size="sm" onClick={load} loading={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        }
      />

      <main className="flex-1 px-6 py-6 space-y-6">

        {/* Banner */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              Grant a person access to one or many companies
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Three ways to do it — pick what's easiest. Person mode is fastest
            for one new joiner; Bulk for a quick row-and-column sweep; Upload
            Excel when HR sends a list.
          </CardContent>
        </Card>

        {/* Mode tabs */}
        <div className="flex gap-2">
          {[
            { id: 'person', label: 'Person',       icon: UserPlus },
            { id: 'bulk',   label: 'Bulk',         icon: Users },
            { id: 'upload', label: 'Upload Excel', icon: FileSpreadsheet },
          ].map(t => {
            const Icon = t.icon
            const active = mode === t.id
            return (
              <Button
                key={t.id}
                variant={active ? 'primary' : 'secondary'}
                size="sm"
                onClick={() => setMode(t.id as Mode)}
              >
                <Icon className="w-4 h-4 mr-1" /> {t.label}
              </Button>
            )
          })}
        </div>

        {error && (
          <Card className="border-red-300 bg-red-50/40 dark:bg-red-950/20">
            <CardContent className="py-3 text-sm text-red-700">{error}</CardContent>
          </Card>
        )}
        {lastResult && (
          <Card className="border-emerald-300 bg-emerald-50/40">
            <CardContent className="py-3 text-sm text-emerald-800">{lastResult}</CardContent>
          </Card>
        )}

        {/* Mode panels */}
        {mode === 'person' && matrix && (
          <PersonMode matrix={matrix} reload={load}
                      setError={setError} setLastResult={setLastResult} />
        )}
        {mode === 'bulk' && matrix && (
          <BulkMode matrix={matrix} reload={load}
                    setError={setError} setLastResult={setLastResult} />
        )}
        {mode === 'upload' && (
          <UploadMode reload={load}
                      setError={setError} setLastResult={setLastResult} />
        )}

        {/* Matrix always visible */}
        {matrix && <Matrix matrix={matrix} />}
      </main>
    </div>
  )
}


/* -------------------------------------------------------------------- */
/* Mode: Person — fastest single-user flow                              */
/* -------------------------------------------------------------------- */
function PersonMode({
  matrix, reload, setError, setLastResult,
}: {
  matrix: UserAccessMatrix
  reload: () => Promise<void>
  setError: (s: string | null) => void
  setLastResult: (s: string | null) => void
}) {
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [cos, setCos] = useState<Set<string>>(new Set())
  const [level, setLevel] = useState<AccessLevel>('view')
  const [busy, setBusy] = useState(false)
  const [revoking, setRevoking] = useState(false)

  const filtered = useMemo(() => {
    const x = q.trim().toLowerCase()
    if (!x) return matrix.users
    return matrix.users.filter(u =>
      u.username.toLowerCase().includes(x) ||
      u.email.toLowerCase().includes(x) ||
      u.full_name.toLowerCase().includes(x) ||
      (u.title || '').toLowerCase().includes(x),
    )
  }, [q, matrix])

  // When a user is picked, pre-tick their current companies
  useEffect(() => {
    if (!picked || !matrix) return
    const g = matrix.grants[picked] || {}
    setCos(new Set(Object.keys(g)))
  }, [picked, matrix])

  const pickedUser = matrix.users.find(u => u.username === picked)

  function toggleCo(id: string) {
    setCos(s => { const x = new Set(s); x.has(id) ? x.delete(id) : x.add(id); return x })
  }

  async function apply(action: 'grant' | 'revoke') {
    if (!picked) { setError('Pick a person first.'); return }
    if (cos.size === 0) { setError('Tick at least one company.'); return }
    action === 'revoke' ? setRevoking(true) : setBusy(true)
    setError(null); setLastResult(null)
    try {
      const codes = matrix.companies.filter(c => cos.has(c.id)).map(c => c.code)
      const r = await bulkUserCompanyAccess({
        users: [picked], companies: codes, action,
        can_view: true, can_write: level === 'rw',
      })
      const touched = action === 'revoke' ? r.revoked : (r.created + r.updated)
      setLastResult(
        `${picked}: ${action === 'revoke' ? 'revoked' : (level === 'rw' ? 'granted rw' : 'granted view')} on ${codes.join(', ')} — ${touched} rows.`
      )
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Apply failed')
    } finally {
      action === 'revoke' ? setRevoking(false) : setBusy(false)
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Person list */}
      <Card className="lg:col-span-1">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Users className="w-4 h-4" /> 1 — Pick a person
          </CardTitle>
        </CardHeader>
        <CardContent>
          <input
            type="search" autoFocus
            placeholder="Search name, email, title…"
            value={q}
            onChange={e => setQ(e.target.value)}
            className="w-full border rounded px-3 py-1.5 text-sm bg-background mb-2"
          />
          <div className="max-h-[420px] overflow-y-auto border rounded">
            {filtered.map(u => (
              <button
                key={u.username}
                onClick={() => setPicked(u.username)}
                className={`w-full text-left px-3 py-1.5 text-sm border-b last:border-0 hover:bg-muted/30 ${
                  picked === u.username ? 'bg-orange-50' : ''
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{u.full_name || u.username}</span>
                  {u.is_superuser && (
                    <span className="text-[10px] uppercase tracking-wider text-amber-700">superuser</span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  {u.username}
                  {u.title && <> · {u.title.replace(/_/g, ' ')}</>}
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="text-xs text-muted-foreground px-3 py-3">No match</div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Companies + level */}
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Building2 className="w-4 h-4" />
            2 — Tag companies + access level
            {pickedUser && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                for <strong>{pickedUser.full_name || pickedUser.username}</strong>
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!picked ? (
            <p className="text-sm text-muted-foreground">Pick someone on the left first.</p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-1 mb-4">
                {matrix.companies.map(c => {
                  const checked = cos.has(c.id)
                  const existing = !!matrix.grants[picked]?.[c.id]
                  return (
                    <label
                      key={c.code}
                      className={`flex items-center gap-2 px-2 py-1.5 text-sm cursor-pointer rounded hover:bg-muted/30 ${
                        existing ? 'bg-blue-50/40' : ''
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleCo(c.id)}
                      />
                      <span className="font-mono font-medium">{c.code}</span>
                      <span className="text-xs text-muted-foreground truncate">{c.name}</span>
                    </label>
                  )
                })}
              </div>

              <div className="flex flex-wrap items-center gap-4">
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">Access level</label>
                  <select
                    value={level}
                    onChange={e => setLevel(e.target.value as AccessLevel)}
                    className="border rounded px-3 py-1.5 text-sm bg-background"
                  >
                    <option value="view">View only</option>
                    <option value="rw">View + Write</option>
                  </select>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="primary" size="md"
                    onClick={() => apply('grant')}
                    loading={busy} disabled={busy || revoking}
                  >
                    <Check className="w-4 h-4 mr-1" /> Apply
                  </Button>
                  <Button
                    variant="danger" size="md"
                    onClick={() => apply('revoke')}
                    loading={revoking} disabled={busy || revoking}
                  >
                    <X className="w-4 h-4 mr-1" /> Revoke ticked
                  </Button>
                </div>
                <span className="ml-auto text-xs text-muted-foreground">
                  {cos.size} of {matrix.companies.length} companies ticked
                </span>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}


/* -------------------------------------------------------------------- */
/* Mode: Bulk                                                           */
/* -------------------------------------------------------------------- */
function BulkMode({
  matrix, reload, setError, setLastResult,
}: {
  matrix: UserAccessMatrix
  reload: () => Promise<void>
  setError: (s: string | null) => void
  setLastResult: (s: string | null) => void
}) {
  const [userQuery, setUserQuery] = useState('')
  const [selUsers, setSelUsers] = useState<Set<string>>(new Set())
  const [selCos,   setSelCos]   = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<false | 'grant_view' | 'grant_write' | 'revoke'>(false)

  const filteredUsers = useMemo(() => {
    const q = userQuery.trim().toLowerCase()
    if (!q) return matrix.users
    return matrix.users.filter(u =>
      u.username.toLowerCase().includes(q) ||
      u.email.toLowerCase().includes(q) ||
      u.full_name.toLowerCase().includes(q) ||
      (u.title || '').toLowerCase().includes(q),
    )
  }, [userQuery, matrix])

  const toggleU = (n: string) => setSelUsers(s => { const x = new Set(s); x.has(n) ? x.delete(n) : x.add(n); return x })
  const toggleC = (c: string) => setSelCos(s => { const x = new Set(s); x.has(c) ? x.delete(c) : x.add(c); return x })

  async function run(action: 'grant_view' | 'grant_write' | 'revoke') {
    if (selUsers.size === 0 || selCos.size === 0) {
      setError('Pick at least one user and one company.'); return
    }
    setBusy(action); setError(null); setLastResult(null)
    try {
      const r = await bulkUserCompanyAccess({
        users: Array.from(selUsers),
        companies: Array.from(selCos),
        action: action === 'revoke' ? 'revoke' : 'grant',
        can_view: true,
        can_write: action === 'grant_write',
      })
      const verb = action === 'revoke' ? 'revoked' : (action === 'grant_write' ? 'granted rw' : 'granted view')
      const touched = action === 'revoke' ? r.revoked : (r.created + r.updated)
      setLastResult(
        `${verb}: ${touched} rows — ${r.users_resolved.length} users × ${r.companies_resolved.length} companies`
      )
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Bulk op failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center justify-between">
            <span className="flex items-center gap-2"><Users className="w-4 h-4" />Users ({selUsers.size})</span>
            <span className="flex gap-2">
              <Button size="sm" variant="ghost" onClick={() => setSelUsers(new Set(filteredUsers.map(u => u.username)))}>All</Button>
              <Button size="sm" variant="ghost" onClick={() => setSelUsers(new Set())}>Clear</Button>
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <input
            type="search" placeholder="Filter…" value={userQuery}
            onChange={e => setUserQuery(e.target.value)}
            className="w-full border rounded px-3 py-1.5 text-sm bg-background mb-2"
          />
          <div className="max-h-[360px] overflow-y-auto border rounded">
            {filteredUsers.map(u => (
              <label key={u.username} className="flex items-center gap-3 px-3 py-1.5 text-sm border-b last:border-0 cursor-pointer hover:bg-muted/30">
                <input type="checkbox" checked={selUsers.has(u.username)} onChange={() => toggleU(u.username)} />
                <span className="font-medium">{u.username}</span>
                <span className="text-xs text-muted-foreground">{u.full_name}</span>
              </label>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center justify-between">
            <span className="flex items-center gap-2"><Building2 className="w-4 h-4" />Companies ({selCos.size})</span>
            <span className="flex gap-2">
              <Button size="sm" variant="ghost" onClick={() => setSelCos(new Set(matrix.companies.map(c => c.code)))}>All</Button>
              <Button size="sm" variant="ghost" onClick={() => setSelCos(new Set())}>Clear</Button>
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-1 max-h-[360px] overflow-y-auto">
            {matrix.companies.map(c => (
              <label key={c.code} className="flex items-center gap-2 px-2 py-1.5 text-sm cursor-pointer hover:bg-muted/30 rounded">
                <input type="checkbox" checked={selCos.has(c.code)} onChange={() => toggleC(c.code)} />
                <span className="font-medium">{c.code}</span>
                <span className="text-xs text-muted-foreground truncate">{c.name}</span>
              </label>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="lg:col-span-2">
        <Card>
          <CardContent className="py-4 flex flex-wrap items-center gap-3">
            <Button variant="primary" size="md" onClick={() => run('grant_view')} loading={busy === 'grant_view'} disabled={!!busy}>
              <Check className="w-4 h-4 mr-1" /> Grant VIEW
            </Button>
            <Button variant="primary" size="md" onClick={() => run('grant_write')} loading={busy === 'grant_write'} disabled={!!busy}>
              <Check className="w-4 h-4 mr-1" /> Grant VIEW + WRITE
            </Button>
            <Button variant="danger" size="md" onClick={() => run('revoke')} loading={busy === 'revoke'} disabled={!!busy}>
              <X className="w-4 h-4 mr-1" /> Revoke
            </Button>
            <span className="ml-auto text-xs text-muted-foreground">
              {selUsers.size} × {selCos.size} = <strong>{selUsers.size * selCos.size}</strong> rows
            </span>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}


/* -------------------------------------------------------------------- */
/* Mode: Upload Excel                                                   */
/* -------------------------------------------------------------------- */
function UploadMode({
  reload, setError, setLastResult,
}: {
  reload: () => Promise<void>
  setError: (s: string | null) => void
  setLastResult: (s: string | null) => void
}) {
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<AccessUploadReport | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function onFile(file: File | null | undefined) {
    if (!file) return
    setBusy(true); setError(null); setLastResult(null); setReport(null)
    try {
      const r = await uploadUserAccessFile(file)
      setReport(r)
      setLastResult(
        `${r.rows_processed} rows: created ${r.created}, updated ${r.updated}, revoked ${r.revoked}, skipped ${r.report.filter(x => x.status === 'skipped').length}.`
      )
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center justify-between">
          <span className="flex items-center gap-2">
            <FileSpreadsheet className="w-4 h-4" /> Upload .xlsx / .csv
          </span>
          <a
            href={userAccessTemplateUrl()}
            className="text-xs text-[#F07F00] hover:underline inline-flex items-center gap-1"
          >
            <Download className="w-3 h-3" /> Download template
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-3">
          Columns expected (any order, header row required):
          <code className="ml-2 px-1.5 py-0.5 bg-muted rounded text-xs">name</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">username</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">email</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">company</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">access</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">title</code>
        </p>
        <p className="text-xs text-muted-foreground mb-3">
          <strong>access</strong> = <code>view</code> / <code>rw</code> / <code>revoke</code>.{' '}
          <strong>company</strong> = code (ADIC / VCM / QIH / …) or UUID.{' '}
          Person resolved by username → email → full name → first-name fuzzy.
        </p>

        <div
          onDragOver={e => { e.preventDefault() }}
          onDrop={e => { e.preventDefault(); onFile(e.dataTransfer.files?.[0]) }}
          className="border-2 border-dashed rounded-lg p-8 text-center hover:bg-muted/20 cursor-pointer"
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="w-6 h-6 mx-auto mb-2 text-muted-foreground" />
          <p className="text-sm">
            {busy ? 'Processing…' : 'Drop a file here or click to pick.'}
          </p>
          <p className="text-xs text-muted-foreground mt-1">.xlsx, .xlsm, or .csv</p>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xlsm,.csv"
            className="hidden"
            onChange={e => onFile(e.target.files?.[0])}
            disabled={busy}
          />
        </div>

        {report && (
          <div className="mt-4">
            <p className="text-sm font-medium mb-2">Per-row report</p>
            <div className="max-h-[300px] overflow-y-auto border rounded">
              <table className="w-full text-xs">
                <thead className="bg-muted/40">
                  <tr>
                    <th className="text-left px-2 py-1">Row</th>
                    <th className="text-left px-2 py-1">Status</th>
                    <th className="text-left px-2 py-1">User</th>
                    <th className="text-left px-2 py-1">Company</th>
                    <th className="text-left px-2 py-1">Access / reason</th>
                  </tr>
                </thead>
                <tbody>
                  {report.report.map((r, i) => (
                    <tr key={i} className="border-t">
                      <td className="px-2 py-0.5">{r.row}</td>
                      <td className="px-2 py-0.5">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                          r.status === 'skipped' ? 'bg-amber-100 text-amber-800'
                          : r.status === 'revoked' ? 'bg-red-100 text-red-800'
                          : 'bg-emerald-100 text-emerald-800'
                        }`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="px-2 py-0.5">{r.user || '—'}</td>
                      <td className="px-2 py-0.5">{r.company || '—'}</td>
                      <td className="px-2 py-0.5">{r.access || r.reason || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}


/* -------------------------------------------------------------------- */
/* Matrix view (always visible at the bottom)                           */
/* -------------------------------------------------------------------- */
function Matrix({ matrix }: { matrix: UserAccessMatrix }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Current matrix</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr>
              <th className="sticky left-0 bg-background text-left py-2 px-2 border-b">User</th>
              {matrix.companies.map(c => (
                <th key={c.code} className="text-center px-2 border-b font-mono">{c.code}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.users.map(u => (
              <tr key={u.username} className="border-b hover:bg-muted/20">
                <td className="sticky left-0 bg-background py-1 px-2 whitespace-nowrap">
                  <span className="font-medium">{u.username}</span>
                  {u.is_superuser && <span className="ml-1 text-[10px] text-amber-700">●</span>}
                </td>
                {matrix.companies.map(c => {
                  const g = matrix.grants[u.username]?.[c.id]
                  const state = u.is_superuser ? 'rw' : (g?.can_write ? 'rw' : (g?.can_view ? 'r' : '-'))
                  const cls = state === 'rw' ? 'bg-emerald-100 text-emerald-700'
                    : state === 'r' ? 'bg-blue-50 text-blue-700'
                    : 'text-muted-foreground'
                  return (
                    <td key={c.code} className="text-center px-1 py-0.5">
                      <span className={`inline-block w-7 px-1 rounded ${cls}`}>
                        {state === '-' ? '—' : state}
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}
