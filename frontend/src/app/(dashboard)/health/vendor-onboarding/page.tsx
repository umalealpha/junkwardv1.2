'use client'

/**
 * /health/vendor-onboarding — staff (Ankete-assisted) vendor onboarding.
 *
 * Two ways to onboard a provider from here:
 *   1. Fill the form on the provider's behalf (the wizard below, with OCR capture).
 *   2. "Send the provider a link" — mint a private, single-use, expiring link the
 *      provider opens themselves at /onboard/<token> (no login). CFO 2026-07-28.
 *
 * The wizard itself is the shared <VendorOnboardingWizard>, identical to the
 * public self-service page so the two can never drift apart.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  submitVendorOnboarding, vendorOnboardingAccess, createVendorInvite, getToken,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { VendorOnboardingWizard } from '@/components/vendor/VendorOnboardingWizard'
import { AlertCircle, Link2, Copy, Check, Send } from 'lucide-react'

export default function VendorOnboardingPage() {
  const router = useRouter()
  const [ready, setReady] = useState(false)
  const [canSubmit, setCanSubmit] = useState<boolean | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    setReady(true)
    vendorOnboardingAccess().then((r) => setCanSubmit(!!r.authorised)).catch(() => setCanSubmit(null))
  }, [router])

  if (!ready) return null

  const accessBanner = canSubmit === false ? (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-orange-200 bg-orange-50 p-3 text-sm text-orange-800">
      <AlertCircle size={16} className="mt-0.5 shrink-0" />
      <span>Heads up — only the vendor-onboarding team can submit a final record. You can still
      fill this in, but the last step will need an authorised user to submit. Contact the CFO if
      you need access.</span>
    </div>
  ) : null

  return (
    <VendorOnboardingWizard
      submit={submitVendorOnboarding}
      enableDocCapture
      header={<><TopBar />{canSubmit ? <InvitePanel /> : null}</>}
      accessBanner={accessBanner}
    />
  )
}

/** Mint a private self-onboarding link for a provider and copy/send it. */
function InvitePanel() {
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [sendEmail, setSendEmail] = useState(false)
  const [busy, setBusy] = useState(false)
  const [link, setLink] = useState('')
  const [emailed, setEmailed] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  async function generate() {
    setBusy(true); setErr(null); setLink(''); setEmailed(false)
    try {
      const r = await createVendorInvite({
        invited_email: email.trim(), invited_name: name.trim(), send_email: sendEmail && !!email.trim(),
      })
      setLink(r.link); setEmailed(r.email_sent)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not create the link.')
    } finally { setBusy(false) }
  }

  async function copy() {
    try { await navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* ignore */ }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 pt-4">
      <Card className="border-[#F4A623]/40">
        <CardHeader className="cursor-pointer" onClick={() => setOpen((v) => !v)}>
          <CardTitle className="flex items-center gap-2 text-base">
            <Link2 className="h-4 w-4 text-[#F4A623]" /> Send the provider a link to fill it in themselves
          </CardTitle>
        </CardHeader>
        {open && (
          <CardContent>
            <p className="mb-3 text-sm text-slate-500">
              Generate a private, single-use link (expires in 14 days). The provider opens it with no
              login and completes the whole form themselves. Or fill the form yourself below.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <input className="h-10 rounded-lg border border-slate-300 px-3 text-sm text-slate-900"
                     placeholder="Provider email (optional)" value={email} onChange={(e) => setEmail(e.target.value)} />
              <input className="h-10 rounded-lg border border-slate-300 px-3 text-sm text-slate-900"
                     placeholder="Provider / practice name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <label className="mt-3 flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" className="h-4 w-4 accent-orange-500" checked={sendEmail}
                     onChange={(e) => setSendEmail(e.target.checked)} disabled={!email.trim()} />
              Email the link to the provider automatically
            </label>
            <div className="mt-3">
              <Button onClick={generate} disabled={busy}>
                <Send size={15} className="mr-2" />{busy ? 'Creating…' : 'Create onboarding link'}
              </Button>
            </div>
            {err && <p className="mt-3 flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-700"><AlertCircle size={16} />{err}</p>}
            {link && (
              <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Onboarding link (copy &amp; send)</div>
                <div className="mt-1 flex items-center gap-2">
                  <input readOnly value={link} className="min-w-0 flex-1 rounded border border-slate-300 bg-white px-2 py-1.5 font-mono text-xs text-slate-800" />
                  <Button variant="outline" onClick={copy}>
                    {copied ? <><Check size={14} className="mr-1 text-green-600" />Copied</> : <><Copy size={14} className="mr-1" />Copy</>}
                  </Button>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  {emailed ? 'Also emailed to the provider. ' : ''}Single use · expires in 14 days.
                </p>
              </div>
            )}
          </CardContent>
        )}
      </Card>
    </div>
  )
}
