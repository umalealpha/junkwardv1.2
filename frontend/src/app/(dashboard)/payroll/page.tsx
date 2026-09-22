import { redirect } from 'next/navigation'

// /payroll had no index page — direct hits returned the Next 404 while every
// sibling module root resolves. Send visitors to the payroll dashboard.
export default function PayrollIndex() {
  redirect('/payroll/dashboard')
}
