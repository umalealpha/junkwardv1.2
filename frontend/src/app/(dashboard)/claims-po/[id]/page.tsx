'use client'

// Claims PO — review screen (Phase 4 of the claims-PO engine port).
//
// RULE: the server /plan/ endpoint owns ALL money math (allocation, markup,
// excess matrix, VAT, the repairer/parts split). This page NEVER recomputes a
// number in JS — every control change PATCHes the inputs then re-GETs /plan/
// and re-renders the server's figures.

import { useEffect, useState, useCallback } from 'react'
import { useRouter, useParams } from 'next/navigation'
import Link from 'next/link'
import {
  getClaimsAssessment, getClaimsPlan, patchClaimsAssessment, createClaimsPos,
  getPurchaseOrder, emailPurchaseOrder, claimsReviewCheck, getToken,
} from '@/lib/api'
import type {
  ClaimsAssessmentDetail, ClaimsPlanResponse, ClaimsMeta,
  ClaimsSupplierSettings, ClaimsAssessmentPatch, ClaimsPoResult,
  PurchaseOrderDetail, ClaimsReviewFlag,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft, AlertCircle, CheckCircle2, FileText, Mail, RefreshCw, Save,
  Sparkles,
} from 'lucide-react'

// Display-only formatting — matches the PO pages' Intl style, P-prefixed (BWP).
const fmtP = (v: string | number | null | undefined): string => {
  if (v === null || v === undefined) return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return 'P' + new Intl.NumberFormat('en-BW', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(n)
}

const EMPTY_META: ClaimsMeta = {
  claims_type: '', policy_number: '', claim_number: '', assessment_number: '',
  claim_description: '', ad_note: '', client_name: '', registration: '',
  vehicle: '', contact_details: '',
}

interface ControlInputs {
  excess_pct: string
  excess_min: string
  excess_amount: string
  markup_pct: string
}

const numToInput = (v: number | null): string => (v === null || v === undefined ? '' : String(v))
const inputToNum = (s: string): number | null => {
  const t = s.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

export default function ClaimsPoReviewPage() {
  const router = useRouter()
  const params = useParams()
  const id = params?.id as string

  const [assessment, setAssessment] = useState<ClaimsAssessmentDetail | null>(null)
  const [planResp, setPlanResp]     = useState<ClaimsPlanResponse | null>(null)
  const [loading, setLoading]       = useState(true)
  const [saving, setSaving]         = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError]           = useState<string | null>(null)
  const [info, setInfo]             = useState<string | null>(null)

  // ── Editable state (initialised from the server, PATCHed back verbatim) ──
  const [meta, setMeta]                 = useState<ClaimsMeta>(EMPTY_META)
  const [controls, setControls]         = useState<ControlInputs>({
    excess_pct: '', excess_min: '', excess_amount: '', markup_pct: '',
  })
  const [rowVendors, setRowVendors]     = useState<Record<string, string>>({})
  // Only vendors the user (or a previous save) explicitly set live here —
  // untouched vendors keep the server's own default (SA-name heuristic).
  const [supplierSettings, setSupplierSettings] = useState<ClaimsSupplierSettings>({})

  const [genResults, setGenResults] = useState<ClaimsPoResult[] | null>(null)
  const [genErrors, setGenErrors]   = useState<{ error: string; vendor?: string; kind?: string }[]>([])

  // ── "Send POs to suppliers" (CFO 2026-07-07) ─────────────────────────────
  // Per created PO: its full detail (supplier_email + last_emailed_* live
  // there), the editable "to" address, and the in-flight / error state.
  const [poDetails, setPoDetails]   = useState<Record<string, PurchaseOrderDetail>>({})
  const [sendEmails, setSendEmails] = useState<Record<string, string>>({})
  const [sendingIds, setSendingIds] = useState<Record<string, boolean>>({})
  const [sendErrors, setSendErrors] = useState<Record<string, string>>({})
  const [sendAllBusy, setSendAllBusy] = useState(false)
  const [sendInfo, setSendInfo]     = useState<string | null>(null)

  // ── Aria's pre-send checks (CFO feature #4, 2026-07-08) ──────────────────
  const [checks, setChecks]     = useState<ClaimsReviewFlag[] | null>(null)
  const [checking, setChecking] = useState(false)
  const runChecks = useCallback(async () => {
    setChecking(true)
    try { setChecks((await claimsReviewCheck(id)).flags) }
    catch { setChecks(null) }
    finally { setChecking(false) }
  }, [id])
  useEffect(() => {
    if (planResp && assessment?.status === 'ready_for_review') runChecks()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planResp, assessment?.status, runChecks])

  const applyPlan = useCallback((p: ClaimsPlanResponse) => {
    setPlanResp(p)
    setMeta({
      claims_type:       p.meta.claims_type ?? '',
      policy_number:     p.meta.policy_number ?? '',
      claim_number:      p.meta.claim_number ?? '',
      assessment_number: p.meta.assessment_number ?? '',
      claim_description: p.meta.claim_description ?? '',
      ad_note:           p.meta.ad_note ?? '',
      client_name:       p.meta.client_name ?? '',
      registration:      p.meta.registration ?? '',
      vehicle:           p.meta.vehicle ?? '',
      contact_details:   p.meta.contact_details ?? '',
    })
    setControls({
      excess_pct:    numToInput(p.saved.excess_pct),
      excess_min:    numToInput(p.saved.excess_min),
      excess_amount: numToInput(p.saved.excess_amount),
      markup_pct:    numToInput(p.saved.markup_pct),
    })
    const overrides: Record<string, string> = {}
    for (const a of p.saved.line_allocations || []) {
      if (a && a.id && a.vendor) overrides[a.id] = a.vendor
    }
    const rv: Record<string, string> = {}
    for (const row of p.plan.rows) rv[row.id] = overrides[row.id] ?? row.default_vendor
    setRowVendors(rv)
    setSupplierSettings(p.saved.supplier_settings || {})
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const a = await getClaimsAssessment(id)
      setAssessment(a)
      if (a.status === 'parse_failed' || a.status === 'failed') return
      const p = await getClaimsPlan(id)
      applyPlan(p)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load assessment')
    } finally {
      setLoading(false)
    }
  }, [id, applyPlan])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  // ── PATCH then re-GET /plan/ so the numbers recompute SERVER-side ────────
  const save = useCallback(async (next?: {
    rowVendors?: Record<string, string>
    supplierSettings?: ClaimsSupplierSettings
  }) => {
    if (!planResp) return
    const rv = next?.rowVendors ?? rowVendors
    const ss = next?.supplierSettings ?? supplierSettings
    const line_allocations = planResp.plan.rows
      .filter((r) => (rv[r.id] ?? r.default_vendor) !== r.default_vendor)
      .map((r) => ({ id: r.id, vendor: rv[r.id] }))
    const body: ClaimsAssessmentPatch = {
      line_allocations,
      supplier_settings: ss,
      excess_pct:    inputToNum(controls.excess_pct),
      excess_min:    inputToNum(controls.excess_min),
      excess_amount: inputToNum(controls.excess_amount),
      markup_pct:    inputToNum(controls.markup_pct),
      ...meta,
    }
    setSaving(true)
    setError(null)
    setInfo(null)
    try {
      const updated = await patchClaimsAssessment(id, body)
      setAssessment(updated)
      const p = await getClaimsPlan(id)
      applyPlan(p)
      setInfo('Saved — totals recalculated.')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }, [planResp, rowVendors, supplierSettings, controls, meta, id, applyPlan])

  function onVendorChange(rowId: string, vendor: string) {
    const rv = { ...rowVendors, [rowId]: vendor }
    setRowVendors(rv)
    save({ rowVendors: rv })
  }

  function onSupplierSetting(label: string, patch: { vat?: boolean | undefined; currency?: 'BWP' | 'ZAR' | undefined }) {
    const current = { ...(supplierSettings[label] || {}) }
    if ('vat' in patch) {
      if (patch.vat === undefined) delete current.vat
      else current.vat = patch.vat
    }
    if ('currency' in patch) {
      if (patch.currency === undefined) delete current.currency
      else current.currency = patch.currency
    }
    const ss = { ...supplierSettings }
    if (Object.keys(current).length === 0) delete ss[label]
    else ss[label] = current
    setSupplierSettings(ss)
    save({ supplierSettings: ss })
  }

  async function generatePos() {
    // Confirm before creating — there is no undo on draft-PO generation
    // (Manus audit 2026-07-08).
    if (!window.confirm(
      'Create the two draft purchase orders (Repairer + Parts) for this '
      + 'assessment? They will appear under Purchase Orders as drafts.')) {
      return
    }
    setGenerating(true)
    setError(null)
    setInfo(null)
    setGenResults(null)
    setGenErrors([])
    try {
      const res = await createClaimsPos(id)
      setGenResults(res.results)
      setGenErrors(res.errors || [])
      if ((res.errors || []).length === 0) {
        setInfo('Draft purchase orders created.')
      } else {
        setInfo(res.results.length > 0
          ? 'Some POs were created, but there were errors — fix and retry.'
          : null)
      }
      // Refresh status (+ po_results on a clean run).
      const a = await getClaimsAssessment(id)
      setAssessment(a)
    } catch (e) {
      const err = e as Error & { status?: number }
      if (err.status === 409) {
        setInfo('Purchase orders were already created for this assessment.')
        try { setAssessment(await getClaimsAssessment(id)) } catch { /* keep current */ }
      } else {
        setError(err.message || 'PO generation failed')
      }
    } finally {
      setGenerating(false)
    }
  }

  // ── Created POs (before the early returns — the fetch effect needs them) ──
  const posCreated  = assessment?.status === 'pos_created'
  const existingPos = posCreated ? (assessment?.po_results || []) : []
  const shownResults = genResults ?? (existingPos.length > 0 ? existingPos : null)

  // Fetch each created PO's detail (supplier_email prefill + Sent ✓ state).
  // Keyed on the joined id list so the effect re-runs only when the set of
  // POs changes — not on every render of the derived array.
  const resultIdsKey = (shownResults ?? []).map((r) => r.id).join(',')
  useEffect(() => {
    if (!resultIdsKey) return
    let cancelled = false
    ;(async () => {
      const entries = await Promise.all(resultIdsKey.split(',').map(async (pid) => {
        try { return [pid, await getPurchaseOrder(pid)] as const } catch { return null }
      }))
      if (cancelled) return
      const map: Record<string, PurchaseOrderDetail> = {}
      for (const e of entries) if (e) map[e[0]] = e[1]
      setPoDetails((prev) => ({ ...prev, ...map }))
      // Prefill the "to" address from the supplier's email on file — but
      // never clobber something the user already typed.
      setSendEmails((prev) => {
        const next = { ...prev }
        for (const [pid, d] of Object.entries(map)) {
          if (next[pid] === undefined) next[pid] = d.supplier_email || ''
        }
        return next
      })
    })()
    return () => { cancelled = true }
  }, [resultIdsKey])

  async function sendOne(poId: string): Promise<boolean> {
    const to = (sendEmails[poId] || '').trim()
    if (!to) {
      setSendErrors((errs) => ({ ...errs, [poId]: 'Enter the supplier email first.' }))
      return false
    }
    setSendingIds((s) => ({ ...s, [poId]: true }))
    setSendErrors((errs) => {
      const next = { ...errs }; delete next[poId]; return next
    })
    try {
      await emailPurchaseOrder(poId, to)
      // Optimistic Sent ✓, then re-fetch the PO for the server's stamp.
      setPoDetails((m) => (m[poId]
        ? { ...m, [poId]: { ...m[poId], last_emailed_at: new Date().toISOString(), last_emailed_to: to } }
        : m))
      getPurchaseOrder(poId)
        .then((d) => setPoDetails((m) => ({ ...m, [poId]: d })))
        .catch(() => { /* keep the optimistic state */ })
      return true
    } catch (e) {
      setSendErrors((errs) => ({
        ...errs, [poId]: e instanceof Error ? e.message : 'Send failed',
      }))
      return false
    } finally {
      setSendingIds((s) => ({ ...s, [poId]: false }))
    }
  }

  async function sendAll() {
    if (!shownResults) return
    const targets = shownResults.filter((r) => !poDetails[r.id]?.last_emailed_at)
    if (targets.length === 0) return
    setSendAllBusy(true)
    setSendInfo(null)
    let ok = 0
    for (const r of targets) {
      if (await sendOne(r.id)) ok++
    }
    setSendInfo(`${ok} of ${targets.length} sent`)
    setSendAllBusy(false)
  }

  // ── Loading / failure states ──────────────────────────────────────────────
  if (loading || !assessment) {
    return (
      <div className="min-h-screen bg-[#F8F9FA]">
        <TopBar title="Claims PO Review" subtitle="..." />
        <div className="p-8 text-gray-500">Loading...</div>
      </div>
    )
  }

  const parseFailed = assessment.status === 'parse_failed' || assessment.status === 'failed'

  if (parseFailed) {
    return (
      <div className="min-h-screen bg-[#F8F9FA]">
        <TopBar
          title={assessment.claim_number || 'Claims PO Review'}
          subtitle={assessment.status_display}
        />
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
          <Link href="/claims-po" className="text-sm text-gray-600 hover:text-gray-800 inline-flex items-center gap-1">
            <ArrowLeft className="w-4 h-4" /> Back to list
          </Link>
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-4 flex items-start gap-2">
              <AlertCircle className="w-5 h-5 text-red-700 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-red-800">
                  This assessment could not be processed ({assessment.status_display}).
                </p>
                <p className="text-sm text-red-700 mt-1">
                  {assessment.parse_error || 'No parse error was recorded.'}
                </p>
                <p className="text-sm text-red-700 mt-2">
                  Upload a corrected PDF from the Claims PO list.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    )
  }

  if (!planResp) {
    return (
      <div className="min-h-screen bg-[#F8F9FA]">
        <TopBar title={assessment.claim_number || 'Claims PO Review'} subtitle={assessment.status_display} />
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
          <Link href="/claims-po" className="text-sm text-gray-600 hover:text-gray-800 inline-flex items-center gap-1">
            <ArrowLeft className="w-4 h-4" /> Back to list
          </Link>
          {error && (
            <Card className="border-red-200 bg-red-50">
              <CardContent className="p-3 flex items-start gap-2">
                <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
                <span className="text-sm text-red-700">{error}</span>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    )
  }

  const { split } = planResp
  const spec  = split.specialised
  const parts = split.motor_centre

  // Distinct vendor labels drive the per-supplier VAT/currency block.
  const vendorLabels: string[] = []
  const seen = new Set<string>()
  for (const v of [...planResp.plan.vendor_options, ...Object.values(rowVendors)]) {
    if (v && !seen.has(v)) { vendorLabels.push(v); seen.add(v) }
  }

  const busy = saving || generating

  const metaFields: { key: keyof ClaimsMeta; label: string; wide?: boolean }[] = [
    { key: 'policy_number',     label: 'Policy number' },
    { key: 'claim_number',      label: 'Claim number' },
    { key: 'assessment_number', label: 'Assessment number' },
    { key: 'claims_type',       label: 'Claims type' },
    { key: 'client_name',       label: 'Client name' },
    { key: 'registration',      label: 'Registration' },
    { key: 'vehicle',           label: 'Vehicle' },
    { key: 'contact_details',   label: 'Contact details' },
    { key: 'claim_description', label: 'Claim description', wide: true },
    { key: 'ad_note',           label: 'AD note',           wide: true },
  ]

  return (
    <div className="min-h-screen bg-[#F8F9FA]">
      <TopBar
        title={assessment.claim_number || 'Claims PO Review'}
        subtitle={`${assessment.client_name || '—'} — ${assessment.status_display}`}
      />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4">
        <div className="flex items-center justify-between">
          <Link href="/claims-po" className="text-sm text-gray-600 hover:text-gray-800 inline-flex items-center gap-1">
            <ArrowLeft className="w-4 h-4" /> Back to list
          </Link>
          {saving && (
            <span className="text-xs text-gray-500 inline-flex items-center gap-1">
              <RefreshCw className="w-3 h-3 animate-spin" /> Saving &amp; recalculating…
            </span>
          )}
        </div>

        {error && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-3 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-700 mt-0.5" />
              <span className="text-sm text-red-700">{error}</span>
            </CardContent>
          </Card>
        )}
        {info && (
          <Card className="border-emerald-200 bg-emerald-50">
            <CardContent className="p-3 flex items-start gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-700 mt-0.5" />
              <span className="text-sm text-emerald-700">{info}</span>
            </CardContent>
          </Card>
        )}

        {/* ── Claim meta ─────────────────────────────────────────────────── */}
        <Card>
          <CardContent className="p-6">
            <h2 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-4">
              Claim details
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              {metaFields.map((f) => (
                <div key={f.key} className={f.wide ? 'md:col-span-2' : ''}>
                  <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor={`meta-${f.key}`}>
                    {f.label}
                  </label>
                  <input
                    id={`meta-${f.key}`}
                    type="text"
                    value={meta[f.key]}
                    onChange={(e) => setMeta({ ...meta, [f.key]: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  />
                </div>
              ))}
            </div>
            <p className="text-xs text-gray-500 mt-3">
              Meta edits are saved with the <span className="font-medium">Save</span> button below.
            </p>
          </CardContent>
        </Card>

        {/* ── Controls (excess + markup) ────────────────────────────────── */}
        <Card>
          <CardContent className="p-6">
            <h2 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-4">
              Excess &amp; markup
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor="ctl-excess-pct">Excess %</label>
                <input
                  id="ctl-excess-pct" type="number" step="0.01" min="0"
                  value={controls.excess_pct}
                  onChange={(e) => setControls({ ...controls, excess_pct: e.target.value })}
                  onBlur={() => save()}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g. 5"
                />
              </div>
              <div>
                <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor="ctl-excess-min">Excess minimum (P)</label>
                <input
                  id="ctl-excess-min" type="number" step="0.01" min="0"
                  value={controls.excess_min}
                  onChange={(e) => setControls({ ...controls, excess_min: e.target.value })}
                  onBlur={() => save()}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g. 5000"
                />
              </div>
              <div>
                <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor="ctl-excess-amount">Excess amount (P) — override</label>
                <input
                  id="ctl-excess-amount" type="number" step="0.01" min="0"
                  value={controls.excess_amount}
                  onChange={(e) => setControls({ ...controls, excess_amount: e.target.value })}
                  onBlur={() => save()}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="overrides % / min"
                />
              </div>
              <div>
                <label className="block text-xs uppercase text-gray-600 mb-1" htmlFor="ctl-markup-pct">Markup %</label>
                <input
                  id="ctl-markup-pct" type="number" step="0.01" min="0"
                  value={controls.markup_pct}
                  onChange={(e) => setControls({ ...controls, markup_pct: e.target.value })}
                  onBlur={() => save()}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                  placeholder="e.g. 25"
                />
              </div>
            </div>
            <p className="text-xs text-gray-500 mt-3">
              A typed excess <span className="font-medium">amount</span> overrides the % and minimum.
              Markup applies to <span className="font-medium">supplier parts only</span> — never the
              repairer&apos;s own parts. All figures recompute on the server after each change.
            </p>
          </CardContent>
        </Card>

        {/* ── Allocation table ──────────────────────────────────────────── */}
        <Card>
          <CardContent className="p-0">
            <div className="p-6 pb-0">
              <h2 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide">
                Line allocation
              </h2>
              <p className="text-xs text-gray-500 mt-1 mb-3">
                Assign each block to the vendor that will be paid for it. Repairer: {' '}
                <span className="font-medium">{planResp.plan.repairer}</span>.
              </p>
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr className="text-left text-xs uppercase text-gray-600">
                  <th className="px-4 py-3">Category</th>
                  <th className="px-4 py-3">Item</th>
                  <th className="px-4 py-3 text-right">Amount (BWP)</th>
                  <th className="px-4 py-3">Vendor</th>
                </tr>
              </thead>
              <tbody>
                {planResp.plan.rows.map((row) => (
                  <tr key={row.id} className="border-b border-gray-100">
                    <td className="px-4 py-3 text-gray-600">{row.category}</td>
                    <td className="px-4 py-3">{row.label}</td>
                    <td className="px-4 py-3 text-right font-mono">{fmtP(row.amount)}</td>
                    <td className="px-4 py-3">
                      <select
                        value={rowVendors[row.id] ?? row.default_vendor}
                        aria-label={`Vendor for ${row.label}`}
                        disabled={busy}
                        onChange={(e) => onVendorChange(row.id, e.target.value)}
                        className="px-2 py-1.5 border border-gray-300 rounded-md text-sm max-w-[260px]"
                      >
                        {/* Keep a saved custom vendor selectable even if it's
                            not in vendor_options */}
                        {!planResp.plan.vendor_options.includes(rowVendors[row.id] ?? row.default_vendor) && (
                          <option value={rowVendors[row.id] ?? row.default_vendor}>
                            {rowVendors[row.id] ?? row.default_vendor}
                          </option>
                        )}
                        {planResp.plan.vendor_options.map((v) => (
                          <option key={v} value={v}>{v}</option>
                        ))}
                      </select>
                      {(rowVendors[row.id] ?? row.default_vendor) !== row.default_vendor && (
                        <span className="ml-2 text-[11px] text-orange-600">reassigned</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        {/* ── Per-supplier VAT / currency ───────────────────────────────── */}
        <Card>
          <CardContent className="p-6">
            <h2 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-1">
              Supplier VAT &amp; currency
            </h2>
            <p className="text-xs text-gray-500 mb-4">
              &quot;Auto&quot; keeps the engine default (SA-named suppliers → no VAT, ZAR record;
              everyone else → 14% VAT, BWP). ZAR is <span className="font-medium">recorded only —
              the PO stays BWP, no conversion</span>.
            </p>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr className="text-left text-xs uppercase text-gray-600">
                  <th className="px-4 py-2">Supplier</th>
                  <th className="px-4 py-2">VAT (14%)</th>
                  <th className="px-4 py-2">Currency</th>
                </tr>
              </thead>
              <tbody>
                {vendorLabels.map((label) => {
                  const s = supplierSettings[label] || {}
                  const vatValue = s.vat === undefined ? 'auto' : (s.vat ? 'on' : 'off')
                  const ccyValue = s.currency ?? 'auto'
                  return (
                    <tr key={label} className="border-b border-gray-100">
                      <td className="px-4 py-2 font-medium">{label}</td>
                      <td className="px-4 py-2">
                        <select
                          value={vatValue}
                          aria-label={`VAT for ${label}`}
                          disabled={busy}
                          onChange={(e) => {
                            const v = e.target.value
                            onSupplierSetting(label, { vat: v === 'auto' ? undefined : v === 'on' })
                          }}
                          className="px-2 py-1.5 border border-gray-300 rounded-md text-sm"
                        >
                          <option value="auto">Auto</option>
                          <option value="on">VAT on</option>
                          <option value="off">VAT off</option>
                        </select>
                      </td>
                      <td className="px-4 py-2">
                        <select
                          value={ccyValue}
                          aria-label={`Currency for ${label}`}
                          disabled={busy}
                          onChange={(e) => {
                            const v = e.target.value
                            onSupplierSetting(label, {
                              currency: v === 'auto' ? undefined : (v as 'BWP' | 'ZAR'),
                            })
                          }}
                          className="px-2 py-1.5 border border-gray-300 rounded-md text-sm"
                        >
                          <option value="auto">Auto</option>
                          <option value="BWP">BWP</option>
                          <option value="ZAR">ZAR (recorded only)</option>
                        </select>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>

        {/* ── Live split summary (server figures — never recomputed here) ── */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card>
            <CardContent className="p-6">
              <h3 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-3">
                Repairer PO — {planResp.plan.repairer}
              </h3>
              <dl className="space-y-1.5 text-sm">
                {(spec.repairer_parts ?? 0) > 0 && (
                  <SplitLine label="Parts (own)" value={fmtP(spec.repairer_parts)} />
                )}
                <SplitLine label="Labour"   value={fmtP(spec.labour)} />
                <SplitLine label="Paint"    value={fmtP(spec.paint)} />
                <SplitLine label="Markup"   value={fmtP(spec.markup)} />
                <SplitLine label="Sundries" value={fmtP(spec.sundries)} />
                {(spec.anti_corrosion ?? 0) > 0 && (
                  <SplitLine label="Anti-Corrosion" value={fmtP(spec.anti_corrosion)} />
                )}
                {(spec.underside_paint ?? 0) > 0 && (
                  <SplitLine label="Underside Paint" value={fmtP(spec.underside_paint)} />
                )}
                <SplitLine label="Gross (excl VAT)" value={fmtP(spec.gross_excl)} strong />
                <SplitLine
                  label={`Excess (${controls.excess_amount.trim() !== '' ? 'typed' : `${spec.excess_pct != null ? (spec.excess_pct * 100).toFixed(2).replace(/\.?0+$/, '') : '—'}% / min ${fmtP(spec.excess_min)}`})`}
                  value={spec.excess ? `− ${fmtP(spec.excess)}` : fmtP(0)}
                  negative={!!spec.excess}
                />
                <div className="border-t border-gray-200 my-1.5" />
                <SplitLine label="Excl" value={fmtP(spec.excl)} />
                <SplitLine label="VAT (14%)" value={fmtP(spec.vat)} />
                <SplitLine label="Incl" value={fmtP(spec.incl)} strong />
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-6">
              <h3 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-3">
                Parts PO
              </h3>
              <dl className="space-y-1.5 text-sm">
                <SplitLine label="Parts total" value={fmtP(parts.parts_total)} />
                <div className="border-t border-gray-200 my-1.5" />
                <SplitLine label="Excl" value={fmtP(parts.excl)} />
                <SplitLine label="VAT (14%)" value={fmtP(parts.vat)} />
                <SplitLine label="Incl" value={fmtP(parts.incl)} strong />
              </dl>
            </CardContent>
          </Card>

          <Card className="border-[#F47C20]/40">
            <CardContent className="p-6">
              <h3 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-3">
                Grand total
              </h3>
              <dl className="space-y-1.5 text-sm">
                <SplitLine label="Excl" value={fmtP(split.grand.excl)} />
                <SplitLine label="VAT" value={fmtP(split.grand.vat)} />
                <SplitLine label="Incl" value={fmtP(split.grand.incl)} strong />
              </dl>
              <p className="text-xs text-gray-500 mt-3">
                Figures come from the server engine after every change — nothing is computed
                in the browser.
              </p>
            </CardContent>
          </Card>
        </div>

        {/* ── Aria's pre-send checks (feature #4, 2026-07-08) ─────────────── */}
        {!posCreated && (checking || (checks && checks.length > 0)) && (
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide flex items-center gap-1.5">
                  <Sparkles className="w-4 h-4 text-[#F47C20]" /> Aria&apos;s checks
                </h3>
                <button type="button" onClick={() => runChecks()} disabled={checking}
                  className="text-xs text-gray-500 hover:text-gray-800 inline-flex items-center gap-1">
                  <RefreshCw className={`w-3 h-3 ${checking ? 'animate-spin' : ''}`} /> Re-check
                </button>
              </div>
              {checking && !checks && <p className="text-sm text-gray-500">Checking…</p>}
              <ul className="space-y-1.5">
                {(checks || []).map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    {f.severity === 'info'
                      ? <CheckCircle2 className="w-4 h-4 text-emerald-600 mt-0.5 flex-shrink-0" />
                      : <AlertCircle className={`w-4 h-4 mt-0.5 flex-shrink-0 ${f.severity === 'high' ? 'text-red-600' : 'text-amber-500'}`} />}
                    <span className={f.severity === 'high' ? 'text-red-700' : f.severity === 'warn' ? 'text-amber-700' : 'text-gray-600'}>
                      {f.message}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-gray-400 mt-2">
                Advisory only — checks run on the server; no data leaves it.
              </p>
            </CardContent>
          </Card>
        )}

        {/* ── Actions ───────────────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={busy} onClick={() => save()}>
            <Save className="w-4 h-4 mr-1" />
            {saving ? 'Saving…' : 'Save'}
          </Button>
          <Button
            className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white"
            disabled={busy || posCreated}
            title={posCreated ? 'Purchase orders were already created for this assessment' : 'Generate the two draft POs'}
            onClick={generatePos}
          >
            <Sparkles className="w-4 h-4 mr-1" />
            {posCreated ? 'POs already created' : generating ? 'Generating…' : 'Generate draft POs'}
          </Button>
        </div>

        {/* ── Generation results / existing POs ─────────────────────────── */}
        {shownResults && shownResults.length > 0 && (
          <Card className="border-emerald-200">
            <CardContent className="p-6">
              <h3 className="text-sm font-semibold text-emerald-800 uppercase tracking-wide mb-3">
                Draft purchase orders
              </h3>
              <ul className="space-y-2">
                {shownResults.map((po) => (
                  <li key={po.id} className="flex items-center gap-3 text-sm">
                    <FileText className="w-4 h-4 text-emerald-700 flex-shrink-0" />
                    <Link href={`/purchase-orders/${po.id}`} className="font-mono text-[#0D1B2A] underline hover:text-[#F47C20]">
                      {po.po_number}
                    </Link>
                    <span className="text-gray-700">{po.supplier}</span>
                    <span className="text-xs px-2 py-0.5 rounded-full border bg-gray-50 text-gray-600 border-gray-300">
                      {po.kind}
                    </span>
                    {poDetails[po.id]?.last_emailed_at && (
                      <span className="text-xs px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-700 border-emerald-200">
                        Sent ✓
                      </span>
                    )}
                    <span className="ml-auto font-mono">{fmtP(po.total)}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {/* ── Send POs to suppliers (CFO 2026-07-07) ─────────────────────── */}
        {shownResults && shownResults.length > 0 && (
          <Card className="border-[#0D1B2A]/20">
            <CardContent className="p-6">
              <h3 className="text-sm font-semibold text-[#0D1B2A] uppercase tracking-wide mb-1">
                Send POs to suppliers
              </h3>
              <p className="text-xs text-gray-500 mb-4">
                The PDF is attached automatically and each email is sent from
                <span className="font-medium"> your own mailbox</span>.
                <span className="text-orange-600 font-medium"> Note: these POs are still DRAFT — they have not been approved.</span>
              </p>

              {sendInfo && (
                <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded p-2 inline-flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4" /> {sendInfo}
                </div>
              )}

              <ul className="space-y-3">
                {shownResults.map((po) => {
                  const detail = poDetails[po.id]
                  const sent = !!detail?.last_emailed_at
                  const sending = !!sendingIds[po.id]
                  return (
                    <li key={po.id} className="flex flex-wrap items-center gap-3 text-sm border-b border-gray-100 pb-3 last:border-b-0 last:pb-0">
                      <Link href={`/purchase-orders/${po.id}`} className="font-mono text-[#0D1B2A] underline hover:text-[#F47C20]">
                        {po.po_number}
                      </Link>
                      <span className="text-gray-700">{po.supplier}</span>
                      <span className="text-xs px-2 py-0.5 rounded-full border bg-gray-50 text-gray-600 border-gray-300">
                        {po.kind}
                      </span>
                      <span className="font-mono">{fmtP(po.total)}</span>
                      <input
                        type="email"
                        value={sendEmails[po.id] ?? ''}
                        onChange={(e) => setSendEmails((m) => ({ ...m, [po.id]: e.target.value }))}
                        placeholder="supplier@example.co.bw"
                        aria-label={`Supplier email for ${po.po_number}`}
                        disabled={sending || sendAllBusy || sent}
                        className="px-3 py-2 border border-gray-300 rounded-md text-sm flex-1 min-w-[220px]"
                      />
                      {sent ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-700 border-emerald-200">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          Sent ✓ {detail?.last_emailed_to}
                        </span>
                      ) : (
                        <Button
                          className="bg-[#0D1B2A] hover:bg-[#1a2940] text-white"
                          disabled={sending || sendAllBusy || !(sendEmails[po.id] ?? '').trim()}
                          onClick={() => { setSendInfo(null); sendOne(po.id) }}
                        >
                          <Mail className="w-4 h-4 mr-1" />
                          {sending ? 'Sending…' : 'Send'}
                        </Button>
                      )}
                      {sendErrors[po.id] && (
                        <span className="w-full text-xs text-red-700">{sendErrors[po.id]}</span>
                      )}
                    </li>
                  )
                })}
              </ul>

              {shownResults.some((r) => !poDetails[r.id]?.last_emailed_at) && (
                <div className="mt-4">
                  <Button
                    variant="outline"
                    disabled={sendAllBusy || Object.values(sendingIds).some(Boolean)}
                    onClick={sendAll}
                  >
                    <Mail className="w-4 h-4 mr-1" />
                    {sendAllBusy ? 'Sending…' : 'Send all'}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {genErrors.length > 0 && (
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-6">
              <h3 className="text-sm font-semibold text-red-800 uppercase tracking-wide mb-3">
                Generation errors — fix and retry
              </h3>
              <ul className="space-y-2">
                {genErrors.map((e, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-red-700">
                    <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                    <span>
                      {e.vendor && <span className="font-medium">{e.vendor}: </span>}
                      {e.error}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-red-700 mt-3">
                The assessment stays in review — reassign the vendor(s) above and generate again.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function SplitLine({ label, value, strong, negative }: {
  label: string; value: string; strong?: boolean; negative?: boolean
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className={`text-gray-600 ${strong ? 'font-semibold text-gray-800' : ''}`}>{label}</dt>
      <dd className={`font-mono ${negative ? 'text-red-700' : ''} ${strong ? 'font-semibold' : ''}`}>
        {value}
      </dd>
    </div>
  )
}
