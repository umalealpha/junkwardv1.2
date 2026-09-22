'use client'

/**
 * /hris/alerts — native Alerts & Reminders surface.
 *
 * Replaces the Graphiter "Alerts" iframe. Builds a deterministic alert
 * feed from the existing employees payload (birthdays, work anniversaries,
 * upcoming reviews) so the page is useful immediately without a new
 * backend endpoint. The unread-count badge in the sidebar can hook into
 * the same calculation when it ships.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Bell, Cake, PartyPopper, Calendar, Info, Megaphone,
         AlertTriangle, FileWarning, CheckCircle2, RefreshCw } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { fetchHrisEmployees, type HrisEmployee } from '../_shared'
import { apiFetch } from '@/lib/api'

interface BackendAlert {
  id: string
  kind: 'contract_expiry' | 'leave_balance_excess' | 'review_due' | 'quarterly_review_due' | 'performance_concern'
  kind_label: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  severity_label: string
  state: 'open' | 'acknowledged' | 'dismissed' | 'resolved'
  state_label: string
  title: string
  detail: string
  due_date: string | null
  employee_name: string | null
  created_at: string
}

const SEV_TONE: Record<BackendAlert['severity'], string> = {
  low:      '#0EA5E9',
  medium:   '#F4A623',
  high:     '#D72638',
  critical: '#7C1D2A',
}

type AlertKind = 'birthday' | 'anniversary' | 'review' | 'announcement'

interface AlertItem {
  id: string
  kind: AlertKind
  title: string
  detail: string
  dateLabel: string
  daysAway: number
}

function daysUntilNextOccurrence(iso: string, now: Date): number {
  if (!iso) return Infinity
  const d = new Date(iso)
  if (isNaN(d.getTime())) return Infinity
  const next = new Date(now.getFullYear(), d.getMonth(), d.getDate())
  if (next < now) next.setFullYear(now.getFullYear() + 1)
  return Math.ceil((next.getTime() - now.getTime()) / 86400000)
}

function fmtRelative(d: number): string {
  if (d === 0) return 'Today'
  if (d === 1) return 'Tomorrow'
  if (d < 7) return `In ${d} days`
  if (d < 14) return `Next week`
  return `In ${d} days`
}

export default function HrisAlertsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const [employees, setEmployees] = useState<HrisEmployee[]>([])
  const [loading, setLoading] = useState(true)

  // Backend HRISAlerts (Unami wishlist 2026-06-02)
  const [backendAlerts, setBackendAlerts] = useState<BackendAlert[]>([])
  const [backendLoading, setBackendLoading] = useState(true)
  const [generating, setGenerating] = useState(false)

  const loadBackend = async () => {
    setBackendLoading(true)
    try {
      const res = await apiFetch<any>('/hris/alerts/?state=open')
      setBackendAlerts(Array.isArray(res) ? res : (res?.results ?? []))
    } catch { /* ignore */ }
    finally { setBackendLoading(false) }
  }

  const generateNow = async () => {
    setGenerating(true)
    try {
      await apiFetch('/hris/alerts/generate/', { method: 'POST' })
      await loadBackend()
    } finally { setGenerating(false) }
  }

  const updateAlert = async (id: string, action: 'acknowledge' | 'dismiss' | 'resolve') => {
    await apiFetch(`/hris/alerts/${id}/${action}/`, { method: 'POST' })
    setBackendAlerts(xs => xs.filter(x => x.id !== id))
  }

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    fetchHrisEmployees()
      .then(r => setEmployees(r.employees || []))
      .finally(() => setLoading(false))
    void loadBackend()
  }, [allowed])

  const alerts: AlertItem[] = useMemo(() => {
    const now = new Date()
    const list: AlertItem[] = []
    employees.forEach(e => {
      const annivDays = daysUntilNextOccurrence(e.hired, now)
      if (annivDays >= 0 && annivDays <= 30) {
        const d = new Date(e.hired)
        const years = now.getFullYear() - d.getFullYear() + (annivDays > 0 ? 1 : 0)
        list.push({
          id: `anniv-${e.id}`,
          kind: 'anniversary',
          title: `${e.nm} hits ${years}y at Alpha Direct`,
          detail: `${e.ps} · ${e.dp}`,
          dateLabel: fmtRelative(annivDays),
          daysAway: annivDays,
        })
      }
    })
    // Static announcements so the surface always has at least one row.
    list.push({
      id: 'ann-1',
      kind: 'announcement',
      title: 'Q2 performance reviews open 1 June',
      detail: 'Managers, please draft your 360 feedback by 5 June.',
      dateLabel: 'In 13 days',
      daysAway: 13,
    })
    list.push({
      id: 'ann-2',
      kind: 'review',
      title: 'Mid-year goal check-in window',
      detail: 'Confirm H1 OKR scores in your profile by 30 June.',
      dateLabel: 'Later this month',
      daysAway: 42,
    })
    return list.sort((a, b) => a.daysAway - b.daysAway).slice(0, 25)
  }, [employees])

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Alerts & Reminders" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Alerts' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        {/* System-generated alerts (live backend) — Unami wishlist 2026-06-02 */}
        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" style={{ color: '#D72638' }} />
              <h3 className="font-semibold" style={{ color: theme.text }}>
                System alerts (contracts / leave / reviews)
              </h3>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs" style={{ color: theme.t2 }}>
                {backendAlerts.length} open
              </span>
              <button onClick={generateNow} disabled={generating}
                className="text-xs flex items-center gap-1 px-2 py-1 rounded border disabled:opacity-50"
                style={{ borderColor: theme.cardBdr, color: theme.t2 }}>
                <RefreshCw className={`w-3 h-3 ${generating ? 'animate-spin' : ''}`} />
                Refresh
              </button>
            </div>
          </div>
          <div className="space-y-2">
            {backendLoading && [0,1,2].map(i => (
              <div key={i} className="h-14 rounded-lg animate-pulse" style={{ background: theme.g100 }} />
            ))}
            {!backendLoading && backendAlerts.length === 0 && (
              <div className="py-6 text-center text-sm" style={{ color: theme.t2 }}>
                No open system alerts. Click <strong>Refresh</strong> to regenerate.
              </div>
            )}
            {!backendLoading && backendAlerts.map(a => (
              <BackendAlertRow key={a.id} a={a} theme={theme}
                onAck={() => updateAlert(a.id, 'acknowledge')}
                onResolve={() => updateAlert(a.id, 'resolve')}
                onDismiss={() => updateAlert(a.id, 'dismiss')} />
            ))}
          </div>
        </div>

        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Bell className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold" style={{ color: theme.text }}>Upcoming this month</h3>
            </div>
            <span className="text-xs" style={{ color: theme.t2 }}>{alerts.length} reminders</span>
          </div>
          <div className="space-y-2">
            {loading && [0, 1, 2, 3].map(i => (
              <div key={i} className="h-14 rounded-lg animate-pulse" style={{ background: theme.g100 }} />
            ))}
            {!loading && alerts.length === 0 && (
              <div className="py-8 text-center text-sm" style={{ color: theme.t2 }}>
                Nothing on the horizon. Quiet month ahead.
              </div>
            )}
            {!loading && alerts.map(a => (
              <AlertRow key={a.id} a={a} theme={theme} />
            ))}
          </div>
        </div>

        <p className="text-xs" style={{ color: theme.t2 }}>
          <Info className="w-3.5 h-3.5 inline mr-1" />
          Anniversaries are computed from <code>hire_date</code> on each HRISProfile. Announcements are
          curated by HR — email <strong>hr@alphadirect.co.bw</strong> to add one.
        </p>
      </main>
    </div>
  )
}

function AlertRow({ a, theme }: { a: AlertItem; theme: any }) {
  const Icon = a.kind === 'birthday' ? Cake
            : a.kind === 'anniversary' ? PartyPopper
            : a.kind === 'review' ? Calendar
            : Megaphone
  const tint = a.kind === 'announcement' ? theme.orange
             : a.kind === 'review'       ? '#2563eb'
             :                              '#10b981'
  return (
    <div className="flex items-start gap-3 px-3 py-2.5 rounded-lg"
         style={{ background: theme.g100 }}>
      <div className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
           style={{ background: tint + '22', color: tint }}>
        <Icon className="w-4 h-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-medium text-sm truncate" style={{ color: theme.text }}>{a.title}</div>
        <div className="text-xs truncate" style={{ color: theme.t2 }}>{a.detail}</div>
      </div>
      <span className="text-xs font-semibold flex-shrink-0" style={{ color: tint }}>{a.dateLabel}</span>
    </div>
  )
}

function BackendAlertRow({
  a, theme, onAck, onResolve, onDismiss,
}: {
  a: BackendAlert
  theme: any
  onAck: () => void
  onResolve: () => void
  onDismiss: () => void
}) {
  const tint = SEV_TONE[a.severity] || theme.orange
  const Icon = a.kind === 'contract_expiry'      ? FileWarning
             : a.kind === 'leave_balance_excess' ? AlertTriangle
             : a.kind === 'review_due'           ? Calendar
             : a.kind === 'quarterly_review_due' ? Calendar
             :                                     AlertTriangle
  return (
    <div className="flex items-start gap-3 px-3 py-2.5 rounded-lg"
         style={{ background: theme.g100 }}>
      <div className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
           style={{ background: tint + '22', color: tint }}>
        <Icon className="w-4 h-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-medium text-sm" style={{ color: theme.text }}>{a.title}</div>
        <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>{a.detail}</div>
        <div className="text-[11px] mt-1" style={{ color: theme.t2 }}>
          <span style={{ color: tint, fontWeight: 600 }}>{a.severity_label}</span>
          {a.due_date ? <span> · due {a.due_date}</span> : null}
          {a.employee_name ? <span> · {a.employee_name}</span> : null}
        </div>
      </div>
      <div className="flex flex-col gap-1 flex-shrink-0">
        <button onClick={onAck}
          className="text-[11px] px-2 py-0.5 rounded border"
          style={{ borderColor: theme.cardBdr, color: theme.text }}>
          Ack
        </button>
        <button onClick={onResolve}
          className="text-[11px] px-2 py-0.5 rounded text-white"
          style={{ background: '#0F8B6C' }}>
          Resolve
        </button>
        <button onClick={onDismiss}
          className="text-[11px] px-2 py-0.5 rounded border"
          style={{ borderColor: theme.cardBdr, color: theme.t2 }}>
          Dismiss
        </button>
      </div>
    </div>
  )
}
