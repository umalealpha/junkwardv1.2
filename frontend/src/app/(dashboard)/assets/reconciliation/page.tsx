'use client'

import { useEffect, useState } from 'react'
import { getAssetReconciliation } from '@/lib/api'
import type { AssetReconciliation } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { StatTile } from '@/components/ui/StatTile'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  TableWrapper, Table, TableHead, TableBody, TableRow, TableHeader, TableCell, EmptyTableRow,
} from '@/components/ui/table'
import { LoadingTable } from '@/components/ui/loading'
import { pushToast } from '@/components/Toaster'
import { useTheme } from '@/contexts/ThemeContext'

const NAVY = '#0D1B2A'

function tiesBadge(ties: boolean | null): React.ReactNode {
  if (ties === null) return <Badge variant="muted">—</Badge>
  return ties
    ? <Badge variant="success">Yes</Badge>
    : <Badge variant="danger">No</Badge>
}

function csvCell(v: string): string {
  // Quote when the value contains a comma, quote or newline; double embedded quotes.
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v
}

export default function ReconciliationPage() {
  const { theme, isFun } = useTheme()
  const [data, setData] = useState<AssetReconciliation | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    getAssetReconciliation()
      .then((d) => { if (alive) setData(d) })
      .catch((e) => pushToast({ type: 'error', message: (e as Error).message }))
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  function exportCsv() {
    if (!data) return
    const headers = ['Tag', 'Name', 'Category', 'Custodian', 'Status']
    const rows = data.register.map((a) => [
      a.tag_number,
      a.name,
      a.category_name,
      a.custodian_employee_name || a.custodian || '',
      a.custody_status_display,
    ])
    const csv = [headers, ...rows]
      .map((r) => r.map((c) => csvCell(String(c ?? ''))).join(','))
      .join('\r\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'asset-register-reconciliation.csv'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  }

  const dash = <span style={{ color: theme.t3 }}>—</span>

  return (
    <div className="p-6 space-y-4">
      {/* ── Header ───────────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <div>
            <CardTitle className={`font-serif ${isFun ? 'tracking-tight' : ''}`} style={{ color: NAVY }}>
              Count Reconciliation
            </CardTitle>
            <p className="text-sm mt-1" style={{ color: theme.t2 }}>
              The live register ties out to the most recent quarterly physical count (acceptance criterion 7).
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={exportCsv} disabled={!data || data.register.length === 0}>
            Export CSV
          </Button>
        </CardHeader>

        <CardContent className="space-y-4">
          {/* ── Stat tiles ─────────────────────────────────────────── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatTile label="Register count" value={data ? data.register_count : dash} />
            <StatTile label="Physically counted" value={data && data.counted_assets != null ? data.counted_assets : dash} />
            <StatTile
              label="Variance"
              value={data && data.variance != null ? data.variance : dash}
              tone={data && data.variance != null && data.variance !== 0 ? 'warn' : 'accent'}
            />
            <StatTile label="Ties out" value={data ? tiesBadge(data.ties_out) : dash} />
          </div>

          {/* ── Count meta + discrepancies ─────────────────────────── */}
          {data?.count_period && (
            <p className="text-sm" style={{ color: theme.t2 }}>
              Count: <span style={{ color: theme.text }}>{data.count_period}</span>
              {data.count_completed_at ? <>, completed <span style={{ color: theme.text }}>{data.count_completed_at}</span></> : null}
            </p>
          )}

          {data?.discrepancies && (
            <div
              className="rounded-lg border px-4 py-3 text-sm whitespace-pre-wrap"
              style={{ background: '#FFFBEB', borderColor: '#FDE68A', color: '#92400E' }}
            >
              {data.discrepancies}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Register table ──────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="font-serif" style={{ color: NAVY }}>Asset register</CardTitle>
        </CardHeader>
        <CardContent>
          {data?.truncated && (
            <p style={{ color: '#B45309', fontSize: 13, marginBottom: 8 }}>
              Showing the first 2,000 assets. Use the CSV export for the full register.
            </p>
          )}
          {loading ? (
            <LoadingTable rows={6} cols={5} />
          ) : (
            <TableWrapper>
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeader>Tag</TableHeader>
                    <TableHeader>Name</TableHeader>
                    <TableHeader>Category</TableHeader>
                    <TableHeader>Custodian</TableHeader>
                    <TableHeader>Status</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {!data || data.register.length === 0 ? (
                    <EmptyTableRow colSpan={5} message="No assets in the register." />
                  ) : (
                    data.register.map((a) => (
                      <TableRow key={a.id}>
                        <TableCell className="font-mono">{a.tag_number}</TableCell>
                        <TableCell>{a.name}</TableCell>
                        <TableCell>{a.category_name}</TableCell>
                        <TableCell>{a.custodian_employee_name || a.custodian || '—'}</TableCell>
                        <TableCell>{a.custody_status_display}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </TableWrapper>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
