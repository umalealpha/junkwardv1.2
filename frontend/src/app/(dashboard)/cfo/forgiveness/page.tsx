'use client'

/**
 * /cfo/forgiveness — who keeps asking to be forgiven (CFO 2026-09-09).
 *
 * "keep a track of them ... so we nail them on Nov performence feedback."
 *
 * A WATCHING screen, not a scoring one — it deducts nothing. It surfaces the
 * pattern the raw count hides: someone at their monthly limit, or filing at
 * 08:58 every day (following the rule to the letter, defeating it in spirit),
 * or filing too late to be forgiven at all. His-eyes-only, same lock as the
 * build log.
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card } from '@/components/ui/card'
import { useTheme } from '@/contexts/ThemeContext'
import { API_BASE } from '@/lib/api'
import { AlertTriangle, Clock, Loader2, ShieldAlert, Sparkles } from 'lucide-react'

interface Person {
  name: string; total: number; in_time: number; out_of_time: number
  worst_month: number; hit_the_cap: boolean; near_cutoff: number
  first: string | null; last: string | null
}
interface Report {
  from: string; to: string; people: Person[]
  totals: { people: number; notices: number; at_the_cap: number; filed_too_late: number }
  narrative?: string
}

function token(): string | null {
  try { const t = localStorage.getItem('alpha_token'); return t ? `Token ${t}` : null }
  catch { return null }
}

export default function ForgivenessPage() {
  const { theme: T } = useTheme()
  const [data, setData] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [months, setMonths] = useState(3)

  const load = useCallback(async () => {
    const auth = token()
    if (!auth) { setLoading(false); setDenied(true); return }
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/cfo/forgiveness/?months=${months}`, { headers: { Authorization: auth } })
      if (r.status === 403 || r.status === 401) { setDenied(true); setData(null) }
      else if (r.ok) { setDenied(false); setData(await r.json()) }
    } catch { /* keep last view */ }
    finally { setLoading(false) }
  }, [months])

  useEffect(() => { void load() }, [load])

  if (denied) {
    return (
      <div>
        <TopBar title="Forgiveness watch" />
        <div style={{ padding: 40, textAlign: 'center', color: T.t2 }}>
          <AlertTriangle className="w-8 h-8" style={{ margin: '0 auto 12px', color: T.wr }} />
          <div style={{ fontSize: 15, fontWeight: 600, color: T.text }}>For the CFO, the CEO and HR.</div>
          <div style={{ fontSize: 13, marginTop: 8, maxWidth: 420, marginInline: 'auto', lineHeight: 1.6 }}>
            This watch screen is for the CFO, the CEO and the HR team. If you are
            one of them and signed in as a shared account, sign in as yourself.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <TopBar title="Forgiveness watch" />
      <div style={{ padding: '18px 20px 40px', maxWidth: 1000, margin: '0 auto' }}>
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          <Stat T={T} label="People asking" value={data?.totals.people ?? 0} tone="inf" />
          <Stat T={T} label="At the monthly limit" value={data?.totals.at_the_cap ?? 0} tone="wr" big />
          <Stat T={T} label="Filed too late to count" value={data?.totals.filed_too_late ?? 0} tone="er" />
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12.5, color: T.t3 }}>Last</span>
            <select value={months} onChange={e => setMonths(+e.target.value)}
                    style={{ fontSize: 12.5, padding: '6px 9px', borderRadius: 8, color: T.text,
                             border: `1px solid ${T.cardBdr}`, background: 'transparent' }}>
              {[1, 3, 6, 12].map(m => <option key={m} value={m}>{m} month{m > 1 ? 's' : ''}</option>)}
            </select>
            {loading && <Loader2 className="w-4 h-4 animate-spin" style={{ color: T.t3 }} />}
          </div>
        </div>

        {data?.narrative && (
          <Card style={{ padding: '13px 15px', marginBottom: 16, borderLeft: `3px solid ${T.teal}` }}>
            <div style={{ display: 'flex', gap: 7, alignItems: 'center', marginBottom: 6, color: T.teal }}>
              <Sparkles className="w-4 h-4" />
              <span style={{ fontSize: 11.5, fontWeight: 700, letterSpacing: .3, textTransform: 'uppercase' }}>The pattern</span>
            </div>
            <div style={{ fontSize: 13.5, color: T.text, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{data.narrative}</div>
          </Card>
        )}

        <Card style={{ overflow: 'hidden' }}>
          <div style={{ padding: '11px 14px', background: T.g50, borderBottom: `1px solid ${T.cardBdr}`,
                        fontSize: 13, fontWeight: 700, color: T.text }}>
            Who is asking — worst month first
          </div>
          {!data?.people.length
            ? <div style={{ padding: 16, fontSize: 12.5, color: T.t3 }}>Nobody has used the &ldquo;running late&rdquo; button yet.</div>
            : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                  <thead>
                    <tr style={{ color: T.t3, textAlign: 'left' }}>
                      {['Person', 'Times', 'Worst month', 'On time', 'Too late', 'Filed near cutoff'].map(h =>
                        <th key={h} style={{ padding: '8px 12px', borderBottom: `2px solid ${T.cardBdr}`, whiteSpace: 'nowrap' }}>{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {data.people.map(p => (
                      <tr key={p.name} style={{ borderBottom: `1px solid ${T.cardBdr}` }}>
                        <td style={{ padding: '8px 12px', color: T.text, fontWeight: p.hit_the_cap ? 700 : 400 }}>
                          {p.hit_the_cap && <ShieldAlert className="w-3.5 h-3.5" style={{ display: 'inline', marginRight: 5, color: T.wr, verticalAlign: -2 }} />}
                          {p.name}
                        </td>
                        <td style={{ padding: '8px 12px' }}>{p.total}</td>
                        <td style={{ padding: '8px 12px', color: p.hit_the_cap ? T.wr : T.t2, fontWeight: p.hit_the_cap ? 700 : 400 }}>{p.worst_month}</td>
                        <td style={{ padding: '8px 12px', color: T.ok }}>{p.in_time}</td>
                        <td style={{ padding: '8px 12px', color: p.out_of_time ? T.er : T.t3 }}>{p.out_of_time}</td>
                        <td style={{ padding: '8px 12px' }}>
                          {p.near_cutoff > 0
                            ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: T.wr }}>
                                <Clock className="w-3.5 h-3.5" />{p.near_cutoff}</span>
                            : <span style={{ color: T.t3 }}>0</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>

        <div style={{ marginTop: 16, fontSize: 11.5, color: T.t3, lineHeight: 1.65 }}>
          This is a watching screen — it changes nobody&rsquo;s score. &ldquo;Worst month&rdquo; is
          the most notices in any single month; three is the limit. &ldquo;Filed near cutoff&rdquo;
          counts notices sent in the last 15 minutes before 09:00 — following the rule to the letter.
          Someone filing too late is already not being forgiven; they need telling, not nailing.
        </div>
      </div>
    </div>
  )
}

function Stat({ T, label, value, tone, big }: { T: any; label: string; value: number; tone: 'ok' | 'wr' | 'er' | 'inf'; big?: boolean }) {
  const c = tone === 'ok' ? T.ok : tone === 'wr' ? T.wr : tone === 'er' ? T.er : T.inf
  return (
    <Card style={{ padding: '12px 15px', minWidth: 150 }}>
      <div style={{ fontSize: 11, letterSpacing: .3, textTransform: 'uppercase', fontWeight: 700, color: T.t3, marginBottom: 5 }}>{label}</div>
      <div style={{ fontSize: big ? 30 : 22, fontWeight: 700, lineHeight: 1, color: c }}>{value}</div>
    </Card>
  )
}
