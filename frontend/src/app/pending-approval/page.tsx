'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Shield, LogOut, Mail, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { isSSOConfigured, getMsalInstance } from '@/auth/msal'

export default function PendingApprovalPage() {
  const router = useRouter()
  const [account, setAccount] = useState<{ name: string; email: string } | null>(null)

  useEffect(() => {
    if (!isSSOConfigured()) {
      router.replace('/login')
      return
    }
    try {
      const msal = getMsalInstance()
      const a = msal.getActiveAccount() ?? msal.getAllAccounts()[0]
      if (!a) {
        router.replace('/login')
        return
      }
      setAccount({
        name:  a.name ?? a.username ?? 'there',
        email: a.username ?? '',
      })
    } catch {
      router.replace('/login')
    }
  }, [router])

  const handleSignOut = async () => {
    try {
      const msal = getMsalInstance()
      await msal.logoutRedirect({ postLogoutRedirectUri: '/login' })
    } catch {
      router.replace('/login')
    }
  }

  if (!account) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#F9FAFB] flex items-center justify-center p-4">
      <div className="max-w-lg w-full bg-white rounded-lg shadow-sm border border-gray-200 p-8">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-12 h-12 rounded-full bg-[#0B0B3B]/10 flex items-center justify-center">
            <Shield className="w-6 h-6 text-[#0B0B3B]" />
          </div>
          <div>
            <p className="text-[#0B0B3B] font-bold text-base leading-tight">
              Alpha <span className="text-[#F07F00]">Direct</span>
            </p>
            <p className="text-[#9CA3AF] text-xs">Financial Management</p>
          </div>
        </div>

        <h1 className="text-xl font-bold text-[#0B0B3B] mb-2">
          Welcome, {account.name.split(' ')[0]} 👋
        </h1>
        <p className="text-sm text-[#6B7280] mb-1">Signed in as</p>
        <p className="text-sm font-medium text-[#111827] mb-6 flex items-center gap-2">
          <Mail className="w-4 h-4 text-[#9CA3AF]" />
          {account.email}
        </p>

        <div className="bg-amber-50 border border-amber-200 rounded-md p-4 mb-6">
          <p className="text-sm font-medium text-amber-900 mb-1">
            Your access is awaiting approval
          </p>
          <p className="text-xs text-amber-800 leading-relaxed">
            Your sign-in succeeded, but no role has been assigned to your account yet.
            An administrator will review your request and grant you access shortly.
            You'll be able to use the system once a role is assigned to you.
          </p>
        </div>

        <div className="text-xs text-[#6B7280] mb-6 leading-relaxed">
          If this is unexpected, please contact your manager or the system administrator.
          Refresh this page once you've been notified that your access has been granted.
        </div>

        <div className="flex items-center justify-between">
          <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={handleSignOut}>
            <LogOut className="w-4 h-4 mr-2" />
            Sign out
          </Button>
        </div>
      </div>
    </div>
  )
}
