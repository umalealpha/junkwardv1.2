'use client'

import { Building2 } from 'lucide-react'
import { useCompany } from '@/contexts/CompanyContext'
import { useTheme } from '@/contexts/ThemeContext'

/**
 * Small inline banner shown at the top of every report page so users always
 * know whether they're seeing the consolidated view or a single subsidiary.
 * Uses the global Company context — switch via the picker in the top bar.
 */
export function ReportScopeBanner() {
  const { selected, companies, loaded } = useCompany()
  const { theme } = useTheme()

  // Don't show until companies have loaded, and skip when only one company
  // exists (no point showing a scope label).
  if (!loaded || companies.length <= 1) return null

  if (!selected) {
    return (
      <div
        className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-xs"
        style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}
      >
        <Building2 className="w-3.5 h-3.5" />
        <span>Scope: <span className="font-semibold">All companies</span> (consolidated)</span>
      </div>
    )
  }
  return (
    <div
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-xs"
      style={{ background: theme.oL, color: theme.orange, border: `1px solid ${theme.orange}40` }}
    >
      <Building2 className="w-3.5 h-3.5" />
      <span>Scope: <span className="font-mono font-semibold">{selected.code}</span> · {selected.name}</span>
    </div>
  )
}
