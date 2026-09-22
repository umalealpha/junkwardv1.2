'use client'

/**
 * Records register — where every physical file is, and who has it.
 *
 * Requested by Admin to replace a self-hosted HTML page. The
 * question this screen exists to answer, in one glance, is "who has this file?"
 * — so `Held by` is a column, not something you open a row to find out.
 */

import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  getRecords, getRecordCategories, getRecordMovements, moveRecord,
  getOverdueRecords, getRecordsDueForDestruction, createRecord, setRecordHold,
} from '@/lib/api'
import type { RecordItem, RecordCategory, RecordMovement } from '@/lib/api'
import { RequestFileButton, FileRequestsPanel } from './file-requests'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { StatTile } from '@/components/ui/StatTile'
import { useTheme } from '@/contexts/ThemeContext'
import {
  Search, AlertCircle, Archive, ArrowRightLeft, Clock, Lock, Plus, Trash2, Undo2, Unlock,
} from 'lucide-react'
import { localYmd } from '@/lib/utils'

const STATUS_STYLES: Record<string, string> = {
  in_store:  'bg-[#ECFDF5] text-[#047857] ring-[#A7F3D0]',
  issued:    'bg-[#FFF7ED] text-[#B45309] ring-[#FED7AA]',
  archived:  'bg-[#EFF6FF] text-[#1D4ED8] ring-[#BFDBFE]',
  destroyed: 'bg-[#F3F4F6] text-[#374151] ring-[#D1D5DB]',
  lost:      'bg-[#FEF2F2] text-[#B91C1C] ring-[#FECACA]',
}
const STATUS_LABEL: Record<string, string> = {
  in_store: 'In store', issued: 'Out', archived: 'Archived',
  destroyed: 'Destroyed', lost: 'Missing',
}
const KIND_LABEL: Record<string, string> = {
  issue: 'Issued out', return: 'Returned', transfer: 'Passed on',
  archive: 'Archived', destroy: 'Destroyed',
}

type View = 'all' | 'out' | 'overdue' | 'destruction'

export default function RecordsPage() {
  const { theme } = useTheme()
  const [records, setRecords] = useState<RecordItem[]>([])
  const [summary, setSummary] = useState({ total: 0, out: 0, destruction: 0 })
  const [categories, setCategories] = useState<RecordCategory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Why the register looks shorter than it is (bug 49c9d8d3).
  const [restrictedNotice, setRestrictedNotice] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [view, setView] = useState<View>('all')
  const [overdueCount, setOverdueCount] = useState(0)
  const [reqKey, setReqKey] = useState(0)

  // movement drawer
  const [active, setActive] = useState<RecordItem | null>(null)
  const [history, setHistory] = useState<RecordMovement[]>([])
  const [moveKind, setMoveKind] = useState('issue')
  const [moveTo, setMoveTo] = useState('')
  const [moveLocation, setMoveLocation] = useState('')
  const [moveReason, setMoveReason] = useState('')
  const [moveDue, setMoveDue] = useState('')
  const [moveDate, setMoveDate] = useState(() => localYmd(new Date()))
  const [moveError, setMoveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  // createPortal needs document; this page is a client component but still
  // pre-renders on the server.
  const [mounted, setMounted] = useState(false)
  // add-a-record form
  const [adding, setAdding] = useState(false)
  const [nRef, setNRef] = useState('')
  const [nTitle, setNTitle] = useState('')
  const [nCat, setNCat] = useState('')
  const [nDept, setNDept] = useState('')
  const [nConf, setNConf] = useState('internal')
  const [nLoc, setNLoc] = useState('')
  const [nRetention, setNRetention] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  useEffect(() => { setMounted(true) }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      if (view === 'overdue') {
        const r = await getOverdueRecords()
        // These two views don't report a withheld count, so clear the notice
        // rather than leave the All-records banner sitting over a different tab.
        setRestrictedNotice(null)
        setRecords(r.results); setOverdueCount(r.count)
      } else if (view === 'destruction') {
        const r = await getRecordsDueForDestruction()
        setRestrictedNotice(null)
        setRecords(r.results)
      } else {
        const r = await getRecords({
          q: search || undefined,
          category: categoryId || undefined,
          out: view === 'out' ? 'true' : undefined,
        })
        setRecords(r.results ?? [])
        // Bug 49c9d8d3: 53 of 55 records were withheld from the Records
        // officer's role and the screen said nothing, so two rows read as a
        // broken register. Access is unchanged — this only explains the gap.
        setRestrictedNotice(r.restricted_notice ?? null)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the register.')
    } finally {
      setLoading(false)
    }
  }, [view, search, categoryId])

  // Headline counts for the KPI row. Uses the server `count` (the role-scoped
  // total) — not results.length, which the API caps at its page size. Total/Out
  // reflect what this user may see, the same set the table shows, so the
  // restricted-access banner still explains any gap. Refetched after every
  // movement so the tiles never contradict the table one click later.
  const loadSummary = useCallback(() => {
    getOverdueRecords().then(r => setOverdueCount(r.count)).catch(() => {})
    Promise.all([
      getRecords({}).then(r => r.count ?? r.results?.length ?? 0).catch(() => 0),
      getRecords({ out: 'true' }).then(r => r.count ?? r.results?.length ?? 0).catch(() => 0),
      getRecordsDueForDestruction().then(r => r.count ?? r.results?.length ?? 0).catch(() => 0),
    ]).then(([total, out, destruction]) => setSummary({ total, out, destruction })).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    getRecordCategories().then(r => setCategories(r.results ?? [])).catch(() => {})
    loadSummary()
  }, [loadSummary])

  async function openRecord(rec: RecordItem) {
    setActive(rec); setMoveError(null); setHistory([])
    // Default to the move that actually makes sense for where the file is now,
    // so the common case is one click rather than a dropdown.
    setMoveKind(rec.status === 'issued' ? 'return' : 'issue')
    setMoveTo(''); setMoveLocation(''); setMoveReason(''); setMoveDue('')
    try {
      const h = await getRecordMovements(rec.id)
      setHistory(h.movements)
    } catch { /* history is nice to have; the move form still works */ }
  }

  async function submitMove() {
    if (!active) return
    setSaving(true); setMoveError(null)
    try {
      await moveRecord(active.id, {
        kind: moveKind,
        moved_at: moveDate,
        to_custodian: moveTo || undefined,
        to_location: moveLocation || undefined,
        reason: moveReason || undefined,
        due_back_on: moveDue || undefined,
      })
      setActive(null)
      await load()
      loadSummary()
    } catch (e) {
      setMoveError(e instanceof Error ? e.message : 'Could not record that movement.')
    } finally {
      setSaving(false)
    }
  }

  async function submitNew() {
    setSaving(true); setAddError(null)
    try {
      await createRecord({
        reference: nRef.trim(), title: nTitle.trim(), category: nCat,
        confidentiality: nConf,
        department: nDept.trim() || undefined,
        current_location: nLoc.trim() || undefined,
        retention_until: nRetention || undefined,
      })
      setAdding(false)
      setNRef(''); setNTitle(''); setNLoc(''); setNRetention(''); setNDept('')
      await load()
      loadSummary()
    } catch (e) {
      setAddError(e instanceof Error ? e.message : 'Could not add that record.')
    } finally { setSaving(false) }
  }

  async function toggleHold(rec: RecordItem) {
    const note = rec.legal_hold ? '' :
      (window.prompt('Why is this record being held?') || '').trim()
    if (!rec.legal_hold && !note) return   // a hold with no reason is not a hold
    try {
      await setRecordHold(rec.id, { legal_hold: !rec.legal_hold, legal_hold_note: note })
      await load()
      loadSummary()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not change the hold.')
    }
  }

  const TABS: { key: View; label: string; icon: typeof Clock }[] = [
    { key: 'all',         label: 'All records',        icon: Archive },
    { key: 'out',         label: 'Out',                icon: ArrowRightLeft },
    { key: 'overdue',     label: 'Overdue',            icon: Clock },
    { key: 'destruction', label: 'Due for destruction', icon: Trash2 },
  ]

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Records Register" />

      <div className="p-6 space-y-5">
        {/* Page hero */}
        <div className="flex items-start gap-4">
          <div>
            <div className="flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-[0.11em]"
                 style={{ color: theme.orangeText }}>
              <span className="w-[5px] h-[5px] rounded-full" style={{ background: theme.orange }} />
              Records
            </div>
            <h1 className="mt-1.5 text-[25px] leading-tight font-bold tracking-[-0.03em]"
                style={{ color: theme.text }}>
              Records Register
            </h1>
            <p className="mt-1.5 text-sm max-w-[62ch]" style={{ color: theme.t2 }}>
              Every physical file — where it is, who has it, and when it is due back.
            </p>
          </div>
          <Button className="ml-auto" onClick={() => { setAdding(true); setAddError(null) }}>
            <Plus className="w-4 h-4 mr-1" /> Add a record
          </Button>
        </div>

        {/* Headline numbers */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3.5">
          <StatTile label="On the register" value={summary.total} icon={Archive} tone="accent"
                    sub="physical files tracked" />
          <StatTile label="Out now" value={summary.out} icon={ArrowRightLeft} tone="info"
                    sub="with a person or firm" />
          <StatTile label="Overdue" value={overdueCount} icon={Clock}
                    tone={overdueCount > 0 ? 'warn' : 'accent'}
                    sub={overdueCount > 0 ? 'past their due-back date' : 'all back on time'}
                    subTone={overdueCount > 0 ? 'warn' : undefined} />
          <StatTile label="Due for destruction" value={summary.destruction} icon={Trash2}
                    tone={summary.destruction > 0 ? 'neg' : 'accent'} sub="retention expired" />
        </div>

        {/* View tabs */}
        <div className="flex flex-wrap gap-2">
          {TABS.map(t => {
            const on = view === t.key
            return (
              <button
                key={t.key}
                onClick={() => setView(t.key)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                style={on
                  ? { background: theme.oL, color: theme.orangeText, border: `1px solid ${theme.orange}` }
                  : { background: theme.card, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}
              >
                <t.icon className="w-4 h-4" />
                {t.label}
                {t.key === 'overdue' && overdueCount > 0 && (
                  <span className="ml-0.5 px-1.5 rounded-full text-[11px] font-semibold"
                        style={{ background: theme.wrB, color: theme.wr }}>
                    {overdueCount}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {view === 'all' && (
          <Card>
            <CardContent className="p-4 flex flex-wrap gap-3 items-end">
              <label className="flex flex-col gap-1 text-sm min-w-[240px] flex-1">
                <span className="text-[#6B7280]">Search</span>
                <span className="relative">
                  <Search className="w-4 h-4 absolute left-2 top-2.5 text-[#9CA3AF]" />
                  <input
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder="Reference, title or who has it…"
                    className="w-full pl-8 pr-3 py-2 border border-[#E5E7EB] rounded-md text-sm"
                  />
                </span>
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-[#6B7280]">Category</span>
                <select
                  value={categoryId}
                  onChange={e => setCategoryId(e.target.value)}
                  className="px-3 py-2 border border-[#E5E7EB] rounded-md text-sm"
                >
                  <option value="">All categories</option>
                  {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </label>
            </CardContent>
          </Card>
        )}

        {view === 'destruction' && (
          <p className="text-sm text-[#6B7280]">
            Retention has expired on these and none is under legal hold. Nothing is
            deleted automatically — this is a list for a person to work through.
          </p>
        )}

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-md bg-[#FEF2F2] border border-[#FECACA] text-[#B91C1C] text-sm">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}

        {restrictedNotice && (
          <div className="flex items-start gap-2 p-3 rounded-md bg-[#FFF7ED] border border-[#FED7AA] text-[#9A3412] text-sm">
            <Lock className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{restrictedNotice}</span>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading ? <LoadingTable /> : records.length === 0 ? (
              <p className="p-8 text-center text-sm text-[#6B7280]">
                {view === 'all' ? 'No records yet.' : 'Nothing here — which is the good outcome.'}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left" style={{ background: theme.g50 }}>
                      {['Reference', 'Title', 'Category', 'Department', 'Status',
                        'Held by', 'Days out', 'Location', ''].map(h => (
                        <th key={h}
                            className="px-3 py-2.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] whitespace-nowrap"
                            style={{ color: theme.t2, borderBottom: `1px solid ${theme.g200}` }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {records.map(r => (
                      // Manus QC 14-Aug-2026: rows carry the file-request history
                      // but were not clickable — the whole trail was reachable only
                      // through the "Move" button, which read as "no history". A
                      // click or Enter on the row now opens the same detail, and a
                      // stopPropagation on the row-level actions keeps their own
                      // click semantics intact.
                      <tr
                        key={r.id}
                        role="button"
                        tabIndex={0}
                        onClick={() => openRecord(r)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault(); openRecord(r)
                          }
                        }}
                        className="border-t border-[#F1F3F5] hover:bg-[#FAFBFC] cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#F4A623]"
                      >
                        <td className="px-3 py-2 font-mono text-[13px] whitespace-nowrap">
                          {r.reference}
                          {r.legal_hold && (
                            <span title={r.legal_hold_note || 'Under legal hold'}>
                              <Lock className="inline w-3.5 h-3.5 ml-1.5 text-[#B91C1C]" />
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2">{r.title}</td>
                        <td className="px-3 py-2 text-[#6B7280]">{r.category_name}</td>
                        <td className="px-3 py-2 text-[#6B7280]">{r.department || '—'}</td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          <span className={`inline-block whitespace-nowrap px-2 py-0.5 rounded-full ring-1 ring-inset text-[11px] font-medium ${STATUS_STYLES[r.status] ?? ''}`}>
                            {STATUS_LABEL[r.status] ?? r.status}
                          </span>
                        </td>
                        <td className="px-3 py-2">{r.held_by || '—'}</td>
                        {/* How long the physical file has been out. Amber past a
                            fortnight, red past a month — a number nobody reads is
                            the same as no number. */}
                        <td className="px-3 py-2 whitespace-nowrap">
                          {r.days_out == null ? (
                            <span className="text-[#9CA3AF]">—</span>
                          ) : (
                            <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                              r.days_out >= 30 ? 'bg-[#FEF2F2] text-[#B42318]'
                              : r.days_out >= 14 ? 'bg-[#FFFBEB] text-[#92400E]'
                              : 'bg-[#F1F5F9] text-[#475569]'}`}>
                              {r.days_out === 0 ? 'today' : `${r.days_out}d`}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-[#6B7280]">{r.current_location || '—'}</td>
                        <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                          <Button variant="ghost" size="sm" onClick={() => toggleHold(r)}
                                  title={r.legal_hold ? 'Release the legal hold' : 'Put under legal hold'}>
                            {r.legal_hold ? <Unlock className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}
                          </Button>
                          {r.status !== 'destroyed' && (
                            <Button variant="outline" size="sm" onClick={() => openRecord(r)}>
                              {r.status === 'issued' ? (
                                <><Undo2 className="w-3.5 h-3.5 mr-1" /> Return</>
                              ) : (
                                <><ArrowRightLeft className="w-3.5 h-3.5 mr-1" /> Move</>
                              )}
                            </Button>
                          )}
                          {r.status !== 'destroyed' && (
                            <RequestFileButton
                              record={{ id: r.id, reference: r.reference, title: r.title }}
                              onDone={() => setReqKey(k => k + 1)} />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
        <FileRequestsPanel reloadKey={reqKey} onDone={() => setReqKey(k => k + 1)} />
      </div>

      {adding && mounted && createPortal((
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-[100]"
             onClick={() => setAdding(false)}>
          <div className="bg-white rounded-lg max-w-xl w-full" onClick={e => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-[#E5E7EB]">
              <h2 className="font-semibold text-[#0D1B2A]">Add a record</h2>
              <p className="text-sm text-[#6B7280] mt-0.5">
                Where it is and who has it are set by recording a movement, not here.
              </p>
            </div>
            <div className="p-5 grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-[#6B7280]">Reference *</span>
                <input value={nRef} onChange={e => setNRef(e.target.value)}
                       placeholder="As written on the file"
                       className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-[#6B7280]">Category *</span>
                <select value={nCat} onChange={e => setNCat(e.target.value)}
                        className="px-3 py-2 border border-[#E5E7EB] rounded-md">
                  <option value="">Choose…</option>
                  {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-sm col-span-2">
                <span className="text-[#6B7280]">Department</span>
                <input value={nDept} onChange={e => setNDept(e.target.value)}
                       placeholder="Whose department the record belongs to"
                       className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
              </label>
              <label className="flex flex-col gap-1 text-sm col-span-2">
                <span className="text-[#6B7280]">Title *</span>
                <input value={nTitle} onChange={e => setNTitle(e.target.value)}
                       className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-[#6B7280]">Sensitivity</span>
                <select value={nConf} onChange={e => setNConf(e.target.value)}
                        className="px-3 py-2 border border-[#E5E7EB] rounded-md">
                  <option value="internal">Internal</option>
                  <option value="public">Public</option>
                  <option value="confidential">Confidential</option>
                  <option value="restricted">Restricted — personal data</option>
                </select>
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-[#6B7280]">Where it lives</span>
                <input value={nLoc} onChange={e => setNLoc(e.target.value)}
                       placeholder="Storeroom A, shelf 3…"
                       className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
              </label>
              <label className="flex flex-col gap-1 text-sm col-span-2">
                <span className="text-[#6B7280]">Keep until</span>
                <input type="date" value={nRetention} onChange={e => setNRetention(e.target.value)}
                       className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
                <span className="text-[11px] text-[#9CA3AF]">
                  Nothing is deleted on this date — it only appears on the destruction list.
                </span>
              </label>
              {addError && (
                <div className="col-span-2 flex items-start gap-2 p-3 rounded-md bg-[#FEF2F2] border border-[#FECACA] text-[#B91C1C] text-sm">
                  <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" /> {addError}
                </div>
              )}
              <div className="col-span-2 flex justify-end gap-2">
                <Button variant="outline" onClick={() => setAdding(false)}>Cancel</Button>
                <Button onClick={submitNew}
                        disabled={saving || !nRef.trim() || !nTitle.trim() || !nCat}>
                  {saving ? 'Adding…' : 'Add it'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      ), document.body)}

      {/* Rendered through a portal to document.body. Raising z-index alone did
          NOT work: the dashboard layout puts this page inside a stacking context,
          so a `fixed` overlay is trapped inside it and the sidebar painted over
          the dialog's left edge — "To whom" rendered as "o whom". A portal is the
          only thing that escapes a parent stacking context. */}
      {active && mounted && createPortal((
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-[100]"
             onClick={() => setActive(null)}>
          <div className="bg-white rounded-lg max-w-2xl w-full max-h-[85vh] overflow-y-auto"
               onClick={e => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-[#E5E7EB]">
              <h2 className="font-semibold text-[#0D1B2A]">{active.reference} — {active.title}</h2>
              <p className="text-sm text-[#6B7280] mt-0.5">
                Currently {STATUS_LABEL[active.status]?.toLowerCase()}
                {active.held_by ? ` with ${active.held_by}` : ''}
                {active.current_location ? ` · ${active.current_location}` : ''}
              </p>
              {active.legal_hold && (
                <p className="mt-2 text-sm text-[#B91C1C] flex items-center gap-1.5">
                  <Lock className="w-4 h-4" />
                  Under legal hold. {active.legal_hold_note}
                </p>
              )}
            </div>

            <div className="p-5 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-[#6B7280]">What is happening</span>
                  <select value={moveKind} onChange={e => setMoveKind(e.target.value)}
                          className="px-3 py-2 border border-[#E5E7EB] rounded-md">
                    {Object.entries(KIND_LABEL).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-[#6B7280]">Date</span>
                  <input type="date" value={moveDate} onChange={e => setMoveDate(e.target.value)}
                         className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-[#6B7280]">To whom</span>
                  <input value={moveTo} onChange={e => setMoveTo(e.target.value)}
                         placeholder="Person or firm holding it"
                         className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="text-[#6B7280]">Where it goes</span>
                  <input value={moveLocation} onChange={e => setMoveLocation(e.target.value)}
                         placeholder="Storeroom A, Legal office…"
                         className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
                </label>
                {moveKind === 'issue' && (
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="text-[#6B7280]">Due back</span>
                    <input type="date" value={moveDue} onChange={e => setMoveDue(e.target.value)}
                           className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
                  </label>
                )}
                <label className="flex flex-col gap-1 text-sm col-span-2">
                  <span className="text-[#6B7280]">Reason</span>
                  <input value={moveReason} onChange={e => setMoveReason(e.target.value)}
                         className="px-3 py-2 border border-[#E5E7EB] rounded-md" />
                </label>
              </div>

              {moveError && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-[#FEF2F2] border border-[#FECACA] text-[#B91C1C] text-sm">
                  <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" /> {moveError}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <Button variant="outline" onClick={() => setActive(null)}>Cancel</Button>
                <Button onClick={submitMove} disabled={saving}>
                  {saving ? 'Recording…' : 'Record it'}
                </Button>
              </div>
            </div>

            {history.length > 0 && (
              <div className="px-5 pb-5">
                <h3 className="text-sm font-semibold text-[#0D1B2A] mb-2">Where it has been</h3>
                <ul className="space-y-1.5">
                  {history.map(m => (
                    <li key={m.id} className="text-sm text-[#374151] border-l-2 border-[#F4A623] pl-3">
                      <strong>{KIND_LABEL[m.kind] ?? m.kind}</strong> on {m.moved_at}
                      {(m.to_name || m.to_custodian) ? ` to ${m.to_name || m.to_custodian}` : ''}
                      {m.to_location ? ` · ${m.to_location}` : ''}
                      {m.reason ? ` — ${m.reason}` : ''}
                      <span className="text-[#9CA3AF]"> · recorded by {m.recorded_by_name || 'unknown'}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      ), document.body)}
    </div>
  )
}
