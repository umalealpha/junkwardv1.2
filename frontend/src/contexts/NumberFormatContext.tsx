'use client'

import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react'
import { setGlobalNumberMode } from '@/lib/utils'

export type NumberFormatMode = 'millions' | 'thousands' | 'full'

interface NumberFormatContextValue {
  mode: NumberFormatMode
  setMode: (mode: NumberFormatMode) => void
  label: string
}

const MODE_LABELS: Record<NumberFormatMode, string> = {
  millions: 'Millions (M)',
  thousands: 'Thousands (K)',
  full: 'Full Detail',
}

const NumberFormatContext = createContext<NumberFormatContextValue | null>(null)

export function NumberFormatProvider({ children }: { children: ReactNode }) {
  // Default Full per spec (Legakwa 2026-06-09 — detail/reconciliation needs
  // exact numbers; summarise is opt-in via the toggle).
  const [mode, setModeState] = useState<NumberFormatMode>('full')

  const setMode = useCallback((m: NumberFormatMode) => {
    setModeState(m)
    setGlobalNumberMode(m)   // sync the module-level mode formatAmount() reads
    if (typeof window !== 'undefined') {
      localStorage.setItem('alpha_number_format', m)
    }
  }, [])

  // Keep the formatter's global mode in lockstep with React state on every
  // render (covers initial mount + the load-from-localStorage path below).
  setGlobalNumberMode(mode)

  useEffect(() => {
    const saved = localStorage.getItem('alpha_number_format') as NumberFormatMode | null
    if (saved && saved in MODE_LABELS) {
      setModeState(saved)
      setGlobalNumberMode(saved)
    }
  }, [])

  return (
    <NumberFormatContext.Provider value={{ mode, setMode, label: MODE_LABELS[mode] }}>
      {children}
    </NumberFormatContext.Provider>
  )
}

export function useNumberFormat() {
  const ctx = useContext(NumberFormatContext)
  if (!ctx) throw new Error('useNumberFormat must be used within NumberFormatProvider')
  return ctx
}
