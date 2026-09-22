'use client'

/**
 * KYC & evidence register for one counterparty.
 * Arun P. Iyer's control brief §5, via the CFO, 16-Sep-2026.
 *
 * The design job here is one thing above all: an empty file must look WORSE
 * than a full one. Paul Beka's report — incomplete offshore financials, Munich
 * Re / Kuwait Re / GIC Re information outstanding — is the story this panel
 * exists to keep visible. So the gaps are stated first, in red, before the list
 * of what IS held, and a counterparty with nothing on file can never render as
 * a quiet empty table.
 *
 * Two smaller rules that matter as much:
 *   * "Uploaded" is drawn differently from "verified". Attaching a PDF is not
 *     evidence that anybody read it.
 *   * The file opens through the streaming endpoint, never a /media/ URL —
 *     that path is not served in production and would give a broken link.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  getReinsurerDocuments, uploadReinsurerDocument, verifyReinsurerDocument,
  openReinsurerDocument,
  type ReinsurerDocumentRegister,
} from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ShieldCheck, ShieldAlert, Upload, Check, X } from 'lucide-react'

const NAVY = '#0D1B2A'

const PILL: Record<string, { bg: string; fg: string }> = {
  verified: { bg: '#D1FAE5', fg: '#065F46' },
  pending:  { bg: '#FEF3C7', fg: '#92400E' },
  rejected: { bg: '#FEE2E2', fg: '#991B1B' },
}

function fmtDate(iso: string | null) {
  return iso
    ? new Date(iso).toLocaleDateString('en-GB',
        { day: '2-digit', month: 'short', year: 'numeric' })
    : '—'
}

export function KycPanel({ reinsurerId }: { reinsurerId: string }) {
  const [reg, setReg] = useState<ReinsurerDocumentRegister | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState('')
  const [expiry, setExpiry] = useState('')
  const [source, setSource] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await getReinsurerDocuments(reinsurerId)
      setReg(data)
      if (!kind && data.kinds.length) setKind(data.kinds[0].value)
    } catch (e) {
      setErr((e as Error).message || 'Could not load the document register.')
    }
    // `kind` is seeded once and then owned by the person — reloading must not
    // reset what they picked mid-upload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reinsurerId])

  useEffect(() => { load() }, [load])

  const upload = async () => {
    if (!file || !kind) return
    setBusy(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('kind', kind)
      if (expiry) form.append('expiry_date', expiry)
      if (source) form.append('source', source)
      await uploadReinsurerDocument(reinsurerId, form)
      setFile(null); setExpiry(''); setSource('')
      await load()
    } catch (e) {
      setErr((e as Error).message || 'The upload was refused.')
    } finally {
      setBusy(false)
    }
  }

  const decide = async (id: string, decision: 'verified' | 'rejected') => {
    setBusy(true); setErr('')
    try {
      const note = decision === 'rejected'
        ? (window.prompt('Why is it rejected?') || '')
        : ''
      if (decision === 'rejected' && !note.trim()) {
        setErr('A rejection has to say why.')
        return
      }
      await verifyReinsurerDocument(id, decision, note)
      await load()
    } catch (e) {
      setErr((e as Error).message || 'That could not be recorded.')
    } finally {
      setBusy(false)
    }
  }

  const gaps = reg?.gaps ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2" style={{ color: NAVY }}>
          {gaps.length === 0
            ? <ShieldCheck className="w-5 h-5 text-[#059669]" />
            : <ShieldAlert className="w-5 h-5 text-[#DC2626]" />}
          KYC &amp; evidence
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {err && (
          <div className="rounded border px-3 py-2 text-sm"
               style={{ background: '#FEE2E2', borderColor: '#FECACA', color: '#991B1B' }}>
            {err}
          </div>
        )}

        {/* The gaps come FIRST. A missing file is the finding, not the absence
            of one, and it must never read as a quiet empty table. */}
        {gaps.length > 0 ? (
          <div className="rounded border px-3 py-3"
               style={{ background: '#FEF2F2', borderColor: '#FECACA' }}>
            <p className="text-sm font-semibold" style={{ color: '#991B1B' }}>
              {gaps.length} required item{gaps.length === 1 ? '' : 's'} outstanding —
              this counterparty cannot be approved or used on a new placement.
            </p>
            <ul className="mt-2 space-y-1 text-sm" style={{ color: '#991B1B' }}>
              {gaps.map((g) => <li key={g}>• {g}</li>)}
            </ul>
            <p className="mt-2 text-xs text-[#6B7280]">
              Records already imported keep working — this blocks new business only.
            </p>
          </div>
        ) : (
          <div className="rounded border px-3 py-2 text-sm"
               style={{ background: '#D1FAE5', borderColor: '#A7F3D0', color: '#065F46' }}>
            Every required document is on file, verified and in date.
          </div>
        )}

        {/* Upload */}
        <div className="flex flex-wrap items-end gap-3 border-t pt-4">
          <label className="text-sm">
            <span className="block text-xs text-[#6B7280] mb-1">Document</span>
            <select value={kind} onChange={(e) => setKind(e.target.value)}
                    className="border rounded px-2 py-1.5 text-sm max-w-[16rem]">
              {(reg?.kinds ?? []).map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}{k.required ? ' (required)' : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="block text-xs text-[#6B7280] mb-1">Expires (if it does)</span>
            <input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)}
                   className="border rounded px-2 py-1.5 text-sm" />
          </label>
          <label className="text-sm">
            <span className="block text-xs text-[#6B7280] mb-1">Who provided it</span>
            <input value={source} onChange={(e) => setSource(e.target.value)}
                   placeholder="the reinsurer / the broker"
                   className="border rounded px-2 py-1.5 text-sm" />
          </label>
          <label className="text-sm">
            <span className="block text-xs text-[#6B7280] mb-1">File</span>
            <input type="file" aria-label="Document file"
                   onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                   className="text-sm max-w-[16rem]" />
          </label>
          <Button onClick={upload} disabled={busy || !file}
                  style={{ background: NAVY }}>
            <Upload className="w-4 h-4 mr-2" /> Add to the file
          </Button>
        </div>

        {/* What is held */}
        {reg && reg.documents.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-[#6B7280] border-b">
                <th className="py-2">Document</th>
                <th className="py-2">Expires</th>
                <th className="py-2">Status</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {reg.documents.map((d) => (
                <tr key={d.id} className="border-b last:border-0">
                  <td className="py-2">
                    {/* A plain <a href> to the streaming endpoint comes back
                        401: Omni sends its token in a header, which an anchor
                        cannot carry. Fetch it with the header, then hand the
                        browser the bytes — the route every other Omni download
                        takes. */}
                    <button type="button"
                            onClick={() => openReinsurerDocument(d)
                              .catch((e) => setErr((e as Error).message))}
                            className="font-medium underline decoration-dotted text-left"
                            style={{ color: NAVY }}>
                      {d.kind_label}
                    </button>
                    <div className="text-xs text-[#9CA3AF]">
                      {d.original_filename}
                      {d.source ? ` · from ${d.source}` : ''}
                      {d.uploaded_by ? ` · ${d.uploaded_by}` : ''}
                    </div>
                  </td>
                  <td className="py-2 text-[#6B7280]">
                    {fmtDate(d.expiry_date)}
                    {d.expired && (
                      <span className="ml-2 text-xs font-semibold" style={{ color: '#991B1B' }}>
                        expired
                      </span>
                    )}
                  </td>
                  <td className="py-2">
                    <span className="text-xs font-semibold px-2 py-0.5 rounded"
                          style={{ background: PILL[d.verification_status].bg,
                                   color: PILL[d.verification_status].fg }}>
                      {d.verification_label}
                    </span>
                    {d.verification_note && (
                      <div className="text-xs text-[#9CA3AF] mt-0.5">{d.verification_note}</div>
                    )}
                  </td>
                  <td className="py-2 text-right whitespace-nowrap">
                    {d.verification_status !== 'verified' && (
                      <button onClick={() => decide(d.id, 'verified')} disabled={busy}
                              className="text-xs font-semibold mr-3 inline-flex items-center gap-1"
                              style={{ color: '#065F46' }}>
                        <Check className="w-3 h-3" /> Verify
                      </button>
                    )}
                    {d.verification_status !== 'rejected' && (
                      <button onClick={() => decide(d.id, 'rejected')} disabled={busy}
                              className="text-xs font-semibold inline-flex items-center gap-1"
                              style={{ color: '#991B1B' }}>
                        <X className="w-3 h-3" /> Reject
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  )
}
