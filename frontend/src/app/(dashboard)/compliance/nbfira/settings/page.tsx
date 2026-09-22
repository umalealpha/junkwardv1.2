'use client'

/**
 * /compliance/nbfira/settings — Compliance module settings.
 *
 * Phase 1: stores values in localStorage so the auditor can review the
 * schema (reminder days, board attestation defaults, NBFIRA contact, etc.)
 * and mark up changes. Phase 2: persist to /api/v1/compliance/settings/.
 */

import { useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Save, Settings as SettingsIcon, ShieldCheck } from 'lucide-react'

const LS_KEY = 'omni:compliance:nbfira:settings'

interface ComplianceSettings {
  reminder_days_quarterly: number     // remind N days before due
  reminder_days_annual:    number
  responsible_officer:     string
  board_attestation_role:  string
  nbfira_contact_name:     string
  nbfira_contact_email:    string
  nbfira_portal_url:       string
  notes:                   string
}

const DEFAULTS: ComplianceSettings = {
  reminder_days_quarterly: 14,
  reminder_days_annual:    30,
  responsible_officer:     'Chief Financial Officer',
  board_attestation_role:  'Board Chairperson',
  nbfira_contact_name:     '',
  nbfira_contact_email:    'returns@nbfira.org.bw',
  nbfira_portal_url:       'https://efile.nbfira.org.bw/',
  notes:                   '',
}

export default function ComplianceSettingsPage() {
  const [s, setS] = useState<ComplianceSettings>(DEFAULTS)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    try {
      const raw = localStorage.getItem(LS_KEY)
      if (raw) setS({ ...DEFAULTS, ...JSON.parse(raw) })
    } catch { /* ignore */ }
  }, [])

  const save = () => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(s)) } catch { /* ignore */ }
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const field = <K extends keyof ComplianceSettings>(
    key: K, label: string, type: 'text' | 'number' | 'email' | 'url' | 'textarea', hint?: string,
  ) => (
    <div>
      <label className="block text-xs font-medium text-[#374151] mb-1">{label}</label>
      {type === 'textarea' ? (
        <textarea
          rows={3}
          value={String(s[key] ?? '')}
          onChange={(e) => setS({ ...s, [key]: e.target.value } as ComplianceSettings)}
          className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
        />
      ) : (
        <input
          type={type}
          value={String(s[key] ?? '')}
          onChange={(e) => setS({
            ...s,
            [key]: type === 'number' ? Number(e.target.value) : e.target.value,
          } as ComplianceSettings)}
          className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
        />
      )}
      {hint && <div className="text-xs text-[#9CA3AF] mt-1">{hint}</div>}
    </div>
  )

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Compliance Settings"
        subtitle="Reminders, board attestation, NBFIRA contact details"
        breadcrumbs={[{ label: 'Compliance' }, { label: 'NBFIRA' }, { label: 'Settings' }]}
        actions={
          <Button onClick={save} leftIcon={<Save className="w-3.5 h-3.5" />}>
            {saved ? 'Saved' : 'Save'}
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4 max-w-3xl">
        <Card>
          <CardContent className="p-5 space-y-4">
            <div className="text-sm font-semibold text-[#0D1B2A]">Reminders</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {field('reminder_days_quarterly', 'Quarterly reminder (days before due)', 'number',
                'Notification fires N days before each quarterly return is due')}
              {field('reminder_days_annual', 'Annual reminder (days before due)', 'number',
                'Notification fires N days before the annual return is due')}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-5 space-y-4">
            <div className="text-sm font-semibold text-[#0D1B2A]">Sign-off defaults</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {field('responsible_officer', 'Responsible Officer', 'text',
                'Title printed on the prepared-by line of every submission')}
              {field('board_attestation_role', 'Board Attestation', 'text',
                'Role of the director attesting on the annual return')}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-5 space-y-4">
            <div className="text-sm font-semibold text-[#0D1B2A]">NBFIRA contact</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {field('nbfira_contact_name',  'Contact name',  'text')}
              {field('nbfira_contact_email', 'Contact email', 'email')}
            </div>
            {field('nbfira_portal_url', 'Portal URL', 'url',
              'Where Submitted returns are filed (efile.nbfira.org.bw)')}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-5 space-y-2">
            <div className="text-sm font-semibold text-[#0D1B2A]">Notes</div>
            {field('notes', 'Free-text notes', 'textarea',
              'Anything specific the auditor needs to remember when reviewing')}
          </CardContent>
        </Card>

        <div className="text-xs text-[#9CA3AF] flex items-start gap-1">
          <ShieldCheck className="w-3.5 h-3.5 mt-0.5" />
          Phase 1 — values stored in this browser only (localStorage). Phase 2 ships a
          /api/v1/compliance/settings/ endpoint that persists across users and survives a
          deploy. Internal auditor: confirm the schema is the right one before we build the backend.
        </div>
      </div>
    </div>
  )
}
