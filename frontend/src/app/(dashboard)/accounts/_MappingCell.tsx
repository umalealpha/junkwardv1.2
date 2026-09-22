'use client'

/**
 * Inline MA-mapping cell for the /accounts CoA page.
 *
 * CFO directive 2026-05-24: every row gets a far-right cell where the
 * CFO maps the account to the proper MA workbook line (fs_line_item).
 * Click the cell → dropdown of canonical labels (grouped BS / P&L) →
 * pick → PATCH /accounts/{id}/ → optimistic update + green flash.
 *
 * Options sourced from /api/v1/accounts/fs-line-options/ — single
 * source of truth so dropdown content matches the MA spec exactly.
 */

import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Loader2, X } from 'lucide-react'
import { getFsLineOptions, patchAccount, type Account, type FsLineOptionGroup } from '@/lib/api'

let CACHED_OPTIONS: FsLineOptionGroup[] | null = null

async function loadOptions(): Promise<FsLineOptionGroup[]> {
  if (CACHED_OPTIONS) return CACHED_OPTIONS
  const data = await getFsLineOptions()
  CACHED_OPTIONS = data.groups
  return CACHED_OPTIONS
}

const SIDE_TONE: Record<string, string> = {
  asset:     'text-[#1D4ED8] bg-[#EFF6FF]',
  liability: 'text-[#9A3412] bg-[#FFF7ED]',
  equity:    'text-[#6D28D9] bg-[#F5F3FF]',
  pl:        'text-[#047857] bg-[#ECFDF5]',
}

export function MappingCell({
  account, onSaved,
}: {
  account: Account
  onSaved?: (next: Account) => void
}) {
  const [open, setOpen]       = useState(false)
  const [groups, setGroups]   = useState<FsLineOptionGroup[]>([])
  const [busy, setBusy]       = useState(false)
  const [flash, setFlash]     = useState(false)
  const [err, setErr]         = useState<string | null>(null)
  const [filter, setFilter]   = useState('')
  const popRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    loadOptions().then(setGroups).catch(e =>
      setErr(e instanceof Error ? e.message : 'Failed to load mapping options'))
    function onDocClick(ev: MouseEvent) {
      if (!popRef.current) return
      if (!popRef.current.contains(ev.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  async function pick(label: string) {
    if (busy || label === account.fs_line_item) {
      setOpen(false); return
    }
    setBusy(true); setErr(null)
    try {
      const next = await patchAccount(account.id, { fs_line_item: label })
      onSaved?.(next)
      setFlash(true); setTimeout(() => setFlash(false), 1200)
      setOpen(false)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  async function clearMapping() {
    setBusy(true); setErr(null)
    try {
      const next = await patchAccount(account.id, { fs_line_item: '' })
      onSaved?.(next)
      setFlash(true); setTimeout(() => setFlash(false), 1200)
      setOpen(false)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Clear failed')
    } finally {
      setBusy(false)
    }
  }

  const current = (account.fs_line_item || '').trim()

  const filtered = groups.map(g => ({
    ...g,
    options: g.options.filter(o =>
      !filter || o.toLowerCase().includes(filter.toLowerCase())),
  })).filter(g => g.options.length > 0)

  return (
    <span
      onClick={ev => ev.stopPropagation()}
      className="relative inline-block"
    >
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={
          'inline-flex items-center gap-1.5 max-w-[260px] px-2.5 py-1 rounded-md border ' +
          'text-xs font-semibold transition-colors ' +
          (current
            ? 'bg-white border-[#D1D5DB] text-[#0D1B2A] hover:bg-[#F9FAFB]'
            : 'bg-[#FEF3C7] border-[#FCD34D] text-[#92400E] hover:bg-[#FDE68A]') +
          (flash ? ' ring-2 ring-[#10B981]' : '')
        }
        title={current || 'Unmapped — click to set MA line'}
      >
        <span className="truncate">{current || '— Unmapped —'}</span>
        {busy
          ? <Loader2 className="w-3 h-3 animate-spin flex-shrink-0" />
          : <ChevronDown className="w-3 h-3 flex-shrink-0" />}
      </button>

      {open && (
        <div
          ref={popRef}
          className="absolute right-0 z-30 mt-1 w-80 max-h-96 overflow-y-auto bg-white border border-[#D1D5DB] rounded-lg shadow-xl"
          style={{ minWidth: 280 }}
        >
          <div className="sticky top-0 bg-white border-b border-[#E5E7EB] px-3 py-2 flex items-center gap-2">
            <input
              autoFocus
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="Search MA lines…"
              className="flex-1 text-xs px-2 py-1 border border-[#D1D5DB] rounded outline-none focus:border-[#F07F00]"
            />
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="p-1 text-[#9CA3AF] hover:text-[#374151]"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          {err && (
            <div className="px-3 py-2 text-xs text-[#B91C1C] bg-[#FEF2F2] border-b border-[#FECACA]">
              {err}
            </div>
          )}

          {current && (
            <button
              type="button"
              onClick={clearMapping}
              className="w-full text-left px-3 py-2 text-xs text-[#9A3412] hover:bg-[#FFF7ED] border-b border-[#E5E7EB] font-semibold"
            >
              Clear mapping
            </button>
          )}

          {filtered.length === 0 && (
            <div className="px-3 py-4 text-xs text-[#9CA3AF] text-center">
              {groups.length === 0 ? 'Loading…' : 'No matches'}
            </div>
          )}

          {filtered.map(g => (
            <div key={g.group + g.side}>
              <div className={
                'sticky top-[37px] px-3 py-1 text-[10px] uppercase font-bold tracking-wider ' +
                (SIDE_TONE[g.side] || 'bg-[#F3F4F6] text-[#6B7280]')
              }>
                {g.group}
              </div>
              {g.options.map(opt => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => pick(opt)}
                  className={
                    'w-full text-left px-3 py-1.5 text-xs hover:bg-[#FFF7ED] flex items-center justify-between ' +
                    (opt === current ? 'bg-[#FFF7ED] font-semibold text-[#9A3412]' : 'text-[#374151]')
                  }
                >
                  <span className="truncate">{opt}</span>
                  {opt === current && <Check className="w-3.5 h-3.5 text-[#F07F00]" />}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </span>
  )
}
