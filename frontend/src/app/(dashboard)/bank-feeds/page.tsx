'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getBankFeedConfigs,
  getBankFeedRuns,
  runBankFeedNow,
  type BankFeedConfig,
  type BankFeedRun,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  RotateCw, AlertTriangle, RefreshCw, Play, Banknote, Clock,
} from 'lucide-react'

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  active: { bg: '#ECFDF5', fg: '#047857' },
  paused: { bg: '#F3F4F6', fg: '#6B7280' },
  error:  { bg: '#FEF2F2', fg: '#B91C1C' },
}

const OUTCOME_BADGE: Record<string, { bg: string; fg: string }> = {
  success: { bg: '#ECFDF5', fg: '#047857' },
  empty:   { bg: '#F3F4F6', fg: '#6B7280' },
  error:   { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function BankFeedsPage() {
  const router = useRouter()
  const [configs, setConfigs] = useState<BankFeedConfig[]>([])
  const [runs, setRuns] = useState<BankFeedRun[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [c, r] = await Promise.all([
        getBankFeedConfigs(),
        getBankFeedRuns(),
      ])
      setConfigs(c.results)
      setRuns(r.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load feeds')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  async function handleRunNow(id: string) {
    setRunning(id); setError(null)
    try {
      await runBankFeedNow(id)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to trigger feed')
    } finally {
      setRunning(null)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Automated Bank Feeds"
        breadcrumbs={[{ label: 'Banking' }, { label: 'Bank Feeds' }]}
        actions={
          <Button
            variant="outline" size="sm"
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={load} disabled={loading}
          >
            Refresh
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Configs */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Banknote className="w-4 h-4 text-[#0B0B3B]" />
              Feed configurations
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && configs.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Banknote className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No bank feeds configured.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Create one in Django admin under <code>/admin/bank_feeds/</code>.
                </p>
              </div>
            )}
            {!loading && configs.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Name</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Bank Account</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Protocol</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Schedule</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Last Run</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {configs.map((cfg) => {
                      const c = STATUS_BADGE[cfg.status] || STATUS_BADGE.paused
                      return (
                        <tr key={cfg.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 text-[#374151]">{cfg.name}</td>
                          <td className="px-4 py-2.5 text-xs text-[#6B7280]">
                            <span className="font-mono">{cfg.bank_account_code}</span>
                            {' · '}{cfg.bank_account_name}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] text-xs">{cfg.protocol_display}</td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {cfg.schedule_cron || '—'}
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs">
                            {fmtDateTime(cfg.last_run_at)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
                            >
                              {cfg.status_display}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <Button
                              size="sm" variant="outline"
                              leftIcon={<Play className="w-3 h-3" />}
                              onClick={() => handleRunNow(cfg.id)}
                              disabled={running === cfg.id || cfg.status !== 'active'}
                            >
                              {running === cfg.id ? 'Running…' : 'Run now'}
                            </Button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Recent runs */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-[#0B0B3B]" />
              Recent runs
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {!loading && runs.length === 0 && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">
                No runs yet.
              </p>
            )}
            {!loading && runs.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Started</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Config</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Outcome</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Files</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Statements</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Lines</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Triggered by</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.slice(0, 30).map((run) => {
                      const o = OUTCOME_BADGE[run.outcome] || OUTCOME_BADGE.empty
                      return (
                        <tr key={run.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 text-[#374151] text-xs font-mono">
                            {fmtDateTime(run.started_at)}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151]">{run.config_name}</td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: o.bg, color: o.fg, borderColor: `${o.fg}30` }}
                            >
                              {run.outcome_display}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {run.files_seen}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {run.statements_created}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {run.lines_created}
                          </td>
                          <td className="px-4 py-2.5 text-xs text-[#6B7280]">
                            {run.triggered_by_username || 'cron'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
