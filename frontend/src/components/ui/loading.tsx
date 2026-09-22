'use client'

import { cn } from '@/lib/utils'

// ─── LoadingSpinner ────────────────────────────────────────────────────────────

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg' | 'xl'
  className?: string
  onDark?: boolean
}

const sizeMap = {
  sm: 'w-4 h-4',
  md: 'w-6 h-6',
  lg: 'w-8 h-8',
  xl: 'w-12 h-12',
}

export function LoadingSpinner({ size = 'md', className, onDark = false }: LoadingSpinnerProps) {
  return (
    <div
      className={cn(
        'border-2 rounded-full animate-spin',
        onDark
          ? 'border-white/20 border-t-white'
          : 'border-[#E5E7EB] border-t-[#0B0B3B]',
        sizeMap[size],
        className
      )}
    />
  )
}

// ─── LoadingCard ──────────────────────────────────────────────────────────────

interface LoadingCardProps {
  message?: string
  className?: string
}

export function LoadingCard({ message = 'Loading...', className }: LoadingCardProps) {
  return (
    <div
      className={cn(
        'bg-white border border-[#E5E7EB] rounded-lg p-8 flex flex-col items-center justify-center gap-3',
        'shadow-[0_1px_3px_rgba(0,0,0,0.08)]',
        className
      )}
    >
      <LoadingSpinner size="lg" />
      <p className="text-[#6B7280] text-sm">{message}</p>
    </div>
  )
}

// ─── Skeleton ─────────────────────────────────────────────────────────────────

interface SkeletonProps {
  className?: string
}

export function Skeleton({ className }: SkeletonProps) {
  return <div className={cn('skeleton rounded', className)} />
}

// ─── LoadingTable ─────────────────────────────────────────────────────────────

interface LoadingTableProps {
  rows?: number
  cols?: number
}

export function LoadingTable({ rows = 5, cols = 5 }: LoadingTableProps) {
  return (
    <div className="w-full overflow-x-auto" role="status" aria-label="Loading table">
      {/* aria-hidden: purely decorative skeleton — its empty <th> cells were
          flagged by axe (empty-table-header) on every list page while loading */}
      <table className="w-full" aria-hidden="true">
        <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
          <tr>
            {Array.from({ length: cols }).map((_, i) => (
              <th key={i} className="px-4 py-3">
                <Skeleton className="h-3 w-20" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[#E5E7EB] bg-white">
          {Array.from({ length: rows }).map((_, rowIdx) => (
            <tr key={rowIdx} className="table-row-alt">
              {Array.from({ length: cols }).map((_, colIdx) => (
                <td key={colIdx} className="px-4 py-3">
                  <Skeleton
                    className={cn('h-4', colIdx === 0 ? 'w-24' : colIdx === cols - 1 ? 'w-16' : 'w-32')}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─── PageLoading ──────────────────────────────────────────────────────────────

interface PageLoadingProps {
  message?: string
}

export function PageLoading({ message = 'Loading...' }: PageLoadingProps) {
  return (
    <div className="fixed inset-0 bg-white flex items-center justify-center z-50">
      <div className="flex flex-col items-center gap-5">
        <div className="relative w-16 h-16">
          <div className="w-16 h-16 border-4 border-[#E5E7EB] rounded-full" />
          <div className="absolute inset-0 w-16 h-16 border-4 border-transparent border-t-[#0B0B3B] rounded-full animate-spin" />
        </div>
        <div className="text-center">
          <p className="text-[#0B0B3B] font-bold text-lg">
            Alpha <span className="text-[#F07F00]">Direct</span>
          </p>
          <p className="text-[#6B7280] text-sm mt-1">{message}</p>
        </div>
      </div>
    </div>
  )
}

// ─── SkeletonCard ─────────────────────────────────────────────────────────────

export function SkeletonCard() {
  return (
    <div className="bg-white border border-[#E5E7EB] rounded-lg p-5 shadow-[0_1px_3px_rgba(0,0,0,0.08)] space-y-3">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-7 w-40" />
      <Skeleton className="h-3 w-32" />
    </div>
  )
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn('h-4', i === lines - 1 ? 'w-2/3' : 'w-full')}
        />
      ))}
    </div>
  )
}
