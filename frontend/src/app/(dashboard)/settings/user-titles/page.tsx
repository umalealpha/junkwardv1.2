'use client'

/**
 * /settings/user-titles — Set / change user job titles (CFO 2026-05-22).
 *
 * Mirrors the structure of /settings/user-access: three modes
 *   1. Person       — pick a user → dropdown title → Apply
 *   2. Inline edit  — edit titles directly in the list (one per row save)
 *   3. Upload Excel — .xlsx / .csv with name | username | email | title
 *
 * Title choices come from the backend (UserProfile.Title), so the
 * dropdown stays in sync if we ever add roles.
 */

import { useEffect, useMemo, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import {
  getUserTitles, setUserTitle, uploadUserTitlesFile,
  userTitlesTemplateUrl, getToken,
} from '@/lib/api'
import type {
  UserTitlesResponse, TitleUpdateReport, TitleUserRow, TitleChoice,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Check, RefreshCw, UserPlus, FileSpreadsheet, Upload, Download, Tag,
} from 'lucide-react'

type Mode = 'person' | 'inline' | 'upload'

export default function UserTitlesPage() {
  const router = useRouter()
  const [data, setData] = useState<UserTitlesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>('person')
  const [lastResult, setLastResult] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setData(await getUserTitles()) }
    catch (e) { setError(e instanceof Error ? e.message : 'Failed to load titles') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Job titles"
        breadcrumbs={[{ label: 'Settings', href: '/settings' }, { label: 'Job titles' }]}
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
              <Tag className="w-4 h-4 text-emerald-600" /> Manage job titles
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Title drives approval rights (CFO, FM, FC approve JEs; CFO + admins
            see everything). Set per user via the form, edit inline in the list,
            or upload a spreadsheet with <code>name / username / email / title</code>.
          </CardContent>
        </Card>

        <div className="flex gap-2">
          {[
            { id: 'person', label: 'Person',       icon: UserPlus },
            { id: 'inline', label: 'Inline edit',  icon: Tag },
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
  data: UserTitlesResponse
  reload: () => Promise<void>
  setError: (s: string | null) => void
  setLastResult: (s: string | null) => void
}) {
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<string | null>(null)
  const [title, setTitle] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const filtered = useMemo(() => {
    const x = q.trim().toLowerCase()
    if (!x) return data.users
    return data.users.filter(u =>
      u.username.toLowerCase().includes(x) ||
      u.email.toLowerCase().includes(x) ||
      u.full_name.toLowerCase().includes(x) ||
      (u.title || '').toLowerCase().includes(x),
    )
  }, [q, data])

  useEffect(() => {
    if (!picked) { setTitle(''); return }
    const u = data.users.find(x => x.username === picked)
    setTitle(u?.title || '')
  }, [picked, data])

  const pickedUser = data.users.find(u => u.username === picked)

  async function apply() {
    if (!picked || !title) { setError('Pick a person and a title.'); return }
    setBusy(true); setError(null); setLastResult(null)
    try {
      const r = await setUserTitle({ username: picked, title })
      setLastResult(`${picked} → ${title} · ${r.updated} updated, ${r.skipped} skipped.`)
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
            placeholder="Search name, email, title…"
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

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">
            2 — Set title
            {pickedUser && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                for <strong>{pickedUser.full_name || pickedUser.username}</strong>
                {pickedUser.title && <> · current: <em>{pickedUser.title.replace(/_/g, ' ')}</em></>}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!picked ? (
            <p className="text-sm text-muted-foreground">Pick someone on the left first.</p>
          ) : (
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">New title</label>
                <select
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  className="border rounded px-3 py-1.5 text-sm bg-background min-w-[260px]"
                >
                  <option value="">— pick —</option>
                  {data.choices.map(c => (
                    <option key={c.value} value={c.value}>{c.label}</option>
                  ))}
                </select>
              </div>
              <Button
                variant="primary" size="md"
                onClick={apply}
                loading={busy} disabled={busy || !title}
              >
                <Check className="w-4 h-4 mr-1" /> Apply
              </Button>
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
  data: UserTitlesResponse
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
      u.full_name.toLowerCase().includes(x) ||
      (u.title || '').toLowerCase().includes(x),
    )
  }, [q, data])

  async function saveOne(u: TitleUserRow) {
    const newTitle = edits[u.username] ?? u.title
    if (newTitle === u.title) return
    setBusyUser(u.username); setError(null); setLastResult(null)
    try {
      await setUserTitle({ username: u.username, title: newTitle })
      setLastResult(`${u.username} → ${newTitle}`)
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
        <CardTitle className="text-base">Inline edit — change title per row</CardTitle>
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
                <th className="text-right px-3 py-1.5"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(u => {
                const value = edits[u.username] ?? u.title
                const dirty = value !== u.title
                return (
                  <tr key={u.username} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="px-3 py-1 font-medium">{u.username}</td>
                    <td className="px-3 py-1 text-muted-foreground">{u.full_name}</td>
                    <td className="px-3 py-1">
                      <select
                        value={value}
                        onChange={e => setEdits(x => ({ ...x, [u.username]: e.target.value }))}
                        className="border rounded px-2 py-0.5 text-xs bg-background"
                      >
                        {data.choices.map(c => (
                          <option key={c.value} value={c.value}>{c.label}</option>
                        ))}
                      </select>
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
  const [report, setReport] = useState<TitleUpdateReport | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function onFile(file: File | null | undefined) {
    if (!file) return
    setBusy(true); setError(null); setLastResult(null); setReport(null)
    try {
      const r = await uploadUserTitlesFile(file)
      setReport(r)
      setLastResult(`${r.updated} updated, ${r.skipped} skipped.`)
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
            href={userTitlesTemplateUrl()}
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
          <code className="ml-1 px-1.5 py-0.5 bg-muted rounded text-xs">title</code>
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
                    <th className="text-left px-2 py-1">Title / reason</th>
                  </tr>
                </thead>
                <tbody>
                  {report.report.map((r, i) => (
                    <tr key={i} className="border-t">
                      <td className="px-2 py-0.5">{r.row}</td>
                      <td className="px-2 py-0.5">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                          r.status === 'skipped' ? 'bg-amber-100 text-amber-800'
                          : 'bg-emerald-100 text-emerald-800'
                        }`}>{r.status}</span>
                      </td>
                      <td className="px-2 py-0.5">{r.username || '—'}</td>
                      <td className="px-2 py-0.5">{r.title || r.reason || ''}</td>
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
