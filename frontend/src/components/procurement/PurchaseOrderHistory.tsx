'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { getPurchaseOrderHistory, getContact, type AuditLogEntry } from '@/lib/api'

const SKIP_FIELDS = new Set([
  'id', 'created_at', 'updated_at', 'created_by', 'created_by_id',
  'po_number', 'submitted_by', 'submitted_by_id', 'fm_approved_by',
  'fm_approved_by_id', 'cfo_approved_by', 'cfo_approved_by_id',
  'fiscal_period', 'fiscal_period_id',
  // The Status field already tells this story ("pending_cfo_approval ->
  // approved") — the matching *_at timestamp on the same entry is the same
  // event restated, not a second change worth its own line.
  'submitted_at', 'fm_approved_at', 'cfo_approved_at', 'cancelled_at',
])

const FIELD_LABELS: Record<string, string> = {
  total_amount: 'Amount',
  total_bwp: 'Amount (BWP)',
  subtotal: 'Subtotal',
  tax_total: 'VAT',
  supplier: 'Supplier',
  status: 'Status',
  discount_percent: 'Discount %',
  discount_total: 'Discount amount',
  cancellation_reason: 'Cancellation reason',
  rejection_reason: 'Rejection reason',
  expected_delivery_date: 'Expected delivery',
  issue_date: 'Issue date',
  justification: 'Justification',
  department: 'Department',
  related_claim_reference: 'Claim reference',
}

const MONEY_FIELDS = new Set(['Amount', 'Amount (BWP)', 'Subtotal', 'VAT', 'Discount amount'])

// UUID v4-shaped string — the field this raw is stored under is a foreign key
// (e.g. `supplier`), so the audit trail holds the related record's id, not its
// name. Anything matching this needs a name looked up before it's shown.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function formatLabel(key: string): string {
  return FIELD_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function fmtMoney(v: unknown): string {
  const n = typeof v === 'string' ? parseFloat(v) : (v as number)
  if (v === null || v === undefined || Number.isNaN(n)) return '(none)'
  return `P ${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)}`
}

function formatValue(label: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '(none)'
  if (MONEY_FIELDS.has(label)) return fmtMoney(value)
  return String(value)
}

interface ChangedField { key: string; label: string; oldVal: string; newVal: string; rawOld: unknown; rawNew: unknown }

function getChangedFields(entry: AuditLogEntry): ChangedField[] {
  if (!entry.old_values || !entry.new_values) return []
  const changed: ChangedField[] = []
  for (const key of Object.keys(entry.new_values)) {
    if (SKIP_FIELDS.has(key)) continue
    const rawOld = entry.old_values[key]
    const rawNew = entry.new_values[key]
    if (String(rawOld ?? '') === String(rawNew ?? '')) continue
    const label = formatLabel(key)
    changed.push({ key, label, oldVal: formatValue(label, rawOld), newVal: formatValue(label, rawNew), rawOld, rawNew })
  }
  return changed
}

function formatTimestamp(ts: string): string {
  return new Date(ts).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export default function PurchaseOrderHistory({ poId }: { poId: string }) {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [names, setNames] = useState<Record<string, string>>({})
  const [hidden, setHidden] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!poId) return
    let cancelled = false
    setLoading(true)
    getPurchaseOrderHistory(poId).then(data => {
      if (cancelled) return
      setEntries(data)
      setLoading(false)

      // Supplier is stored as a contact id, not a name — resolve every id
      // that shows up in a "Supplier" change so the log reads as a name,
      // matching exactly what was asked for (never a raw id on screen).
      const ids = new Set<string>()
      for (const entry of data) {
        for (const cf of getChangedFields(entry)) {
          if (cf.label !== 'Supplier') continue
          if (typeof cf.rawOld === 'string' && UUID_RE.test(cf.rawOld)) ids.add(cf.rawOld)
          if (typeof cf.rawNew === 'string' && UUID_RE.test(cf.rawNew)) ids.add(cf.rawNew)
        }
      }
      ids.forEach(id => {
        getContact(id)
          .then(c => { if (!cancelled) setNames(prev => ({ ...prev, [id]: c.name })) })
          .catch(() => { /* contact may since be deleted — fall back to the raw id */ })
      })
    }).catch(() => {
      if (cancelled) return
      setHidden(true)
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [poId])

  if (hidden || loading) return null

  if (entries.length === 0) {
    return (
      <Card>
        <CardContent className="p-4">
          <div className="font-semibold text-sm mb-3">History</div>
          <p className="text-sm text-muted-foreground">No history yet.</p>
        </CardContent>
      </Card>
    )
  }

  const resolve = (label: string, raw: unknown, formatted: string): string => {
    if (label === 'Supplier' && typeof raw === 'string' && names[raw]) return names[raw]
    return formatted
  }

  return (
    <Card>
      <CardContent className="p-4">
        <div className="font-semibold text-sm mb-3">History</div>
        <div className="space-y-3">
          {entries.map(entry => {
            const changed = getChangedFields(entry)
            const actionLabel = entry.action_display
              || (entry.action.charAt(0).toUpperCase() + entry.action.slice(1))
            return (
              <div key={entry.id} className="text-sm">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-muted-foreground">{formatTimestamp(entry.created_at)}</span>
                  <span className="font-medium">{entry.user_username || 'System'}</span>
                  <span className="text-muted-foreground">{actionLabel}</span>
                </div>
                {entry.description && (
                  <div className="text-muted-foreground mt-0.5">{entry.description}</div>
                )}
                {changed.length > 0 && (
                  <div className="mt-1 space-y-0.5">
                    {changed.map(cf => (
                      <div key={cf.key} className="text-muted-foreground text-xs">
                        {cf.label}: {resolve(cf.label, cf.rawOld, cf.oldVal)} → {resolve(cf.label, cf.rawNew, cf.newVal)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
