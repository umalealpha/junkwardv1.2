'use client'

import { useEffect, useState } from 'react'
import { getMyAssets } from '@/lib/api'
import type { AssetListItem } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import {
  TableWrapper, Table, TableHead, TableBody, TableRow, TableHeader, TableCell, EmptyTableRow,
} from '@/components/ui/table'
import { LoadingTable } from '@/components/ui/loading'
import { pushToast } from '@/components/Toaster'
import { useTheme } from '@/contexts/ThemeContext'

const NAVY = '#0D1B2A'

type Employee = { id: string; full_name: string } | null

export default function MyAssetsPage() {
  const { isFun } = useTheme()
  const [employee, setEmployee] = useState<Employee>(null)
  const [assets, setAssets] = useState<AssetListItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    getMyAssets()
      .then((res) => {
        if (!alive) return
        setEmployee(res.employee)
        setAssets(res.assets)
      })
      .catch((e) => pushToast({ type: 'error', message: (e as Error).message }))
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  if (loading) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="p-4">
            <LoadingTable rows={6} cols={4} />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (employee === null) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="p-6 text-sm text-[#374151]">
            No staff record is linked to your login, so there are no assets to show.
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className={`font-serif ${isFun ? 'tracking-tight' : ''}`} style={{ color: NAVY }}>
            Assets held by {employee.full_name}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <TableWrapper>
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeader>Tag</TableHeader>
                  <TableHeader>Name</TableHeader>
                  <TableHeader>Category</TableHeader>
                  <TableHeader>Location</TableHeader>
                  <TableHeader>Status</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {assets.length === 0 ? (
                  <EmptyTableRow colSpan={5} message="You are not currently holding any assets." />
                ) : (
                  assets.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="font-mono">{a.tag_number}</TableCell>
                      <TableCell>{a.name}</TableCell>
                      <TableCell>{a.category_name}</TableCell>
                      <TableCell>{a.location || '—'}</TableCell>
                      <TableCell>{a.custody_status_display}</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </TableWrapper>
        </CardContent>
      </Card>
    </div>
  )
}
