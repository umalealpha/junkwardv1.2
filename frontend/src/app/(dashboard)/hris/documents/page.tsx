'use client'

/**
 * /hris/documents — HR document vault (Dorothy 2026-06-26).
 * Onboarding packs, signed agreements, policies; ready for exit-interview and
 * disciplinary documents. HR-gated by the backend (_gate). Personal documents
 * (signed agreements) are flagged "restricted".
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getHRDocuments, uploadHRDocument, downloadHRDocument, getAssetEmployees,
  getToken, type HRDocument, type HRDocumentList, type AssetEmployeeOption,
} from '@/lib/api'
import { FileText, Download, Upload, Lock, FolderOpen, Loader2, AlertTriangle, CheckCircle2, ClipboardType, Paperclip, X, Search } from 'lucide-react'

function fmtSize(n: number) {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function HRDocumentsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const fileRef = useRef<HTMLInputElement>(null)
  const [data, setData] = useState<HRDocumentList | null>(null)
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [form, setForm] = useState({ title: '', category: 'job_description', description: '', employee_name: '', is_personal: false, job_title: '', text_content: '' })
  // Default to the fast path: paste a job description for a person and save.
  const [mode, setMode] = useState<'text' | 'file'>('text')
  const [empPk, setEmpPk] = useState('')
  const [empQ, setEmpQ] = useState('')
  const [empOpts, setEmpOpts] = useState<AssetEmployeeOption[]>([])
  const [empOpen, setEmpOpen] = useState(false)

  const refresh = useCallback((cat?: string) => {
    setLoading(true); setErr(null)
    getHRDocuments(cat).then(setData)
      .catch(e => setErr(e instanceof Error ? e.message : 'Could not load documents'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    refresh()
  }, [refresh, router])

  // Staff picker — search the same lightweight name list the asset register uses.
  useEffect(() => {
    if (!empOpen) return
    let live = true
    const t = setTimeout(() => {
      getAssetEmployees(empQ || undefined)
        .then(rows => { if (live) setEmpOpts(rows.slice(0, 8)) })
        .catch(() => { if (live) setEmpOpts([]) })
    }, 200)
    return () => { live = false; clearTimeout(t) }
  }, [empQ, empOpen])

  const card_ = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const input = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }
  const cats = data?.categories || []
  const docs = data?.documents || []

  function pickEmployee(o: AssetEmployeeOption) {
    setEmpPk(o.id)
    setForm(f => ({ ...f, employee_name: o.full_name }))
    setEmpOpen(false); setEmpQ('')
  }
  function clearEmployee() {
    setEmpPk('')
    setForm(f => ({ ...f, employee_name: '' }))
  }

  async function onUpload() {
    const f = fileRef.current?.files?.[0]
    if (mode === 'file' && !f) { setErr('Choose a file first.'); return }
    if (mode === 'text' && !form.text_content.trim()) { setErr('Paste the text (e.g. the job description) first.'); return }
    setBusy(true); setMsg(null); setErr(null)
    try {
      const fd = new FormData()
      if (mode === 'file' && f) fd.append('file', f)
      if (mode === 'text') fd.append('text_content', form.text_content)
      fd.append('title', form.title.trim())
      fd.append('category', form.category)
      fd.append('description', form.description)
      fd.append('job_title', form.job_title)
      fd.append('employee_name', form.employee_name)
      if (empPk) fd.append('employee_pk', empPk)
      fd.append('is_personal', form.is_personal ? '1' : '0')
      const d = await uploadHRDocument(fd)
      setMsg(`Saved — ${d.title}`)
      setForm({ title: '', category: form.category, description: '', employee_name: '', is_personal: false, job_title: '', text_content: '' })
      setEmpPk('')
      if (fileRef.current) fileRef.current.value = ''
      refresh(filter || undefined)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Upload failed') }
    finally { setBusy(false) }
  }

  function download(d: HRDocument) {
    downloadHRDocument(d.id, d.filename || `${d.title}.pdf`)
      .catch(e => setErr(e instanceof Error ? e.message : 'Download failed'))
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="HR Documents" breadcrumbs={[{ label: 'HR' }, { label: 'Documents' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <div className="flex items-center gap-3">
          <FolderOpen className="w-5 h-5" style={{ color: theme.orange }} />
          <h2 className="text-lg font-semibold" style={{ color: theme.text }}>HR Document Vault</h2>
        </div>

        {msg && (
          <div className="flex items-center gap-2 text-sm rounded-lg px-3 py-2" style={{ background: theme.g100, color: '#16a34a' }}>
            <CheckCircle2 className="w-4 h-4" /> {msg}
          </div>
        )}
        {err && (
          <div className="flex items-center gap-2 text-sm rounded-lg px-3 py-2" style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
            <AlertTriangle className="w-4 h-4" /> {err}
          </div>
        )}

        {/* Upload */}
        <div className="rounded-2xl p-4 space-y-3" style={card_}>
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Add a document</h3>
            {/* Paste-text vs attach-file */}
            <div className="inline-flex rounded-lg overflow-hidden" style={{ border: `1px solid ${theme.cardBdr}` }}>
              <button onClick={() => setMode('text')} className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold"
                      style={{ background: mode === 'text' ? theme.orange : 'transparent', color: mode === 'text' ? '#fff' : theme.t2 }}>
                <ClipboardType className="w-3.5 h-3.5" /> Paste text
              </button>
              <button onClick={() => setMode('file')} className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold"
                      style={{ background: mode === 'file' ? theme.orange : 'transparent', color: mode === 'file' ? '#fff' : theme.t2 }}>
                <Paperclip className="w-3.5 h-3.5" /> Attach file
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {/* Category */}
            <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                    className="px-3 py-2 rounded-lg text-sm outline-none" style={input}>
              {(cats.length ? cats : [{ value: 'job_description', label: 'Job Description' }]).map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>

            {/* Employee picker */}
            <div className="relative">
              {empPk || form.employee_name ? (
                <div className="flex items-center justify-between px-3 py-2 rounded-lg text-sm" style={input}>
                  <span className="truncate" style={{ color: theme.text }}>{form.employee_name}</span>
                  <button onClick={clearEmployee}><X className="w-3.5 h-3.5" style={{ color: theme.t2 }} /></button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm" style={input}>
                  <Search className="w-3.5 h-3.5" style={{ color: theme.t2 }} />
                  <input placeholder="Employee (search name)…" value={empQ}
                         onFocus={() => setEmpOpen(true)}
                         onChange={e => { setEmpQ(e.target.value); setEmpOpen(true) }}
                         className="bg-transparent outline-none flex-1" style={{ color: theme.text }} />
                </div>
              )}
              {empOpen && empOpts.length > 0 && !empPk && (
                <div className="absolute z-20 mt-1 w-full rounded-lg overflow-hidden shadow-lg" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
                  {empOpts.map(o => (
                    <button key={o.id} onClick={() => pickEmployee(o)}
                            className="w-full text-left px-3 py-2 text-sm hover:opacity-80" style={{ color: theme.text }}>
                      {o.full_name} <span style={{ color: theme.t2 }}>· {o.company_code || '—'}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Position (mainly for a JD) */}
            <input placeholder="Position / job title" value={form.job_title} onChange={e => setForm(f => ({ ...f, job_title: e.target.value }))}
                   className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
          </div>

          {mode === 'text' ? (
            <textarea placeholder="Paste the job description here — duties, responsibilities, reporting line…"
                      value={form.text_content} onChange={e => setForm(f => ({ ...f, text_content: e.target.value }))}
                      rows={8} className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y" style={input} />
          ) : (
            <input ref={fileRef} type="file" className="text-sm" style={{ color: theme.t2 }} />
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input placeholder="Title (optional — auto-named for a JD)" value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                   className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
            <input placeholder="Note (optional)" value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                   className="px-3 py-2 rounded-lg text-sm outline-none" style={input} />
          </div>

          <div className="flex items-center justify-between gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-sm" style={{ color: theme.t2 }}>
              <input type="checkbox" checked={form.is_personal} onChange={e => setForm(f => ({ ...f, is_personal: e.target.checked }))} />
              Personal / restricted (signed agreement for one person)
            </label>
            <button onClick={onUpload} disabled={busy}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />} Save
            </button>
          </div>
        </div>

        {/* Category filter */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <button onClick={() => { setFilter(''); refresh() }}
                  className="px-3 py-1.5 rounded-full text-xs font-semibold"
                  style={{ background: filter === '' ? theme.orange : theme.g100, color: filter === '' ? '#fff' : theme.t2 }}>
            All ({docs.length})
          </button>
          {cats.map(c => (
            <button key={c.value} onClick={() => { setFilter(c.value); refresh(c.value) }}
                    className="px-3 py-1.5 rounded-full text-xs font-semibold"
                    style={{ background: filter === c.value ? theme.orange : theme.g100, color: filter === c.value ? '#fff' : theme.t2 }}>
              {c.label}
            </button>
          ))}
        </div>

        {/* List */}
        <div className="rounded-2xl overflow-hidden" style={card_}>
          {loading ? (
            <div className="px-4 py-10 text-center" style={{ color: theme.t2 }}><Loader2 className="w-4 h-4 inline animate-spin" /> Loading…</div>
          ) : !docs.length ? (
            <div className="px-4 py-10 text-center" style={{ color: theme.t2 }}>No documents yet.</div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead><tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                {['Document', 'Category', 'For', 'Size', ''].map((h, i) => (
                  <th key={h} className={`px-3 py-2.5 text-[11px] uppercase tracking-wider font-semibold ${i === 4 ? 'text-right' : ''}`} style={{ color: theme.t2 }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {docs.map(d => (
                  <tr key={d.id} style={{ borderTop: `1px solid ${theme.cardBdr}55` }}>
                    <td className="px-3 py-2" style={{ color: theme.text }}>
                      <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4 flex-shrink-0" style={{ color: theme.orange }} />
                        <span className="truncate">{d.title}</span>
                        {d.is_personal && <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded" style={{ background: theme.erB, color: theme.er }}><Lock className="w-3 h-3" /> Restricted</span>}
                      </div>
                    </td>
                    <td className="px-3 py-2" style={{ color: theme.t2 }}>{d.category_label}</td>
                    <td className="px-3 py-2" style={{ color: theme.t2 }}>{d.employee_name || '—'}</td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: theme.t2 }}>{fmtSize(d.size)}</td>
                    <td className="px-3 py-2 text-right">
                      <button onClick={() => download(d)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                              style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
                        <Download className="w-3.5 h-3.5" /> Download
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  )
}
