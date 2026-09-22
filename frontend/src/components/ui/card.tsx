'use client'

import { cn } from '@/lib/utils'
import { HTMLAttributes, forwardRef } from 'react'
import { useTheme } from '@/contexts/ThemeContext'

// ─── Card ─────────────────────────────────────────────────────────────────────

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  hover?: boolean
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, hover = false, style, ...props }, ref) => {
    const { theme } = useTheme()
    return (
      <div
        ref={ref}
        className={cn(
          'rounded-lg',
          hover && 'card-hover cursor-pointer',
          className
        )}
        style={{
          background: theme.card,
          border: `1px solid ${theme.cardBdr}`,
          boxShadow: theme.cardSh,
          ...style,
        }}
        {...props}
      />
    )
  }
)
Card.displayName = 'Card'

// ─── CardHeader ───────────────────────────────────────────────────────────────

export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, style, ...props }, ref) => {
    const { theme } = useTheme()
    return (
      <div
        ref={ref}
        className={cn('px-5 py-4', className)}
        style={{ borderBottom: `1px solid ${theme.cardBdr}`, ...style }}
        {...props}
      />
    )
  }
)
CardHeader.displayName = 'CardHeader'

// ─── CardTitle ────────────────────────────────────────────────────────────────

export const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, style, ...props }, ref) => {
    const { theme } = useTheme()
    return (
      // h2, not h3 — pages get their h1 from TopBar (sr-only), so card titles
      // are the second level; h1→h3 jumps failed axe heading-order.
      <h2
        ref={ref}
        className={cn('text-base font-semibold', className)}
        style={{ color: theme.navy, ...style }}
        {...props}
      />
    )
  }
)
CardTitle.displayName = 'CardTitle'

// ─── CardDescription ──────────────────────────────────────────────────────────

export const CardDescription = forwardRef<HTMLParagraphElement, HTMLAttributes<HTMLParagraphElement>>(
  ({ className, style, ...props }, ref) => {
    const { theme } = useTheme()
    return (
      <p
        ref={ref}
        className={cn('text-sm mt-0.5', className)}
        style={{ color: theme.t2, ...style }}
        {...props}
      />
    )
  }
)
CardDescription.displayName = 'CardDescription'

// ─── CardContent ──────────────────────────────────────────────────────────────

export const CardContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('px-5 py-4', className)} {...props} />
  )
)
CardContent.displayName = 'CardContent'

// ─── CardFooter ───────────────────────────────────────────────────────────────

export const CardFooter = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, style, ...props }, ref) => {
    const { theme } = useTheme()
    return (
      <div
        ref={ref}
        className={cn('px-5 py-3', className)}
        style={{ borderTop: `1px solid ${theme.cardBdr}`, background: theme.g50, ...style }}
        {...props}
      />
    )
  }
)
CardFooter.displayName = 'CardFooter'

// ─── StatCard (KPI Card) ──────────────────────────────────────────────────────

interface StatCardProps {
  title: string
  value: string
  subtitle?: string
  icon?: React.ReactNode
  trend?: { value: number; label: string }
  valueColor?: string
  className?: string
}

export function StatCard({
  title,
  value,
  subtitle,
  icon,
  trend,
  className,
}: StatCardProps) {
  const { theme } = useTheme()
  return (
    <Card className={cn('p-5', className)}>
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>{title}</p>
          <p className="text-2xl font-bold mt-2 font-mono-nums truncate" style={{ color: theme.navy }}>
            {value}
          </p>
          {subtitle && <p className="text-xs mt-1" style={{ color: theme.t3 }}>{subtitle}</p>}
          {trend && (
            <div className="flex items-center gap-1 mt-2">
              <span
                className="text-xs font-medium"
                style={{ color: trend.value >= 0 ? theme.ok : theme.er }}
              >
                {trend.value >= 0 ? '↑' : '↓'} {Math.abs(trend.value).toFixed(1)}%
              </span>
              <span className="text-xs" style={{ color: theme.t3 }}>{trend.label}</span>
            </div>
          )}
        </div>
        {icon && (
          <div
            className="ml-4 w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0"
            style={{ background: theme.g100, color: theme.t3 }}
          >
            {icon}
          </div>
        )}
      </div>
    </Card>
  )
}
