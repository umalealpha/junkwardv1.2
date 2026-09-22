import { redirect } from 'next/navigation'

// The "Debtors" sidebar group has no top-level dashboard yet; deep-linking to
// /debtors returned 404. Redirect to Customer Invoices, the most-used child,
// so bookmarks and the sidebar parent label both land somewhere useful.
export default function DebtorsLandingRedirect() {
  redirect('/invoices')
}
