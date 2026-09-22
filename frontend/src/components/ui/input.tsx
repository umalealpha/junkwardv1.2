'use client'

import { cn } from '@/lib/utils'
import {
  InputHTMLAttributes,
  TextareaHTMLAttributes,
  SelectHTMLAttributes,
  forwardRef,
  LabelHTMLAttributes,
} from 'react'

// ─── Label ────────────────────────────────────────────────────────────────────

interface LabelProps extends LabelHTMLAttributes<HTMLLabelElement> {
  required?: boolean
}

export const Label = forwardRef<HTMLLabelElement, LabelProps>(
  ({ className, children, required, ...props }, ref) => (
    <label
      ref={ref}
      className={cn('block text-xs font-medium text-[#374151] mb-1.5', className)}
      {...props}
    >
      {children}
      {required && <span className="text-[#F07F00] ml-0.5">*</span>}
    </label>
  )
)
Label.displayName = 'Label'

// ─── Shared input base styles ─────────────────────────────────────────────────

const inputBase =
  'w-full bg-white border rounded-md px-3 text-sm text-[#111827] placeholder-[#9CA3AF] ' +
  'focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] ' +
  'disabled:bg-[#F3F4F6] disabled:text-[#9CA3AF] disabled:cursor-not-allowed ' +
  'transition-all duration-150'

// ─── Input ────────────────────────────────────────────────────────────────────

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  error?: string
  leftIcon?: React.ReactNode
  rightIcon?: React.ReactNode
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, error, leftIcon, rightIcon, ...props }, ref) => (
    <div className="relative">
      {leftIcon && (
        <div className="absolute left-3 top-1/2 -translate-y-1/2 text-[#9CA3AF] pointer-events-none">
          {leftIcon}
        </div>
      )}
      <input
        ref={ref}
        className={cn(
          inputBase,
          'h-10',
          error ? 'border-[#DC2626] focus:border-[#DC2626] focus:ring-[rgba(220,38,38,0.1)]' : 'border-[#D1D5DB]',
          leftIcon  ? 'pl-9'  : '',
          rightIcon ? 'pr-9'  : '',
          className
        )}
        {...props}
      />
      {rightIcon && (
        <div className="absolute right-3 top-1/2 -translate-y-1/2 text-[#9CA3AF]">
          {rightIcon}
        </div>
      )}
    </div>
  )
)
Input.displayName = 'Input'

// ─── Textarea ─────────────────────────────────────────────────────────────────

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, error, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        inputBase,
        'py-2.5 min-h-[100px] resize-vertical',
        error ? 'border-[#DC2626] focus:border-[#DC2626] focus:ring-[rgba(220,38,38,0.1)]' : 'border-[#D1D5DB]',
        className
      )}
      {...props}
    />
  )
)
Textarea.displayName = 'Textarea'

// ─── Select ───────────────────────────────────────────────────────────────────

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  error?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, error, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        inputBase,
        'h-10 cursor-pointer appearance-none pr-8',
        error ? 'border-[#DC2626] focus:border-[#DC2626] focus:ring-[rgba(220,38,38,0.1)]' : 'border-[#D1D5DB]',
        // Down arrow via bg SVG
        "bg-[url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239CA3AF' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E\")] bg-no-repeat bg-[right_12px_center]",
        className
      )}
      {...props}
    >
      {children}
    </select>
  )
)
Select.displayName = 'Select'

// ─── FormField wrapper ────────────────────────────────────────────────────────

interface FormFieldProps {
  label?: string
  required?: boolean
  error?: string
  hint?: string
  children: React.ReactNode
  className?: string
}

export function FormField({ label, required, error, hint, children, className }: FormFieldProps) {
  return (
    <div className={cn('space-y-1', className)}>
      {label && <Label required={required}>{label}</Label>}
      {children}
      {error && <p className="text-xs text-[#DC2626] mt-1">{error}</p>}
      {hint && !error && <p className="text-xs text-[#9CA3AF] mt-1">{hint}</p>}
    </div>
  )
}

// ─── SearchInput ──────────────────────────────────────────────────────────────

import { Search } from 'lucide-react'

interface SearchInputProps extends InputHTMLAttributes<HTMLInputElement> {
  onClear?: () => void
}

export function SearchInput({ className, onClear, value, ...props }: SearchInputProps) {
  return (
    <div className="relative">
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9CA3AF] pointer-events-none" strokeWidth={1.5} />
      <input
        value={value}
        className={cn(
          inputBase,
          'h-10 border-[#D1D5DB] pl-9',
          onClear && value ? 'pr-8' : '',
          className
        )}
        {...props}
      />
      {onClear && value && (
        <button
          type="button"
          onClick={onClear}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-[#9CA3AF] hover:text-[#6B7280]"
        >
          &times;
        </button>
      )}
    </div>
  )
}
