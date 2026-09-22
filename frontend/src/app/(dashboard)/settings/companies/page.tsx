'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getCompanies, createCompany, updateCompany, getCurrencies, getToken,
} from '@/lib/api'
import type { Company, Currency } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { cn } from '@/lib/utils'
import {
  Plus, Pencil, Star, AlertCircle, CheckCircle, X, Building2,
} from 'lucide-react'

export default function CompaniesPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { reload: reloadCompanies } = useCompany()

  const [companies, setCompanies] = useState<Company[]>([])
  const [currencies, setCurrencies] = useState<Currency[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Modal state
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Company | null>(null)
  const [form, setForm] = useState({
    code: '',
    name: '',
    legal_name: '',
    registration_number: '',
    tax_id: '',
    country: 'BW',
    base_currency: 'BWP',
    address: '',
    is_default: false,
    is_active: true,
  })
  const [saving, setSaving] = useState(false)
  const [formErr, setFormErr] = useState<string | null>(null)

  const load = async () => {
    setLoading(true); setError(null)
    try {
      const [cr, cu] = await Promise.all([
        getCompanies(),
        getCurrencies(),
      ])
      setCompanies(cr.results)
      setCurrencies(cu.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load companies')
    } finally { setLoading(false) }
  }

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openNew = () => {
    setEditing(null)
    setForm({
      code: '', name: '', legal_name: '', registration_number: '',
      tax_id: '', country: 'BW', base_currency: 'BWP', address: '',
      is_default: false, is_active: true,
    })
    setFormErr(null)
    setModalOpen(true)
  }

  const openEdit = (c: Company) => {
    setEditing(c)
    setForm({
      code: c.code,
      name: c.name,
      legal_name: c.legal_name || '',
      registration_number: c.registration_number || '',
      tax_id: c.tax_id || '',
      country: c.country,
      base_currency: c.base_currency,
      address: c.address || '',
      is_default: c.is_default,
      is_active: c.is_active,
    })
    setFormErr(null)
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    setFormErr(null)
    if (!form.code.trim() || !form.name.trim()) {
      setFormErr('Code and name are required'); return
    }
    setSaving(true)
    try {
      const payload = {
        code: form.code.trim().toUpperCase(),
        name: form.name.trim(),
        legal_name: form.legal_name.trim() || undefined,
        registration_number: form.registration_number.trim() || undefined,
        tax_id: form.tax_id.trim() || undefined,
        country: form.country.trim().toUpperCase() || 'BW',
        base_currency: form.base_currency,
        address: form.address.trim() || undefined,
        is_default: form.is_default,
      }
      if (editing) {
        await updateCompany(editing.id, payload)
        setSuccess(`Updated ${payload.code}`)
      } else {
        await createCompany(payload)
        setSuccess(`Created ${payload.code}`)
      }
      setTimeout(() => setSuccess(null), 3500)
      setModalOpen(false)
      await load()
      await reloadCompanies()  // refresh the global picker
    } catch (err) {
      setFormErr(err instanceof Error ? err.message : 'Failed to save company')
    } finally { setSaving(false) }
  }

  const makeDefault = async (c: Company) => {
    setError(null)
    try {
      await updateCompany(c.id, { is_default: true })
      setSuccess(`${c.code} set as default`)
      setTimeout(() => setSuccess(null), 3500)
      await load()
      await reloadCompanies()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set default')
    }
  }

  const toggleActive = async (c: Company) => {
    try {
      await updateCompany(c.id, { is_active: !c.is_active } as any)
      await load()
      await reloadCompanies()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle')
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Companies"
        breadcrumbs={[{ label: 'Settings', href: '/settings' }, { label: 'Companies' }]}
        actions={
          <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={openNew}>
            New Company
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <div className="max-w-5xl mx-auto w-full">
          <div className="flex items-start gap-3 mb-5">
            <Building2 className="w-5 h-5 mt-0.5" style={{ color: theme.orange }} />
            <div>
              <h1 className="text-[20px] font-bold" style={{ color: theme.navy }}>Subsidiaries</h1>
              <p className="text-sm mt-1" style={{ color: theme.t2 }}>
                Each invoice, payment, and journal entry belongs to one company.
                Reports can be filtered by company using the picker in the top bar.
              </p>
            </div>
          </div>

          {success && (
            <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 mb-3 flex items-center gap-2">
              <CheckCircle className="w-4 h-4 text-[#059669]" />
              <p className="text-[#059669] text-sm">{success}</p>
            </div>
          )}
          {error && (
            <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 mb-3 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-[#DC2626]" />
              <p className="text-[#DC2626] text-sm">{error}</p>
            </div>
          )}

          <Card>
            <CardContent className="p-0">
              {loading ? (
                <LoadingTable rows={4} cols={6} />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <tr>
                        <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Code</th>
                        <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Name</th>
                        <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Legal Name</th>
                        <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Country</th>
                        <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Base Currency</th>
                        <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Default</th>
                        <th className="px-3 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                        <th className="px-3 py-3"></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {companies.length === 0 ? (
                        <tr>
                          <td colSpan={8} className="px-4 py-12 text-center">
                            <Building2 className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                            <p className="text-[#6B7280] text-sm font-medium">No companies yet</p>
                          </td>
                        </tr>
                      ) : (
                        companies.map((c) => (
                          <tr key={c.id} className={cn(!c.is_active && 'opacity-60')}>
                            <td className="px-3 py-3 font-mono text-xs font-semibold text-[#CC6C00] whitespace-nowrap">{c.code}</td>
                            <td className="px-3 py-3 text-[#111827] font-medium">{c.name}</td>
                            <td className="px-3 py-3 text-[#6B7280] text-xs">{c.legal_name || '—'}</td>
                            <td className="px-3 py-3 text-[#6B7280] text-xs">{c.country}</td>
                            <td className="px-3 py-3 text-[#374151] text-xs font-mono">{c.base_currency}</td>
                            <td className="px-3 py-3">
                              {c.is_default ? (
                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-[#FFF7ED] text-[#CC6C00]">
                                  <Star className="w-3 h-3 fill-current" />Default
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => makeDefault(c)}
                                  className="text-[11px] text-[#6B7280] hover:text-[#CC6C00] underline"
                                >
                                  Set default
                                </button>
                              )}
                            </td>
                            <td className="px-3 py-3">
                              <button
                                onClick={() => toggleActive(c)}
                                className={cn(
                                  'inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium',
                                  c.is_active ? 'bg-[#ECFDF5] text-[#059669]' : 'bg-[#F3F4F6] text-[#6B7280]'
                                )}
                              >
                                {c.is_active ? 'Active' : 'Inactive'}
                              </button>
                            </td>
                            <td className="px-3 py-3 text-right">
                              <button
                                onClick={() => openEdit(c)}
                                className="p-1.5 rounded hover:bg-[#F3F4F6] text-[#6B7280] hover:text-[#0B0B3B]"
                                aria-label={`Edit ${c.code}`}
                              >
                                <Pencil className="w-3.5 h-3.5" />
                              </button>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Edit / new modal */}
      {modalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => !saving && setModalOpen(false)}
        >
          <div
            className="bg-white rounded-xl shadow-xl w-full max-w-lg p-5 mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-[#0B0B3B]">
                {editing ? `Edit ${editing.code}` : 'New Company'}
              </h2>
              <button onClick={() => !saving && setModalOpen(false)} className="text-[#9CA3AF] hover:text-[#374151]">
                <X className="w-4 h-4" />
              </button>
            </div>
            {formErr && (
              <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-md p-2.5 mb-3 text-xs text-[#DC2626]">
                {formErr}
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">Code <span className="text-[#DC2626]">*</span></label>
                <input
                  type="text"
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                  placeholder="ADIZ"
                  maxLength={10}
                  disabled={!!editing}
                  className="w-full text-sm font-mono uppercase rounded-md px-3 py-2 border border-[#E5E7EB] disabled:bg-[#F9FAFB] disabled:text-[#9CA3AF]"
                />
                <p className="text-[10px] text-[#9CA3AF] mt-0.5">Short code, all caps. Cannot be changed after creation.</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">Country</label>
                <input
                  type="text"
                  value={form.country}
                  onChange={(e) => setForm({ ...form, country: e.target.value })}
                  placeholder="BW"
                  maxLength={2}
                  className="w-full text-sm font-mono uppercase rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
                <p className="text-[10px] text-[#9CA3AF] mt-0.5">2-letter ISO code (BW, ZM, ZA…)</p>
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-[#374151] mb-1">Name <span className="text-[#DC2626]">*</span></label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Alpha Direct Insurance Zambia"
                  className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-[#374151] mb-1">Legal Name</label>
                <input
                  type="text"
                  value={form.legal_name}
                  onChange={(e) => setForm({ ...form, legal_name: e.target.value })}
                  placeholder="Alpha Direct Insurance Zambia Ltd"
                  className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">Registration Number</label>
                <input
                  type="text"
                  value={form.registration_number}
                  onChange={(e) => setForm({ ...form, registration_number: e.target.value })}
                  className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">Tax ID</label>
                <input
                  type="text"
                  value={form.tax_id}
                  onChange={(e) => setForm({ ...form, tax_id: e.target.value })}
                  className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-[#374151] mb-1">Base Currency</label>
                <select
                  value={form.base_currency}
                  onChange={(e) => setForm({ ...form, base_currency: e.target.value })}
                  className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB] bg-white"
                >
                  {currencies.map((cur) => (
                    <option key={cur.code} value={cur.code}>{cur.code} — {cur.name}</option>
                  ))}
                </select>
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-[#374151] mb-1">Address</label>
                <textarea
                  value={form.address}
                  onChange={(e) => setForm({ ...form, address: e.target.value })}
                  rows={2}
                  className="w-full text-sm rounded-md px-3 py-2 border border-[#E5E7EB]"
                />
              </div>
              <div className="col-span-2 flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_default"
                  checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                  className="rounded border-[#D1D5DB]"
                />
                <label htmlFor="is_default" className="text-sm text-[#374151]">
                  Make this the default company (used when no company is specified on a record)
                </label>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 mt-5">
              <Button variant="outline" size="sm" onClick={() => setModalOpen(false)} disabled={saving}>
                Cancel
              </Button>
              <Button variant="accent" size="sm" onClick={handleSubmit} disabled={saving}>
                {saving ? 'Saving…' : (editing ? 'Save Changes' : 'Create Company')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
