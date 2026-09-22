'use client'

/**
 * /hris/inbox — Manager command-center.
 *
 * Single network call to /hris/api/inbox/ aggregates everything the
 * caller should action: pending leave decisions, recent kudos received,
 * open onboarding tasks. CFO directive 2026-05-20 (Manus HRIS Part 4).
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, Inbox as InboxIcon, Calendar, Award, ClipboardCheck,
  Loader2, RefreshCw, ArrowUpRight,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface InboxResp {
  pending_leave: Array<{ id: string; employee: string; leave_code: string; start_date: string; end_date: string; days: number }>
  kudos_recent:  Array<{ id: string; sender: string; value: string; message: string; points: number; when: string }>
  onboarding_open: Array<{ id: string; employee: string; task: string; category: string; due_date: string }>
  counts: { pending_leave: number; kudos_recent: number; onboarding_open: number }
}

export default function HrisInboxPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const [data, setData] = useState<InboxResp | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    refresh()
  }, [allowed])

  function refresh() {
    setLoading(true)
    authedHrisFetch('/hris/api/inbox/')
      .then(async r => {
        if (r.ok) setData(await r.json())
      })
      .finally(() => setLoading(false))
  }

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const counts = data?.counts || { pending_leave: 0, kudos_recent: 0, onboarding_open: 0 }
  const total = counts.pending_leave + counts.onboarding_open

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Manager Inbox" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Inbox' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <div className="flex items-center justify-between">
          <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
            <ChevronLeft className="w-4 h-4" /> Back to HRIS
          </Link>
          <button type="button" onClick={refresh}
                  className="inline-flex items-center gap-1.5 text-xs font-semibold"
                  style={{ color: theme.orange }}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
        </div>

        <div className="rounded-2xl p-5"
             style={{ background: `linear-gradient(135deg, ${theme.navy}, ${theme.navy}e6)`, color: '#fff' }}>
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-[11px] uppercase tracking-widest opacity-60 font-semibold">Pending actions</div>
              <h2 className="font-display text-3xl font-bold mt-1 italic tabular-nums">{total}</h2>
              <p className="text-xs opacity-70 mt-1">
                {counts.pending_leave} leave · {counts.onboarding_open} onboarding
              </p>
            </div>
            <InboxIcon className="w-12 h-12 opacity-30" />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* Pending leave */}
          <InboxSection
            theme={theme}
            icon={Calendar}
            title="Pending leave"
            count={counts.pending_leave}
            cta={{ href: '/hris/leave', label: 'Open queue' }}
          >
            {loading && <Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.t2 }} />}
            {!loading && data?.pending_leave?.length === 0 && (
              <p className="text-xs" style={{ color: theme.t2 }}>Nothing pending. Inbox zero.</p>
            )}
            {!loading && data?.pending_leave?.map(row => (
              <div key={row.id} className="rounded-md px-3 py-2"
                   style={{ background: theme.g100 }}>
                <div className="text-sm font-medium" style={{ color: theme.text }}>{row.employee}</div>
                <div className="text-[11px]" style={{ color: theme.t2 }}>
                  {/* toFixed(0) showed a 2.5-day request to its approver as "3d".
                      Trim, never round — same rule as every other leave screen. */}
                  {row.leave_code} · {row.start_date} → {row.end_date} · {parseFloat(row.days.toFixed(2))}d
                </div>
              </div>
            ))}
          </InboxSection>

          {/* Recent kudos */}
          <InboxSection
            theme={theme}
            icon={Award}
            title="Recent kudos"
            count={counts.kudos_recent}
            cta={{ href: '/hris/rewards', label: 'See feed' }}
          >
            {loading && <Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.t2 }} />}
            {!loading && data?.kudos_recent?.length === 0 && (
              <p className="text-xs" style={{ color: theme.t2 }}>No kudos yet.</p>
            )}
            {!loading && data?.kudos_recent?.map(row => (
              <div key={row.id} className="rounded-md px-3 py-2"
                   style={{ background: theme.g100 }}>
                <div className="text-sm" style={{ color: theme.text }}>
                  <strong>{row.sender}</strong>{' '}
                  <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold"
                        style={{ background: theme.orange + '22', color: theme.orange }}>
                    {row.value}
                  </span>
                </div>
                <p className="text-xs italic mt-1" style={{ color: theme.t2 }}>"{row.message}"</p>
              </div>
            ))}
          </InboxSection>

          {/* Onboarding tasks */}
          <InboxSection
            theme={theme}
            icon={ClipboardCheck}
            title="Onboarding tasks"
            count={counts.onboarding_open}
            cta={{ href: '/hris/inbox', label: 'View all' }}
          >
            {loading && <Loader2 className="w-4 h-4 animate-spin" style={{ color: theme.t2 }} />}
            {!loading && data?.onboarding_open?.length === 0 && (
              <p className="text-xs" style={{ color: theme.t2 }}>No open tasks.</p>
            )}
            {!loading && data?.onboarding_open?.map(row => (
              <div key={row.id} className="rounded-md px-3 py-2"
                   style={{ background: theme.g100 }}>
                <div className="text-sm font-medium" style={{ color: theme.text }}>{row.task}</div>
                <div className="text-[11px]" style={{ color: theme.t2 }}>
                  {row.employee} · {row.category} · due {row.due_date || '—'}
                </div>
              </div>
            ))}
          </InboxSection>
        </div>
      </main>
    </div>
  )
}

function InboxSection({ theme, icon: Icon, title, count, cta, children }:
                      { theme: any; icon: any; title: string; count: number;
                        cta?: { href: string; label: string }; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl p-5 space-y-2"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
          <Icon className="w-4 h-4" style={{ color: theme.orange }} />
          {title}
          {count > 0 && (
            <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold"
                  style={{ background: theme.orange, color: '#fff' }}>{count}</span>
          )}
        </h3>
        {cta && (
          <Link href={cta.href} className="text-[11px] font-semibold inline-flex items-center gap-0.5"
                style={{ color: theme.t2 }}>
            {cta.label} <ArrowUpRight className="w-3 h-3" />
          </Link>
        )}
      </div>
      {children}
    </div>
  )
}
