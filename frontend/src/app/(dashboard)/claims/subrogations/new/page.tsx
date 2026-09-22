'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createSubrogation, getToken } from '@/lib/api'
import type { Subrogation, SubrogationStatus } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Save } from 'lucide-react'
import { localYmd } from '@/lib/utils'

const inputCls = 'w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]'

export default function NewSubrogationPage() {
  const router = useRouter()
  const today = localYmd(new Date())
  // Bug fix 2026-05-20: same 0-stuck-on-fallback pattern as salvages/new.
  // Start empty so users can type a real value; coerce to '0' at save.
  const [form, setForm] = useState<Partial<Subrogation>>({
    claim_reference: '', incident_date: today, third_party_name: '', third_party_insurer: '',
    claim_paid_amount: '', expected_recovery: '', actual_recovery: '',
    status: 'pending' as SubrogationStatus, notes: '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!getToken()) { router.replace('/login'); return null }

  function set<K extends keyof Subrogation>(k: K, v: Subrogation[K]) {
    setForm(prev => ({ ...prev, [k]: v }))
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      const payload = { ...form }
      // Coerce empty money fields to '0' at the boundary.
      if (!payload.claim_paid_amount) payload.claim_paid_amount = '0'
      if (!payload.expected_recovery) payload.expected_recovery = '0'
      if (!payload.actual_recovery)   payload.actual_recovery   = '0'
      const created = await createSubrogation(payload)
      router.push(`/claims/subrogations/${created.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="New Subrogation"
        breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Subrogations', href: '/claims/subrogations' }, { label: 'New' }]}
        actions={<Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.back()}>Back</Button>} />
      <div className="flex-1 p-6 max-w-3xl space-y-4">
        {error && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p></div>}
        <Card><CardHeader><CardTitle>Subrogation details</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="Claim reference *"><input value={form.claim_reference || ''} onChange={e => set('claim_reference', e.target.value)} className={inputCls} placeholder="CLM-2026-001234" /></Field>
            <Field label="Incident date"><input type="date" value={form.incident_date || ''} onChange={e => set('incident_date', e.target.value)} className={inputCls} /></Field>
            <Field label="Third party name *"><input value={form.third_party_name || ''} onChange={e => set('third_party_name', e.target.value)} className={inputCls} /></Field>
            <Field label="Third party insurer"><input value={form.third_party_insurer || ''} onChange={e => set('third_party_insurer', e.target.value)} className={inputCls} /></Field>
            <Field label="Claim paid (BWP) *"><input type="number" step="0.01" min="0" inputMode="decimal" value={form.claim_paid_amount ?? ''} onChange={e => set('claim_paid_amount', e.target.value)} className={inputCls} placeholder="0.00" /></Field>
            <Field label="Expected recovery (BWP)"><input type="number" step="0.01" min="0" inputMode="decimal" value={form.expected_recovery ?? ''} onChange={e => set('expected_recovery', e.target.value)} className={inputCls} placeholder="0.00" /></Field>
            <Field label="Actual recovery (BWP)"><input type="number" step="0.01" min="0" inputMode="decimal" value={form.actual_recovery ?? ''} onChange={e => set('actual_recovery', e.target.value)} className={inputCls} placeholder="0.00" /></Field>
            <Field label="Status"><select value={form.status || 'pending'} onChange={e => set('status', e.target.value as SubrogationStatus)} className={inputCls}>
              <option value="pending">Pending</option><option value="partial">Partial</option><option value="fully_recovered">Fully recovered</option>
              <option value="in_litigation">In litigation</option><option value="written_off">Written off</option>
            </select></Field>
            <Field label="Notes" className="md:col-span-2"><textarea value={form.notes || ''} onChange={e => set('notes', e.target.value)} className={inputCls + ' min-h-[80px]'} /></Field>
          </CardContent></Card>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => router.back()} disabled={busy}>Cancel</Button>
          <Button variant="accent" leftIcon={<Save className="w-3.5 h-3.5" />} onClick={save} disabled={busy || !form.claim_reference || !form.third_party_name}>
            {busy ? 'Saving…' : 'Create'}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return <label className={`block ${className || ''}`}><span className="block text-xs font-medium text-[#374151] mb-1">{label}</span>{children}</label>
}
