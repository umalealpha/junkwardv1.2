'use client'

/** /m/staff/reviews — your OWN Development Dialogues (past performance reviews).
 * Confidential: only you, your line manager, the executives and HR can see a
 * review. A line manager also sees their direct reports' reviews here. Files
 * download through the access-controlled endpoint (CFO 2026-07-18). */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, FileText, Download, Lock } from 'lucide-react'
import { sfetch, sfetchBlob } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

interface Doc {
  id: string; title: string; category: string; category_label: string
  employee_name: string; filename: string; size: number; created_at: string
}

export default function StaffReviews() {
  const base = useStaffBase()
  const [docs, setDocs] = useState<Doc[] | null>(null)
  const [note, setNote] = useState('')
  const [dl, setDl] = useState<string | null>(null)
  const [toast, setToast] = useState('')

  useEffect(() => {
    sfetch<{ documents: Doc[] }>('/hris/my-documents/')
      .then(d => { setDocs(d.documents); if (!d.documents.length) setNote('No reviews yet. Your Development Dialogues appear here once HR loads them.') })
      .catch(e => { setDocs([]); setNote(e instanceof Error ? e.message : 'Could not load.') })
  }, [])

  async function download(doc: Doc) {
    if (dl) return
    setDl(doc.id)
    try {
      const blob = await sfetchBlob(`/hris/documents/${doc.id}/download/`)
      const url = URL.createObjectURL(blob)
      // iOS Safari (Bharath, 11 Aug 2026): a `window.open(blobUrl)` AFTER an
      // await is treated as a programmatic popup and blocked, and Safari ignores
      // the `<a download>` attribute for blob: URLs — so the old flow did
      // nothing on an iPhone. He could see the DD Box Grid listed but tapping
      // "Open" produced no file. On iOS, navigating the SAME tab to the blob
      // hands it to QuickLook, which previews the .xlsx — which is what "open"
      // means on a phone. Desktop keeps the real download.
      const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent)
        || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
      if (isIOS) {
        window.location.href = url
      } else {
        const a = document.createElement('a')
        a.href = url; a.download = doc.filename || 'review.xlsx'
        document.body.appendChild(a); a.click(); a.remove()
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) {
      setToast(e instanceof Error ? e.message : 'Could not open the review.')
      setTimeout(() => setToast(''), 4000)
    } finally { setDl(null) }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>My Reviews</h1>
      </header>
      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <p style={{ display: 'flex', alignItems: 'center', gap: 6, color: C.inkSoft, fontSize: 12.5, margin: '0 0 2px' }}>
          <Lock size={13} /> Confidential — only you, your line manager, the executives and HR can see these.
        </p>
        {docs === null && <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>Loading…</p>}
        {docs !== null && docs.length === 0 && (
          <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>{note}</p>)}

        {(docs || []).map(d => (
          <div key={d.id} style={{ ...card, padding: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 800, color: C.ink, fontSize: 15 }}>
              <FileText size={16} style={{ color: C.orange }} /> {d.title}
            </div>
            {d.employee_name && <p style={{ margin: '4px 0 0', fontSize: 12, color: C.inkSoft }}>{d.employee_name}</p>}
            <button type="button" onClick={() => download(d)} disabled={dl === d.id}
              style={{ marginTop: 14, width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: C.navy, color: '#fff', border: 'none', borderRadius: 12, padding: '11px 0', fontSize: 14, fontWeight: 700, cursor: 'pointer', opacity: dl === d.id ? 0.6 : 1 }}>
              <Download size={16} /> {dl === d.id ? 'Preparing…' : 'Open / download'}
            </button>
          </div>
        ))}
      </main>
      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center', fontFamily: sans }}>
          {toast}
        </div>)}
    </div>
  )
}
