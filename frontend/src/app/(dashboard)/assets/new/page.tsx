'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  createAsset, getAssetCategories, getCompanies, getToken,
} from '@/lib/api'
import type { AssetCategory, Company, CreateAssetInput } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Save } from 'lucide-react'
import { localYmd } from '@/lib/utils'

export default function NewAssetPage() {
  const router = useRouter()
  const [categories, setCategories] = useState<AssetCategory[]>([])
  const [companies, setCompanies] = useState<Company[]>([])
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [bootstrapped, setBootstrapped] = useState(false)

  const today = localYmd(new Date())

  const [form, setForm] = useState<CreateAssetInput>({
    tag_number: '',
    name: '',
    company: '',
    category: '',
    cost: '',
    salvage_value: '0',
    method: 'straight_line',
    useful_life_months: 60,
    purchase_date: today,
    in_service_date: today,
    location: '',
    custodian: '',
    opening_accumulated_depreciation: '0',
    notes: '',
    // Tax / VAT defaults — overridden by category selection
    vat_treatment: 'standard',
    purchase_vat_amount: '0',
    capital_allowance_method: 'reducing_balance',
    capital_allowance_rate: '25.00',
    tax_cost_cap: '',
  })

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    Promise.all([getAssetCategories(), getCompanies()])
      .then(([cats, cos]) => {
        setCategories(cats.results)
        setCompanies(cos.results)
        if (cats.results.length > 0) {
          setForm(f => ({
            ...f,
            category: f.category || cats.results[0].id,
            method: f.method || cats.results[0].default_method,
            useful_life_months: f.useful_life_months || cats.results[0].default_useful_life_months,
          }))
        }
        const def = cos.results.find(c => c.is_default) || cos.results[0]
        if (def) setForm(f => ({ ...f, company: f.company || def.id }))
        setBootstrapped(true)
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load form data'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function update<K extends keyof CreateAssetInput>(key: K, value: CreateAssetInput[K]) {
    setForm(prev => ({ ...prev, [key]: value }))
  }

  function onCategoryChange(id: string) {
    const cat = categories.find(c => c.id === id)
    setForm(prev => ({
      ...prev,
      category: id,
      method: cat?.default_method ?? prev.method,
      useful_life_months: cat?.default_useful_life_months ?? prev.useful_life_months,
    }))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const created = await createAsset(form)
      router.push(`/assets/${created.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create asset')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="New Fixed Asset"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Fixed Assets', href: '/assets' }, { label: 'New' }]}
        actions={
          <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.back()}>
            Back
          </Button>
        }
      />

      <div className="flex-1 p-6">
        <form onSubmit={handleSubmit} className="max-w-3xl space-y-4">
          {error && (
            <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-[#DC2626] flex-shrink-0" />
              <p className="text-[#DC2626] text-sm">{error}</p>
            </div>
          )}

          <Card>
            <CardHeader><CardTitle>Identity</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Tag number *">
                <input value={form.tag_number} onChange={e => update('tag_number', e.target.value)} required className={inputCls} placeholder="ADI-IT-0042" />
              </Field>
              <Field label="External reference (e.g. Odoo code)">
                <input value={form.external_ref || ''} onChange={e => update('external_ref', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Name *" className="md:col-span-2">
                <input value={form.name} onChange={e => update('name', e.target.value)} required className={inputCls} />
              </Field>
              <Field label="Serial number">
                <input value={form.serial_number || ''} onChange={e => update('serial_number', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Barcode">
                <input value={form.barcode || ''} onChange={e => update('barcode', e.target.value)} className={inputCls} />
              </Field>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Classification</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Company *">
                <select value={form.company} onChange={e => update('company', e.target.value)} required className={inputCls}>
                  {companies.map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
                </select>
              </Field>
              <Field label="Category *">
                <select value={form.category} onChange={e => onCategoryChange(e.target.value)} required className={inputCls}>
                  {categories.map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
                </select>
              </Field>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Cost & Depreciation</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Cost (BWP) *">
                <input type="number" step="0.01" min="0" value={form.cost} onChange={e => update('cost', e.target.value)} required className={inputCls} />
              </Field>
              <Field label="Salvage value (BWP)">
                <input type="number" step="0.01" min="0" value={form.salvage_value} onChange={e => update('salvage_value', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Method *">
                <select value={form.method} onChange={e => update('method', e.target.value as 'straight_line' | 'reducing_balance')} className={inputCls}>
                  <option value="straight_line">Straight-line</option>
                  <option value="reducing_balance">Reducing balance</option>
                </select>
              </Field>
              <Field label="Useful life (months) *">
                <input type="number" min="1" value={form.useful_life_months} onChange={e => update('useful_life_months', parseInt(e.target.value || '0', 10))} required className={inputCls} />
              </Field>
              <Field label="Purchase date *">
                <input type="date" value={form.purchase_date} onChange={e => update('purchase_date', e.target.value)} required className={inputCls} />
              </Field>
              <Field label="In-service date">
                <input type="date" value={form.in_service_date} onChange={e => update('in_service_date', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Opening accumulated depreciation (BWP)" className="md:col-span-2">
                <input type="number" step="0.01" min="0" value={form.opening_accumulated_depreciation} onChange={e => update('opening_accumulated_depreciation', e.target.value)} className={inputCls} placeholder="Only set if asset was already partially depreciated elsewhere (e.g. Odoo)" />
              </Field>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Location & Custody</CardTitle></CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Field label="Location">
                <input value={form.location || ''} onChange={e => update('location', e.target.value)} className={inputCls} placeholder="2F BAC Two — Boardroom" />
              </Field>
              <Field label="Custodian">
                <input value={form.custodian || ''} onChange={e => update('custodian', e.target.value)} className={inputCls} placeholder="Finance Team" />
              </Field>
              <Field label="Notes" className="md:col-span-2">
                <textarea value={form.notes || ''} onChange={e => update('notes', e.target.value)} className={inputCls + ' min-h-[80px]'} />
              </Field>
            </CardContent>
          </Card>

          <div className="flex justify-end gap-3">
            <Button type="button" variant="outline" onClick={() => router.back()} disabled={saving}>Cancel</Button>
            <Button type="submit" variant="accent" leftIcon={<Save className="w-3.5 h-3.5" />} disabled={saving || !bootstrapped}>
              {saving ? 'Saving...' : 'Create Asset'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}

const inputCls =
  'w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all'

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`block ${className || ''}`}>
      <span className="block text-xs font-medium text-[#374151] mb-1">{label}</span>
      {children}
    </label>
  )
}
