'use client'

import { useRouter } from 'next/navigation'
import {
  ClipboardList,
  UserCheck,
  Shield,
  Building2,
  Sparkles,
  type LucideIcon,
} from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import { TopBar } from '@/components/layout/TopBar'

interface SettingCard {
  label: string
  desc: string
  icon: LucideIcon
  href: string
}

const cards: SettingCard[] = [
  { label: 'Companies',          desc: 'Subsidiaries & legal entities', icon: Building2,     href: '/settings/companies' },
  { label: 'Users & Permissions', desc: 'Manage users, assign titles',   icon: UserCheck,    href: '/settings/users' },
  { label: 'Roles & Hierarchy',  desc: 'Insurance role catalogue, role assignments, audit trail', icon: Shield, href: '/settings/roles' },
  { label: 'Bulk Access',        desc: 'Give a role to many people at once (Aria-assisted)', icon: UserCheck, href: '/settings/bulk-access' },
  { label: 'Digital Assistant', desc: 'Upload the 3D rat (Aria) GLB',   icon: Sparkles,      href: '/settings/digital-assistant' },
  { label: 'Audit Log',          desc: 'System activity',               icon: ClipboardList, href: '/audit-log' },
]

export default function SettingsPage() {
  const router = useRouter()
  const { theme } = useTheme()

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Settings"
        breadcrumbs={[
          { label: 'Home',     href: '/' },
          { label: 'Settings' },
        ]}
      />

      <div className="flex-1 p-6">
        <div className="max-w-4xl mx-auto">
          {/* Header */}
          <h1
            className="text-[28px] font-bold mb-2"
            style={{ color: theme.navy }}
          >
            Settings
          </h1>
          <p
            className="text-sm mb-8"
            style={{ color: theme.t2 }}
          >
            System configuration, audit log, and administration
          </p>

          {/* Cards grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {cards.map(({ label, desc, icon: Icon, href }) => (
              <button
                key={href}
                onClick={() => router.push(href)}
                className="flex items-center gap-4 rounded-lg p-5 text-left cursor-pointer transition-transform hover:scale-[1.01] active:scale-[0.99]"
                style={{
                  background: theme.card,
                  border: `1px solid ${theme.cardBdr}`,
                  boxShadow: theme.cardSh,
                }}
              >
                <div
                  className="flex items-center justify-center rounded-lg flex-shrink-0"
                  style={{
                    width: 42,
                    height: 42,
                    background: theme.oL,
                  }}
                >
                  <Icon
                    className="w-5 h-5"
                    style={{ color: theme.orange }}
                    strokeWidth={1.5}
                  />
                </div>
                <div className="min-w-0">
                  <p
                    className="text-sm font-bold truncate"
                    style={{ color: theme.navy }}
                  >
                    {label}
                  </p>
                  <p
                    className="text-xs mt-1 truncate"
                    style={{ color: theme.t3 }}
                  >
                    {desc}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
