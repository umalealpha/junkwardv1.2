'use client'

/**
 * /compliance/nbfira/submissions — Submission History & Audit Trail.
 *
 * Holds the immutable record of every NBFIRA filing the company has sent
 * (quarterly + annual + ad-hoc). Each row captures: kind, period, prepared
 * by, submitted by, NBFIRA acknowledgement reference, submitted-at, status.
 *
 * Phase 1: no submissions exist in the system. The page renders the empty
 * state + the column structure so the auditor can sign off the schema.
 * Phase 2: rows insert via the Prepare → Submit flow on Quarterly /
 * Annual pages.
 */

import { useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { FileSpreadsheet, Filter } from 'lucide-react'

interface Submission {
  id: string
  kind: 'quarterly' | 'annual' | 'ad_hoc'
  period: string
  prepared_by: string
  submitted_by: string | null
  submitted_at: string | null
  ack_ref: string | null
  status: 'draft' | 'submitted' | 'acknowledged' | 'rejected'
}

const KIND_LABEL: Record<string, string> = {
  quarterly: 'Quarterly Return',
  annual:    'Annual Return',
  ad_hoc:    'Ad-hoc Submission',
}

const STATUS_STYLES: Record<string, string> = {
  draft:        'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
  submitted:    'bg-[#EFF6FF] text-[#1D4ED8] border-[#BFDBFE]',
  acknowledged: 'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  rejected:     'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
}

export default function SubmissionsHistoryPage() {
  // Phase 1: empty list. Phase 2: GET /api/v1/compliance/submissions/.
  const [rows] = useState<Submission[]>([])
  const [filter, setFilter] = useState<'all' | Submission['kind']>('all')

  const filtered = filter === 'all' ? rows : rows.filter((r) => r.kind === filter)

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Submission History & Audit Trail"
        subtitle="Every NBFIRA filing recorded with prepared-by, submitted-by, NBFIRA ack reference"
        breadcrumbs={[{ label: 'Compliance' }, { label: 'NBFIRA' }, { label: 'Submissions' }]}
        actions={
          <Button variant="secondary" size="sm" disabled
                  leftIcon={<FileSpreadsheet className="w-3.5 h-3.5" />}>
            Export CSV
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <Card>
          <CardContent className="p-3 flex items-center gap-2">
            <Filter className="w-4 h-4 text-[#6B7280]" />
            <span className="text-xs text-[#6B7280] mr-2">Filter:</span>
            {(['all', 'quarterly', 'annual', 'ad_hoc'] as const).map((k) => (
              <button key={k}
                      onClick={() => setFilter(k)}
                      className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                        filter === k
                          ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]'
                          : 'bg-white text-[#374151] border-[#E5E7EB] hover:border-[#D1D5DB]'
                      }`}>
                {k === 'all' ? 'All' : KIND_LABEL[k]}
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-[#6B7280] border-b border-[#E5E7EB]">
                <tr>
                  <th className="py-3 px-4">Kind</th>
                  <th className="py-3 px-4">Period</th>
                  <th className="py-3 px-4">Prepared by</th>
                  <th className="py-3 px-4">Submitted by</th>
                  <th className="py-3 px-4">Submitted at</th>
                  <th className="py-3 px-4">NBFIRA ack ref</th>
                  <th className="py-3 px-4">Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-12 text-center">
                      <FileSpreadsheet className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                      <p className="text-sm text-[#6B7280]">No submissions recorded yet.</p>
                      <p className="text-xs text-[#9CA3AF] mt-1">
                        Each filing prepared from Quarterly Returns or Annual Returns will land here once submitted to NBFIRA.
                      </p>
                    </td>
                  </tr>
                )}
                {filtered.map((r) => (
                  <tr key={r.id} className="border-b border-[#F3F4F6]">
                    <td className="py-3 px-4">{KIND_LABEL[r.kind]}</td>
                    <td className="py-3 px-4 font-mono-nums">{r.period}</td>
                    <td className="py-3 px-4">{r.prepared_by}</td>
                    <td className="py-3 px-4">{r.submitted_by || '—'}</td>
                    <td className="py-3 px-4 font-mono-nums">{r.submitted_at || '—'}</td>
                    <td className="py-3 px-4 font-mono">{r.ack_ref || '—'}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded-full text-xs border ${STATUS_STYLES[r.status]}`}>
                        {r.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <div className="text-xs text-[#9CA3AF]">
          Audit trail is append-only by design — once a submission lands, the row + its supporting PDFs are locked. Internal audit can request the prepared-by and approved-by signatures + the NBFIRA acknowledgement reference from this page.
        </div>
      </div>
    </div>
  )
}
