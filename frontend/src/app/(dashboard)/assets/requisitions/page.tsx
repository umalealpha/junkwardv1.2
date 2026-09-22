'use client'

/**
 * /assets/requisitions — Asset Requisitions (CFO Asset Control spec 2026-09-02).
 *
 * Raise a request for a new asset purchase, or the reissue of a spare from the
 * pool, to a NAMED member of staff. The recipient is always picked from the
 * staff directory — never typed — so a requisition can only ever be raised for
 * a real employee. Material items (≥ the value threshold) run the full gate:
 * Finance Manager AND CFO both approve.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  getAssetRequisitions, createAssetRequisition, getAssetEmployees,
  getSparePool, getAssetCategories, getToken,
  type AssetRequisition, type RequisitionStatus, type RequisitionType,
  type AssetEmployeeOption, type AssetListItem, type AssetCategory,
} from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  TableWrapper, Table, TableHead, TableBody, TableRow, TableHeader, TableCell, EmptyTableRow,
} from '@/components/ui/table'
import { Modal, ModalBody, ModalFooter } from '@/components/ui/modal'
import { Badge } from '@/components/ui/badge'
import { Label, Input, Textarea, Select } from '@/components/ui/input'
import { LoadingTable } from '@/components/ui/loading'
import { SearchableSelect } from '@/components/ui/SearchableSelect'
import { pushToast } from '@/components/Toaster'
import { useTheme } from '@/contexts/ThemeContext'
import { Plus, Search, X, ClipboardList } from 'lucide-react'

// ─── Formatting ───────────────────────────────────────────────────────────────

function fmtBwp(value: string | number): string {
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (Number.isNaN(n)) return '—'
  return `P ${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)}`
}

function fmtDate(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

// Status → Badge variant. pending=amber, approved=blue, fulfilled=green,
// rejected=red, draft/cancelled=grey.
type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted' | 'orange' | 'navy'
const STATUS_VARIANT: Record<RequisitionStatus, BadgeVariant> = {
  draft:                'muted',
  pending_fm_approval:  'warning',
  pending_cfo_approval: 'warning',
  approved:             'info',
  fulfilled:            'success',
  rejected:             'danger',
  cancelled:            'muted',
}

// ─── Status filter ──────────────────────────────────────────────────────────────

type StatusFilter = 'all' | RequisitionStatus
const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all',                  label: 'All' },
  { value: 'draft',                label: 'Draft' },
  { value: 'pending_fm_approval',  label: 'Pending FM' },
  { value: 'pending_cfo_approval', label: 'Pending CFO' },
  { value: 'approved',             label: 'Approved' },
  { value: 'fulfilled',            label: 'Fulfilled' },
  { value: 'rejected',             label: 'Rejected' },
]

// ─── Empty new-requisition form ─────────────────────────────────────────────────

interface FormState {
  req_type: RequisitionType
  category: string
  description: string
  estimated_value: string
  reason: string
  spare_asset: string
}
const EMPTY_FORM: FormState = {
  req_type: 'new_purchase',
  category: '',
  description: '',
  estimated_value: '',
  reason: '',
  spare_asset: '',
}

export default function AssetRequisitionsPage() {
  const router = useRouter()
  const { theme } = useTheme()

  const [rows, setRows] = useState<AssetRequisition[]>([])
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState<StatusFilter>('all')

  const [modalOpen, setModalOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)

  // Recipient (directory search — never typed).
  const [recipient, setRecipient] = useState<AssetEmployeeOption | null>(null)
  const [empQ, setEmpQ] = useState('')
  const [empOpts, setEmpOpts] = useState<AssetEmployeeOption[]>([])
  const [empOpen, setEmpOpen] = useState(false)

  // Lookups for the form.
  const [categories, setCategories] = useState<AssetCategory[]>([])
  const [spares, setSpares] = useState<AssetListItem[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getAssetRequisitions(status === 'all' ? undefined : { status })
      setRows(res.results)
    } catch (e) {
      pushToast({ type: 'error', message: 'Could not load requisitions', description: (e as Error).message })
    } finally {
      setLoading(false)
    }
  }, [status])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  // Debounced staff search — same lightweight list the asset register uses.
  useEffect(() => {
    if (!empOpen) return
    let live = true
    const t = setTimeout(() => {
      getAssetEmployees(empQ || undefined)
        .then(r => { if (live) setEmpOpts(r.slice(0, 8)) })
        .catch(() => { if (live) setEmpOpts([]) })
    }, 200)
    return () => { live = false; clearTimeout(t) }
  }, [empQ, empOpen])

  function openModal() {
    setForm(EMPTY_FORM)
    setRecipient(null)
    setEmpQ('')
    // Load the pickers lazily on first open.
    getAssetCategories().then(r => setCategories(r.results)).catch(() => setCategories([]))
    getSparePool().then(setSpares).catch(() => setSpares([]))
    setModalOpen(true)
  }

  function pickEmployee(o: AssetEmployeeOption) {
    setRecipient(o)
    setEmpOpen(false)
    setEmpQ('')
  }

  const isReissue = form.req_type === 'reissue'
  const canSubmit =
    !!recipient &&
    !!form.category &&
    form.description.trim().length > 0 &&
    form.estimated_value.trim().length > 0 &&
    form.reason.trim().length > 0 &&
    (!isReissue || !!form.spare_asset)

  async function onSubmit() {
    if (!recipient || !canSubmit) return
    setSubmitting(true)
    try {
      const created = await createAssetRequisition({
        req_type: form.req_type,
        category: form.category,
        description: form.description.trim(),
        estimated_value: Number(form.estimated_value),
        reason: form.reason.trim(),
        recipient: recipient.id,
        spare_asset: isReissue ? form.spare_asset : null,
      })
      pushToast({ type: 'success', message: `Requisition ${created.requisition_number} raised` })
      setModalOpen(false)
      setForm(EMPTY_FORM)
      setRecipient(null)
      load()
    } catch (e) {
      pushToast({ type: 'error', message: 'Could not raise requisition', description: (e as Error).message })
    } finally {
      setSubmitting(false)
    }
  }

  // Shared inline styles from the theme (matches hris/documents density).
  const fieldStyle = { background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Header */}
      <header
        className="flex items-start justify-between gap-4 px-6 py-5 border-b"
        style={{ borderColor: theme.cardBdr, background: theme.card }}
      >
        <div className="flex items-start gap-3">
          <div
            className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
            style={{ background: theme.oL }}
          >
            <ClipboardList className="h-5 w-5" style={{ color: theme.orange }} />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight" style={{ color: theme.navy }}>
              Asset Requisitions
            </h1>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed" style={{ color: theme.t2 }}>
              Recipients are picked from the staff directory — never typed. Material items
              (≥ the value threshold) need both the Finance Manager and the CFO to approve.
            </p>
          </div>
        </div>
        <Button variant="accent" size="md" leftIcon={<Plus className="h-4 w-4" />} onClick={openModal}>
          New Requisition
        </Button>
      </header>

      <main className="flex-1 overflow-y-auto p-6 space-y-4">
        {/* Status filter — segmented */}
        <div className="flex flex-wrap items-center gap-1.5">
          {FILTERS.map(f => {
            const active = status === f.value
            return (
              <button
                key={f.value}
                type="button"
                onClick={() => setStatus(f.value)}
                className="rounded-full px-3 py-1.5 text-xs font-semibold transition-colors"
                style={{
                  background: active ? theme.orange : theme.g100,
                  color: active ? '#fff' : theme.t2,
                }}
              >
                {f.label}
              </button>
            )
          })}
        </div>

        {/* Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={8} cols={7} />
            ) : (
              <TableWrapper>
                <Table>
                  <TableHead>
                    <TableRow>
                      <TableHeader>Requisition #</TableHeader>
                      <TableHeader>Type</TableHeader>
                      <TableHeader>Recipient</TableHeader>
                      <TableHeader>Category</TableHeader>
                      <TableHeader className="text-right">Est. value</TableHeader>
                      <TableHeader>Status</TableHeader>
                      <TableHeader>Raised</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {rows.length === 0 ? (
                      <EmptyTableRow
                        colSpan={7}
                        message="No requisitions yet."
                        icon={<ClipboardList className="h-9 w-9" strokeWidth={1.5} />}
                      />
                    ) : (
                      rows.map(r => (
                        <TableRow
                          key={r.id}
                          clickable
                          onClick={() => router.push(`/assets/requisitions/${r.id}`)}
                        >
                          <TableCell className="font-mono font-semibold" style={{ color: theme.navy }}>
                            <Link
                              href={`/assets/requisitions/${r.id}`}
                              className="hover:underline"
                              onClick={e => e.stopPropagation()}
                            >
                              {r.requisition_number}
                            </Link>
                          </TableCell>
                          <TableCell>{r.req_type_display}</TableCell>
                          <TableCell>
                            <div className="font-medium" style={{ color: theme.text }}>{r.recipient_name}</div>
                            {r.recipient_email && (
                              <div className="text-xs" style={{ color: theme.t3 }}>{r.recipient_email}</div>
                            )}
                          </TableCell>
                          <TableCell style={{ color: theme.t2 }}>{r.category_name}</TableCell>
                          <TableCell className="text-right tabular-nums">{fmtBwp(r.estimated_value)}</TableCell>
                          <TableCell>
                            <Badge variant={STATUS_VARIANT[r.status] || 'default'}>{r.status_display}</Badge>
                          </TableCell>
                          <TableCell style={{ color: theme.t2 }}>{fmtDate(r.created_at)}</TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </TableWrapper>
            )}
          </CardContent>
        </Card>
      </main>

      {/* New Requisition modal */}
      <Modal
        open={modalOpen}
        onOpenChange={setModalOpen}
        title="New Requisition"
        description="Raise a request for a named member of staff."
        size="lg"
      >
        <ModalBody className="space-y-4">
          {/* Type toggle */}
          <div>
            <Label>Type</Label>
            <div className="inline-flex overflow-hidden rounded-lg" style={{ border: `1px solid ${theme.cardBdr}` }}>
              {([
                { v: 'new_purchase', l: 'New purchase' },
                { v: 'reissue', l: 'Reissue of a spare' },
              ] as { v: RequisitionType; l: string }[]).map(opt => {
                const active = form.req_type === opt.v
                return (
                  <button
                    key={opt.v}
                    type="button"
                    onClick={() => setForm(f => ({ ...f, req_type: opt.v }))}
                    className="px-4 py-2 text-xs font-semibold transition-colors"
                    style={{ background: active ? theme.orange : 'transparent', color: active ? '#fff' : theme.t2 }}
                  >
                    {opt.l}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Recipient — directory search */}
          <div>
            <Label required>Recipient</Label>
            {recipient ? (
              <div
                className="flex items-center justify-between rounded-md px-3 py-2.5 text-sm"
                style={fieldStyle}
              >
                <span style={{ color: theme.text }}>
                  Selected: <span className="font-medium">{recipient.full_name}</span>{' '}
                  <span style={{ color: theme.t3 }}>({recipient.email})</span>
                </span>
                <button type="button" onClick={() => setRecipient(null)} aria-label="Clear recipient">
                  <X className="h-4 w-4" style={{ color: theme.t2 }} />
                </button>
              </div>
            ) : (
              <div className="relative">
                <div className="flex items-center gap-2 rounded-md px-3 py-2.5 text-sm" style={fieldStyle}>
                  <Search className="h-4 w-4 flex-shrink-0" style={{ color: theme.t3 }} />
                  <input
                    placeholder="Search staff by name…"
                    value={empQ}
                    onFocus={() => setEmpOpen(true)}
                    onChange={e => { setEmpQ(e.target.value); setEmpOpen(true) }}
                    className="w-full bg-transparent outline-none"
                    style={{ color: theme.text }}
                  />
                </div>
                {empOpen && empOpts.length > 0 && (
                  <div
                    className="absolute z-50 mt-1 w-full overflow-hidden rounded-lg shadow-lg"
                    style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
                  >
                    {empOpts.map(o => (
                      <button
                        key={o.id}
                        type="button"
                        onClick={() => pickEmployee(o)}
                        className="block w-full px-3 py-2 text-left text-sm hover:opacity-80"
                        style={{ color: theme.text }}
                      >
                        <span className="font-medium">{o.full_name}</span>
                        <span style={{ color: theme.t3 }}> · {o.email}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {/* Category */}
            <div>
              <Label required>Category</Label>
              <Select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
                <option value="">Select category…</option>
                {categories.map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </Select>
            </div>

            {/* Estimated value */}
            <div>
              <Label required>Estimated value (P)</Label>
              <Input
                type="number"
                min="0"
                step="0.01"
                placeholder="0.00"
                value={form.estimated_value}
                onChange={e => setForm(f => ({ ...f, estimated_value: e.target.value }))}
              />
            </div>
          </div>

          {/* Description */}
          <div>
            <Label required>Description</Label>
            <Input
              placeholder="Make / model / spec"
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            />
          </div>

          {/* Spare asset — reissue only */}
          {isReissue && (
            <div>
              <Label required>Spare asset</Label>
              <SearchableSelect
                options={spares.map(a => ({ value: a.id, label: `${a.tag_number} — ${a.name}`, hint: a.category_name }))}
                value={form.spare_asset}
                onChange={v => setForm(f => ({ ...f, spare_asset: v }))}
                placeholder="Pick a spare from the pool…"
              />
            </div>
          )}

          {/* Reason */}
          <div>
            <Label required>Reason</Label>
            <Textarea
              placeholder="Why is this needed?"
              value={form.reason}
              onChange={e => setForm(f => ({ ...f, reason: e.target.value }))}
            />
          </div>
        </ModalBody>

        <ModalFooter>
          <Button variant="secondary" size="sm" onClick={() => setModalOpen(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="accent" size="sm" loading={submitting} disabled={!canSubmit} onClick={onSubmit}>
            Raise requisition
          </Button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
