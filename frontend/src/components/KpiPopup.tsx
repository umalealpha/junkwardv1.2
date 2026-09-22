'use client'

import { useState, useEffect } from 'react'
import Image from 'next/image'
import { BRAND } from '@/lib/brand'
import { kpiQuotes } from '@/lib/quotes'
import { useTheme } from '@/contexts/ThemeContext'

interface KpiPopupProps {
  cardLabel: string | null
}

export function KpiPopup({ cardLabel }: KpiPopupProps) {
  const { theme, reduceMotion } = useTheme()
  const [visible, setVisible] = useState(false)
  const [quote, setQuote] = useState('')

  useEffect(() => {
    if (!cardLabel || reduceMotion) {
      setVisible(false)
      return
    }

    const quotes = kpiQuotes[cardLabel]
    if (!quotes || quotes.length === 0) {
      setVisible(false)
      return
    }

    setQuote(quotes[Math.floor(Math.random() * quotes.length)])
    setVisible(true)

    const timer = setTimeout(() => setVisible(false), 3500)
    return () => clearTimeout(timer)
  }, [cardLabel, reduceMotion])

  if (reduceMotion || !visible) return null

  return (
    <div
      className="fixed flex items-end gap-2 transition-opacity duration-200"
      style={{
        bottom: 20,
        right: 20,
        zIndex: 100,
        opacity: visible ? 1 : 0,
      }}
    >
      {/* Speech bubble */}
      <div
        className="relative"
        style={{
          background: theme.card,
          border: `1px solid ${theme.cardBdr}`,
          borderRadius: 14,
          padding: '8px 14px',
          maxWidth: 200,
          boxShadow: theme.cardSh,
        }}
      >
        <p
          className="text-[13px] font-medium"
          style={{ color: theme.orange }}
        >
          {quote}
        </p>
        {/* Bubble tail pointing right */}
        <div
          className="absolute -right-1.5 bottom-3 w-3 h-3 rotate-45"
          style={{
            background: theme.card,
            borderRight: `1px solid ${theme.cardBdr}`,
            borderBottom: `1px solid ${theme.cardBdr}`,
          }}
        />
      </div>

      {/* Mascot */}
      <Image
        src={BRAND.mascots.boyStand}
        alt="Alpha Boy"
        width={52}
        height={52}
        className="object-contain flex-shrink-0"
        style={{ height: 'auto' }}
      />
    </div>
  )
}
