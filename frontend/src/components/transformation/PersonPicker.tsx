'use client'

import { useId, useMemo, useRef, useState } from 'react'
import type { Theme } from '@/lib/themes'
import type { TransformationPerson } from '@/lib/api'
import { Search, User } from 'lucide-react'

interface PersonPickerProps {
  theme: Theme
  people: TransformationPerson[]
  loading: boolean
  value: TransformationPerson | null
  onSelect: (person: TransformationPerson) => void
  /** Emails already on this step — offered but visually marked, never hidden,
   *  so re-picking someone to change their role/date still works. */
  currentEmails?: string[]
  label: string
}

const MAX_RESULTS = 8

// Accessible combobox for a couple hundred people — searches name, email and
// department client-side (cheap at this size) rather than round-tripping the
// server per keystroke. Full keyboard support: type to filter, Arrow keys to
// move, Enter to pick, Escape to close.
export function PersonPicker({ theme, people, loading, value, onSelect, currentEmails = [], label }: PersonPickerProps) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const listboxId = useId()
  const inputRef = useRef<HTMLInputElement>(null)

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    const pool = q
      ? people.filter((p) =>
          p.name.toLowerCase().includes(q)
          || p.email.toLowerCase().includes(q)
          || p.department.toLowerCase().includes(q))
      : people
    return pool.slice(0, MAX_RESULTS)
  }, [people, query])

  const pick = (person: TransformationPerson) => {
    onSelect(person)
    setQuery('')
    setOpen(false)
    setActiveIndex(0)
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      setOpen(true)
      return
    }
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (results[activeIndex]) pick(results[activeIndex])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="relative">
      <label htmlFor={`${listboxId}-input`} className="sr-only">{label}</label>
      <div className="relative">
        <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: theme.t3 }} />
        <input
          id={`${listboxId}-input`}
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          autoComplete="off"
          placeholder={value ? value.name : loading ? 'Loading people…' : 'Search name, email or department…'}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); setActiveIndex(0) }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          disabled={loading}
          className="w-full h-9 rounded-md pl-8 pr-2 text-sm"
          style={{ border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text }}
        />
      </div>
      {open && (
        <ul
          id={listboxId}
          role="listbox"
          aria-label={label}
          className="absolute z-20 mt-1 w-full max-h-64 overflow-auto rounded-md shadow-lg text-sm"
          style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
        >
          {results.length === 0 && (
            <li className="px-3 py-2" style={{ color: theme.t3 }}>
              {loading ? 'Loading…' : 'No match.'}
            </li>
          )}
          {results.map((p, idx) => {
            const already = currentEmails.includes(p.email)
            return (
              <li
                key={p.email}
                role="option"
                aria-selected={idx === activeIndex}
                // onMouseDown (not onClick) so this fires before the input's onBlur closes the list.
                onMouseDown={(e) => { e.preventDefault(); pick(p) }}
                className="px-3 py-2 cursor-pointer flex items-center justify-between gap-2"
                style={{ background: idx === activeIndex ? theme.oL : 'transparent' }}
              >
                <span className="flex items-center gap-2 min-w-0">
                  <User className="w-3.5 h-3.5 flex-shrink-0" style={{ color: theme.t3 }} />
                  <span className="truncate">
                    <span style={{ color: theme.text }}>{p.name}</span>
                    <span className="ml-1.5" style={{ color: theme.t3 }}>{p.department || 'no dept on file'}</span>
                  </span>
                </span>
                {already && (
                  <span className="text-xs flex-shrink-0" style={{ color: theme.t3 }}>already on step</span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
