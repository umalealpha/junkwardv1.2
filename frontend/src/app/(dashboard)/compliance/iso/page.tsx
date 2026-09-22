'use client'

/**
 * /compliance/iso — ISO 27001 10-Commandments audit register.
 *
 * CFO directive 2026-06-01. Renders the 10 distilled commandments
 * derived from ISO/IEC 27001:2022 Annex A, with their live audit
 * status (Good / Done / Partial / Pending) and drill-down findings.
 *
 * Backend: /api/v1/iso/commandments/ + /api/v1/iso/run-audit/
 *
 * "Run Audit" — only superusers + the cfo / iso_auditor groups.
 * Resolve / Accept-risk — same allow-list.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import {
  ShieldCheck, AlertTriangle, CheckCircle2, Circle, Lock,
  ChevronDown, ChevronRight, Play, BookOpen, Award,
} from 'lucide-react'

// ────────────────────────────────────────────────────────────────────
//  Types

type FindingSeverity = 'good' | 'info' | 'low' | 'medium' | 'high' | 'critical'
type FindingState = 'open' | 'resolved' | 'accepted'
type CommandmentStatus = 'good' | 'done' | 'partial' | 'pending' | 'na'

interface Finding {
  id: string
  severity: FindingSeverity
  severity_label: string
  state: FindingState
  state_label: string
  title: string
  detail: string
  fix_hint: string
  evidence: string
  detected_at: string
  resolved_at: string | null
}

interface Commandment {
  number: number
  title: string
  summary: string
  iso_clauses: string
  why_it_matters: string
  status: CommandmentStatus
  status_label: string
  owner: string
  last_audited_at: string | null
  findings: Finding[]
  open_count: number
  critical_count: number
  high_count: number
  good_count: number
}

interface AuditRun {
  id: string
  started_at: string
  finished_at: string | null
  actor: string
  findings_created: number
  score_pct: number
  notes: string
}

interface CommandmentsResponse {
  commandments: Commandment[]
  latest_run: AuditRun | null
  overall_score: number
}

// ────────────────────────────────────────────────────────────────────
//  Visual helpers

const STATUS_STYLES: Record<CommandmentStatus, { bg: string; fg: string; ring: string; icon: any }> = {
  good:    { bg: '#0F8B6C', fg: '#fff', ring: '#0F8B6C', icon: ShieldCheck   },
  done:    { bg: '#1E88E5', fg: '#fff', ring: '#1E88E5', icon: CheckCircle2  },
  partial: { bg: '#F4A623', fg: '#0D1B2A', ring: '#F4A623', icon: Circle     },
  pending: { bg: '#D72638', fg: '#fff', ring: '#D72638', icon: AlertTriangle },
  na:      { bg: '#6B7280', fg: '#fff', ring: '#6B7280', icon: Lock          },
}

const SEVERITY_TONE: Record<FindingSeverity, string> = {
  good:     'text-emerald-700 bg-emerald-50 border-emerald-200',
  info:     'text-slate-700 bg-slate-50 border-slate-200',
  low:      'text-amber-800 bg-amber-50 border-amber-200',
  medium:   'text-orange-800 bg-orange-50 border-orange-200',
  high:     'text-red-800 bg-red-50 border-red-200',
  critical: 'text-white bg-red-700 border-red-700',
}

// ────────────────────────────────────────────────────────────────────
//  Page

export default function ISOCommandmentsPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<CommandmentsResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch<CommandmentsResponse>('/iso/commandments/')
      setData(res)
    } catch (e: any) {
      setError(e?.message || 'Failed to load ISO commandments')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const runAudit = async () => {
    setRunning(true)
    setError(null)
    try {
      await apiFetch('/iso/run-audit/', { method: 'POST' })
      await load()
    } catch (e: any) {
      setError(e?.message || 'Audit failed')
    } finally {
      setRunning(false)
    }
  }

  const toggle = (n: number) => {
    const next = new Set(expanded)
    if (next.has(n)) next.delete(n)
    else next.add(n)
    setExpanded(next)
  }

  const score = data?.overall_score ?? 0
  const tally = useMemo(() => {
    const out = { good: 0, done: 0, partial: 0, pending: 0, na: 0 }
    for (const c of data?.commandments ?? []) out[c.status] += 1
    return out
  }, [data])

  return (
    <div className="min-h-screen bg-slate-50">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        {/* Header */}
        <div className="mb-6 flex items-start justify-between gap-6">
          <div>
            <div className="flex items-center gap-3">
              <ShieldCheck size={32} style={{ color: '#0D1B2A' }} />
              <h1 className="text-3xl font-serif font-semibold" style={{ color: '#0D1B2A' }}>
                ISO 27001 — The 10 Commandments
              </h1>
            </div>
            <p className="mt-2 max-w-3xl text-sm text-slate-600">
              The CFO-friendly distillation of <strong>ISO/IEC 27001:2022</strong> Annex A controls
              that an insurer must operate. Each commandment is scored from a live system scan —
              identity, logging, encryption, backup, change-management, incident-response,
              suppliers, asset register, business continuity, and overall compliance posture.
              Open findings explain what is <em>good</em>, what is <em>done</em>, and what is
              still <em>pending</em>.
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <button
              onClick={runAudit}
              disabled={running}
              className="flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium text-white shadow-sm transition disabled:opacity-50"
              style={{ background: '#F4A623' }}
            >
              <Play size={16} />
              {running ? 'Running…' : 'Run Audit Now'}
            </button>
            {data?.latest_run?.finished_at && (
              <span className="text-xs text-slate-500">
                Last audited {new Date(data.latest_run.finished_at).toLocaleString()} ·{' '}
                {data.latest_run.actor}
              </span>
            )}
          </div>
        </div>

        {/* Score band */}
        <Card className="mb-6 border border-slate-200">
          <CardContent className="flex flex-wrap items-center justify-between gap-6 py-5">
            <div className="flex items-center gap-4">
              <ScoreDial score={score} />
              <div>
                <div className="font-serif text-2xl font-semibold" style={{ color: '#0D1B2A' }}>
                  Overall posture: {score}%
                </div>
                <p className="text-sm text-slate-600">
                  Composite of the 10 commandments, weighted by worst open finding.
                </p>
              </div>
            </div>
            <div className="flex flex-wrap gap-3 text-sm">
              <Tally label="Good" count={tally.good} tone={STATUS_STYLES.good.bg} />
              <Tally label="Done" count={tally.done} tone={STATUS_STYLES.done.bg} />
              <Tally label="Partial" count={tally.partial} tone={STATUS_STYLES.partial.bg} />
              <Tally label="Pending" count={tally.pending} tone={STATUS_STYLES.pending.bg} />
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="mb-6 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {loading && !data ? (
          <div className="rounded-md border border-slate-200 bg-white px-4 py-12 text-center text-slate-500">
            Loading commandments…
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {(data?.commandments ?? []).map((c) => (
              <CommandmentCard
                key={c.number}
                cmd={c}
                expanded={expanded.has(c.number)}
                onToggle={() => toggle(c.number)}
                onResolve={async (fid) => {
                  await apiFetch(`/iso/findings/${fid}/resolve/`, { method: 'POST' })
                  await load()
                }}
                onAccept={async (fid) => {
                  await apiFetch(`/iso/findings/${fid}/accept/`, { method: 'POST' })
                  await load()
                }}
              />
            ))}
          </div>
        )}

        <footer className="mt-10 flex items-center gap-2 text-xs text-slate-500">
          <BookOpen size={14} />
          <span>
            Mapped to ISO/IEC 27001:2022 Annex A controls. Distillation maintained by the CFO. This
            page is for management oversight — formal certification audits remain the responsibility
            of the appointed certification body.
          </span>
        </footer>
      </main>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────
//  Components

function Tally({ label, count, tone }: { label: string; count: number; tone: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: tone }} />
      <span className="text-xs uppercase tracking-wide text-slate-500">{label}</span>
      <span className="font-mono text-sm font-semibold" style={{ color: '#0D1B2A' }}>{count}</span>
    </div>
  )
}

function ScoreDial({ score }: { score: number }) {
  const colour = score >= 85 ? '#0F8B6C' : score >= 65 ? '#F4A623' : '#D72638'
  // Simple SVG ring
  const radius = 36
  const circ = 2 * Math.PI * radius
  const dash = (score / 100) * circ
  return (
    <svg width={88} height={88} viewBox="0 0 88 88">
      <circle cx="44" cy="44" r={radius} stroke="#E5E7EB" strokeWidth="8" fill="none" />
      <circle
        cx="44" cy="44" r={radius}
        stroke={colour} strokeWidth="8" fill="none"
        strokeDasharray={`${dash} ${circ - dash}`}
        strokeDashoffset={circ / 4}
        strokeLinecap="round"
      />
      <text x="44" y="50" textAnchor="middle"
            fontFamily="Book Antiqua, Georgia, serif"
            fontSize="22" fontWeight="600" fill="#0D1B2A">
        {score}
      </text>
    </svg>
  )
}

function CommandmentCard(props: {
  cmd: Commandment
  expanded: boolean
  onToggle: () => void
  onResolve: (id: string) => void | Promise<void>
  onAccept: (id: string) => void | Promise<void>
}) {
  const { cmd, expanded, onToggle, onResolve, onAccept } = props
  const style = STATUS_STYLES[cmd.status]
  const Icon = style.icon

  const openFindings = cmd.findings.filter((f) => f.state === 'open')
  const resolved = cmd.findings.filter((f) => f.state !== 'open')

  return (
    <Card className="border border-slate-200">
      <CardContent className="p-0">
        <button
          onClick={onToggle}
          className="flex w-full items-center justify-between gap-4 p-5 text-left"
        >
          <div className="flex flex-1 items-start gap-4">
            <div
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full font-mono text-sm font-semibold"
              style={{ background: style.bg, color: style.fg }}
            >
              {cmd.number.toString().padStart(2, '0')}
            </div>
            <div className="flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-serif text-lg font-semibold" style={{ color: '#0D1B2A' }}>
                  {cmd.title}
                </h3>
                <span
                  className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium"
                  style={{ background: style.bg, color: style.fg }}
                >
                  <Icon size={12} />
                  {cmd.status_label}
                </span>
              </div>
              <p className="mt-1 text-sm text-slate-600">{cmd.summary}</p>
              <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <span className="font-mono">{cmd.iso_clauses}</span>
                {cmd.owner && <span>· Owner: {cmd.owner}</span>}
                {cmd.last_audited_at && (
                  <span>· Audited {new Date(cmd.last_audited_at).toLocaleDateString()}</span>
                )}
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <Badge tone={SEVERITY_TONE.good}     label={`Good ${cmd.good_count}`} />
                <Badge tone={SEVERITY_TONE.high}     label={`High ${cmd.high_count}`} />
                <Badge tone={SEVERITY_TONE.critical} label={`Critical ${cmd.critical_count}`} />
                <Badge tone={SEVERITY_TONE.info}     label={`Open ${cmd.open_count}`} />
              </div>
            </div>
          </div>
          <div className="text-slate-400">
            {expanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
          </div>
        </button>

        {expanded && (
          <div className="space-y-4 border-t border-slate-100 bg-slate-50/50 p-5">
            {cmd.why_it_matters && (
              <div className="text-sm text-slate-700">
                <strong style={{ color: '#0D1B2A' }}>Why it matters: </strong>
                {cmd.why_it_matters}
              </div>
            )}

            <FindingList
              title="Open findings"
              items={openFindings}
              showActions
              onResolve={onResolve}
              onAccept={onAccept}
              empty="No open findings — controls clean."
            />

            {resolved.length > 0 && (
              <FindingList
                title="History (resolved / accepted)"
                items={resolved}
                empty=""
                showActions={false}
              />
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function FindingList({
  title, items, showActions, onResolve, onAccept, empty,
}: {
  title: string
  items: Finding[]
  showActions: boolean
  onResolve?: (id: string) => void | Promise<void>
  onAccept?: (id: string) => void | Promise<void>
  empty: string
}) {
  if (items.length === 0 && !empty) return null

  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </div>
      {items.length === 0 ? (
        <p className="text-sm italic text-slate-500">{empty}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((f) => (
            <li key={f.id} className={`rounded-md border px-3 py-2 ${SEVERITY_TONE[f.severity]}`}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex-1">
                  <div className="text-sm font-semibold">
                    [{f.severity_label}] {f.title}
                  </div>
                  {f.detail && <p className="mt-1 text-xs opacity-90">{f.detail}</p>}
                  {f.fix_hint && (
                    <p className="mt-1 text-xs opacity-80">
                      <strong>Fix:</strong> {f.fix_hint}
                    </p>
                  )}
                  {f.evidence && (
                    <p className="mt-1 font-mono text-[11px] opacity-70">{f.evidence}</p>
                  )}
                  <p className="mt-1 text-[11px] opacity-70">
                    {new Date(f.detected_at).toLocaleString()}
                    {f.state !== 'open' && ` · ${f.state_label}`}
                  </p>
                </div>
                {showActions && (
                  <div className="flex shrink-0 gap-1">
                    <button
                      onClick={() => onResolve?.(f.id)}
                      className="rounded-md border border-current px-2 py-1 text-[11px] font-medium"
                    >
                      Resolve
                    </button>
                    <button
                      onClick={() => onAccept?.(f.id)}
                      className="rounded-md border border-current px-2 py-1 text-[11px] font-medium"
                    >
                      Accept risk
                    </button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Badge({ tone, label }: { tone: string; label: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] ${tone}`}>
      {label}
    </span>
  )
}
