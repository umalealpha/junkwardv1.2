'use client'

/**
 * /periods — Period Management (CFO directive 2026-05-28).
 *
 * Primary user: Pako Kago (Financial Controller). When a TB upload fails
 * with "Period [YYYY-MM] is locked", he comes here to clear his FM lock
 * signature, re-upload the TB, then re-sign.
 *
 * Backend reality: locks are DUAL-signed (CFO + FM). A period is fully
 * LOCKED only when both signatures are present; clearing either signature
 * flips the status back to OPEN. So Pako alone can clear FM → unlock,
 * re-upload, re-sign FM → lock — without CFO ever touching the period.
 */

import { useEffect, useState, useMemo, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getFiscalPeriods, signPeriodLock, clearPeriodLock, getPeriodAuditLog,
  getToken, getMe,
} from '@/lib/api'
import type { FiscalPeriod, PeriodAuditEntry, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { ConfirmDialog } from '@/components/ui/modal'
import { formatDate, cn } from '@/lib/utils'
import { useCompany } from '@/contexts/CompanyContext'
import {
  Lock, Unlock, ShieldCheck, AlertCircle, CheckCircle2, History, X, Filter,
} from 'lucide-react'

type Role = 'cfo' | 'fm'

interface UserRoles { canCfo: boolean; canFm: boolean; preferred: Role | null }

function deriveRoles(me: UserProfile | null): UserRoles {
  if (!me) return { canCfo: false, canFm: false, preferred: null }
  const title = (me.title || '').toLowerCase()
  const canCfo = title === 'cfo'
  const canFm  = me.is_administrator || title === 'cfo' || title === 'finance_manager'
  const preferred: Role | null = canCfo ? 'cfo' : (canFm ? 'fm' : null)
  return { canCfo, canFm, preferred }
}

function daysUntil(iso: string): number {
  const d = new Date(iso).getTime() - Date.now()
  return Math.ceil(d / 86_400_000)
}

function statusBadge(p: FiscalPeriod) {
  // CFO directive 2026-06-09: deferred auto-lock — when status is OPEN
  // but auto_lock_at is set and still in the future, show a distinct
  // "locks in N d" badge so users know the grace window is ticking.
  // Once the timestamp passes, is_effectively_locked goes True on the
  // server and gates JE posting; we render it as "Auto-Locked" so the
  // dual-sign Sign buttons are still allowed (override path).
  const autoAt = (p as any).auto_lock_at as string | null | undefined
  const autoPending = !!(p as any).auto_lock_pending
  const effLocked   = !!(p as any).is_effectively_locked
  if (p.status === 'open' && autoPending && autoAt) {
    const n = daysUntil(autoAt)
    const label = n <= 0 ? 'Locks today' : (n === 1 ? 'Locks in 1d' : `Locks in ${n}d`)
    return { label, bg: '#FFF7ED', fg: '#CC6C00', icon: Lock }
  }
  if (p.status === 'open' && effLocked) {
    return { label: 'Auto-locked', bg: '#FEF2F2', fg: '#DC2626', icon: Lock }
  }
  switch (p.status) {
    case 'open':    return { label: 'Open',    bg: '#ECFDF5', fg: '#059669', icon: Unlock }
    case 'locked':  return { label: 'Locked',  bg: '#FEF2F2', fg: '#DC2626', icon: Lock }
    case 'closing': return { label: 'Closing', bg: '#FEF3C7', fg: '#D97706', icon: Lock }
    case 'closed':  return { label: 'Closed',  bg: '#F3F4F6', fg: '#6B7280', icon: Lock }
    default:        return { label: p.status,  bg: '#F3F4F6', fg: '#6B7280', icon: Lock }
  }
}

export default function PeriodsPage() {
  const router = useRouter()
  const { companies, selectedId: ctxCompanyId } = useCompany()

  const [me, setMe] = useState<UserProfile | null>(null)
  const [rows, setRows] = useState<FiscalPeriod[]>([])
  const [audit, setAudit] = useState<PeriodAuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [acting, setActing] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [showAudit, setShowAudit] = useState(false)
  const [confirmBulk, setConfirmBulk] = useState<'sign' | 'clear' | null>(null)

  // filters
  const [entityFilter, setEntityFilter] = useState<string>('')   // company id, '' = all
  const [fyFilter, setFyFilter] = useState<string>('')           // fiscal_year_label, '' = all

  const roles = useMemo(() => deriveRoles(me), [me])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params: Record<string, string> = { page_size: '500' }
      if (entityFilter) params.company = entityFilter
      else if (ctxCompanyId) params.company = ctxCompanyId
      const [periodsRes, auditRes, meRes] = await Promise.all([
        getFiscalPeriods(params),
        getPeriodAuditLog(20).catch(() => [] as PeriodAuditEntry[]),
        me ? Promise.resolve(me) : getMe().catch(() => null),
      ])
      setRows(periodsRes.results || [])
      setAudit(auditRes)
      if (!me && meRes) setMe(meRes)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load periods')
    } finally { setLoading(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityFilter, ctxCompanyId])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  // ---------- derived ----------
  const fyOptions = useMemo(() => {
    const labels = new Set<string>()
    rows.forEach(r => { if (r.fiscal_year_label) labels.add(r.fiscal_year_label) })
    return Array.from(labels).sort().reverse()
  }, [rows])

  const visibleRows = useMemo(() => {
    let xs = rows.slice()
    if (fyFilter) xs = xs.filter(r => r.fiscal_year_label === fyFilter)
    xs.sort((a, b) => {
      const e = (a.company_code || '').localeCompare(b.company_code || '')
      if (e !== 0) return e
      return a.start_date.localeCompare(b.start_date)
    })
    return xs
  }, [rows, fyFilter])

  const lockedCount = visibleRows.filter(r => r.status === 'locked').length
  const openCount   = visibleRows.filter(r => r.status === 'open').length

  const toggleOne = (id: string) => setSelected(prev => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const toggleAll = () => setSelected(prev => {
    const allSel = visibleRows.length > 0 && visibleRows.every(r => prev.has(r.id))
    const n = new Set<string>()
    if (!allSel) visibleRows.forEach(r => n.add(r.id))
    return n
  })
  const clearSel = () => setSelected(new Set())

  // ---------- actions ----------
  const runForRow = async (id: string, op: 'sign' | 'clear', role: Role): Promise<string | null> => {
    try {
      if (op === 'sign')  await signPeriodLock(id, role, '')
      else                await clearPeriodLock(id, role)
      return null
    } catch (err) {
      return err instanceof Error ? err.message : 'failed'
    }
  }

  const handleRow = async (id: string, op: 'sign' | 'clear', role: Role) => {
    setActing(true); setError(null); setSuccess(null)
    const err = await runForRow(id, op, role)
    if (err) setError(err)
    else { setSuccess(`${op === 'sign' ? 'Signed' : 'Cleared'} ${role.toUpperCase()}`); setTimeout(() => setSuccess(null), 3500) }
    setActing(false); await load()
  }

  const handleBulk = async (op: 'sign' | 'clear') => {
    if (!roles.preferred) { setError('Your title is not authorised for lock signatures.'); return }
    const ids = Array.from(selected)
    if (ids.length === 0) return
    setConfirmBulk(null)
    setActing(true); setError(null); setSuccess(null)
    let ok = 0; const errs: string[] = []
    for (const id of ids) {
      const e = await runForRow(id, op, roles.preferred)
      if (e) errs.push(e); else ok++
    }
    if (errs.length === 0) {
      setSuccess(`${op === 'sign' ? 'Signed' : 'Cleared'} ${ok} periods as ${roles.preferred.toUpperCase()}`)
      setTimeout(() => setSuccess(null), 4500)
    } else {
      setError(`${op} ok=${ok} fails=${errs.length}: ${errs.slice(0, 3).join('; ')}`)
    }
    setActing(false); clearSel(); await load()
  }

  const roleLabel = roles.preferred ? roles.preferred.toUpperCase() : '—'

  // ---------- render ----------
  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Period Management"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Period Management' }]}
      />
      <div className="flex-1 p-6 space-y-4">

        {/* role banner */}
        <div className="flex items-center gap-2 text-sm text-[#6B7280]">
          <ShieldCheck className="w-4 h-4 text-[#CC6C00]" />
          {loading ? (
            // BUG-004: role fetch not resolved yet — don't flash the "no title"
            // warning before we know the user's role.
            <span className="text-[#9CA3AF]">Checking your lock permissions…</span>
          ) : roles.preferred ? (
            <span>
              Signed in as <b className="text-[#0D1B2A]">{me?.first_name || me?.username}</b> &middot; lock role: <b>{roleLabel}</b>.
              Periods need <b>BOTH</b> a CFO and an FM signature to fully lock. Clearing either signature opens the period.
            </span>
          ) : (
            <span>Your account has no CFO or FM title — you can view the lock status but cannot sign or clear locks. Ask the CFO to set your title.</span>
          )}
        </div>

        {/* filters */}
        <Card>
          <CardContent className="py-3">
            <div className="flex flex-wrap items-center gap-3">
              <Filter className="w-4 h-4 text-[#6B7280]" />
              <select
                value={entityFilter}
                onChange={e => { setEntityFilter(e.target.value); clearSel() }}
                className="h-9 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827]"
              >
                <option value="">All entities</option>
                {companies.map(c => (
                  <option key={c.id} value={c.id}>{c.code} — {c.name}</option>
                ))}
              </select>
              <select
                value={fyFilter}
                onChange={e => { setFyFilter(e.target.value); clearSel() }}
                className="h-9 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827]"
              >
                <option value="">All fiscal years</option>
                {fyOptions.map(fy => <option key={fy} value={fy}>{fy}</option>)}
              </select>
              <div className="ml-auto flex items-center gap-3 text-xs">
                <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-[#ECFDF5] text-[#059669] font-medium">
                  <Unlock className="w-3 h-3" /> {openCount} open
                </span>
                <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-[#FEF2F2] text-[#DC2626] font-medium">
                  <Lock className="w-3 h-3" /> {lockedCount} locked
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* bulk action bar */}
        {selected.size > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={clearSel} className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium bg-[#FFF7ED] text-[#CC6C00] border border-[#FFD7B5] hover:bg-[#FFEDD5]">
              {selected.size} selected <X className="w-3.5 h-3.5" />
            </button>
            <Button
              variant="accent" size="sm" leftIcon={<Lock className="w-3.5 h-3.5" />}
              onClick={() => setConfirmBulk('sign')}
              disabled={acting || !roles.preferred}
            >Sign {roleLabel} on selected</Button>
            <Button
              variant="outline" size="sm" leftIcon={<Unlock className="w-3.5 h-3.5" />}
              onClick={() => setConfirmBulk('clear')}
              disabled={acting || !roles.preferred}
            >Clear {roleLabel} on selected</Button>
          </div>
        )}

        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[#059669]" />
            <p className="text-[#059669] text-sm">{success}</p>
          </div>
        )}
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* table */}
        <Card>
          <CardContent className="p-0">
            {loading ? <LoadingTable rows={10} cols={8} /> : visibleRows.length === 0 ? (
              <div className="px-4 py-16 text-center text-[#6B7280] text-sm">No periods match the current filter.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                    <tr>
                      <th className="w-10 px-3 py-3">
                        <input type="checkbox"
                          checked={visibleRows.length > 0 && visibleRows.every(r => selected.has(r.id))}
                          onChange={toggleAll} />
                      </th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Entity</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">FY</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Period</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">CFO Sig</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">FM Sig</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Last Modified</th>
                      <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {visibleRows.map(p => {
                      const s = statusBadge(p)
                      const SI = s.icon
                      const isSel = selected.has(p.id)
                      const cfoSig = !!p.locked_by_cfo
                      const fmSig  = !!p.locked_by_fm
                      const userRole = roles.preferred
                      const userSigned = userRole === 'cfo' ? cfoSig : userRole === 'fm' ? fmSig : false
                      return (
                        <tr key={p.id} className={cn('transition-colors', isSel ? 'bg-[#FFF7ED]' : 'hover:bg-[#FAFAFA]')}>
                          <td className="px-3 py-3" onClick={e => e.stopPropagation()}>
                            <input type="checkbox" checked={isSel} onChange={() => toggleOne(p.id)} />
                          </td>
                          <td className="px-3 py-3 text-[#111827]">
                            {p.company_code ? (
                              <><span className="font-mono text-xs text-[#CC6C00]">{p.company_code}</span> <span className="text-[#6B7280]">·</span> {p.company_name}</>
                            ) : <span className="text-[#9CA3AF]">— legacy —</span>}
                          </td>
                          <td className="px-3 py-3 text-[#374151]">{p.fiscal_year_label || '—'}</td>
                          <td className="px-3 py-3 font-mono text-xs text-[#111827] font-semibold">{p.period_name}</td>
                          <td className="px-3 py-3">
                            <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-medium" style={{ background: s.bg, color: s.fg }}>
                              <SI className="w-3 h-3" /> {s.label}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-[#374151] text-xs">
                            {cfoSig ? (
                              <span title={p.locked_by_cfo_at || ''}>✓ <span className="text-[#6B7280]">{p.locked_by_cfo_name || ''}</span></span>
                            ) : <span className="text-[#D1D5DB]">—</span>}
                          </td>
                          <td className="px-3 py-3 text-[#374151] text-xs">
                            {fmSig ? (
                              <span title={p.locked_by_fm_at || ''}>✓ <span className="text-[#6B7280]">{p.locked_by_fm_name || ''}</span></span>
                            ) : <span className="text-[#D1D5DB]">—</span>}
                          </td>
                          <td className="px-3 py-3 text-[#6B7280] text-xs whitespace-nowrap">{p.updated_at ? formatDate(p.updated_at) : '—'}</td>
                          <td className="px-3 py-3">
                            {userRole ? (
                              userSigned ? (
                                <Button variant="outline" size="sm" leftIcon={<Unlock className="w-3.5 h-3.5" />}
                                  onClick={() => handleRow(p.id, 'clear', userRole)} disabled={acting}>
                                  Clear {userRole.toUpperCase()}
                                </Button>
                              ) : (
                                <Button variant="accent" size="sm" leftIcon={<Lock className="w-3.5 h-3.5" />}
                                  onClick={() => handleRow(p.id, 'sign', userRole)} disabled={acting || p.status === 'closing' || p.status === 'closed'}>
                                  Sign {userRole.toUpperCase()}
                                </Button>
                              )
                            ) : <span className="text-[#9CA3AF] text-xs">read-only</span>}
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

        {/* audit log */}
        <Card>
          <CardHeader className="cursor-pointer" onClick={() => setShowAudit(s => !s)}>
            <CardTitle className="flex items-center gap-2 text-sm">
              <History className="w-4 h-4 text-[#CC6C00]" />
              Recent lock activity ({audit.length})
              <span className="ml-auto text-xs text-[#6B7280]">{showAudit ? 'hide' : 'show'}</span>
            </CardTitle>
          </CardHeader>
          {showAudit && (
            <CardContent className="p-0">
              {audit.length === 0 ? (
                <div className="px-4 py-8 text-center text-[#6B7280] text-xs">No recent activity.</div>
              ) : (
                <table className="w-full text-xs border-collapse">
                  <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                    <tr>
                      <th className="px-3 py-2 text-left text-[#374151] uppercase tracking-wider">When</th>
                      <th className="px-3 py-2 text-left text-[#374151] uppercase tracking-wider">User</th>
                      <th className="px-3 py-2 text-left text-[#374151] uppercase tracking-wider">Entity</th>
                      <th className="px-3 py-2 text-left text-[#374151] uppercase tracking-wider">Period</th>
                      <th className="px-3 py-2 text-left text-[#374151] uppercase tracking-wider">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {audit.map(a => (
                      <tr key={a.id}>
                        <td className="px-3 py-2 text-[#6B7280] whitespace-nowrap">{formatDate(a.created_at)}</td>
                        <td className="px-3 py-2 text-[#374151]">{a.user || '—'}</td>
                        <td className="px-3 py-2 text-[#374151] font-mono">{a.company_code || '—'}</td>
                        <td className="px-3 py-2 text-[#374151] font-mono">{a.period_name || '—'}</td>
                        <td className="px-3 py-2 text-[#374151]">{a.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardContent>
          )}
        </Card>
      </div>

      <ConfirmDialog
        open={confirmBulk === 'sign'}
        onOpenChange={(o) => !o && setConfirmBulk(null)}
        title={`Sign ${roleLabel} lock on ${selected.size} periods`}
        description={`Add your ${roleLabel} signature to ${selected.size} selected periods. Periods with the OTHER party's signature already in place will flip to LOCKED.`}
        confirmLabel={`Sign ${roleLabel}`}
        variant="primary"
        loading={acting}
        onConfirm={() => handleBulk('sign')}
      />
      <ConfirmDialog
        open={confirmBulk === 'clear'}
        onOpenChange={(o) => !o && setConfirmBulk(null)}
        title={`Clear ${roleLabel} signature on ${selected.size} periods`}
        description={`Remove your ${roleLabel} signature from ${selected.size} selected periods. Locked periods will flip back to OPEN.`}
        confirmLabel={`Clear ${roleLabel}`}
        variant="warning"
        loading={acting}
        onConfirm={() => handleBulk('clear')}
      />
    </div>
  )
}
