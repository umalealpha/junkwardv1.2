'use client'
import { List } from '../../TileList'
const ROWS = [
  ['/app/payslips', 'My payslips', 'Only you can see these'],
  ['/app/leave', 'Leave', 'Apply and track'],
  ['/app/staff-loan', 'Staff loan', 'Apply and track'],
  ['/app/checkins', 'My check-ins', 'Monthly performance check-in'],
  ['/app/reviews', 'My reviews', 'Development Dialogues'],
  ['/app/rewards', 'Staff Rewards', 'Points and pillars'],
  ['/app/pulse', 'Pulse', 'One-tap weekly check-in'],
  ['/app/leave-encashment', 'Leave pay', 'Cash out annual leave — approvers sign in Omni'],
  ['/app/devices', 'Signed-in devices', 'Switch a phone off'],
]
export default function Me() { return <List title="Me" rows={ROWS} /> }
