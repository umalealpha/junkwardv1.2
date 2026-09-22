'use client'

import * as RadixTabs from '@radix-ui/react-tabs'
import { cn } from '@/lib/utils'

// ─── Tabs root ────────────────────────────────────────────────────────────────

export const Tabs = RadixTabs.Root

// ─── TabsList ─────────────────────────────────────────────────────────────────

interface TabsListProps {
  children: React.ReactNode
  className?: string
  variant?: 'underline' | 'pills'
}

export function TabsList({ children, className, variant = 'underline' }: TabsListProps) {
  return (
    <RadixTabs.List
      className={cn(
        'flex',
        variant === 'underline'
          ? 'border-b-2 border-[#E5E7EB] gap-0'
          : 'bg-[#F3F4F6] rounded-lg p-1 gap-1',
        className
      )}
    >
      {children}
    </RadixTabs.List>
  )
}

// ─── TabsTrigger ──────────────────────────────────────────────────────────────

interface TabsTriggerProps {
  value: string
  children: React.ReactNode
  className?: string
  variant?: 'underline' | 'pills'
  count?: number
}

export function TabsTrigger({
  value,
  children,
  className,
  variant = 'underline',
  count,
}: TabsTriggerProps) {
  return (
    <RadixTabs.Trigger
      value={value}
      className={cn(
        'flex items-center gap-2 text-sm font-medium transition-all duration-150',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-[#F07F00]',
        variant === 'underline'
          ? [
              'px-5 py-3 text-[#6B7280] border-b-2 border-transparent -mb-[2px]',
              'hover:text-[#0B0B3B] hover:border-[#D1D5DB]',
              'data-[state=active]:text-[#0B0B3B] data-[state=active]:border-[#F07F00]',
            ]
          : [
              'px-3 py-1.5 rounded-md text-[#6B7280]',
              'hover:text-[#0B0B3B] hover:bg-white',
              'data-[state=active]:bg-white data-[state=active]:text-[#0B0B3B] data-[state=active]:shadow-[0_1px_3px_rgba(0,0,0,0.08)]',
            ],
        className
      )}
    >
      {children}
      {count !== undefined && (
        <span
          className={cn(
            'inline-flex items-center justify-center px-1.5 py-0.5 text-xs font-semibold rounded-full min-w-[20px]',
            'bg-[#F3F4F6] text-[#6B7280]',
            'data-[state=active]:bg-[#FFF7ED] data-[state=active]:text-[#CC6C00]'
          )}
        >
          {count}
        </span>
      )}
    </RadixTabs.Trigger>
  )
}

// ─── TabsContent ──────────────────────────────────────────────────────────────

interface TabsContentProps {
  value: string
  children: React.ReactNode
  className?: string
}

export function TabsContent({ value, children, className }: TabsContentProps) {
  return (
    <RadixTabs.Content
      value={value}
      className={cn('focus:outline-none animate-fade-in', className)}
    >
      {children}
    </RadixTabs.Content>
  )
}
