'use client'

import * as Dialog from '@radix-ui/react-dialog'
import { cn } from '@/lib/utils'
import { X, AlertTriangle } from 'lucide-react'
import { Button } from './button'

// ─── Modal / Dialog ───────────────────────────────────────────────────────────

interface ModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title?: string
  description?: string
  children: React.ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
  className?: string
}

const sizeClasses = {
  sm:   'max-w-sm',
  md:   'max-w-lg',
  lg:   'max-w-2xl',
  xl:   'max-w-4xl',
  full: 'max-w-7xl',
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  size = 'md',
  className,
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-[200] animate-fade-in" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[210]',
            'bg-white border border-[#E5E7EB] rounded-xl shadow-[0_20px_60px_rgba(0,0,0,0.15)]',
            'w-full mx-4 max-h-[85vh] overflow-y-auto animate-slide-in',
            sizeClasses[size],
            className
          )}
        >
          {(title || description) && (
            <div className="flex items-start justify-between px-6 py-4 border-b border-[#E5E7EB]">
              <div>
                {title && (
                  <Dialog.Title className="text-base font-semibold text-[#0B0B3B]">
                    {title}
                  </Dialog.Title>
                )}
                {description && (
                  <Dialog.Description className="text-sm text-[#6B7280] mt-0.5">
                    {description}
                  </Dialog.Description>
                )}
              </div>
              <Dialog.Close asChild>
                <button
                  className="text-[#9CA3AF] hover:text-[#374151] transition-colors p-1 rounded-md hover:bg-[#F3F4F6]"
                  aria-label="Close"
                >
                  <X className="w-4 h-4" />
                </button>
              </Dialog.Close>
            </div>
          )}
          <div>{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// ─── ModalTrigger ─────────────────────────────────────────────────────────────

export const ModalTrigger = Dialog.Trigger

// ─── Modal body/footer helpers ────────────────────────────────────────────────

export function ModalBody({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('px-6 py-5', className)}>{children}</div>
}

export function ModalFooter({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'px-6 py-4 border-t border-[#E5E7EB] bg-[#F9FAFB] flex items-center justify-end gap-3',
        className
      )}
    >
      {children}
    </div>
  )
}

// ─── ConfirmDialog ────────────────────────────────────────────────────────────

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'danger' | 'warning' | 'primary'
  loading?: boolean
  onConfirm: () => void
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'danger',
  loading = false,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Modal open={open} onOpenChange={onOpenChange} size="sm">
      <ModalBody>
        <div className="flex items-start gap-4">
          <div
            className={cn(
              'flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center',
              variant === 'danger' ? 'bg-[#FEF2F2]' : 'bg-[#FFF7ED]'
            )}
          >
            <AlertTriangle
              className={cn('w-5 h-5', variant === 'danger' ? 'text-[#DC2626]' : 'text-[#F07F00]')}
            />
          </div>
          <div>
            <Dialog.Title className="text-base font-semibold text-[#0B0B3B]">{title}</Dialog.Title>
            <Dialog.Description className="text-sm text-[#6B7280] mt-1">{description}</Dialog.Description>
          </div>
        </div>
      </ModalBody>
      <ModalFooter>
        <Button variant="secondary" size="sm" onClick={() => onOpenChange(false)} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button
          variant={variant === 'danger' ? 'danger' : 'primary'}
          size="sm"
          loading={loading}
          onClick={onConfirm}
        >
          {confirmLabel}
        </Button>
      </ModalFooter>
    </Modal>
  )
}
