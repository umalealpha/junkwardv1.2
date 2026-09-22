'use client'

/**
 * /payments/upload — RETIRED, 2026-09-13. This page now sends people to the one
 * canonical uploader on /payment-requests.
 *
 * Manus QC-UNICOIN-BULK-2026-09-12 flagged this as "a second, stale workflow…
 * no matching backend implementation exists". The second half was wrong — its
 * endpoints (`/payments/bulk-upload/`, `/payments/upload-template/`) resolve on
 * production today. That makes the finding MORE serious, not less: this screen
 * wrote payment rows through `payments/bulk_upload.create_payments`, which
 * inserts drafts directly and therefore walks past the duplicate gate, the
 * bank-change gate, first-payment-to-a-new-payee and the hard bank cross-check —
 * every control the uploader on /payment-requests exists to route each row
 * through, one ordinary gated call at a time.
 *
 * Two doors into payments, one of them unguarded, is the whole problem. The
 * backend route is deliberately left in place (other callers and its tests are
 * out of scope here); what is removed is the way a person reaches it by
 * clicking, and the second, different CSV contract it documented.
 */

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

export default function RetiredBulkPaymentUploadPage() {
  const router = useRouter()

  useEffect(() => {
    router.replace('/payment-requests')
  }, [router])

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="max-w-md text-center">
        <h1 className="text-base font-semibold text-[#0D1B2A]">
          This page has moved
        </h1>
        <p className="mt-2 text-sm text-[#6B7280]">
          Uploading a list of payments now happens on the Payment Requests page,
          where every line goes through the same checks as a payment you type in
          by hand. Taking you there now.
        </p>
      </div>
    </div>
  )
}
