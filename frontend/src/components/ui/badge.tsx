'use client'

import { cn, getStatusBgColor } from '@/lib/utils'

// ─── Badge variants ────────────────────────────────────────────────────────────

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'muted' | 'orange' | 'navy'

const variantClasses: Record<BadgeVariant, string> = {
  default: 'bg-[#F3F4F6] text-[#6B7280]',
  success: 'bg-[#ECFDF5] text-[#059669]',
  warning: 'bg-[#FFFBEB] text-[#D97706]',
  danger:  'bg-[#FEF2F2] text-[#DC2626]',
  info:    'bg-[#EFF6FF] text-[#2563EB]',
  muted:   'bg-[#F3F4F6] text-[#9CA3AF]',
  orange:  'bg-[#FFF7ED] text-[#CC6C00]',
  navy:    'bg-[#EDEDF5] text-[#0B0B3B]',
}

interface BadgeProps {
  children: React.ReactNode
  variant?: BadgeVariant
  className?: string
  size?: 'sm' | 'md'
}

export function Badge({ children, variant = 'default', className, size = 'sm' }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center font-medium rounded-full',
        size === 'sm' ? 'px-2.5 py-0.5 text-xs' : 'px-3 py-1 text-sm',
        variantClasses[variant],
        className
      )}
    >
      {children}
    </span>
  )
}

// ─── StatusBadge — auto-maps status string to semantic color ──────────────────

interface StatusBadgeProps {
  status: string
  className?: string
  size?: 'sm' | 'md'
}

export function StatusBadge({ status, className, size = 'sm' }: StatusBadgeProps) {
  const colorClass = getStatusBgColor(status)

  const label = status
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())

  return (
    <span
      className={cn(
        'inline-flex items-center font-medium rounded-full',
        size === 'sm' ? 'px-2.5 py-0.5 text-xs' : 'px-3 py-1 text-sm',
        colorClass,
        className
      )}
    >
      {label}
    </span>
  )
}

// ─── TypeBadge — for invoice/contact types ────────────────────────────────────

interface TypeBadgeProps {
  type: string
  className?: string
}

export function TypeBadge({ type, className }: TypeBadgeProps) {
  const t = type?.toLowerCase()
  let classes = 'bg-[#F3F4F6] text-[#6B7280]'
  let label = type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

  if (t === 'customer_invoice' || t === 'customer') {
    classes = 'bg-[#EFF6FF] text-[#2563EB]'
    label = t === 'customer_invoice' ? 'Invoice' : 'Customer'
  } else if (t === 'vendor_bill' || t === 'vendor') {
    classes = 'bg-[#F5F3FF] text-[#7C3AED]'
    label = t === 'vendor_bill' ? 'Bill' : 'Vendor'
  } else if (t === 'credit_note') {
    classes = 'bg-[#ECFDF5] text-[#059669]'
    label = 'Credit Note'
  } else if (t === 'broker') {
    classes = 'bg-[#FFF7ED] text-[#CC6C00]'
    label = 'Broker'
  } else if (t === 'received') {
    classes = 'bg-[#ECFDF5] text-[#059669]'
    label = 'Received'
  } else if (t === 'sent') {
    classes = 'bg-[#FEF2F2] text-[#DC2626]'
    label = 'Sent'
  }

  return (
    <span
      className={cn(
        'inline-flex items-center px-2.5 py-0.5 text-xs font-medium rounded-full',
        classes,
        className
      )}
    >
      {label}
    </span>
  )
}
