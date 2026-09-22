'use client'

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  getAsset, depreciateAsset, disposeAsset, getBankAccounts, getToken,
  transferAsset, getAssetEmployees,
} from '@/lib/api'
import type { AssetDetail, BankAccount, AssetEmployeeOption } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { ArrowLeft, AlertCircle, TrendingDown, Trash2, Boxes, Receipt, MapPin, User, ArrowLeftRight } from 'lucide-react'
import { localYmd } from '@/lib/utils'

function fmtBwp(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

const STATUS_STYLES: Record<string, string> = {
  active:       'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  disposed:     'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
  written_off:  'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
  transferred:  'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]',
}

export default function AssetDetailPage() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const id = params?.id as string

  const [asset, setAsset] = useState<AssetDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showDepreciate, setShowDepreciate] = useState(false)
  const [showDispose, setShowDispose] = useState(false)
  const [showTransfer, setShowTransfer] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setAsset(await getAsset(id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load asset')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Loading…" breadcrumbs={[{ label: 'Finance' }, { label: 'Fixed Assets', href: '/assets' }]} />
        <div className="flex-1 p-6">
          <div className="animate-pulse h-32 bg-[#F3F4F6] rounded-lg" />
        </div>
      </div>
    )
  }

  if (error || !asset) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Error" breadcrumbs={[{ label: 'Finance' }, { label: 'Fixed Assets', href: '/assets' }]} />
        <div className="flex-1 p-6">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error || 'Asset not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={`${asset.tag_number} — ${asset.name}`}
        breadcrumbs={[
          { label: 'Finance' },
          { label: 'Fixed Assets', href: '/assets' },
          { label: asset.tag_number },
        ]}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/assets')}>
              Back
            </Button>
            {asset.status === 'active' && (
              <>
                <Button variant="outline" size="sm" leftIcon={<ArrowLeftRight className="w-3.5 h-3.5" />} onClick={() => setShowTransfer(true)}>
                  Transfer / hand over
                </Button>
                <Button variant="outline" size="sm" leftIcon={<TrendingDown className="w-3.5 h-3.5" />} onClick={() => setShowDepreciate(true)}>
                  Depreciate one period
                </Button>
                <Button variant="outline" size="sm" leftIcon={<Trash2 className="w-3.5 h-3.5" />} onClick={() => setShowDispose(true)}>
                  Dispose
                </Button>
              </>
            )}
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {actionMsg && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 text-sm text-[#047857]">
            {actionMsg}
          </div>
        )}

        {/* KPI strip */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Kpi label="Cost" value={fmtBwp(asset.cost)} />
          <Kpi label="Accum. depreciation" value={fmtBwp(asset.accumulated_depreciation)} />
          <Kpi label="Net book value" value={fmtBwp(asset.net_book_value)} accent />
          <Kpi label="Status" value={<span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${STATUS_STYLES[asset.status] || STATUS_STYLES.active}`}>{asset.status_display}</span>} />
        </div>

        <Tabs defaultValue="details">
          <TabsList variant="underline">
            <TabsTrigger value="details">Details</TabsTrigger>
            <TabsTrigger value="depreciation">
              Depreciation ({asset.depreciation_entries.length})
            </TabsTrigger>
            <TabsTrigger value="disposal">
              Disposal {asset.disposal ? '(1)' : ''}
            </TabsTrigger>
            <TabsTrigger value="history">
              Hand-over history ({asset.assignments.length})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="details" className="mt-4">
            <Card>
              <CardContent className="p-6 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
                <Row icon={<Boxes className="w-4 h-4" />} label="Category" value={`${asset.category_code} — ${asset.category_name}`} />
                <Row icon={<Receipt className="w-4 h-4" />} label="Company" value={asset.company_code} />
                <Row label="External ref" value={asset.external_ref || '—'} />
                <Row label="Serial number" value={asset.serial_number || '—'} />
                <Row label="Method" value={asset.method_display} />
                <Row label="Useful life" value={`${asset.useful_life_months} months`} />
                <Row label="Salvage value" value={`BWP ${fmtBwp(asset.salvage_value)}`} />
                <Row label="Opening accum. depr." value={`BWP ${fmtBwp(asset.opening_accumulated_depreciation)}`} />
                <Row label="Purchase date" value={asset.purchase_date} />
                <Row label="In-service date" value={asset.in_service_date} />
                <Row label="Last depreciation" value={asset.last_depreciation_date || '—'} />
                <Row icon={<MapPin className="w-4 h-4" />} label="Location" value={asset.location || '—'} />
                <Row icon={<User className="w-4 h-4" />} label="Held by (staff)" value={asset.custodian_employee_name || asset.custodian || '—'} />
                {asset.description && <Row label="Description" value={asset.description} className="md:col-span-2" />}
                {asset.notes && <Row label="Notes" value={asset.notes} className="md:col-span-2" />}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="depreciation" className="mt-4">
            <Card>
              <CardHeader><CardTitle>Depreciation history</CardTitle></CardHeader>
              <CardContent className="p-0">
                {asset.depreciation_entries.length === 0 ? (
                  <p className="px-6 py-8 text-center text-sm text-[#6B7280]">No depreciation booked yet.</p>
                ) : (
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Period</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">End date</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Amount (BWP)</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">JE #</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {asset.depreciation_entries.map(e => (
                        <tr key={e.id} className={e.reversed_at ? 'opacity-50' : ''}>
                          <td className="px-4 py-2 text-[#111827] font-mono">{e.period_name}</td>
                          <td className="px-4 py-2 text-[#374151]">{e.period_end_date}</td>
                          <td className="px-4 py-2 text-right text-[#111827] tabular-nums">{fmtBwp(e.amount)}</td>
                          <td className="px-4 py-2 text-[#374151] font-mono text-xs">{e.journal_entry_number || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="disposal" className="mt-4">
            {asset.disposal ? (
              <Card>
                <CardHeader><CardTitle>Disposal</CardTitle></CardHeader>
                <CardContent className="p-6 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
                  <Row label="Type" value={asset.disposal.disposal_type_display} />
                  <Row label="Date" value={asset.disposal.disposal_date} />
                  <Row label="Proceeds" value={`BWP ${fmtBwp(asset.disposal.proceeds)}`} />
                  <Row label="Cost at disposal" value={`BWP ${fmtBwp(asset.disposal.cost_at_disposal)}`} />
                  <Row label="Accum. depr. at disposal" value={`BWP ${fmtBwp(asset.disposal.accumulated_depr_at_disposal)}`} />
                  <Row label="NBV at disposal" value={`BWP ${fmtBwp(asset.disposal.nbv_at_disposal)}`} />
                  <Row
                    label={parseFloat(asset.disposal.gain_loss) >= 0 ? 'Gain on disposal' : 'Loss on disposal'}
                    value={`BWP ${fmtBwp(Math.abs(parseFloat(asset.disposal.gain_loss)))}`}
                  />
                  <Row label="JE number" value={asset.disposal.journal_entry_number || '—'} />
                  {asset.disposal.notes && <Row label="Notes" value={asset.disposal.notes} className="md:col-span-2" />}
                </CardContent>
              </Card>
            ) : (
              <Card><CardContent className="p-8 text-center text-sm text-[#6B7280]">No disposal recorded.</CardContent></Card>
            )}
          </TabsContent>

          <TabsContent value="history" className="mt-4">
            <Card>
              <CardHeader><CardTitle>Hand-over history</CardTitle></CardHeader>
              <CardContent className="p-0">
                {asset.assignments.length === 0 ? (
                  <p className="px-6 py-8 text-center text-sm text-[#6B7280]">
                    No hand-overs recorded yet. Use “Transfer / hand over” to assign this asset to a person and place.
                  </p>
                ) : (
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Date</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">From</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">To</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Location</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Reason</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">By</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {asset.assignments.map(h => (
                        <tr key={h.id}>
                          <td className="px-4 py-2 text-[#374151] whitespace-nowrap">{h.transferred_at}</td>
                          <td className="px-4 py-2 text-[#6B7280]">{h.from_custodian || '—'}</td>
                          <td className="px-4 py-2 text-[#111827] font-medium">{h.to_custodian || '—'}</td>
                          <td className="px-4 py-2 text-[#374151]">{h.to_location || '—'}</td>
                          <td className="px-4 py-2 text-[#6B7280]">{h.reason || '—'}</td>
                          <td className="px-4 py-2 text-[#6B7280] text-xs">{h.transferred_by_name || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      {showDepreciate && (
        <DepreciateModal
          asset={asset}
          onClose={() => setShowDepreciate(false)}
          onSuccess={(msg) => {
            setShowDepreciate(false)
            setActionMsg(msg)
            load()
          }}
        />
      )}

      {showDispose && (
        <DisposeModal
          asset={asset}
          onClose={() => setShowDispose(false)}
          onSuccess={(msg) => {
            setShowDispose(false)
            setActionMsg(msg)
            load()
          }}
        />
      )}

      {showTransfer && (
        <TransferModal
          asset={asset}
          onClose={() => setShowTransfer(false)}
          onSuccess={(msg) => {
            setShowTransfer(false)
            setActionMsg(msg)
            load()
          }}
        />
      )}
    </div>
  )
}

function TransferModal({ asset, onClose, onSuccess }: {
  asset: AssetDetail
  onClose: () => void
  onSuccess: (msg: string) => void
}) {
  const today = localYmd(new Date())
  const [employees, setEmployees] = useState<AssetEmployeeOption[]>([])
  const [toEmployee, setToEmployee] = useState<string>('')
  const [toLocation, setToLocation] = useState<string>(asset.location || '')
  const [reason, setReason] = useState('')
  const [date, setDate] = useState(today)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    getAssetEmployees().then(setEmployees).catch(() => { /* non-fatal */ })
  }, [])

  async function go() {
    if (!toEmployee && !toLocation.trim()) {
      setErr('Choose a new holder and/or a new location.')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const updated = await transferAsset(asset.id, {
        to_employee: toEmployee || null,
        to_location: toLocation.trim(),
        reason: reason.trim(),
        transferred_at: date,
      })
      onSuccess(`Handed over to ${updated.custodian_employee_name || updated.custodian || 'new location'}.`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Transfer failed')
      setBusy(false)
    }
  }

  return (
    <Modal title="Transfer / hand over asset" onClose={onClose}>
      <p className="text-sm text-[#374151]">
        Hand <span className="font-medium">{asset.tag_number} — {asset.name}</span> to a new
        person and/or location. This records the move in the asset’s history. It does not affect
        depreciation, cost, or the owning company.
      </p>
      <label className="block">
        <span className="block text-xs font-medium text-[#374151] mb-1">New holder (staff)</span>
        <select value={toEmployee} onChange={e => setToEmployee(e.target.value)}
                className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
          <option value="">— keep current holder —</option>
          {employees.map(e => (
            <option key={e.id} value={e.id}>
              {e.full_name}{e.company_code ? ` (${e.company_code})` : ''}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="block text-xs font-medium text-[#374151] mb-1">New location</span>
        <input value={toLocation} onChange={e => setToLocation(e.target.value)}
               className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm"
               placeholder="e.g. Exco boardroom" />
      </label>
      <label className="block">
        <span className="block text-xs font-medium text-[#374151] mb-1">Date</span>
        <input type="date" value={date} onChange={e => setDate(e.target.value)}
               className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
      </label>
      <label className="block">
        <span className="block text-xs font-medium text-[#374151] mb-1">Reason (optional)</span>
        <input value={reason} onChange={e => setReason(e.target.value)}
               className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm"
               placeholder="e.g. New employee onboarding" />
      </label>
      {err && <p className="text-sm text-[#DC2626]">{err}</p>}
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="accent" onClick={go} disabled={busy}>{busy ? 'Transferring…' : 'Confirm hand-over'}</Button>
      </div>
    </Modal>
  )
}

function Kpi({ label, value, accent }: { label: string; value: React.ReactNode; accent?: boolean }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider">{label}</p>
        <p className={`mt-1 text-lg font-semibold ${accent ? 'text-[#F07F00]' : 'text-[#111827]'}`}>{value}</p>
      </CardContent>
    </Card>
  )
}

function Row({ icon, label, value, className }: { icon?: React.ReactNode; label: string; value: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider flex items-center gap-1.5">
        {icon}{label}
      </p>
      <p className="mt-0.5 text-[#111827]">{value}</p>
    </div>
  )
}

function DepreciateModal({ asset, onClose, onSuccess }: {
  asset: AssetDetail
  onClose: () => void
  onSuccess: (msg: string) => void
}) {
  const [period, setPeriod] = useState(() => {
    // Default to next month after last_depreciation_date or in-service date
    const ref = asset.last_depreciation_date || asset.in_service_date
    const d = new Date(ref)
    d.setMonth(d.getMonth() + 1)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function go() {
    setBusy(true)
    setErr(null)
    try {
      const entry = await depreciateAsset(asset.id, period)
      onSuccess(`Posted depreciation of BWP ${entry.amount} for ${entry.period_name}.`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to depreciate')
      setBusy(false)
    }
  }

  return (
    <Modal title="Depreciate one period" onClose={onClose}>
      <p className="text-sm text-[#374151]">
        Post one month of depreciation against this asset. The system will skip if the period
        is already booked, the asset is fully depreciated, or the period is before the in-service date.
      </p>
      <label className="block">
        <span className="block text-xs font-medium text-[#374151] mb-1">Fiscal period (YYYY-MM)</span>
        <input value={period} onChange={e => setPeriod(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" placeholder="2026-04" />
      </label>
      {err && <p className="text-sm text-[#DC2626]">{err}</p>}
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="accent" onClick={go} disabled={busy || !period}>{busy ? 'Posting…' : 'Post depreciation'}</Button>
      </div>
    </Modal>
  )
}

function DisposeModal({ asset, onClose, onSuccess }: {
  asset: AssetDetail
  onClose: () => void
  onSuccess: (msg: string) => void
}) {
  const today = localYmd(new Date())
  const [type, setType] = useState<'sale' | 'write_off' | 'transfer' | 'trade_in'>('sale')
  const [date, setDate] = useState(today)
  const [proceeds, setProceeds] = useState('0')
  const [bankAccount, setBankAccount] = useState<string>('')
  const [notes, setNotes] = useState('')
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    getBankAccounts()
      .then(res => setBankAccounts(res.results))
      .catch(() => { /* non-fatal */ })
  }, [])

  async function go() {
    setBusy(true)
    setErr(null)
    try {
      const result = await disposeAsset(asset.id, {
        disposal_type: type,
        disposal_date: date,
        proceeds,
        bank_account: type === 'sale' && bankAccount ? bankAccount : null,
        notes,
      })
      const gl = parseFloat(result.gain_loss)
      onSuccess(
        gl >= 0
          ? `Disposed ${asset.tag_number} — gain BWP ${result.gain_loss}.`
          : `Disposed ${asset.tag_number} — loss BWP ${Math.abs(gl).toFixed(2)}.`,
      )
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to dispose')
      setBusy(false)
    }
  }

  return (
    <Modal title="Dispose asset" onClose={onClose}>
      <p className="text-sm text-[#374151]">
        Posts a balanced JE: clears cost and accumulated depreciation, books any
        proceeds, and recognises the gain/loss to P&L.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="block text-xs font-medium text-[#374151] mb-1">Type</span>
          <select value={type} onChange={e => setType(e.target.value as 'sale')} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
            <option value="sale">Sale</option>
            <option value="write_off">Write-off</option>
            <option value="transfer">Transfer</option>
            <option value="trade_in">Trade-in</option>
          </select>
        </label>
        <label className="block">
          <span className="block text-xs font-medium text-[#374151] mb-1">Date</span>
          <input type="date" value={date} onChange={e => setDate(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
        </label>
        <label className="block">
          <span className="block text-xs font-medium text-[#374151] mb-1">Proceeds (BWP)</span>
          <input type="number" step="0.01" min="0" value={proceeds} onChange={e => setProceeds(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
        </label>
        {type === 'sale' && (
          <label className="block">
            <span className="block text-xs font-medium text-[#374151] mb-1">Bank account</span>
            <select value={bankAccount} onChange={e => setBankAccount(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
              <option value="">— Select —</option>
              {bankAccounts.map(b => <option key={b.id} value={b.id}>{b.gl_account_code} — {b.bank_name}</option>)}
            </select>
          </label>
        )}
      </div>
      <label className="block">
        <span className="block text-xs font-medium text-[#374151] mb-1">Notes</span>
        <textarea value={notes} onChange={e => setNotes(e.target.value)} className="w-full bg-white border border-[#D1D5DB] rounded-md p-3 text-sm min-h-[60px]" />
      </label>
      {err && <p className="text-sm text-[#DC2626]">{err}</p>}
      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="accent" onClick={go} disabled={busy}>{busy ? 'Posting…' : 'Confirm disposal'}</Button>
      </div>
    </Modal>
  )
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6 space-y-4" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-[#111827]">{title}</h2>
        {children}
      </div>
    </div>
  )
}
