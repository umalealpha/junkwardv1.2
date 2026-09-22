'use client'
/** /m/privacy-notice — PUBLIC Policyholder Privacy Notice (DPA Part VIII; audit S-3).
 * Renders the notice served by /api/v1/privacy-notices/ so policyholders have a
 * reachable, linkable notice (from quotes, policy docs, claim emails). CFO 2026-07-20. */
import { useEffect, useState } from 'react'
import { C, serif, sans, h, headerPad } from '../../ui'

export default function PolicyholderPrivacyNotice() {
  const [html, setHtml] = useState<string>('')
  const [ver, setVer] = useState<string>('')
  useEffect(() => {
    fetch('/api/v1/privacy-notices/?audience=policyholder')
      .then(r => r.json())
      .then(d => { setHtml(d?.notice?.html || ''); setVer(d?.notice?.version || d?.version || '') })
      .catch(() => {})
  }, [])
  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Direct Insurance</span>
      </header>
      <main style={{ maxWidth: 720, margin: '0 auto', padding: '20px 20px 60px' }}>
        <h1 style={h(28)}>Policyholder Privacy Notice</h1>
        {ver && <p style={{ fontSize: 14, color: C.inkSoft, margin: '0 0 14px' }}>Version {ver}</p>}
        {html
          ? <div style={{ fontSize: 15, lineHeight: 1.6, color: C.ink }} dangerouslySetInnerHTML={{ __html: html }} />
          : <p style={{ fontSize: 15, color: C.inkSoft }}>Loading…</p>}
      </main>
    </div>
  )
}
