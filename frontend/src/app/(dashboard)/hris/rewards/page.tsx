'use client'

/**
 * /hris/rewards — native Rewards & Recognition surface.
 *
 * - Top-10 composite leaderboard from /hris/api/employees/.
 * - Kudos feed + send form via /hris/api/kudos/ (CFO directive 2026-05-20).
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, Award, Sparkles, Star, TrendingUp, Send, Loader2,
  AlertCircle, CheckCircle2, MessageSquare, Lock, Globe,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch, fetchHrisEmployees, type HrisEmployee } from '../_shared'
import { getMe } from '@/lib/api'

function avg(nums: number[]): number {
  if (!nums.length) return 0
  return nums.reduce((a, b) => a + b, 0) / nums.length
}

function compositeScore(e: HrisEmployee): number {
  const compVals = Object.values(e.comp || {}).filter(v => typeof v === 'number' && v > 0)
  const valVals = (e.vals || []).filter(v => typeof v === 'number' && v > 0)
  const potVals = (e.pot  || []).filter(v => typeof v === 'number' && v > 0)
  const parts = [avg(compVals), avg(valVals), avg(potVals)].filter(v => v > 0)
  return avg(parts)
}

interface Kudos {
  id: string
  sender: string
  receiver: string
  value_demonstrated: string
  message: string
  points: number
  is_public: boolean
  created_at: string
}

const VALUES = [
  ['integrity',   'Integrity'],
  ['excellence',  'Excellence'],
  ['ownership',   'Ownership'],
  ['teamwork',    'Teamwork'],
  ['innovation',  'Innovation'],
  ['customer',    'Customer focus'],
] as const

export default function HrisRewardsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const [employees, setEmployees] = useState<HrisEmployee[]>([])
  const [loading, setLoading] = useState(true)
  const [kudos, setKudos] = useState<Kudos[]>([])
  const [loadingKudos, setLoadingKudos] = useState(true)

  const [receiver, setReceiver] = useState('')
  const [value, setValue] = useState<typeof VALUES[number][0]>('teamwork')
  const [message, setMessage] = useState('')
  const [points, setPoints] = useState(1)
  const [isPublic, setIsPublic] = useState(true)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  // BUG c2219e99: don't offer the logged-in user as a kudos receiver.
  const [meName, setMeName] = useState('')

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    fetchHrisEmployees()
      .then(r => setEmployees(r.employees || []))
      .finally(() => setLoading(false))
    getMe().then(m => setMeName(`${m.first_name || ''} ${m.last_name || ''}`.trim())).catch(() => {})
    refreshKudos()
  }, [allowed])

  function refreshKudos() {
    setLoadingKudos(true)
    authedHrisFetch('/hris/api/kudos/')
      .then(async r => {
        if (r.ok) {
          const data = await r.json()
          setKudos(Array.isArray(data.kudos) ? data.kudos : [])
        }
      })
      .finally(() => setLoadingKudos(false))
  }

  async function sendKudos() {
    if (!receiver.trim() || !message.trim()) {
      setMsg({ ok: false, text: 'Pick a receiver and write a message.' }); return
    }
    setBusy(true); setMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/kudos/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          receiver: receiver.trim(),
          value_demonstrated: value,
          message: message.trim(),
          points,
          is_public: isPublic,
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setMsg({ ok: true, text: `Kudos sent to ${data.receiver}.` })
      setMessage(''); setReceiver(''); setPoints(1)
      refreshKudos()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setBusy(false)
    }
  }

  const ranked = useMemo(() => {
    return [...employees]
      .map(e => ({ ...e, score: compositeScore(e) }))
      .filter(e => e.score > 0)
      .sort((a, b) => b.score - a.score)
  }, [employees])

  const top10 = ranked.slice(0, 10)
  const orgAvg = avg(ranked.map(r => r.score))
  const above4 = ranked.filter(r => r.score >= 4).length

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Rewards" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Rewards' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <StatCard theme={theme} icon={Star} label="Org composite" value={orgAvg.toFixed(2)} suffix="/ 5" />
          <StatCard theme={theme} icon={TrendingUp} label="High performers" value={above4.toString()} suffix={`of ${ranked.length}`} />
          <StatCard theme={theme} icon={Award} label="Rated this cycle" value={ranked.length.toString()} suffix="reviews" />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          {/* Leaderboard */}
          <div className="rounded-2xl p-5"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold" style={{ color: theme.text }}>Top 10 performers</h3>
              <span className="text-xs" style={{ color: theme.t2 }}>From latest review</span>
            </div>
            <div className="space-y-2">
              {loading && [0, 1, 2, 3, 4].map(i => (
                <div key={i} className="h-12 rounded-lg animate-pulse" style={{ background: theme.g100 }} />
              ))}
              {!loading && top10.length === 0 && (
                <div className="py-8 text-center text-sm" style={{ color: theme.t2 }}>
                  No performance reviews on file yet.
                </div>
              )}
              {!loading && top10.map((e, i) => (
                <div key={e.id} className="flex items-center gap-3 px-3 py-2.5 rounded-lg"
                     style={{ background: i === 0 ? theme.oL : 'transparent' }}>
                  <div className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0"
                       style={{ background: i < 3 ? theme.orange : theme.g100, color: i < 3 ? '#fff' : theme.t2 }}>
                    {i + 1}
                  </div>
                  <div className="w-8 h-8 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                       style={{ background: theme.navy, color: '#fff' }}>
                    {e.img || e.nm.split(' ').map(p => p[0]).slice(0, 2).join('').toUpperCase()}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate" style={{ color: theme.text }}>{e.nm}</div>
                    <div className="text-xs truncate" style={{ color: theme.t2 }}>{e.ps} · {e.dp}</div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Sparkles className="w-3.5 h-3.5" style={{ color: theme.orange }} />
                    <span className="text-sm font-bold tabular-nums" style={{ color: theme.text }}>
                      {e.score.toFixed(2)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Send Kudos */}
          <div className="rounded-2xl p-5"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2 mb-3">
              <Award className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold" style={{ color: theme.text }}>Send a kudos</h3>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Receiver</label>
                <select value={receiver} onChange={e => setReceiver(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                        style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                  <option value="">Pick a colleague…</option>
                  {employees
                    .filter(emp => !meName || emp.nm.trim().toLowerCase() !== meName.toLowerCase())
                    .map(emp => (
                      <option key={emp.id} value={emp.nm}>{emp.nm} — {emp.ps}</option>
                    ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Value</label>
                  <select value={value} onChange={e => setValue(e.target.value as any)}
                          className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                          style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                    {VALUES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Points (1–5)</label>
                  <input type="number" min={1} max={5} value={points}
                         onChange={e => setPoints(Math.max(1, Math.min(5, Number(e.target.value) || 1)))}
                         className="w-full px-3 py-2 rounded-lg text-sm outline-none tabular-nums"
                         style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Message</label>
                <textarea value={message} onChange={e => setMessage(e.target.value.slice(0, 500))} rows={3}
                          placeholder="What did they do well? (≤500 chars)"
                          className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
                          style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                <div className="text-[10px] mt-0.5 text-right" style={{ color: theme.t2 }}>{message.length}/500</div>
              </div>
              <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: theme.t2 }}>
                <input type="checkbox" checked={isPublic} onChange={e => setIsPublic(e.target.checked)} />
                {isPublic
                  ? <><Globe className="w-3.5 h-3.5" /> Public — visible on the kudos feed</>
                  : <><Lock className="w-3.5 h-3.5" /> Private — only the receiver + their manager</>}
              </label>
              <div className="flex justify-end">
                <button type="button" onClick={sendKudos} disabled={busy}
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                        style={{ background: theme.orange, color: '#fff' }}>
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                  {busy ? 'Sending…' : 'Send kudos'}
                </button>
              </div>
              {msg && (
                <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                     style={{
                       background: msg.ok ? theme.okB : theme.erB,
                       color:      msg.ok ? theme.ok  : theme.er,
                       border:     `1px solid ${msg.ok ? theme.ok : theme.er}40`,
                     }}>
                  {msg.ok ? <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" /> :
                            <AlertCircle  className="w-4 h-4 flex-shrink-0 mt-0.5" />}
                  <div>{msg.text}</div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Kudos feed */}
        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
              <MessageSquare className="w-4 h-4" style={{ color: theme.orange }} />
              Kudos feed
            </h3>
            <span className="text-xs" style={{ color: theme.t2 }}>Last 50</span>
          </div>
          {loadingKudos && (
            <div className="space-y-2">
              {[0, 1, 2].map(i => (
                <div key={i} className="h-14 rounded-lg animate-pulse" style={{ background: theme.g100 }} />
              ))}
            </div>
          )}
          {!loadingKudos && kudos.length === 0 && (
            <div className="py-8 text-center text-sm" style={{ color: theme.t2 }}>
              No kudos yet. Be the first to recognize a colleague.
            </div>
          )}
          {!loadingKudos && kudos.length > 0 && (
            <div className="space-y-2">
              {kudos.map(k => (
                <div key={k.id} className="rounded-lg px-3 py-2.5"
                     style={{ background: theme.g100 }}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm" style={{ color: theme.text }}>
                        <strong>{k.sender}</strong>{' '}
                        <span style={{ color: theme.t2 }}>recognized</span>{' '}
                        <strong>{k.receiver}</strong>{' '}
                        <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold"
                              style={{ background: theme.orange + '22', color: theme.orange }}>
                          {k.value_demonstrated}
                        </span>
                      </div>
                      <p className="text-sm mt-1" style={{ color: theme.text }}>"{k.message}"</p>
                      <div className="text-[10px] mt-1.5 inline-flex items-center gap-2" style={{ color: theme.t2 }}>
                        {!k.is_public && <Lock className="w-3 h-3" />}
                        {new Date(k.created_at).toLocaleString('en-BW')}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0" style={{ color: theme.orange }}>
                      <Sparkles className="w-3.5 h-3.5" />
                      <span className="text-sm font-bold tabular-nums">{k.points}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}

function StatCard({ theme, icon: Icon, label, value, suffix }: { theme: any; icon: any; label: string; value: string; suffix?: string }) {
  return (
    <div className="rounded-2xl p-5"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>{label}</span>
        <Icon className="w-4 h-4" style={{ color: theme.orange }} />
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="text-3xl font-bold tabular-nums" style={{ color: theme.text }}>{value}</span>
        {suffix && <span className="text-sm" style={{ color: theme.t2 }}>{suffix}</span>}
      </div>
    </div>
  )
}
