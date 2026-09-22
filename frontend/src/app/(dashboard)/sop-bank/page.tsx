'use client'

/**
 * /sop-bank — ISO 9001 QMS Standard Operating Procedure library.
 *
 * CFO directive 2026-06-10 (BOBS certification audit gap): every staff
 * member must reach their department's SOPs inside omni, and reads are
 * acknowledged per user — the acknowledgment trail is the training
 * evidence the auditors probe.
 *
 * Open to ALL authenticated staff (deliberately NOT behind the HRIS gate).
 * Data: GET /api/v1/iso/sops/ · coverage: /api/v1/iso/sops/coverage/
 */
import { useEffect, useMemo, useState } from 'react'
import {
  BookOpenCheck, Check, Download, FileText, Film, Loader2, Search,
  ShieldCheck, AlertTriangle, Sheet, Users, UploadCloud, Sparkles, X,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch, apiFetchBinary, API_BASE } from '@/lib/api'

interface SopRow {
  id: string
  department: string
  doc_type: string
  sop_number: string
  title: string
  owner: string
  revision_date: string | null
  iso_compliant: boolean | null
  file_type: string
  size_bytes: number
  status: string
  omni_fit: string
  omni_fit_notes: string
  uploaded_by: string
  ack_count: number
  my_acknowledged: boolean
}

interface SopListResponse {
  count: number
  rows: SopRow[]
  departments: { name: string; count: number }[]
  doc_type_counts: { sop: number; policy: number }
  can_upload: boolean
}

interface UploadSuggestion {
  department: string
  doc_type: string
  title: string
  confidence: number
  source: string
}

interface UploadResponse {
  id: string
  suggestion: UploadSuggestion
  departments: string[]
  doc_type_choices: string[]
  file_type: string
  size_bytes: number
}

interface CoverageResponse {
  active_staff: number
  total_sops: number
  total_acknowledgements: number
  departments: {
    department: string
    sops: number
    acknowledgements: number
    distinct_readers: number
  }[]
}

function fmtSize(b: number): string {
  if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB'
  if (b >= 1024) return (b / 1024).toFixed(0) + ' KB'
  return b + ' B'
}

function FileIcon({ type }: { type: string }) {
  if (type === 'webm' || type === 'mp4') return <Film className="w-4 h-4" />
  if (type === 'xlsx') return <Sheet className="w-4 h-4" />
  return <FileText className="w-4 h-4" />
}

export default function SopBankPage() {
  const { theme } = useTheme()
  const [data, setData] = useState<SopListResponse | null>(null)
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null)
  const [dept, setDept] = useState('')
  const [docTypeF, setDocTypeF] = useState<'' | 'sop' | 'policy'>('')
  const [q, setQ] = useState('')
  const [statusF, setStatusF] = useState<'active' | 'all'>('active')
  const [loading, setLoading] = useState(true)
  const [busyAck, setBusyAck] = useState<string | null>(null)
  const [busyDl, setBusyDl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showUpload, setShowUpload] = useState(false)

  function load() {
    setLoading(true); setError(null)
    const params = new URLSearchParams({ status: statusF })
    if (dept) params.set('department', dept)
    if (docTypeF) params.set('doc_type', docTypeF)
    if (q.trim()) params.set('q', q.trim())
    apiFetch<SopListResponse>(`/iso/sops/?${params.toString()}`)
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load documents'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() /* eslint-disable-line react-hooks/exhaustive-deps */ }, [dept, docTypeF, statusF])
  useEffect(() => {
    apiFetch<CoverageResponse>('/iso/sops/coverage/').then(setCoverage).catch(() => {})
  }, [])

  async function acknowledge(id: string) {
    setBusyAck(id); setError(null)
    try {
      await apiFetch(`/iso/sops/${id}/acknowledge/`, { method: 'POST' })
      setData(d => d ? {
        ...d,
        rows: d.rows.map(r => r.id === id
          ? { ...r, my_acknowledged: true, ack_count: r.ack_count + (r.my_acknowledged ? 0 : 1) }
          : r),
      } : d)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not mark as read')
    } finally {
      setBusyAck(null)
    }
  }

  async function download(row: SopRow) {
    setBusyDl(row.id)
    try {
      const res = await apiFetchBinary(`${API_BASE}/iso/sops/${row.id}/download/`, { headers: {} })
      if (!res.ok) throw new Error(`Download failed: ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${row.title}.${row.file_type}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Download failed')
    } finally {
      setBusyDl(null)
    }
  }

  const visibleRows = useMemo(() => data?.rows || [], [data])
  const myReadCount = visibleRows.filter(r => r.my_acknowledged).length
  const cardStyle = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inputStyle = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="SOP Bank" breadcrumbs={[{ label: 'SOP Bank' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">

        {/* ISO context banner */}
        <div className="rounded-2xl p-4 flex items-start gap-3" style={cardStyle}>
          <ShieldCheck className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: theme.orange }} />
          <div className="text-sm" style={{ color: theme.text }}>
            <strong>Corporate Governance — SOPs &amp; Policies.</strong>{' '}
            <span style={{ color: theme.t2 }}>
              Every department's Standard Operating Procedures and Policies in one place.
              Read the ones for your role, then mark each as read — your acknowledgment is
              part of Alpha Direct's BOBS certification audit evidence.
            </span>
          </div>
        </div>

        {/* Coverage cards */}
        {coverage && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {[
              { icon: BookOpenCheck, label: 'Active documents in the library', value: String(coverage.total_sops) },
              { icon: Check, label: 'Total read-acknowledgements', value: String(coverage.total_acknowledgements) },
              { icon: Users, label: 'Your reads (this view)', value: `${myReadCount} / ${visibleRows.length}` },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} className="rounded-2xl p-4" style={cardStyle}>
                <div className="flex items-center gap-2 text-xs" style={{ color: theme.t2 }}>
                  <Icon className="w-3.5 h-3.5" style={{ color: theme.orange }} /> {label}
                </div>
                <div className="mt-1 text-2xl font-bold tabular-nums" style={{ color: theme.text }}>{value}</div>
              </div>
            ))}
          </div>
        )}

        {/* Filters */}
        <div className="rounded-2xl p-4 space-y-3" style={cardStyle}>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[220px]">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: theme.t2 }} />
              <input value={q} onChange={e => setQ(e.target.value)}
                     onKeyDown={e => { if (e.key === 'Enter') load() }}
                     placeholder="Search title, content, owner… (Enter)"
                     className="w-full pl-8 pr-3 py-2 rounded-lg text-sm outline-none" style={inputStyle} />
            </div>
            <button type="button" onClick={() => setStatusF(s => s === 'active' ? 'all' : 'active')}
                    className="px-2.5 py-1.5 rounded-md text-xs font-semibold"
                    style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
              {statusF === 'active' ? 'Active only' : 'Incl. obsolete + drafts'}
            </button>
            <button type="button" onClick={load} disabled={loading}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
              Search
            </button>
            {data?.can_upload && (
              <button type="button" onClick={() => setShowUpload(true)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                      style={{ background: theme.text, color: theme.card }}>
                <UploadCloud className="w-3.5 h-3.5" /> Upload document
              </button>
            )}
          </div>
          {/* Document kind: SOPs vs Policies */}
          <div className="flex flex-wrap gap-1.5">
            {([
              { k: '' as const, label: 'All types', n: (data ? data.doc_type_counts.sop + data.doc_type_counts.policy : undefined) },
              { k: 'sop' as const, label: 'SOPs', n: data?.doc_type_counts.sop },
              { k: 'policy' as const, label: 'Policies', n: data?.doc_type_counts.policy },
            ]).map(({ k, label, n }) => (
              <button key={label} type="button" onClick={() => setDocTypeF(k)}
                      className="px-2.5 py-1 rounded-md text-xs font-semibold"
                      style={{
                        background: docTypeF === k ? theme.text : theme.g100,
                        color: docTypeF === k ? theme.card : theme.t2,
                      }}>
                {label}{n !== undefined ? ` (${n})` : ''}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button type="button" onClick={() => setDept('')}
                    className="px-2.5 py-1 rounded-md text-xs font-semibold"
                    style={{
                      background: dept === '' ? theme.orange : theme.g100,
                      color: dept === '' ? '#fff' : theme.t2,
                    }}>
              All departments ({data?.departments.reduce((a, d) => a + d.count, 0) ?? '…'})
            </button>
            {(data?.departments || []).map(d => (
              <button key={d.name} type="button" onClick={() => setDept(d.name)}
                      className="px-2.5 py-1 rounded-md text-xs font-semibold"
                      style={{
                        background: dept === d.name ? theme.orange : theme.g100,
                        color: dept === d.name ? '#fff' : theme.t2,
                      }}>
                {d.name} ({d.count})
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="rounded-lg px-3 py-2 text-sm"
               style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
            {error}
          </div>
        )}

        {/* SOP list */}
        <div className="space-y-2">
          {loading && (
            <div className="rounded-2xl p-8 text-center text-sm" style={{ ...cardStyle, color: theme.t2 }}>
              <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> Loading the SOP bank…
            </div>
          )}
          {!loading && visibleRows.length === 0 && (
            <div className="rounded-2xl p-8 text-center text-sm" style={{ ...cardStyle, color: theme.t2 }}>
              No SOPs match. Try clearing filters.
            </div>
          )}
          {!loading && visibleRows.map(row => (
            <div key={row.id} className="rounded-xl px-4 py-3 flex items-start gap-3" style={cardStyle}>
              <div className="mt-0.5 flex-shrink-0" style={{ color: theme.orange }}>
                <FileIcon type={row.file_type} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold" style={{ color: theme.text }}>
                  <span className="mr-1.5 px-1.5 py-0.5 rounded text-[10px] uppercase font-bold tracking-wide"
                        style={row.doc_type === 'policy'
                          ? { background: theme.text, color: theme.card }
                          : { background: theme.orange + '22', color: theme.orange }}>
                    {row.doc_type === 'policy' ? 'Policy' : 'SOP'}
                  </span>
                  {row.sop_number && (
                    <span className="mr-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold tabular-nums"
                          style={{ background: theme.orange + '22', color: theme.orange }}>
                      #{row.sop_number}
                    </span>
                  )}
                  {row.title}
                  {row.status !== 'active' && (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px] uppercase font-semibold"
                          style={{ background: theme.g100, color: theme.t2 }}>{row.status}</span>
                  )}
                </div>
                <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                  {row.department}
                  {row.owner ? <> · owner {row.owner}</> : null}
                  {row.revision_date ? <> · rev {row.revision_date}</> : null}
                  {' '}· {row.file_type.toUpperCase()} · {fmtSize(row.size_bytes)} · {row.ack_count} read{row.ack_count === 1 ? '' : 's'}
                </div>
                {row.omni_fit === 'gap' && (
                  <div className="text-xs mt-1 flex items-start gap-1" style={{ color: theme.er }}>
                    <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                    <span>Omni alignment gap: {row.omni_fit_notes || 'references a process omni has replaced'}</span>
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button type="button" onClick={() => download(row)} disabled={busyDl === row.id}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-semibold disabled:opacity-50"
                        style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
                  {busyDl === row.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                  Open
                </button>
                {row.my_acknowledged ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-semibold"
                        style={{ background: theme.okB, color: theme.ok }}>
                    <Check className="w-3 h-3" /> Read
                  </span>
                ) : (
                  <button type="button" onClick={() => acknowledge(row.id)} disabled={busyAck === row.id}
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-semibold disabled:opacity-50"
                          style={{ background: theme.orange, color: '#fff' }}>
                    {busyAck === row.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                    Mark as read
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Department coverage table (audit evidence view) */}
        {coverage && coverage.departments.length > 0 && (
          <div className="rounded-2xl overflow-hidden" style={cardStyle}>
            <div className="px-4 pt-4 pb-2 text-sm font-semibold flex items-center gap-2" style={{ color: theme.text }}>
              <Users className="w-4 h-4" style={{ color: theme.orange }} />
              Read coverage by department (audit evidence)
            </div>
            <table className="w-full text-left">
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                  {['Department', 'SOPs', 'Acknowledgements', 'Distinct readers'].map((h, i) => (
                    <th key={h} className={`px-4 py-2 text-[11px] uppercase tracking-wider font-semibold ${i > 0 ? 'text-right' : ''}`}
                        style={{ color: theme.t2 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {coverage.departments.map(d => (
                  <tr key={d.department} style={{ borderTop: `1px solid ${theme.cardBdr}55` }}>
                    <td className="px-4 py-2 text-sm" style={{ color: theme.text }}>{d.department}</td>
                    <td className="px-4 py-2 text-sm text-right tabular-nums" style={{ color: theme.text }}>{d.sops}</td>
                    <td className="px-4 py-2 text-sm text-right tabular-nums" style={{ color: theme.text }}>{d.acknowledgements}</td>
                    <td className="px-4 py-2 text-sm text-right tabular-nums"
                        style={{ color: d.distinct_readers === 0 ? theme.er : theme.text }}>{d.distinct_readers}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>

      {showUpload && (
        <UploadModal
          departments={(data?.departments || []).map(d => d.name)}
          onClose={() => setShowUpload(false)}
          onDone={() => { setShowUpload(false); load(); apiFetch<CoverageResponse>('/iso/sops/coverage/').then(setCoverage).catch(() => {}) }}
        />
      )}
    </div>
  )
}

/** Empowered-staff upload: pick a file → DeepSeek suggests department + type →
 *  confirm in one tap. A draft is created on upload and either published
 *  (confirm) or deleted (cancel), so nothing half-filed leaks to staff. */
function UploadModal({ departments, onClose, onDone }: {
  departments: string[]
  onClose: () => void
  onDone: () => void
}) {
  const { theme } = useTheme()
  const [step, setStep] = useState<'pick' | 'thinking' | 'confirm' | 'saving'>('pick')
  const [draftId, setDraftId] = useState<string | null>(null)
  const [suggestion, setSuggestion] = useState<UploadSuggestion | null>(null)
  const [deptOptions, setDeptOptions] = useState<string[]>(departments)
  const [dept, setDept] = useState('')
  const [docType, setDocType] = useState<'sop' | 'policy'>('sop')
  const [title, setTitle] = useState('')
  const [err, setErr] = useState<string | null>(null)

  const cardStyle = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inputStyle = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  async function onFile(file: File) {
    setErr(null); setStep('thinking')
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await apiFetch<UploadResponse>('/iso/sops/upload/', { method: 'POST', body: form })
      setDraftId(res.id)
      setSuggestion(res.suggestion)
      setDeptOptions(Array.from(new Set([...(res.departments || []), ...(res.suggestion.department ? [res.suggestion.department] : [])])))
      setDept(res.suggestion.department || '')
      setDocType(res.suggestion.doc_type === 'policy' ? 'policy' : 'sop')
      setTitle(res.suggestion.title || '')
      setStep('confirm')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
      setStep('pick')
    }
  }

  async function confirm() {
    if (!draftId) return
    if (!dept.trim()) { setErr('Please choose a department.'); return }
    setErr(null); setStep('saving')
    try {
      await apiFetch(`/iso/sops/${draftId}/confirm/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ department: dept.trim(), doc_type: docType, title: title.trim() }),
      })
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save')
      setStep('confirm')
    }
  }

  async function cancel() {
    if (draftId) { apiFetch(`/iso/sops/${draftId}/discard/`, { method: 'POST' }).catch(() => {}) }
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: '#0008' }} onClick={cancel}>
      <div className="w-full max-w-lg rounded-2xl p-5 space-y-4" style={cardStyle}
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="text-base font-semibold flex items-center gap-2" style={{ color: theme.text }}>
            <UploadCloud className="w-5 h-5" style={{ color: theme.orange }} /> Upload a document
          </div>
          <button type="button" onClick={cancel} style={{ color: theme.t2 }}><X className="w-4 h-4" /></button>
        </div>

        {err && (
          <div className="rounded-lg px-3 py-2 text-sm"
               style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>{err}</div>
        )}

        {step === 'pick' && (
          <div className="space-y-3">
            <p className="text-sm" style={{ color: theme.t2 }}>
              Choose a Word (.docx), PDF or PowerPoint file. Aria will read it and suggest
              the department and whether it is an SOP or a Policy — you confirm before it is filed.
            </p>
            <label className="block rounded-xl border-2 border-dashed p-6 text-center cursor-pointer"
                   style={{ borderColor: theme.cardBdr, color: theme.t2 }}>
              <UploadCloud className="w-6 h-6 mx-auto mb-2" style={{ color: theme.orange }} />
              <span className="text-sm font-semibold" style={{ color: theme.text }}>Click to choose a file</span>
              <input type="file" className="hidden" accept=".docx,.pdf,.doc,.pptx,.xlsx"
                     onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }} />
            </label>
          </div>
        )}

        {step === 'thinking' && (
          <div className="py-8 text-center text-sm" style={{ color: theme.t2 }}>
            <Loader2 className="w-5 h-5 inline animate-spin mr-2" style={{ color: theme.orange }} />
            Reading the document and working out where it belongs…
          </div>
        )}

        {(step === 'confirm' || step === 'saving') && (
          <div className="space-y-3">
            <div className="rounded-lg px-3 py-2 text-xs flex items-start gap-2"
                 style={{ background: theme.orange + '18', color: theme.text }}>
              <Sparkles className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" style={{ color: theme.orange }} />
              <span>
                {suggestion?.source === 'deepseek'
                  ? <>Aria suggests <strong>{suggestion?.doc_type === 'policy' ? 'Policy' : 'SOP'}</strong> in <strong>{suggestion?.department || '—'}</strong>. Change anything below, then confirm.</>
                  : <>Couldn’t read the file automatically — please pick the department and type below.</>}
              </span>
            </div>

            <div>
              <label className="text-xs font-semibold" style={{ color: theme.t2 }}>Type</label>
              <div className="flex gap-1.5 mt-1">
                {(['sop', 'policy'] as const).map(t => (
                  <button key={t} type="button" onClick={() => setDocType(t)}
                          className="px-3 py-1.5 rounded-md text-xs font-semibold"
                          style={{ background: docType === t ? theme.orange : theme.g100, color: docType === t ? '#fff' : theme.t2 }}>
                    {t === 'policy' ? 'Policy' : 'SOP'}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="text-xs font-semibold" style={{ color: theme.t2 }}>Department</label>
              <input list="sop-dept-list" value={dept} onChange={e => setDept(e.target.value)}
                     placeholder="e.g. Human Resources"
                     className="w-full mt-1 px-3 py-2 rounded-lg text-sm outline-none" style={inputStyle} />
              <datalist id="sop-dept-list">
                {deptOptions.map(d => <option key={d} value={d} />)}
              </datalist>
            </div>

            <div>
              <label className="text-xs font-semibold" style={{ color: theme.t2 }}>Title</label>
              <input value={title} onChange={e => setTitle(e.target.value)}
                     className="w-full mt-1 px-3 py-2 rounded-lg text-sm outline-none" style={inputStyle} />
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={cancel} disabled={step === 'saving'}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold"
                      style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
                Cancel
              </button>
              <button type="button" onClick={confirm} disabled={step === 'saving'}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-50"
                      style={{ background: theme.orange, color: '#fff' }}>
                {step === 'saving' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                Confirm &amp; file
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
