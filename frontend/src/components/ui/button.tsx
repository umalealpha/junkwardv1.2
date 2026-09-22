'use client'

import { cn } from '@/lib/utils'
import { ButtonHTMLAttributes, forwardRef } from 'react'
import { Loader2 } from 'lucide-react'

// ─── Button variants & sizes ───────────────────────────────────────────────────

type ButtonVariant = 'primary' | 'accent' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'success'
type ButtonSize = 'sm' | 'md' | 'lg' | 'icon'

const variantClasses: Record<ButtonVariant, string> = {
  // Navy bg — default action
  primary:
    'bg-[#0B0B3B] hover:bg-[#1A1A5E] active:bg-[#07074E] text-white border border-[#0B0B3B] hover:border-[#1A1A5E]',
  // Orange bg — single most important CTA
  accent:
    'bg-[#B04E00] hover:bg-[#A34A00] active:bg-[#A34A00] text-white border border-[#B04E00] hover:border-[#A34A00] font-semibold',
  // White bg, navy border
  secondary:
    'bg-white hover:bg-[#F9FAFB] text-[#0B0B3B] border border-[#D1D5DB] hover:border-[#0B0B3B]',
  // Transparent, navy text
  ghost:
    'bg-transparent hover:bg-[#F3F4F6] text-[#0B0B3B] border border-transparent',
  // Red — destructive
  danger:
    'bg-[#DC2626] hover:bg-[#B91C1C] text-white border border-[#DC2626] hover:border-[#B91C1C]',
  // Outlined — same as secondary for compat
  outline:
    'bg-white hover:bg-[#F9FAFB] text-[#0B0B3B] border border-[#D1D5DB] hover:border-[#0B0B3B]',
  // Success green
  success:
    'bg-[#059669] hover:bg-[#047857] text-white border border-[#059669]',
}

const sizeClasses: Record<ButtonSize, string> = {
  sm:   'px-3 h-8 text-xs rounded-lg',
  md:   'px-4 h-10 text-sm rounded-lg',
  lg:   'px-6 h-12 text-base rounded-lg',
  icon: 'w-10 h-10 rounded-lg',
}

// ─── Button component ──────────────────────────────────────────────────────────

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  leftIcon?: React.ReactNode
  rightIcon?: React.ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      loading = false,
      leftIcon,
      rightIcon,
      className,
      children,
      disabled,
      ...props
    },
    ref
  ) => {
    const isDisabled = disabled || loading

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        className={cn(
          'inline-flex items-center justify-center gap-2 font-medium transition-all duration-150',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-[#F07F00] focus-visible:ring-offset-2',
          'disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none',
          variantClasses[variant],
          sizeClasses[size],
          className
        )}
        {...props}
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : leftIcon ? (
          <span className="flex-shrink-0">{leftIcon}</span>
        ) : null}
        {children}
        {!loading && rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
      </button>
    )
  }
)
Button.displayName = 'Button'

// ─── IconButton ────────────────────────────────────────────────────────────────

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  'aria-label': string
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ variant = 'ghost', size = 'icon', loading, className, children, ...props }, ref) => (
    <Button ref={ref} variant={variant} size={size} loading={loading} className={className} {...props}>
      {children}
    </Button>
  )
)
IconButton.displayName = 'IconButton'
