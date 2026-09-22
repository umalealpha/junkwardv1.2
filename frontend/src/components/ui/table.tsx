'use client'

import { cn } from '@/lib/utils'
import { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes, forwardRef } from 'react'

// ─── Table wrapper (responsive) ───────────────────────────────────────────────

interface TableProps extends HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}

export function TableWrapper({ className, children, ...props }: TableProps) {
  return (
    <div className={cn('w-full overflow-x-auto', className)} {...props}>
      {children}
    </div>
  )
}

// ─── Table ────────────────────────────────────────────────────────────────────

export const Table = forwardRef<HTMLTableElement, HTMLAttributes<HTMLTableElement>>(
  ({ className, ...props }, ref) => (
    <table
      ref={ref}
      className={cn('w-full text-sm text-left border-collapse', className)}
      {...props}
    />
  )
)
Table.displayName = 'Table'

// ─── TableHead ────────────────────────────────────────────────────────────────

export const TableHead = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => (
    <thead
      ref={ref}
      className={cn('bg-[#F3F4F6] border-b-2 border-[#E5E7EB]', className)}
      {...props}
    />
  )
)
TableHead.displayName = 'TableHead'

// ─── TableBody ────────────────────────────────────────────────────────────────

export const TableBody = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => (
    <tbody ref={ref} className={cn('divide-y divide-[#E5E7EB] bg-white', className)} {...props} />
  )
)
TableBody.displayName = 'TableBody'

// ─── TableFooter ──────────────────────────────────────────────────────────────

export const TableFooter = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => (
    <tfoot
      ref={ref}
      className={cn('bg-[#F3F4F6] border-t-2 border-[#E5E7EB] font-medium', className)}
      {...props}
    />
  )
)
TableFooter.displayName = 'TableFooter'

// ─── TableRow ─────────────────────────────────────────────────────────────────

interface TableRowProps extends HTMLAttributes<HTMLTableRowElement> {
  clickable?: boolean
  highlight?: 'danger' | 'warning' | 'success'
}

export const TableRow = forwardRef<HTMLTableRowElement, TableRowProps>(
  ({ className, clickable = false, highlight, ...props }, ref) => (
    <tr
      ref={ref}
      className={cn(
        'table-row-alt transition-colors duration-100',
        clickable && 'cursor-pointer hover:bg-[#FFF7ED]',
        !clickable && 'hover:bg-[#FFF7ED]',
        highlight === 'danger'  && 'bg-[#FEF2F2] hover:bg-[#FEE2E2]',
        highlight === 'warning' && 'bg-[#FFFBEB] hover:bg-[#FEF3C7]',
        highlight === 'success' && 'bg-[#ECFDF5] hover:bg-[#D1FAE5]',
        className
      )}
      {...props}
    />
  )
)
TableRow.displayName = 'TableRow'

// ─── TableHeader (th) ─────────────────────────────────────────────────────────

export const TableHeader = forwardRef<HTMLTableCellElement, ThHTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <th
      ref={ref}
      className={cn(
        'px-4 py-3 text-xs font-semibold text-[#374151] uppercase tracking-wider whitespace-nowrap text-left',
        className
      )}
      {...props}
    />
  )
)
TableHeader.displayName = 'TableHeader'

// ─── TableCell (td) ───────────────────────────────────────────────────────────

export const TableCell = forwardRef<HTMLTableCellElement, TdHTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <td
      ref={ref}
      className={cn('px-4 py-3 text-[#111827] whitespace-nowrap', className)}
      {...props}
    />
  )
)
TableCell.displayName = 'TableCell'

// ─── Empty state ──────────────────────────────────────────────────────────────

interface EmptyTableProps {
  colSpan: number
  message?: string
  icon?: React.ReactNode
}

export function EmptyTableRow({ colSpan, message = 'No records found', icon }: EmptyTableProps) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-16 text-center">
        <div className="flex flex-col items-center gap-3">
          {icon && <div className="text-[#D1D5DB] mb-1">{icon}</div>}
          <p className="text-[#6B7280] text-sm font-medium">{message}</p>
        </div>
      </td>
    </tr>
  )
}
