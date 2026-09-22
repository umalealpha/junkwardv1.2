'use client'

import { useEffect, useState } from 'react'
import { useRouter, useParams } from 'next/navigation'
import { getContact, updateContact, getInvoices, getPayments, getToken } from '@/lib/api'
import type { Contact, Invoice, Payment } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { StatusBadge, TypeBadge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { FormField, Input, Select } from '@/components/ui/input'
import { LoadingCard, LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { Edit2, Save, X, AlertCircle, CheckCircle, Mail, Phone, MapPin } from 'lucide-react'

export default function ContactDetailPage() {
  const router = useRouter()
  const params = useParams()
  const id = params.id as string
  const { mode } = useNumberFormat()
  const fmt = (amount: string | number, currency = 'BWP') => formatAmount(amount, currency, mode)

  const [contact, setContact] = useState<Contact | null>(null)
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [payments, setPayments] = useState<Payment[]>([])
  const [loading, setLoading] = useState(true)
  const [invoicesLoading, setInvoicesLoading] = useState(false)
  const [paymentsLoading, setPaymentsLoading] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState('invoices')
  const [editData, setEditData] = useState<Partial<Contact>>({})

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    loadContact()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  useEffect(() => {
    if (activeTab === 'invoices') loadInvoices()
    else if (activeTab === 'payments') loadPayments()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab])

  const loadContact = async () => {
    setLoading(true)
    try {
      const c = await getContact(id)
      setContact(c)
      setEditData(c)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load contact')
    } finally { setLoading(false) }
  }

  const loadInvoices = async () => {
    setInvoicesLoading(true)
    try {
      const res = await getInvoices({ contact: id, page_size: '50' })
      setInvoices(res.results)
    } catch {} finally { setInvoicesLoading(false) }
  }

  const loadPayments = async () => {
    setPaymentsLoading(true)
    try {
      const res = await getPayments({ contact: id, page_size: '50' })
      setPayments(res.results)
    } catch {} finally { setPaymentsLoading(false) }
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await updateContact(id, editData)
      setContact(updated)
      setEditing(false)
      setSuccess('Contact updated successfully')
      setTimeout(() => setSuccess(null), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update contact')
    } finally { setSaving(false) }
  }

  const handleCancelEdit = () => {
    setEditData(contact || {})
    setEditing(false)
  }

  if (loading) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Contact Detail" breadcrumbs={[{ label: 'Contacts', href: '/contacts' }]} />
        <div className="p-6"><LoadingCard message="Loading contact..." className="h-40" /></div>
      </div>
    )
  }

  if (!contact) {
    return (
      <div className="flex flex-col min-h-screen">
        <TopBar title="Contact Detail" breadcrumbs={[{ label: 'Contacts', href: '/contacts' }]} />
        <div className="p-6">
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-4">
            <p className="text-[#DC2626]">{error || 'Contact not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={contact.name}
        breadcrumbs={[{ label: 'Contacts', href: '/contacts' }, { label: contact.name }]}
        actions={
          !editing ? (
            <Button variant="secondary" size="sm" leftIcon={<Edit2 className="w-3.5 h-3.5" />} onClick={() => setEditing(true)}>
              Edit
            </Button>
          ) : (
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" leftIcon={<X className="w-3.5 h-3.5" />} onClick={handleCancelEdit}>Cancel</Button>
              <Button variant="accent" size="sm" leftIcon={<Save className="w-3.5 h-3.5" />} loading={saving} onClick={handleSave}>Save</Button>
            </div>
          )
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-xl p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#059669]" />
            <p className="text-[#059669] text-sm">{success}</p>
          </div>
        )}
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-[#FFF7ED] border border-[#FED7AA] rounded-full flex items-center justify-center">
                <span className="text-[#CC6C00] font-bold uppercase">{contact.name[0]}</span>
              </div>
              <div>
                <h2 className="text-base font-semibold text-[#0B0B3B]">{contact.name}</h2>
                <TypeBadge type={contact.contact_type} />
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {editing ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <FormField label="Name" required>
                  <Input value={editData.name || ''} onChange={(e) => setEditData({ ...editData, name: e.target.value })} />
                </FormField>
                <FormField label="Contact Type">
                  <Select value={editData.contact_type || ''} onChange={(e) => setEditData({ ...editData, contact_type: e.target.value })}>
                    <option value="customer">Customer</option>
                    <option value="vendor">Vendor</option>
                    <option value="broker">Broker</option>
                    <option value="both">Both</option>
                  </Select>
                </FormField>
                <FormField label="Email">
                  <Input type="email" value={editData.email || ''} onChange={(e) => setEditData({ ...editData, email: e.target.value })} placeholder="email@example.com" />
                </FormField>
                <FormField label="Phone">
                  <Input value={editData.phone || ''} onChange={(e) => setEditData({ ...editData, phone: e.target.value })} placeholder="+267 xxx xxxx" />
                </FormField>
                <FormField label="Currency">
                  <Select value={editData.currency_code || 'BWP'} onChange={(e) => setEditData({ ...editData, currency_code: e.target.value })}>
                    <option value="BWP">BWP</option>
                    <option value="USD">USD</option>
                    <option value="ZAR">ZAR</option>
                    <option value="EUR">EUR</option>
                    <option value="GBP">GBP</option>
                  </Select>
                </FormField>
                <FormField label="Payment Terms (days)">
                  <Input type="number" value={String(editData.payment_terms_days || 0)} onChange={(e) => setEditData({ ...editData, payment_terms_days: parseInt(e.target.value) })} min="0" />
                </FormField>
                <FormField label="Address" className="col-span-full">
                  <Input value={editData.address || ''} onChange={(e) => setEditData({ ...editData, address: e.target.value })} placeholder="Physical address..." />
                </FormField>
              </div>
            ) : (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Email</p>
                  <p className="text-sm text-[#374151] mt-1 flex items-center gap-1">
                    {contact.email ? (
                      <>
                        <Mail className="w-3 h-3 text-[#9CA3AF]" />
                        <a href={`mailto:${contact.email}`} className="hover:text-[#F07F00] transition-colors">{contact.email}</a>
                      </>
                    ) : <span className="text-[#D1D5DB]">—</span>}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Phone</p>
                  <p className="text-sm text-[#374151] mt-1 flex items-center gap-1">
                    {contact.phone ? (
                      <>
                        <Phone className="w-3 h-3 text-[#9CA3AF]" />
                        {contact.phone}
                      </>
                    ) : <span className="text-[#D1D5DB]">—</span>}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Currency</p>
                  <p className="text-sm text-[#374151] mt-1">{contact.currency_code}</p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Payment Terms</p>
                  <p className="text-sm text-[#374151] mt-1">
                    {contact.payment_terms_days > 0 ? `Net ${contact.payment_terms_days} days` : 'Immediate'}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Status</p>
                  <div className="mt-1.5"><StatusBadge status={contact.is_active ? 'active' : 'inactive'} /></div>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">Resident</p>
                  <p className="text-sm text-[#374151] mt-1">{contact.is_resident ? 'Yes' : 'No'}</p>
                </div>
                <div>
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">WHT Exempt</p>
                  <p className="text-sm text-[#374151] mt-1">{contact.wht_exempt ? 'Yes' : 'No'}</p>
                </div>
                {contact.address && (
                  <div className="col-span-2">
                    <p className="text-xs text-[#6B7280] uppercase tracking-wider">Address</p>
                    <p className="text-sm text-[#374151] mt-1 flex items-start gap-1">
                      <MapPin className="w-3 h-3 text-[#9CA3AF] mt-0.5 flex-shrink-0" />
                      {contact.address}
                    </p>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList variant="underline">
            <TabsTrigger value="invoices">Invoices</TabsTrigger>
            <TabsTrigger value="payments">Payments</TabsTrigger>
          </TabsList>

          <TabsContent value="invoices" className="mt-4">
            <Card>
              <CardContent className="p-0">
                {invoicesLoading ? <LoadingTable rows={5} cols={6} /> : (
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Invoice #</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Type</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Due Date</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Total</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Balance</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {invoices.length === 0 ? (
                        <tr><td colSpan={6} className="px-4 py-8 text-center text-[#9CA3AF] text-sm">No invoices for this contact</td></tr>
                      ) : (
                        invoices.map((inv) => (
                          <tr key={inv.id} className="table-row-alt hover:bg-[#FFF7ED] cursor-pointer transition-colors" onClick={() => router.push(`/invoices/${inv.id}`)}>
                            <td className="px-4 py-3 font-mono text-xs text-[#CC6C00]">{inv.invoice_number}</td>
                            <td className="px-4 py-3"><TypeBadge type={inv.invoice_type} /></td>
                            <td className="px-4 py-3 text-[#6B7280] text-xs">{formatDate(inv.due_date)}</td>
                            <td className="px-4 py-3 text-right font-mono-nums text-[#374151]">{fmt(inv.total_amount, inv.currency)}</td>
                            <td className="px-4 py-3 text-right font-mono-nums">
                              <span className={parseAmount(inv.balance_due) > 0 ? 'text-[#D97706]' : 'text-[#059669]'}>
                                {fmt(inv.balance_due, inv.currency)}
                              </span>
                            </td>
                            <td className="px-4 py-3"><StatusBadge status={inv.status} /></td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="payments" className="mt-4">
            <Card>
              <CardContent className="p-0">
                {paymentsLoading ? <LoadingTable rows={5} cols={5} /> : (
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Payment #</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Method</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Amount</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {payments.length === 0 ? (
                        <tr><td colSpan={5} className="px-4 py-8 text-center text-[#9CA3AF] text-sm">No payments for this contact</td></tr>
                      ) : (
                        payments.map((p) => (
                          <tr key={p.id} className="table-row-alt hover:bg-[#FFF7ED] cursor-pointer transition-colors" onClick={() => router.push(`/payments/${p.id}`)}>
                            <td className="px-4 py-3 font-mono text-xs text-[#CC6C00]">{p.payment_number}</td>
                            <td className="px-4 py-3 text-[#6B7280] text-xs">{formatDate(p.payment_date)}</td>
                            <td className="px-4 py-3 text-[#6B7280] text-xs">{p.payment_method}</td>
                            <td className="px-4 py-3 text-right font-mono-nums text-[#059669]">{fmt(p.amount, p.currency)}</td>
                            <td className="px-4 py-3"><StatusBadge status={p.status} /></td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
