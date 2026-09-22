'use client'
/** "Does this person manage people?" — decides whether the Team tab and the
 * Home live-team card show at all (CFO 2026-09-03). Two signals, either wins:
 * /team/glance/ returns at least one direct report, or /taskboard/overview/
 * answers 200 (a task-dashboard manager). The verdict is cached in
 * sessionStorage so the tab bar does not flicker between screens; default is
 * hidden until the probe says otherwise. */
import { useEffect, useState } from 'react'
import { afetch, AppApiError, getAppToken } from './api'

export const PROBE_KEY = 'omni_app_is_manager'

export interface GlanceReport {
  employee_id: string; user_id: number | null; name: string; job_title: string
  on_leave_today: boolean; leave_type: string | null; online: boolean
  /** dark_days_7 = short_days_7 + awaiting_review_7 (kept for older builds). */
  dark_days_7: number; short_days_7: number; awaiting_review_7: number
  open_tasks: number; overdue_tasks: number
}
/** overdue = TASKS; overdue_people = PEOPLE with at least one (CFO 2026-09-20 —
 * the old card said "15 overdue" next to seven names). awaiting_me = explanations
 * this manager still has to review. */
export interface GlanceCounts { in: number; on_leave: number; dark: number; overdue: number; overdue_people: number; awaiting_me: number }
export interface Glance { as_of: string; day_name: string; is_workday: boolean; reports: GlanceReport[]; counts: GlanceCounts }

function readCache(): boolean | null {
  try {
    const v = typeof window !== 'undefined' ? sessionStorage.getItem(PROBE_KEY) : null
    return v === '1' ? true : v === '0' ? false : null
  } catch { return null }
}
function writeCache(v: boolean) { try { sessionStorage.setItem(PROBE_KEY, v ? '1' : '0') } catch { /* private mode */ } }

export async function probeManager(): Promise<{ isManager: boolean; glance: Glance | null }> {
  let glance: Glance | null = null
  try { glance = await afetch<Glance>('/team/glance/') } catch { glance = null }
  if (glance && glance.reports.length > 0) return { isManager: true, glance }
  try {
    await afetch('/taskboard/overview/?week=this')
    return { isManager: true, glance }
  } catch (e) {
    // 403 "Managers only." is a definite no; a network blip is not a verdict.
    if (e instanceof AppApiError && e.status === 403) return { isManager: false, glance }
    return { isManager: readCache() ?? false, glance }
  }
}

export function useManagerProbe(enabled = true): { isManager: boolean; glance: Glance | null } {
  const [isManager, setIsManager] = useState(false)
  const [glance, setGlance] = useState<Glance | null>(null)
  useEffect(() => {
    // Nothing to probe with nobody signed in (the sign-in screen). Asking anyway
    // gets a 401, and a 401 sends the browser to sign in — a reload loop.
    if (!enabled || !getAppToken()) return
    const cached = readCache()
    if (cached !== null) setIsManager(cached)
    let alive = true
    probeManager().then(r => {
      if (!alive) return
      setIsManager(r.isManager); setGlance(r.glance); writeCache(r.isManager)
    })
    return () => { alive = false }
  }, [enabled])
  return { isManager, glance }
}
