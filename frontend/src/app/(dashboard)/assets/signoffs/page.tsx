'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getAssetSignOffs, signAssetFirst, signAssetSecond, getMe, getToken,
} from '@/lib/api'
import type { AssetSignOff, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  AlertCircle, CheckCircle, ArrowLeft, ShieldAlert, ShieldCheck,
} from 'lucide-react'

const STATUS_STYLES: Record<string, string> = {
  pending:           'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
  partially_signed:  'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
  completed:         'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
  overdue:           'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
  cancelled:         'bg-[#F3F4F6] text-[#9CA3AF] border-[#E5E7EB]',
}

export default function AssetSignOffsPage() {
  const router = useRouter()
  const [items, setItems] = useState<AssetSignOff[]>([])
  const [me, setMe] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getAssetSignOffs({ open_only: true })
      setItems(res.results)
    } catch (e) { setError(e instanceof Error ? e.message : 'Failed to load') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getMe().then(setMe).catch(() => setMe(null))
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function flash(msg: string) { setSuccess(msg); setTimeout(() => setSuccess(null), 4000) }

  async function signFirst(s: AssetSignOff) {
    setBusy(true); setError(null)
    try { await signAssetFirst(s.id); flash('First signature recorded.'); load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Sign failed') }
    finally { setBusy(false) }
  }
  async function signSecond(s: AssetSignOff) {
    setBusy(true); setError(null)
    try { await signAssetSecond(s.id); flash('Second signature recorded.'); load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Sign failed') }
    finally { setBusy(false) }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Asset Sign-Offs"
        breadcrumbs={[{ label: 'Fixed Assets', href: '/assets' }, { label: 'Sign-Offs' }]}
        actions={
          <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/dashboard')}>
            Dashboard
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}
        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-[#047857]" />
            <p className="text-[#047857] text-sm">{success}</p>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-[#F07F00]" />
              Open sign-offs ({items.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {loading ? (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            ) : items.length === 0 ? (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <ShieldCheck className="w-10 h-10 mx-auto mb-3 text-[#A7F3D0]" strokeWidth={1.5} />
                <p className="text-sm">No open sign-offs. Internal controls are clean.</p>
              </div>
            ) : (
              <ul className="divide-y divide-[#E5E7EB]">
                {items.map(s => {
                  const canSignFirst  = !s.first_signed_by_username && !!me?.is_active
                  const meIsApprover  = !!me?.can_approve_journal_entries
                  const meIsFirst     = me?.username === s.first_signed_by_username
                  const canSignSecond = !s.second_signed_by_username && meIsApprover && !meIsFirst
                  return (
                    <li key={s.id} className="p-4">
                      <div className="flex items-center gap-3 flex-wrap">
                        <div className="flex-1 min-w-0">
                          <p className="font-medium text-[#111827]">
                            {s.kind_display}{' '}
                            <span className="text-[#6B7280] font-normal">· {s.period_label}</span>
                          </p>
                          <p className="text-xs text-[#6B7280] mt-0.5">
                            Due {s.due_date}
                            {s.asset_tag && <> · {s.asset_tag} {s.asset_name}</>}
                          </p>
                        </div>
                        <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium border ${STATUS_STYLES[s.status] || STATUS_STYLES.pending}`}>
                          {s.status_display}
                        </span>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 text-sm">
                        <div className="bg-[#F9FAFB] border border-[#E5E7EB] rounded-md p-3">
                          <p className="text-xs uppercase tracking-wider text-[#6B7280]">{s.first_role_label}</p>
                          {s.first_signed_by_username ? (
                            <p className="mt-1 text-[#047857] font-medium">✓ {s.first_signed_by_username}</p>
                          ) : (
                            <Button size="sm" variant="outline" className="mt-1" onClick={() => signFirst(s)} disabled={!canSignFirst || busy}>
                              Sign as {s.first_role_label}
                            </Button>
                          )}
                        </div>

                        <div className="bg-[#F9FAFB] border border-[#E5E7EB] rounded-md p-3">
                          <p className="text-xs uppercase tracking-wider text-[#6B7280]">{s.second_role_label}</p>
                          {s.second_signed_by_username ? (
                            <p className="mt-1 text-[#047857] font-medium">✓ {s.second_signed_by_username}</p>
                          ) : (
                            <Button size="sm" variant="accent" className="mt-1"
                                    onClick={() => signSecond(s)}
                                    disabled={!canSignSecond || busy}
                                    title={
                                      !meIsApprover ? 'Approver title required (CFO / Finance Manager / Financial Controller)'
                                      : meIsFirst ? 'You signed the first slot — segregation of duties prevents the same person doing both'
                                      : undefined
                                    }>
                              Sign as {s.second_role_label}
                            </Button>
                          )}
                        </div>
                      </div>

                      {s.discrepancies_text && (
                        <p className="mt-2 text-xs text-[#92400E] bg-[#FFFBEB] border border-[#FDE68A] rounded p-2">
                          <strong>Discrepancies noted:</strong> {s.discrepancies_text}
                        </p>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4 text-xs text-[#6B7280]">
            <p><strong>How this works:</strong> the daily scanner creates a sign-off record for every active asset whose NBV has hit salvage but is still in use, plus two half-year count records (due 30-Jun and 31-Dec). Both signatures must come from <em>different</em> people — segregation of duties is enforced server-side.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
