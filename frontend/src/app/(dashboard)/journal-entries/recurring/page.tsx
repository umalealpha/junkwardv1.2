import { redirect } from 'next/navigation'

/**
 * Bug (Oprah Mogomotsi, 2026-06-22): navigating directly to
 * /journal-entries/recurring hit the dynamic /journal-entries/[id] route,
 * which interpreted "recurring" as a journal-entry UUID, tried to load a JE
 * with that id, and rendered "Not found" (plus "Request failed - Not found."
 * toasts). The Recurring Journal Entries feature actually lives at
 * /settings/recurring-journal-entries.
 *
 * This STATIC `recurring` segment takes precedence over the sibling dynamic
 * `[id]` segment in the Next.js App Router, so it intercepts the intuitive URL
 * (typed / bookmarked) and redirects to the real page instead of erroring.
 */
export default function RecurringJournalEntriesRedirect() {
  redirect('/settings/recurring-journal-entries')
}
