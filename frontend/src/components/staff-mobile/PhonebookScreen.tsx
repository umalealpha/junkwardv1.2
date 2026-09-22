'use client'

/** /m/staff/phonebook — the internal directory: tap to call or email a
 * colleague. Work contacts only. */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Phone, Mail, Search } from 'lucide-react'
import { sfetch } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

interface Person { name: string; title: string; department: string; phone: string; email: string }

export default function StaffPhonebook() {
  const base = useStaffBase()
  const [people, setPeople] = useState<Person[]>([])
  const [q, setQ] = useState('')

  useEffect(() => {
    const t = setTimeout(() => {
      sfetch<{ people: Person[] }>(`/staff/phonebook/${q ? `?q=${encodeURIComponent(q)}` : ''}`)
        .then(d => setPeople(d.people || [])).catch(() => setPeople([]))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Phonebook</h1>
      </header>

      <main style={{ padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: C.card, borderRadius: 999, padding: '4px 8px 4px 16px', boxShadow: '0 1px 3px rgba(15,28,44,0.08)', marginBottom: 14 }}>
          <Search size={16} style={{ color: C.inkSoft }} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Name, department or role…" aria-label="Search people" type="search"
            style={{ flex: 1, border: 'none', outline: 'none', padding: '11px 0', fontSize: 15, color: C.ink, background: 'transparent', fontFamily: sans }} />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {people.length === 0 && <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 20 }}>No matches.</p>}
          {people.map(p => (
            <div key={p.name + p.email} style={{ ...card, padding: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ width: 42, height: 42, borderRadius: 999, background: `linear-gradient(150deg, ${C.navy2}, ${C.navy})`, color: C.orange, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 800, fontSize: 14, flexShrink: 0 }}>
                {p.name.split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <b style={{ color: C.ink, fontSize: 14.5, display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</b>
                <span style={{ color: C.inkSoft, fontSize: 12 }}>{[p.title, p.department].filter(Boolean).join(' · ') || '—'}</span>
              </div>
              {p.phone && (
                <a href={`tel:${p.phone.replace(/\s+/g, '')}`} aria-label={`Call ${p.name}`}
                  style={{ width: 44, height: 44, borderRadius: 999, background: '#ECFDF5', color: '#047857', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Phone size={16} />
                </a>)}
              {p.email && (
                <a href={`mailto:${p.email}`} aria-label={`Email ${p.name}`}
                  style={{ width: 44, height: 44, borderRadius: 999, background: '#FFF7ED', color: C.orange, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <Mail size={16} />
                </a>)}
            </div>))}
        </div>
      </main>
    </div>
  )
}
