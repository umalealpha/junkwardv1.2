'use client'

/**
 * ModalPortal — render an overlay at <body> level so it is not trapped inside
 * the dashboard's main content stacking context.
 *
 * Bug 5f2fca79 (Legakwa Ntabeni, 2026-08-04): the New payment request form was
 * cut off down its left edge, hiding the supplier and invoice-number labels.
 * Cause: app/(dashboard)/layout.tsx renders <main className="… relative z-[1]">,
 * which makes main a stacking context. A modal written inline in a page lives
 * inside main, so its z-50 is compared only against its siblings *within* main —
 * while the Sidebar is main's sibling at z-40. 40 beats the whole z-[1] subtree,
 * so the sidebar paints over the modal.
 *
 * main cannot simply drop z-[1]: FunBackground and .aurora-surface::before are
 * fixed at z-index 0, so main needs a positive z-index to stay above them.
 * The fix is therefore to lift the overlay out of main entirely — which is what
 * the shared Radix modal (components/ui/modal.tsx, z-[200]) already does, and
 * why modals built on it were never affected.
 *
 * Use Z_MODAL for the overlay's z-index so page-level modals sit on the same
 * layer as the shared one.
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

/** Same layer as the shared Radix modal overlay. Above the sidebar (z-40). */
export const Z_MODAL = 200

export function ModalPortal({ children }: { children: React.ReactNode }) {
  // Portals need a DOM node, which does not exist during the server render.
  const [ready, setReady] = useState(false)
  useEffect(() => { setReady(true) }, [])
  if (!ready || typeof document === 'undefined') return null
  return createPortal(children, document.body)
}
