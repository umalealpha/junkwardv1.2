'use client'

/**
 * /onboard/[token] — PUBLIC (no login) self-service vendor onboarding.
 *
 * A service provider opens the private, single-use link the onboarding team
 * generated and fills in the whole form themselves. Validates the token first,
 * then renders the shared <VendorOnboardingWizard> (OCR capture off — the
 * extract endpoint is staff-only), submitting through the token-gated endpoint.
 */
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { getVendorInvite, submitVendorInvite, type VendorSubmitResult } from '@/lib/api'
import { VendorOnboardingWizard } from '@/components/vendor/VendorOnboardingWizard'
import { ShieldCheck, AlertTriangle } from 'lucide-react'

const Header = (
  <div className="bg-[#0D1B2A] px-5 py-3 text-white">
    <div className="mx-auto flex max-w-3xl items-center gap-2">
      <ShieldCheck className="h-5 w-5 text-[#F4A623]" />
      <span className="font-semibold">Alpha Direct Healthcare — Service Provider Onboarding</span>
    </div>
  </div>
)

export default function PublicOnboardPage() {
  const params = useParams<{ token: string }>()
  const token = (params?.token as string) || ''
  const [state, setState] = useState<'loading' | 'ok' | 'bad'>('loading')
  const [msg, setMsg] = useState('')
  const [email, setEmail] = useState('')

  useEffect(() => {
    if (!token) { setState('bad'); setMsg('This link is missing its code.'); return }
    getVendorInvite(token)
      .then((r) => { setEmail(r.invited_email || ''); setState('ok') })
      .catch((e) => { setState('bad'); setMsg(e instanceof Error ? e.message : 'This link is not valid.') })
  }, [token])

  if (state === 'loading') {
    return (
      <div className="min-h-screen bg-slate-50">
        {Header}
        <div className="mx-auto max-w-3xl px-4 py-20 text-center text-slate-500">Checking your link…</div>
      </div>
    )
  }

  if (state === 'bad') {
    return (
      <div className="min-h-screen bg-slate-50">
        {Header}
        <div className="mx-auto max-w-xl px-4 py-20 text-center">
          <AlertTriangle className="mx-auto mb-3 h-10 w-10 text-orange-400" />
          <h1 className="text-xl font-bold text-slate-900">This onboarding link can’t be opened</h1>
          <p className="mt-2 text-slate-600">{msg}</p>
          <p className="mt-4 text-sm text-slate-500">
            Please contact the Alpha Direct Healthcare team for a fresh link.
          </p>
        </div>
      </div>
    )
  }

  return (
    <VendorOnboardingWizard
      submit={(payload: Record<string, unknown>): Promise<VendorSubmitResult> => submitVendorInvite(token, payload)}
      enableDocCapture={false}
      header={Header}
      initialContactEmail={email}
    />
  )
}
