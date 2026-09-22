'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getCapitalCheck, getCapitalSnapshots, takeCapitalSnapshot, approveCapitalSnapshot,
  getCapitalParameters, updateCapitalParameter, getMe, getToken,
} from '@/lib/api'
import type { CapitalCheckResult, CapitalSnapshot, CapitalParameter, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, AlertTriangle, CheckCircle, ShieldCheck, ShieldAlert, Camera,
  Edit2, Save, ArrowLeft, RefreshCw, Lock,
} from 'lucide-react'
import { formatDate, localYmd } from '@/lib/utils'

function fmt(v: string | number | null | undefined): string {
  // null used to fall through to Intl and render "0.00": Number.isNaN(null) is
  // false. The backend now returns null for a withheld ratio, so a regulator-
  // facing screen would have shown "CAR 0.00%" — a different invented number in
  // place of the 612% (Fable 2026-08-10).
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

const STATUS: Record<string, { label: string; cls: string; icon: React.ComponentType<any> }> = {
  compliant: { label: 'COMPLIANT',  cls: 'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]', icon: ShieldCheck   },
  margin:    { label: 'MARGIN',     cls: 'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]', icon: AlertTriangle },
  breach:    { label: 'BREACH',     cls: 'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]', icon: ShieldAlert   },
  draft:     { label: 'DRAFT',      cls: 'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]', icon: Camera        },
}

export default function RegulatoryCapitalPage() {
  const router = useRouter()
  const today = localYmd(new Date())
  const [me, setMe] = useState<UserProfile | null>(null)
  const [asOf, setAsOf] = useState(today)
  const [check, setCheck] = useState<CapitalCheckResult | null>(null)
  const [snapshots, setSnapshots] = useState<CapitalSnapshot[]>([])
  const [parameters, setParameters] = useState<CapitalParameter[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [editingParam, setEditingParam] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [c, s, p, m] = await Promise.all([
        // Was `.catch(() => null)`. A swallowed failure here renders an empty
        // shell with no message, which is exactly how nobody noticed the page
        // had never been connected (2026-08-10).
        getCapitalCheck(asOf).catch((e) => {
          setError(e instanceof Error ? e.message : 'Could not load the capital check')
          return null
        }),
        getCapitalSnapshots().catch(() => ({ results: [] as CapitalSnapshot[] })),
        getCapitalParameters().catch(() => ({ results: [] as CapitalParameter[] })),
        getMe().catch(() => null),
      ])
      setCheck(c)
      setSnapshots((s as { results: CapitalSnapshot[] }).results)
      setParameters((p as { results: CapitalParameter[] }).results)
      setMe(m)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [asOf])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function flash(msg: string) { setSuccess(msg); setTimeout(() => setSuccess(null), 4000) }

  async function takeSnapshot() {
    setBusy(true); setError(null)
    try {
      await takeCapitalSnapshot(asOf, `Snapshot at ${asOf}`)
      flash('Snapshot saved'); load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Snapshot failed') }
    finally { setBusy(false) }
  }

  async function approveSnap(id: string) {
    setBusy(true); setError(null)
    try {
      await approveCapitalSnapshot(id)
      flash('Snapshot approved'); load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Approve failed') }
    finally { setBusy(false) }
  }

  async function saveParam(p: CapitalParameter) {
    setBusy(true); setError(null)
    try {
      await updateCapitalParameter(p.id, { value: editValue })
      setEditingParam(null); flash(`Updated ${p.code}`); load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(false) }
  }

  const canApprove = !!me?.can_approve_journal_entries
  const status = check ? (STATUS[check.status] || STATUS.draft) : STATUS.draft
  const StatusIcon = status.icon

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Regulatory Capital (NBFIRA)"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Regulatory Capital' }]}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/reports')}>
              Reports
            </Button>
            <Button variant="outline" size="sm" leftIcon={<RefreshCw className="w-3.5 h-3.5" />} onClick={load} disabled={busy || loading}>
              Refresh
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}
        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#047857]" /><p className="text-[#047857] text-sm">{success}</p>
          </div>
        )}

        {/* ── Live status + filters ─────────────────────────────── */}
        <Card>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
            <label className="block">
              <span className="block text-xs font-medium text-[#374151] mb-1">As-of date</span>
              <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)}
                     className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
            </label>
            <Button variant="accent" leftIcon={<RefreshCw className="w-3.5 h-3.5" />} onClick={load} disabled={busy || loading}>
              {loading ? 'Loading…' : 'Recompute'}
            </Button>
            <Button variant="outline" leftIcon={<Camera className="w-3.5 h-3.5" />} onClick={takeSnapshot}
                    disabled={!canApprove || busy}
                    title={canApprove ? undefined : 'Approver title required'}>
              Save snapshot
            </Button>
            <div className="text-xs text-[#6B7280]">
              Snapshots are immutable once approved by the CFO and form your NBFIRA audit trail.
            </div>
          </CardContent>
        </Card>

        {/* ── Big status card ───────────────────────────────────── */}
        {check && (
          <Card>
            <CardContent className="p-6">
              {check.status === 'under_revision' && (
                <div className="mb-4 rounded-lg border-2 border-amber-500 bg-amber-50 p-4">
                  <p className="text-base font-bold text-amber-900">
                    Under revision — not for regulatory or board use
                  </p>
                  <p className="mt-1 text-sm text-amber-900">{check.status_note}</p>
                  {Array.isArray(check.unverified_parameters)
                    && check.unverified_parameters.length > 0 && (
                    <p className="mt-2 text-xs text-amber-800">
                      Waiting on an effective date for:{' '}
                      {check.unverified_parameters.join(', ')}
                    </p>
                  )}
                </div>
              )}
              <div className={`rounded-lg border-2 p-4 flex items-start gap-4 ${status.cls}`}>
                <StatusIcon className="w-10 h-10 flex-shrink-0" />
                <div className="flex-1">
                  <div className="flex items-baseline gap-3 flex-wrap">
                    <p className="text-2xl font-bold">CAR {fmt(check.car_percent)}%</p>
                    <span className="text-base font-semibold uppercase tracking-wide">{status.label}</span>
                  </div>
                  <p className="text-sm mt-1">
                    Available BWP {fmt(check.available_capital)} · Required BWP {fmt(check.required_capital)}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                <Stat label="Total equity" value={`BWP ${fmt(check.components.total_equity)}`} />
                <Stat label="Intangibles deducted" value={`BWP ${fmt(check.components.intangibles)}`} />
                <Stat label="Reinsurers' share excluded"
                      value={`BWP ${fmt(check.components.reinsurance_share_excluded)}`} />
                <Stat label="Opex basis (next year est.)"
                      value={`BWP ${fmt(check.components.opex_basis_amount)}`} />
                <Stat label="Minimum floor" value={`BWP ${fmt(check.components.minimum_floor)}`} />
                <Stat label={`Opex-based requirement (${Number(check.components.opex_factor ?? 0.25) * 100}%)`}
                      value={`BWP ${fmt(check.components.opex_required)}`} />
                <Stat label="Binding constraint" value={check.components.binding_constraint} accent />
              </div>
            </CardContent>
          </Card>
        )}

        {/* ── Snapshots list ────────────────────────────────────── */}
        <Card>
          <CardHeader><CardTitle>Saved snapshots ({snapshots.length})</CardTitle></CardHeader>
          <CardContent className="p-0">
            {snapshots.length === 0 ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">
                No snapshots yet. Click <strong>Save snapshot</strong> above to capture a permanent record.
              </p>
            ) : (
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase">As-of</th>
                    <th className="px-4 py-2 text-right text-xs font-semibold uppercase">Available</th>
                    <th className="px-4 py-2 text-right text-xs font-semibold uppercase">Required</th>
                    <th className="px-4 py-2 text-right text-xs font-semibold uppercase">CAR</th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Status</th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Prepared / approved</th>
                    <th className="px-4 py-2"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {snapshots.map(s => {
                    const meta = STATUS[s.status] || STATUS.draft
                    // A snapshot computed on a withdrawn method is not a
                    // status, it is a void record. Showing 612% with a green
                    // COMPLIANT badge and a live Approve button — directly
                    // under the banner saying the figure is not for regulatory
                    // use — invited exactly the signature the banner warns off.
                    const withdrawn = s.method_current === false
                    const canApproveThis = canApprove
                      && !withdrawn
                      && !s.approved_by_username
                      && me?.username !== s.prepared_by_username
                    return (
                      <tr key={s.id} className={withdrawn ? 'bg-[#FFFBEB]' : undefined}>
                        <td className="px-4 py-2 font-mono">{formatDate(s.as_of_date)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmt(s.available_capital)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmt(s.required_capital)}</td>
                        <td className={`px-4 py-2 text-right tabular-nums ${withdrawn ? 'line-through text-[#9CA3AF]' : ''}`}>
                          {fmt(s.car_pct)}%
                        </td>
                        <td className="px-4 py-2">
                          {withdrawn ? (
                            <span className="inline-block px-2 py-0.5 rounded text-xs border bg-amber-50 text-amber-900 border-amber-400"
                                  title={s.method_note}>
                              WITHDRAWN
                            </span>
                          ) : (
                            <span className={`inline-block px-2 py-0.5 rounded text-xs border ${meta.cls}`}>{meta.label}</span>
                          )}
                        </td>
                        <td className="px-4 py-2 text-xs text-[#374151]">
                          By {s.prepared_by_username || '—'}{s.approved_by_username && <> · ✓ {s.approved_by_username}</>}
                        </td>
                        <td className="px-4 py-2 text-right">
                          {!s.approved_by_username && (
                            <Button size="sm" variant="accent" onClick={() => approveSnap(s.id)}
                                    disabled={!canApproveThis || busy}
                                    title={withdrawn ? s.method_note : !canApprove ? 'Approver title required' : me?.username === s.prepared_by_username ? 'Cannot approve your own snapshot (SoD)' : undefined}>
                              Approve
                            </Button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {/* ── Parameters editor ─────────────────────────────────── */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lock className="w-4 h-4 text-[#F07F00]" />
              Capital-adequacy parameters {!canApprove && <span className="text-xs font-normal text-[#9CA3AF]">(read-only — approver only)</span>}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm border-collapse">
              <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Code</th>
                  <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Label</th>
                  <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Value</th>
                  <th className="px-4 py-2 text-left text-xs font-semibold uppercase">Notes</th>
                  <th className="px-4 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E7EB] bg-white">
                {parameters.map(p => (
                  <tr key={p.id}>
                    <td className="px-4 py-2 font-mono text-xs">{p.code}</td>
                    <td className="px-4 py-2 text-[#374151]">{p.label}</td>
                    <td className="px-4 py-2 font-mono">
                      {editingParam === p.id ? (
                        <input value={editValue} onChange={e => setEditValue(e.target.value)}
                               className="h-8 bg-white border border-[#D1D5DB] rounded-md px-2 text-sm font-mono w-full max-w-xs" />
                      ) : p.value}
                    </td>
                    <td className="px-4 py-2 text-xs text-[#6B7280] max-w-md">{p.notes}</td>
                    <td className="px-4 py-2 text-right">
                      {editingParam === p.id ? (
                        <div className="flex gap-1 justify-end">
                          <Button size="sm" variant="ghost" onClick={() => setEditingParam(null)} disabled={busy}>Cancel</Button>
                          <Button size="sm" variant="accent" leftIcon={<Save className="w-3.5 h-3.5" />}
                                  onClick={() => saveParam(p)} disabled={busy}>Save</Button>
                        </div>
                      ) : canApprove ? (
                        <Button size="sm" variant="ghost" leftIcon={<Edit2 className="w-3.5 h-3.5" />}
                                onClick={() => { setEditingParam(p.id); setEditValue(p.value) }}>Edit</Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
          <CardContent className="border-t border-[#E5E7EB] p-3 text-xs text-[#92400E] bg-[#FFFBEB] flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <p>The seeded values are <strong>placeholders</strong>. The CFO must verify them against the current NBFIRA Insurance Industry Regulations before relying on this for any submission. Edit values above when the regulations change.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <Card>
      <CardContent className="p-3">
        <p className="text-xs uppercase tracking-wider text-[#6B7280]">{label}</p>
        <p className={`text-base font-semibold mt-0.5 ${accent ? 'text-[#F07F00]' : 'text-[#111827]'}`}>{value}</p>
      </CardContent>
    </Card>
  )
}
