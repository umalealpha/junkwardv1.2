'use client'

/**
 * DialogueView — read-only render of one person's Development Dialogue.
 * Shared by the staff self-view (/hris/my-dialogue) and the manager
 * team-view (/hris/team-dialogues). Presentation only; no fetching.
 *
 * Redesign 2026-08-30 (Manus QC, CFO): the CFO's three questions get
 * answered at a glance BEFORE the reader scrolls anything — a snapshot
 * hero (big score, rating pill, nine-box placement, stat tiles), a
 * compact score summary table, a read-only 3×3 nine-box mini grid, then
 * the detail sections as accordions (first open, rest collapsed). All
 * original fields are preserved — nothing deleted, nothing renamed.
 */

import { useState } from 'react'

const NAVY   = '#0B0B3B'
const ORANGE = '#F07F00'
const GREEN  = '#1F9D57'
const AMBER  = '#E9A800'
const RED    = '#C1121F'
const TEAL   = '#0A9396'

type Row = {
  perspective?: string
  attributes?: string
  sbi?: string
  manager?: number | null
  employee?: number | null
  weight?: number | null
  comments?: string
}
type Section = { name?: string; weight?: number | null; rows?: Row[] }
type Value = {
  value?: string
  behaviours?: string
  self?: number | null
  manager?: number | null
  comments?: string
}
type Target = { title?: string; due?: string; status?: string; progress?: number }
type DD = {
  details?: Record<string, string>
  sections?: Section[]
  values?: Value[]
  rating?: string
  pdp?: string
  careerAspirations?: string
  managersComments?: string
  developmentPriorities?: string
  developmentMeasures?: string
}
export type Person = {
  name?: string
  dept?: string
  position?: string
  period?: string
  supervisor?: string
  overall?: number | null
  rating?: string
  performance?: number | null    // 0-1 nine-box perf axis (from talent_cockpit backend)
  potential?: number | null      // 0-1 nine-box potential axis
  locked?: boolean               // signed off + read-only
  targets?: Target[]
  dd?: DD
}

const pctW = (v?: number | null) => (v == null ? '' : Math.round(v * 100 * 10) / 10 + '%')
const pctS = (v?: number | null) => (v == null ? '—' : Math.round(v * 100 * 10) / 10 + '%')
const fmtDate = (d?: string) => {
  if (!d) return '—'
  const x = new Date(d)
  return isNaN(+x) ? d : x.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

// Nine-box labels + colours mirror the Talent Cockpit exactly, so the
// two screens always tell the same story. Coordinate system: rows are
// potential (0 = bottom, 2 = top), cols are performance (0 = left).
const BOX: { l: string; c: string }[][] = [
  // top row  (potential H)
  [{ l: 'Enigma',              c: '#94D2BD' }, { l: 'Growth Employee',   c: '#0A9396' }, { l: 'Future Leader',     c: '#005F73' }],
  // middle row (potential M)
  [{ l: 'Inconsistent Player', c: '#E9D8A6' }, { l: 'Core Player',       c: '#EE9B00' }, { l: 'High Performer',    c: '#CA6702' }],
  // bottom row (potential L)
  [{ l: 'Talent Risk',         c: '#C1121F' }, { l: 'Underperformer',    c: '#AE2012' }, { l: 'Solid Specialist',  c: '#BB3E03' }],
]
function boxOf(perf?: number | null, pot?: number | null) {
  const p = Math.max(0, Math.min(1, perf ?? 0.5))
  const q = Math.max(0, Math.min(1, pot  ?? 0.5))
  const col = p < 1 / 3 ? 0 : p < 2 / 3 ? 1 : 2
  const row = q < 1 / 3 ? 2 : q < 2 / 3 ? 1 : 0
  return BOX[row][col]
}
function ratingColour(rating?: string) {
  const t = (rating || '').toLowerCase()
  if (t.includes('exceed') || t.includes('outstanding') || t.includes('strong')) return GREEN
  if (t.includes('meets') || t.includes('solid') || t.includes('on track'))       return ORANGE
  if (t.includes('below') || t.includes('under') || t.includes('improve'))        return RED
  return NAVY
}

// ── little presentation pieces ─────────────────────────────────────────────

function Tile({ label, value, accent = NAVY }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-lg bg-white/10 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.15em] text-white/60">{label}</div>
      <div className="mt-0.5 text-lg font-bold" style={{ color: accent }}>{value}</div>
    </div>
  )
}

function NineBoxMini({ perf, pot }: { perf?: number | null; pot?: number | null }) {
  const has = perf != null && pot != null
  const p = Math.max(0, Math.min(1, perf ?? 0.5))
  const q = Math.max(0, Math.min(1, pot  ?? 0.5))
  // pin position within the 3×3 grid (px on a 90px canvas)
  const size = 90
  const x = p * size
  const y = (1 - q) * size
  return (
    <div className="shrink-0">
      <div className="relative rounded-md bg-white/10 p-1" style={{ width: size + 8, height: size + 8 }}>
        <div className="grid h-full w-full grid-cols-3 grid-rows-3 gap-[1px]">
          {BOX.flat().map((b, i) => (
            <div key={i} className="rounded-[2px]" style={{ background: b.c, opacity: 0.65 }} />
          ))}
        </div>
        {has && (
          <div
            title={boxOf(perf, pot).l}
            className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-white"
            style={{ left: x + 4, top: y + 4, background: NAVY }}
          />
        )}
      </div>
      <div className="mt-1 text-center text-[9px] uppercase tracking-wider text-white/70">Perf →   ↑ Pot</div>
    </div>
  )
}

function SnapshotHero({ person }: { person: Person }) {
  const rating = person.rating || person.dd?.rating || ''
  const rc = ratingColour(rating)
  const box = boxOf(person.performance, person.potential)
  const sections = person.dd?.sections?.length ?? 0
  const selfDone = (person.dd?.sections || []).reduce(
    (n, s) => n + (s.rows || []).filter((r) => r.employee != null).length, 0)
  const selfTotal = (person.dd?.sections || []).reduce((n, s) => n + (s.rows || []).length, 0)
  const tgts = person.targets || []
  const onTrack = tgts.filter((t) => (t.progress || 0) >= 60).length
  return (
    <div
      className="rounded-2xl p-5 md:p-6 text-white shadow-md"
      style={{ background: `linear-gradient(135deg, ${NAVY} 0%, #1D3270 65%, ${ORANGE} 200%)` }}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-[0.2em] text-white/70">Development Dialogue</div>
          <h2 className="mt-1 truncate text-2xl font-extrabold md:text-3xl">{person.name || '—'}</h2>
          <p className="text-[13px] text-white/80">
            {person.position || '—'}{person.dept ? ` · ${person.dept}` : ''}
          </p>
          <p className="mt-0.5 text-[12px] text-white/60">
            Period: {person.period || person.dd?.details?.reviewedPeriod || '—'}
            {' · '}Supervisor: {person.supervisor || person.dd?.details?.supName || '—'}
            {person.locked && (
              <span className="ml-2 rounded-full bg-green-500/20 px-2 py-0.5 text-[10px] font-bold text-green-200">
                SIGNED OFF
              </span>
            )}
          </p>
        </div>
        <div className="flex items-start gap-3">
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-[0.15em] text-white/60">Overall</div>
            <div className="text-4xl font-extrabold leading-none">
              {person.overall != null ? `${person.overall}%` : '—'}
            </div>
            {rating && (
              <span className="mt-1 inline-block rounded-full px-2 py-0.5 text-[11px] font-bold text-white" style={{ background: rc }}>
                {rating}
              </span>
            )}
          </div>
          <NineBoxMini perf={person.performance} pot={person.potential} />
        </div>
      </div>

      {/* stat tiles */}
      <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
        <Tile label="9-box placement" value={box.l} accent="#fff" />
        <Tile label="Self-assessment" value={selfTotal ? `${selfDone} / ${selfTotal}` : '—'} accent="#fff" />
        <Tile label="Targets on track" value={tgts.length ? `${onTrack} / ${tgts.length}` : '—'} accent="#fff" />
        <Tile label="Sections" value={sections ? String(sections) : '—'} accent="#fff" />
      </div>
    </div>
  )
}

function ScoreSummary({ dd }: { dd?: DD }) {
  const secs = dd?.sections || []
  if (!secs.length) return null
  const rowFor = (s: Section) => {
    const rows = s.rows || []
    const mgr = rows.map((r) => r.manager).filter((x): x is number => x != null)
    const emp = rows.map((r) => r.employee).filter((x): x is number => x != null)
    const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
    const w = s.weight ?? null
    const m = avg(mgr); const e = avg(emp)
    const weighted = m != null && w != null ? m * w : null
    return { name: s.name || '—', w, m, e, weighted, rows: rows.length }
  }
  const rows = secs.map(rowFor)
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <h3 className="mb-3 text-sm font-extrabold text-[#0B0B3B] dark:text-gray-100">Score summary</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead className="text-[10px] uppercase tracking-wider text-gray-500">
            <tr>
              <th className="text-left py-1.5 px-2">Section</th>
              <th className="text-right py-1.5 px-2">Weight</th>
              <th className="text-right py-1.5 px-2">Manager</th>
              <th className="text-right py-1.5 px-2">Self</th>
              <th className="text-right py-1.5 px-2">Weighted</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {rows.map((r, i) => {
              const mgrPct = r.m != null ? Math.round(r.m * 100) : null
              const bar = mgrPct == null ? 0 : mgrPct
              return (
                <tr key={i}>
                  <td className="py-1.5 px-2 text-gray-800 dark:text-gray-200">{r.name}</td>
                  <td className="py-1.5 px-2 text-right font-mono">{pctS(r.w)}</td>
                  <td className="py-1.5 px-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <span className="inline-block h-1 w-16 overflow-hidden rounded bg-gray-200 dark:bg-gray-700">
                        <span className="block h-full" style={{ width: `${bar}%`, background: ORANGE }} />
                      </span>
                      <span className="font-mono">{pctS(r.m)}</span>
                    </div>
                  </td>
                  <td className="py-1.5 px-2 text-right font-mono">{pctS(r.e)}</td>
                  <td className="py-1.5 px-2 text-right font-mono font-bold text-[#0B0B3B] dark:text-gray-100">{pctS(r.weighted)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Field({ label, value }: { label: string; value?: string }) {
  if (!value) return null
  return (
    <div className="mb-3">
      <div className="text-[10px] font-bold uppercase tracking-wide text-gray-400">{label}</div>
      <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-gray-800 dark:text-gray-200">{value}</div>
    </div>
  )
}

function Accordion({ title, weight, defaultOpen, children }:
  { title: string; weight?: number | null; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(!!defaultOpen)
  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between gap-2 px-5 py-4 text-left"
      >
        <span className="flex items-center gap-2">
          <span className="text-sm font-extrabold text-[#0B0B3B] dark:text-gray-100">{title}</span>
          {weight != null && (
            <span className="rounded-full bg-orange-50 px-2 py-0.5 text-[10px] font-bold" style={{ color: ORANGE }}>
              weight {pctW(weight)}
            </span>
          )}
        </span>
        <span className="text-gray-400" aria-hidden>{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="px-5 pb-5">{children}</div>}
    </div>
  )
}

function targetChip(t: Target) {
  const s = (t.status || '').toLowerCase()
  const p = t.progress || 0
  if (s.includes('done') || p >= 100)          return { bg: GREEN, fg: '#fff', text: 'Done' }
  if (s.includes('overdue') || s.includes('missed')) return { bg: RED,   fg: '#fff', text: 'Overdue' }
  if (s.includes('risk')  || p < 30)            return { bg: AMBER, fg: '#000', text: 'At risk' }
  if (s.includes('track') || p >= 60)           return { bg: TEAL,  fg: '#fff', text: 'On track' }
  return { bg: '#E5E7EB', fg: '#374151', text: t.status || '—' }
}

// ── main view ──────────────────────────────────────────────────────────────

export function DialogueView({ person }: { person: Person }) {
  const dd = person?.dd
  return (
    <div className="space-y-5">
      <SnapshotHero person={person} />
      <ScoreSummary dd={dd} />

      {dd?.sections?.map((sec, si) => (
        <Accordion key={si} title={sec.name || 'Section'} weight={sec.weight} defaultOpen={si === 0}>
          {(sec.rows || []).map((r, ri) => (
            <div key={ri} className="mb-4 border-l-2 pl-3" style={{ borderColor: ORANGE }}>
              <div className="text-[13px] font-bold text-gray-900 dark:text-gray-100">{r.perspective}</div>
              <Field label="Attributes" value={r.attributes} />
              <Field label="SBI — Situation, Behaviour & Impact" value={r.sbi} />
              <div className="my-2 flex flex-wrap gap-4 text-[12px]">
                <span className="text-gray-500">Manager: <b className="text-gray-800 dark:text-gray-200">{pctS(r.manager)}</b></span>
                <span className="text-gray-500">Employee: <b className="text-gray-800 dark:text-gray-200">{pctS(r.employee)}</b></span>
                <span className="text-gray-500">Weight: <b className="text-gray-800 dark:text-gray-200">{pctS(r.weight)}</b></span>
              </div>
              <Field label="Comments" value={r.comments} />
            </div>
          ))}
        </Accordion>
      ))}

      {dd?.values && dd.values.length > 0 && (
        <Accordion title="Value Supporting Behaviours">
          {dd.values.map((v, vi) => (
            <div key={vi} className="mb-3 border-l-2 pl-3" style={{ borderColor: ORANGE }}>
              <div className="text-[13px] font-bold text-gray-900 dark:text-gray-100">{v.value}</div>
              <Field label="Behaviours" value={v.behaviours} />
              <div className="my-1 flex flex-wrap gap-4 text-[12px] text-gray-500">
                <span>Self: <b className="text-gray-800 dark:text-gray-200">{pctS(v.self)}</b></span>
                <span>Manager: <b className="text-gray-800 dark:text-gray-200">{pctS(v.manager)}</b></span>
              </div>
              <Field label="Comments" value={v.comments} />
            </div>
          ))}
        </Accordion>
      )}

      {(dd?.pdp || dd?.careerAspirations || dd?.developmentPriorities || dd?.developmentMeasures || dd?.managersComments) && (
        <Accordion title="Development & Career">
          <Field label="Personal Development Plan" value={dd?.pdp} />
          <Field label="Career aspirations & goals" value={dd?.careerAspirations} />
          <Field label="Manager's comments" value={dd?.managersComments} />
          <Field label="Development priorities" value={dd?.developmentPriorities} />
          <Field label="Development measures" value={dd?.developmentMeasures} />
        </Accordion>
      )}

      {person.targets && person.targets.length > 0 && (
        <Accordion title={`Targets (${person.targets.length})`} defaultOpen>
          <div className="space-y-2">
            {person.targets.map((t, ti) => {
              const chip = targetChip(t)
              return (
                <div key={ti} className="rounded-lg border border-gray-100 p-3 dark:border-gray-800">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="text-[13px] text-gray-800 dark:text-gray-200">{t.title}</div>
                    <span className="rounded-full px-2 py-0.5 text-[10px] font-bold" style={{ background: chip.bg, color: chip.fg }}>
                      {chip.text}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-gray-500">
                    <span>Due: {fmtDate(t.due)}</span>
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-1.5 w-24 overflow-hidden rounded bg-gray-200 dark:bg-gray-700">
                        <span className="block h-full rounded" style={{ width: `${t.progress || 0}%`, background: ORANGE }} />
                      </span>
                      <b className="text-gray-700 dark:text-gray-300">{t.progress || 0}%</b>
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </Accordion>
      )}
    </div>
  )
}
