'use client'

/**
 * /hris/skills — Skills & Gaps Map.
 *
 * A grid of who has which skill across the org (proficiency 1-5), plus a
 * gaps panel: how many people are proficient (level >= 3) per skill, and
 * which skills are a single point of failure (0-1 proficient people —
 * risk if that person leaves). HR can add skills and set an employee's
 * level from the controls above the grid, or by clicking a cell.
 *
 * Calls GET/POST /api/v1/hris/skills/*. See hris/skills_views.py.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import FeatureAcceptBar from '@/components/hris/FeatureAcceptBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface SkillRow {
  id: string
  name: string
  category: string
}
interface PersonRow {
  profile_id: string
  name: string
  department: string
}
interface GapRow {
  skill_id: string
  name: string
  proficient: number
  single_point_of_failure: boolean
}
interface MatrixResponse {
  skills: SkillRow[]
  people: PersonRow[]
  levels: Record<string, number>
  gaps: GapRow[]
}

// Proficiency ramp: 1 pale -> 5 strong, navy/orange family (matches the
// brand palette, distinct from the theme's generic status colors).
const LEVEL_STYLE: Record<number, { bg: string; fg: string }> = {
  1: { bg: '#FDEBD3', fg: '#8A4A12' },
  2: { bg: '#FBD3A0', fg: '#7A3D00' },
  3: { bg: '#F4A623', fg: '#2B1600' },
  4: { bg: '#C1730E', fg: '#FFFFFF' },
  5: { bg: '#0D1B2A', fg: '#F4A623' },
}
const LEVEL_LABEL: Record<number, string> = {
  1: 'Novice', 2: 'Basic', 3: 'Proficient', 4: 'Advanced', 5: 'Expert',
}

export default function SkillsGapsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const levelFormRef = useRef<HTMLFormElement>(null)

  const [data, setData] = useState<MatrixResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Add-skill form
  const [skillName, setSkillName] = useState('')
  const [skillCategory, setSkillCategory] = useState('')
  const [addingSkill, setAddingSkill] = useState(false)
  const [addSkillError, setAddSkillError] = useState('')

  // Set-level form
  const [levelProfileId, setLevelProfileId] = useState('')
  const [levelSkillId, setLevelSkillId] = useState('')
  const [levelValue, setLevelValue] = useState('3')
  const [levelNote, setLevelNote] = useState('')
  const [savingLevel, setSavingLevel] = useState(false)
  const [levelError, setLevelError] = useState('')
  const [levelSaved, setLevelSaved] = useState(false)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  const load = useCallback(() => {
    return authedHrisFetch('/api/v1/hris/skills/matrix/')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d: MatrixResponse) => { setData(d); setError('') })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
  }, [])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    load().finally(() => setLoading(false))
  }, [allowed, load])

  const sortedGaps = useMemo(() => {
    if (!data) return []
    return [...data.gaps].sort((a, b) => {
      if (a.single_point_of_failure !== b.single_point_of_failure) {
        return a.single_point_of_failure ? -1 : 1
      }
      return a.proficient - b.proficient
    })
  }, [data])

  const stats = useMemo(() => {
    if (!data) return null
    return {
      skills: data.skills.length,
      people: data.people.length,
      atRisk: data.gaps.filter(g => g.single_point_of_failure).length,
    }
  }, [data])

  async function handleAddSkill(e: React.FormEvent) {
    e.preventDefault()
    const name = skillName.trim()
    if (!name || addingSkill) return
    setAddingSkill(true)
    setAddSkillError('')
    try {
      const r = await authedHrisFetch('/api/v1/hris/skills/skill/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, category: skillCategory.trim() }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
      setSkillName('')
      setSkillCategory('')
      await load()
    } catch (err) {
      setAddSkillError(err instanceof Error ? err.message : 'Could not add skill')
    } finally {
      setAddingSkill(false)
    }
  }

  async function handleSetLevel(e: React.FormEvent) {
    e.preventDefault()
    if (!levelProfileId || !levelSkillId || savingLevel) return
    setSavingLevel(true)
    setLevelError('')
    setLevelSaved(false)
    try {
      const r = await authedHrisFetch('/api/v1/hris/skills/set-level/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_id: levelProfileId,
          skill_id: levelSkillId,
          level: Number(levelValue),
          note: levelNote.trim(),
        }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
      setLevelNote('')
      setLevelSaved(true)
      await load()
    } catch (err) {
      setLevelError(err instanceof Error ? err.message : 'Could not save level')
    } finally {
      setSavingLevel(false)
    }
  }

  function openCellInForm(profileId: string, skillId: string, currentLevel?: number) {
    setLevelProfileId(profileId)
    setLevelSkillId(skillId)
    setLevelValue(String(currentLevel || 3))
    setLevelSaved(false)
    setLevelError('')
    levelFormRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  if (allowed !== true) return <Loader theme={theme} />

  const hasSkills = !!data && data.skills.length > 0

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Skills & Gaps Map"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Skills & Gaps' }]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <FeatureAcceptBar featureKey="skills" />
        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {loading && (
          <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>
            Loading the skills map…
          </div>
        )}

        {!loading && data && (
          <>
            {/* Summary stats */}
            <section className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              <StatCard label="Skills tracked" value={stats?.skills ?? 0} theme={theme} />
              <StatCard label="People mapped" value={stats?.people ?? 0} theme={theme} />
              <div
                className="rounded-xl p-3"
                style={{
                  background: (stats?.atRisk ?? 0) > 0 ? theme.erB : theme.okB,
                  color: (stats?.atRisk ?? 0) > 0 ? theme.er : theme.ok,
                }}
              >
                <div className="text-[10px] uppercase tracking-wider opacity-80">At-risk skills</div>
                <div className="text-2xl font-bold mt-1">{stats?.atRisk ?? 0}</div>
                <div className="text-[11px] opacity-90">single point of failure</div>
              </div>
            </section>

            {/* Controls: add a skill / set a level */}
            <section
              className="rounded-xl p-4 grid grid-cols-1 md:grid-cols-2 gap-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <form onSubmit={handleAddSkill} className="space-y-2">
                <h3 className="text-sm font-semibold" style={{ color: theme.text }}>Add a skill</h3>
                <div>
                  <label className="text-[11px] uppercase tracking-wide" style={{ color: theme.t3 }}>
                    Skill name
                  </label>
                  <input
                    type="text"
                    value={skillName}
                    onChange={e => setSkillName(e.target.value)}
                    placeholder="e.g. IFRS 17 reporting"
                    maxLength={120}
                    className="mt-1 w-full rounded-md px-3 py-1.5 text-sm outline-none"
                    style={{ background: theme.input, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
                  />
                </div>
                <div>
                  <label className="text-[11px] uppercase tracking-wide" style={{ color: theme.t3 }}>
                    Category (optional)
                  </label>
                  <input
                    type="text"
                    value={skillCategory}
                    onChange={e => setSkillCategory(e.target.value)}
                    placeholder="e.g. Finance"
                    maxLength={60}
                    className="mt-1 w-full rounded-md px-3 py-1.5 text-sm outline-none"
                    style={{ background: theme.input, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
                  />
                </div>
                {addSkillError && <div className="text-xs" style={{ color: theme.er }}>{addSkillError}</div>}
                <button
                  type="submit"
                  disabled={!skillName.trim() || addingSkill}
                  className="rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50"
                  style={{ background: theme.navy, color: theme.orange }}
                >
                  {addingSkill ? 'Adding…' : 'Add skill'}
                </button>
              </form>

              <form onSubmit={handleSetLevel} className="space-y-2" ref={levelFormRef}>
                <h3 className="text-sm font-semibold" style={{ color: theme.text }}>Set a person&apos;s level</h3>
                {(!data.people.length || !data.skills.length) ? (
                  <p className="text-xs" style={{ color: theme.t3 }}>
                    Add a skill first{!data.people.length ? ' (and confirm HRIS profiles exist)' : ''} to rate someone.
                  </p>
                ) : (
                  <>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[11px] uppercase tracking-wide" style={{ color: theme.t3 }}>Person</label>
                        <select
                          value={levelProfileId}
                          onChange={e => setLevelProfileId(e.target.value)}
                          className="mt-1 w-full rounded-md px-2 py-1.5 text-sm outline-none"
                          style={{ background: theme.input, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
                        >
                          <option value="">Select…</option>
                          {data.people.map(p => (
                            <option key={p.profile_id} value={p.profile_id}>
                              {p.name}{p.department ? ` — ${p.department}` : ''}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="text-[11px] uppercase tracking-wide" style={{ color: theme.t3 }}>Skill</label>
                        <select
                          value={levelSkillId}
                          onChange={e => setLevelSkillId(e.target.value)}
                          className="mt-1 w-full rounded-md px-2 py-1.5 text-sm outline-none"
                          style={{ background: theme.input, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
                        >
                          <option value="">Select…</option>
                          {data.skills.map(s => (
                            <option key={s.id} value={s.id}>{s.name}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[11px] uppercase tracking-wide" style={{ color: theme.t3 }}>Level</label>
                        <select
                          value={levelValue}
                          onChange={e => setLevelValue(e.target.value)}
                          className="mt-1 w-full rounded-md px-2 py-1.5 text-sm outline-none"
                          style={{ background: theme.input, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
                        >
                          {[1, 2, 3, 4, 5].map(l => (
                            <option key={l} value={l}>{l} — {LEVEL_LABEL[l]}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="text-[11px] uppercase tracking-wide" style={{ color: theme.t3 }}>Note (optional)</label>
                        <input
                          type="text"
                          value={levelNote}
                          onChange={e => setLevelNote(e.target.value)}
                          placeholder="e.g. certified 2024"
                          maxLength={200}
                          className="mt-1 w-full rounded-md px-2 py-1.5 text-sm outline-none"
                          style={{ background: theme.input, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
                        />
                      </div>
                    </div>
                    {levelError && <div className="text-xs" style={{ color: theme.er }}>{levelError}</div>}
                    {levelSaved && !levelError && <div className="text-xs" style={{ color: theme.ok }}>Saved.</div>}
                    <button
                      type="submit"
                      disabled={!levelProfileId || !levelSkillId || savingLevel}
                      className="rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50"
                      style={{ background: theme.orange, color: theme.navy }}
                    >
                      {savingLevel ? 'Saving…' : 'Save level'}
                    </button>
                  </>
                )}
              </form>
            </section>

            {!hasSkills && (
              <section
                className="rounded-xl p-8 text-center"
                style={{ background: theme.card, border: `1px dashed ${theme.cardBdr}` }}
              >
                <div className="text-sm font-medium" style={{ color: theme.text }}>No skills tracked yet.</div>
                <div className="text-sm mt-1" style={{ color: theme.t2 }}>
                  Add your first skill above to start mapping who can do what — and where the gaps are.
                </div>
              </section>
            )}

            {hasSkills && (
              <>
                {/* Gaps panel */}
                <section className="space-y-2">
                  <h2 className="text-sm font-semibold" style={{ color: theme.text }}>Gaps — coverage per skill</h2>
                  <div className="rounded-xl overflow-hidden" style={{ border: `1px solid ${theme.cardBdr}` }}>
                    {sortedGaps.map((g, i) => {
                      const tier = g.single_point_of_failure ? 'critical' : g.proficient <= 2 ? 'watch' : 'ok'
                      const tierStyle = tier === 'critical'
                        ? { bg: theme.erB, fg: theme.er, label: 'Single point of failure' }
                        : tier === 'watch'
                          ? { bg: theme.wrB, fg: theme.wr, label: 'Thin bench' }
                          : { bg: theme.okB, fg: theme.ok, label: 'Covered' }
                      return (
                        <div
                          key={g.skill_id}
                          className="flex items-center justify-between gap-3 px-4 py-2.5"
                          style={{
                            background: theme.card,
                            borderTop: i === 0 ? 'none' : `1px solid ${theme.cardBdr}`,
                          }}
                        >
                          <div className="text-sm font-medium" style={{ color: theme.text }}>{g.name}</div>
                          <div className="flex items-center gap-3">
                            <span className="text-xs" style={{ color: theme.t2 }}>
                              {g.proficient} {g.proficient === 1 ? 'person' : 'people'} proficient
                            </span>
                            <span
                              className="text-[11px] font-semibold px-2 py-1 rounded uppercase"
                              style={{ background: tierStyle.bg, color: tierStyle.fg }}
                            >
                              {tierStyle.label}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </section>

                {/* Matrix grid */}
                <section className="space-y-2">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-semibold" style={{ color: theme.text }}>
                      Skills matrix — click a cell to rate it
                    </h2>
                    <div className="flex items-center gap-1.5">
                      {[1, 2, 3, 4, 5].map(l => (
                        <span key={l} className="flex items-center gap-1 text-[11px]" style={{ color: theme.t3 }}>
                          <span
                            className="inline-block h-3 w-3 rounded-sm"
                            style={{ background: LEVEL_STYLE[l].bg }}
                          />
                          {l}
                        </span>
                      ))}
                    </div>
                  </div>

                  {!data.people.length ? (
                    <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>
                      No HRIS profiles found in your entity scope yet.
                    </div>
                  ) : (
                    <div
                      className="overflow-x-auto rounded-xl"
                      style={{ border: `1px solid ${theme.cardBdr}` }}
                    >
                      <table className="min-w-full text-sm border-collapse">
                        <thead>
                          <tr>
                            <th
                              className="sticky left-0 z-10 text-left px-3 py-2 whitespace-nowrap"
                              style={{ background: theme.g100, color: theme.t2 }}
                            >
                              Person
                            </th>
                            {data.skills.map(s => (
                              <th
                                key={s.id}
                                title={s.category || s.name}
                                className="px-2 py-2 text-center font-medium whitespace-nowrap"
                                style={{ background: theme.g100, color: theme.t2 }}
                              >
                                {s.name}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {data.people.map(p => (
                            <tr key={p.profile_id}>
                              <td
                                className="sticky left-0 z-10 px-3 py-2 whitespace-nowrap"
                                style={{ background: theme.card, borderTop: `1px solid ${theme.cardBdr}` }}
                              >
                                <div className="font-medium" style={{ color: theme.text }}>{p.name}</div>
                                <div className="text-[11px]" style={{ color: theme.t3 }}>{p.department || '—'}</div>
                              </td>
                              {data.skills.map(s => {
                                const lvl = data.levels[`${p.profile_id}:${s.id}`]
                                const st = lvl ? LEVEL_STYLE[lvl] : undefined
                                return (
                                  <td
                                    key={s.id}
                                    className="px-2 py-2 text-center"
                                    style={{ borderTop: `1px solid ${theme.cardBdr}` }}
                                  >
                                    <button
                                      type="button"
                                      onClick={() => openCellInForm(p.profile_id, s.id, lvl)}
                                      title={lvl ? `${LEVEL_LABEL[lvl]} — click to change` : 'Not rated — click to set'}
                                      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-xs font-semibold transition-opacity hover:opacity-80"
                                      style={st ? { background: st.bg, color: st.fg } : { background: theme.g100, color: theme.t3 }}
                                    >
                                      {lvl || '–'}
                                    </button>
                                  </td>
                                )
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              </>
            )}
          </>
        )}
      </main>
    </div>
  )
}

function StatCard({ label, value, theme }: { label: string; value: number; theme: { card: string; cardBdr: string; text: string; t2: string } }) {
  return (
    <div className="rounded-xl p-3" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t2 }}>{label}</div>
      <div className="text-2xl font-bold mt-1" style={{ color: theme.text }}>{value}</div>
    </div>
  )
}

function Loader({ theme }: { theme: { bg: string } }) {
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
      <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
    </div>
  )
}
