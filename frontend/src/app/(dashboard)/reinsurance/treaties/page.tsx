'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getReinsuranceTreaties,
  apiFetch,
  apiFetchBinary,
  type ReinsuranceTreaty,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Shield, AlertTriangle, RefreshCw, Upload, FileText, Sparkles, X, CheckCircle2, Trash2 } from 'lucide-react'

interface TreatyCandidate {
  treaty_number: string
  description: string
  reinsurer_code: string | null
  reinsurer_name: string | null
  reinsurer_id: string | null
  resolved_reinsurer: string | null
  needs_reinsurer: boolean
  treaty_type: string | null
  line_of_business: string
  inception_date: string | null
  expiry_date: string | null
  cession_share_percent: string | null
  commission_percent: string | null
  retention_amount: string | null
  limit_amount: string | null
  currency_code: string
  notes: string
  confidence: number
  warnings: string[]
}

interface Reinsurer { id: string; name: string; short_code: string }
interface ParseResp {
  candidates: TreatyCandidate[]
  doc_kind: string
  deepseek_used: boolean
  raw_text_preview: string
  extracted_chars: number
}

function fmtPct(v: string | null): string {
  if (!v) return '—'
  const n = Number(v)
  return isFinite(n) ? `${n.toFixed(2)}%` : v
}

const inp = 'border border-[#E5E7EB] rounded px-2 py-1 text-xs w-full focus:outline-none focus:border-[#F07F00] bg-white'

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  draft:     { bg: '#F3F4F6', fg: '#374151' },
  active:    { bg: '#ECFDF5', fg: '#047857' },
  expired:   { bg: '#FEF3C7', fg: '#92400E' },
  cancelled: { bg: '#FEF2F2', fg: '#B91C1C' },
}

export default function ReinsuranceTreatiesPage() {
  const router = useRouter()
  const [treaties, setTreaties] = useState<ReinsuranceTreaty[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Upload + AI parse state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [parseInfo, setParseInfo]   = useState<{ doc_kind: string; deepseek_used: boolean; chars: number } | null>(null)
  const [candidates, setCandidates] = useState<TreatyCandidate[]>([])
  const [committing, setCommitting] = useState(false)
  const [commitResult, setCommitResult] = useState<{ created: number; updated: number; errors: { treaty_number: string; error: string }[] } | null>(null)
  const [reinsurers, setReinsurers] = useState<Reinsurer[]>([])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getReinsuranceTreaties()
      setTreaties(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load treaties')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
    apiFetch<{ results: Reinsurer[] }>('/reinsurers/?page_size=200')
      .then(r => setReinsurers(r.results || []))
      .catch(() => {})
  }, [router, load])

  const onPickFile = () => fileInputRef.current?.click()

  const parseFile = async (file: File) => {
    setParsing(true); setParseError(null); setParseInfo(null); setCandidates([]); setCommitResult(null)
    try {
      const form = new FormData()
      form.append('file', file)
      // FE-SWEEP swarm 2026-06-08 #15: SSO users had getToken()==='__sso__'
      // → "Token __sso__" 401. apiFetchBinary handles MSAL Bearer first, falls
      // back to legacy Token, and skips the sentinel.
      const res = await apiFetchBinary('/api/v1/reinsurance/treaties/upload-parse/', {
        method: 'POST',
        body: form,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      const data: ParseResp = await res.json()
      setCandidates(data.candidates || [])
      setParseInfo({ doc_kind: data.doc_kind, deepseek_used: data.deepseek_used, chars: data.extracted_chars })
      if ((data.candidates || []).length === 0) {
        setParseError('No treaties recognised in the document. Try a clearer copy or fill in manually below.')
      }
    } catch (e) {
      setParseError(e instanceof Error ? e.message : 'Parse failed')
    } finally { setParsing(false) }
  }

  const updateCandidate = (idx: number, patch: Partial<TreatyCandidate>) => {
    setCandidates(prev => prev.map((c, i) => i === idx ? { ...c, ...patch } : c))
  }
  const removeCandidate = (idx: number) => {
    setCandidates(prev => prev.filter((_, i) => i !== idx))
  }

  const commitAll = async () => {
    if (candidates.length === 0) return
    setCommitting(true); setCommitResult(null); setParseError(null)
    try {
      const res = await apiFetch<{ committed: { id: string; treaty_number: string; created: boolean }[]; errors: { treaty_number: string; error: string }[] }>(
        '/reinsurance/treaties/upload-commit/',
        { method: 'POST', body: JSON.stringify({ candidates }) },
      )
      const created = res.committed.filter(r => r.created).length
      const updated = res.committed.length - created
      setCommitResult({ created, updated, errors: res.errors })
      if (res.errors.length === 0) {
        setCandidates([])
        await load()
      }
    } catch (e) {
      setParseError(e instanceof Error ? e.message : 'Commit failed')
    } finally { setCommitting(false) }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Reinsurance Treaties"
        breadcrumbs={[{ label: 'Reinsurance' }, { label: 'Treaties' }]}
        actions={
          <Button
            variant="outline" size="sm"
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={load} disabled={loading}
          >
            Refresh
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {/* AI-assisted upload zone */}
        <Card>
          <CardContent className="p-4">
            <div className="flex items-start gap-3 mb-3">
              <Sparkles className="w-5 h-5 text-[#F07F00] mt-0.5" />
              <div className="flex-1">
                <h3 className="font-semibold text-[#0B0B3B]">AI-assisted treaty import</h3>
                <p className="text-sm text-[#6B7280] mt-1">
                  Drop a treaty PDF, Word document, or Excel sheet and Aria
                  will extract every treaty it finds. Review the results, fix
                  anything wrong, then click <em>Commit</em> to create the rows.
                </p>
              </div>
            </div>
            <input
              ref={fileInputRef} type="file" hidden
              accept=".pdf,.docx,.xlsx,.xls,.csv"
              onChange={e => { const f = e.target.files?.[0]; if (f) void parseFile(f); e.currentTarget.value = '' }}
            />
            <div
              className="border-2 border-dashed rounded-lg p-6 text-center cursor-pointer hover:bg-[#FAFAFA] transition"
              style={{ borderColor: '#E5E7EB' }}
              onClick={onPickFile}
              onDragOver={e => e.preventDefault()}
              onDrop={e => {
                e.preventDefault()
                const f = e.dataTransfer.files?.[0]; if (f) void parseFile(f)
              }}
            >
              <Upload className="w-7 h-7 mx-auto text-[#9CA3AF] mb-2" />
              <p className="text-sm text-[#374151]">
                {parsing ? 'Reading and parsing the document…' : 'Click to choose a file or drag-and-drop here'}
              </p>
              <p className="text-xs text-[#9CA3AF] mt-1">PDF · DOCX · XLSX · CSV (max 25 MB)</p>
            </div>
            {parseInfo && (
              <div className="mt-3 text-xs text-[#6B7280] flex flex-wrap items-center gap-3">
                <span className="inline-flex items-center gap-1"><FileText className="w-3 h-3" /> {parseInfo.doc_kind.toUpperCase()}</span>
                <span>{parseInfo.chars.toLocaleString()} characters extracted</span>
                <span style={{ color: parseInfo.deepseek_used ? '#047857' : '#B45309' }}>
                  {parseInfo.deepseek_used ? '✓ Parsed with Aria' : '⚠ AI unavailable — regex fallback only'}
                </span>
              </div>
            )}
            {parseError && (
              <div className="mt-3 bg-[#FEF2F2] border border-[#FEE2E2] rounded p-2 text-sm text-[#B91C1C] flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" /> {parseError}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Review grid */}
        {candidates.length > 0 && (
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-[#0B0B3B]">Review extracted treaties ({candidates.length})</h3>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => { setCandidates([]); setParseInfo(null) }}>
                    Discard
                  </Button>
                  <Button size="sm" onClick={commitAll} disabled={committing || candidates.some(c => !c.reinsurer_id)}
                    leftIcon={<CheckCircle2 className="w-3.5 h-3.5" />}>
                    {committing ? 'Committing…' : `Commit ${candidates.length} treaty${candidates.length === 1 ? '' : 'ies'}`}
                  </Button>
                </div>
              </div>
              {candidates.some(c => !c.reinsurer_id) && (
                <div className="mb-3 bg-[#FFFBEB] border border-[#FDE68A] rounded p-2 text-xs text-[#78350F]">
                  Some rows need a reinsurer pick before they can be committed. Use the dropdown in the Reinsurer column.
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                    <tr>
                      {['Treaty #','Description','Reinsurer','Type','LoB','Inception','Expiry','Cession %','Commission %','Currency','Conf.','Warnings',''].map(h => (
                        <th key={h} className="text-left px-3 py-2 font-semibold text-[#374151]">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {candidates.map((c, idx) => (
                      <tr key={idx} className="border-b border-[#F3F4F6] align-top">
                        <td className="px-3 py-2"><input className={inp} value={c.treaty_number} onChange={e => updateCandidate(idx, { treaty_number: e.target.value })} /></td>
                        <td className="px-3 py-2"><input className={inp} value={c.description} onChange={e => updateCandidate(idx, { description: e.target.value })} /></td>
                        <td className="px-3 py-2">
                          <select className={inp} value={c.reinsurer_id || ''} onChange={e => updateCandidate(idx, { reinsurer_id: e.target.value || null, needs_reinsurer: !e.target.value })}>
                            <option value="">— pick reinsurer —</option>
                            {reinsurers.map(r => <option key={r.id} value={r.id}>{r.short_code} · {r.name}</option>)}
                          </select>
                          {c.reinsurer_name && !c.reinsurer_id && (
                            <div className="text-[10px] text-[#9CA3AF] mt-1">AI saw: {c.reinsurer_name}</div>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <select className={inp} value={c.treaty_type || ''} onChange={e => updateCandidate(idx, { treaty_type: e.target.value })}>
                            <option value="">—</option>
                            <option value="quota_share">Quota Share</option>
                            <option value="surplus">Surplus</option>
                            <option value="xl">Excess of Loss</option>
                            <option value="stop_loss">Stop Loss</option>
                            <option value="facultative">Facultative</option>
                          </select>
                        </td>
                        <td className="px-3 py-2"><input className={inp} value={c.line_of_business} onChange={e => updateCandidate(idx, { line_of_business: e.target.value })} /></td>
                        <td className="px-3 py-2"><input className={inp} type="date" value={c.inception_date || ''} onChange={e => updateCandidate(idx, { inception_date: e.target.value })} /></td>
                        <td className="px-3 py-2"><input className={inp} type="date" value={c.expiry_date || ''} onChange={e => updateCandidate(idx, { expiry_date: e.target.value })} /></td>
                        <td className="px-3 py-2"><input className={inp} type="number" step="0.01" value={c.cession_share_percent || ''} onChange={e => updateCandidate(idx, { cession_share_percent: e.target.value })} /></td>
                        <td className="px-3 py-2"><input className={inp} type="number" step="0.01" value={c.commission_percent || ''} onChange={e => updateCandidate(idx, { commission_percent: e.target.value })} /></td>
                        <td className="px-3 py-2"><input className={inp + ' w-16'} value={c.currency_code} onChange={e => updateCandidate(idx, { currency_code: e.target.value.toUpperCase() })} /></td>
                        <td className="px-3 py-2 font-mono">{(c.confidence * 100).toFixed(0)}%</td>
                        <td className="px-3 py-2 max-w-[200px]">
                          {c.warnings.length > 0 ? (
                            <ul className="text-[10px] text-[#B45309]">
                              {c.warnings.map((w, i) => <li key={i}>• {w}</li>)}
                            </ul>
                          ) : <span className="text-[#9CA3AF]">—</span>}
                        </td>
                        <td className="px-3 py-2"><button onClick={() => removeCandidate(idx)} className="text-[#DC2626]"><Trash2 className="w-3.5 h-3.5" /></button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}

        {commitResult && (
          <div className={`rounded-lg p-3 text-sm ${commitResult.errors.length ? 'bg-[#FFFBEB] text-[#78350F] border border-[#FDE68A]' : 'bg-[#ECFDF5] text-[#047857] border border-[#A7F3D0]'}`}>
            <p className="font-semibold">Commit complete</p>
            <p className="mt-1">{commitResult.created} created · {commitResult.updated} updated · {commitResult.errors.length} error(s)</p>
            {commitResult.errors.length > 0 && (
              <ul className="mt-2 text-xs">
                {commitResult.errors.map((e, i) => <li key={i}>• {e.treaty_number}: {e.error}</li>)}
              </ul>
            )}
          </div>
        )}

        {/* v1 banner (kept for the Cessions/Recoveries note) */}
        <div
          className="rounded-lg p-4 flex items-start gap-3"
          style={{ background: '#FFFBEB', border: '1px solid #FDE68A' }}
        >
          <Shield className="w-5 h-5 text-[#B45309] flex-shrink-0 mt-0.5" />
          <div className="text-sm text-[#78350F]">
            <p className="font-semibold mb-1">Cessions + recoveries still managed via API.</p>
            <p>
              Cessions ({' '}
              <code>POST /api/v1/reinsurance-cessions/{'{id}'}/post_cession/</code>) book
              {' '}DR 4200 / CR 2120; recoveries book DR 1230 / CR 5200. Use Django
              admin (<code>/admin/reinsurance/</code>) for full CRUD on those.
            </p>
          </div>
        </div>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && treaties.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <Shield className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No reinsurance treaties yet.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Create reinsurers and treaties in Django admin under{' '}
                  <code>/admin/reinsurance/</code>.
                </p>
              </div>
            )}
            {!loading && treaties.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Treaty #</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Reinsurer</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Type</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Line of Business</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Period</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Cession %</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Commission %</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {treaties.map((t) => {
                      const c = STATUS_BADGE[t.status] || STATUS_BADGE.draft
                      return (
                        <tr key={t.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">{t.treaty_number}</td>
                          <td className="px-4 py-2.5 text-[#374151]">
                            <span className="font-mono text-xs">{t.reinsurer_short_code}</span>
                            <span className="text-[#9CA3AF] text-xs ml-1.5">{t.reinsurer_name}</span>
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] text-xs">{t.treaty_type_display}</td>
                          <td className="px-4 py-2.5 text-[#374151] text-xs">{t.line_of_business}</td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs font-mono">
                            {t.inception_date} → {t.expiry_date}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtPct(t.cession_share_percent)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtPct(t.commission_percent)}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
                            >
                              {t.status_display}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
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
