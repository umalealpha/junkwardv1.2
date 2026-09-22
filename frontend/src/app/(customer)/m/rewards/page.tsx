'use client'

/** /m/rewards — Alpha Nexus rewards (Stitch design): navy discount hero with
 * the coin, then real points history. */
import { useEffect, useState } from 'react'
import { ArrowRight, ArrowUpRight, ArrowDownRight, Gift, Sparkles, X } from 'lucide-react'
import { getRewards, type RewardsResp } from '../../api'
import { C, serif, sans, h, card, pill, headerPad } from '../../ui'

export default function CustomerRewards() {
  const [data, setData] = useState<RewardsResp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showRedeem, setShowRedeem] = useState(false)
  useEffect(() => { getRewards().then(setData).catch(e => setError(e instanceof Error ? e.message : 'Failed to load')) }, [])

  const tier = data ? (data.member.tier.charAt(0).toUpperCase() + data.member.tier.slice(1)) : ''

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>

      <main style={{ padding: 16 }}>
        {error && (
          <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C', fontSize: 13, padding: '12px 14px', borderRadius: 12 }}>{error}</div>
        )}
        {!data && !error && (
          <div style={{ ...card, padding: 24, textAlign: 'center', color: C.inkSoft, fontSize: 14 }}>Loading your rewards…</div>
        )}
        {data && (
          <>
            <div style={{ background: `linear-gradient(160deg, ${C.navy2}, ${C.navy})`, borderRadius: 24, padding: 24, color: '#fff', boxShadow: '0 18px 40px rgba(15,28,44,0.28)' }}>
              <span style={pill('rgba(45,212,191,0.18)', C.tealLight)}>◈ {tier} Tier Member</span>
              <h1 style={{ ...h(34), color: '#fff', marginTop: 14 }}>{data.discount}% Premium<br />Discount</h1>
              <p style={{ color: 'rgba(255,255,255,0.62)', fontSize: 14, lineHeight: 1.5, margin: '10px 0 18px' }}>
                Your status grants exclusive access to premium rewards and optimized wellness sessions.
              </p>
              <div style={{ display: 'flex', gap: 36 }}>
                <div><p style={lblW}>TOTAL POINTS</p><p style={statW}>{data.member.points.toLocaleString()}</p></div>
                <div><p style={lblW}>NEXT TIER</p><p style={statW}>{data.nextTier}</p></div>
              </div>
              <button onClick={() => setShowRedeem(true)} style={{ marginTop: 18, padding: '13px 22px', borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.tealLight}, ${C.teal})`, color: C.navy, fontWeight: 700, fontSize: 15, display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontFamily: sans }}>
                Redeem Rewards <ArrowRight size={18} />
              </button>
              <div style={{ background: '#fff', borderRadius: 18, marginTop: 20, padding: 10, display: 'flex', justifyContent: 'center' }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/brand/nexus/rewards.png" alt="" width={150} height={150} />
              </div>
            </div>

            <div style={{ ...card, padding: 18, marginTop: 16 }}>
              <p style={{ color: C.inkSoft, fontSize: 12, margin: 0 }}>Membership level</p>
              <p style={{ fontFamily: serif, fontWeight: 700, fontSize: 22, color: C.ink, margin: '4px 0 0' }}>{tier} Nexus</p>
            </div>

            <h2 style={{ ...h(24), margin: '24px 0 12px' }}>Points History</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {data.transactions.length === 0 && <p style={{ color: C.inkSoft, fontSize: 13 }}>No activity yet — drive safely and scan to earn.</p>}
              {data.transactions.map((t, i) => {
                const earn = t.points >= 0
                return (
                  <div key={i} style={{ ...card, padding: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{ width: 38, height: 38, borderRadius: 12, background: earn ? 'rgba(45,212,191,0.14)' : 'rgba(244,166,35,0.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {earn ? <ArrowUpRight size={18} style={{ color: C.teal }} /> : <ArrowDownRight size={18} style={{ color: C.orangeDeep }} />}
                    </span>
                    <div style={{ flex: 1 }}>
                      <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: C.ink }}>{t.detail || t.program || t.kind}</p>
                      <p style={{ margin: 0, fontSize: 12, color: C.inkSoft }}>{t.occurredAt ? new Date(t.occurredAt).toLocaleDateString() : ''}</p>
                    </div>
                    <span style={{ fontWeight: 700, fontSize: 14, color: earn ? C.teal : C.orangeDeep }}>{earn ? '+' : ''}{t.points.toLocaleString()}</span>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </main>

      {/* Redeem popup. There is no live rewards catalogue yet, so the button
          cannot complete a redemption — instead of doing nothing (the bug a
          tester reported), it now explains the situation: points > 0 → points
          are safe, catalogue opening soon; points == 0 → how to earn first. */}
      {showRedeem && data && (
        <div role="dialog" aria-modal="true" aria-labelledby="redeem-title"
          onClick={() => setShowRedeem(false)}
          onKeyDown={e => { if (e.key === 'Escape') setShowRedeem(false) }}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()} style={{ ...card, maxWidth: 360, width: '100%', padding: 24, position: 'relative' }}>
            <button onClick={() => setShowRedeem(false)} aria-label="Close" autoFocus
              style={{ position: 'absolute', top: 14, right: 14, background: 'none', border: 'none', color: C.inkSoft, cursor: 'pointer' }}>
              <X size={20} />
            </button>
            {data.member.points > 0 ? (
              <>
                <div style={{ width: 52, height: 52, borderRadius: 16, background: 'rgba(45,212,191,0.16)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}>
                  <Gift size={26} style={{ color: C.teal }} />
                </div>
                <h2 id="redeem-title" style={{ ...h(22), marginBottom: 8 }}>Hang tight — rewards are coming</h2>
                <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.55, margin: 0 }}>
                  You have <b style={{ color: C.ink }}>{data.member.points.toLocaleString()} point{data.member.points === 1 ? '' : 's'}</b>, and they&apos;re safe. We&apos;re still negotiating the rewards and are busy finalising them — we&apos;ll let you know the moment you can redeem.
                </p>
              </>
            ) : (
              <>
                <div style={{ width: 52, height: 52, borderRadius: 16, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}>
                  <Sparkles size={26} style={{ color: C.orange }} />
                </div>
                <h2 id="redeem-title" style={{ ...h(22), marginBottom: 8 }}>No points yet</h2>
                <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.55, margin: 0 }}>
                  You have <b style={{ color: C.ink }}>0 points</b>. Earn points by taking a safe drive, doing a 30-second wellness scan, and logging your activities — then come back here to redeem.
                </p>
              </>
            )}
            <button onClick={() => setShowRedeem(false)}
              style={{ marginTop: 20, width: '100%', padding: '13px', borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: '#fff', fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans }}>
              Got it
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const lblW: React.CSSProperties = { color: 'rgba(255,255,255,0.5)', fontSize: 11, letterSpacing: '0.1em', margin: 0 }
const statW: React.CSSProperties = { fontFamily: serif, fontWeight: 800, fontSize: 24, color: '#fff', margin: '2px 0 0' }
