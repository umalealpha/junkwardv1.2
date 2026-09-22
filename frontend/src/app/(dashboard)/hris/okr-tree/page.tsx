'use client'

/**
 * /hris/okr-tree — OKR Alignment Tree.
 *
 * Shows every OKR for a period as a tree by `parent`: company objective(s)
 * at the root, department objectives underneath, individual objectives
 * underneath those — whatever hierarchy HR links when creating an
 * objective. Calls the okr_tree_views endpoints:
 *   GET  /api/v1/hris/okr-tree/?period=<p>
 *   POST /api/v1/hris/okr-tree/objective/
 *   GET  /api/v1/hris/okr-tree/periods/
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, ChevronDown, ChevronRight, Plus, Target, Loader2, Send, X,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import FeatureAcceptBar from '@/components/hris/FeatureAcceptBar'
import { useTheme } from '@/contexts/ThemeContext'
import type { Theme } from '@/lib/themes'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch, fetchHrisEmployees, type HrisEmployee } from '../_shared'

type OkrScope = 'company' | 'department' | 'individual'

interface OkrNode {
  id: string
  name: string
  scope: OkrScope
  owner: string
  progress: number | null
  weight_pct: number
  target: string
  parent_id: string | null
  children: OkrNode[]
}

interface TreeResponse {
  period: string | null
  node_count: number
  roots: OkrNode[]
}

interface FlatNode {
  id: string
  name: string
  scope: OkrScope
  depth: number
}

const SCOPE_ORDER: OkrScope[] = ['company', 'department', 'individual']
const SCOPE_LABEL: Record<OkrScope, string> = {
  company: 'Company', department: 'Department', individual: 'Individual',
}
const CHILD_SCOPE: Record<OkrScope, OkrScope> = {
  company: 'department', department: 'individual', individual: 'individual',
}

export default function HrisOkrTreePage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [tree, setTree] = useState<TreeResponse | null>(null)
  const [periodsList, setPeriodsList] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [employees, setEmployees] = useState<HrisEmployee[] | null>(null)

  // Add-objective form state
  const [formOpen, setFormOpen] = useState(false)
  const [fScope, setFScope] = useState<OkrScope>('company')
  const [fParentId, setFParentId] = useState('')
  const [fName, setFName] = useState('')
  const [fTarget, setFTarget] = useState('')
  const [fWeight, setFWeight] = useState('')
  const [fProfileId, setFProfileId] = useState('')
  const [fPeriod, setFPeriod] = useState('')
  const [newPeriodDraft, setNewPeriodDraft] = useState('')
  const [periodMode, setPeriodMode] = useState<'pick' | 'new'>('pick')
  const [submitting, setSubmitting] = useState(false)
  const [formMsg, setFormMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => { if (allowed === false) router.replace('/dashboard') }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    loadPeriods()
    loadTree()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed])

  useEffect(() => {
    // Creating for a brand-new period means nothing exists there yet — any
    // parent picked from the currently-displayed (different) period would
    // fail server-side, so clear it the moment the user switches modes.
    if (periodMode === 'new') setFParentId('')
  }, [periodMode])

  function loadPeriods() {
    authedHrisFetch('/api/v1/hris/okr-tree/periods/')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: { periods: string[] }) => setPeriodsList(Array.isArray(d.periods) ? d.periods : []))
      .catch(() => setPeriodsList([]))
  }

  function loadTree(period?: string) {
    setLoading(true); setError(null)
    const qs = period ? `?period=${encodeURIComponent(period)}` : ''
    authedHrisFetch(`/api/v1/hris/okr-tree/${qs}`)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: TreeResponse) => setTree(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load the OKR tree'))
      .finally(() => setLoading(false))
  }

  function loadEmployeesOnce() {
    if (employees !== null) return
    fetchHrisEmployees().then(r => setEmployees((r.employees || []).filter(e => e.pid)))
  }

  const flatNodes = useMemo(() => {
    const out: FlatNode[] = []
    function walk(nodes: OkrNode[], depth: number) {
      nodes.forEach(n => {
        out.push({ id: n.id, name: n.name, scope: n.scope, depth })
        walk(n.children, depth + 1)
      })
    }
    if (tree) walk(tree.roots, 0)
    return out
  }, [tree])

  const summary = useMemo(() => {
    const c = { company: 0, department: 0, individual: 0, scored: 0, progressSum: 0 }
    flatNodes.forEach(n => { c[n.scope]++ })
    function walk(nodes: OkrNode[]) {
      nodes.forEach(n => {
        if (n.progress !== null) { c.scored++; c.progressSum += n.progress }
        walk(n.children)
      })
    }
    if (tree) walk(tree.roots)
    return c
  }, [flatNodes, tree])

  const avgProgress = summary.scored > 0 ? summary.progressSum / summary.scored : null

  function toggleNode(id: string) {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function openForm(scope: OkrScope, parentId?: string) {
    setFScope(scope)
    setFParentId(parentId || '')
    setFName(''); setFTarget(''); setFWeight(''); setFProfileId('')
    setFPeriod(tree?.period || '')
    setPeriodMode(periodsList.length > 0 ? 'pick' : 'new')
    setNewPeriodDraft('')
    setFormMsg(null)
    setFormOpen(true)
    if (scope === 'individual') loadEmployeesOnce()
  }

  async function submitObjective() {
    const period = (periodMode === 'new' ? newPeriodDraft : fPeriod).trim()
    if (!period) { setFormMsg({ ok: false, text: 'Pick or type a period.' }); return }
    if (!fName.trim()) { setFormMsg({ ok: false, text: 'Objective name is required.' }); return }
    if (fScope === 'individual' && !fProfileId) {
      setFormMsg({ ok: false, text: 'Pick an owner for an individual objective.' }); return
    }
    setSubmitting(true); setFormMsg(null)
    try {
      const r = await authedHrisFetch('/api/v1/hris/okr-tree/objective/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          period,
          name: fName.trim(),
          scope: fScope,
          parent_id: fParentId || undefined,
          profile_id: fScope === 'individual' ? fProfileId : undefined,
          weight_pct: fWeight ? Number(fWeight) : undefined,
          target: fTarget.trim() || undefined,
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setFormMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
      setFormOpen(false)
      loadPeriods()
      loadTree(period)
    } catch (err) {
      setFormMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setSubmitting(false)
    }
  }

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }
  const inp = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="OKR Alignment Tree" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'OKR Alignment Tree' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <FeatureAcceptBar featureKey="okr_tree" />
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        {/* Header + period selector + add button */}
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold flex items-center gap-2" style={{ color: theme.text }}>
              <Target className="w-5 h-5" style={{ color: theme.orange }} /> OKR Alignment Tree
            </h2>
            <p className="text-sm mt-0.5" style={{ color: theme.t2 }}>
              {tree?.period
                ? <>Showing <strong style={{ color: theme.text }}>{tree.period}</strong> — how every goal ladders up to the company objective.</>
                : 'How every goal ladders up — company objective → department → individual.'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {periodsList.length > 0 && (
              <div>
                <label htmlFor="okr-period-select" className="sr-only">Period</label>
                <select
                  id="okr-period-select"
                  value={tree?.period || ''}
                  onChange={e => loadTree(e.target.value)}
                  className="rounded-lg px-3 py-2 text-sm outline-none"
                  style={inp}
                >
                  {!tree?.period && <option value="">— select period —</option>}
                  {periodsList.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
            )}
            <button
              onClick={() => (formOpen ? setFormOpen(false) : openForm('company'))}
              className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-semibold"
              style={formOpen
                ? { background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }
                : { background: theme.orange, color: '#fff' }}
            >
              {formOpen ? <><X className="w-4 h-4" /> Close</> : <><Plus className="w-4 h-4" /> Add objective</>}
            </button>
          </div>
        </div>

        {/* Summary tiles */}
        {tree && tree.roots.length > 0 && (
          <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatTile label="Company" value={summary.company} theme={theme} accent={theme.navy} />
            <StatTile label="Department" value={summary.department} theme={theme} accent={theme.orangeText} />
            <StatTile label="Individual" value={summary.individual} theme={theme} accent={theme.teal} />
            <StatTile
              label="Avg. progress (scored)"
              value={avgProgress !== null ? `${avgProgress.toFixed(0)}%` : '—'}
              theme={theme}
              accent={theme.ok}
            />
          </section>
        )}

        {/* Add-objective form */}
        {formOpen && (
          <div className="rounded-2xl p-5 space-y-4" style={card}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold flex items-center gap-2" style={{ color: theme.text }}>
                <Plus className="w-4 h-4" style={{ color: theme.orange }} /> Add objective
              </h3>
              <button onClick={() => setFormOpen(false)} aria-label="Close form" style={{ color: theme.t3 }}>
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Period */}
            <div>
              <label htmlFor="okr-form-period" className="block text-xs mb-1" style={{ color: theme.t2 }}>Period</label>
              {periodMode === 'pick' && periodsList.length > 0 ? (
                <div className="flex items-center gap-3">
                  <select id="okr-form-period" value={fPeriod} onChange={e => setFPeriod(e.target.value)}
                    className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
                    <option value="">— select period —</option>
                    {periodsList.map(p => <option key={p} value={p}>{p}</option>)}
                  </select>
                  <button type="button" onClick={() => setPeriodMode('new')}
                    className="text-xs font-medium whitespace-nowrap" style={{ color: theme.orangeText }}>
                    + New period
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <input id="okr-form-period" value={newPeriodDraft} onChange={e => setNewPeriodDraft(e.target.value)}
                    placeholder="e.g. 2026-H2 or FY27" maxLength={20}
                    className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
                  {periodsList.length > 0 && (
                    <button type="button" onClick={() => setPeriodMode('pick')}
                      className="text-xs font-medium whitespace-nowrap" style={{ color: theme.t2 }}>
                      Use existing
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* Scope */}
            <div>
              <span className="block text-xs mb-1" style={{ color: theme.t2 }}>Scope</span>
              <div className="inline-flex rounded-lg overflow-hidden" role="group" aria-label="Objective scope"
                style={{ border: `1px solid ${theme.cardBdr}` }}>
                {SCOPE_ORDER.map(s => (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={fScope === s}
                    onClick={() => { setFScope(s); if (s === 'individual') loadEmployeesOnce() }}
                    className="px-4 py-2 text-sm font-medium"
                    style={{
                      background: fScope === s ? theme.orange : 'transparent',
                      color: fScope === s ? '#fff' : theme.t2,
                    }}
                  >
                    {SCOPE_LABEL[s]}
                  </button>
                ))}
              </div>
            </div>

            {/* Parent */}
            <div>
              <label htmlFor="okr-form-parent" className="block text-xs mb-1" style={{ color: theme.t2 }}>
                Parent objective (optional)
              </label>
              <select id="okr-form-parent" value={fParentId} onChange={e => setFParentId(e.target.value)}
                disabled={periodMode === 'new'}
                className="w-full rounded-lg px-3 py-2 text-sm outline-none disabled:opacity-50" style={inp}>
                <option value="">— none (root objective) —</option>
                {flatNodes.map(n => (
                  <option key={n.id} value={n.id}>
                    {'—'.repeat(n.depth)} {n.name} ({SCOPE_LABEL[n.scope]})
                  </option>
                ))}
              </select>
              {periodMode === 'new' && (
                <p className="text-[11px] mt-1" style={{ color: theme.t3 }}>
                  Not available yet — this will be the first objective in the new period.
                </p>
              )}
            </div>

            {/* Owner — individual only */}
            {fScope === 'individual' && (
              <div>
                <label htmlFor="okr-form-owner" className="block text-xs mb-1" style={{ color: theme.t2 }}>Owner</label>
                <select id="okr-form-owner" value={fProfileId} onChange={e => setFProfileId(e.target.value)}
                  className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
                  <option value="">— select employee —</option>
                  {(employees || []).map(e => (
                    <option key={e.pid} value={e.pid}>{e.nm} · {e.dp || 'Unassigned'} · {e.company}</option>
                  ))}
                </select>
                {employees === null && (
                  <p className="text-[11px] mt-1" style={{ color: theme.t3 }}>Loading employees…</p>
                )}
              </div>
            )}

            {/* Name */}
            <div>
              <label htmlFor="okr-form-name" className="block text-xs mb-1" style={{ color: theme.t2 }}>Objective name</label>
              <input id="okr-form-name" value={fName} onChange={e => setFName(e.target.value)} maxLength={200}
                placeholder="e.g. Grow gross written premium 15% YoY"
                className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="okr-form-target" className="block text-xs mb-1" style={{ color: theme.t2 }}>Target (optional)</label>
                <input id="okr-form-target" value={fTarget} onChange={e => setFTarget(e.target.value)} maxLength={200}
                  placeholder="e.g. 125Mn GWP"
                  className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
              </div>
              <div>
                <label htmlFor="okr-form-weight" className="block text-xs mb-1" style={{ color: theme.t2 }}>Weight % (optional)</label>
                <input id="okr-form-weight" type="number" min={0} max={100} value={fWeight}
                  onChange={e => setFWeight(e.target.value)} placeholder="e.g. 30"
                  className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button onClick={submitObjective} disabled={submitting}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
                style={{ background: theme.orange, color: '#fff' }}>
                {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} Create objective
              </button>
              <button onClick={() => setFormOpen(false)} className="text-sm font-medium" style={{ color: theme.t2 }}>
                Cancel
              </button>
            </div>

            {formMsg && (
              <div className="rounded-lg px-3 py-2 text-sm" style={{
                background: formMsg.ok ? theme.okB : theme.erB,
                color: formMsg.ok ? theme.ok : theme.er,
                border: `1px solid ${(formMsg.ok ? theme.ok : theme.er)}40`,
              }}>
                {formMsg.text}
              </div>
            )}
          </div>
        )}

        {/* Tree body */}
        <div className="rounded-2xl p-5" style={card}>
          {loading && (
            <div className="text-sm py-10 text-center" style={{ color: theme.t2 }}>
              <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> Loading the OKR tree…
            </div>
          )}
          {!loading && error && (
            <div className="rounded-md p-3 text-sm" style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
              {error}
            </div>
          )}
          {!loading && !error && tree && tree.roots.length === 0 && (
            <EmptyState theme={theme} onAdd={() => openForm('company')} />
          )}
          {!loading && !error && tree && tree.roots.length > 0 && (
            <div className="overflow-x-auto">
              <div className="min-w-[420px]">
                {tree.roots.map(n => (
                  <OkrNodeRow
                    key={n.id}
                    node={n}
                    depth={0}
                    theme={theme}
                    collapsed={collapsed}
                    onToggle={toggleNode}
                    onAddChild={openForm}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}

// ─── Presentational helpers ───────────────────────────────────────────────────

function StatTile({ label, value, theme, accent }: {
  label: string; value: number | string; theme: Theme; accent: string
}) {
  return (
    <div className="rounded-xl p-3" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: theme.t3 }}>{label}</div>
      <div className="text-2xl font-bold mt-1" style={{ color: accent }}>{value}</div>
    </div>
  )
}

function ScopeBadge({ scope, theme, dark }: { scope: OkrScope; theme: Theme; dark: boolean }) {
  const meta: Record<OkrScope, { bg: string; fg: string }> = {
    company: { bg: theme.navy, fg: '#FFFFFF' },
    department: { bg: theme.oL, fg: theme.orangeText },
    individual: { bg: theme.tealL, fg: theme.teal },
  }
  const m = dark ? { bg: 'rgba(255,255,255,0.18)', fg: '#FFFFFF' } : meta[scope]
  return (
    <span
      className="flex-shrink-0 text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded"
      style={{ background: m.bg, color: m.fg }}
    >
      {SCOPE_LABEL[scope]}
    </span>
  )
}

function ProgressBar({ value, theme, dark }: { value: number | null; theme: Theme; dark: boolean }) {
  if (value === null) {
    return (
      <span className="text-[11px] whitespace-nowrap" style={{ color: dark ? 'rgba(255,255,255,0.65)' : theme.t3 }}>
        Not yet scored
      </span>
    )
  }
  const color = value >= 75 ? theme.ok : value >= 40 ? theme.wr : theme.er
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full overflow-hidden flex-shrink-0"
        style={{ background: dark ? 'rgba(255,255,255,0.22)' : theme.g200 }}>
        <div className="h-full rounded-full" style={{ width: `${value}%`, background: color }} />
      </div>
      <span className="text-[11px] font-mono whitespace-nowrap" style={{ color: dark ? '#fff' : theme.t2 }}>
        {value.toFixed(0)}%
      </span>
    </div>
  )
}

function EmptyState({ theme, onAdd }: { theme: Theme; onAdd: () => void }) {
  return (
    <div className="text-center py-12">
      <Target className="w-10 h-10 mx-auto mb-3" style={{ color: theme.t3 }} />
      <p className="text-sm font-medium" style={{ color: theme.text }}>
        No objectives yet for this period — add the company objective to start.
      </p>
      <p className="text-xs mt-1" style={{ color: theme.t2 }}>
        Every department and individual goal will ladder up to it once it&rsquo;s here.
      </p>
      <button
        onClick={onAdd}
        className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold"
        style={{ background: theme.orange, color: '#fff' }}
      >
        <Plus className="w-4 h-4" /> Add the company objective
      </button>
    </div>
  )
}

function OkrNodeRow({ node, depth, theme, collapsed, onToggle, onAddChild }: {
  node: OkrNode
  depth: number
  theme: Theme
  collapsed: Set<string>
  onToggle: (id: string) => void
  onAddChild: (scope: OkrScope, parentId: string) => void
}) {
  const hasKids = node.children.length > 0
  const isCollapsed = collapsed.has(node.id)
  const isCompany = node.scope === 'company'

  return (
    <div style={{ marginBottom: 10 }}>
      <div className="flex items-center gap-2">
        {depth > 0 && <div style={{ width: 20, height: 2, background: theme.cardBdr, flexShrink: 0 }} />}
        <div
          className="flex-1 flex flex-wrap items-center justify-between gap-3 rounded-xl min-w-0"
          style={{
            padding: isCompany ? '14px 16px' : '10px 14px',
            background: isCompany ? theme.navy : theme.card,
            border: `1px solid ${isCompany ? theme.navy : theme.cardBdr}`,
            boxShadow: theme.cardSh,
          }}
        >
          <div className="flex items-center gap-2.5 min-w-0">
            {hasKids ? (
              <button
                onClick={() => onToggle(node.id)}
                aria-label={isCollapsed ? `Expand ${node.name}` : `Collapse ${node.name}`}
                className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded"
                style={{ color: isCompany ? 'rgba(255,255,255,0.85)' : theme.t2 }}
              >
                {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </button>
            ) : (
              <span style={{ width: 20, flexShrink: 0 }} />
            )}
            <ScopeBadge scope={node.scope} theme={theme} dark={isCompany} />
            <div className="min-w-0">
              <div className="text-sm font-semibold truncate" style={{ color: isCompany ? '#fff' : theme.text }}>
                {node.name}
              </div>
              <div className="text-[11px] truncate" style={{ color: isCompany ? 'rgba(255,255,255,0.72)' : theme.t2 }}>
                {node.owner}{node.target ? ` · Target: ${node.target}` : ''}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3 flex-shrink-0">
            {node.weight_pct > 0 && (
              <span
                className="text-[11px] font-mono px-1.5 py-0.5 rounded whitespace-nowrap"
                style={{
                  background: isCompany ? 'rgba(255,255,255,0.14)' : theme.g100,
                  color: isCompany ? '#fff' : theme.t2,
                }}
              >
                wt {node.weight_pct.toFixed(0)}%
              </span>
            )}
            <ProgressBar value={node.progress} theme={theme} dark={isCompany} />
            <button
              onClick={() => onAddChild(CHILD_SCOPE[node.scope], node.id)}
              title={`Add a ${SCOPE_LABEL[CHILD_SCOPE[node.scope]].toLowerCase()} objective under this one`}
              aria-label={`Add a child objective under ${node.name}`}
              className="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-full"
              style={{
                background: isCompany ? 'rgba(255,255,255,0.16)' : theme.g100,
                color: isCompany ? '#fff' : theme.t2,
              }}
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {hasKids && !isCollapsed && (
        <div style={{ marginLeft: 20, paddingLeft: 20, marginTop: 10, borderLeft: `2px solid ${theme.cardBdr}` }}>
          {node.children.map(c => (
            <OkrNodeRow
              key={c.id}
              node={c}
              depth={depth + 1}
              theme={theme}
              collapsed={collapsed}
              onToggle={onToggle}
              onAddChild={onAddChild}
            />
          ))}
        </div>
      )}
    </div>
  )
}
