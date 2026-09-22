'use client'

/*
 * /unicoin — canonical home of the UniCoin Instant Insurance agent-commission
 * portal (CFO 2026-07-27: "it sits in omni.alphadirect.co.bw, under unicoin,
 * but it says Instant Insurance agents"). The portal itself lives in the
 * agent-portal page module; this route re-presents it under the UniCoin path so
 * it reads as UniCoin's, not Alpha Direct's. The legacy /agent-portal path still
 * resolves for any old links.
 */
import AgentPortalPage from '../agent-portal/page'

export default function UnicoinPortalPage() {
  return <AgentPortalPage />
}
