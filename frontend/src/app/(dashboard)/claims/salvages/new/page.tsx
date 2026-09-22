'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createSalvage, getToken } from '@/lib/api'
import type { Salvage, SalvageStatus } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Save } from 'lucide-react'
import { localYmd } from '@/lib/utils'

const inputCls = 'w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]'

export default function NewSalvagePage() {
  const router = useRouter()
  const today = localYmd(new Date())
  // CFO + Bharath bug 2026-05-20: estimated_value / sale_proceeds shipped
  // with '0' default + `|| '0'` fallback on every keystroke, so users
  // couldn't erase the zero to type a real value. Start empty; coerce
  // to '0' only at save time.
  const [form, setForm] = useState<Partial<Salvage>>({
    claim_reference: '', incident_date: today, asset_description: '',
    estimated_value: '', sale_proceeds: '', sale_date: '', buyer_name: '',
    status: 'pending' as SalvageStatus, notes: '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!getToken()) { router.replace('/login'); return null }

  function set<K extends keyof Salvage>(k: K, v: Salvage[K]) {
    setForm(prev => ({ ...prev, [k]: v }))
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      const payload = { ...form }
      if (!payload.sale_date) delete (payload as { sale_date?: string }).sale_date
      // Coerce empty money fields to '0' at the boundary — the backend
      // expects a number, but the input box stays user-erasable.
      if (!payload.estimated_value) payload.estimated_value = '0'
      if (!payload.sale_proceeds)   payload.sale_proceeds   = '0'
      const created = await createSalvage(payload as Partial<Salvage>)
      router.push(`/claims/salvages/${created.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="New Salvage"
        breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Salvages', href: '/claims/salvages' }, { label: 'New' }]}
        actions={<Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.back()}>Back</Button>} />
      <div className="flex-1 p-6 max-w-3xl space-y-4">
        {error && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p></div>}
        <Card><CardHeader><CardTitle>Salvage details</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="Claim reference *"><input value={form.claim_reference || ''} onChange={e => set('claim_reference', e.target.value)} className={inputCls} placeholder="CLM-2026-001234" /></Field>
            <Field label="Incident date"><input type="date" value={form.incident_date || ''} onChange={e => set('incident_date', e.target.value)} className={inputCls} /></Field>
            <Field label="Asset description *" className="md:col-span-2"><input value={form.asset_description || ''} onChange={e => set('asset_description', e.target.value)} className={inputCls} placeholder="2018 Toyota Hilux 2.4D, reg B-123-ABC" /></Field>
            <Field label="Estimated value (BWP)"><input type="number" step="0.01" min="0" inputMode="decimal" value={form.estimated_value ?? ''} onChange={e => set('estimated_value', e.target.value)} className={inputCls} placeholder="0.00" /></Field>
            <Field label="Sale proceeds (BWP)"><input type="number" step="0.01" min="0" inputMode="decimal" value={form.sale_proceeds ?? ''} onChange={e => set('sale_proceeds', e.target.value)} className={inputCls} placeholder="0.00" /></Field>
            <Field label="Sale date"><input type="date" value={form.sale_date || ''} onChange={e => set('sale_date', e.target.value)} className={inputCls} /></Field>
            <Field label="Buyer name"><input value={form.buyer_name || ''} onChange={e => set('buyer_name', e.target.value)} className={inputCls} /></Field>
            <Field label="Status"><select value={form.status || 'pending'} onChange={e => set('status', e.target.value as SalvageStatus)} className={inputCls}>
              <option value="pending">Pending</option><option value="for_sale">For sale</option>
              <option value="sold">Sold</option><option value="scrapped">Scrapped</option><option value="retained">Retained</option>
            </select></Field>
            <Field label="Notes" className="md:col-span-2"><textarea value={form.notes || ''} onChange={e => set('notes', e.target.value)} className={inputCls + ' min-h-[80px]'} /></Field>
          </CardContent></Card>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => router.back()} disabled={busy}>Cancel</Button>
          <Button variant="accent" leftIcon={<Save className="w-3.5 h-3.5" />} onClick={save} disabled={busy || !form.claim_reference || !form.asset_description}>
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
