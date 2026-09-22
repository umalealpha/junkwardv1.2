'use client'

/**
 * SearchableSelect — a dependency-free, theme-aware searchable combobox.
 *
 * Built 2026-06-03 (CFO/Pako directive): the plain <select> account pickers
 * on Smart Entry / Journal Entry are unusable once the Chart of Accounts is
 * large — you cannot type to find an account. This component renders a text
 * input that filters the option list as you type, with keyboard navigation
 * (↑/↓/Enter/Esc) and click-to-select.
 *
 * Drop-in replacement for a controlled <select>:
 *   <SearchableSelect
 *      options={accounts.map(a => ({ value: a.code, label: `${a.code} — ${a.name}` }))}
 *      value={line.account}
 *      onChange={(v) => updateLine(idx, 'account', v)}
 *      placeholder="Select account…"
 *   />
 *
 * Aesthetic-only; no API or routing behaviour. Uses the app ThemeContext so it
 * matches light + Fun (dark) themes.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, ChevronDown, Check, X } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'

export interface SelectOption {
  value: string
  label: string
  /** Optional secondary text shown muted under the label. */
  hint?: string
}

interface Props {
  options: SelectOption[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Smaller paddings for dense table rows. */
  dense?: boolean
  disabled?: boolean
  className?: string
  /** Width hint for table cells. */
  minWidth?: number
}

export function SearchableSelect({
  options, value, onChange,
  placeholder = 'Select…', dense = false, disabled = false,
  className = '', minWidth,
}: Props) {
  const { theme } = useTheme()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [hi, setHi] = useState(0)               // highlighted index
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const selected = options.find((o) => o.value === value) || null

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter((o) =>
      o.label.toLowerCase().includes(q) ||
      o.value.toLowerCase().includes(q) ||
      (o.hint || '').toLowerCase().includes(q),
    )
  }, [options, query])

  // Close on outside click.
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false); setQuery('')
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  // Focus the input + reset highlight when opening.
  useEffect(() => {
    if (open) {
      setHi(0)
      const t = setTimeout(() => inputRef.current?.focus(), 0)
      return () => clearTimeout(t)
    }
  }, [open])

  // Keep the highlighted row scrolled into view.
  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.children[hi] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [hi, open])

  const choose = (opt: SelectOption) => {
    onChange(opt.value)
    setOpen(false)
    setQuery('')
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((i) => Math.min(i + 1, filtered.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (filtered[hi]) choose(filtered[hi]) }
    else if (e.key === 'Escape') { setOpen(false); setQuery('') }
  }

  const pad = dense ? 'px-2 py-1.5 text-xs' : 'px-3 py-2.5 text-sm'

  return (
    <div ref={rootRef} className={`relative ${className}`} style={{ minWidth }}>
      {/* Trigger */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        className={`flex w-full items-center justify-between gap-2 rounded ${pad} focus:outline-none`}
        style={{
          background: theme.card,
          border: `1px solid ${open ? theme.orange : theme.g200}`,
          color: selected ? theme.text : theme.t3,
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <span className="truncate text-left">{selected ? selected.label : placeholder}</span>
        <ChevronDown className="h-3.5 w-3.5 flex-shrink-0" style={{ color: theme.t3 }} />
      </button>

      {/* Popover */}
      {open && (
        <div
          className="absolute z-50 mt-1 w-full overflow-hidden rounded-lg shadow-lg"
          style={{ background: theme.card, border: `1px solid ${theme.g200}`, minWidth: minWidth ? Math.max(minWidth, 220) : 220 }}
        >
          {/* Search box */}
          <div className="flex items-center gap-2 border-b px-2.5 py-2" style={{ borderColor: theme.g200 }}>
            <Search className="h-3.5 w-3.5 flex-shrink-0" style={{ color: theme.t3 }} />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => { setQuery(e.target.value); setHi(0) }}
              onKeyDown={onKeyDown}
              placeholder="Type to search…"
              className="w-full bg-transparent text-xs focus:outline-none"
              style={{ color: theme.text }}
            />
            {query && (
              <button type="button" onClick={() => { setQuery(''); inputRef.current?.focus() }}
                className="flex-shrink-0" style={{ color: theme.t3 }}>
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Options */}
          <ul ref={listRef} className="max-h-64 overflow-y-auto py-1">
            {filtered.length === 0 && (
              <li className="px-3 py-3 text-center text-xs" style={{ color: theme.t3 }}>No match.</li>
            )}
            {filtered.map((opt, i) => {
              const active = i === hi
              const isSel = opt.value === value
              return (
                <li
                  key={opt.value}
                  onMouseEnter={() => setHi(i)}
                  onMouseDown={(e) => { e.preventDefault(); choose(opt) }}
                  className="flex cursor-pointer items-center justify-between gap-2 px-3 py-1.5 text-xs"
                  style={{ background: active ? theme.g100 : 'transparent', color: theme.text }}
                >
                  <span className="truncate">
                    {opt.label}
                    {opt.hint && <span className="ml-1.5" style={{ color: theme.t3 }}>{opt.hint}</span>}
                  </span>
                  {isSel && <Check className="h-3.5 w-3.5 flex-shrink-0" style={{ color: theme.orange }} />}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

export default SearchableSelect
