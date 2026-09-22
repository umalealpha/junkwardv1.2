'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  API_BASE, getToken, issueUnderwritingDocument, extractUnderwriting,
  getUnderwritingDocuments, openUnderwritingPdf, getUnderwritingToolToken,
} from '@/lib/api'
import type { UnderwritingDocument } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { BrokerLossRatioPanel } from '@/components/graphite/BrokerLossRatioPanel'
import { Card, CardContent } from '@/components/ui/card'
import { FileText, ExternalLink, RefreshCw } from 'lucide-react'

export default function UnderwritingPage() {
  const router = useRouter()
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [docs, setDocs] = useState<UnderwritingDocument[]>([])
  const [loading, setLoading] = useState(false)
  // The tool page carries the real signature/stamp, so it's gated: we fetch a
  // short-lived token (authed) and put it in the iframe src.
  const [toolSrc, setToolSrc] = useState<string | null>(null)

  const loadRegister = useCallback(() => {
    setLoading(true)
    getUnderwritingDocuments().then((r) => setDocs(r.results || [])).catch(() => {}).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    loadRegister()
    getUnderwritingToolToken()
      .then((t) => setToolSrc(`${API_BASE}/underwriting/tool/?t=${encodeURIComponent(t)}`))
      .catch(() => {})
  }, [router, loadRegister])

  // The embedded tool asks the parent to issue (parent-proxied so auth works
  // for both token and SSO users — the iframe never holds a credential).
  useEffect(() => {
    const onMsg = async (ev: MessageEvent) => {
      if (ev.origin !== window.location.origin) return
      const win = iframeRef.current?.contentWindow
      if (ev.data?.type === 'ad-issue') {
        try {
          const doc = await issueUnderwritingDocument(ev.data.payload)
          win?.postMessage({
            type: 'ad-issue-result', ok: true,
            emailed: !!doc.emailed, to: ev.data.payload?.email_to || '',
            detail: doc.email_error || '',
          }, window.location.origin)
          loadRegister()
        } catch (e) {
          win?.postMessage({
            type: 'ad-issue-result', ok: false,
            detail: e instanceof Error ? e.message : 'issue failed',
          }, window.location.origin)
        }
      } else if (ev.data?.type === 'ad-extract') {
        // Smart reader (DeepSeek → Gemini) — parent makes the authed call.
        try {
          const r = await extractUnderwriting({
            file_b64: ev.data.b64, filename: ev.data.name, content_type: ev.data.ctype,
          })
          win?.postMessage({ type: 'ad-extract-result', ...r }, window.location.origin)
        } catch (e) {
          win?.postMessage({
            type: 'ad-extract-result', ok: false,
            message: e instanceof Error ? e.message : 'reader failed',
          }, window.location.origin)
        }
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [loadRegister])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Underwriting Documents"
              breadcrumbs={[{ label: 'Underwriting' }, { label: 'Document Generator' }]} />
      <div className="flex-1 p-4 md:p-6 space-y-5">
        <div className="flex items-center gap-2 text-sm text-[#6B7280]">
          <FileText className="w-4 h-4 text-[#F47C20]" />
          Cover Notes &amp; WCA certificates — fill the form, pick a look, then
          <b className="mx-1 text-[#1D3270]">Save PDF</b> (prints) or
          <b className="mx-1 text-[#1D3270]">Email it</b> (issues to the register + emails the client).
        </div>

        {/* The ported tool — self-contained, pixel-faithful, in an iframe.
            Loads only once the short-lived tool token is fetched. */}
        <Card className="overflow-hidden p-0">
          {toolSrc ? (
            <iframe
              ref={iframeRef}
              src={toolSrc}
              title="Underwriting Document Generator"
              className="w-full block"
              style={{ height: 'calc(100vh - 230px)', minHeight: 620, border: 0 }}
            />
          ) : (
            <div className="flex items-center justify-center text-sm text-[#6B7280]"
                 style={{ height: 'calc(100vh - 230px)', minHeight: 620 }}>
              Loading the document generator…
            </div>
          )}
        </Card>

        {/* Broker loss ratios, from Graphite's nightly analytics. Underwriting
            had no view of which intermediaries actually make money. */}
        <BrokerLossRatioPanel />

        {/* Register of issued documents */}
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-[#1D3270]">Issued document register</h2>
              <button onClick={loadRegister} className="text-xs text-[#6B7280] hover:text-[#1D3270] flex items-center gap-1">
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
              </button>
            </div>
            {docs.length === 0 ? (
              <p className="text-sm text-[#9CA3AF]">No documents issued yet. Use <b>Email it</b> to issue one.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wider text-[#6B7280] border-b border-[#E5E7EB]">
                      <th className="py-2 pr-3">Type</th><th className="py-2 pr-3">Policy</th>
                      <th className="py-2 pr-3">Insured</th><th className="py-2 pr-3">Issued by</th>
                      <th className="py-2 pr-3">Emailed to</th><th className="py-2 pr-3">PDF</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#F1F3F7]">
                    {docs.map((d) => (
                      <tr key={d.id} className="hover:bg-[#FAFBFD]">
                        <td className="py-2 pr-3 whitespace-nowrap">{d.doctype_label}{d.fmt_label ? ` · ${d.fmt_label}` : ''}</td>
                        <td className="py-2 pr-3 font-mono text-xs text-[#1D3270]">{d.policy_number || '—'}</td>
                        <td className="py-2 pr-3 max-w-[220px] truncate">{d.insured_name || '—'}</td>
                        <td className="py-2 pr-3 text-[#6B7280]">{d.issued_by_name || '—'}</td>
                        <td className="py-2 pr-3 text-[#6B7280]">{d.emailed_to || '—'}</td>
                        <td className="py-2 pr-3">
                          {d.pdf_size ? (
                            <button onClick={() => openUnderwritingPdf(d.id).catch(() => {})}
                               className="text-[#B04E00] hover:underline inline-flex items-center gap-1">
                              Open <ExternalLink className="w-3 h-3" />
                            </button>
                          ) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
