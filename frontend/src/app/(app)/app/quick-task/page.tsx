'use client'
// Omni Mobile — Quick Task (Workstream G).
// Tap a colleague → type the work → send. Reuses the existing task system:
// GET /tasks/assignees/ (active staff, no leavers/bots) and POST /tasks/. A
// client idempotency key means a double-tap / retry creates ONE task, never two.
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft, Search, Check, Loader2, ListChecks } from 'lucide-react'
import { afetch } from '../../api'
import { C, card, serif, headerPad, h } from '../../ui'

interface Assignee { username: string; full_name: string; title: string; department: string; tier?: number; recent_count?: number }
interface CreatedTask { id: string; title: string; status: string; assignee: string; deduped?: boolean }

type Priority = 'normal' | 'high' | 'urgent'

function newKey(): string {
  try { return crypto.randomUUID() } catch { return `qt-${Date.now()}-${Math.random().toString(16).slice(2)}` }
}
function iso(d: Date): string {
  // LOCAL calendar date (Africa/Gaborone), never toISOString() — that is UTC and
  // between 00:00 and 01:59 Botswana it stamps yesterday, so "Today" would land
  // the task overdue and mis-count the CFO 48h notice guard.
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
function nextFriday(): Date {
  const d = new Date(); const add = (5 - d.getDay() + 7) % 7 || 7; d.setDate(d.getDate() + add); return d
}

export default function QuickTask() {
  const [people, setPeople] = useState<Assignee[] | null>(null)
  const [peopleErr, setPeopleErr] = useState(false)
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<Assignee | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [due, setDue] = useState<string>('')          // ISO date or ''
  const [priority, setPriority] = useState<Priority>('normal')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState<CreatedTask | null>(null)

  useEffect(() => {
    afetch<{ assignees: Assignee[] }>('/tasks/assignees/')
      .then(r => { setPeople(r.assignees || []); setPeopleErr(false) })
      .catch(() => { setPeople([]); setPeopleErr(true) })  // a load failure is an error, not "no colleagues"
  }, [])

  const filtered = useMemo(() => {
    const list = people || []
    const s = q.trim().toLowerCase()
    if (!s) return list
    return list.filter(p => p.full_name.toLowerCase().includes(s) || p.username.toLowerCase().includes(s))
  }, [people, q])

  const choose = (p: Assignee) => { setPicked(p); setKey(newKey()); setErr('') }

  const send = async () => {
    if (!picked || !title.trim() || busy) return
    setBusy(true); setErr('')
    try {
      const t = await afetch<CreatedTask>('/tasks/', {
        method: 'POST',
        body: JSON.stringify({
          assignee_username: picked.username,
          title: title.trim(),
          body: body.trim() || undefined,
          due_at: due || undefined,
          priority,
          source: 'quick_task',
          client_key: key,
        }),
      })
      setDone(t)
    } catch (e) {
      setErr((e as Error)?.message || 'Could not send the task. Try again.')
    } finally { setBusy(false) }
  }

  // ---- success ----
  if (done) {
    return (
      <main>
        <Header />
        <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 12 }}>
          <div className="oa-rise" style={{ ...card, padding: 22, textAlign: 'center' }}>
            <div style={{ display: 'grid', placeItems: 'center', width: 56, height: 56, borderRadius: 999, background: 'rgba(4,120,87,0.12)', margin: '0 auto 12px' }}>
              <Check size={28} color={C.green} />
            </div>
            <div style={{ fontFamily: serif, fontSize: 20, fontWeight: 700, color: C.head }}>
              {done.deduped ? 'Already sent' : 'Task sent'}
            </div>
            <div style={{ color: C.inkSoft, fontSize: 14, marginTop: 4 }}>
              {picked?.full_name} will get it in their Omni inbox and by email.
            </div>
          </div>
          <Link href="/app" className="oa-press" style={{ ...card, display: 'flex', alignItems: 'center', gap: 12, padding: 16, textDecoration: 'none', color: C.ink }}>
            <ListChecks size={20} color={C.head} /><span style={{ flex: 1, fontWeight: 600 }}>Back to home</span>
          </Link>
          <button onClick={() => { setDone(null); setPicked(null); setTitle(''); setBody(''); setDue(''); setPriority('normal'); setQ('') }}
            className="oa-press" style={{ minHeight: 48, borderRadius: 12, border: `1px solid ${C.line}`, background: C.card, color: C.head, fontWeight: 700, fontSize: 15 }}>
            Send another
          </button>
        </section>
      </main>
    )
  }

  // ---- compose (person chosen) ----
  if (picked) {
    return (
      <main>
        <Header onBack={() => setPicked(null)} />
        <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 12 }}>
          <div className="oa-rise" style={{ ...card, padding: 16 }}>
            <div style={{ fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.inkSoft, fontWeight: 600 }}>To</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
              <Avatar name={picked.full_name} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 16 }}>{picked.full_name}</div>
                <div style={{ fontSize: 12.5, color: C.inkSoft }}>{[picked.title, picked.department].filter(Boolean).join(' · ')}</div>
              </div>
              <button onClick={() => setPicked(null)} className="oa-press" style={{ minHeight: 40, padding: '0 12px', borderRadius: 10, border: `1px solid ${C.line}`, background: C.card, color: C.inkSoft, fontWeight: 600, fontSize: 13 }}>Change</button>
            </div>
          </div>

          <div className="oa-rise oa-rise-2" style={{ ...card, padding: 16, display: 'grid', gap: 12 }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={h(15)}>What do you need done?</span>
              <textarea value={title} onChange={e => setTitle(e.target.value)} rows={2} autoFocus
                placeholder="e.g. Send me the June reconciliations"
                style={{ resize: 'none', width: '100%', boxSizing: 'border-box', padding: 12, borderRadius: 12, border: `1px solid ${C.line}`, fontSize: 16, fontFamily: 'inherit', color: C.ink }} />
            </label>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 13, color: C.inkSoft, fontWeight: 600 }}>More detail (optional)</span>
              <textarea value={body} onChange={e => setBody(e.target.value)} rows={3}
                placeholder="Anything that helps them do it"
                style={{ resize: 'none', width: '100%', boxSizing: 'border-box', padding: 12, borderRadius: 12, border: `1px solid ${C.line}`, fontSize: 15, fontFamily: 'inherit', color: C.ink }} />
            </label>

            <div style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 13, color: C.inkSoft, fontWeight: 600 }}>Due</span>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {[
                  { k: '', label: 'No date' },
                  { k: iso(new Date()), label: 'Today' },
                  { k: iso(new Date(Date.now() + 864e5)), label: 'Tomorrow' },
                  { k: iso(nextFriday()), label: 'Friday' },
                ].map(o => <Chip key={o.label} on={due === o.k} onClick={() => setDue(o.k)}>{o.label}</Chip>)}
              </div>
            </div>

            <div style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 13, color: C.inkSoft, fontWeight: 600 }}>Priority</span>
              <div style={{ display: 'flex', gap: 8 }}>
                {(['normal', 'high', 'urgent'] as Priority[]).map(p =>
                  <Chip key={p} on={priority === p} onClick={() => setPriority(p)}>{p[0].toUpperCase() + p.slice(1)}</Chip>)}
              </div>
            </div>
          </div>

          {err && <div role="alert" style={{ ...card, padding: 14, borderLeft: `4px solid ${C.red}`, color: C.red, fontSize: 14 }}>{err}</div>}

          <button onClick={send} disabled={!title.trim() || busy} className="oa-press"
            style={{ minHeight: 52, borderRadius: 14, border: 0, background: title.trim() && !busy ? C.orange : C.line, color: title.trim() && !busy ? C.navy : C.inkSoft, fontWeight: 800, fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            {busy ? <Loader2 size={20} className="oa-spin" /> : null}{busy ? 'Sending…' : 'Send task'}
          </button>
        </section>
      </main>
    )
  }

  // ---- pick a person ----
  return (
    <main>
      <Header />
      <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 12 }}>
        <div className="oa-rise" style={{ ...card, padding: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Search size={18} color={C.inkSoft} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search a colleague" autoFocus
            style={{ flex: 1, border: 0, outline: 'none', fontSize: 16, color: C.ink, background: 'transparent' }} />
        </div>
        <div className="oa-rise oa-rise-2" style={{ ...card, padding: 0, overflow: 'hidden' }}>
          {people === null && <div style={{ padding: 20, textAlign: 'center', color: C.inkSoft }}><Loader2 size={20} className="oa-spin" /></div>}
          {peopleErr && <div role="alert" style={{ padding: 20, textAlign: 'center', color: C.red, fontSize: 14 }}>Couldn't load your colleagues. Check your connection and try again.</div>}
          {people !== null && !peopleErr && filtered.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: C.inkSoft, fontSize: 14 }}>No colleague matches “{q}”.</div>}
          {filtered.map((p, i) => (
            <button key={p.username} onClick={() => choose(p)} className="oa-press"
              style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', border: 0, borderBottom: i === filtered.length - 1 ? undefined : `1px solid ${C.line}`, background: C.card, textAlign: 'left', cursor: 'pointer' }}>
              <Avatar name={p.full_name} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 600, fontSize: 15, color: C.ink }}>{p.full_name}</span>
                <span style={{ display: 'block', fontSize: 12.5, color: C.inkSoft, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{[p.title, p.department].filter(Boolean).join(' · ')}</span>
              </span>
            </button>
          ))}
        </div>
      </section>
    </main>
  )
}

function Header({ onBack }: { onBack?: () => void }) {
  return (
    <header style={{ background: C.navy, color: '#fff', padding: headerPad, paddingBottom: 44, display: 'flex', alignItems: 'center', gap: 10 }}>
      {onBack
        ? <button onClick={onBack} aria-label="Back" className="oa-press" style={{ border: 0, background: 'transparent', color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}><ChevronLeft size={24} /></button>
        : <Link href="/app" aria-label="Home" className="oa-press" style={{ color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}><ChevronLeft size={24} /></Link>}
      <div>
        <h1 style={{ fontFamily: serif, fontSize: 24, fontWeight: 700, lineHeight: 1, margin: 0 }}>Quick task</h1>
        <div style={{ opacity: 0.8, fontSize: 13, marginTop: 4 }}>Hand a job to a colleague</div>
      </div>
    </header>
  )
}

function Avatar({ name }: { name: string }) {
  const initials = name.trim().split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase()
  return <span style={{ display: 'grid', placeItems: 'center', width: 40, height: 40, borderRadius: 999, background: 'var(--ao-navy-wash, rgba(11,11,59,0.06))', color: C.head, fontWeight: 700, fontSize: 14, flexShrink: 0 }}>{initials}</span>
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="oa-press" style={{ minHeight: 40, padding: '0 14px', borderRadius: 999, fontSize: 14, fontWeight: 600, cursor: 'pointer',
      border: `1px solid ${on ? C.navy : C.line}`, background: on ? C.navy : C.card, color: on ? '#fff' : C.ink }}>{children}</button>
  )
}
