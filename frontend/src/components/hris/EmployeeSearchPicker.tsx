'use client'

/**
 * EmployeeSearchPicker — replaces the plain employee <select> on HRIS
 * Amendments (CFO file 2 Medium, 18-Sep-2026).
 *
 *   • type a name, email or employee number to filter (the list is the one the
 *     page already loaded, so company scope is exactly the server's);
 *   • WAI-ARIA combobox: ↑/↓ move, Enter picks, Esc closes;
 *   • a typed name never selects anyone — only an explicit pick does, so two
 *     people with the same name cannot be swapped;
 *   • once picked, a confirm card shows name, email, employee number and
 *     entity, with a "same name" warning when the name is shared.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Search, X, AlertTriangle } from 'lucide-react'
import { sameNameCount, searchEmployees } from '@/lib/employeeSearch'

export type PickerEmployee = {
  eid?: string; nm: string; email?: string; en?: string
  ps?: string; dp?: string; company?: string
}

export default function EmployeeSearchPicker({ employees, value, onChange, dark }: {
  employees: PickerEmployee[]; value: string; onChange: (eid: string) => void; dark: boolean
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const changeRef = useRef<HTMLButtonElement>(null)
  const justPicked = useRef(false)
  const [announce, setAnnounce] = useState('')

  const selected = useMemo(() => employees.find(e => e.eid === value), [employees, value])
  const results = useMemo(() => searchEmployees(employees, query), [employees, query])
  const twins = selected ? sameNameCount(employees, selected.nm) : 0

  // The search box unmounts on a pick, so move focus to the card's Change
  // button instead of dropping it on <body> (review, 18-Sep-2026).
  useEffect(() => {
    if (selected && justPicked.current) { justPicked.current = false; changeRef.current?.focus() }
  }, [selected])

  // One live region that is ALWAYS mounted, so screen readers hear both the
  // result count while typing and the confirmation after a pick.
  useEffect(() => {
    if (selected) setAnnounce(`Selected ${selected.nm}, ${selected.email || 'no email'}, number ${selected.en || 'none'}${twins > 1 ? `. ${twins} people share this name` : ''}.`)
    else if (open) setAnnounce(results.length ? `${results.length} ${results.length === 1 ? 'result' : 'results'}` : 'No matching employee')
  }, [selected, open, results.length, twins])

  const inp = dark ? 'bg-white/5 border-white/10 text-white' : 'bg-white border-slate-300 text-slate-900'
  const pop = dark ? 'bg-[#0D1B2A] border-white/10' : 'bg-white border-slate-200'
  const sub = dark ? 'text-slate-400' : 'text-slate-500'

  function pick(eid: string | undefined) {
    if (!eid) return
    justPicked.current = true
    onChange(eid); setQuery(''); setOpen(false)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); setActive(0); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, results.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); pick(results[active]?.eid) }
    else if (e.key === 'Escape') { e.preventDefault(); setOpen(false) }
  }

  const activeId = !selected && open && results[active] ? `${listId}-opt-${active}` : undefined
  const live = <p className="sr-only" aria-live="polite" aria-atomic="true">{announce}</p>

  // ONE live region for both views: if it remounted on a pick, the
  // confirmation (and the same-name warning) would not be spoken.
  return <>{live}{selected ? (
      <div className={`rounded-lg border px-3 py-2 mb-4 ${inp}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-medium truncate">{selected.nm}</p>
            <p className={`text-xs ${sub} break-all`}>
              {selected.email || 'no email on record'} · No. {selected.en || '—'} · {selected.company || '—'}
            </p>
            <p className={`text-xs ${sub}`}>{[selected.ps, selected.dp].filter(Boolean).join(' · ')}</p>
          </div>
          <button ref={changeRef} type="button" onClick={() => { onChange(''); setTimeout(() => inputRef.current?.focus(), 0) }}
            className="shrink-0 inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[#F4A623] hover:bg-[#F4A623]/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#F4A623]"
            aria-label={`Change employee (currently ${selected.nm})`}>
            <X size={14} /> Change
          </button>
        </div>
        {twins > 1 && (
          <p className="mt-2 flex items-center gap-1 text-xs text-amber-500">
            <AlertTriangle size={14} /> {twins} people share this name — check the email and number above before submitting.
          </p>
        )}
      </div>
    ) : (
    <div className="relative mb-4">
      <div className="relative">
        <Search size={16} className={`absolute left-3 top-1/2 -translate-y-1/2 ${sub}`} aria-hidden />
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          aria-label="Search employee by name, email or employee number"
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true); setActive(0) }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          placeholder="Search by name, email or employee number"
          className={`w-full rounded-lg border pl-9 pr-3 py-2 ${inp}`}
        />
      </div>
      {open && results.length === 0 && (
        <div className={`absolute z-20 mt-1 w-full rounded-lg border px-3 py-2 text-sm shadow-lg ${pop} ${sub}`}>
          No employee matches “{query}”.
        </div>
      )}
      {open && results.length > 0 && (
        <ul id={listId} role="listbox" aria-label="Employees"
          className={`absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border shadow-lg ${pop}`}>
          {results.map((e, i) => {
            const twin = sameNameCount(employees, e.nm) > 1
            return (
              <li key={e.eid} id={`${listId}-opt-${i}`} role="option" aria-selected={i === active}
                onMouseDown={ev => { ev.preventDefault(); pick(e.eid) }}
                onMouseEnter={() => setActive(i)}
                className={`cursor-pointer px-3 py-2 text-sm ${i === active ? 'bg-[#F4A623]/15' : ''}`}>
                <span className="font-medium">{e.nm}</span>
                {twin && <span className="ml-2 text-[11px] text-amber-500">same name</span>}
                <span className={`block text-xs ${sub} break-all`}>
                  {e.email || 'no email'} · No. {e.en || '—'} · {e.company || '—'}{e.dp ? ` · ${e.dp}` : ''}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )}</>
}
