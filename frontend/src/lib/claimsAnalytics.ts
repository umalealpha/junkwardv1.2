/**
 * claimsAnalytics.ts — API client for the Claims-PO analytics dashboard.
 *
 * Kept out of lib/api.ts on purpose (parallel work is editing that file);
 * only imports the shared apiFetch wrapper, which handles auth + the
 * company switcher query param automatically.
 */

import { apiFetch } from '@/lib/api'

export interface ClaimsPOMonthly {
  month: string   // 'YYYY-MM'
  label: string   // 'Jul 25'
  spend: number
  count: number
}

export interface ClaimsPOSupplier {
  supplier: string
  total: number
  count: number
}

export interface ClaimsPOSplitBucket {
  count: number
  value: number
}

export interface ClaimsPOAnalytics {
  company: string | null
  as_of: string
  totals: {
    po_count: number
    total_spend: number
    spend_this_month: number
    excess_total: number
    excess_avg: number
    excess_po_count: number
    avg_turnaround_days: number | null
    turnaround_sample: number
    sent: number
    unsent: number
    sent_known: boolean
  }
  monthly: ClaimsPOMonthly[]
  top_suppliers: ClaimsPOSupplier[]
  split: {
    repairer: ClaimsPOSplitBucket
    parts: ClaimsPOSplitBucket
  }
  notes: string[]
}

export function getClaimsPOAnalytics(): Promise<ClaimsPOAnalytics> {
  return apiFetch<ClaimsPOAnalytics>('/reports/claims-po-analytics/')
}
