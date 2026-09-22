'use client'

import { useEffect, useState, useCallback, useMemo, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import {
  getFNBStatus, getFNBSyncLogs, getFNBBatchSubmissions, testFNBConnection,
  pullFNBStatement, refreshFNBBatch,
  setBankAccountNumber, toggleBankAccountHidden, getBankAccounts,
  getFNBHealthSummary, askFNBHealth,
  getToken,
} from '@/lib/api'
import type {
  FNBStatus, FNBSyncLogItem, FNBBatchSubmissionItem, BankAccount,
  FNBHealthSummary,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, CheckCircle2, Activity,
  Banknote, RefreshCw, AlertTriangle, Download, Edit3,
  EyeOff, Eye, ChevronDown, ChevronUp, Sparkles, Bell, Ban, Plug,
} from 'lucide-react'
import { localYmd } from '@/lib/utils'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'

type Tone = 'good' | 'warn' | 'bad' | 'idle'
const TONE: Record<Tone, { bg: string; text: string; dot: string }> = {
  good: { bg: 'bg-emerald-50 border-emerald-200', text: 'text-emerald-800', dot: 'bg-emerald-500' },
  warn: { bg: 'bg-amber-50 border-amber-200',     text: 'text-amber-800',   dot: 'bg-amber-500' },
  bad:  { bg: 'bg-red-50 border-red-200',         text: 'text-red-800',     dot: 'bg-red-500' },
  idle: { bg: 'bg-zinc-50 border-zinc-200',       text: 'text-zinc-600',    dot: 'bg-zinc-400' },
}

function todayISO(): string { return localYmd(new Date()) }
function daysAgoISO(d: number): string {
  const t = new Date(); t.setDate(t.getDate() - d)
  return localYmd(t)
}

function HealthPill({ icon, label, value, sub, tone }: {
  icon: ReactNode; label: string; value: string; sub?: string; tone: Tone
}) {
  const t = TONE[tone]
  return (
    <div className={`rounded-xl border p-4 ${t.bg}`}>
      <div className={`flex items-center gap-2 ${t.text}`}>
        <span className={`w-2 h-2 rounded-full ${t.dot}`} />
        {icon}
        <span className="text-xs font-medium">{label}</span>
      </div>
      <div className={`text-base font-semibold mt-1.5 ${t.text}`}>{value}</div>
      {sub && <div className={`text-xs mt-0.5 ${t.text}`}>{sub}</div>}
    </div>
  )
}

export default function FNBIntegrationPage() {
  const router = useRouter()
  const [status, setStatus]       = useState<FNBStatus | null>(null)
  const [health, setHealth]       = useState<FNBHealthSummary | null>(null)
  const [healthLoading, setHealthLoading] = useState(true)
  const [logs, setLogs]           = useState<FNBSyncLogItem[]>([])
  const [batches, setBatches]     = useState<FNBBatchSubmissionItem[]>([])
  const [accounts, setAccounts]   = useState<BankAccount[]>([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [info, setInfo]           = useState<string | null>(null)
  const [testing, setTesting]     = useState(false)

  // Aria ask
  const [asking, setAsking]       = useState(false)
  const [answer, setAnswer]       = useState<string | null>(null)

  // Pull statements form
  const [pullAccId, setPullAccId]   = useState('')
  const [pullFrom, setPullFrom]     = useState(daysAgoISO(31))
  const [pullTo,   setPullTo]       = useState(todayISO())
  const [pulling, setPulling]       = useState(false)

  // Per-row refresh tracking
  const [refreshingId, setRefreshingId] = useState<string | null>(null)

  // Edit-account-number modal
  const [editingBA, setEditingBA] = useState<BankAccount | null>(null)
  const [editNum, setEditNum]     = useState('')
  const [editSaving, setEditSaving] = useState(false)

  // Expand "+N more" + show-hidden toggle
  const [expandAll,    setExpandAll]    = useState(false)
  const [showHidden,   setShowHidden]   = useState(false)
  const [togglingId,   setTogglingId]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [s, l, b, a] = await Promise.all([
        getFNBStatus().catch(() => null),
        getFNBSyncLogs({ page: 1 }).catch(() => null),
        getFNBBatchSubmissions({ page: 1 }).catch(() => null),
        getBankAccounts().catch(() => null),
      ])
      if (s) setStatus(s)
      const toList = <T,>(r: any): T[] => {
        if (!r) return []
        if (Array.isArray(r)) return r
        if (Array.isArray(r.results)) return r.results
        return []
      }
      setLogs(toList<FNBSyncLogItem>(l))
      setBatches(toList<FNBBatchSubmissionItem>(b))
      const accs = toList<BankAccount>(a)
      setAccounts(accs.filter((x) => x?.is_active !== false))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadHealth = useCallback(async (refresh = false) => {
    setHealthLoading(true)
    try {
      const h = await getFNBHealthSummary(refresh)
      setHealth(h)
    } catch {
      setHealth(null)
    } finally {
      setHealthLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load(); loadHealth()
  }, [load, loadHealth, router])

  const onTest = async () => {
    setTesting(true); setError(null); setInfo(null)
    try {
      const r = await testFNBConnection()
      if (r.success) {
        setInfo(`Connection OK — HTTP ${r.http_status} in ${r.elapsed_ms} ms`)
      } else {
        setError(r.detail || r.reason || 'Connection test failed')
      }
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Test failed')
    } finally {
      setTesting(false)
    }
  }

  const onAsk = async (question: string) => {
    setAsking(true); setAnswer(null); setError(null)
    try {
      const r = await askFNBHealth(question)
      setAnswer(r.answer || 'No answer returned.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Aria could not answer right now.')
    } finally {
      setAsking(false)
    }
  }

  const onPull = async () => {
    if (!pullAccId) { setError('Pick a bank account first.'); return }
    setPulling(true); setError(null); setInfo(null)
    try {
      const r = await pullFNBStatement({
        bank_account_id: pullAccId,
        from_date: pullFrom,
        to_date:   pullTo,
      })
      if (r.success) {
        setInfo(
          `Statement pulled: ${r.statement_number} — ${r.line_count} lines, ` +
          `open ${r.opening_balance} / close ${r.closing_balance} BWP`,
        )
      } else {
        setError(r.detail || r.reason || 'Pull failed')
      }
      await load(); loadHealth(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pull failed')
    } finally {
      setPulling(false)
    }
  }

  const onRefreshBatch = async (id: string) => {
    setRefreshingId(id); setError(null); setInfo(null)
    try {
      const r = await refreshFNBBatch(id)
      if (r.success) {
        setInfo(`Batch refreshed: FNB status = ${r.fnb_status || '(none)'}`)
      } else {
        setError(r.detail || 'Refresh failed')
      }
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Refresh failed')
    } finally {
      setRefreshingId(null)
    }
  }

  const openEdit = (ba: BankAccount) => {
    setEditingBA(ba); setEditNum(ba.account_number || ''); setError(null); setInfo(null)
  }
  const saveEdit = async () => {
    if (!editingBA) return
    if (!/^\d{1,32}$/.test(editNum)) {
      setError('Account number must be 1-32 digits.'); return
    }
    setEditSaving(true); setError(null); setInfo(null)
    try {
      const r = await setBankAccountNumber(editingBA.id, editNum)
      if (r.success) {
        setInfo(`Account number updated: ${r.old} → ${r.new}`)
        setEditingBA(null)
      }
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setEditSaving(false)
    }
  }

  const isUnsetPlaceholder = (s?: string) =>
    !s || s.startsWith('(unset') || s === '0' || !s.trim()
  const accLabel = (a: BankAccount) => {
    const rawName  = a.bank_name_display
      || (!isUnsetPlaceholder(a.bank_name) ? a.bank_name : '')
      || (!isUnsetPlaceholder(a.account_name) ? a.account_name : '')
      || a.gl_account_name
      || 'Unnamed account'
    const num = !isUnsetPlaceholder(a.account_number)
      ? a.account_number
      : (a.account_number_display && !isUnsetPlaceholder(a.account_number_display)
          ? a.account_number_display
          : 'NO NUMBER')
    return `${rawName} (${num})`
  }
  const accRowLabel = (a: BankAccount) => {
    const n = a.bank_name_display
      || (!isUnsetPlaceholder(a.bank_name) ? a.bank_name : '')
      || (!isUnsetPlaceholder(a.account_name) ? a.account_name : '')
      || a.gl_account_name
      || 'Unnamed account'
    return `${n} — GL ${a.gl_account_code || '?'}`
  }

  const visibleAccounts = useMemo(
    () => accounts.filter((a) => showHidden || !a.hide_in_banking_ui),
    [accounts, showHidden],
  )
  const accountsMissingNumber = useMemo(
    () => visibleAccounts.filter((a) => !a.account_number || a.account_number === '0'),
    [visibleAccounts],
  )
  const isFnb = (a: BankAccount) =>
    /fnb|first ?national/i.test(
      `${a.bank_name_display || ''} ${a.bank_name || ''} ${a.account_name || ''} ${a.gl_account_name || ''}`,
    )
  const fnbAccounts = useMemo(() => {
    const f = visibleAccounts.filter(isFnb)
    return f.length ? f : visibleAccounts
  }, [visibleAccounts])
  const hiddenCount = useMemo(
    () => accounts.filter((a) => a.hide_in_banking_ui).length,
    [accounts],
  )

  const notificationLogs = useMemo(
    () => logs.filter((l) => l.service === 'notification').slice(0, 6),
    [logs],
  )

  const onToggleHide = async (ba: BankAccount) => {
    setTogglingId(ba.id); setError(null); setInfo(null)
    try {
      const r = await toggleBankAccountHidden(ba.id, !ba.hide_in_banking_ui)
      if (r.success) {
        setInfo(`${ba.bank_name} — ${r.hide_in_banking_ui ? 'hidden from' : 'visible on'} this page`)
      }
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Hide toggle failed')
    } finally {
      setTogglingId(null)
    }
  }

  // ---- health-strip tone helpers -------------------------------------------
  const sig = health?.signals
  const connTone: Tone = !status ? 'idle'
    : !status.configured ? 'bad'
    : status.last_sync_status === 'success' ? 'good'
    : status.failed_calls_24h > 0 ? 'warn' : 'good'
  const stmtTone: Tone = !sig ? 'idle'
    : sig.statements.rejected_accounts.length > 0 ? 'warn'
    : sig.statements.imported_ok_24h > 0 ? 'good'
    : 'idle'
  const alertsState = sig?.alerts_feed.current_state || ''
  const alertsTone: Tone = !sig ? 'idle'
    : /working/i.test(alertsState) ? 'good'
    : /timeout|not responding/i.test(alertsState) ? 'warn'
    : /blocked|failing/i.test(alertsState) ? 'bad' : 'idle'
  const rejected = sig?.statements.rejected_accounts || []

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="FNB Botswana — bank connection" subtitle="Live health · statements · alerts, explained in plain English" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}
        {info && (
          <Card className="border-emerald-200 bg-emerald-50">
            <CardContent className="p-3 flex items-start gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-700 mt-0.5" />
              <span className="text-sm text-emerald-700">{info}</span>
            </CardContent>
          </Card>
        )}

        {/* Health strip */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <HealthPill
            icon={<Plug className="w-4 h-4" />} label="Connection" tone={connTone}
            value={!status ? '—' : status.configured ? 'Healthy' : 'Not set up'}
            sub={sig ? `last call ${sig.connection.last_call}` : undefined}
          />
          <HealthPill
            icon={<Download className="w-4 h-4" />} label="Statements" tone={stmtTone}
            value={!sig ? '—' : sig.statements.rejected_accounts.length > 0 ? 'One rejected' : 'Healthy'}
            sub={sig ? `${sig.statements.imported_ok_24h} imported (24h)` : undefined}
          />
          <HealthPill
            icon={<Bell className="w-4 h-4" />} label="Alerts" tone={alertsTone}
            value={!sig ? '—'
              : /working/i.test(alertsState) ? 'Working'
              : /timeout|not responding/i.test(alertsState) ? 'Not responding'
              : /blocked/i.test(alertsState) ? 'Blocked' : 'Check'}
            sub={sig ? `checked ${sig.alerts_feed.last_checked}` : undefined}
          />
          <HealthPill
            icon={<AlertTriangle className="w-4 h-4" />}
            label={rejected.length ? `${rejected.length} account issue` : 'Accounts'}
            tone={rejected.length ? 'bad' : 'good'}
            value={rejected.length ? rejected[0].split('→')[0].replace('account', '').trim() : 'All OK'}
            sub={rejected.length ? 'FNB must enable it' : 'no rejections'}
          />
        </div>

        {/* EFT batch submissions — moved to the top (CFO 2026-08-26): what left
            the bank is the first thing to see; Aria's read sits below it. */}
        <Card>
          <CardContent className="p-0">
            <div className="px-4 py-3 border-b flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-600" />
              <h3 className="font-medium">EFT batch submissions</h3>
            </div>
            {batches.length === 0 ? (
              <div className="p-12 text-center text-gray-500 text-sm">
                No batches submitted yet.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b text-xs uppercase text-gray-600">
                  <tr className="text-left">
                    <th className="px-4 py-3">Idempotency key</th>
                    <th className="px-4 py-3">From account</th>
                    <th className="px-4 py-3">To account</th>
                    <th className="px-4 py-3 text-right">Payments</th>
                    <th className="px-4 py-3 text-right">Total (BWP)</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">FNB ref</th>
                    <th className="px-4 py-3">Submitted</th>
                    <th className="px-4 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {batches.map((b) => (
                    <tr key={b.id} className="border-b border-gray-100">
                      <td className="px-4 py-2 font-mono text-xs">{b.idempotency_key}</td>
                      <td className="px-4 py-2 text-xs">{b.source_account_name}</td>
                      <td className="px-4 py-2 text-xs">
                        {!b.recipients || b.recipients.length === 0 ? (
                          '—'
                        ) : b.recipients.length === 1 ? (
                          <span>
                            {b.recipients[0].holder || b.recipients[0].bank || 'Payee'}
                            <span className="block font-mono text-[11px] text-gray-500">
                              {[b.recipients[0].bank, b.recipients[0].account_number].filter(Boolean).join(' · ')}
                            </span>
                          </span>
                        ) : (
                          <span title={b.recipients.map((r) => `${r.holder || 'Payee'} — ${r.account_number}`).join('\n')}>
                            {b.recipients.length} recipients
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right">{b.payment_count}</td>
                      <td className="px-4 py-2 text-right font-mono">{b.total_amount_bwp}</td>
                      <td className="px-4 py-2 text-xs">{b.status_display}</td>
                      <td className="px-4 py-2 font-mono text-xs">{b.fnb_reference || '—'}</td>
                      <td className="px-4 py-2 text-xs text-gray-600">
                        {b.submitted_at ? new Date(b.submitted_at).toLocaleString('en-BW', { dateStyle: 'short', timeStyle: 'short' }) : '—'}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <Button size="sm" variant="outline"
                          disabled={refreshingId === b.id}
                          onClick={() => onRefreshBatch(b.id)}>
                          <RefreshCw className={`w-3 h-3 mr-1 ${refreshingId === b.id ? 'animate-spin' : ''}`} />
                          {refreshingId === b.id ? 'Refreshing…' : 'Refresh'}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {/* Aria card */}
        <Card className="border-l-4" style={{ borderLeftColor: ORANGE }}>
          <CardContent className="p-5">
            <div className="flex items-center gap-2 mb-2">
              <span className="w-7 h-7 rounded-full flex items-center justify-center"
                style={{ background: '#EAF1FB' }}>
                <Sparkles className="w-4 h-4" style={{ color: NAVY }} />
              </span>
              <h3 className="text-sm font-semibold" style={{ color: NAVY }}>Aria&apos;s read</h3>
              <span className="text-[10px] font-mono text-gray-500 border rounded-full px-2 py-0.5">
                powered by DeepSeek
              </span>
              <Button size="sm" variant="outline" className="ml-auto"
                onClick={() => loadHealth(true)} disabled={healthLoading}>
                <RefreshCw className={`w-3 h-3 mr-1 ${healthLoading ? 'animate-spin' : ''}`} />
                {healthLoading ? 'Reading…' : 'Refresh'}
              </Button>
            </div>
            <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-line min-h-[2.5rem]">
              {healthLoading && !health ? 'Reading the bank connection…'
                : health?.summary || 'Aria could not read the connection just now.'}
            </p>
            <div className="flex gap-2 flex-wrap mt-3">
              <Button size="sm" variant="outline" disabled={asking}
                onClick={() => onAsk('Why is a bank account being rejected by FNB, and what should I ask FNB to fix it?')}>
                Why is an account failing?
              </Button>
              <Button size="sm" variant="outline" disabled={asking}
                onClick={() => onAsk('Draft a short email to FNB about the account that is rejected on statement retrieval.')}>
                Draft the FNB email
              </Button>
            </div>
            {(asking || answer) && (
              <div className="mt-3 rounded-lg border bg-[#FAFAFA] p-3 text-sm text-gray-800 whitespace-pre-line">
                {asking ? 'Aria is thinking…' : answer}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Connection card */}
        <Card>
          <CardContent className="p-6">
            <div className="flex items-start justify-between flex-wrap gap-3">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <Banknote className="w-5 h-5" style={{ color: ORANGE }} />
                  <h3 className="text-lg font-medium">Connection</h3>
                </div>
                {status && (
                  <div className="text-sm text-gray-600 space-y-0.5 mt-2">
                    <div>
                      Status:{' '}
                      {status.configured ? (
                        <span className="text-emerald-700 font-medium">Configured</span>
                      ) : (
                        <span className="text-amber-800 font-medium">Not configured</span>
                      )}
                    </div>
                    <div>Auth mode: <span className="font-mono text-xs">{status.auth_mode}</span></div>
                    <div>API base: <span className="font-mono text-xs">{status.api_base}</span></div>
                    <div>
                      Last sync: {status.last_sync_at
                        ? <>{new Date(status.last_sync_at).toLocaleString()} · <span className="font-medium">{status.last_sync_status}</span></>
                        : 'never'}
                    </div>
                    <div className="flex gap-4 mt-2">
                      <span>Pending batches: <strong>{status.pending_batches}</strong></span>
                      <span>Failed calls (24h): <strong className={status.failed_calls_24h > 0 ? 'text-red-700' : ''}>{status.failed_calls_24h}</strong></span>
                    </div>
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => { load(); loadHealth(true) }} disabled={loading}>
                  <RefreshCw className="w-4 h-4 mr-1" /> Refresh
                </Button>
                <Button onClick={onTest} disabled={testing || !status?.configured}
                  className="text-white" style={{ background: NAVY }}>
                  <Activity className="w-4 h-4 mr-1" /> {testing ? 'Testing...' : 'Test connection'}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Bank accounts missing FNB number */}
        {accountsMissingNumber.length > 0 && (
          <Card className="border-amber-300 bg-amber-50">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <AlertTriangle className="w-4 h-4 text-amber-700" />
                <h3 className="font-medium text-amber-900">
                  {accountsMissingNumber.length} bank account{accountsMissingNumber.length === 1 ? '' : 's'} need an FNB account number
                </h3>
                <div className="ml-auto flex items-center gap-2">
                  {hiddenCount > 0 && (
                    <Button size="sm" variant="outline"
                      onClick={() => setShowHidden((v) => !v)}
                      title="Toggle visibility of hidden accounts">
                      {showHidden
                        ? <><EyeOff className="w-3 h-3 mr-1" /> Hide hidden ({hiddenCount})</>
                        : <><Eye    className="w-3 h-3 mr-1" /> Show hidden ({hiddenCount})</>}
                    </Button>
                  )}
                </div>
              </div>
              <p className="text-xs text-amber-800 mb-3">
                Pull needs a real FNB account number (e.g. 63001966639).
                These rows show <code>&apos;0&apos;</code> — set the number, or hide rows you don&apos;t care about (E-Wallet, suspense, etc.).
              </p>
              <table className="w-full text-xs">
                <tbody>
                  {(expandAll
                    ? accountsMissingNumber
                    : accountsMissingNumber.slice(0, 8)
                  ).map((a) => (
                    <tr key={a.id} className={`border-t border-amber-200 ${a.hide_in_banking_ui ? 'opacity-60' : ''}`}>
                      <td className="py-2 pr-2">
                        {a.hide_in_banking_ui && <EyeOff className="w-3 h-3 inline mr-1 text-zinc-500" />}
                        {accRowLabel(a)}
                      </td>
                      <td className="py-2 pr-2 font-mono text-amber-700">{a.account_number || '—'}</td>
                      <td className="py-2 text-right whitespace-nowrap">
                        <Button size="sm" variant="outline" className="mr-1"
                          onClick={() => openEdit(a)}>
                          <Edit3 className="w-3 h-3 mr-1" /> Set number
                        </Button>
                        <Button size="sm" variant="outline"
                          disabled={togglingId === a.id}
                          onClick={() => onToggleHide(a)}
                          title={a.hide_in_banking_ui
                            ? 'Bring this row back onto the FNB page'
                            : "Hide this row — won't appear in dropdowns or this list"}>
                          {a.hide_in_banking_ui
                            ? <><Eye    className="w-3 h-3 mr-1" /> Unhide</>
                            : <><EyeOff className="w-3 h-3 mr-1" /> Hide</>}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {accountsMissingNumber.length > 8 && (
                <div className="mt-2">
                  <Button size="sm" variant="outline"
                    onClick={() => setExpandAll((v) => !v)}>
                    {expandAll
                      ? <><ChevronUp   className="w-3 h-3 mr-1" /> Collapse</>
                      : <><ChevronDown className="w-3 h-3 mr-1" /> Show all {accountsMissingNumber.length}</>}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Edit account number modal (inline) */}
        {editingBA && (
          <Card className="border-blue-300">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <Edit3 className="w-4 h-4 text-blue-700" />
                <h3 className="font-medium">Set account number — {accRowLabel(editingBA)}</h3>
              </div>
              <input
                value={editNum} onChange={(e) => setEditNum(e.target.value)}
                placeholder="e.g. 63001966639"
                className="w-full border rounded px-3 py-2 font-mono text-sm mb-2"
              />
              <div className="flex gap-2 justify-end">
                <Button variant="outline" onClick={() => setEditingBA(null)} disabled={editSaving}>Cancel</Button>
                <Button onClick={saveEdit} disabled={editSaving}
                  className="text-white" style={{ background: NAVY }}>
                  {editSaving ? 'Saving…' : 'Save'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Pull statement + Latest alerts */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <CardContent className="p-6">
              <div className="flex items-center gap-2 mb-3">
                <Download className="w-5 h-5" style={{ color: ORANGE }} />
                <h3 className="text-lg font-medium">Get a statement</h3>
              </div>
              <p className="text-xs text-gray-500 mb-3">
                Pulls one account&apos;s statement for a single calendar month into omni.
              </p>
              <div className="grid grid-cols-1 gap-3">
                <select value={pullAccId} onChange={(e) => setPullAccId(e.target.value)}
                  aria-label="Bank account"
                  className="border rounded px-3 py-2 text-sm">
                  <option value="">Pick bank account…</option>
                  {fnbAccounts.map((a) => {
                    const mapped = a.account_number && a.account_number !== '0'
                    return (
                      <option key={a.id} value={a.id} disabled={!mapped}>
                        {mapped ? accLabel(a) : `${accRowLabel(a)} — set FNB number first`}
                      </option>
                    )
                  })}
                </select>
                <div className="grid grid-cols-2 gap-3">
                  <input type="date" value={pullFrom} onChange={(e) => setPullFrom(e.target.value)}
                    aria-label="From date"
                    className="border rounded px-3 py-2 text-sm" />
                  <input type="date" value={pullTo} onChange={(e) => setPullTo(e.target.value)}
                    aria-label="To date"
                    className="border rounded px-3 py-2 text-sm" />
                </div>
                <Button onClick={onPull} disabled={pulling || !pullAccId}
                  className="text-white" style={{ background: NAVY }}>
                  <Download className="w-4 h-4 mr-1" /> {pulling ? 'Pulling…' : 'Pull statement'}
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-6">
              <div className="flex items-center gap-2 mb-3">
                <Bell className="w-5 h-5" style={{ color: ORANGE }} />
                <h3 className="text-lg font-medium">Latest alerts</h3>
              </div>
              {notificationLogs.length === 0 ? (
                <p className="text-sm text-gray-500 py-4">No alert checks recorded yet.</p>
              ) : (
                <div className="divide-y">
                  {notificationLogs.map((l) => {
                    const ok = l.status === 'success'
                    return (
                      <div key={l.id} className="flex items-center justify-between py-2.5 text-sm">
                        <span className="text-gray-600">
                          {new Date(l.created_at).toLocaleString('en-BW', { dateStyle: 'short', timeStyle: 'short' })}
                        </span>
                        <span className={ok ? 'text-emerald-700' : 'text-red-700'}>
                          {ok ? 'OK' : (l.http_status === 403 ? 'Blocked' : l.status_display)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Retired quick transfer */}
        <Card className="bg-zinc-50 border-zinc-200">
          <CardContent className="p-4 flex items-center gap-3">
            <Ban className="w-4 h-4 text-zinc-500 shrink-0" />
            <span className="text-sm text-zinc-600">
              The old &ldquo;Quick transfer&rdquo; tool has been retired — payments through it were never accepted by the bank.
              Make payments in the FNB online banking portal.
            </span>
          </CardContent>
        </Card>

        {/* Connection liveness — one line, replaced the noisy sync-log table
            (CFO 2026-08-26). Detail lives in Aria's read above. */}
        <div className="flex flex-wrap items-center gap-2 text-sm px-1 py-1">
          <span className={`inline-flex items-center gap-1.5 font-semibold ${connTone === 'good' ? 'text-emerald-700' : connTone === 'bad' ? 'text-red-700' : 'text-amber-700'}`}>
            <span className={`w-2.5 h-2.5 rounded-full ${connTone === 'good' ? 'bg-emerald-500' : connTone === 'bad' ? 'bg-red-500' : 'bg-amber-500'}`} />
            {connTone === 'good' ? 'LIVE' : connTone === 'bad' ? 'NOT LIVE' : connTone === 'warn' ? 'DEGRADED' : 'CHECKING'}
          </span>
          <span className="text-gray-500">
            {status?.last_sync_at
              ? <>· last sync {new Date(status.last_sync_at).toLocaleString('en-BW', { dateStyle: 'short', timeStyle: 'short' })} ({status.last_sync_status})</>
              : '· no sync yet'}
            {status && status.failed_calls_24h > 0 ? ` · ${status.failed_calls_24h} failed in 24h` : ''}
          </span>
        </div>

      </div>
    </div>
  )
}
