'use client'
import { List } from '../../TileList'
const ROWS = [
  ['/app/approvals', 'Approvals inbox', 'Leave, loans, incentives, POs, petty cash'],
  ['/app/payments', 'Payments to sign', 'Supplier, claim and operational packs'],
  ['/app/my-requests', 'My requests', 'Where each request is — leave, loans, petty cash, payments'],
  ['/app/review-explanations', 'Review explanations', "Your team's short-day reasons to approve (managers)"],
  ['/app/roster-flag', 'Flag my roster', "Someone on your list who shouldn't be? Tell HR (managers)"],
]
export default function Approve() { return <List title="Approve" rows={ROWS} /> }
