'use client'
import { usePathname } from 'next/navigation'
/** The staff screens render under two shells. Links inside them must stay in
 * the shell the user is in: '/app/...' in the Omni app, '/m/staff/...' in Nexus. */
export function useStaffBase(): string {
  const p = usePathname() || ''
  return p === '/app' || p.startsWith('/app/') ? '/app' : '/m/staff'
}
