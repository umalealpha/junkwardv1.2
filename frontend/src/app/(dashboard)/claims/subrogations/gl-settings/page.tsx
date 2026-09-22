'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getSubrogationGLConfig, setSubrogationGLConfig, getToken } from '@/lib/api'
import type { SubrogationGLConfig } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Save, CheckCircle2 } from 'lucide-react'

const inputCls = 'w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)]'

export default function SubrogationGLSettingsPage() {
  const router = useRouter()
  const [cfg, setCfg] = useState<SubrogationGLConfig | null>(null)
  const [selected, setSelected] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getSubrogationGLConfig()
      .then(c => { setCfg(c); setSelected(c.recovery_income_account || '') })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
  }, [router])

  async function save() {
    setBusy(true); setError(null); setSaved(false)
    try {
      const c = await setSubrogationGLConfig(selected || null)
      setCfg(c); setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Subrogation GL accounts"
        breadcrumbs={[{ label: 'Claims Recoveries' }, { label: 'Subrogations', href: '/claims/subrogations' }, { label: 'GL accounts' }]}
        actions={<Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/claims/subrogations')}>Back</Button>} />

      <div className="flex-1 p-6 max-w-2xl space-y-4">
        <p className="text-sm text-[#6B7280]">
          Assign the general-ledger account a subrogation recovery credits. Setting it here
          does not post anything on its own — it is the account the accounting entries will
          use once recovery posting is switched on. Until it is set, posting stays off.
        </p>

        {error && <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2"><AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p></div>}

        <Card>
          <CardHeader><CardTitle>Recovery income account</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            {cfg === null ? (
              <p className="text-sm text-[#6B7280]">Loading…</p>
            ) : (
              <>
                <label className="block">
                  <span className="block text-xs font-medium text-[#374151] mb-1">Account (revenue accounts only)</span>
                  <select value={selected} onChange={e => { setSelected(e.target.value); setSaved(false) }} className={inputCls}>
                    <option value="">— Not set (posting blocked) —</option>
                    {cfg.revenue_accounts.map(a => (
                      <option key={a.id} value={a.id}>{a.code} — {a.name}</option>
                    ))}
                  </select>
                </label>
                {cfg.recovery_income_account_code && (
                  <p className="text-xs text-[#6B7280]">
                    Currently: <span className="font-medium text-[#111827]">{cfg.recovery_income_account_code} — {cfg.recovery_income_account_name}</span>
                  </p>
                )}
                <div className="flex items-center gap-3">
                  <Button variant="accent" size="sm" leftIcon={<Save className="w-3.5 h-3.5" />} onClick={save} disabled={busy}>
                    {busy ? 'Saving…' : 'Save'}
                  </Button>
                  {saved && <span className="inline-flex items-center gap-1 text-sm text-[#047857]"><CheckCircle2 className="w-4 h-4" /> Saved</span>}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
