'use client'

import { useEffect, useState, useCallback } from 'react'
import { getAriaPopups, markAriaPopupRead, type AriaPopupMessage } from '@/lib/api'

export default function AriaPopupOverlay() {
  const [popups, setPopups] = useState<AriaPopupMessage[]>([])

  const fetchPopups = useCallback(async () => {
    try {
      const fresh = await getAriaPopups()
      if (!fresh.length) return
      setPopups((prev) => {
        const seen = new Set(prev.map((p) => p.id))
        const added = fresh.filter((p) => !seen.has(p.id))
        return added.length ? [...prev, ...added] : prev
      })
    } catch {
      // A background poll must never surface an error to the recipient.
    }
  }, [])

  useEffect(() => {
    fetchPopups()
    const iv = setInterval(fetchPopups, 30_000)
    return () => clearInterval(iv)
  }, [fetchPopups])

  const dismiss = async (id: string) => {
    setPopups((prev) => prev.filter((p) => p.id !== id))
    try {
      await markAriaPopupRead(id)
    } catch {
      // Dismissed locally; the next poll re-shows it if the server missed it.
    }
  }

  if (!popups.length) return null

  const popup = popups[0]

  return (
    <div
      className="fixed inset-0 z-[99999] flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      role="dialog"
      aria-modal="true"
      aria-label="Message from Aria"
    >
      <div
        className="relative w-full max-w-lg mx-4 rounded-2xl shadow-2xl overflow-hidden"
        style={{ backgroundColor: '#0D1B2A' }}
      >
        <div className="px-6 py-4 text-center" style={{ backgroundColor: '#F4A623' }}>
          <h2 className="text-2xl font-bold text-white tracking-wide">Aria says</h2>
          <p className="text-sm text-white/90 mt-1">from {popup.sender}</p>
        </div>

        <div className="px-8 py-8">
          <p className="text-lg text-white leading-relaxed whitespace-pre-wrap">
            {popup.message}
          </p>
        </div>

        <div className="px-8 pb-6 flex justify-center">
          <button
            onClick={() => dismiss(popup.id)}
            className="px-10 py-3 rounded-xl text-lg font-semibold transition-all hover:scale-105 active:scale-95"
            style={{ backgroundColor: '#F4A623', color: '#0D1B2A' }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
