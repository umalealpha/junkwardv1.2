'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getHealthCarePlans, submitHealthQuickQuote, getToken,
} from '@/lib/api'
import type { HCPlan, HCQuoteResult } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, CheckCircle2, Upload, Sparkles, FileText, Send,
  Calculator, Stethoscope,
} from 'lucide-react'
import { localYmd } from '@/lib/utils'

function todayISO() { return localYmd(new Date()) }

export default function HealthQuickQuotePage() {
  const router = useRouter()

  const [plans, setPlans]     = useState<HCPlan[]>([])
  const [plan, setPlan]       = useState('AD_CORE')
  const [quoteDate, setQuoteDate] = useState(todayISO())
  const [file, setFile]       = useState<File | null>(null)
  const [rawText, setRawText] = useState('')

  const [submitting, setSubmitting] = useState(false)
  const [result, setResult]   = useState<HCQuoteResult | null>(null)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getHealthCarePlans()
      .then((r) => setPlans(r.plans || []))
      .catch(() => setPlans([]))
  }, [router])

  const onSubmit = useCallback(async () => {
    if (!file && !rawText.trim()) {
      setError('Pick a file OR paste text describing the lives.')
      return
    }
    setSubmitting(true); setError(null); setResult(null)
    try {
      const r = await submitHealthQuickQuote({
        file:       file || undefined,
        raw_text:   rawText.trim() || undefined,
        plan_code:  plan,
        quote_date: quoteDate,
      })
      setResult(r)
      if (!r.success) setError(r.detail || 'Quote failed.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Quote failed.')
    } finally {
      setSubmitting(false)
    }
  }, [file, rawText, plan, quoteDate])

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0])
  }

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar title="Health Care — Quick Quote"
              subtitle="Upload a doc with age / DOB → Aria extracts → office + Hannover Re cost + margin" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}

        {/* Input card */}
        <Card>
          <CardContent className="p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Stethoscope className="w-5 h-5 text-[#F4A623]" />
              <h3 className="text-lg font-medium">1. Tell ARIA who to quote</h3>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="text-xs font-medium text-gray-600">Plan</label>
                <select value={plan} onChange={(e) => setPlan(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm">
                  {plans.map((p) => (
                    <option key={p.code} value={p.code}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-gray-600">Quote date</label>
                <input type="date" value={quoteDate}
                  onChange={(e) => setQuoteDate(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm" />
              </div>
              <div className="flex items-end">
                <Button onClick={onSubmit} disabled={submitting}
                  className="w-full bg-[#F4A623] hover:bg-[#d68f1a] text-[#0D1B2A] font-semibold">
                  <Send className="w-4 h-4 mr-1" />
                  {submitting ? 'Quoting…' : 'Quote it'}
                </Button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div onDragOver={(e) => e.preventDefault()} onDrop={onDrop}
                className="border-2 border-dashed rounded p-6 text-center
                           hover:border-[#F4A623] cursor-pointer bg-white"
                onClick={() => document.getElementById('hc-file')?.click()}>
                <Upload className="w-6 h-6 mx-auto text-gray-500 mb-2" />
                <p className="text-sm text-gray-700">
                  {file
                    ? <><b>{file.name}</b> ({(file.size/1024).toFixed(1)} KB)</>
                    : 'Drop a PDF, image, XLSX, CSV, DOCX or TXT here'}
                </p>
                <p className="text-xs text-gray-500 mt-1">Max 10 MB.</p>
                <input id="hc-file" type="file" hidden
                  accept=".pdf,.png,.jpg,.jpeg,.xlsx,.csv,.docx,.txt"
                  onChange={(e) => setFile(e.target.files?.[0] || null)} />
              </div>

              <div>
                <label className="text-xs font-medium text-gray-600">
                  …or paste text (broker WhatsApp, email, etc.)
                </label>
                <textarea value={rawText} onChange={(e) => setRawText(e.target.value)}
                  rows={5}
                  placeholder='e.g. "Please quote Mr X age 38 male main, Mrs X age 35 female spouse, child age 10 male."'
                  className="w-full border rounded px-3 py-2 text-sm" />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Results */}
        {result && result.success && (
          <>
            {/* Totals card — RI cost prominent */}
            <Card>
              <CardContent className="p-6">
                <div className="flex items-center gap-2 mb-3">
                  <Calculator className="w-5 h-5 text-[#F4A623]" />
                  <h3 className="text-lg font-medium">Totals — {result.plan_name}</h3>
                  <span className="ml-auto text-xs text-gray-500">
                    quote {result.quote_date}
                  </span>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-sm">
                  <Stat label="Office (monthly)" v={result.totals?.office_monthly_bwp} />
                  <Stat label="Reinsurance cost"  v={result.totals?.ri_monthly_bwp} highlight />
                  <Stat label="Gross margin"      v={result.totals?.gross_margin_monthly_bwp} />
                  <Stat label="Broker comm"       v={result.totals?.broker_commission_bwp} />
                  <Stat label="NBFIRA levy"       v={result.totals?.nbfira_levy_bwp} />
                  <Stat label="Net AD margin"     v={result.totals?.net_ad_margin_bwp} bold />
                </div>
                <p className="text-xs text-gray-500 mt-3">
                  All values in BWP per month. RI cost = Hannover Re quote per
                  life summed. Annual = monthly × 12.
                </p>
              </CardContent>
            </Card>

            {/* Lives table */}
            <Card>
              <CardContent className="p-0">
                <div className="px-4 py-3 border-b flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-gray-500" />
                  <h3 className="font-medium">Lives ({result.lives?.length ?? 0})</h3>
                </div>
                {(result.lives || []).length === 0 ? (
                  <div className="p-12 text-center text-gray-500 text-sm">
                    No lives extracted. Try pasting text in the box above.
                  </div>
                ) : (
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 border-b text-xs uppercase text-gray-600">
                      <tr className="text-left">
                        <th className="px-4 py-2">Age</th>
                        <th className="px-4 py-2">Gender</th>
                        <th className="px-4 py-2">Category</th>
                        <th className="px-4 py-2 text-right">Office (BWP/mo)</th>
                        <th className="px-4 py-2 text-right">RI (BWP/mo)</th>
                        <th className="px-4 py-2 text-right">Margin</th>
                        <th className="px-4 py-2 text-right">Margin %</th>
                        <th className="px-4 py-2 text-right">Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.lives?.map((L, i) => (
                        <tr key={i} className="border-b border-gray-100">
                          <td className="px-4 py-2 font-mono">{L.age_years}</td>
                          <td className="px-4 py-2">{L.gender}</td>
                          <td className="px-4 py-2 text-xs">{L.life_category}</td>
                          <td className="px-4 py-2 text-right font-mono">{L.office_monthly_bwp}</td>
                          <td className="px-4 py-2 text-right font-mono text-amber-700">{L.ri_monthly_bwp}</td>
                          <td className="px-4 py-2 text-right font-mono">{L.margin_monthly_bwp}</td>
                          <td className="px-4 py-2 text-right text-xs">
                            {(Number(L.margin_pct) * 100).toFixed(1)}%
                          </td>
                          <td className="px-4 py-2 text-right text-xs">
                            <span className={L.extraction_confidence < 0.6
                              ? 'text-amber-700 font-medium' : 'text-gray-600'}>
                              {(L.extraction_confidence * 100).toFixed(0)}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>

            {result.warnings && result.warnings.length > 0 && (
              <Card className="border-amber-200 bg-amber-50">
                <CardContent className="p-3">
                  <div className="text-xs font-medium text-amber-900 mb-1">Warnings</div>
                  <ul className="text-xs text-amber-900 list-disc pl-5 space-y-1">
                    {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                </CardContent>
              </Card>
            )}

            {result.python_code && (
              <Card>
                <CardContent className="p-0">
                  <div className="px-4 py-3 border-b flex items-center gap-2">
                    <FileText className="w-4 h-4 text-gray-500" />
                    <h3 className="font-medium">Code generated by Aria (audit)</h3>
                  </div>
                  <pre className="p-4 text-xs font-mono bg-gray-50 overflow-x-auto">
                    {result.python_code}
                  </pre>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Stat({ label, v, highlight, bold }: {
  label: string; v?: string; highlight?: boolean; bold?: boolean
}) {
  return (
    <div className={`rounded border p-3 ${highlight ? 'bg-[#FFF7E6] border-[#F4A623]' : 'bg-white'}`}>
      <div className="text-xs text-gray-600">{label}</div>
      <div className={`mt-1 font-mono ${bold ? 'text-lg font-semibold text-[#0D1B2A]' : 'text-sm'}`}>
        BWP {v ?? '—'}
      </div>
    </div>
  )
}
