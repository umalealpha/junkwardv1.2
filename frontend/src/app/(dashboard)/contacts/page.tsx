'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getContacts, getToken } from '@/lib/api'
import type { Contact } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { StatusBadge, TypeBadge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { LoadingTable } from '@/components/ui/loading'

import { Plus, Search, Users, AlertCircle, Mail, Phone, ChevronLeft, ChevronRight } from 'lucide-react'
import SmartUpload from '@/components/SmartUpload'

// ─── Contacts List Page ───────────────────────────────────────────────────────

export default function ContactsPage() {
  const router = useRouter()
  const [contacts, setContacts] = useState<Contact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [activeTab, setActiveTab] = useState('all')
  const [page, setPage] = useState(1)
  const [totalCount, setTotalCount] = useState(0)
  const PAGE_SIZE = 25

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = { page: String(page), page_size: String(PAGE_SIZE) }
      if (activeTab !== 'all') params.contact_type = activeTab
      if (search) params.search = search
      const res = await getContacts(params)
      setContacts(res.results)
      setTotalCount(res.count)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load contacts')
    } finally {
      setLoading(false)
    }
  }, [activeTab, page, search])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  const totalPages = Math.ceil(totalCount / PAGE_SIZE)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Contacts"
        breadcrumbs={[{ label: 'Finance' }, { label: 'Contacts' }]}
        actions={
          <Button variant="accent" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => router.push('/contacts/new')}>
            New Contact
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <SmartUpload
          section={activeTab === 'vendor' ? 'vendors' : 'customers'}
        />
        <Tabs value={activeTab} onValueChange={(v) => { setActiveTab(v); setPage(1) }}>
          <TabsList variant="underline">
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="customer">Customers</TabsTrigger>
            <TabsTrigger value="vendor">Vendors</TabsTrigger>
            <TabsTrigger value="broker">Brokers</TabsTrigger>
          </TabsList>

          {/* Search */}
          <div className="flex gap-3 mt-4">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
              <input
                type="text"
                placeholder="Search contacts..."
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1) }}
                className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md pl-9 pr-3 text-sm text-[#111827] placeholder-[#9CA3AF] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
              />
            </div>
          </div>

          {error && (
            <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2 mt-2">
              <AlertCircle className="w-4 h-4 text-[#DC2626] flex-shrink-0" />
              <p className="text-[#DC2626] text-sm">{error}</p>
            </div>
          )}

          <TabsContent value={activeTab} className="mt-4">
            <Card>
              <CardContent className="p-0">
                {loading ? (
                  <LoadingTable rows={8} cols={7} />
                ) : (
                  <>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm border-collapse">
                        <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                          <tr>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Name</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Type</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden sm:table-cell">Email</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Phone</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">Currency</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">Payment Terms</th>
                            <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Status</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[#E5E7EB] bg-white">
                          {contacts.length === 0 ? (
                            <tr>
                              <td colSpan={7} className="px-4 py-16 text-center">
                                <Users className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                                <p className="text-[#6B7280] text-sm font-medium">No contacts found</p>
                              </td>
                            </tr>
                          ) : (
                            contacts.map((contact) => (
                              <tr
                                key={contact.id}
                                onClick={() => router.push(`/contacts/${contact.id}`)}
                                className="table-row-alt hover:bg-[#FFF7ED] cursor-pointer transition-colors"
                              >
                                <td className="px-4 py-3 font-medium text-[#111827]">{contact.name}</td>
                                <td className="px-4 py-3"><TypeBadge type={contact.contact_type} /></td>
                                <td className="px-4 py-3 text-[#6B7280] text-xs hidden sm:table-cell">
                                  {contact.email ? (
                                    <div className="flex items-center gap-1">
                                      <Mail className="w-3 h-3" />
                                      <span className="truncate max-w-[180px]">{contact.email}</span>
                                    </div>
                                  ) : <span className="text-[#D1D5DB]">—</span>}
                                </td>
                                <td className="px-4 py-3 text-[#6B7280] text-xs hidden md:table-cell">
                                  {contact.phone ? (
                                    <div className="flex items-center gap-1">
                                      <Phone className="w-3 h-3" />
                                      <span>{contact.phone}</span>
                                    </div>
                                  ) : <span className="text-[#D1D5DB]">—</span>}
                                </td>
                                <td className="px-4 py-3 text-[#6B7280] text-xs hidden lg:table-cell">{contact.currency_code}</td>
                                <td className="px-4 py-3 text-[#6B7280] text-xs hidden lg:table-cell">
                                  {contact.payment_terms_days > 0 ? `Net ${contact.payment_terms_days}` : 'Immediate'}
                                </td>
                                <td className="px-4 py-3"><StatusBadge status={contact.is_active ? 'active' : 'inactive'} /></td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>

                    {totalCount > PAGE_SIZE && (
                      <div className="flex items-center justify-between px-4 py-3 border-t border-[#E5E7EB]">
                        <p className="text-xs text-[#9CA3AF]">
                          {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, totalCount)} of {totalCount}
                        </p>
                        <div className="flex items-center gap-2">
                          <Button variant="ghost" size="sm" leftIcon={<ChevronLeft className="w-3.5 h-3.5" />} disabled={page === 1} onClick={() => setPage(page - 1)}>Prev</Button>
                          <span className="text-xs text-[#6B7280]">{page} / {totalPages}</span>
                          <Button variant="ghost" size="sm" rightIcon={<ChevronRight className="w-3.5 h-3.5" />} disabled={page === totalPages} onClick={() => setPage(page + 1)}>Next</Button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
