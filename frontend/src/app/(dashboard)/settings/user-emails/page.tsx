'use client'

/**
 * /settings/user-emails — Master user list + email IDs (CFO 2026-05-22).
 *
 * Three modes (same architecture as user-titles / user-access):
 *   1. Person       — pick a user → edit email/first/last → Apply
 *   2. Inline edit  — edit emails directly in the list (one per row save)
 *   3. Upload Excel — .xlsx / .csv with name | username | email |
 *                     first_name | last_name | title
 *
 * Upload mode creates new users when only an email is provided
 * (username derived from email local-part). Person + Inline modes only
 * update existing users.
 */

import { useEffect, useMemo, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import {
  getUserEmails, setUserEmail, uploadUserEmailsFile,
  userEmailsTemplateUrl, getToken,
} from '@/lib/api'
import type {
  UserEmailsResponse, EmailUpdateReport, EmailUserRow,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Check, RefreshCw, UserPlus, FileSpreadsheet, Upload, Download, Mail, ShieldAlert,
} from 'lucide-react'

type Mode = 'person' | 'inline' | 'upload'

export default function UserEmailsPage() {
  const router = useRouter()
  const [data, setData] = useState<UserEmailsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('person')
  const [lastResult, setLastResult] = useState<string | null>(null)
  const [denied, setDenied] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setData(await getUserEmails()); setDenied(false) }
    catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load users'
      // The server refuses this endpoint to non-admins. Say so, rather than
      // leaving a blank page that still looks like a working admin tool
      // (found 2026-07-25 by the non-admin QA account).
      if (/\b403\b|permission|forbidden/i.test(msg)) setDenied(true)
      else setError(msg)
    }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Email IDs"
        breadcrumbs={[{ label: 'Settings', href: '/settings' }, { label: 'Email IDs' }]}
        actions={
          <Button variant="secondary" size="sm" onClick={load} loading={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        }
      />

      <main className="flex-1 px-6 py-6 space-y-6">

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Mail className="w-4 h-4 text-emerald-600" /> Master user list — email IDs
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Email is used for login (SSO match), password-reset routing, and the
            morning-check digest. Set per user via the form, edit inline in the
            list, or upload a spreadsheet with{' '}
            <code>name / username / email / first_name / last_name / title</code>.
            Upload mode <strong>creates new users</strong> if only an email is
            provided (username = email local-part).
          </CardContent>
        </Card>

        {denied && (
          <div className="max-w-xl">
            <div className="rounded-xl border border-red-200 bg-red-50 p-6 flex gap-3">
              <ShieldAlert className="h-5 w-5 text-red-600 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-medium text-red-800">Access restricted</p>
                <p className="text-sm text-red-700 mt-1">
                  Managing user email IDs is limited to administrators.
                </p>
              </div>
            </div>
          </div>
        )}

        {!denied && <div className="flex gap-2">
          {[
            { id: 'person', label: 'Person',       icon: UserPlus },
            { id: 'inline', label: 'Inline edit',  icon: Mail },
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
        </div>}

        {/* Never leave the page silently empty: say what is happening. */}
        {!denied && loading && !data && (
          <p className="text-sm text-muted-foreground">Loading the user list…</p>
        )}
        {!denied && !loading && !data && !error && (
          <Card className="border-amber-300 bg-amber-50/40">
            <CardContent className="py-3 text-sm text-amber-800">
              The user list didn’t load. Press Refresh — if it stays empty, you may not
              have permission for this page.
            </CardContent>
          </Card>
        )}

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

        {mode === 'person' && data && (
          <PersonMode data={data} reload={load}
                      setError={setError} setLastResult={setLastResult} />
        )}
        {mode === 'inline' && data && (
          <InlineMode data={data} reload={load}
                      setError={setError} setLastResult={setLastResult} />
        )}
        {mode === 'upload' && (
          <UploadMode reload={load}
                      setError={setError} setLastResult={setLastResult} />
        )}
      </main>
    </div>
  )
}


function PersonMode({
  data, reload, setError, setLastResult,
}: {
  data: UserEmailsResponse
  reload: () => Promise<void>
  setError: (s: string | null) => void
  setLastResult: (s: string | null) => void
}) {
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [email, setEmail] = useState('')
  const [first, setFirst] = useState('')
  const [last, setLast] = useState('')
  const [busy, setBusy] = useState(false)

  const filtered = useMemo(() => {
    const x = q.trim().toLowerCase()
    if (!x) return data.users
    return data.users.filter(u =>
      u.username.toLowerCase().includes(x) ||
      u.email.toLowerCase().includes(x) ||
      u.full_name.toLowerCase().includes(x),
    )
  }, [q, data])

  useEffect(() => {
    if (!picked) { setEmail(''); setFirst(''); setLast(''); return }
    const u = data.users.find(x => x.username === picked)
    setEmail(u?.email || '')
    setFirst(u?.first_name || '')
    setLast(u?.last_name || '')
  }, [picked, data])

  const pickedUser = data.users.find(u => u.username === picked)

  async function apply() {
    if (!picked) { setError('Pick a person first.'); return }
    if (!email) { setError('Email cannot be empty.'); return }
    setBusy(true); setError(null); setLastResult(null)
    try {
      const r = await setUserEmail({
        username: picked, email, first_name: first, last_name: last,
        create_if_missing: false,
      })
      setLastResult(`${picked} → ${email} · ${r.updated} updated, ${r.skipped} skipped.`)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Apply failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <Card className="lg:col-span-1">
        <CardHeader>
          <CardTitle className="text-base">1 — Pick a person</CardTitle>
        </CardHeader>
        <CardContent>
          <input
            type="search" autoFocus
            placeholder="Search name, email, username…"
            value={q} onChange={e => setQ(e.target.value)}
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
                  {u.email && <> · {u.email}</>}
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="text-xs text-muted-foreground px-3 py-3">No match</div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">
            2 — Edit email + name
            {pickedUser && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                for <strong>{pickedUser.full_name || pickedUser.username}</strong>
                {pickedUser.title && <> · {pickedUser.title.replace(/_/g, ' ')}</>}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!picked ? (
            <p className="text-sm text-muted-foreground">Pick someone on the left first.</p>
          ) : (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Email</label>
                <input
                  type="email" value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="firstname.lastname@alphadirect.co.bw"
                  className="w-full border rounded px-3 py-1.5 text-sm bg-background"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">First name</label>
                  <input
                    type="text" value={first}
                    onChange={e => setFirst(e.target.value)}
                    className="w-full border rounded px-3 py-1.5 text-sm bg-background"
                  />
                </div>
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">Last name</label>
                  <input
                    type="text" value={last}
                    onChange={e => setLast(e.target.value)}
                    className="w-full border rounded px-3 py-1.5 text-sm bg-background"
                  />
                </div>
              </div>
              <div className="pt-2">
                <Button
                  variant="primary" size="md"
                  onClick={apply}
                  loading={busy} disabled={busy || !email}
                >
                  <Check className="w-4 h-4 mr-1" /> Apply
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}


function InlineMode({
  data, reload, setError, setLastResult,
}: {
  data: UserEmailsResponse
  reload: () => Promise<void>
  setError: (s: string | null) => void
  setLastResult: (s: string | null) => void
}) {
  const [q, setQ] = useState('')
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [busyUser, setBusyUser] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const x = q.trim().toLowerCase()
    if (!x) return data.users
    return data.users.filter(u =>
      u.username.toLowerCase().includes(x) ||
      u.email.toLowerCase().includes(x) ||
      u.full_name.toLowerCase().includes(x),
    )
  }, [q, data])

  async function saveOne(u: EmailUserRow) {
    const newEmail = edits[u.username] ?? u.email
    if (newEmail === u.email) return
    if (!newEmail) { setError('Email cannot be empty.'); return }
    setBusyUser(u.username); setError(null); setLastResult(null)
    try {
      await setUserEmail({
        username: u.username, email: newEmail, create_if_missing: false,
      })
      setLastResult(`${u.username} → ${newEmail}`)
      await reload()
      setEdits(e => { const x = { ...e }; delete x[u.username]; return x })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusyUser(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Inline edit — change email per row</CardTitle>
      </CardHeader>
      <CardContent>
        <input
          type="search" placeholder="Filter users…"
          value={q} onChange={e => setQ(e.target.value)}
          className="w-full border rounded px-3 py-1.5 text-sm bg-background mb-3"
        />
        <div className="max-h-[600px] overflow-y-auto border rounded">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 sticky top-0">
              <tr>
                <th className="text-left px-3 py-1.5">Username</th>
                <th className="text-left px-3 py-1.5">Full name</th>
                <th className="text-left px-3 py-1.5">Title</th>
                <th className="text-left px-3 py-1.5">Email</th>
                <th className="text-right px-3 py-1.5"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(u => {
                const value = edits[u.username] ?? u.email
                const dirty = value !== u.email
                return (
                  <tr key={u.username} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="px-3 py-1 font-medium">{u.username}</td>
                    <td className="px-3 py-1 text-muted-foreground">{u.full_name}</td>
                    <td className="px-3 py-1 text-xs text-muted-foreground">
                      {u.title ? u.title.replace(/_/g, ' ') : '—'}
                    </td>
                    <td className="px-3 py-1">
                      <input
                        type="email" value={value}
                        onChange={e => setEdits(x => ({ ...x, [u.username]: e.target.value }))}
                        className="w-full border rounded px-2 py-0.5 text-xs bg-background"
                      />
                    </td>
                    <td className="px-3 py-1 text-right">
                      <Button
                        variant={dirty ? 'primary' : 'ghost'}
                        size="sm"
                        onClick={() => saveOne(u)}
                        loading={busyUser === u.username}
                        disabled={!dirty || busyUser === u.username}
                      >
                        {dirty ? 'Save' : '—'}
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}


function UploadMode({
  reload, setError, setLastResult,
}: {
  reload: () => Promise<void>
  setError: (s: string | null) => void
  setLastResult: (s: string | null) => void
}) {
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<EmailUpdateReport | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function onFile(file: File | null | undefined) {
    if (!file) return
    setBusy(true); setError(null); setLastResult(null); setReport(null)
    try {
      const r = await uploadUserEmailsFile(file)
      setReport(r)
      setLastResult(`${r.updated} updated, ${r.created} created, ${r.skipped} skipped.`)
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
            href={userEmailsTemplateUrl()}
            className="text-xs text-[#F07F00] hover:underline inline-flex items-center gap-1"
          >
            <Download className="w-3 h-3" /> Download template
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-3">
          Columns expected:
          <code className="ml-2 px-1.5 py-0.5 bg-muted rounded text-xs">name</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">username</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">email</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">first_name</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">last_name</code>
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">title</code>
        </p>
        <p className="text-xs text-muted-foreground mb-3">
          If <code>username</code> is blank and the email belongs to an unknown
          user, a new user is created with username = email local-part.
        </p>

        <div
          onDragOver={e => { e.preventDefault() }}
          onDrop={e => { e.preventDefault(); onFile(e.dataTransfer.files?.[0]) }}
          className="border-2 border-dashed rounded-lg p-8 text-center hover:bg-muted/20 cursor-pointer"
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="w-6 h-6 mx-auto mb-2 text-muted-foreground" />
          <p className="text-sm">{busy ? 'Processing…' : 'Drop a file here or click to pick.'}</p>
          <p className="text-xs text-muted-foreground mt-1">.xlsx, .xlsm, or .csv</p>
          <input
            ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv"
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
                    <th className="text-left px-2 py-1">Email / reason</th>
                  </tr>
                </thead>
                <tbody>
                  {report.report.map((r, i) => (
                    <tr key={i} className="border-t">
                      <td className="px-2 py-0.5">{r.row}</td>
                      <td className="px-2 py-0.5">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                          r.status === 'skipped' ? 'bg-amber-100 text-amber-800'
                          : r.status === 'created' ? 'bg-sky-100 text-sky-800'
                          : 'bg-emerald-100 text-emerald-800'
                        }`}>{r.status}</span>
                      </td>
                      <td className="px-2 py-0.5">{r.username || '—'}</td>
                      <td className="px-2 py-0.5">{r.email || r.reason || ''}</td>
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
