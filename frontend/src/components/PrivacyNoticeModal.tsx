'use client'

/**
 * PrivacyNoticeModal — one-page staff privacy notice shown as a login pop-up
 * (CFO directive 2026-07-09).
 *
 * Mounted once in the dashboard layout. On every load it asks the server
 * whether the signed-in user has acknowledged the CURRENT notice version; if
 * not, it shows a NON-dismissible modal (no close button, no backdrop-dismiss)
 * that only clears once the user ticks the box and confirms. The confirmation
 * is recorded server-side (who + when + version) and surfaced to HR in the
 * daily HRIS email so they can see who has not signed yet.
 *
 * Consent-capture, not a security gate — so the enforcement is "it re-appears
 * until you sign", not a hard lock. Bump NOTICE_VERSION server-side to require
 * everyone to sign again.
 */
import { useEffect, useState } from 'react'
import { getPrivacyNotice, ackPrivacyNotice, type PrivacyNotice } from '@/lib/api'
import { ShieldCheck, Loader2 } from 'lucide-react'

export function PrivacyNoticeModal() {
  const [notice, setNotice] = useState<PrivacyNotice | null>(null)
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    getPrivacyNotice()
      .then(n => { setNotice(n); if (!n.acknowledged) setVisible(true) })
      .catch(() => { /* never block the app if the check fails */ })
  }, [])

  async function agree() {
    if (!agreed || busy) return
    setBusy(true); setErr(null)
    try {
      await ackPrivacyNotice()
      setVisible(false)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save — please try again.')
    } finally { setBusy(false) }
  }

  if (!visible || !notice) return null

  return (
    <div className="fixed inset-0 bg-black/70 z-[90] flex items-center justify-center p-4">
      {/* Fixed light "paper" card in ALL themes. This modal used dark: variants,
          which follow the OS colour-scheme (NOT the app theme) — so on a
          dark-mode laptop the card went dark navy with navy text = unreadable
          (CEO, 2026-08-13), while the rest of Omni stayed light. A white
          declaration card reads correctly under every theme; the black/70
          backdrop dims whatever dashboard sits behind it. */}
      <div className="bg-white rounded-2xl w-full max-w-2xl shadow-2xl flex flex-col max-h-[88vh]">
        <div className="flex items-center gap-2 px-6 pt-6 pb-3 border-b border-[#E5E7EB]">
          <ShieldCheck className="w-5 h-5 text-[#F4A623] shrink-0" />
          <h2 className="text-lg font-bold text-[#0D1B2A]">{notice.title}</h2>
        </div>

        <div
          className="px-6 py-4 overflow-y-auto text-sm leading-relaxed text-[#374151]
                     [&_p]:mb-3 [&_p]:text-[#374151] [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:mb-3 [&_li]:mt-1
                     [&_b]:text-[#0D1B2A] [&_hr]:my-4 [&_hr]:border-[#E5E7EB]"
          dangerouslySetInnerHTML={{ __html: notice.html }}
        />

        <div className="px-6 pt-3 pb-6 border-t border-[#E5E7EB]">
          <label className="flex items-start gap-3 cursor-pointer select-none mb-4">
            <input
              type="checkbox" checked={agreed}
              onChange={e => setAgreed(e.target.checked)}
              className="mt-0.5 w-4 h-4 accent-[#F4A623]"
            />
            <span className="text-sm text-[#0D1B2A]">
              {notice.checkbox_label ?? 'I have read and understood this notice.'}
            </span>
          </label>
          {err && <p className="text-xs text-red-700 mb-2">{err}</p>}
          <button
            onClick={agree} disabled={!agreed || busy}
            className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold text-white transition-colors"
            style={{ background: agreed ? '#0D1B2A' : '#9CA3AF', cursor: agreed ? 'pointer' : 'not-allowed' }}
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            {notice.cta_label ?? 'I have read and understood'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default PrivacyNoticeModal
