'use client'
import { List } from '../../TileList'
import { clearAppToken } from '../../api'
import { C } from '../../ui'
const ROWS = [
  ['/app/appearance', 'Appearance', 'Choose how Omni looks on this phone'],
  ['/app/my-data', 'Your data', 'What Omni holds about you — and delete it'],
  ['/app/phonebook', 'Phonebook', 'Tap to call or email a colleague'],
  ['/app/bug', 'Report a bug', 'Straight to the board'],
  ['/helpdesk/', 'IT Help Desk', 'Opens the help desk'],
]
export default function More() {
  return (
    <>
      <List title="More" rows={ROWS} />
      <div style={{ padding: 16 }}>
        <button onClick={() => { clearAppToken(); window.location.href = '/app/login' }} className="oa-press"
          style={{ width: '100%', minHeight: 44, borderRadius: 12, border: `1px solid ${C.line}`, background: C.card, color: C.red, fontWeight: 600 }}>Sign out of this phone</button>
      </div>
    </>
  )
}
