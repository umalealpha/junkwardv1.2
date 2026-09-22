'use client'

/**
 * /transformation — the Transformation Board (CFO 2026-09-20): "First AI
 * Insurance Company in Botswana", 20-Sep-2026 → 20-Jan-2027.
 *
 * Restricted server-side to CEO / CFO / COO / Chief Human Capital Officer
 * (transformation/permissions.py CanViewTransformationBoard) — a non-viewer
 * gets a calm 403 here, never a crash. Every figure comes straight off
 * transformation/pulse.py build_board() + workforce.py workforce_block();
 * this page only lays it out, it never computes a number of its own.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Lock, RefreshCw } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/contexts/ThemeContext'
import {
  assignToInitiative,
  getTransformationBoard,
  getTransformationHistory,
  setTransformationProgress,
  unassignFromInitiative,
  type TransformationAssignment,
  type TransformationAssignPayload,
  type TransformationBoard,
  type TransformationHistoryPoint,
  type TransformationInitiative,
  type TransformationPerson,
  type TransformationProgressUpdate,
} from '@/lib/api'
import { Hero } from '@/components/transformation/Hero'
import { EvolutionStrip } from '@/components/transformation/EvolutionStrip'
import { PathStepper } from '@/components/transformation/PathStepper'
import { MorningNote } from '@/components/transformation/MorningNote'
import { BlockedAndBehind } from '@/components/transformation/BlockedAndBehind'
import { DepartmentsHeatmap } from '@/components/transformation/DepartmentsHeatmap'
import { PeopleLeaderboard } from '@/components/transformation/PeopleLeaderboard'
import { StaffCostBridge } from '@/components/transformation/StaffCostBridge'
import { WorkforcePanel } from '@/components/transformation/WorkforcePanel'
import { InitiativesBoard } from '@/components/transformation/InitiativesBoard'
import { TrendChart } from '@/components/transformation/TrendChart'
import { SectionCard } from '@/components/transformation/SectionCard'

export default function TransformationBoardPage() {
  const { theme, reduceMotion } = useTheme()
  const [board, setBoard] = useState<TransformationBoard | null>(null)
  const [history, setHistory] = useState<TransformationHistoryPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [denied, setDenied] = useState<string | null>(null)

  const load = useCallback(async (live = false) => {
    if (live) setRefreshing(true)
    else setLoading(true)
    setError(null)
    setDenied(null)
    try {
      const [b, h] = await Promise.all([
        getTransformationBoard(live),
        // The trend is a nice-to-have — a history hiccup should never take
        // down the whole board.
        getTransformationHistory().catch(() => ({ points: [] as TransformationHistoryPoint[] })),
      ])
      setBoard(b)
      setHistory(h.points)
    } catch (e) {
      const err = e as Error & { status?: number }
      if (err.status === 403) {
        setDenied(err.message || 'The Transformation Board is restricted.')
      } else {
        setError(err.message || 'Could not load the Transformation Board.')
      }
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Optimistic edit for the initiative progress controls: apply the change to
  // local state immediately, send the POST, and roll every field back to the
  // pre-edit snapshot if the server refuses it. Only the initiatives array is
  // touched — the board's other roll-up figures (overall_percent, the month
  // stepper, department costs) are computed server-side and stay as of the
  // last load until the next refresh, rather than being re-derived from a
  // partial guess here.
  const handleProgressChange = useCallback(async (
    code: string,
    payload: TransformationProgressUpdate,
  ): Promise<boolean> => {
    let previous: TransformationInitiative[] = []
    setBoard((cur) => {
      if (!cur) return cur
      previous = cur.initiatives
      return {
        ...cur,
        initiatives: cur.initiatives.map((item) => {
          if (item.code !== code) return item
          return {
            ...item,
            ...(payload.percent !== undefined ? { percent: payload.percent } : {}),
            ...(payload.status !== undefined ? { status: payload.status } : {}),
            ...(payload.blocked_on !== undefined ? { blocked_on: payload.blocked_on } : {}),
          }
        }),
      }
    })

    try {
      const result = await setTransformationProgress(code, payload)
      setBoard((cur) => cur ? {
        ...cur,
        initiatives: cur.initiatives.map((item) => item.code === code
          ? { ...item, percent: result.percent, status: result.status as TransformationInitiative['status'], blocked_on: result.blocked_on }
          : item),
      } : cur)
      return true
    } catch {
      setBoard((cur) => cur ? { ...cur, initiatives: previous } : cur)
      return false
    }
  }, [])

  // Assign / unassign follow the same optimistic-then-reconcile shape: apply
  // a best-guess change immediately, then overwrite with the `assignments`
  // array the server actually returns (never keep guessing after that), and
  // roll every field back to the pre-edit snapshot if the server refuses it —
  // e.g. the 400 "not an active Omni login" from transformation/assign.py,
  // which is shown to the CFO verbatim rather than a generic failure.
  const handleAssign = useCallback(async (
    code: string,
    payload: TransformationAssignPayload,
    person: TransformationPerson,
  ): Promise<{ ok: boolean; error?: string }> => {
    let previous: TransformationInitiative[] = []
    const optimistic: TransformationAssignment = {
      name: person.name,
      email: person.email,
      role: payload.role || '',
      is_owner: !!payload.is_owner,
      due_date: payload.due_date || null,
      task_id: null,
      task_status: 'pending',
      overdue: false,
    }
    setBoard((cur) => {
      if (!cur) return cur
      previous = cur.initiatives
      return {
        ...cur,
        initiatives: cur.initiatives.map((item) => {
          if (item.code !== code) return item
          const rest = item.assignments.filter((a) => a.email !== person.email)
          return { ...item, assignments: [...rest, optimistic] }
        }),
      }
    })

    try {
      const result = await assignToInitiative(code, payload)
      setBoard((cur) => cur ? {
        ...cur,
        initiatives: cur.initiatives.map((item) => item.code === code
          ? { ...item, assignments: result.assignments }
          : item),
      } : cur)
      return { ok: true }
    } catch (e) {
      setBoard((cur) => cur ? { ...cur, initiatives: previous } : cur)
      return { ok: false, error: (e as Error).message }
    }
  }, [])

  const handleUnassign = useCallback(async (
    code: string,
    email: string,
  ): Promise<{ ok: boolean; error?: string }> => {
    let previous: TransformationInitiative[] = []
    setBoard((cur) => {
      if (!cur) return cur
      previous = cur.initiatives
      return {
        ...cur,
        initiatives: cur.initiatives.map((item) => item.code === code
          ? { ...item, assignments: item.assignments.filter((a) => a.email !== email) }
          : item),
      }
    })

    try {
      const result = await unassignFromInitiative(code, email)
      setBoard((cur) => cur ? {
        ...cur,
        initiatives: cur.initiatives.map((item) => item.code === code
          ? { ...item, assignments: result.assignments }
          : item),
      } : cur)
      return { ok: true }
    } catch (e) {
      setBoard((cur) => cur ? { ...cur, initiatives: previous } : cur)
      return { ok: false, error: (e as Error).message }
    }
  }, [])

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Transformation Board" />

      <div className="p-4 lg:p-6 space-y-5 max-w-[1400px] mx-auto">
        {denied && (
          <div
            className="rounded-lg p-5 flex items-start gap-3"
            style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}
          >
            <Lock className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: theme.t2 }} />
            <div>
              <p className="text-sm font-medium" style={{ color: theme.navy }}>This board is restricted</p>
              <p className="text-sm mt-1" style={{ color: theme.t2 }}>{denied}</p>
            </div>
          </div>
        )}

        {error && !denied && (
          <div
            className="rounded-lg p-4 flex items-center gap-3"
            style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}
          >
            <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: theme.er }} />
            <div className="flex-1">
              <p className="font-medium text-sm" style={{ color: theme.er }}>Couldn&apos;t load the board</p>
              <p className="text-xs mt-0.5" style={{ color: theme.er, opacity: 0.8 }}>{error}</p>
            </div>
            <Button variant="danger" size="sm" onClick={() => load()}>Retry</Button>
          </div>
        )}

        {loading && !denied && !error && (
          <div className="space-y-5">
            <div className="rounded-2xl h-72 animate-pulse" style={{ background: theme.g100 }} />
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="rounded-xl h-40 animate-pulse" style={{ background: theme.g100 }} />
            ))}
          </div>
        )}

        {!loading && !denied && !error && board && (
          <>
            <div className="flex flex-col items-end gap-1">
              <Button variant="secondary" size="sm" onClick={() => load(true)} disabled={refreshing}>
                <RefreshCw className={`w-4 h-4 mr-1.5 ${refreshing ? 'animate-spin' : ''}`} strokeWidth={1.7} />
                Recompute now
              </Button>
              <p className="text-xs" style={{ color: theme.t3 }}>
                {board.source} · as of {board.as_of}
                {board.stale_days ? ` · ${board.stale_days} day(s) old` : ''}
              </p>
            </div>

            <Hero
              theme={theme}
              headline={board.headline}
              overallPercent={board.overall_percent}
              timePercent={board.time_percent}
              clock={board.clock}
              reduceMotion={reduceMotion}
            />

            {/* The CFO's own idea (2026-09-20): the March of Progress, but the
                company evolving from paper to fully AI. Renders nothing if an
                older cached snapshot has no `evolution` block. */}
            <EvolutionStrip theme={theme} evolution={board.evolution} reduceMotion={reduceMotion} />

            <SectionCard theme={theme} title="The path" subtitle="Four months to 20 January 2027" index={0} reduceMotion={reduceMotion} id="tb-path">
              <PathStepper theme={theme} months={board.months} reduceMotion={reduceMotion} />
            </SectionCard>

            <SectionCard theme={theme} title="This morning" subtitle="AI read of the board, and the judge's check on it" index={1} reduceMotion={reduceMotion} id="tb-note">
              <MorningNote theme={theme} ai={board.ai} />
            </SectionCard>

            <SectionCard theme={theme} title="Blocked and behind" index={2} reduceMotion={reduceMotion} id="tb-blocked">
              <BlockedAndBehind theme={theme} blocked={board.blocked} behind={board.behind} />
            </SectionCard>

            <SectionCard
              theme={theme}
              title="Departments"
              subtitle="Which department is not using automation, and what it costs"
              index={3}
              reduceMotion={reduceMotion}
              id="tb-departments"
            >
              <DepartmentsHeatmap theme={theme} departments={board.departments} />
            </SectionCard>

            <SectionCard theme={theme} title="People" subtitle="Pushing automation, and who is not" index={4} reduceMotion={reduceMotion} id="tb-people">
              <PeopleLeaderboard
                theme={theme}
                pushing={board.leaderboard.pushing}
                lagging={board.leaderboard.lagging}
                unscored={board.leaderboard.unscored}
              />
            </SectionCard>

            <SectionCard theme={theme} title="Staff cost" subtitle="Now vs. target shape" index={5} reduceMotion={reduceMotion} id="tb-cost">
              <StaffCostBridge
                theme={theme}
                staffCost={board.staff_cost}
                costOfDelay={board.cost_of_delay}
                salaryAtRiskMonth={board.workforce.salary_at_risk_month}
                reduceMotion={reduceMotion}
              />
            </SectionCard>

            <SectionCard theme={theme} title="Workforce" subtitle={`Time Doctor + attendance, last ${board.workforce.window_days} days`} index={6} reduceMotion={reduceMotion} id="tb-workforce">
              <WorkforcePanel theme={theme} workforce={board.workforce} />
            </SectionCard>

            <SectionCard theme={theme} title="The steps" subtitle="Every initiative, grouped by month" index={7} reduceMotion={reduceMotion} id="tb-steps">
              <InitiativesBoard
                theme={theme}
                initiatives={board.initiatives}
                onProgressChange={handleProgressChange}
                onAssign={handleAssign}
                onUnassign={handleUnassign}
              />
            </SectionCard>

            <SectionCard theme={theme} title="Trend" subtitle="Work done vs. time used, over time" index={8} reduceMotion={reduceMotion} id="tb-trend">
              <TrendChart theme={theme} points={history} daysTotal={board.clock.days_total} />
            </SectionCard>
          </>
        )}
      </div>
    </div>
  )
}
