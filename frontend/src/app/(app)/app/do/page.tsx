'use client'
import { List } from '../../TileList'
const ROWS = [
  ['/app/lookup', 'Look up a client', 'Claim or policy status from Graphite — by number or name'],
  ['/app/snap', 'Snap anything', 'Photo an invoice, receipt or quote — Omni sorts it'],
  ['/app/docs', 'Quotes & certificates', 'Describe it in a sentence — Omni fills the cover note, WCA certificate or quote and emails the PDF'],
  ['/app/tasks', 'My tasks', 'Complete, or flag blocked with a photo'],
  ['/app/explain-day', 'My hours', 'Explain a short day or plan a day off'],
  ['/app/rooms', 'Rooms', "Book a meeting room, see today's board"],
  ['/app/receipt', 'Snap a receipt', 'Claim money back'],
  ['/app/petty-cash', 'Petty cash', 'Ask before you spend'],
  ['/app/card-spend', 'Company card', 'Cardholders only'],
  ['/app/raise-po', 'Raise a purchase order', 'Vendor, lines, approver — same rules as desktop'],
  ['/app/raise-payment', 'Raise a payment request', 'Photograph the invoice — Omni reads it'],
  ['/app/vehicle-trip', 'Log a vehicle trip', 'Odometer + photo'],
  ['/app/letter', 'Request a letter', 'HR letters'],
]
export default function Do() { return <List title="Do" rows={ROWS} /> }
