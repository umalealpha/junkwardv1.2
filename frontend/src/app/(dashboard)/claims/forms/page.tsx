'use client'

import { useEffect, useMemo, useState, useCallback } from 'react'
import { getClaimForms, downloadClaimForm } from '@/lib/api'
import type { ClaimFormVault, ClaimFormVaultItem } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Download, Search, FileText, AlertCircle } from 'lucide-react'

/**
 * Claim Forms Vault — one place to find and download the current blank claim
 * form for any claim type. Read-only reference library; blank templates, no PII.
 * Downloads stream through the authed helper (never a raw /media link).
 */
export default function ClaimFormsVaultPage() {
  const [vault, setVault] = useState<ClaimFormVault | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [downloading, setDownloading] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setVault(await getClaimForms())
    } catch (e: any) {
      setError(e?.message === 'Failed to fetch' ? 'Could not load the forms.' : (e?.message || 'Could not load the forms.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (vault?.data ?? []).filter(f => {
      if (category && f.category !== category) return false
      if (q && !f.title.toLowerCase().includes(q) && !f.slug.includes(q)) return false
      return true
    })
  }, [vault, search, category])

  async function onDownload(item: ClaimFormVaultItem) {
    setDownloading(item.id)
    try {
      await downloadClaimForm(item)
    } catch {
      setError(`Could not download ${item.title}.`)
    } finally {
      setDownloading(null)
    }
  }

  const cats = vault?.summary.categories ?? []
  const counts = vault?.summary.byCategory ?? {}

  return (
    <div className="min-h-screen bg-[#F8FAFC]">
      <TopBar />
      <div className="max-w-6xl mx-auto px-4 py-6">
        <div className="mb-5">
          <h1 className="text-xl font-semibold text-[#0D1B2A]">Claim Forms</h1>
          <p className="text-sm text-slate-500 mt-1">
            The current blank claim form for every claim type. Download the one you need.
          </p>
        </div>

        <div className="flex flex-wrap gap-2 mb-4">
          <div className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <Input
              className="pl-9"
              placeholder="Search forms…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
          <button
            onClick={() => setCategory('')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md border transition ${category === '' ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
          >
            All ({vault?.summary.total ?? 0})
          </button>
          {cats.filter(c => counts[c.key]).map(c => (
            <button
              key={c.key}
              onClick={() => setCategory(c.key)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md border transition ${category === c.key ? 'bg-[#0D1B2A] text-white border-[#0D1B2A]' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
            >
              {c.label} ({counts[c.key]})
            </button>
          ))}
        </div>

        {error && (
          <div className="mb-4 flex items-center gap-2 text-sm text-[#B91C1C] bg-[#FEF2F2] border border-[#FECACA] rounded-md px-3 py-2">
            <AlertCircle className="h-4 w-4" /> {error}
          </div>
        )}

        {loading ? (
          <p className="text-sm text-slate-500 py-10 text-center">Loading…</p>
        ) : rows.length === 0 ? (
          <Card><CardContent className="py-10 text-center text-slate-500 text-sm">
            {vault && vault.summary.total === 0
              ? 'No forms loaded yet.'
              : 'No forms match your search.'}
          </CardContent></Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {rows.map(f => (
              <Card key={f.id} className="hover:shadow-md transition-shadow">
                <CardContent className="p-4 flex flex-col h-full">
                  <div className="flex items-start gap-3 mb-3">
                    <div className="rounded-lg bg-[#FFF7EC] p-2">
                      <FileText className="h-5 w-5 text-[#F4A623]" />
                    </div>
                    <div className="min-w-0">
                      <p className="font-medium text-sm text-[#0D1B2A] leading-snug">{f.title}</p>
                      <p className="text-[11px] text-slate-400 mt-0.5">
                        {f.categoryLabel}{f.sizeKb ? ` · ${f.sizeKb} KB` : ''}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => onDownload(f)}
                    disabled={downloading === f.id}
                    className="mt-auto inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-md bg-[#F4A623] text-[#0D1B2A] hover:opacity-90 transition disabled:opacity-50"
                  >
                    <Download className="h-3.5 w-3.5" />
                    {downloading === f.id ? 'Downloading…' : 'Download'}
                  </button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
