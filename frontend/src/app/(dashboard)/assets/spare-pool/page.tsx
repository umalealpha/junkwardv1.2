'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { getSparePool, uploadSparePoolCsv, updateAssetCondition } from '@/lib/api'
import type { AssetListItem } from '@/lib/api'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  TableWrapper, Table, TableHead, TableBody, TableRow, TableHeader, TableCell, EmptyTableRow,
} from '@/components/ui/table'
import { LoadingTable } from '@/components/ui/loading'
import { pushToast } from '@/components/Toaster'
import { useTheme } from '@/contexts/ThemeContext'

const NAVY = '#0D1B2A'

const CONDITION_COLOURS: Record<string, string> = {
  functional: '#059669',
  broken: '#DC2626',
  unknown: '#9CA3AF',
}

export default function SparePoolPage() {
  const { isFun } = useTheme()
  const [assets, setAssets] = useState<AssetListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = () => {
    setLoading(true)
    getSparePool()
      .then(setAssets)
      .catch((e) => pushToast({ type: 'error', message: (e as Error).message }))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const result = await uploadSparePoolCsv(file)
      pushToast({ type: 'success', message: `${result.created} spare asset(s) added.` })
      if (result.errors?.length) {
        pushToast({ type: 'warning', message: result.errors.join('; ') })
      }
      load()
    } catch (err) {
      pushToast({ type: 'error', message: (err as Error).message })
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const setCondition = async (id: string, condition: string) => {
    try {
      await updateAssetCondition(id, condition)
      setAssets((prev) => prev.map((a) => a.id === id ? { ...a, condition: condition as AssetListItem['condition'], condition_display: condition.charAt(0).toUpperCase() + condition.slice(1) } : a))
    } catch (err) {
      pushToast({ type: 'error', message: (err as Error).message })
    }
  }

  return (
    <div className="p-6 space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className={`font-serif ${isFun ? 'tracking-tight' : ''}`} style={{ color: NAVY }}>
            Spare Pool
          </CardTitle>
          <div>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              className="hidden"
              onChange={handleUpload}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              {uploading ? 'Uploading…' : 'Upload CSV / Excel'}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div
            className="rounded-lg border px-4 py-3 text-sm"
            style={{ background: '#FFFBEB', borderColor: '#FDE68A', color: '#92400E' }}
          >
            A spare can only be issued through a new, approved requisition (Finance Manager + CFO).
            It cannot be handed out directly.
          </div>

          {loading ? (
            <LoadingTable rows={6} cols={6} />
          ) : (
            <TableWrapper>
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeader>Tag</TableHeader>
                    <TableHeader>Name</TableHeader>
                    <TableHeader>Category</TableHeader>
                    <TableHeader>Condition</TableHeader>
                    <TableHeader>Location</TableHeader>
                    <TableHeader className="text-right">Action</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {assets.length === 0 ? (
                    <EmptyTableRow colSpan={6} message="No returned or spare assets right now." />
                  ) : (
                    assets.map((a) => (
                      <TableRow key={a.id}>
                        <TableCell className="font-mono">{a.tag_number}</TableCell>
                        <TableCell>{a.name}</TableCell>
                        <TableCell>{a.category_name}</TableCell>
                        <TableCell>
                          <select
                            value={a.condition}
                            onChange={(e) => setCondition(a.id, e.target.value)}
                            className="rounded border px-2 py-1 text-sm font-medium"
                            style={{
                              color: CONDITION_COLOURS[a.condition] || '#6B7280',
                              borderColor: CONDITION_COLOURS[a.condition] || '#D1D5DB',
                            }}
                          >
                            <option value="functional">Functional</option>
                            <option value="broken">Broken</option>
                            <option value="unknown">Unknown</option>
                          </select>
                        </TableCell>
                        <TableCell>{a.location || '—'}</TableCell>
                        <TableCell className="text-right">
                          <Link href="/assets/requisitions">
                            <Button
                              variant="accent"
                              size="sm"
                              style={{ background: '#F4A623', borderColor: '#F4A623' }}
                            >
                              Reissue
                            </Button>
                          </Link>
                        </TableCell>
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
