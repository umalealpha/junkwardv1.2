'use client'

/**
 * /contacts/new — create a contact (vendor / supplier / repairer, customer,
 * broker, reinsurer).
 *
 * CFO / Kao (Claims) 2026-07-13: the "New Vendor" and "New Contact" buttons
 * pointed here but the page never existed — the URL fell through to the
 * /contacts/[id] detail page, which tried to load a contact with id "new"
 * and failed. This is the real form. Claims reach it from the Procurement
 * sidebar ("Add Supplier / Repairer"); Finance from /vendors and /contacts.
 *
 * Duplicate guard: on name entry we search existing contacts and show any
 * close matches, so the vendor master doesn't fill with duplicates again
 * (the 2026-07-03 dedupe took vendors 1485 -> 1042; let's not undo it).
 */

import { useEffect, useState, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { createContact, getContacts, getToken } from '@/lib/api'
import type { Contact } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FormField, Input, Select } from '@/components/ui/input'
import { AlertCircle, Building2, CheckCircle2, Save } from 'lucide-react'

const TYPE_OPTIONS = [
  { value: 'vendor',    label: 'Vendor / Supplier / Repairer' },
  { value: 'customer',  label: 'Customer' },
  { value: 'broker',    label: 'Broker' },
  { value: 'reinsurer', label: 'Reinsurer' },
]

function NewContactForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { selected, companies } = useCompany()

  const initialType = TYPE_OPTIONS.some((t) => t.value === searchParams.get('type'))
    ? (searchParams.get('type') as string)
    : 'vendor'

  const [contactType, setContactType] = useState(initialType)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [address, setAddress] = useState('')
  const [taxId, setTaxId] = useState('')
  const [regNo, setRegNo] = useState('')
  const [companyId, setCompanyId] = useState('')

  const [similar, setSimilar] = useState<Contact[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) router.replace('/login')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Default the owning company to the topbar selection (ADIC by default).
  useEffect(() => {
    if (!companyId && selected?.id) setCompanyId(selected.id)
  }, [selected, companyId])

  // Duplicate guard — search existing contacts as the name is typed.
  useEffect(() => {
    const q = name.trim()
    if (q.length < 3) { setSimilar([]); return }
    const t = setTimeout(async () => {
      try {
        const res = await getContacts({ search: q, page_size: '5' })
        setSimilar(res.results.filter((c) => c.is_active))
      } catch { setSimilar([]) }
    }, 350)
    return () => clearTimeout(t)
  }, [name])

  async function handleSave() {
    setError(null)
    if (name.trim().length < 2) { setError('Enter the full name — at least 2 characters.'); return }
    if (!companyId) { setError('Pick which company this contact belongs to.'); return }
    setSaving(true)
    try {
      const created = await createContact({
        contact_type: contactType,
        name: name.trim(),
        email: email.trim() || null,
        phone: phone.trim() || null,
        address: address.trim() || null,
        tax_id: taxId.trim() || null,
        registration_number: regNo.trim() || null,
        company: companyId,
      })
      router.push(`/contacts/${created.id}?created=1`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save — please try again.')
      setSaving(false)
    }
  }

  const isVendor = contactType === 'vendor'

  return (
    <div>
      <TopBar />
      <div className="p-6 max-w-2xl mx-auto space-y-6">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Building2 className="h-5 w-5 text-[#F4A623]" />
            {isVendor ? 'Add Supplier / Repairer' : 'New Contact'}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {isVendor
              ? 'Add a supplier or repairer that is not yet on the vendor list, then pick them on your PO.'
              : 'Create a new contact record.'}
          </p>
        </div>

        <Card><CardContent className="p-5 space-y-4">
          <FormField label="Type">
            <Select value={contactType} onChange={(e) => setContactType(e.target.value)}>
              {TYPE_OPTIONS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </Select>
          </FormField>

          <FormField label="Name" required>
            <Input value={name} onChange={(e) => setName(e.target.value)}
                   placeholder={isVendor ? 'e.g. Mancon (Pty) Ltd' : 'Full name'} />
          </FormField>

          {similar.length > 0 && (
            <div className="text-sm rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/30 p-3 space-y-1">
              <p className="font-medium flex items-center gap-1.5">
                <AlertCircle className="h-4 w-4 text-amber-500" /> Similar contacts already exist — check before adding a duplicate:
              </p>
              {similar.map((c) => (
                <button key={c.id} type="button"
                        onClick={() => router.push(`/contacts/${c.id}`)}
                        className="block text-left underline underline-offset-2 hover:text-[#F4A623]">
                  {c.name} ({c.contact_type}{c.company_code ? ` · ${c.company_code}` : ''})
                </button>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <FormField label="Email">
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
            </FormField>
            <FormField label="Phone">
              <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+267 …" />
            </FormField>
          </div>

          <FormField label="Address">
            <Input value={address} onChange={(e) => setAddress(e.target.value)}
                   placeholder="Plot / P O Box, City" />
          </FormField>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <FormField label="Company registration no.">
              <Input value={regNo} onChange={(e) => setRegNo(e.target.value)} placeholder="Optional" />
            </FormField>
            <FormField label="Tax ID (TIN)">
              <Input value={taxId} onChange={(e) => setTaxId(e.target.value)} placeholder="Optional" />
            </FormField>
          </div>

          <FormField label="Belongs to company" required>
            <Select value={companyId} onChange={(e) => setCompanyId(e.target.value)}>
              <option value="">Select company…</option>
              {companies.map((c) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
            </Select>
          </FormField>

          {error && (
            <p className="text-sm text-red-600 flex items-center gap-1.5">
              <AlertCircle className="h-4 w-4" /> {error}
            </p>
          )}

          <div className="flex items-center gap-3 pt-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving
                ? 'Saving…'
                : <><Save className="h-4 w-4 mr-1" /> {isVendor ? 'Add supplier' : 'Create contact'}</>}
            </Button>
            <Button variant="outline" onClick={() => router.back()} disabled={saving}>Cancel</Button>
            {saving && <CheckCircle2 className="h-4 w-4 text-muted-foreground animate-pulse" />}
          </div>
        </CardContent></Card>
      </div>
    </div>
  )
}

export default function NewContactPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-muted-foreground">Loading…</div>}>
      <NewContactForm />
    </Suspense>
  )
}
