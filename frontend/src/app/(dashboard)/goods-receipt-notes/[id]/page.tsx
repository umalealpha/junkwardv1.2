'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { getGRN } from '@/lib/api'
import type { GRNDetail } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, Printer } from 'lucide-react'

export default function GoodsReceiptNotePage() {
  const params = useParams()
  const router = useRouter()
  const id = params?.id as string

  const [grn, setGrn] = useState<GRNDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getGRN(id)
      .then(setGrn)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [id])

  return (
    <div>
      <div className="print:hidden">
        <TopBar />
      </div>

      <div className="max-w-3xl mx-auto p-4 sm:p-6">
        <div className="flex items-center justify-between mb-4 print:hidden">
          <Button variant="outline" onClick={() => router.back()}>
            <ArrowLeft className="w-4 h-4 mr-1" /> Back
          </Button>
          {grn && (
            <Button onClick={() => window.print()}
              className="bg-[#0D1B2A] hover:bg-[#16273d] text-white">
              <Printer className="w-4 h-4 mr-1" /> Print
            </Button>
          )}
        </div>

        {loading && <div className="text-sm text-gray-500">Loading…</div>}
        {error && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">{error}</div>
        )}

        {grn && (
          <Card>
            <CardContent className="p-6 space-y-6">
              <header className="flex items-start justify-between border-b border-gray-200 pb-4">
                <div>
                  <div className="text-xs uppercase tracking-wide text-[#F4A623] font-semibold">
                    Alpha Direct Insurance
                  </div>
                  <h1 className="text-xl font-bold text-[#0D1B2A] mt-1">Goods Receipt Note</h1>
                </div>
                <div className="text-right">
                  <div className="text-lg font-bold text-[#0D1B2A]">{grn.grn_number}</div>
                  <span className={`inline-block mt-1 text-xs px-2 py-0.5 rounded-full ${
                    grn.status === 'posted' ? 'bg-green-100 text-green-800'
                    : grn.status === 'cancelled' ? 'bg-gray-200 text-gray-700'
                    : 'bg-amber-100 text-amber-800'}`}>
                    {grn.status_display || grn.status}
                  </span>
                </div>
              </header>

              <div className="grid grid-cols-2 gap-4 text-sm">
                <Field label="Purchase order">
                  <Link href={`/purchase-orders/${grn.purchase_order}`}
                    className="text-[#0D1B2A] underline print:no-underline">
                    {grn.po_number}
                  </Link>
                </Field>
                <Field label="Supplier">{grn.supplier_name}</Field>
                <Field label="Receipt date">{grn.receipt_date}</Field>
                <Field label="Delivery note ref">{grn.delivery_note_reference || '—'}</Field>
                <Field label="Received by">{grn.received_by_username || '—'}</Field>
                <Field label="Journal entry">{grn.journal_entry_number || '—'}</Field>
              </div>

              <div>
                <h2 className="text-sm font-semibold text-gray-800 mb-2">Items received</h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm border-collapse">
                    <thead>
                      <tr className="border-b border-gray-300 text-left text-gray-600">
                        <th className="py-2 pr-3">Description</th>
                        <th className="py-2 px-3 text-right">Ordered</th>
                        <th className="py-2 px-3 text-right">Received</th>
                        <th className="py-2 px-3">Condition</th>
                        <th className="py-2 pl-3">Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {grn.lines.map((l) => (
                        <tr key={l.id} className="border-b border-gray-100">
                          <td className="py-2 pr-3">{l.po_line_description}</td>
                          <td className="py-2 px-3 text-right">{l.po_line_quantity}</td>
                          <td className="py-2 px-3 text-right">{l.quantity_received}</td>
                          <td className="py-2 px-3 capitalize">{l.condition}</td>
                          <td className="py-2 pl-3 text-gray-500">{l.notes || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {grn.notes && (
                <div className="text-sm">
                  <div className="text-xs uppercase text-gray-500 mb-1">Notes</div>
                  <div className="text-gray-800">{grn.notes}</div>
                </div>
              )}

              <footer className="border-t border-gray-200 pt-4 text-xs text-gray-500">
                Created by {grn.created_by_username || '—'} · {grn.created_at?.slice(0, 10)}
              </footer>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase text-gray-500">{label}</div>
      <div className="text-sm font-medium text-gray-800">{children}</div>
    </div>
  )
}
